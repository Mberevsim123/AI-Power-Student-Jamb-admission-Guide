import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.config import settings
from app.core.exceptions import LLMQuotaError, LLMUnavailableError
from app.core.llm_client import LLMClient
from app.rag.retriever import RAGRetriever
from tests.conftest import auth


@pytest.fixture
def gemini(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "LLM_MODEL", "gemini-3.8-flash")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-google-key")
    monkeypatch.setattr(settings, "GEMINI_WEB_SEARCH_ENABLED", True)


def result(text="Published fee: ₦2,000.", **changes):
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": text}]},
                "finishReason": "STOP",
                "groundingMetadata": {
                    "groundingChunks": [
                        {
                            "web": {
                                "uri": "https://example.edu.ng/admissions",
                                "title": "Institution announcement",
                            }
                        }
                    ],
                    "groundingSupports": [
                        {
                            "segment": {"endIndex": len(text.encode("utf-8")), "text": text},
                            "groundingChunkIndices": [0],
                        }
                    ],
                    "webSearchQueries": ["official institution admissions announcement 2026"],
                    "searchEntryPoint": {"renderedContent": "<div>Google Search suggestions</div>"},
                },
            }
        ],
        "usageMetadata": {"totalTokenCount": 42},
    } | changes


def mock_google(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)),
    )


async def test_native_gemini_key_history_evidence_and_unicode_citations(gemini, monkeypatch):
    seen = []

    def response(request):
        seen.append(request)
        return httpx.Response(200, json=result())

    mock_google(monkeypatch, response)
    llm = LLMClient(web_search=True)
    text, tokens = await llm.complete(
        [
            {"role": "system", "content": "Reviewed local knowledge"},
            {"role": "user", "content": "Previous question"},
            {"role": "assistant", "content": "Previous answer"},
            {"role": "user", "content": "Any recent updates?"},
        ]
    )
    request = seen[0]
    assert request.url.host == "generativelanguage.googleapis.com"
    assert request.url.path.endswith("gemini-3.8-flash:generateContent")
    assert request.headers["x-goog-api-key"] == "test-google-key"
    assert "test-google-key" not in str(request.url)
    payload = json.loads(request.content)
    assert payload["tools"] == [{"google_search": {}}]
    assert payload["systemInstruction"]["parts"][0]["text"] == "Reviewed local knowledge"
    assert [m["role"] for m in payload["contents"]] == ["user", "model", "user"]
    assert "₦2,000. [Web 1](<https://example.edu.ng/admissions>)" in text
    assert tokens == 42
    assert llm.web_sources[0]["origin"] == "web"
    assert llm.web_grounding["search_suggestions_html"].startswith("<div>")


async def test_intent_generation_does_not_enable_web_search(gemini, monkeypatch):
    payloads = []

    def response(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json=result("general_admission"))

    mock_google(monkeypatch, response)
    await LLMClient().complete([{"role": "user", "content": "Classify this"}], max_tokens=20)
    assert "tools" not in payloads[0]


async def test_native_stream_accumulates_grounding_and_ignores_thoughts(gemini, monkeypatch):
    first = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"thought": True, "text": "Internal reasoning is never user output"},
                        {"text": "Published "},
                    ]
                }
            }
        ]
    }
    last = result("fee: ₦2,000.")
    events = "".join("data: " + json.dumps(event) + "\r\n\r\n" for event in [first, last])
    seen = []

    def response(request):
        seen.append(request)
        return httpx.Response(200, text=events, headers={"content-type": "text/event-stream"})

    mock_google(monkeypatch, response)
    llm = LLMClient(web_search=True)
    chunks = [chunk async for chunk in llm.stream([{"role": "user", "content": "Updates?"}])]
    assert "".join(chunks).startswith("Published fee: ₦2,000.")
    assert "[Web 1]" in "".join(chunks)
    assert "[Web 1]" in llm.final_text
    assert llm.usage_tokens == 42
    assert seen[0].url.params["alt"] == "sse"


@pytest.mark.parametrize(
    "status,error",
    [
        (429, LLMQuotaError),
        (400, LLMUnavailableError),
        (403, LLMUnavailableError),
        (404, LLMUnavailableError),
        (503, LLMUnavailableError),
    ],
)
async def test_google_errors_are_sanitized(gemini, monkeypatch, status, error):
    mock_google(monkeypatch, lambda request: httpx.Response(status, text="private provider detail"))
    with pytest.raises(error) as exc:
        await LLMClient(web_search=True).complete([{"role": "user", "content": "test"}])
    assert "private provider detail" not in str(exc.value)
    assert "test-google-key" not in str(exc.value)


