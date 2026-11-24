"""Administrator-only, validated source maintenance and operational metrics."""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_admin_user
from app.db.session import AsyncSessionLocal, get_db
from app.models.admission import AdmissionCycle, Course, CutoffMark, PostUTMESchedule, University
from app.models.chat import Message
from app.models.knowledge import DocumentCategory, KnowledgeDocument

router = APIRouter(prefix="/admin", tags=["administration"], dependencies=[Depends(get_admin_user)])
logger = logging.getLogger(__name__)


class SourceInput(BaseModel):
    source_url: HttpUrl

    @field_validator("source_url")
    @classmethod
    def https_source(cls, value):
        if value.scheme != "https":
            raise ValueError("Provide an HTTPS official publication URL")
        return value


class UniversityInput(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    abbreviation: str | None = Field(default=None, max_length=20)
    state: str = Field(min_length=2, max_length=50)
    university_type: Literal["federal", "state", "private"]
    website_url: HttpUrl


class CourseInput(SourceInput):
    requirements_year: int = Field(ge=2000, le=2100)
    university_id: int = Field(ge=1)
    name: str = Field(min_length=2, max_length=150)
    faculty: str = Field(min_length=2, max_length=100)
    jamb_subjects: list[str] = Field(min_length=4, max_length=4)
    olevel_requirements: dict[str, str] = Field(min_length=5)
    course_notes: str | None = Field(default=None, max_length=2000)

    @field_validator("jamb_subjects")
    @classmethod
    def distinct_subjects(cls, subjects):
        from app.services.admission_service import normalize_subject

        normalized = [normalize_subject(s) for s in subjects]
        if any(not s for s in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("Provide four distinct subject names")
        return subjects

    @field_validator("olevel_requirements")
    @classmethod
    def grades(cls, value):
        from app.schemas.schema import EligibilityRequest

        return EligibilityRequest(
            jamb_score=0, course_name="validation", olevel_results=value
        ).olevel_results


class CycleInput(SourceInput):
    year: int = Field(ge=2000, le=2100)
    is_current: bool = False
    registration_start: datetime | None = None
    registration_end: datetime | None = None
    exam_start: datetime | None = None
    exam_end: datetime | None = None
    registration_fee_ngn: Decimal | None = Field(default=None, ge=0)
    guidance: str | None = Field(default=None, max_length=3000)

    @model_validator(mode="after")
    def dates(self):
        for start, end in [
            (self.registration_start, self.registration_end),
            (self.exam_start, self.exam_end),
        ]:
            if any(d and d.tzinfo is None for d in (start, end)):
                raise ValueError("Dates must include a timezone")
            if start and end and end < start:
                raise ValueError("End date must follow start date")
        return self


class CutoffInput(SourceInput):
    university_id: int = Field(ge=1)
    course_id: int = Field(ge=1)
    cycle_id: int = Field(ge=1)
    score: int = Field(ge=0, le=400)
    is_official: bool = True
    notes: str | None = Field(default=None, max_length=1000)


class ScheduleInput(SourceInput):
    university_id: int = Field(ge=1)
    cycle_id: int = Field(ge=1)
    start_date: datetime
    end_date: datetime
    fee_ngn: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    eligibility: str = Field(min_length=5, max_length=3000)

    @model_validator(mode="after")
    def dates(self):
        if self.start_date.tzinfo is None or self.end_date.tzinfo is None:
            raise ValueError("Dates must include a timezone")
        if self.end_date < self.start_date:
            raise ValueError("End date must follow start date")
        return self


class DocumentInput(SourceInput):
    title: str = Field(min_length=3, max_length=300)
    category: DocumentCategory
    cycle_year: int | None = Field(default=None, ge=2000, le=2100)
    text: str = Field(min_length=20, max_length=500_000)


async def require_record(db, model, key):
    obj = await db.get(model, key)
    if obj is None:
        raise HTTPException(404, f"{model.__name__} not found")
    return obj


async def save(db, model, body, match, extra=None):
    obj = await db.scalar(select(model).filter_by(**match))
    if obj is None:
        obj = model(**match)
        db.add(obj)
    data = body.model_dump(mode="json")
    data.update(extra or {})
    for name, value in data.items():
        if hasattr(model, name):
            setattr(obj, name, value)
    await db.commit()
    return {"id": obj.id}


@router.post("/universities")
async def university(body: UniversityInput, db: AsyncSession = Depends(get_db)):
    return await save(db, University, body, {"name": body.name})


@router.post("/courses")
async def course(body: CourseInput, db: AsyncSession = Depends(get_db)):
    await require_record(db, University, body.university_id)
    return await save(
        db,
        Course,
        body,
        {"university_id": body.university_id, "name": body.name, "programme_type": "utme"},
    )


@router.post("/cycles")
async def cycle(body: CycleInput, db: AsyncSession = Depends(get_db)):
    if body.is_current:
        # Serialize current-cycle changes across administrators.
        await db.execute(select(AdmissionCycle).with_for_update())
        await db.execute(update(AdmissionCycle).values(is_current=False))
    values = {
        k: getattr(body, k)
        for k in ("registration_start", "registration_end", "exam_start", "exam_end")
    }
    values["cycle_notes"] = {
        "source_url": str(body.source_url),
        "fee_ngn": str(body.registration_fee_ngn)
        if body.registration_fee_ngn is not None
        else None,
        "guidance": body.guidance,
    }
    return await save(db, AdmissionCycle, body, {"year": body.year}, values)


@router.post("/cutoffs")
async def cutoff(body: CutoffInput, db: AsyncSession = Depends(get_db)):
    course = await require_record(db, Course, body.course_id)
    await require_record(db, AdmissionCycle, body.cycle_id)
    if course.university_id != body.university_id:
        raise HTTPException(422, "Course does not belong to this university")
    return await save(
        db,
        CutoffMark,
        body,
        {k: getattr(body, k) for k in ("cycle_id", "university_id", "course_id")},
    )


@router.post("/post-utme")
async def schedule(body: ScheduleInput, db: AsyncSession = Depends(get_db)):
    await require_record(db, University, body.university_id)
    await require_record(db, AdmissionCycle, body.cycle_id)
    return await save(
        db,
        PostUTMESchedule,
        body,
        {"university_id": body.university_id, "cycle_id": body.cycle_id},
        {"start_date": body.start_date, "end_date": body.end_date, "fee_ngn": body.fee_ngn},
    )


async def index_document(document_id):
    from app.rag.embedding import DocumentIngester

    async with AsyncSessionLocal() as db:
        doc = await db.get(KnowledgeDocument, document_id)
        try:
            count = await DocumentIngester(db).ingest(doc)
            doc.status, doc.chunk_count = "indexed", count
            doc.indexed_at = datetime.now().astimezone()
            await db.commit()
        except Exception:
            logger.exception("Document indexing failed: %s", document_id)
            await db.rollback()
            doc = await db.get(KnowledgeDocument, document_id)
            doc.status = "failed"
            await db.commit()


@router.post("/documents", status_code=202)
async def document(body: DocumentInput, tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    from app.core.config import settings

    doc = KnowledgeDocument(
        title=body.title,
        category=body.category.value,
        cycle_year=body.cycle_year,
        source_url=str(body.source_url),
        doc_meta={"text": body.text},
        status="processing",
        embedding_model=settings.EMBEDDING_MODEL,
    )
    db.add(doc)
    await db.commit()
    tasks.add_task(index_document, doc.id)
    return {"id": doc.id, "status": "processing"}


@router.get("/documents")
async def documents(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    docs = (
        await db.scalars(
            select(KnowledgeDocument)
            .order_by(KnowledgeDocument.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [
        dict(
            id=d.id,
            title=d.title,
            status=d.status,
            chunk_count=d.chunk_count,
            source_url=d.source_url,
            cycle_year=d.cycle_year,
            is_active=d.is_active,
        )
        for d in docs
    ]


@router.delete("/documents/{document_id}", status_code=204)
async def deactivate_document(document_id: int, db: AsyncSession = Depends(get_db)):
    doc = await require_record(db, KnowledgeDocument, document_id)
    doc.is_active = False
    await db.commit()


@router.post("/documents/{document_id}/retry", status_code=202)
async def retry_document(
    document_id: int, tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)
):
    doc = await require_record(db, KnowledgeDocument, document_id)
    if not doc.is_active or doc.status not in ("failed", "pending"):
        raise HTTPException(409, "Only active failed or pending documents can be retried")
    doc.status = "processing"
    await db.commit()
    tasks.add_task(index_document, doc.id)
    return {"id": doc.id, "status": doc.status}


@router.get("/metrics")
async def metrics(db: AsyncSession = Depends(get_db)):
    counts = dict(
        (
            await db.execute(
                select(Message.state, func.count(Message.id))
                .where(Message.role == "assistant")
                .group_by(Message.state)
            )
        ).all()
    )
    p95 = await db.scalar(
        select(func.percentile_cont(0.95).within_group(Message.latency_ms)).where(
            Message.role == "assistant", Message.state == "completed"
        )
    )
    return {
        "assistant_messages_by_state": counts,
        "response_latency_p95_ms": p95,
        "note": "Observed metrics only; research accuracy and usability scores require a labelled evaluation and participant study.",
    }
