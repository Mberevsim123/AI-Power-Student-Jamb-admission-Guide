from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.core.llm_client import LLMClient
from app.models.chat import QueryIntent
from app.models.knowledge import DocumentChunk, KnowledgeDocument
from app.rag.retriever import EmbeddingClient, RAGRetriever
from app.services.local_knowledge import render_local_answer
from scripts.seed_knowledge import seed
from tests.conftest import auth


def test_missing_cutoff_gives_specific_next_step():
    answer = render_local_answer(QueryIntent.cutoff_lookup, "Medicine cutoff", {}, [])
    assert "cannot confirm" in answer
    assert "official admissions page" in answer
    assert "no live web verification" in answer


def test_general_local_guidance_includes_next_step():
    context = {
        "local_guides": [
            {"text": "Check your CAPS status.", "source_url": "https://jamb.gov.ng/caps"}
        ]
    }
    answer = render_local_answer(QueryIntent.general_admission, "CAPS", context, [])
    assert "Check your CAPS status" in answer
    assert "Next, check the official JAMB" in answer


async def test_sql_retrieval_works_without_embeddings_or_qdrant(db, records, monkeypatch):
    async def unavailable(*args, **kwargs):
        raise AssertionError("Lexical retrieval must not call external services")

    monkeypatch.setattr(EmbeddingClient, "embed", unavailable)
    import app.rag.retriever as module

    monkeypatch.setattr(module, "vector_client", unavailable)
    for year, active, status in [
        (2026, True, "indexed"),
        (2025, True, "indexed"),
        (2026, False, "indexed"),
        (2026, True, "failed"),
    ]:
        doc = KnowledgeDocument(
            title="Official CAPS guide",
            category="general_faq",
            cycle_year=year,
            source_url="https://jamb.gov.ng/caps",
            is_active=active,
            status=status,
        )
        db.add(doc)
        await db.flush()
        db.add(
            DocumentChunk(
                document_id=doc.id,
                chunk_index=0,
                text="CAPS admission status is available through JAMB e-Facility.",
                category="general_faq",
                cycle_year=year,
            )
        )
    await db.flush()
    chunks = await RAGRetriever(db).retrieve("CAPS admission status", QueryIntent.general_admission)
    assert len(chunks) == 1 and chunks[0].cycle_year == 2026
    assert chunks[0].source_url == "https://jamb.gov.ng/caps"
    assert (
        await RAGRetriever(db).retrieve(
            "CAPS admission status", QueryIntent.general_admission, cycle_year=2027
        )
        == []
    )


@pytest.mark.parametrize("streaming", [False, True])
async def test_chat_without_any_keys_answers_from_saved_records(
    client, records, monkeypatch, streaming
):
    for provider in ("GROQ", "GEMINI", "OPENAI", "ANTHROPIC"):
        monkeypatch.setattr(settings, provider + "_API_KEY", "")
    generation = AsyncMock(side_effect=AssertionError("No provider may be invoked"))
    monkeypatch.setattr(LLMClient, "complete", generation)
    path = "/api/v1/chat/stream" if streaming else "/api/v1/chat/"
    response = await client.post(
        path, json={"message": "Cut-off for Medicine in 2026"}, headers=auth(records["user"])
    )
    assert response.status_code == 200, response.text
    assert "200" in response.text and "240" in response.text and "2026" in response.text
    assert "example.edu.ng/cutoffs" in response.text
    if streaming:
        assert "[DONE]" in response.text and "event: error" not in response.text
    else:
        assert response.json()["model_used"] == "local-guidance"
        assert response.json()["rag_sources"]
    generation.assert_not_called()


async def test_seed_idempotent_and_no_fabricated_cutoffs(db, records):
    from sqlalchemy import func, select

    from app.models.admission import CutoffMark, University

    before = await db.scalar(select(func.count()).select_from(CutoffMark))
    from app.services.local_knowledge import UNIVERSITIES

    assert await seed(db) == len(UNIVERSITIES) == 329
    assert await seed(db) == 0
    assert await db.scalar(select(func.count()).select_from(CutoffMark)) == before
    nsuk = await db.scalar(select(University).where(University.abbreviation == "NSUK"))
    assert nsuk.general_cutoff is None
    assert nsuk.admissions_meta["source_url"].startswith("https://www.nuc.edu.ng/")


async def test_unknown_university_never_returns_other_schools_cutoffs(client, records, monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "")
    response = await client.post(
        "/api/v1/chat/",
        json={"message": "Cut-off for Medicine at Unknown University in 2026"},
        headers=auth(records["user"]),
    )
    assert response.status_code == 200
    content = response.json()["content"]
    assert "cannot confirm" in content
    assert "**200**" not in content and "**240**" not in content


async def test_no_provider_subject_requirements_respect_requested_year(
    client, records, monkeypatch
):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "")
    for year in (2026, 2027):
        response = await client.post(
            "/api/v1/chat/",
            json={"message": f"JAMB subject combination for Medicine in {year}"},
            headers=auth(records["user"]),
        )
        assert response.status_code == 200
        content = response.json()["content"]
        if year == 2026:
            assert "Biology" in content and "Chemistry" in content and "Physics" in content
            assert "C6" in content and "B3" in content
        else:
            assert "cannot confirm" in content and "2027" in content
            assert "C6" not in content and "B3" not in content


def test_directory_covers_every_nuc_university_type():
    from collections import Counter

    from app.services.local_knowledge import UNIVERSITIES, directory_query, match_university

    assert Counter(row[3] for row in UNIVERSITIES) == {"private": 183, "federal": 77, "state": 69}
    assert [m["name"] for m in match_university("lagos state university of education")] == [
        "Lagos State University of Education, Ijanikin"
    ]
    assert match_university("Is Covenant University good?")[0]["university_type"] == "private"
    listing = directory_query("List private universities in Ogun State")
    assert listing["filters"] == {"type": "private", "state": "Ogun"}
    assert all(u["state"] == "Ogun" for u in listing["universities"])
    assert directory_query("How many federal universities are in Nigeria?")["count"] == 77
    assert directory_query("What is the cut-off at UNILAG?") is None


async def test_chat_lists_directory_without_a_model(client, records, monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "")
    response = await client.post(
        "/api/v1/chat/",
        json={"message": "Which private universities are in Abuja?"},
        headers=auth(records["user"]),
    )
    content = response.json()["content"]
    assert "NUC-approved private universities** in the FCT (Abuja)" in content
    assert "Baze University" in content and "nuc.edu.ng" in content
