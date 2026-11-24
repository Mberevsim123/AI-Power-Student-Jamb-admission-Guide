"""
app/main.py — FastAPI application factory
"""

from contextlib import asynccontextmanager
from importlib import import_module

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import setup_logging

# Import route modules explicitly to avoid ambiguous "from package import name"
# behavior with namespace packages.
admission = import_module("app.api.v1.routes.admission")
agent = import_module("app.api.v1.routes.agent")
auth = import_module("app.api.v1.routes.auth")
chat = import_module("app.api.v1.routes.chat")


# ---------------------------------------------------------------------------
# Lifespan: startup / shutdown
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    setup_logging()

    try:
        from app.rag.retriever import ensure_collection

        await ensure_collection()
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "Qdrant unavailable; knowledge retrieval will be limited"
        )

    # Self-heal: a fresh database or a skipped release step still gets every
    # NUC-approved university (idempotent, lock-protected across workers).
    from app.db.session import AsyncSessionLocal
    from app.services.directory import ensure_directory

    await ensure_directory(AsyncSessionLocal)

    yield  # app is running

    # Shutdown — close Redis pool
    try:
        from app.utils.cache import _redis

        if _redis:
            await _redis.aclose()
    except Exception:
        pass
    from app.db.session import engine

    await engine.dispose()


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    from pathlib import Path

    proj_root = Path(__file__).resolve().parents[1]
    docs_enabled = settings.DEBUG

    app = FastAPI(
        title=settings.APP_NAME,
        description=(
            "AI-powered chatbot API for JAMB and Nigerian university admissions guidance."
        ),
        version="1.0.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Exception handlers ────────────────────────────────────────────────
    register_exception_handlers(app)

    # ── Routers ───────────────────────────────────────────────────────────
    prefix = "/api/v1"
    app.include_router(auth.router, prefix=prefix)
    app.include_router(chat.router, prefix=prefix)
    app.include_router(admission.router, prefix=prefix)
    app.include_router(agent.router, prefix=prefix)

    from app.api.v1.routes import admin

    app.include_router(admin.router, prefix=prefix)

    # Register health before the root static mount so it remains reachable.
    @app.get("/health", tags=["meta"])
    async def health():
        """Liveness: the process is serving requests."""
        return {"status": "ok", "service": settings.APP_NAME}

    @app.get("/health/ready", tags=["meta"])
    async def ready():
        """Readiness: the database answers. Used as the deployment health check."""
        from fastapi.responses import JSONResponse
        from sqlalchemy import text

        from app.db.session import engine

        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "unavailable", "database": "down"}, status_code=503)
        return {"status": "ok", "database": "up"}

    # Vite emits its production assets here; legacy UI remains a fallback.
    ui_path = proj_root / "client" / "dist"
    if not ui_path.exists():
        ui_path = proj_root / "ui"
    if ui_path.exists():
        app.mount("/", StaticFiles(directory=str(ui_path), html=True), name="ui")

    return app


app = create_app()
