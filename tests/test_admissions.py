import pytest

from app.services.admission_service import AdmissionService
from tests.conftest import auth


def payload(**extra):
    data = {
        "course_name": "Medicine",
        "jamb_score": 200,
        "jamb_subjects": ["Use of English", "Biology", "Chemistry", "Physics"],
        "olevel_results": {
            "English language": "C6",
            "Maths": "C6",
            "Biology": "C6",
            "Chemistry": "C6",
            "Physics": "C6",
        },
    }
    return data | extra


async def test_eligibility_exact_cutoff_and_per_university_grades(client, records):
    response = await client.post(
        "/api/v1/admissions/eligibility",
        json=payload(jamb_score=250),
        headers=auth(records["user"]),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["is_eligible"] is True
    assert len(data["eligible_universities"]) == 1
    assert data["assessments"][1]["status"] == "does_not_meet_requirements"
    response = await client.post(
        "/api/v1/admissions/eligibility", json=payload(), headers=auth(records["user"])
    )
    assert response.json()["is_eligible"] is True
    assert response.json()["borderline_universities"][0]["margin"] == 0


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"jamb_score": 199}, "does_not_meet_requirements"),
        ({"jamb_subjects": ["English", "Biology", "Chemistry"]}, "does_not_meet_requirements"),
        ({"olevel_results": {}}, "insufficient_data"),
        ({"jamb_subjects": []}, "insufficient_data"),
        ({"course_name": "Unknown"}, "insufficient_data"),
        ({"year": 2020}, "insufficient_data"),
    ],
)
async def test_eligibility_conservative(client, records, changes, status):
    response = await client.post(
        "/api/v1/admissions/eligibility", json=payload(**changes), headers=auth(records["user"])
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == status
    assert response.json()["is_eligible"] is False


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/admissions/cutoffs?university=Missing",
        "/api/v1/admissions/cutoffs?course=Missing",
        "/api/v1/admissions/subjects?course=Medicine&university=Missing",
    ],
)
async def test_unknown_filters_never_return_other_schools(client, records, path):
    assert (await client.get(path)).status_code == 404


async def test_cutoffs_include_all_institutions_and_sources(client, records):
    response = await client.get("/api/v1/admissions/cutoffs?course=Medicine")
    assert len(response.json()) == 2
    assert response.json()[0]["source_url"].startswith("https://")
    assert response.json()[0]["year"] == 2026
    assert (await client.get("/api/v1/admissions/cycle/current")).status_code == 200


async def test_university_search_combines_filters_and_paginates(client, records, db):
    records["universities"][1].university_type = "state"
    await db.flush()
    response = await client.get(
        "/api/v1/admissions/universities?name=Example&university_type=state&state=Nasarawa"
    )
    assert response.status_code == 200
    assert [u["id"] for u in response.json()] == [records["universities"][1].id]
    assert (
        await client.get("/api/v1/admissions/universities?name=Example&state=Lagos")
    ).json() == []
    first = (await client.get("/api/v1/admissions/universities?limit=1")).json()
    second = (await client.get("/api/v1/admissions/universities?limit=1&offset=1")).json()
    assert first[0]["id"] != second[0]["id"]


async def test_inactive_admissions_records_are_not_published(client, records, db):
    university = records["universities"][0]
    records["courses"][0].is_active = False
    await db.flush()
    assert (
        await client.get(f"/api/v1/admissions/universities/{university.id}/courses")
    ).status_code == 404
    cutoffs = (await client.get("/api/v1/admissions/cutoffs")).json()
    assert all(row["university"] != university.name for row in cutoffs)
    university.is_active = False
    await db.flush()
    assert (
        await client.get(f"/api/v1/admissions/universities/{university.id}/courses")
    ).status_code == 404


async def test_admin_schedule_and_permissions(client, records):
    body = dict(
        university_id=records["universities"][0].id,
        cycle_id=records["cycle"].id,
        start_date="2026-11-01T09:00:00+01:00",
        end_date="2026-11-30T17:00:00+01:00",
        fee_ngn="2000.00",
        eligibility="See the official course requirements.",
        source_url="https://example.edu.ng/screening",
    )
    assert (
        await client.post("/api/v1/admin/post-utme", json=body, headers=auth(records["user"]))
    ).status_code == 403
    response = await client.post(
        "/api/v1/admin/post-utme", json=body, headers=auth(records["admin"])
    )
    assert response.status_code == 200, response.text
    results = (await client.get("/api/v1/admissions/post-utme")).json()
    assert results[0]["fee_ngn"] == "2000.00"
    assert results[0]["year"] == 2026
    body["end_date"] = "2020-01-01T00:00:00Z"
    assert (
        await client.post("/api/v1/admin/post-utme", json=body, headers=auth(records["admin"]))
    ).status_code == 422


