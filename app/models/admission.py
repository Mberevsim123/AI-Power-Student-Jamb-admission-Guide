"""
Admission domain models
-----------------------
University  Course (a university offers many courses)
AdmissionCycle CutoffMark (each cycle publishes cutoffs per course/university)
"""

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class UniversityType(str, Enum):
    federal = "federal"
    state = "state"
    private = "private"


class ProgrammeType(str, Enum):
    utme = "utme"
    direct_entry = "direct_entry"
    both = "both"


# ---------------------------------------------------------------------------
# University
# ---------------------------------------------------------------------------


class University(Base):
    __tablename__ = "universities"
    __table_args__ = (
        UniqueConstraint("name", name="uq_university_name"),
        UniqueConstraint("abbreviation", name="uq_university_abbreviation"),
        Index("ix_universities_state", "state"),
        Index("ix_universities_type", "university_type"),
        Index("ix_universities_active", "is_active"),
    )

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    name: str = Column(String(200), nullable=False)
    abbreviation: Optional[str] = Column(String(20), nullable=True)
    state: str = Column(String(50), nullable=False)
    university_type: str = Column(String(20), nullable=False)
    general_cutoff: Optional[int] = Column(Integer, nullable=True)
    website_url: Optional[str] = Column(String(300), nullable=True)
    admissions_meta: Dict[str, Any] = Column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_active: bool = Column(Boolean, nullable=False, default=True)
    created_at: datetime = Column(DateTime(timezone=True), server_default=func.now())
    updated_at: datetime = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    courses: List["Course"] = relationship(
        "Course", back_populates="university", cascade="all, delete-orphan", lazy="noload"
    )
    cutoffs: List["CutoffMark"] = relationship(
        "CutoffMark", back_populates="university", lazy="noload"
    )


# ---------------------------------------------------------------------------
# Course
# ---------------------------------------------------------------------------


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (
        UniqueConstraint(
            "university_id", "name", "programme_type", name="uq_course_university_programme"
        ),
        Index("ix_courses_university", "university_id"),
        Index("ix_courses_faculty", "faculty"),
        Index("ix_courses_name", "name"),
        Index("ix_courses_programme_type", "programme_type"),
        CheckConstraint("duration_years BETWEEN 1 AND 7", name="ck_course_duration"),
    )

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    university_id: int = Column(
        Integer,
        ForeignKey("universities.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: str = Column(String(150), nullable=False)
    faculty: str = Column(String(100), nullable=False)
    department: Optional[str] = Column(String(150), nullable=True)
    programme_type: str = Column(
        String(20), nullable=False, server_default=ProgrammeType.utme.value
    )
    duration_years: int = Column(Integer, nullable=False, default=4)
    jamb_subjects: List[str] = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    olevel_requirements: Dict[str, Any] = Column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    minimum_score: Optional[int] = Column(Integer, nullable=True)
    source_url = Column(String(500), nullable=True)
    requirements_year = Column(Integer, nullable=True)
    course_notes: Optional[str] = Column(String(2000), nullable=True)
    is_active: bool = Column(Boolean, nullable=False, default=True)
    created_at: datetime = Column(DateTime(timezone=True), server_default=func.now())
    updated_at: datetime = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    university: "University" = relationship("University", back_populates="courses", lazy="joined")
    cutoffs: List["CutoffMark"] = relationship("CutoffMark", back_populates="course", lazy="noload")


# ---------------------------------------------------------------------------
# AdmissionCycle  —  each JAMB year has its own cycle
# ---------------------------------------------------------------------------


class AdmissionCycle(Base):
    __tablename__ = "admission_cycles"
    __table_args__ = (
        UniqueConstraint("year", name="uq_cycle_year"),
        Index(
            "ix_cycles_current",
            "is_current",
            unique=True,
            postgresql_where=text("is_current = true"),
        ),
    )
    id: int = Column(Integer, primary_key=True, autoincrement=True)
    year: int = Column(Integer, nullable=False)  # e.g. 2025
    registration_start: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    registration_end: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    exam_start: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    exam_end: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    results_date: Optional[date] = Column(Date, nullable=True)

    results_released: bool = Column(Boolean, nullable=False, default=False)
    supplementary_open: bool = Column(Boolean, nullable=False, default=False)
    is_current: bool = Column(Boolean, nullable=False, default=False)
    cycle_notes: Dict[str, Any] = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: datetime = Column(DateTime(timezone=True), server_default=func.now())
    updated_at: datetime = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    cutoffs: List["CutoffMark"] = relationship("CutoffMark", back_populates="cycle", lazy="noload")


# ---------------------------------------------------------------------------
# CutoffMark  —  official cut-off score per university/course/cycle
# ---------------------------------------------------------------------------


class CutoffMark(Base):
    __tablename__ = "cutoff_marks"
    __table_args__ = (
        UniqueConstraint(
            "cycle_id", "university_id", "course_id", name="uq_cutoff_cycle_university_course"
        ),
        Index("ix_cutoff_cycle_university", "cycle_id", "university_id"),
        Index("ix_cutoff_score", "score"),
        CheckConstraint("score BETWEEN 0 AND 400", name="ck_cutoff_score_range"),
    )
    id: int = Column(Integer, primary_key=True, autoincrement=True)
    cycle_id: int = Column(
        Integer,
        ForeignKey("admission_cycles.id", ondelete="CASCADE"),
        nullable=False,
    )
    university_id: int = Column(
        Integer, ForeignKey("universities.id", ondelete="CASCADE"), nullable=False
    )
    course_id: Optional[int] = Column(
        Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=True
    )
    score: int = Column(Integer, nullable=False)
    post_utme_weight: Optional[float] = Column(Float, nullable=True)  # % weight of Post-UTME
    is_official: bool = Column(Boolean, nullable=False, default=True)  # False = estimated
    source_url: Optional[str] = Column(String(500), nullable=True)
    notes: Optional[str] = Column(String(1000), nullable=True)
    created_at: datetime = Column(DateTime(timezone=True), server_default=func.now())
    updated_at: datetime = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    cycle: "AdmissionCycle" = relationship(
        "AdmissionCycle", back_populates="cutoffs", lazy="joined"
    )
    university: "University" = relationship("University", back_populates="cutoffs", lazy="joined")
    course: Optional["Course"] = relationship("Course", back_populates="cutoffs", lazy="joined")


class PostUTMESchedule(Base):
    __tablename__ = "post_utme_schedules"
    __table_args__ = (
        UniqueConstraint("university_id", "cycle_id", name="uq_post_utme_university_cycle"),
        CheckConstraint("end_date >= start_date", name="ck_post_utme_dates"),
        CheckConstraint("fee_ngn >= 0", name="ck_post_utme_fee"),
    )
    id = Column(Integer, primary_key=True)
    university_id = Column(
        Integer, ForeignKey("universities.id", ondelete="CASCADE"), nullable=False
    )
    cycle_id = Column(
        Integer, ForeignKey("admission_cycles.id", ondelete="CASCADE"), nullable=False
    )
    start_date = Column(DateTime(timezone=True), nullable=False)
    end_date = Column(DateTime(timezone=True), nullable=False)
    fee_ngn = Column(Numeric(12, 2), nullable=False)
    eligibility = Column(String(3000), nullable=False)
    source_url = Column(String(500), nullable=False)
    university = relationship("University", lazy="joined")
