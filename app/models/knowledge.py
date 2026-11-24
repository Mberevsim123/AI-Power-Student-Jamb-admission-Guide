"""
Knowledge-base models for RAG
------------------------------
KnowledgeDocument ──< DocumentChunk
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class DocumentCategory(str, Enum):
    jamb_brochure = "jamb_brochure"
    jamb_syllabus = "jamb_syllabus"
    admission_guideline = "admission_guideline"
    post_utme_guide = "post_utme_guide"
    scholarship_info = "scholarship_info"
    general_faq = "general_faq"
    news_update = "news_update"


class DocumentStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    indexed = "indexed"
    failed = "failed"


# ---------------------------------------------------------------------------
# KnowledgeDocument
# ---------------------------------------------------------------------------


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        Index("ix_docs_category", "category"),
        Index("ix_docs_status", "status"),
        Index("ix_docs_active", "is_active"),
        Index("ix_docs_cycle_year", "cycle_year"),
        Index("ix_docs_created", "created_at"),
    )

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    title: str = Column(String(300), nullable=False)
    category: str = Column(String(50), nullable=False)
    status: str = Column(String(20), nullable=False, server_default=DocumentStatus.pending.value)
    cycle_year: Optional[int] = Column(Integer, nullable=True)
    source_url: Optional[str] = Column(String(500), nullable=True)
    storage_path: Optional[str] = Column(String(500), nullable=True)
    file_type: Optional[str] = Column(String(20), nullable=True)
    chunk_count: int = Column(Integer, nullable=False, default=0)
    embedding_model: Optional[str] = Column(String(100), nullable=True)
    doc_meta: Dict[str, Any] = Column(JSONB, nullable=False, server_default=sa_text("'{}'::jsonb"))
    is_active: bool = Column(Boolean, nullable=False, default=True)
    created_at: datetime = Column(DateTime(timezone=True), server_default=func.now())
    updated_at: datetime = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    indexed_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    chunks: List["DocumentChunk"] = relationship(
        "DocumentChunk", back_populates="document", cascade="all, delete-orphan", lazy="noload"
    )


# ---------------------------------------------------------------------------
# DocumentChunk  —  one paragraph-sized piece ready for vector search
# ---------------------------------------------------------------------------


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        Index("ix_chunks_document_index", "document_id", "chunk_index"),
        Index("ix_chunks_hash", "document_id", "content_hash", unique=True),
        Index("ix_chunks_category", "category"),
        Index("ix_chunks_created", "created_at"),
    )
    id: int = Column(Integer, primary_key=True, autoincrement=True)
    document_id: int = Column(
        Integer, ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: int = Column(Integer, nullable=False)
    text: str = Column(String(10000), nullable=False)
    category: str = Column(String(50), nullable=False)
    cycle_year: Optional[int] = Column(Integer, nullable=True)

    content_hash: Optional[str] = Column(String(64), nullable=True)
    embedding_model: Optional[str] = Column(String(100), nullable=True)
    vector_id: Optional[str] = Column(String(200), nullable=True)
    token_count: Optional[int] = Column(Integer, nullable=True)
    chunk_meta: Dict[str, Any] = Column(
        JSONB, nullable=False, server_default=sa_text("'{}'::jsonb")
    )
    created_at: datetime = Column(DateTime(timezone=True), server_default=func.now())
    updated_at: datetime = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    document: "KnowledgeDocument" = relationship(
        "KnowledgeDocument", back_populates="chunks", lazy="joined"
    )
