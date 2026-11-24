"""Conservative, institution-specific admissions evaluation."""

from app.repositories.repository import AdmissionRepository
from app.schemas.schema import EligibilityRequest, EligibilityResult

GRADE_RANK = {"A1": 1, "B2": 2, "B3": 3, "C4": 4, "C5": 5, "C6": 6, "D7": 7, "E8": 8, "F9": 9}


def normalize_subject(value):
    value = " ".join(value.casefold().replace("-", " ").split())
    return {
        "maths": "mathematics",
        "math": "mathematics",
        "english language": "english",
        "use of english": "english",
    }.get(value, value)


class AdmissionService:
    def __init__(self, db):
        self.repo = AdmissionRepository(db)

    async def get_current_cycle(self):
        return await self.repo.get_current_cycle()

    async def get_courses_by_name(self, name):
        return await self.repo.get_courses_by_name(name)

    async def get_eligible_universities(self, course_name, jamb_score, cycle_id):
        return await self.repo.get_universities_by_score(course_name, jamb_score, cycle_id)

    async def check_eligibility(self, req: EligibilityRequest) -> EligibilityResult:
        cycle = (
            await self.repo.get_cycle_by_year(req.year)
            if req.year
            else await self.repo.get_current_cycle()
        )
        result = EligibilityResult(
            course_name=req.course_name,
            jamb_score=req.jamb_score,
            is_eligible=False,
            missing_subjects=[],
            failed_olevel=[],
            eligible_universities=[],
            borderline_universities=[],
            status="insufficient_data",
            assessments=[],
        )
        if not cycle:
            result.reason = "No admission cycle data is available for the requested year."
            return result
        cutoffs = await self.repo.get_cutoffs(cycle.id)
        matching = [
            c
            for c in cutoffs
            if c.course
            and c.course.is_active
            and c.university.is_active
            and c.course.name.casefold() == req.course_name.strip().casefold()
            and (not req.university_id or c.university_id == req.university_id)
        ]
        if not matching:
            result.reason = "No course-specific cut-offs are available for this course and cycle."
            return result
        subjects = {normalize_subject(s) for s in req.jamb_subjects}
        grades = {normalize_subject(s): g.upper() for s, g in req.olevel_results.items()}
        for cutoff in matching:
            course = cutoff.course
            missing = [s for s in course.jamb_subjects if normalize_subject(s) not in subjects]
            failed = []
            for subject, grade in course.olevel_requirements.items():
                actual = grades.get(normalize_subject(subject))
                if actual is None or GRADE_RANK.get(actual, 99) > GRADE_RANK.get(
                    str(grade).upper(), 0
                ):
                    failed.append(f"{subject}: need {grade}, got {actual or 'not provided'}")
            # Empty or unsupported requirements cannot prove eligibility.
            complete = bool(
                course.jamb_subjects
                and len(course.olevel_requirements) >= 5
                and req.jamb_subjects
                and req.olevel_results
                and cutoff.is_official
                and cutoff.source_url
                and course.source_url
                and course.requirements_year == cycle.year
            )
            entry = dict(
                university=cutoff.university.name,
                university_id=cutoff.university_id,
                course=course.name,
                cutoff=cutoff.score,
                margin=req.jamb_score - cutoff.score,
                year=cycle.year,
                source_url=cutoff.source_url,
                is_official=cutoff.is_official,
                missing_subjects=missing,
                failed_olevel=failed,
            )
            entry["status"] = (
                "meets_requirements"
                if complete and not missing and not failed and req.jamb_score >= cutoff.score
                else "does_not_meet_requirements"
                if complete
                else "insufficient_data"
            )
            result.assessments.append(entry)
            if entry["status"] == "meets_requirements":
                result.eligible_universities.append(entry)
                if entry["margin"] <= 10:
                    result.borderline_universities.append(entry)
        result.is_eligible = bool(result.eligible_universities)
        result.status = (
            "meets_requirements"
            if result.is_eligible
            else "insufficient_data"
            if any(a["status"] == "insufficient_data" for a in result.assessments)
            else "does_not_meet_requirements"
        )
        result.missing_subjects = sorted(
            {s for a in result.assessments for s in a["missing_subjects"]}
        )
        result.failed_olevel = sorted({s for a in result.assessments for s in a["failed_olevel"]})
        result.reason = "Meeting published requirements does not guarantee admission. Confirm sitting limits, screening and any programme-specific conditions with the institution."
        return result

    async def _university_id(self, name):
        matches = await self.repo.search_universities(name)
        return matches[0].id if matches else None

    async def get_cutoffs(self, university_name=None, course_name=None, year=None):
        cycle = (
            await self.repo.get_cycle_by_year(year) if year else await self.repo.get_current_cycle()
        )
        if not cycle:
            return []
        uid = await self._university_id(university_name) if university_name else None
        if university_name and uid is None:
            return []
        cutoffs = await self.repo.get_cutoffs(cycle.id, university_id=uid)
        return [
            dict(
                university=c.university.name,
                abbreviation=c.university.abbreviation,
                state=c.university.state,
                university_type=c.university.university_type,
                course=c.course.name if c.course else "General",
                score=c.score,
                year=cycle.year,
                is_official=c.is_official,
                source_url=c.source_url,
                notes=c.notes,
            )
            for c in cutoffs
            if c.university.is_active
            and (c.course is None or c.course.is_active)
            and (not course_name or c.course and course_name.casefold() in c.course.name.casefold())
        ]

    async def get_subject_combination(self, course_name, university_name=None):
        uid = await self._university_id(university_name) if university_name else None
        if university_name and uid is None:
            return []
        courses = await self.repo.get_courses_by_name(course_name, university_id=uid)
        return [
            dict(
                course=c.name,
                university=c.university.name,
                faculty=c.faculty,
                jamb_subjects=c.jamb_subjects,
                olevel_requirements=c.olevel_requirements,
                minimum_score=c.minimum_score,
                notes=c.course_notes,
                source_url=c.source_url,
                requirements_year=c.requirements_year,
            )
            for c in courses
            if c.university.is_active
        ][:20]

    async def get_post_utme(self, university_name=None, year=None):
        from sqlalchemy import select

        from app.models.admission import PostUTMESchedule

        cycle = (
            await self.repo.get_cycle_by_year(year) if year else await self.repo.get_current_cycle()
        )
        if not cycle:
            return []
        uid = await self._university_id(university_name) if university_name else None
        if university_name and uid is None:
            return []
        stmt = select(PostUTMESchedule).where(PostUTMESchedule.cycle_id == cycle.id)
        if uid:
            stmt = stmt.where(PostUTMESchedule.university_id == uid)
        rows = (await self.repo.db.scalars(stmt)).all()
        return [
            dict(
                id=s.id,
                university=s.university.name,
                year=cycle.year,
                start_date=s.start_date,
                end_date=s.end_date,
                fee_ngn=str(s.fee_ngn),
                eligibility=s.eligibility,
                source_url=s.source_url,
            )
            for s in rows
            if s.university.is_active
        ]
