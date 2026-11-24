# app/api/routes/chat.py
"""
Chat routes — /api/v1/chat
"""

import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import get_consented_user
from app.core.exceptions import JAMBGuideException
from app.core.llm_client import LLMClient
from app.core.rate_limit import chat_limiter, search_limiter
from app.db.session import get_db
from app.models.chat import MessageFeedback
from app.models.user import User
from app.rag.prompt_builder import PromptBuilder
from app.rag.retriever import RAGRetriever
from app.repositories.repository import ChatRepository
from app.schemas.schema import (
    ChatRequest,
    ConversationDetailResponse,
    ConversationResponse,
    FeedbackRequest,
    MessageResponse,
)
from app.services.admission_service import AdmissionService
from app.services.chat_service import ChatService
from app.services.intent_service import IntentClassifier

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


# ---------------------------------------------------------------------------
# Dependency factories
# ---------------------------------------------------------------------------


def get_chat_repo(db: AsyncSession = Depends(get_db)) -> ChatRepository:
    return ChatRepository(db)


def get_chat_service(db: AsyncSession = Depends(get_db)) -> ChatService:
    """
    Wires every ChatService collaborator explicitly.
    Single-instance stateless helpers (IntentClassifier, RAGRetriever,
    PromptBuilder, LLMClient) are constructed here per-request cheaply;
    if they hold heavy resources (model weights, connection pools) they
    should be singletons injected via app.state instead.
    """
    return ChatService(
        db=db,
        chat_repo=ChatRepository(db),
        adm_service=AdmissionService(db),
        intent_clf=IntentClassifier(),
        retriever=RAGRetriever(db),
        prompt_bld=PromptBuilder(),
        llm=LLMClient(web_search=settings.GEMINI_WEB_SEARCH_ENABLED),
    )


# ---------------------------------------------------------------------------
# Send a message (non-streaming)
# ---------------------------------------------------------------------------


@router.post(
    "/",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Send a message and get a synchronous response",
)
async def send_message(
    body: ChatRequest,
    current_user: User = Depends(get_consented_user),
    svc: ChatService = Depends(get_chat_service),
    _rate: None = Depends(chat_limiter),
):
    if getattr(body, "stream", False):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Use POST /chat/stream for streaming responses.",
        )
    # FIX: pass user object, not user_id — matches new ChatService signature
    return await svc.chat(
        user=current_user,
        user_message=body.message,
        conversation_id=body.conversation_id,
    )


# ---------------------------------------------------------------------------
# Streaming endpoint (Server-Sent Events)
# ---------------------------------------------------------------------------


@router.post(
    "/stream",
    summary="Send a message and stream the response via SSE",
)
async def send_message_stream(
    body: ChatRequest,
    current_user: User = Depends(get_consented_user),
    svc: ChatService = Depends(get_chat_service),
    _rate: None = Depends(chat_limiter),
):
    if body.conversation_id:
        conv = await svc.chat_repo.get_conversation(body.conversation_id, current_user.id)
        if conv is None:
            raise HTTPException(404, "Conversation not found")

    async def event_generator() -> AsyncIterator[str]:
        sent_metadata = False
        try:
            # FIX: pass user object, not user_id — matches new ChatService signature
            async for chunk in svc.chat_stream(
                user=current_user,
                user_message=body.message,
                conversation_id=body.conversation_id,
            ):
                if not sent_metadata:
                    metadata = {
                        "conversation_id": svc.conversation_id,
                        "message_id": svc.message_id,
                    }
                    yield f"event: metadata\ndata: {json.dumps(metadata)}\n\n"
                    sent_metadata = True
                if isinstance(chunk, dict):
                    # Agent plan: actions taken, nudges, options and quick replies.
                    yield f"event: agent\ndata: {json.dumps(chunk['agent'], default=str)}\n\n"
                    continue
                yield f"data: {json.dumps({'delta': chunk})}\n\n"

            yield "data: [DONE]\n\n"

        except JAMBGuideException as exc:
            if not sent_metadata and getattr(svc, "conversation_id", None):
                metadata = {"conversation_id": svc.conversation_id, "message_id": svc.message_id}
                yield f"event: metadata\ndata: {json.dumps(metadata)}\n\n"
            logger.warning(
                "Chat response failed: status=%s detail=%s", exc.status_code, exc.message
            )
            error = {"detail": exc.message, "status": exc.status_code}
            if getattr(exc, "retry_after", None):
                error["retry_after"] = exc.retry_after
            yield f"event: error\ndata: {json.dumps(error)}\n\n"
        except Exception:
            logger.exception("Stream failed for user %s", current_user.id)
            yield 'event: error\ndata: {"detail": "An error occurred. Please try again.", "status": 503}\n\n'

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# List conversations
# ---------------------------------------------------------------------------


@router.get(
    "/conversations",
    response_model=list[ConversationResponse],
    summary="List the current user's conversations",
)
async def list_conversations(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_consented_user),
    repo: ChatRepository = Depends(get_chat_repo),
    _rate: None = Depends(search_limiter),
):
    return await repo.list_conversations(current_user.id, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# Get a single conversation with its messages
# ---------------------------------------------------------------------------


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
    summary="Get a conversation and its full message history",
)
async def get_conversation(
    conversation_id: int,
    current_user: User = Depends(get_consented_user),
    repo: ChatRepository = Depends(get_chat_repo),
    _rate: None = Depends(search_limiter),
):
    conv = await repo.get_conversation_with_messages(conversation_id, current_user.id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


# ---------------------------------------------------------------------------
# Soft-delete a conversation
# ---------------------------------------------------------------------------


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a conversation",
)
async def delete_conversation(
    conversation_id: int,
    current_user: User = Depends(get_consented_user),
    db: AsyncSession = Depends(get_db),
    repo: ChatRepository = Depends(get_chat_repo),
    _rate: None = Depends(search_limiter),
):
    conv = await repo.get_conversation(conversation_id, current_user.id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await repo.soft_delete_conversation(conversation_id, current_user.id)
    await db.commit()


# ---------------------------------------------------------------------------
# Submit feedback on an assistant message
# ---------------------------------------------------------------------------


@router.post(
    "/messages/{message_id}/feedback",
    status_code=status.HTTP_201_CREATED,
    summary="Rate an assistant response (thumbs up / down)",
)
async def submit_feedback(
    message_id: int,
    body: FeedbackRequest,
    current_user: User = Depends(get_consented_user),
    db: AsyncSession = Depends(get_db),
    repo: ChatRepository = Depends(get_chat_repo),
    _rate: None = Depends(search_limiter),
):
    message = await repo.get_message_for_user(message_id, current_user.id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    existing = await repo.get_feedback(message_id, current_user.id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Feedback already submitted for this message",
        )

    feedback = MessageFeedback(
        message_id=message_id,
        user_id=current_user.id,
        rating=body.rating,
        comment=body.comment,
    )
    await repo.save_feedback(feedback)
    await db.commit()
    return {"status": "ok"}
