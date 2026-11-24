"""Conservative, institution-specific admissions evaluation."""

import re

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


IBASS = "https://eligibility.jamb.gov.ng"
OFFICIAL_LINKS = [
    {"label": "JAMB IBASS eligibility checker", "url": IBASS},
    {"label": "JAMB e-Facility", "url": "https://efacility.jamb.gov.ng"},
]
CREDIT = 6  # A1–C6 are credit passes; D7/E8 are passes; F9 is a fail.


def _words(value):
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def course_matches(requested, published):
    """'Medicine' matches 'Medicine and Surgery'; 'Nursing' matches 'Nursing Science'."""
    from app.agent.perception import COURSE_ALIASES

    a, b = _words(requested), _words(published)
    if not a or not b:
        return False
    ca, cb = COURSE_ALIASES.get(a, a).casefold(), COURSE_ALIASES.get(b, b).casefold()
    return a == b or ca == cb or f" {a} " in f" {b} " or f" {b} " in f" {a} "


def general_checks(req):
    """National rules that hold for every UTME candidate and degree programme."""
    checks = []
    subjects = [normalize_subject(s) for s in req.jamb_subjects if s.strip()]
    if not subjects:
        checks.append(
            dict(
                id="utme_subjects",
                status="missing",
                label="UTME subjects",
                detail="Add the four subjects you sat for.",
            )
        )
    else:
        count_ok = len(set(subjects)) == 4
        checks.append(
            dict(
                id="utme_count",
                status="pass" if count_ok else "fail",
                label="Four UTME subjects",
                detail="You entered four subjects."
                if count_ok
                else f"UTME is taken in exactly four subjects; you entered {len(set(subjects))}.",
            )
        )
        english = "english" in subjects
        checks.append(
            dict(
                id="utme_english",
                status="pass" if english else "fail",
                label="Use of English in UTME",
                detail="Included."
                if english
                else "Use of English is compulsory for every candidate.",
            )
        )
    grades = {normalize_subject(s): g.upper() for s, g in req.olevel_results.items()}
    if not grades:
        checks.append(
            dict(
                id="olevel",
                status="missing",
                label="O'Level results",
                detail="Add your WAEC/NECO grades to check credit requirements.",
            )
        )
    else:
        credits = [s for s, g in grades.items() if GRADE_RANK.get(g, 99) <= CREDIT]
        five = len(credits) >= 5
        checks.append(
            dict(
                id="olevel_five",
                status="pass" if five else "fail",
                label="Five O'Level credits (A1–C6)",
                detail=f"You have {len(credits)} credit{'s' if len(credits) != 1 else ''}."
                + (
                    ""
                    if five
                    else " Degree programmes require at least five, in relevant subjects."
                ),
            )
        )
        eng = grades.get("english")
        eng_ok = eng is not None and GRADE_RANK.get(eng, 99) <= CREDIT
        checks.append(
            dict(
                id="olevel_english",
                status="pass" if eng_ok else "fail",
                label="Credit in English Language",
                detail=f"{eng}."
                if eng_ok
                else f"Required for every degree programme; you have {eng or 'no English result'}.",
            )
        )
        maths = grades.get("mathematics")
        maths_ok = maths is not None and GRADE_RANK.get(maths, 99) <= CREDIT
        checks.append(
            dict(
                id="olevel_maths",
                status="pass" if maths_ok else "warn",
                label="Credit in Mathematics",
                detail=f"{maths}."
                if maths_ok
                else "Required by most programmes (a few arts "
                "programmes accept a pass) — confirm your course on IBASS.",
            )
        )
    return checks


def next_steps(result, checks, has_records):
    steps = []
    failed = [c for c in checks if c["status"] == "fail"]
    if any(c["id"] in ("utme_count", "utme_english") for c in failed):
        steps.append(
            "Correct your UTME subject combination: four subjects including Use of English."
        )
    if any(c["id"].startswith("olevel") for c in failed):
        steps.append(
            "Plan an O'Level resit (WAEC/NECO/NABTEB) for the missing credits, or "
            "combine two sittings if your course accepts it."
        )
    if any(c["status"] == "missing" for c in checks):
        steps.append("Add your UTME subjects and O'Level grades for a complete check.")
    if result.missing_subjects:
        steps.append("Your UTME subjects do not match the course's required combination.")
    if not has_records:
        steps.append(
            "Confirm your course's exact subject and grade requirements on JAMB's IBASS "
            "checker for your chosen institution."
        )
    if result.is_eligible:
        steps.append(
            "Register for the institution's Post-UTME screening when it is announced, "
            "and make sure your O'Level results are uploaded to JAMB."
        )
    return steps


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
        """Two layers: national rules that always apply, then each institution's
        published, year-matched requirements. Unknown or unpublished data is reported
        as missing — never assumed to be met."""
        checks = general_checks(req)
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
            general_checks=checks,
            official_links=OFFICIAL_LINKS,
        )
        general_failed = any(c["status"] == "fail" for c in checks)
        cycle = (
            await self.repo.get_cycle_by_year(req.year)
            if req.year
            else await self.repo.get_current_cycle()
        )
        matching = []
        if cycle:
            cutoffs = await self.repo.get_cutoffs(cycle.id)
            matching = [
                c
                for c in cutoffs
                if c.course
                and c.course.is_active
                and c.university.is_active
                and course_matches(req.course_name, c.course.name)
                and (not req.university_id or c.university_id == req.university_id)
            ]
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
                if complete
                and not missing
                and not failed
                and not general_failed
                and req.jamb_score >= cutoff.score
                else "does_not_meet_requirements"
                if complete or general_failed
                else "insufficient_data"
            )
            result.assessments.append(entry)
            if entry["status"] == "meets_requirements":
                result.eligible_universities.append(entry)
                if entry["margin"] <= 10:
                    result.borderline_universities.append(entry)
        result.assessments.sort(key=lambda a: (-a["margin"], a["university"]))
        result.is_eligible = bool(result.eligible_universities)
        result.status = (
            "meets_requirements"
            if result.is_eligible
            else "does_not_meet_requirements"
            if general_failed
            or (
                result.assessments
                and all(a["status"] == "does_not_meet_requirements" for a in result.assessments)
            )
            else "insufficient_data"
        )
        result.missing_subjects = sorted(
            {s for a in result.assessments for s in a["missing_subjects"]}
        )
        result.failed_olevel = sorted({s for a in result.assessments for s in a["failed_olevel"]})
        if general_failed:
            result.reason = (
                "Your results do not meet the national entry rules below, so no institution "
                "can admit you with them as entered. Fix those first."
            )
        elif not cycle:
            result.reason = (
                f"No admission cycle {'for ' + str(req.year) + ' ' if req.year else ''}has been "
                "published here yet, so institution cut-offs cannot be compared. The national "
                "rules below were checked."
            )
        elif not matching:
            result.reason = (
                f"No institution has published {req.course_name} requirements for "
                f"{cycle.year} here yet. The national rules below were checked."
            )
        elif result.is_eligible:
            result.reason = (
                "You meet the published requirements below. Meeting them does not guarantee "
                "admission: Post-UTME screening, catchment and quotas still apply."
            )
        else:
            result.reason = (
                "Compared with each institution's published requirements for "
                f"{cycle.year}. Meeting them does not guarantee admission."
            )
        result.next_steps = next_steps(result, checks, bool(matching))
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
