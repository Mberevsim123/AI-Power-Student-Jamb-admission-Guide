from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.exceptions import LLMQuotaError, LLMUnavailableError
from app.core.llm_client import LLMClient
from app.models.chat import Message
from app.rag.retriever import RAGRetriever
from tests.conftest import auth


async def test_registration_requires_consent_and_rotates_refresh(client, db):
    body = dict(email="new@example.com", username="newstudent", password="password123")
    assert (await client.post("/api/v1/auth/register", json=body)).status_code == 422
    body["data_consent"] = True
    response = await client.post("/api/v1/auth/register", json=body)
    assert response.status_code == 201, response.text
    login = (await client.post("/api/v1/auth/login", json=body)).json()
    refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}
    )
    assert refresh.status_code == 200, refresh.text
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]})
    ).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/logout", headers={"Authorization": "Bearer " + login["access_token"]}
        )
    ).status_code == 204
    assert (
        await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh.json()["refresh_token"]}
        )
    ).status_code == 401


async def test_existing_user_consent_and_account_deletion(client, records, db):
    user = records["user"]
    user.data_consent_at = None
    await db.commit()
    headers = auth(user)
    assert (
        await client.post("/api/v1/chat/", json={"message": "hello"}, headers=headers)
    ).status_code == 403
    assert (
        await client.post("/api/v1/auth/consent", json={"data_consent": True}, headers=headers)
    ).status_code == 200
    assert (
        await client.post("/api/v1/chat/", json={"message": "hello"}, headers=headers)
    ).status_code == 200
    assert (await client.delete("/api/v1/auth/me", headers=headers)).status_code == 204
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401
    assert not (await db.scalars(select(Message).where(Message.user_id == user.id))).all()


async def test_ten_exchanges_and_no_duplicate_current_turn(client, records, monkeypatch):
    captured = []

    async def complete(self, messages, max_tokens=1024):
        captured.append(messages)
        return "Verified test response.", 20

    monkeypatch.setattr(LLMClient, "complete", complete)
    monkeypatch.setattr(RAGRetriever, "retrieve", AsyncMock(return_value=[]))
    headers = auth(records["user"])
    conversation_id = None
    for i in range(11):
        response = await client.post(
            "/api/v1/chat/",
            json={
                "message": f"Cut-off for Medicine question {i}",
                "conversation_id": conversation_id,
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
        conversation_id = response.json()["conversation_id"]
    last = captured[-1]
    assert len(last) == 22  # system + 20 previous messages + current user
    assert sum(m["content"] == "Cut-off for Medicine question 10" for m in last) == 1
    assert any(m["content"] == "Cut-off for Medicine question 0" for m in last)
    conversation = (
        await client.get(f"/api/v1/chat/conversations/{conversation_id}", headers=headers)
    ).json()
    assert conversation["message_count"] == 22
    assert len(conversation["messages"]) == 22
    assert (
        await client.get(
            f"/api/v1/chat/conversations/{conversation_id}", headers=auth(records["admin"])
        )
    ).status_code == 404
    assert (
        await client.post(
            "/api/v1/chat/", json={"message": "hi", "conversation_id": 99999999}, headers=headers
        )
    ).status_code == 404


@pytest.mark.parametrize(
    "exception,expected", [(LLMQuotaError(), 429), (LLMUnavailableError(), 503)]
)
async def test_provider_failures_return_saved_local_evidence(
    client, records, monkeypatch, db, exception, expected
):
    monkeypatch.setattr(LLMClient, "complete", AsyncMock(side_effect=exception))
    monkeypatch.setattr(RAGRetriever, "retrieve", AsyncMock(return_value=[]))
    response = await client.post(
        "/api/v1/chat/", json={"message": "Cut-off for Medicine"}, headers=auth(records["user"])
    )
    assert response.status_code == 200, response.text
    assert response.json()["model_used"] == "local-guidance"
    assert "200" in response.json()["content"]
    assert "2026" in response.json()["content"]
    assert "example.edu.ng/cutoffs" in response.json()["content"]
    messages = (
        await db.scalars(select(Message).where(Message.user_id == records["user"].id))
    ).all()
    assert len(messages) == 2
    assistant = next(m for m in messages if m.role == "assistant")
    assert assistant.state == "completed"
    assert assistant.error_message is None


async def test_fallbacks_need_no_provider(client, records, monkeypatch):
    model = AsyncMock(side_effect=AssertionError("Fallback must not use the model"))
    monkeypatch.setattr(LLMClient, "complete", model)
    monkeypatch.setattr(RAGRetriever, "retrieve", AsyncMock(return_value=[]))
    from app.core.config import settings

    monkeypatch.setattr(settings, "GROQ_API_KEY", "")
    for message in ["Hello", "Write a song", "When is JAMB registration?"]:
        response = await client.post(
            "/api/v1/chat/", json={"message": message}, headers=auth(records["user"])
        )
        assert response.status_code == 200, response.text
        assert response.json()["model_used"] == "local-guidance"
    assert (
        await client.post("/api/v1/chat/", json={"message": "  "}, headers=auth(records["user"]))
    ).status_code == 422


async def test_stream_and_feedback_ownership(client, records, monkeypatch):
    response = await client.post(
        "/api/v1/chat/stream", json={"message": "hello"}, headers=auth(records["user"])
    )
    assert response.status_code == 200
    assert "[DONE]" in response.text
    assert "event: metadata" in response.text
    assert '"delta"' in response.text
    chats = (await client.get("/api/v1/chat/conversations", headers=auth(records["user"]))).json()
    detail = (
        await client.get(
            f"/api/v1/chat/conversations/{chats[0]['id']}", headers=auth(records["user"])
        )
    ).json()
    message_id = detail["messages"][-1]["id"]
    route = f"/api/v1/chat/messages/{message_id}/feedback"
    assert (
        await client.post(route, json={"rating": 1}, headers=auth(records["admin"]))
    ).status_code == 404
    assert (
        await client.post(route, json={"rating": 1}, headers=auth(records["user"]))
    ).status_code == 201
    assert (
        await client.post(route, json={"rating": -1}, headers=auth(records["user"]))
    ).status_code == 409


async def test_failed_provider_stream_completes_with_saved_local_evidence(
    client, records, monkeypatch
):
    async def failed_stream(self, messages, max_tokens=1024):
        raise LLMQuotaError("Configured Gemini quota is unavailable.", retry_after=12)
        yield ""  # Mark the mocked method as an async generator.

    monkeypatch.setattr(LLMClient, "stream", failed_stream)
    monkeypatch.setattr(RAGRetriever, "retrieve", AsyncMock(return_value=[]))
    response = await client.post(
        "/api/v1/chat/stream",
        json={"message": "Cut-off for Medicine"},
        headers=auth(records["user"]),
    )
    assert response.status_code == 200  # SSE headers precede model execution.
    assert "event: metadata" in response.text
    assert "event: error" not in response.text
    assert "[DONE]" in response.text
    assert "Local knowledge base" in response.text
    conversations = (
        await client.get("/api/v1/chat/conversations", headers=auth(records["user"]))
    ).json()
    detail = (
        await client.get(
            f"/api/v1/chat/conversations/{conversations[0]['id']}", headers=auth(records["user"])
        )
    ).json()
    assert detail["messages"][-1]["state"] == "completed"
    assert detail["messages"][-1]["error_message"] is None
    assert detail["messages"][-1]["model_used"] == "local-guidance"