async def test_admin_upsert_course_cycle_and_cutoff(client, records):
    headers = auth(records["admin"])
    response = await client.post(
        "/api/v1/admin/cycles",
        json=dict(year=2027, is_current=True, source_url="https://www.jamb.gov.ng/"),
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert (await client.get("/api/v1/admissions/cycle/current")).json()["year"] == 2027
    body = dict(
        university_id=records["universities"][0].id,
        course_id=records["courses"][1].id,
        cycle_id=response.json()["id"],
        score=200,
        source_url="https://example.edu.ng/cutoffs",
    )
    assert (
        await client.post("/api/v1/admin/cutoffs", json=body, headers=headers)
    ).status_code == 422


async def test_unverified_and_historical_requirements_are_not_eligibility(db, records):
    from app.schemas.schema import EligibilityRequest

    for course in records["courses"]:
        course.requirements_year = 2025
    await db.flush()
    result = await AdmissionService(db).check_eligibility(
        EligibilityRequest(**payload(jamb_score=300))
    )
    assert result.status == "insufficient_data"
    assert not result.is_eligible


def test_national_rules_are_checked_without_any_records():
    from app.schemas.schema import EligibilityRequest
    from app.services.admission_service import general_checks

    def status(**changes):
        req = EligibilityRequest(**payload(**changes))
        return {c["id"]: c["status"] for c in general_checks(req)}

    assert set(status().values()) == {"pass"}
    assert (
        status(jamb_subjects=["Biology", "Chemistry", "Physics", "Mathematics"])["utme_english"]
        == "fail"
    )
    assert status(jamb_subjects=["English", "Biology", "Chemistry"])["utme_count"] == "fail"
    weak = {
        "English": "D7",
        "Mathematics": "E8",
        "Biology": "C6",
        "Chemistry": "F9",
        "Physics": "C4",
    }
    checks = status(olevel_results=weak)
    assert checks["olevel_five"] == "fail"  # D7/E8/F9 are not credits
    assert checks["olevel_english"] == "fail"
    assert checks["olevel_maths"] == "warn"  # most, not all, programmes need it
    assert status(olevel_results={})["olevel"] == "missing"


def test_course_names_match_published_variants():
    from app.services.admission_service import course_matches

    assert course_matches("Medicine", "Medicine and Surgery")
    assert course_matches("MBBS", "Medicine and Surgery")
    assert course_matches("nursing", "Nursing Science")
    assert course_matches("Computer Science", "Computer Science")
    assert not course_matches("Law", "Lawn Tennis Studies")
    assert not course_matches("Medicine", "Veterinary Studies")


async def test_published_variant_is_compared_and_national_failures_block(client, records, db):
    records["courses"][0].name = "Medicine and Surgery"
    await db.commit()
    data = (
        await client.post(
            "/api/v1/admissions/eligibility",
            json=payload(jamb_score=250),
            headers=auth(records["user"]),
        )
    ).json()
    assert any(a["course"] == "Medicine and Surgery" for a in data["assessments"])
    assert data["official_links"][0]["url"] == "https://eligibility.jamb.gov.ng"
    no_english = payload(
        jamb_score=300,
        olevel_results={
            "Maths": "A1",
            "Biology": "A1",
            "Chemistry": "A1",
            "Physics": "A1",
            "Economics": "A1",
        },
    )
    data = (
        await client.post(
            "/api/v1/admissions/eligibility", json=no_english, headers=auth(records["user"])
        )
    ).json()
    assert data["status"] == "does_not_meet_requirements" and not data["is_eligible"]
    assert "national entry rules" in data["reason"]


async def test_no_cycle_still_reports_national_rules(client, records):
    data = (
        await client.post(
            "/api/v1/admissions/eligibility",
            json=payload(year=2019),
            headers=auth(records["user"]),
        )
    ).json()
    assert data["status"] == "insufficient_data"
    assert "No admission cycle for 2019" in data["reason"]
    assert {c["status"] for c in data["general_checks"]} == {"pass"}
    assert any("IBASS" in step for step in data["next_steps"])
