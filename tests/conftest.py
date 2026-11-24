import os
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ["DEBUG"] = "true"
os.environ["SECRET_KEY"] = "test-only-secret-key-at-least-32-bytes-long"
if os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

from app.core.config import settings
from app.core.rate_limit import RateLimiter
from app.db.session import get_db
from app.main import app
from app.models.admission import AdmissionCycle, Course, CutoffMark, University
from app.models.user import User, UserProfile
from app.utils.hashing import hash_password
from app.utils.jwt import create_access_token


@pytest.fixture(autouse=True)
def offline_provider_defaults(monkeypatch):
    """Tests must never inherit live provider or search settings from .env."""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDERS", [])
    monkeypatch.setattr(settings, "LLM_MODEL", "")
    for provider in ("GROQ", "GEMINI", "OPENAI", "ANTHROPIC"):
        monkeypatch.setattr(settings, provider + "_API_KEY", "")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-only-fake-key")
    monkeypatch.setattr(settings, "INTENT_LLM_FALLBACK", False)
    monkeypatch.setattr(settings, "GEMINI_WEB_SEARCH_ENABLED", False)


@pytest.fixture
async def db():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a migrated disposable PostgreSQL database")
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = async_sessionmaker(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )()
        yield session
        await session.close()
        await transaction.rollback()
    await engine.dispose()


@pytest.fixture
async def client(db, monkeypatch):
    async def override_db():
        yield db

    async def allowed(*args):
        return True

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(RateLimiter, "_check", allowed)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
async def records(db):
    user = User(
        email="student@example.com",
        username="student",
        hashed_password=hash_password("password123"),
        data_consent_at=datetime.now(timezone.utc),
    )
    admin = User(
        email="admin@example.com",
        username="administrator",
        hashed_password=hash_password("password123"),
        role="admin",
        data_consent_at=datetime.now(timezone.utc),
    )
    cycle = AdmissionCycle(year=2026, is_current=True)
    db.add_all([user, admin, cycle])
    await db.flush()
    db.add_all([UserProfile(user_id=user.id), UserProfile(user_id=admin.id)])
    universities, courses = [], []
    for name, score, grade in [
        ("Example Federal University", 200, "C6"),
        ("Example State University", 240, "B3"),
    ]:
        uni = University(name=name, state="Nasarawa", university_type="federal")
        db.add(uni)
        await db.flush()
        course = Course(
            university_id=uni.id,
            name="Medicine",
            faculty="Medicine",
            jamb_subjects=["English", "Biology", "Chemistry", "Physics"],
            olevel_requirements={
                "English": grade,
                "Mathematics": grade,
                "Biology": grade,
                "Chemistry": grade,
                "Physics": grade,
            },
            source_url="https://example.edu.ng/requirements",
            requirements_year=2026,
        )
        db.add(course)
        await db.flush()
        db.add(
            CutoffMark(
                university_id=uni.id,
                course_id=course.id,
                cycle_id=cycle.id,
                score=score,
                source_url="https://example.edu.ng/cutoffs",
                is_official=True,
            )
        )
        universities.append(uni)
        courses.append(course)
    await db.commit()
    return {
        "user": user,
        "admin": admin,
        "cycle": cycle,
        "universities": universities,
        "courses": courses,
    }


def auth(user):
    return {"Authorization": "Bearer " + create_access_token({"sub": str(user.id)})}
