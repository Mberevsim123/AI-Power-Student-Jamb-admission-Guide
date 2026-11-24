"""Bounded provider failover; incomplete answers never escape into the conversation."""

import asyncio
import logging
from collections.abc import AsyncIterator

import anthropic
import openai

from app.core.config import settings
from app.core.exceptions import LLMQuotaError, LLMUnavailableError

logger = logging.getLogger(__name__)
PromptMessages = list[dict]


class LLMClient:
    def __init__(self, web_search=False):
        self.web_search = web_search
        self._reset()

    def _reset(self):
        self.web_sources = []
        self.web_grounding = {}
        self.final_text = None
        self.usage_tokens = None
        self.provider_used = None
        self.model_used = None

    @property
    def providers(self):
        order = dict.fromkeys([settings.LLM_PROVIDER, *settings.LLM_FALLBACK_PROVIDERS])
        providers = [p for p in order if self._key(p)]
        # Search-enabled chat needs the provider with a search tool to run first;
        # other calls (including intent classification) keep the configured order.
        if self.web_search and "gemini" in providers:
            providers.remove("gemini")
            providers.insert(0, "gemini")
        return providers

    @staticmethod
    def _key(provider):
        key = getattr(settings, f"{provider.upper()}_API_KEY").strip()
        return key if key and not key.startswith(("your-", "your_", "replace-")) else None

    @property
    def can_search(self):
        return self.web_search and "gemini" in self.providers

    def _capture_gemini(self, client):
        self.web_sources = client.sources
        self.web_grounding = client.grounding
        self.final_text = client.final_text
        self.usage_tokens = client.token_count

    def _openai_client(self, provider):
        return openai.AsyncOpenAI(
            api_key=self._key(provider),
            base_url="https://api.groq.com/openai/v1" if provider == "groq" else None,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            max_retries=0,
        )

    async def _complete_provider(self, provider, messages, max_tokens):
        model = settings.model_for(provider)
        if provider == "gemini":
            from app.core.gemini_client import GeminiClient

            client = GeminiClient(web_search=self.web_search, model=model)
            result = await client.complete(messages, max_tokens)
            self._capture_gemini(client)
            return result
        # The shared admissions persona describes optional Google Search. Explicitly
        # scope it to this attempt: Groq/OpenAI/Anthropic have no tools wired here.
        # In particular GPT-OSS can otherwise emit browser calls and Groq rejects
        # the entire request with tool_use_failed instead of returning an answer.
        messages = [dict(message) for message in messages]
        capabilities = (
            "\n\nTOOL AVAILABILITY FOR THIS REQUEST: No tools are available. "
            "Web search, browser.search, browser.open, Python and function calls are disabled. "
            "Do not emit or attempt tool calls. Answer in plain text using only the supplied "
            "admissions evidence. If current facts are missing, explain the gap and provide "
            "the official source to check; do not claim to have browsed or verified it live."
        )
        if messages and messages[0]["role"] == "system":
            messages[0]["content"] += capabilities
        else:
            messages.insert(0, {"role": "system", "content": capabilities.strip()})
        if provider == "anthropic":
            async with anthropic.AsyncAnthropic(
                api_key=self._key(provider), timeout=settings.LLM_TIMEOUT_SECONDS, max_retries=0
            ) as client:
                resp = await client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system="\n".join(m["content"] for m in messages if m["role"] == "system"),
                    messages=[m for m in messages if m["role"] != "system"],
                )
                if resp.stop_reason != "end_turn":
                    raise LLMUnavailableError("The provider returned an incomplete answer.")
                return "".join(b.text for b in resp.content if b.type == "text"), (
                    resp.usage.input_tokens + resp.usage.output_tokens
                )
        async with self._openai_client(provider) as client:
            resp = await client.chat.completions.create(
                model=model,
                messages=messages,
                max_completion_tokens=max_tokens,
                tool_choice="none",
            )
            if not resp.choices or resp.choices[0].finish_reason != "stop":
                raise LLMUnavailableError("The provider returned an incomplete answer.")
            return resp.choices[0].message.content or "", (
                resp.usage.total_tokens if resp.usage else 0
            )

    async def _stream_provider(self, provider, messages, max_tokens):
        if provider == "gemini":
            from app.core.gemini_client import GeminiClient

            client = GeminiClient(web_search=self.web_search, model=settings.model_for(provider))
            async for chunk in client.stream(messages, max_tokens):
                yield chunk
            self._capture_gemini(client)
        else:
            # Complete before delivery: a failed or truncated provider response
            # must not be concatenated with another provider's replacement answer.
            text, tokens = await self._complete_provider(provider, messages, max_tokens)
            self.usage_tokens = tokens
            yield text

    async def _run(self, messages, max_tokens, streaming):
        self._reset()
        last_error = LLMUnavailableError("No AI provider is configured.")
        try:
            async with asyncio.timeout(settings.LLM_FAILOVER_TIMEOUT_SECONDS):
                for provider in self.providers:
                    self._reset()
                    try:
                        async with asyncio.timeout(settings.LLM_TIMEOUT_SECONDS):
                            if streaming:
                                parts = []
                                async for chunk in self._stream_provider(
                                    provider, messages, max_tokens
                                ):
                                    parts.append(chunk)
                                text = self.final_text or "".join(parts)
                                tokens = self.usage_tokens or 0
                            else:
                                text, tokens = await self._complete_provider(
                                    provider, messages, max_tokens
                                )
                            if not text.strip():
                                raise LLMUnavailableError("The provider returned an empty answer.")
                            self.final_text, self.usage_tokens = text, tokens
                            self.provider_used, self.model_used = (
                                provider,
                                settings.model_for(provider),
                            )
                            return text, tokens
                    except (openai.RateLimitError, anthropic.RateLimitError):
                        last_error = LLMQuotaError()
                    except (
                        openai.APIError,
                        anthropic.APIError,
                        TimeoutError,
                        ValueError,
                        TypeError,
                        AttributeError,
                    ):
                        last_error = LLMUnavailableError()
                    except (LLMQuotaError, LLMUnavailableError) as exc:
                        last_error = exc
                    logger.warning(
                        "AI provider %s failed (%s); trying next configured provider",
                        provider,
                        type(last_error).__name__,
                    )
        except TimeoutError:
            last_error = LLMUnavailableError("AI provider failover timed out.")
        self._reset()
        raise last_error

    async def complete(self, messages: PromptMessages, max_tokens: int = 4096) -> tuple[str, int]:
        return await self._run(messages, max_tokens, streaming=False)

    async def stream(self, messages: PromptMessages, max_tokens: int = 4096) -> AsyncIterator[str]:
        text, _ = await self._run(messages, max_tokens, streaming=True)
        yield text
