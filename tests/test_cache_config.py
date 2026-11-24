from app.utils import cache


async def test_local_redis_uses_resp2_without_maintenance_notifications(monkeypatch):
    monkeypatch.setattr(cache, "_redis", None)
    client = await cache.get_redis()
    try:
        assert client.connection_pool.connection_kwargs["protocol"] == 2
    finally:
        await client.aclose()


def test_platform_database_urls_use_asyncpg():
    from app.core.config import Settings

    plain = Settings(DATABASE_URL="postgresql://u:p@db.railway.internal:5432/railway")
    assert plain.DATABASE_URL == "postgresql+asyncpg://u:p@db.railway.internal:5432/railway"
    legacy = Settings(DATABASE_URL="postgres://u:p@host/db?sslmode=require")
    assert legacy.DATABASE_URL == "postgresql+asyncpg://u:p@host/db?ssl=require"


def test_production_refuses_placeholder_secret_and_debug():
    import pytest
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(ENVIRONMENT="production", SECRET_KEY="change-me-in-production", DEBUG=False)
    with pytest.raises(ValidationError, match="DEBUG"):
        Settings(ENVIRONMENT="production", SECRET_KEY="x" * 48, DEBUG=True)
    assert Settings(ENVIRONMENT="production", SECRET_KEY="x" * 48, DEBUG=False).ENVIRONMENT
