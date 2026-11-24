"""
Chat domain models
------------------
Conversation ──< Message ──< MessageFeedback
"""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

if TYPE_CHECKING:
    from app.models.user import User  # noqa: F401

from app.db.base import Base

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class MessageRole(str, Enum):
    user = "user"
    assistant = "assistant"
    system = "system"


class MessageState(str, Enum):
    pending = "pending"
    streaming = "streaming"
    completed = "completed"
    failed = "failed"


class ConversationStatus(str, Enum):
    active = "active"
    archived = "archived"
    deleted = "deleted"


class QueryIntent(str, Enum):
    """Classified intent of a user message — drives RAG routing."""

    cutoff_lookup = "cutoff_lookup"
    course_eligibility = "course_eligibility"
    subject_combination = "subject_combination"
    school_recommendation = "school_recommendation"
    olevel_requirement = "olevel_requirement"
    post_utme_info = "post_utme_info"
    jamb_process = "jamb_process"
    general_admission = "general_admission"
    greeting = "greeting"
    out_of_scope = "out_of_scope"


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','archived','deleted')",
            name="ck_conversations_status",
        ),
        CheckConstraint(
            "(status != 'deleted') OR (deleted_at IS NOT NULL)",
            name="ck_conversations_deleted_ts",
        ),
        Index("ix_conversations_user_status", "user_id", "status"),
        Index("ix_conversations_user_updated", "user_id", "updated_at"),
        Index("ix_conversations_created", "created_at"),
    )

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    user_id: int = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    title: str = Column(String(200), nullable=False, default="New Chat")
    status: str = Column(String(20), nullable=False, server_default=ConversationStatus.active.value)
    student_context: Dict[str, Any] = Column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    summary: Optional[str] = Column(String(5000), nullable=True)
    message_count: int = Column(Integer, nullable=False, default=0)
    total_tokens: int = Column(Integer, nullable=False, default=0)
    last_message_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    created_at: datetime = Column(DateTime(timezone=True), server_default=func.now())
    updated_at: datetime = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)

    user: "User" = relationship("User", back_populates="conversations", lazy="joined")
    messages: List["Message"] = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        # A user message and its reply share a transaction timestamp: break ties by id.
        order_by=lambda: [Message.created_at, Message.id],
        lazy="noload",
    )


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user','assistant','system')",
            name="ck_messages_role",
        ),
        CheckConstraint(
            "state IN ('pending','streaming','completed','failed')",
            name="ck_messages_state",
        ),
        CheckConstraint("token_count IS NULL OR token_count >= 0"),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
        Index("ix_messages_role", "role"),
        Index("ix_messages_intent", "intent"),
        Index("ix_messages_state", "state"),
        Index("ix_messages_created", "created_at"),
    )

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id: int = Column(
        Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: int = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: str = Column(String(20), nullable=False)
    state: str = Column(String(20), nullable=False, server_default=MessageState.completed.value)
    content: Optional[str] = Column(String(20000), nullable=True)
    intent: Optional[str] = Column(String(50), nullable=True)
    rag_sources: List[Dict[str, Any]] = Column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    structured_context: Dict[str, Any] = Column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    model_used: Optional[str] = Column(String(100), nullable=True)
    token_count: Optional[int] = Column(Integer, nullable=True)
    latency_ms: Optional[int] = Column(Integer, nullable=True)
    error_message: Optional[str] = Column(String(1000), nullable=True)

    created_at: datetime = Column(DateTime(timezone=True), server_default=func.now())
    updated_at: datetime = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    conversation: "Conversation" = relationship(
        "Conversation", back_populates="messages", lazy="joined"
    )
    feedback: Optional["MessageFeedback"] = relationship(
        "MessageFeedback",
        back_populates="message",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="noload",
    )


# ---------------------------------------------------------------------------
# MessageFeedback  —  thumbs up/down + optional comment per assistant turn
# ---------------------------------------------------------------------------


class MessageFeedback(Base):
    __tablename__ = "message_feedback"
    __table_args__ = (
        CheckConstraint("rating IN (-1, 1)", name="ck_feedback_rating"),
        Index("ix_feedback_message", "message_id", unique=True),
        Index("ix_feedback_rating", "rating"),
        Index("ix_feedback_created", "created_at"),
    )
    id: int = Column(Integer, primary_key=True, autoincrement=True)
    message_id: int = Column(Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    user_id: int = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rating: int = Column(Integer, nullable=False)
    comment: Optional[str] = Column(String(500), nullable=True)
    created_at: datetime = Column(DateTime(timezone=True), server_default=func.now())
    message: "Message" = relationship("Message", back_populates="feedback", lazy="joined")
