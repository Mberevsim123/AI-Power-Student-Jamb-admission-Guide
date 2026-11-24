"""Check Gemini configuration without revealing API keys or raw provider errors.

By default this only lists model availability. --generate performs one small
generation request; --search enables Google Search for that one request.
"""

import argparse
import asyncio
import json

import httpx

from app.core.config import settings
from app.core.exceptions import JAMBGuideException
from app.core.gemini_client import GeminiClient


async def check(generate=False, search=False):
    report = {
        "model": settings.model_for("gemini"),
        "key_configured": bool(settings.GEMINI_API_KEY.strip()),
        "search_enabled": settings.GEMINI_WEB_SEARCH_ENABLED,
    }
    try:
        async with GeminiClient()._client() as client:
            model_path = GeminiClient()._endpoint("generateContent").rsplit(":", 1)[0]
            response = await client.get(model_path)
            GeminiClient._check_status(response)
            model = response.json()
            report["model_available"] = model.get(
                "name"
            ) == f"models/{settings.model_for('gemini')}" and "generateContent" in model.get(
                "supportedGenerationMethods", []
            )
            if generate:
                payload = {
                    "contents": [{"parts": [{"text": "Reply with OK."}]}],
                    "generationConfig": {"maxOutputTokens": 32},
                }
                if search:
                    payload["contents"][0]["parts"][0]["text"] = (
                        "Find the official JAMB website and cite it."
                    )
                    payload["tools"] = [{"google_search": {}}]
                response = await client.post(
                    GeminiClient()._endpoint("generateContent"), json=payload
                )
                report["generation_http_status"] = response.status_code
                report["search_requested"] = search
                GeminiClient._check_status(response)
    except JAMBGuideException as exc:
        report["error"] = exc.message
        if getattr(exc, "retry_after", None):
            report["retry_after_seconds"] = exc.retry_after
    except (httpx.HTTPError, ValueError):
        report["error"] = "The provider check could not complete. Check connectivity and try again."
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--generate", action="store_true", help="Make one small generation request."
    )
    parser.add_argument("--search", action="store_true", help="Include Google Search in the probe.")
    args = parser.parse_args()
    if args.search and not args.generate:
        parser.error("--search requires --generate")
    asyncio.run(check(args.generate, args.search))
