"""Production client and API must remain reachable on the same origin."""

import re
from pathlib import Path

import httpx
import pytest

from app.main import create_app


async def test_built_client_does_not_shadow_health_or_api():
    if not Path("client/dist/index.html").exists():
        pytest.skip("Build the Vite client before checking production static serving")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        root = await client.get("/")
        assert root.status_code == 200
        assert "JAMB Guide" in root.text
        asset = re.search(r'src="([^"]+\.js)"', root.text)
        assert asset
        assert (await client.get(asset.group(1))).status_code == 200
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        # A protected API route must stay under API control, not the static mount.
        assert (await client.get("/api/v1/auth/me")).status_code in (401, 403)


async def test_readiness_reports_database_state(monkeypatch):
    import os

    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("Readiness needs a reachable database (TEST_DATABASE_URL)")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        ready = await client.get("/health/ready")
        assert ready.status_code == 200 and ready.json()["database"] == "up"

    import app.db.session as session

    class Down:
        def connect(self):
            raise OSError("database unreachable")

    monkeypatch.setattr(session, "engine", Down())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        down = await client.get("/health/ready")
        assert down.status_code == 503 and down.json()["database"] == "down"