async def test_missing_gemini_key_does_not_call_another_provider(gemini, monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    with pytest.raises(LLMUnavailableError, match="No AI provider"):
        await LLMClient().complete([{"role": "user", "content": "test"}])


@pytest.mark.parametrize(
    "details,expected,retry",
    [
        (
            [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [
                        {"quotaValue": "0", "quotaMetric": "generate_content_free_tier_requests"},
                    ],
                }
            ],
            "no quota allocated",
            None,
        ),
        (
            [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "12.3s"}],
            "Try again in 13 seconds",
            13,
        ),
        (
            [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [
                        {"quotaMetric": "google_search_grounding_requests", "quotaValue": "100"},
                    ],
                }
            ],
            "Google Search grounding quota",
            None,
        ),
    ],
)
async def test_quota_details_are_actionable_and_sanitized(
    gemini, monkeypatch, details, expected, retry
):
    mock_google(
        monkeypatch,
        lambda request: httpx.Response(
            429,
            json={
                "error": {
                    "code": 429,
                    "message": "private project identifier and secret",
                    "details": details,
                }
            },
        ),
    )
    with pytest.raises(LLMQuotaError) as caught:
        await LLMClient(web_search=True).complete([{"role": "user", "content": "test"}])
    assert expected in caught.value.message
    assert caught.value.retry_after == retry
    assert "secret" not in caught.value.message


async def test_stream_reads_quota_error_body_before_parsing(gemini, monkeypatch):
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield json.dumps(
                {
                    "error": {
                        "code": 429,
                        "details": [
                            {
                                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                                "retryDelay": "8s",
                            }
                        ],
                    }
                }
            ).encode()

    mock_google(monkeypatch, lambda request: httpx.Response(429, stream=Body()))
    with pytest.raises(LLMQuotaError) as caught:
        _ = [
            chunk
            async for chunk in LLMClient(web_search=True).stream(
                [
                    {"role": "user", "content": "test"},
                ]
            )
        ]
    assert caught.value.retry_after == 8


async def test_truncated_stream_is_a_failure(gemini, monkeypatch):
    mock_google(
        monkeypatch,
        lambda request: httpx.Response(
            200, text='data: {"candidates":[{"content":{"parts":[{"text":"partial"}]}}]}\n\n'
        ),
    )
    with pytest.raises(LLMUnavailableError, match="incomplete"):
        _ = [chunk async for chunk in LLMClient().stream([{"role": "user", "content": "test"}])]


async def test_unsafe_web_sources_are_never_cited(gemini, monkeypatch):
    response = result()
    response["candidates"][0]["groundingMetadata"]["groundingChunks"][0]["web"]["uri"] = (
        "javascript:alert(1)"
    )
    mock_google(monkeypatch, lambda request: httpx.Response(200, json=response))
    llm = LLMClient(web_search=True)
    text, _ = await llm.complete([{"role": "user", "content": "test"}])
    assert "javascript" not in text
    assert llm.web_sources == []


@pytest.mark.parametrize("streaming", [False, True])
async def test_chat_combines_local_evidence_and_saved_web_sources(
    client, records, gemini, monkeypatch, streaming
):
    payloads = []

    def response(request):
        payloads.append(json.loads(request.content))
        if streaming:
            return httpx.Response(200, text="data: " + json.dumps(result()) + "\n\n")
        return httpx.Response(200, json=result())

    mock_google(monkeypatch, response)
    monkeypatch.setattr(RAGRetriever, "retrieve", AsyncMock(return_value=[]))
    route = "/api/v1/chat/stream" if streaming else "/api/v1/chat/"
    response = await client.post(
        route, json={"message": "Latest cut-off for Medicine?"}, headers=auth(records["user"])
    )
    assert response.status_code == 200, response.text
    if streaming:
        assert "[DONE]" in response.text
        conversations = (
            await client.get("/api/v1/chat/conversations", headers=auth(records["user"]))
        ).json()
        detail = (
            await client.get(
                f"/api/v1/chat/conversations/{conversations[0]['id']}",
                headers=auth(records["user"]),
            )
        ).json()
        message = detail["messages"][-1]
    else:
        message = response.json()
    assert message["model_used"] == "gemini-3.8-flash"
    assert "[Web 1]" in message["content"]
    assert any(s["origin"] == "web" for s in message["rag_sources"])
    assert any(s["origin"] == "knowledge_base" for s in message["rag_sources"])
    assert message["structured_context"]["cutoffs"]
    assert message["structured_context"]["web_grounding"]["provider"] == "google_search"
    assert message["token_count"] == 42
    assert "Admissions evidence" in payloads[0]["systemInstruction"]["parts"][0]["text"]


async def test_empty_knowledge_base_uses_web_instead_of_fixed_fallback(
    client, records, gemini, monkeypatch
):
    mock_google(monkeypatch, lambda request: httpx.Response(200, json=result()))
    monkeypatch.setattr(RAGRetriever, "retrieve", AsyncMock(return_value=[]))
    response = await client.post(
        "/api/v1/chat/",
        json={"message": "Latest JAMB Facebook announcement?"},
        headers=auth(records["user"]),
    )
    assert response.status_code == 200
    assert response.json()["model_used"] == "gemini-3.8-flash"
    assert response.json()["structured_context"]["web_grounding"]["sources"]
