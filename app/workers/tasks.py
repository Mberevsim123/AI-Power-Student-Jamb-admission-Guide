"""
app/workers/tasks.py
Celery tasks for background processing:
  - Document ingestion & embedding
  - Conversation summarisation
  - Admission data refresh
"""

import asyncio
import logging

from celery import Celery

from app.core.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "jamb_guide",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Africa/Lagos",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)


def _run(coro):
    """Run an async coroutine from a sync Celery task."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Document ingestion
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="tasks.ingest_document",
    max_retries=3,
    default_retry_delay=60,
)
def ingest_document(self, document_id: int) -> dict:
    """
    Chunk a KnowledgeDocument, generate embeddings, upsert into Qdrant.
    Triggered after a new document is uploaded.
    """
    try:
        return _run(_ingest_document_async(document_id))
    except Exception as exc:
        logger.exception("ingest_document failed for doc %d", document_id)
        raise self.retry(exc=exc)


async def _ingest_document_async(document_id: int) -> dict:
    from app.api.v1.routes.admin import index_document

    await index_document(document_id)
    return {"document_id": document_id}


# ---------------------------------------------------------------------------
# Conversation summarisation
# ---------------------------------------------------------------------------


@celery_app.task(
    name="tasks.summarise_conversation",
    max_retries=2,
)
def summarise_conversation(conversation_id: int) -> dict:
    """
    Compress a long conversation into a running summary that gets
    prepended to future prompts instead of full history.
    Triggered when message_count reaches a threshold (e.g. 30).
    """
    try:
        return _run(_summarise_async(conversation_id))
    except Exception as exc:
        logger.exception("summarise_conversation failed for conv %d", conversation_id)
        return {"status": "failed", "error": str(exc)}


async def _summarise_async(conversation_id: int) -> dict:
    from sqlalchemy import update

    from app.core.llm_client import LLMClient
    from app.db.session import AsyncSessionLocal
    from app.models.chat import Conversation
    from app.repositories.repository import ChatRepository

    async with AsyncSessionLocal() as db:
        repo = ChatRepository(db)
        messages = await repo.get_recent_messages(conversation_id, limit=40)

        if len(messages) < 10:
            return {"status": "skipped", "reason": "not enough messages"}

        history_text = "\n".join(f"{m.role.upper()}: {m.content}" for m in messages if m.content)

        prompt = [
            {
                "role": "system",
                "content": (
                    "You summarise student–chatbot conversations about JAMB and "
                    "Nigerian university admissions. Write a concise 3–5 sentence "
                    "summary capturing: student's JAMB score, preferred course, "
                    "schools discussed, and key advice given."
                ),
            },
            {
                "role": "user",
                "content": f"Summarise this conversation:\n\n{history_text}",
            },
        ]

        llm = LLMClient()
        summary, _ = await llm.complete(prompt, max_tokens=300)

        await db.execute(
            update(Conversation).where(Conversation.id == conversation_id).values(summary=summary)
        )
        await db.commit()

        logger.info("Summarised conversation %d", conversation_id)
        return {"status": "ok", "conversation_id": conversation_id}


# ---------------------------------------------------------------------------
# Admission data refresh
# ---------------------------------------------------------------------------


@celery_app.task(
    name="tasks.refresh_cutoff_cache",
)
def refresh_cutoff_cache() -> dict:
    """
    Bust Redis cache for cutoff marks after admin uploads new data.
    Schedule via Celery Beat or trigger manually after bulk imports.
    """
    return _run(_bust_cache())


async def _bust_cache() -> dict:
    from app.utils.cache import get_redis

    r = await get_redis()
    keys = await r.keys("jamb:cutoff*")
    if keys:
        await r.delete(*keys)
        logger.info("Busted %d cutoff cache keys", len(keys))
    return {"busted": len(keys)}


# ---------------------------------------------------------------------------
# Celery Beat schedule (periodic tasks)
# ---------------------------------------------------------------------------

celery_app.conf.beat_schedule = {
    "refresh-cutoff-cache-nightly": {
        "task": "tasks.refresh_cutoff_cache",
        "schedule": 86400,  # every 24 hours
    },
}
