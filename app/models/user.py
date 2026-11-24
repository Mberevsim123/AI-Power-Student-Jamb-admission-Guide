from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.chat import Conversation  # noqa: F401


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class UserRole(str, Enum):
    student = "student"
    admin = "admin"


class AcademicStage(str, Enum):
    """Where the student is in the admissions journey."""

    pre_jamb = "pre_jamb"
    post_jamb = "post_jamb"
    post_utme = "post_utme"
    admitted = "admitted"
    direct_entry = "direct_entry"


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        UniqueConstraint("username", name="uq_users_username"),
        CheckConstraint("char_length(username) >= 3", name="ck_users_username_min"),
        Index("ix_users_email_active", "email", "is_active"),
        Index("ix_users_role", "role"),
        Index("ix_users_created_at", "created_at"),
    )
    id: int = Column(Integer, primary_key=True, autoincrement=True)
    email: str = Column(String(255), nullable=False)
    username: str = Column(String(50), nullable=False)
    hashed_password: str = Column(String(255), nullable=False)
    role: str = Column(
        String(20),
        nullable=False,
        server_default=UserRole.student.value,
    )
    is_active: bool = Column(Boolean, nullable=False, default=True)
    is_verified: bool = Column(Boolean, nullable=False, default=False)
    data_consent_at = Column(DateTime(timezone=True), nullable=True)
    last_login_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    deleted_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    created_at: datetime = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: datetime = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    profile: "UserProfile" = relationship(
        "UserProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="joined",
    )
    conversations: List["Conversation"] = relationship(
        "Conversation", back_populates="user", cascade="all, delete-orphan", lazy="noload"
    )
    refresh_tokens: List["RefreshToken"] = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan", lazy="noload"
    )


# ---------------------------------------------------------------------------
# UserProfile  —  student-specific counselling context
# ---------------------------------------------------------------------------


class UserProfile(Base):
    __tablename__ = "user_profiles"
    __table_args__ = (
        CheckConstraint(
            "jamb_score IS NULL OR jamb_score BETWEEN 0 AND 400", name="ck_profile_jamb_score_range"
        ),
        CheckConstraint(
            "jamb_year  IS NULL OR jamb_year  BETWEEN 2000 AND 2100",
            name="ck_profile_jamb_year_range",
        ),
        Index("ix_user_profiles_stage", "academic_stage"),
        Index("ix_user_profiles_state", "state_of_origin"),
    )
    user_id: int = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    display_name: Optional[str] = Column(String(100), nullable=True)
    avatar_url: Optional[str] = Column(String(500), nullable=True)
    jamb_score: Optional[int] = Column(Integer, nullable=True)
    jamb_year: Optional[int] = Column(Integer, nullable=True)
    jamb_reg_number: Optional[str] = Column(String(20), nullable=True)

    academic_stage: str = Column(
        String(20),
        nullable=False,
        server_default=AcademicStage.pre_jamb.value,
    )
    state_of_origin: Optional[str] = Column(String(50), nullable=True)
    lga_of_origin: Optional[str] = Column(String(100), nullable=True)
    preferred_course: Optional[str] = Column(String(150), nullable=True)
    preferred_universities: List[str] = Column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    olevel_results: Dict[str, Any] = Column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    preferences: Dict[str, Any] = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    total_queries: int = Column(Integer, nullable=False, default=0)
    created_at: datetime = Column(DateTime(timezone=True), server_default=func.now())
    updated_at: datetime = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    user: "User" = relationship("User", back_populates="profile")


# ---------------------------------------------------------------------------
# RefreshToken
# ---------------------------------------------------------------------------


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_hash", "token_hash", unique=True),
        Index("ix_refresh_tokens_user_active", "user_id", "revoked", "expires_at"),
        CheckConstraint("expires_at > created_at", name="ck_refresh_token_expiry"),
    )
    id: int = Column(Integer, primary_key=True, autoincrement=True)

    user_id: int = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: str = Column(String(64), nullable=False)
    family_id: Optional[str] = Column(String(36), nullable=True)  # rotation family
    revoked: bool = Column(Boolean, nullable=False, default=False)
    revoked_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    user_agent: Optional[str] = Column(String(500), nullable=True)
    ip_address: Optional[str] = Column(String(45), nullable=True)
    expires_at: datetime = Column(DateTime(timezone=True), nullable=False)
    created_at: datetime = Column(DateTime(timezone=True), server_default=func.now())
    user: "User" = relationship("User", back_populates="refresh_tokens")
