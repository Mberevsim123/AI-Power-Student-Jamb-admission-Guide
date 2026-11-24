from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.core.exceptions import LLMQuotaError, LLMUnavailableError
from app.core.llm_client import LLMClient
from app.models.chat import QueryIntent
from app.models.knowledge import KnowledgeDocument
from app.rag.embedding import DocumentIngester
from app.rag.retriever import EmbeddingClient, RAGRetriever
from app.schemas.schema import EligibilityRequest
from app.services.chat_service import ChatService
from app.services.intent_service import IntentClassifier


@pytest.mark.parametrize(
    "message,intent",
    [
        ("hello", QueryIntent.greeting),
        ("What are the JAMB subjects for Medicine?", QueryIntent.subject_combination),
        ("I need the cut-off for UNILAG", QueryIntent.cutoff_lookup),
        ("When is Post-UTME screening?", QueryIntent.post_utme_info),
        ("How do I register for JAMB?", QueryIntent.jamb_process),
        ("What are the WAEC requirements?", QueryIntent.olevel_requirement),
        ("Write malware", QueryIntent.out_of_scope),
    ],
)
async def test_intents_without_paid_classification(message, intent, monkeypatch):
    monkeypatch.setattr(settings, "INTENT_LLM_FALLBACK", False)
    assert await IntentClassifier().classify(message) == intent


async def test_follow_up_uses_previous_question_for_evidence(monkeypatch):
    previous = SimpleNamespace(
        role="user", state="completed", content="Tell me about UNILAG", intent="general_admission"
    )
    conv = SimpleNamespace(id=1, summary=None)
    repo = SimpleNamespace(
        get_conversation=AsyncMock(return_value=conv),
        get_recent_messages=AsyncMock(return_value=[previous]),
        create_message=AsyncMock(side_effect=lambda message: message),
    )
    retriever = SimpleNamespace(retrieve=AsyncMock(return_value=[]))
    prompt = SimpleNamespace(build=MagicMock(return_value=[]))
    llm = SimpleNamespace(providers=["test"], can_search=True)
    service = ChatService(None, repo, None, IntentClassifier(), retriever, prompt, llm)
    service._build_structured_context = AsyncMock(return_value={})
    monkeypatch.setattr("app.services.chat_service.baseline_context", lambda *args: {})
    monkeypatch.setattr("app.services.chat_service.render_local_answer", lambda *args: "local")

    await service._prepare_turn(SimpleNamespace(id=2, profile=None), "What is its cut-off?", 1)

    query = "Tell me about UNILAG\nWhat is its cut-off?"
    service._build_structured_context.assert_awaited_once_with(
        QueryIntent.cutoff_lookup, query, None
    )
    assert retriever.retrieve.await_args.kwargs["query"] == query
    assert prompt.build.call_args.kwargs["user_message"] == "What is its cut-off?"


async def test_chat_excludes_unofficial_and_unsourced_cutoffs():
    course = SimpleNamespace(name="Medicine")
    university = SimpleNamespace(name="University of Lagos", abbreviation="UNILAG")
    db = SimpleNamespace(
        scalars=AsyncMock(
            side_effect=[
                SimpleNamespace(all=lambda: [course]),
                SimpleNamespace(all=lambda: [university]),
            ]
        )
    )
    records = [
        {"score": 100, "is_official": False, "source_url": "https://example.edu/a"},
        {"score": 200, "is_official": True, "source_url": None},
        {"score": 250, "is_official": True, "source_url": "https://example.edu/b"},
    ]
    admissions = SimpleNamespace(
        get_current_cycle=AsyncMock(return_value=None),
        get_cutoffs=AsyncMock(return_value=records),
    )
    service = ChatService(db, None, admissions, None, None, None, None)
    context = await service._build_structured_context(
        QueryIntent.cutoff_lookup, "Medicine cut-off at UNILAG", None
    )
    assert context["cutoffs"] == [records[2]]


@pytest.mark.parametrize(
    "grades", [{"Maths": "Z1"}, {"Maths": "A1", "Mathematics": "C6"}, {"": "A1"}]
)
def test_grade_validation(grades):
    with pytest.raises(ValidationError):
        EligibilityRequest(course_name="Medicine", jamb_score=200, olevel_results=grades)


async def test_groq_client_uses_only_groq_key(monkeypatch):
    import openai

    seen = []

    def response(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "answer"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "groq-test-key")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "must-never-be-used")
    original = openai.AsyncOpenAI
    monkeypatch.setattr(
        openai,
        "AsyncOpenAI",
        lambda **kwargs: original(
            **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(response))
        ),
    )
    assert await LLMClient().complete([{"role": "user", "content": "test"}]) == ("answer", 2)
    assert str(seen[0].url) == "https://api.groq.com/openai/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer groq-test-key"


@pytest.mark.parametrize(
    "status,error", [(429, LLMQuotaError), (401, LLMUnavailableError), (503, LLMUnavailableError)]
)
async def test_provider_error_normalization(monkeypatch, status, error):
    import openai

    original = openai.AsyncOpenAI
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(
        openai,
        "AsyncOpenAI",
        lambda **kwargs: original(
            **kwargs,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(
                        status,
                        json={"error": {"message": "provider internal error", "type": "error"}},
                    )
                )
            ),
        ),
    )
    with pytest.raises(error):
        await LLMClient().complete([{"role": "user", "content": "test"}])


async def test_missing_key_never_uses_another_provider(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "unused-key")
    with pytest.raises(LLMUnavailableError):
        await LLMClient().complete([{"role": "user", "content": "test"}])


async def test_source_ingestion_and_current_active_filter(db, records, monkeypatch):
    from qdrant_client import AsyncQdrantClient

    import app.rag.retriever as module

    client = AsyncQdrantClient(location=":memory:")
    real_close = client.close
    monkeypatch.setattr(client, "close", AsyncMock())
    monkeypatch.setattr(module, "vector_client", lambda: client)
    monkeypatch.setattr(EmbeddingClient, "embed", AsyncMock(return_value=[1.0] + [0.0] * 383))
    docs = []
    for year, active in [(2026, True), (2025, True), (2026, False)]:
        doc = KnowledgeDocument(
            title="Official sample",
            category="admission_guideline",
            cycle_year=year,
            source_url="https://example.edu.ng/admissions",
            doc_meta={"text": "Sample admission information with provenance. " * 50},
            status="processing",
            is_active=active,
        )
        db.add(doc)
        await db.flush()
        count = await DocumentIngester(db).ingest(doc)
        assert count > 0
        assert await DocumentIngester(db).ingest(doc) == count
        doc.status = "indexed"
        docs.append(doc)
    await db.flush()
    result = await RAGRetriever(db).retrieve("admissions", QueryIntent.general_admission)
    assert result
    assert all(chunk.document_id == docs[0].id for chunk in result)
    assert result[0].source_url == "https://example.edu.ng/admissions"
    assert any(len(chunk.snippet) > 1000 for chunk in result)
    docs[0].is_active = False
    await db.flush()
    assert await RAGRetriever(db).retrieve("admissions", QueryIntent.general_admission) == []
    await real_close()
