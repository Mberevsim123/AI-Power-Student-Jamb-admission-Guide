"""Actuators: deterministic tools the agent invokes against verified records.

Tools wrap the existing admissions services, so their provenance rules apply:
only official, sourced, year-matched records are returned.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.gemini_client import safe_web_url
from app.models.admission import Course, University
from app.schemas.schema import EligibilityRequest

# Search hints for negotiation: which programmes students commonly weigh together.
# They never assert that a programme exists; only catalogued records are offered.
COURSE_FAMILIES = [
    {"Medicine", "Dentistry", "Nursing", "Pharmacy", "Medical Laboratory Science", "Physiotherapy",
     "Anatomy", "Physiology", "Radiography", "Optometry", "Public Health", "Biochemistry",
     "Microbiology"},
    {"Computer Science", "Software Engineering", "Information Technology", "Cyber Security",
     "Computer Engineering", "Mathematics", "Statistics"},
    {"Electrical Engineering", "Mechanical Engineering", "Civil Engineering", "Chemical Engineering",
     "Petroleum Engineering", "Computer Engineering", "Architecture"},
    {"Law", "Political Science", "International Relations", "Mass Communication", "Sociology",
     "Psychology"},
    {"Accounting", "Banking and Finance", "Business Administration", "Economics", "Statistics"},
]  # fmt: skip


def related_courses(course):
    key = (course or "").casefold()
    related = set()
    for family in COURSE_FAMILIES:
        if any(member.casefold() in key or key in member.casefold() for member in family):
            related |= family
    return {c for c in related if c.casefold() not in key}


def band(margin):
    if margin >= 10:
        return "comfortable"
    if margin >= 0:
        return "borderline"
    if margin >= -10:
        return "just_short"
    return "out_of_reach"


class AdmissionsTools:
    def __init__(self, db, adm_service):
        self.db, self.adm = db, adm_service
        self.available = db is not None and adm_service is not None

    async def catalog(self):
        if self.db is None:
            return [], []
        courses = (
            await self.db.scalars(select(Course.name).where(Course.is_active.is_(True)).distinct())
        ).all()
        rows = (
            await self.db.execute(
                select(University.name, University.abbreviation).where(
                    University.is_active.is_(True)
                )
            )
        ).all()
        return list(courses), [{"name": n, "abbreviation": a} for n, a in rows]

    async def _official(self, **filters):
        rows = await self.adm.get_cutoffs(**filters)
        return [r for r in rows if r.get("is_official") and safe_web_url(r.get("source_url"))]

    async def cutoffs_for_course(self, course, year=None):
        return await self._official(course_name=course, year=year)

    async def cutoffs_at(self, university, year=None):
        return await self._official(university_name=university, year=year)

    async def eligibility_snapshot(self, course, score, year=None):
        """Compare a score with every official cut-off on file for the course."""
        rows = []
        for row in await self.cutoffs_for_course(course, year):
            margin = score - row["score"]
            rows.append({**row, "margin": margin, "band": band(margin)})
        rows.sort(key=lambda r: r["margin"], reverse=True)
        return rows

    async def full_eligibility(self, course, score, subjects, olevel, year=None):
        request = EligibilityRequest(
            course_name=course,
            jamb_score=score,
            jamb_subjects=subjects[:4],
            olevel_results=olevel,
            year=year,
        )
        return await self.adm.check_eligibility(request)

    async def current_cycle(self):
        return await self.adm.get_current_cycle()

    async def post_utme(self, university):
        return [
            r for r in await self.adm.get_post_utme(university) if safe_web_url(r.get("source_url"))
        ]
