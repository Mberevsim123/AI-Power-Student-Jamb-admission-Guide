import asyncio
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.core.exceptions import LLMQuotaError, LLMUnavailableError
from app.core.llm_client import LLMClient
from app.models.chat import QueryIntent
from app.services.local_knowledge import baseline_context, render_local_answer


@pytest.fixture
def providers(monkeypatch):
    monkeypatch.setattr(
        settings, "LLM_FALLBACK_PROVIDERS", ["gemini", "openai", "anthropic", "groq"]
    )
    for provider in ("GROQ", "GEMINI", "OPENAI", "ANTHROPIC"):
        monkeypatch.setattr(settings, provider + "_API_KEY", "test-only-" + provider)
    return monkeypatch


async def test_fallback_order_models_and_success(providers):
    llm = LLMClient()
    seen = []

    async def attempt(provider, messages, max_tokens):
        seen.append((provider, settings.model_for(provider)))
        if provider in ("groq", "gemini"):
            raise LLMQuotaError()
        return "Sourced answer", 8

    providers.setattr(llm, "_complete_provider", attempt)
    assert await llm.complete([{"role": "user", "content": "question"}]) == ("Sourced answer", 8)
    assert [p for p, model in seen] == ["groq", "gemini", "openai"]
    assert [model for p, model in seen] == [
        settings.GROQ_MODEL,
        settings.GEMINI_MODEL,
        settings.OPENAI_MODEL,
    ]
    assert llm.model_used == settings.OPENAI_MODEL
    assert llm.provider_used == "openai"


@pytest.mark.parametrize("streaming", [False, True])
async def test_search_enabled_prefers_gemini_when_groq_is_primary(providers, streaming):
    llm = LLMClient(web_search=True)
    attempt = AsyncMock(return_value=("grounded answer", 4))
    providers.setattr(llm, "_complete_provider", attempt)
    if streaming:

        async def stream(provider, messages, max_tokens):
            text, _ = await attempt(provider, messages, max_tokens)
            yield text

        providers.setattr(llm, "_stream_provider", stream)
        assert [part async for part in llm.stream([])] == ["grounded answer"]
    else:
        assert await llm.complete([]) == ("grounded answer", 4)
    assert attempt.await_args.args[0] == "gemini"
    assert attempt.await_count == 1
    assert llm.provider_used == "gemini"


async def test_midstream_failure_discards_text_and_stale_grounding(providers):
    llm = LLMClient(web_search=True)
    seen = []

    async def attempt(provider, messages, max_tokens):
        seen.append(provider)
        if provider == "gemini":
            llm.web_sources = [{"source_url": "https://invalid.example/stale"}]
            llm.web_grounding = {"stale": True}
            yield "Wrong partial answer"
            raise LLMUnavailableError()
        yield "Replacement answer"

    providers.setattr(llm, "_stream_provider", attempt)
    chunks = [chunk async for chunk in llm.stream([])]
    assert chunks == ["Replacement answer"]
    assert seen == ["gemini", "groq"]
    assert llm.web_sources == [] and llm.web_grounding == {}
    assert llm.provider_used == "groq"


@pytest.mark.parametrize("streaming", [False, True])
async def test_every_provider_failed(providers, streaming):
    llm = LLMClient()
    attempt = AsyncMock(side_effect=LLMQuotaError())
    providers.setattr(llm, "_complete_provider", attempt)
    if streaming:
        # Use completion for all mocked providers; Gemini normally has native SSE.
        async def stream(provider, messages, max_tokens):
            text, _ = await attempt(provider, messages, max_tokens)
            yield text

        providers.setattr(llm, "_stream_provider", stream)
    with pytest.raises(LLMQuotaError):
        if streaming:
            [chunk async for chunk in llm.stream([])]
        else:
            await llm.complete([])
    assert attempt.await_count == 4
    assert llm.model_used is None and llm.final_text is None


async def test_empty_response_and_timeout_fail_over(providers):
    llm = LLMClient()
    attempt = AsyncMock(side_effect=[TimeoutError(), ("  ", 0), ("answer", 3)])
    providers.setattr(llm, "_complete_provider", attempt)
    assert await llm.complete([]) == ("answer", 3)
    assert attempt.await_count == 3


async def test_total_timeout_does_not_keep_trying(providers):
    providers.setattr(settings, "LLM_FAILOVER_TIMEOUT_SECONDS", 0.01)
    llm = LLMClient()

    async def slow(*args):
        await asyncio.sleep(10)

    attempt = AsyncMock(side_effect=slow)
    providers.setattr(llm, "_complete_provider", attempt)
    with pytest.raises(LLMUnavailableError, match="timed out"):
        await llm.complete([])
    assert attempt.await_count == 1


async def test_cancel_does_not_fail_over(providers):
    llm = LLMClient()
    attempt = AsyncMock(side_effect=asyncio.CancelledError())
    providers.setattr(llm, "_complete_provider", attempt)
    with pytest.raises(asyncio.CancelledError):
        await llm.complete([])
    assert attempt.await_count == 1


def test_skip_missing_placeholder_keys_and_deduplicate(providers):
    providers.setattr(settings, "GEMINI_API_KEY", "")
    providers.setattr(settings, "OPENAI_API_KEY", "your-openai-key")
    assert LLMClient().providers == ["groq", "anthropic"]
    assert not LLMClient(web_search=True).can_search


