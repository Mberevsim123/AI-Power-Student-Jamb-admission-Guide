"""Agent routes — /api/v1/agent

Proactive session briefing (the agent speaks first) and journey management.
"""

from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import journey
from app.agent.core import AdmissionsAgent
from app.agent.memory import Beliefs
from app.core.dependencies import get_consented_user
from app.core.rate_limit import search_limiter
from app.db.session import get_db
from app.models.user import User, UserProfile
from app.repositories.repository import ChatRepository
from app.services.admission_service import AdmissionService

router = APIRouter(prefix="/agent", tags=["agent"])

PROFILE_CHECKS = [
    ("display_name", "your name", "Call me…"),
    ("preferred_course", "the course you want", "I want to study…"),
    ("jamb_score", "your UTME score", "I scored…"),
    ("preferred_universities", "your target institutions", "I'm applying to…"),
    ("olevel_results", "your O'Level grades", "My O'Level grades are…"),
    ("state_of_origin", "your state of origin", "I'm from … State"),
]


class JourneyUpdate(BaseModel):
    done: bool


def _profile_gaps(profile):
    missing = [
        {"field": f, "label": label, "starter": starter}
        for f, label, starter in PROFILE_CHECKS
        if not getattr(profile, f, None)
    ]
    return {
        "completeness": round(100 * (len(PROFILE_CHECKS) - len(missing)) / len(PROFILE_CHECKS)),
        "missing": missing,
    }


def _suggestions(profile, memory, nxt):
    course = getattr(profile, "preferred_course", None) or memory.get("course")
    score = getattr(profile, "jamb_score", None)
    unis = getattr(profile, "preferred_universities", None) or memory.get("universities") or []
    prompts = []
    if course and score is not None:
        prompts.append(f"Am I eligible for {course} with {score}?")
    elif course:
        prompts.append(f"What do I need for {course}?")
    if course and unis:
        prompts.append(f"What's the {course} cut-off at {unis[0]}?")
    if score is not None:
        prompts.append(f"Which schools can I get into with {score}?")
    if nxt:
        prompts.append(nxt["prompt"])
    prompts += [
        "How do I register for JAMB?",
        "What subjects do I need for my course?",
        "How does Post-UTME screening work?",
    ]
    return list(dict.fromkeys(prompts))[:4]


@router.get("/briefing", summary="Proactive, personalised session briefing")
async def briefing(
    current_user: User = Depends(get_consented_user),
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(search_limiter),
):
    profile = current_user.profile
    last = await ChatRepository(db).latest_student_context(current_user.id)
    beliefs = Beliefs(last, profile)
    agent = AdmissionsAgent(db, AdmissionService(db))
    nudges = await agent.deadline_nudges(beliefs)
    overview = journey.overview(profile, beliefs)
    recall = {
        "course": beliefs.focus.get("course") or beliefs.get("course"),
        "universities": beliefs.focus.get("universities") or beliefs.get("universities") or [],
    }
    name = AdmissionsAgent._name(current_user)
    hour = datetime.now(ZoneInfo("Africa/Lagos")).hour
    part = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
    return {
        "greeting": f"Good {part}{', ' + name if name else ''}",
        "recall": recall if recall["course"] else None,
        "known": beliefs.known(),
        "nudges": [asdict(n) for n in nudges[:3]],
        "journey": overview,
        "profile": _profile_gaps(profile),
        "suggestions": _suggestions(profile, recall, overview["next"]),
    }


@router.patch("/journey/{step_id}", summary="Mark a journey step as done or not done")
async def update_journey(
    step_id: str,
    body: JourneyUpdate,
    current_user: User = Depends(get_consented_user),
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(search_limiter),
):
    if step_id not in journey.STEP_BY_ID:
        raise HTTPException(404, "Unknown journey step")
    profile = current_user.profile
    if profile is None:
        profile = UserProfile(user_id=current_user.id)
        db.add(profile)
    journey.mark(profile, [step_id], done=body.done, by="student")
    await db.commit()
    return journey.overview(profile)
