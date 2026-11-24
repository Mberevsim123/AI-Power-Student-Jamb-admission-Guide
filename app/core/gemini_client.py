"""Native Gemini generation and Google Search grounding, without exposing API keys."""

import asyncio
import json
import math
import re
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import httpx

from app.core.config import settings
from app.core.exceptions import LLMQuotaError, LLMUnavailableError


def safe_web_url(value):
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        if url.scheme in ("https", "http") and url.hostname and not url.username:
            return value
    except ValueError:
        pass
    return None


class GeminiClient:
    def __init__(self, web_search=False, model=None):
        self.model = model or settings.model_for("gemini")
        self.web_search = web_search
        self.grounding = {}
        self.sources = []
        self.final_text = None
        self.token_count = 0
        self._chunks = []
        self._supports = []
        self._queries = []
        self._suggestions = ""
        self._finished = False

    def _client(self):
        key = settings.GEMINI_API_KEY.strip()
        if not key or key.startswith(("your-", "your_", "replace-")):
            raise LLMUnavailableError("Add GEMINI_API_KEY to the backend .env and restart the API.")
        return httpx.AsyncClient(
            base_url="https://generativelanguage.googleapis.com/v1beta/",
            headers={"x-goog-api-key": key},
            timeout=httpx.Timeout(settings.LLM_TIMEOUT_SECONDS, connect=10),
        )

    def _payload(self, messages, max_tokens):
        payload = {
            "systemInstruction": {
                "parts": [
                    {"text": "\n\n".join(m["content"] for m in messages if m["role"] == "system")}
                ]
            },
            "contents": [
                {
                    "role": "model" if m["role"] == "assistant" else "user",
                    "parts": [{"text": m["content"]}],
                }
                for m in messages
                if m["role"] in ("user", "assistant")
            ],
            # Gemini's output budget includes reasoning; tiny classifier budgets
            # can otherwise finish before producing a user-visible label.
            "generationConfig": {
                "maxOutputTokens": max(max_tokens, settings.GEMINI_MAX_OUTPUT_TOKENS),
            },
        }
        if self.web_search:
            payload["tools"] = [{"google_search": {}}]
        return payload

    def _endpoint(self, method):
        return f"models/{quote(self.model, safe='-._')}:{method}"

    @staticmethod
    def _check_status(response):
        if response.status_code == 429:
            try:
                error = response.json().get("error", {})
            except (ValueError, AttributeError):
                error = {}
            raise GeminiClient._quota_error(error)
        if response.status_code in (400, 401, 403, 404):
            raise LLMUnavailableError(
                "Gemini could not accept this request. Check the API key, model, and Google Search access."
            )
        if not response.is_success:
            raise LLMUnavailableError("Gemini is temporarily unavailable. Please try again.")

    @staticmethod
    def _quota_error(error):
        """Translate structured Google quota details; never expose its raw error body."""
        if not isinstance(error, dict):
            error = {}
        violations = []
        retry_after = None
        for detail in error.get("details", []):
            if not isinstance(detail, dict):
                continue
            if detail.get("@type", "").endswith("QuotaFailure"):
                violations.extend(v for v in detail.get("violations", []) if isinstance(v, dict))
            if detail.get("@type", "").endswith("RetryInfo"):
                delay = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(detail.get("retryDelay", "")))
                if delay:
                    retry_after = min(86400, max(1, math.ceil(float(delay[1]))))
        no_quota = any(str(v.get("quotaValue", "")) in ("0", "0.0") for v in violations)
        search_quota = any(
            re.search(
                r"search|ground", str(v.get("quotaMetric", "")) + str(v.get("quotaId", "")), re.I
            )
            for v in violations
        )
        if no_quota:
            return LLMQuotaError(
                "Gemini has no quota allocated for this model or Google Search. "
                "The administrator must check the API project's billing and model/search quotas. "
                "Waiting or sending the question again will not resolve a zero quota."
            )
        if search_quota:
            return LLMQuotaError(
                "Google Search grounding quota is unavailable or exhausted. "
                "The administrator must check the project's Search grounding limits in Google AI Studio.",
                retry_after=retry_after,
            )
        if retry_after:
            return LLMQuotaError(
                f"Gemini's request limit was reached. Try again in {retry_after} seconds. "
                "If it continues, the administrator should check the API project's quota and billing.",
                retry_after=retry_after,
            )
        return LLMQuotaError(
            "Gemini's API quota is unavailable or exhausted. "
            "The administrator must check the selected model's quota and billing in Google AI Studio."
        )

    def _consume(self, data):
        if data.get("error"):
            if data["error"].get("code") == 429:
                raise self._quota_error(data["error"])
            raise LLMUnavailableError()
        if data.get("promptFeedback", {}).get("blockReason"):
            raise LLMUnavailableError("Gemini could not answer that request. Please rephrase it.")
        usage = data.get("usageMetadata", {})
        self.token_count = usage.get("totalTokenCount", self.token_count)
        candidates = data.get("candidates", [])
        if not candidates:
            return ""
        candidate = candidates[0]
        reason = candidate.get("finishReason")
        if reason:
            if reason != "STOP":
                raise LLMUnavailableError(
                    "Gemini could not complete the answer. Please try a shorter or rephrased question."
                )
            self._finished = True
        metadata = candidate.get("groundingMetadata", {})
        self._chunks.extend(metadata.get("groundingChunks", []))
        self._supports.extend(metadata.get("groundingSupports", []))
        self._queries.extend(metadata.get("webSearchQueries", []))
        self._suggestions = metadata.get("searchEntryPoint", {}).get(
            "renderedContent", self._suggestions
        )
        return "".join(
            part.get("text", "")
            for part in candidate.get("content", {}).get("parts", [])
            if not part.get("thought")
        )

    def _finalize(self, text):
        if not self._finished or not text.strip():
            raise LLMUnavailableError("Gemini returned an incomplete answer. Please try again.")
        retrieved_at = datetime.now(timezone.utc).isoformat()
        for chunk in self._chunks:
            web = chunk.get("web", {})
            url = safe_web_url(web.get("uri"))
            if url and not any(s["source_url"] == url for s in self.sources):
                self.sources.append(
                    {
                        "doc_title": web.get("title") or "Web publication",
                        "source_url": url,
                        "origin": "web",
                        "retrieved_at": retrieved_at,
                    }
                )
        # Citation indices refer to provider chunks, not the deduplicated source list.
        insertions = {}
        encoded = text.encode("utf-8")
        for support in self._supports:
            segment = support.get("segment", {})
            end = segment.get("endIndex")
            segment_text = segment.get("text")
            offset = None
            if isinstance(end, int) and 0 < end <= len(encoded):
                try:
                    prefix = encoded[:end].decode("utf-8")
                    if not segment_text or prefix.endswith(segment_text):
                        offset = len(prefix)
                except UnicodeDecodeError:
                    pass
            # Streamed parts can use local byte offsets. Locate their supplied
            # segment text in the accumulated answer rather than corrupt Unicode.
            if offset is None and segment_text:
                start = text.rfind(segment_text)
                if start >= 0:
                    offset = start + len(segment_text)
            if offset is None:
                continue
            for index in support.get("groundingChunkIndices", []):
                if not isinstance(index, int) or not 0 <= index < len(self._chunks):
                    continue
                url = safe_web_url(self._chunks[index].get("web", {}).get("uri"))
                if url:
                    # Angle-bracket destinations prevent parentheses in URLs from
                    # escaping the Markdown link; encoded brackets remain inert.
                    url = url.replace("<", "%3C").replace(">", "%3E").replace("\n", "")
                    insertions.setdefault(offset, set()).add(f"[Web {index + 1}](<{url}>)")
        for offset in sorted(insertions, reverse=True):
            text = text[:offset] + " " + ", ".join(sorted(insertions[offset])) + text[offset:]
        if self._queries or self.sources or self._suggestions:
            self.grounding = {
                "provider": "google_search",
                "retrieved_at": retrieved_at,
                "search_queries": list(dict.fromkeys(self._queries)),
                "sources": self.sources,
                "search_suggestions_html": self._suggestions,
            }
        self.final_text = text
        return text

    async def complete(self, messages, max_tokens):
        try:
            async with asyncio.timeout(settings.LLM_TIMEOUT_SECONDS):
                async with self._client() as client:
                    response = await client.post(
                        self._endpoint("generateContent"), json=self._payload(messages, max_tokens)
                    )
                    self._check_status(response)
                    text = self._consume(response.json())
                    return self._finalize(text), self.token_count
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            raise LLMUnavailableError(
                "Could not complete the Gemini request. Please try again."
            ) from exc

    async def stream(self, messages, max_tokens):
        text = ""
        try:
            async with asyncio.timeout(settings.LLM_TIMEOUT_SECONDS):
                async with self._client() as client:
                    async with client.stream(
                        "POST",
                        self._endpoint("streamGenerateContent"),
                        params={"alt": "sse"},
                        json=self._payload(messages, max_tokens),
                    ) as response:
                        if not response.is_success:
                            await response.aread()
                        self._check_status(response)
                        frame = []
                        async for line in response.aiter_lines():
                            if line.startswith("data:"):
                                frame.append(line[5:].lstrip())
                            elif not line and frame:
                                delta = self._consume(json.loads("\n".join(frame)))
                                frame = []
                                if delta:
                                    text += delta
                                    yield delta
                        if frame:
                            delta = self._consume(json.loads("\n".join(frame)))
                            text += delta
                            if delta:
                                yield delta
                        self._finalize(text)
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            raise LLMUnavailableError(
                "The Gemini response was interrupted. Please try again."
            ) from exc