def test_legacy_model_override_cannot_leak_into_fallback(providers):
    providers.setattr(settings, "LLM_MODEL", "primary-groq-model")
    assert settings.model_for("groq") == "primary-groq-model"
    assert settings.model_for("gemini") == settings.GEMINI_MODEL


@pytest.mark.parametrize("streaming", [False, True])
async def test_no_keys_never_makes_network_requests(monkeypatch, streaming):
    for provider in ("GROQ", "GEMINI", "OPENAI", "ANTHROPIC"):
        monkeypatch.setattr(settings, provider + "_API_KEY", "")
    llm = LLMClient()
    attempt = AsyncMock(side_effect=AssertionError("No network permitted"))
    monkeypatch.setattr(llm, "_complete_provider", attempt)
    with pytest.raises(LLMUnavailableError, match="No AI provider"):
        if streaming:
            [chunk async for chunk in llm.stream([])]
        else:
            await llm.complete([])
    attempt.assert_not_called()


def test_local_nsuk_has_verified_identity_but_no_invented_score():
    query = "What is the cutoff for Nasarawa State University in 2027?"
    ctx = baseline_context(query, QueryIntent.cutoff_lookup)
    text = render_local_answer(QueryIntent.cutoff_lookup, query, ctx, [])
    assert "NSUK" in text and "https://www.nsuk.edu.ng" in text
    assert "2027" in text and "cannot confirm" in text
    assert "160" not in text and "180" not in text


def test_local_ignores_estimates_and_missing_provenance():
    rows = [
        dict(
            university="Test",
            course="Medicine",
            score=999,
            year=2026,
            is_official=False,
            source_url="https://test.edu.ng",
        )
    ]
    assert "999" not in render_local_answer(
        QueryIntent.cutoff_lookup, "cutoff", {"cutoffs": rows}, []
    )
    rows[0].update(is_official=True, source_url=None)
    assert "999" not in render_local_answer(
        QueryIntent.cutoff_lookup, "cutoff", {"cutoffs": rows}, []
    )


async def test_real_sdk_transports_switch_auth_keys_models_and_providers(providers):
    import json

    import httpx
    import openai

    from app.core.gemini_client import GeminiClient

    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host == "api.groq.com":
            return httpx.Response(
                429, json={"error": {"message": "limit", "type": "rate_limit_error"}}
            )
        if request.url.host == "generativelanguage.googleapis.com":
            return httpx.Response(503, json={"error": {"message": "private detail"}})
        return httpx.Response(
            200,
            json={
                "id": "mock",
                "object": "chat.completion",
                "created": 0,
                "model": settings.OPENAI_MODEL,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Recovered answer"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
            },
        )

    llm = LLMClient(web_search=True)
    providers.setattr(
        llm,
        "_openai_client",
        lambda provider: openai.AsyncOpenAI(
            api_key=llm._key(provider),
            base_url="https://api.groq.com/openai/v1" if provider == "groq" else None,
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ),
    )
    providers.setattr(
        GeminiClient,
        "_client",
        lambda self: httpx.AsyncClient(
            base_url="https://generativelanguage.googleapis.com/v1beta/",
            headers={"x-goog-api-key": settings.GEMINI_API_KEY},
            transport=httpx.MockTransport(handler),
        ),
    )
    assert await llm.complete([{"role": "user", "content": "question"}]) == ("Recovered answer", 4)
    assert len(requests) == 3
    assert requests[0].headers["x-goog-api-key"] == "test-only-GEMINI"
    assert requests[1].headers["authorization"] == "Bearer test-only-GROQ"
    assert requests[2].headers["authorization"] == "Bearer test-only-OPENAI"
    assert settings.GEMINI_MODEL in str(requests[0].url)
    assert json.loads(requests[1].content)["model"] == settings.GROQ_MODEL
    assert json.loads(requests[2].content)["model"] == settings.OPENAI_MODEL
    assert llm.model_used == settings.OPENAI_MODEL


@pytest.mark.parametrize(
    "query",
    [
        "What is NSUK?",
        "How do I get a profile code?",
        "How do I upload my results?",
        "What is IBASS?",
    ],
)
async def test_baseline_questions_recognised_locally(query):
    from app.services.intent_service import IntentClassifier

    assert await IntentClassifier().classify(query) != QueryIntent.out_of_scope


async def test_ungrounded_provider_is_told_tools_are_disabled(monkeypatch):
    import json

    import httpx
    import openai

    seen = []

    def respond(request):
        seen.append(json.loads(request.content))
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
                        "message": {
                            "role": "assistant",
                            "content": "No verified cutoff is recorded.",
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    llm = LLMClient(web_search=True)
    monkeypatch.setattr(
        llm,
        "_openai_client",
        lambda provider: openai.AsyncOpenAI(
            api_key="fake-test-key",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        ),
    )
    original = [
        {"role": "system", "content": "Use Google Search if available."},
        {"role": "user", "content": "LASU cutoff?"},
    ]
    await llm._complete_provider("groq", original, 1024)
    assert seen[0]["tool_choice"] == "none"
    assert (
        "TOOL AVAILABILITY FOR THIS REQUEST: No tools are available."
        in seen[0]["messages"][0]["content"]
    )
    assert "Do not emit or attempt tool calls" in seen[0]["messages"][0]["content"]
    assert "TOOL AVAILABILITY" not in original[0]["content"]
