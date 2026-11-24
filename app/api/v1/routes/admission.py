"""
Admissions routes — /api/v1/admissions
Public read-only data endpoints + authenticated eligibility + profile update.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import (  # FIX: import shared factory
    get_consented_user,
    get_user_repo,
)
from app.core.rate_limit import search_limiter
from app.db.session import get_db
from app.models.user import User, UserProfile
from app.repositories.repository import AdmissionRepository, UserRepository
from app.schemas.schema import (
    AdmissionCycleResponse,
    CourseBase,
    EligibilityRequest,
    EligibilityResult,
    ProfileResponse,
    ProfileUpdateRequest,
    UniversityBase,
)
from app.services.admission_service import AdmissionService

router = APIRouter(prefix="/admissions", tags=["admissions"])


# ---------------------------------------------------------------------------
# Dependency factories
# ---------------------------------------------------------------------------


def get_admission_repo(db: AsyncSession = Depends(get_db)) -> AdmissionRepository:
    return AdmissionRepository(db)


def get_admission_service(db: AsyncSession = Depends(get_db)) -> AdmissionService:
    # FIX: wire collaborators explicitly — AdmissionService owns its repo
    return AdmissionService(db=db)


# get_user_repo is imported from dependencies.py — no local duplicate needed


# ---------------------------------------------------------------------------
# Current admission cycle
# ---------------------------------------------------------------------------


@router.get(
    "/cycle/current",
    response_model=AdmissionCycleResponse,
    summary="Get the active JAMB admission cycle",
)
async def current_cycle(
    svc: AdmissionService = Depends(get_admission_service),  # FIX: use service, not raw repo
    _rate: None = Depends(search_limiter),
):
    cycle = await svc.get_current_cycle()
    if not cycle:
        raise HTTPException(status_code=404, detail="No active admission cycle found")
    return cycle


# ---------------------------------------------------------------------------
# University search / list
# ---------------------------------------------------------------------------


@router.get(
    "/universities",
    response_model=list[UniversityBase],
    summary="Search or list Nigerian universities",
)
async def search_universities(
    name: Optional[str] = Query(None, description="Partial name match"),
    state: Optional[str] = Query(None, description="State filter"),
    university_type: Optional[str] = Query(None, description="federal | state | private"),
    limit: int = Query(20, ge=1, le=100),  # FIX: added ge=1
    offset: int = Query(0, ge=0),  # FIX: added ge=0
    repo: AdmissionRepository = Depends(get_admission_repo),
    _rate: None = Depends(search_limiter),
):
    return await repo.list_universities(
        name=name, state=state, university_type=university_type, limit=limit, offset=offset
    )


@router.get(
    "/universities/{university_id}/courses",
    response_model=list[CourseBase],
    summary="List all courses offered by a university",
)
async def university_courses(
    university_id: int,
    repo: AdmissionRepository = Depends(get_admission_repo),
    _rate: None = Depends(search_limiter),
):
    # FIX: get_university_by_id already selectinloads courses — reuse them,
    # no second query needed
    university = await repo.get_university_by_id(university_id)
    if not university:
        raise HTTPException(status_code=404, detail="University not found")

    courses = [course for course in university.courses if course.is_active]
    if not courses:
        raise HTTPException(
            status_code=404,
            detail=f"University {university_id} exists but has no courses listed",
        )
    return courses


# ---------------------------------------------------------------------------
# Cut-off marks
# ---------------------------------------------------------------------------


@router.get(
    "/cutoffs",
    summary="Look up cut-off marks by university, course, or year",
)
async def get_cutoffs(
    university: Optional[str] = Query(None, description="University name (partial match)"),
    course: Optional[str] = Query(None, description="Course name (partial match)"),
    year: Optional[int] = Query(None, description="JAMB year — defaults to current cycle"),
    svc: AdmissionService = Depends(get_admission_service),
    _rate: None = Depends(search_limiter),
):
    results = await svc.get_cutoffs(
        university_name=university,
        course_name=course,
        year=year,
    )
    if not results:
        raise HTTPException(
            status_code=404,
            detail="No cut-off data found for the given filters",
        )
    return results


# ---------------------------------------------------------------------------
# Subject combinations
# ---------------------------------------------------------------------------


@router.get(
    "/subjects",
    summary="Get JAMB subject combinations for a course",
)
async def get_subject_combination(
    course: str = Query(..., description="Course name"),
    university: Optional[str] = Query(None, description="Narrow by university name"),
    svc: AdmissionService = Depends(get_admission_service),
    _rate: None = Depends(search_limiter),
):
    results = await svc.get_subject_combination(course, university)
    if not results:
        raise HTTPException(
            status_code=404,
            detail=f"No subject combination data found for '{course}'",
        )
    return results


# ---------------------------------------------------------------------------
# Eligibility check (authenticated)
# ---------------------------------------------------------------------------


@router.post(
    "/eligibility",
    response_model=EligibilityResult,
    summary="Check if a student's score and O-Level qualify them for a course",
)
async def check_eligibility(
    body: EligibilityRequest,
    current_user: User = Depends(get_consented_user),
    svc: AdmissionService = Depends(get_admission_service),
    _rate: None = Depends(search_limiter),
):
    return await svc.check_eligibility(body)


# ---------------------------------------------------------------------------
# Student profile update
# ---------------------------------------------------------------------------


@router.patch(
    "/profile",
    response_model=ProfileResponse,
    summary="Update the student's JAMB context and preferences",
)
async def update_profile(
    body: ProfileUpdateRequest,
    current_user: User = Depends(get_consented_user),
    repo: UserRepository = Depends(get_user_repo),  # FIX: imported, not re-declared
    _rate: None = Depends(search_limiter),
):
    profile = current_user.profile

    if not profile:
        profile = UserProfile(user_id=current_user.id)

    # FIX: renamed `field` → `attr` to avoid shadowing the built-in
    update_data = body.model_dump(exclude_unset=True)
    for attr, value in update_data.items():
        setattr(profile, attr, value)

    # FIX: repo owns db.add(), flush, commit, and refresh — route has no raw db
    # FIX: removed redundant db.refresh() — upsert_profile returns the live object
    updated_profile = await repo.upsert_profile(profile)
    return updated_profile


@router.get(
    "/profile",
    response_model=ProfileResponse,
    summary="Get the current student's JAMB profile",
)
async def get_profile(
    current_user: User = Depends(get_consented_user),
):
    profile = current_user.profile
    if not profile:
        raise HTTPException(status_code=404, detail="Student profile not found")
    return profile


@router.get("/post-utme", summary="Published Post-UTME schedules and eligibility")
async def post_utme(
    university: Optional[str] = Query(None, min_length=1),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    svc: AdmissionService = Depends(get_admission_service),
    _rate: None = Depends(search_limiter),
):
    return await svc.get_post_utme(university, year)
