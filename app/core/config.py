"""
app/core/config.py — all environment-driven settings
"""

from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_SECRETS = {"", "change-me-in-production", "replace-with-a-random-local-secret"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    APP_NAME: str = "JAMB Guide API"
    ENVIRONMENT: Literal["development", "test", "production"] = "development"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production"

    @field_validator("DEBUG", mode="before")
    @classmethod
    def debug_build_label(cls, value):
        # Some IDE environments export DEBUG as a build configuration label.
        if isinstance(value, str) and value.casefold() in ("release", "debug"):
            return value.casefold() == "debug"
        return value

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://user:pass@localhost/jamb_db"

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def async_driver(cls, value):
        """Accept platform URLs (postgres://, postgresql://) and use the asyncpg driver.

        Hosted Postgres (e.g. Railway) supplies a plain URL; asyncpg also spells the
        libpq ``sslmode`` parameter as ``ssl``.
        """
        if not isinstance(value, str):
            return value
        parts = urlsplit(value.strip())
        if parts.scheme in ("postgres", "postgresql"):
            parts = parts._replace(scheme="postgresql+asyncpg")
        if parts.scheme == "postgresql+asyncpg" and parts.query:
            query = [("ssl" if k == "sslmode" else k, v) for k, v in parse_qsl(parts.query)]
            parts = parts._replace(query=urlencode(query))
        return urlunsplit(parts)

    # JWT
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # LLM
    LLM_PROVIDER: Literal["groq", "openai", "anthropic", "gemini"] = "groq"
    # LLM_MODEL is a legacy override for the primary provider only.
    LLM_MODEL: str = ""
    LLM_FALLBACK_PROVIDERS: list[Literal["groq", "gemini", "openai", "anthropic"]] = [
        "gemini",
        "openai",
        "anthropic",
    ]
    GROQ_MODEL: str = "openai/gpt-oss-20b"
    GEMINI_MODEL: str = "gemini-3.8-flash"
    OPENAI_MODEL: str = "gpt-4.1-mini"
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"
    LLM_FAILOVER_TIMEOUT_SECONDS: float = Field(default=60, gt=0)
    LLM_TIMEOUT_SECONDS: float = Field(default=20, gt=0)
    INTENT_LLM_FALLBACK: bool = False
    CHAT_HISTORY_MESSAGES: int = Field(default=20, ge=20, le=100)
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    GEMINI_WEB_SEARCH_ENABLED: bool = False
    GEMINI_MAX_OUTPUT_TOKENS: int = Field(default=4096, ge=256, le=32768)

    def model_for(self, provider: str) -> str:
        if provider == self.LLM_PROVIDER and self.LLM_MODEL:
            return self.LLM_MODEL
        return getattr(self, f"{provider.upper()}_MODEL")

    # Embeddings
    EMBEDDING_PROVIDER: Literal["local", "openai"] = "local"
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    EMBEDDING_DIMENSIONS: int = Field(default=384, gt=0)

    # Vector store
    VECTOR_STORE: Literal["qdrant"] = "qdrant"
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str = ""
    QDRANT_COLLECTION: str = "jamb_docs_minilm_384"

    # RAG
    RAG_TOP_K: int = Field(default=3, ge=1, le=10)
    RAG_SCORE_THRESHOLD: float = Field(default=0.35, ge=0, le=1)

    # Context budgets (characters, ~4 per token) keep prompts focused and affordable.
    PROMPT_CONTEXT_CHARS: int = Field(default=24000, ge=4000)
    PROMPT_HISTORY_CHARS: int = Field(default=24000, ge=2000)

    # Redis (rate limiting / caching)
    REDIS_URL: str = "redis://localhost:6379"

    # CORS
    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

    @model_validator(mode="after")
    def production_guards(self):
        """Refuse to start an insecure production deployment."""
        if self.ENVIRONMENT == "production":
            if self.SECRET_KEY.strip() in PLACEHOLDER_SECRETS or len(self.SECRET_KEY) < 32:
                raise ValueError(
                    "Set SECRET_KEY to a random value of at least 32 characters in production: "
                    "python -c 'import secrets; print(secrets.token_urlsafe(48))'"
                )
            if self.DEBUG:
                raise ValueError("DEBUG must be false in production (it exposes /docs).")
        return self


settings = Settings()
