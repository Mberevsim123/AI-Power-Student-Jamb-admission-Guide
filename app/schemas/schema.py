"""
Pydantic schemas — request/response DTOs
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# ===========================================================================
# Auth
# ===========================================================================


class RegisterRequest(BaseModel):
    data_consent: Literal[True] = Field(
        description="Consent to account, profile and conversation storage and sending chat context to the configured AI provider."
    )
    email: EmailStr
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=72)

    @field_validator("password")
    @classmethod
    def password_bytes(cls, value):
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 UTF-8 bytes")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


# ===========================================================================
# User / Profile
# ===========================================================================


class ProfileUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=100)
    jamb_score: Optional[int] = Field(default=None, ge=0, le=400)
    jamb_year: Optional[int] = Field(default=None, ge=2000, le=2100)
    jamb_reg_number: Optional[str] = Field(default=None, max_length=20)
    academic_stage: Optional[
        Literal["pre_jamb", "post_jamb", "post_utme", "admitted", "direct_entry"]
    ] = None
    state_of_origin: Optional[str] = Field(default=None, max_length=50)
    lga_of_origin: Optional[str] = Field(default=None, max_length=100)
    preferred_course: Optional[str] = Field(default=None, max_length=150)
    preferred_universities: Optional[List[str]] = None
    olevel_results: Optional[Dict[str, str]] = None  # {"Maths": "A1"}

    @field_validator("olevel_results")
    @classmethod
    def valid_grades(cls, value):
        if value is None:
            return {}
        from app.services.admission_service import GRADE_RANK, normalize_subject

        normalized = {}
        for subject, grade in value.items():
            key = normalize_subject(subject)
            if not key or grade.strip().upper() not in GRADE_RANK:
                raise ValueError("Use a subject name and a valid grade from A1 to F9")
            if key in normalized:
                raise ValueError("Duplicate subject aliases are not allowed")
            normalized[key] = grade.strip().upper()
        return normalized

    @field_validator("academic_stage")
    @classmethod
    def nonnull_stage(cls, value):
        if value is None:
            raise ValueError("academic_stage cannot be null")
        return value

    @field_validator("preferred_universities")
    @classmethod
    def clear_universities(cls, value):
        return value or []


class ProfileResponse(BaseModel):
    user_id: int
    display_name: Optional[str]
    jamb_score: Optional[int]
    jamb_year: Optional[int]
    jamb_reg_number: Optional[str]
    academic_stage: str
    state_of_origin: Optional[str]
    lga_of_origin: Optional[str]
    preferred_course: Optional[str]
    preferred_universities: List[str]
    olevel_results: Dict[str, Any]
    total_queries: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserResponse(BaseModel):
    data_consent_at: Optional[datetime] = None
    id: int
    email: str
    username: str
    role: str
    is_active: bool
    is_verified: bool
    created_at: datetime
    profile: Optional[ProfileResponse] = None

    model_config = ConfigDict(from_attributes=True)


# ===========================================================================
# Chat
# ===========================================================================


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)

    @field_validator("message")
    @classmethod
    def nonblank_message(cls, value):
        if not value.strip():
            raise ValueError("message must contain text")
        return value.strip()

    conversation_id: Optional[int] = Field(default=None, ge=1)  # omit to start a new conversation
    stream: bool = False


class MessageResponse(BaseModel):
    id: int
    conversation_id: int
    role: str
    state: str
    content: Optional[str]
    error_message: Optional[str] = None
    intent: Optional[str]
    rag_sources: List[Dict[str, Any]]
    structured_context: Dict[str, Any]
    model_used: Optional[str]
    token_count: Optional[int]
    latency_ms: Optional[int]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationResponse(BaseModel):
    id: int
    title: str
    status: str
    student_context: Dict[str, Any]
    message_count: int
    last_message_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationDetailResponse(ConversationResponse):
    messages: List[MessageResponse] = []


class FeedbackRequest(BaseModel):
    rating: int = Field(ge=-1, le=1)
    comment: Optional[str] = Field(default=None, max_length=500)

    @field_validator("rating")
    @classmethod
    def rating_must_be_nonzero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("rating must be 1 or -1")
        return v


# ===========================================================================
# Admissions — read schemas (data is admin-loaded, students only query)
# ===========================================================================


class UniversityBase(BaseModel):
    id: int
    name: str
    abbreviation: Optional[str]
    state: str
    university_type: str
    general_cutoff: Optional[int]
    website_url: Optional[str]

    model_config = ConfigDict(from_attributes=True)


class CourseBase(BaseModel):
    source_url: Optional[str] = None
    requirements_year: Optional[int] = None
    id: int
    name: str
    faculty: str
    department: Optional[str]
    programme_type: str
    duration_years: int
    jamb_subjects: List[str]
    olevel_requirements: Dict[str, Any]
    minimum_score: Optional[int]
    course_notes: Optional[str]

    model_config = ConfigDict(from_attributes=True)


class CutoffMarkResponse(BaseModel):
    id: int
    university: UniversityBase
    course: Optional[CourseBase]
    score: int
    post_utme_weight: Optional[float]
    is_official: bool
    notes: Optional[str]
    cycle_year: int  # resolved from cycle

    model_config = ConfigDict(from_attributes=True)


class EligibilityRequest(BaseModel):
    jamb_score: int = Field(ge=0, le=400)
    course_name: str = Field(min_length=2, max_length=150)
    jamb_subjects: List[str] = Field(default_factory=list, max_length=4)
    university_id: Optional[int] = Field(default=None, ge=1)
    year: Optional[int] = Field(default=None, ge=2000, le=2100)
    olevel_results: Dict[str, str] = {}  # {"Mathematics": "A1"}
    state_of_origin: Optional[str] = None

    @field_validator("olevel_results")
    @classmethod
    def valid_grades(cls, value):
        if value is None:
            return value
        from app.services.admission_service import GRADE_RANK, normalize_subject

        normalized = {}
        for subject, grade in value.items():
            key = normalize_subject(subject)
            if not key or grade.strip().upper() not in GRADE_RANK:
                raise ValueError("Use a subject name and a valid grade from A1 to F9")
            if key in normalized:
                raise ValueError("Duplicate subject aliases are not allowed")
            normalized[key] = grade.strip().upper()
        return normalized


class EligibilityResult(BaseModel):
    status: Literal["meets_requirements", "does_not_meet_requirements", "insufficient_data"] = (
        "insufficient_data"
    )
    reason: Optional[str] = None
    assessments: List[Dict[str, Any]] = Field(default_factory=list)
    course_name: str
    is_eligible: bool
    jamb_score: int
    missing_subjects: List[str]  # JAMB subjects the student didn't indicate
    failed_olevel: List[str]  # O-Level credits not met
    eligible_universities: List[Dict[str, Any]]  # universities where score qualifies
    borderline_universities: List[Dict[str, Any]]  # within 10 marks of cutoff
    general_checks: List[Dict[str, Any]] = Field(default_factory=list)  # national rules
    next_steps: List[str] = Field(default_factory=list)
    official_links: List[Dict[str, str]] = Field(default_factory=list)


class AdmissionCycleResponse(BaseModel):
    id: int
    year: int
    results_released: bool
    supplementary_open: bool
    is_current: bool
    registration_start: Optional[datetime]
    registration_end: Optional[datetime]
    exam_start: Optional[datetime]
    exam_end: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


# ===========================================================================
# Pagination helper
# ===========================================================================


class PaginatedResponse(BaseModel):
    total: int
    page: int
    size: int
    pages: int
    items: List[Any]


# Reusable validator attached to request models below via a shared base is avoided
# to preserve the public schema names. Each request validates its own grades.
