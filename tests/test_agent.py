"""Agent behaviour: perception, memory, negotiation, management, proactivity, verification."""

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.agent.core import AdmissionsAgent
from app.agent.memory import Beliefs
from app.agent.perception import perceive
from app.agent.verifier import disclosure, verify
from app.core.config import settings
from app.core.llm_client import LLMClient
from app.models.chat import Conversation, QueryIntent
from app.models.user import UserProfile
from app.rag.prompt_builder import PromptBuilder
from app.rag.retriever import RAGRetriever, RetrievedChunk
from tests.conftest import auth

Q = QueryIntent


@pytest.fixture(autouse=True)
def specialists_must_not_fail(caplog):
    """Specialist errors are contained at runtime; in tests they must not happen at all."""
    caplog.set_level(logging.ERROR, logger="app.agent.core")
    yield
    failures = [r.getMessage() for r in caplog.records if "specialist" in r.getMessage()]
    assert not failures, failures


# ── Perception (sensors) ────────────────────────────────────────────────────


def test_perceives_student_facts_affect_and_register():
    p = perceive(
        "Abeg I'm so worried. I scored 245 and I want to study Medicine at UNILAG. "
        "Maths B3, English C4, bio A1, chem B2, physics C5",
        Q.course_eligibility,
    )
    assert p.entities["jamb_score"] == 245
    assert p.entities["course"] == "Medicine"
    assert p.entities["universities"] == ["University of Lagos"]
    assert p.entities["olevel"]["mathematics"] == "B3"
    assert {"jamb_score", "course", "universities", "olevel"} <= p.stated
    assert p.affect == "anxious" and p.pidgin


@pytest.mark.parametrize(
    "text,expected",
    [
        ("What is the minimum score of 180 for UI?", None),  # not the student's score
        ("I have 5 credits", None),
        ("my jamb score is 262", 262),
        ("can I get in with 198?", 198),
    ],
)
def test_score_sensor_needs_a_student_cue(text, expected):
    assert perceive(text, Q.general_admission).entities.get("jamb_score") == expected


def test_milestones_respect_negation_and_choices_are_parsed():
    assert perceive("I have registered for JAMB", Q.jamb_process).milestones_done == [
        "register_utme"
    ]
    assert perceive("I haven't registered for JAMB yet", Q.jamb_process).milestones_done == []
    assert perceive("option B please", Q.out_of_scope).option_choice == 1
    assert perceive("the second one", Q.out_of_scope).option_choice == 1


def test_unknown_named_institution_is_flagged():
    assert perceive("Cut-off at Unknown University", Q.cutoff_lookup).unknown_institution
    assert not perceive("Which university is best?", Q.school_recommendation).unknown_institution


# ── Memory (beliefs) ────────────────────────────────────────────────────────


def test_beliefs_track_provenance_conflicts_and_propose_profile_patch():
    profile = SimpleNamespace(
        jamb_score=200,
        preferred_course=None,
        preferred_universities=["University of Ibadan"],
        olevel_results={},
        state_of_origin=None,
        academic_stage="pre_jamb",
    )
    beliefs = Beliefs({}, profile)
    beliefs.observe(
        perceive("I scored 245 and I want to study Law at UNILAG", Q.course_eligibility)
    )
    assert beliefs.get("jamb_score") == 245 and beliefs.source("jamb_score") == "said in this chat"
    assert beliefs.conflicts[0]["field"] == "jamb_score"
    patch = beliefs.profile_patch()
    assert patch["jamb_score"] == 245 and patch["preferred_course"] == "Law"
    assert patch["preferred_universities"] == ["University of Ibadan", "University of Lagos"]
    assert patch["academic_stage"] == "post_jamb"
    assert "UTME score 245" in beliefs.summary()


def test_focus_decays_but_facts_persist():
    beliefs = Beliefs({}, None)
    beliefs.observe(perceive("Cut-off for Law at UNILAG", Q.cutoff_lookup))
    assert beliefs.focus["course"] == "Law"
    for _ in range(4):
        beliefs.observe(perceive("How do I register for JAMB?", Q.jamb_process))
    assert "course" not in beliefs.focus


# ── Deliberation without a database ────────────────────────────────────────


async def _deliberate(text, intent, memory=None, profile=None):
    user = SimpleNamespace(id=1, username="ada", profile=profile)
    conv = SimpleNamespace(id=1, student_context=memory or {})
    return await AdmissionsAgent().deliberate(
        user=user, conv=conv, message=text, intent=intent, query=text
    )


@pytest.mark.parametrize(
    "text,intent",
    [
        ("My score is 245", Q.course_eligibility),
        ("thanks a lot!", Q.greeting),
        ("I have uploaded my O'Level", Q.general_admission),
        ("Write a song", Q.out_of_scope),
    ],
)
async def test_intent_refined_by_perception(text, intent):
    assert (await _deliberate(text, Q.out_of_scope)).intent == intent


async def test_crisis_gets_safety_reply_and_stops_planning():
    board = await _deliberate("I failed jamb and I want to die", Q.out_of_scope)
    assert board.safety and "112" in board.local_reply
    assert board.actions == [{"tool": "safety_protocol", "summary": board.actions[0]["summary"]}]


async def test_follow_up_query_is_expanded_from_memory():
    first = await _deliberate("I want to study Law at UNILAG", Q.general_admission)
    board = await _deliberate("What is the cut-off?", Q.cutoff_lookup, first.beliefs.snapshot())
    assert board.query == "What is the cut-off?\nLaw University of Lagos"
    broad = await _deliberate(
        "Which universities offer it?", Q.cutoff_lookup, first.beliefs.snapshot()
    )
    assert "University of Lagos" not in broad.query


async def test_missing_slot_is_asked_once_then_filled_from_bare_answer():
    board = await _deliberate("Am I eligible for Medicine?", Q.course_eligibility)
    assert board.ask == "jamb_score" and "I scored 250" in board.suggestions
    nxt = await _deliberate("245", Q.out_of_scope, board.beliefs.snapshot())
    assert nxt.beliefs.get("jamb_score") == 245
    assert nxt.intent == Q.course_eligibility


# ── Verifier (hallucination guard) ─────────────────────────────────────────


def test_verifier_flags_unsupported_figures_only():
    evidence = {"structured": {"cutoffs": [{"score": 240}]}, "student": ["I scored 210"]}
    answer = "The cut-off is 240 and you have 210. The Post-UTME fee is ₦2,000; aim for 280 points."
    result = verify(answer, evidence)
    assert {c["value"] for c in result["unsupported"]} == {"2000", "280"}
    assert "₦2,000" in disclosure(result)
    assert verify(answer, evidence, web_cited=True)["unsupported"] == []
    clean = verify("Register at a CBT centre.", evidence)
    assert clean["checked"] == 0 and not (
        clean["unsupported"] or clean["timing"] or clean["identity"]
    )


# ── Context budgeting ──────────────────────────────────────────────────────


def test_prompt_fits_context_budget(monkeypatch):
    monkeypatch.setattr(settings, "PROMPT_CONTEXT_CHARS", 10000)
    monkeypatch.setattr(settings, "PROMPT_HISTORY_CHARS", 2000)
    sources = [
        RetrievedChunk(i, f"Doc {i}", "general_faq", "word " * 2000, 0.9, 2026, "https://x.ng")
        for i in range(3)
    ]
    history = [
        SimpleNamespace(
            role="user" if i % 2 == 0 else "assistant", content="x" * 900, state="completed"
        )
        for i in range(20)
    ]
    messages = PromptBuilder().build(
        None, None, history, "question", sources, {"cutoffs": [{"score": 1}] * 30}, "\n\n### Agent"
    )
    assert len(messages[0]["content"]) <= 10000
    assert "### Agent" in messages[0]["content"]
    assert 4 <= len(messages) - 2 < 20  # oldest turns dropped, recent kept
    assert messages[-1]["content"] == "question"


# ── End-to-end through the API (database) ──────────────────────────────────


def capture_llm(monkeypatch, answer="Here is your guidance."):
    prompts = []

    async def complete(self, messages, max_tokens=4096):
        prompts.append(messages)
        return answer, 10

    monkeypatch.setattr(LLMClient, "complete", complete)
    monkeypatch.setattr(RAGRetriever, "retrieve", AsyncMock(return_value=[]))
    return prompts


async def say(client, user, message, conversation_id=None):
    response = await client.post(
        "/api/v1/chat/",
        json={"message": message, "conversation_id": conversation_id},
        headers=auth(user),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_negotiates_alternatives_then_resolves_choice(client, records, monkeypatch, db):
    prompts = capture_llm(monkeypatch)
    user = records["user"]
    reply = await say(
        client, user, "I scored 210 and I want to study Medicine at Example State University"
    )
    agent = reply["structured_context"]["agent"]
    system = prompts[-1][0]["content"]
    assert "margin -30" in system and "Negotiate, don't decide" in system
    options = agent["options"]
    assert options[0]["university"] == "Example Federal University"
    assert options[0]["margin"] == 10 and options[0]["id"] == "A"
    assert any(o["kind"] == "keep_target" for o in options)
    assert agent["profile_patch"]["jamb_score"] == 210
    assert agent["profile_patch"]["preferred_universities"] == ["Example State University"]

    reply = await say(client, user, "Option A", reply["conversation_id"])
    agent = reply["structured_context"]["agent"]
    assert any("chose option A" in a["summary"] for a in agent["actions"])
    assert "The student chose" in prompts[-1][0]["content"]
    conv = await db.get(Conversation, reply["conversation_id"])
    await db.refresh(conv)
    assert conv.student_context["negotiation"]["status"] == "accepted"
    assert "Agreed plan" in conv.summary


async def test_memory_carries_goal_and_local_mode_renders_analysis(client, records, monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "")
    user = records["user"]
    first = await say(client, user, "I want to study Medicine")
    reply = await say(client, user, "I scored 250", first["conversation_id"])
    assert reply["intent"] == "course_eligibility"
    assert reply["model_used"] == "local-guidance"
    assert "+50" in reply["content"] and "+10" in reply["content"]
    assert "example.edu.ng/cutoffs" in reply["content"]


async def test_journey_is_managed_from_chat_and_briefing(client, records, db):
    user = records["user"]
    reply = await say(client, user, "Good news, I have registered for JAMB!")
    assert reply["structured_context"]["agent"]["journey"]["updated"] == ["register_utme"]
    briefing = (await client.get("/api/v1/agent/briefing", headers=auth(user))).json()
    steps = {s["id"]: s for s in briefing["journey"]["steps"]}
    assert steps["register_utme"]["done"] and steps["register_utme"]["by"] == "chat"
    assert briefing["profile"]["completeness"] < 100
    response = await client.patch(
        "/api/v1/agent/journey/register_utme", json={"done": False}, headers=auth(user)
    )
    assert response.status_code == 200
    assert not {s["id"]: s for s in response.json()["steps"]}["register_utme"]["done"]
    assert (
        await client.patch("/api/v1/agent/journey/unknown", json={"done": True}, headers=auth(user))
    ).status_code == 404


async def test_proactive_deadline_nudge_from_verified_cycle(client, records, monkeypatch, db):
    capture_llm(monkeypatch)
    now = datetime.now(timezone.utc)
    cycle = records["cycle"]
    cycle.registration_start = now - timedelta(days=10)
    cycle.registration_end = now + timedelta(days=2, hours=1)
    cycle.cycle_notes = {"source_url": "https://jamb.gov.ng/news"}
    await db.commit()
    briefing = (await client.get("/api/v1/agent/briefing", headers=auth(records["user"]))).json()
    assert briefing["nudges"][0]["severity"] == "urgent"
    assert "closes in 2 days" in briefing["nudges"][0]["detail"]
    reply = await say(client, records["user"], "What subjects do I need for Medicine?")
    assert reply["structured_context"]["agent"]["nudges"][0]["title"].startswith("JAMB 2026")

    cycle.cycle_notes = {}  # unsourced calendars never produce alerts
    await db.commit()
    briefing = (await client.get("/api/v1/agent/briefing", headers=auth(records["user"]))).json()
    assert briefing["nudges"] == []


@pytest.mark.parametrize(
    "answer,flagged", [("The Medicine cut-off is 275.", True), ("The cut-off is 240.", False)]
)
async def test_unsupported_model_figures_are_disclosed(
    client, records, monkeypatch, answer, flagged
):
    capture_llm(monkeypatch, answer)
    reply = await say(client, records["user"], "Medicine cut-off at Example State University")
    verification = reply["structured_context"]["agent"]["verification"]
    assert ("Check before relying on this" in reply["content"]) is flagged
    assert bool(verification["unsupported"]) is flagged


async def test_social_replies_and_recall_need_no_model(client, records, monkeypatch):
    model = AsyncMock(return_value=("ok", 1))  # the substantive first turn may use the model
    monkeypatch.setattr(LLMClient, "complete", model)
    monkeypatch.setattr(RAGRetriever, "retrieve", AsyncMock(return_value=[]))
    user = records["user"]
    await say(client, user, "I want to study Medicine at Example Federal University")
    model.side_effect = AssertionError("Social turns must not use the model")
    hello = await say(client, user, "hello")
    assert "Welcome back" in hello["content"] and "Medicine" in hello["content"]
    thanks = await say(client, user, "thank you!", hello["conversation_id"])
    assert thanks["content"].startswith("You're welcome")
    crisis = await say(client, user, "I want to end my life")
    assert "112" in crisis["content"] and crisis["model_used"] == "local-guidance"


async def test_stream_emits_agent_frame_before_answer(client, records, monkeypatch, db):
    capture_llm(monkeypatch)
    response = await client.post(
        "/api/v1/chat/stream",
        json={"message": "I scored 230. Am I eligible for Medicine?"},
        headers=auth(records["user"]),
    )
    text = response.text
    assert text.index("event: metadata") < text.index("event: agent") < text.index('"delta"')
    assert "[DONE]" in text
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == records["user"].id))
    await db.refresh(profile)
    assert profile.total_queries == 1


def test_reciprocal_rank_fusion_rewards_agreement():
    from app.rag.retriever import reciprocal_rank_fusion

    def chunk(doc, text):
        return RetrievedChunk(doc, f"Doc {doc}", "general_faq", text, 0.5, None, None, doc)

    lexical = [chunk(1, "a"), chunk(2, "b")]
    semantic = [chunk(2, "b"), chunk(3, "c")]
    fused = reciprocal_rank_fusion(lexical, semantic)
    assert [c.document_id for c in fused] == [2, 1, 3]


def test_negotiator_infers_preferences_from_the_goal():
    from app.agent.core import Negotiator

    target = {"university": "UNILAG", "state": "Lagos", "university_type": "federal"}
    beliefs = Beliefs({}, None)
    prefs = Negotiator._preferences(beliefs, [target])
    rows = [
        {"university": "EKSU", "state": "Ekiti", "university_type": "state", "margin": 43},
        {"university": "LASU", "state": "Lagos", "university_type": "state", "margin": 23},
        {"university": "UNILORIN", "state": "Kwara", "university_type": "federal", "margin": 13},
    ]
    ranked = sorted(rows, key=lambda r: Negotiator._utility(r, prefs), reverse=True)
    assert [r["university"] for r in ranked] == ["LASU", "UNILORIN", "EKSU"]
    assert Negotiator._why(ranked[0], prefs).startswith("also in Lagos")


async def test_related_course_and_stage_aware_alerts(client, records, monkeypatch, db):
    from app.models.admission import Course, CutoffMark

    prompts = capture_llm(monkeypatch)
    state_uni = records["universities"][1]  # Medicine cut-off 240
    nursing = Course(
        university_id=state_uni.id,
        name="Nursing Science",
        faculty="Medicine",
        source_url="https://example.edu.ng/requirements",
        requirements_year=2026,
    )
    db.add(nursing)
    await db.flush()
    db.add(
        CutoffMark(
            university_id=state_uni.id,
            course_id=nursing.id,
            cycle_id=records["cycle"].id,
            score=215,
            source_url="https://example.edu.ng/cutoffs",
            is_official=True,
        )
    )
    now = datetime.now(timezone.utc)
    cycle = records["cycle"]
    cycle.registration_start, cycle.registration_end = (
        now - timedelta(days=1),
        now + timedelta(days=1),
    )
    cycle.cycle_notes = {"source_url": "https://jamb.gov.ng/news"}
    await db.commit()
    reply = await say(
        client,
        records["user"],
        "I scored 220 and I want to study Medicine at Example State University",
    )
    agent = reply["structured_context"]["agent"]
    related = [o for o in agent["options"] if o["kind"] == "related_course"]
    assert related and related[0]["course"] == "Nursing Science" and related[0]["margin"] == 5
    # A student who already has a UTME score is past registration: no registration alert.
    assert not any("registration" in n["title"] for n in agent["nudges"])
    assert "registration" not in prompts[-1][0]["content"].split("### Agent analysis")[1]


async def test_which_schools_gets_options_even_when_target_is_met(client, records, monkeypatch, db):
    capture_llm(monkeypatch)
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == records["user"].id))
    profile.preferred_universities = ["Example Federal University"]  # cut-off 200: met
    await db.commit()
    reply = await say(client, records["user"], "I scored 250, which schools can I get Medicine?")
    options = reply["structured_context"]["agent"]["options"]
    assert [o["university"] for o in options] == ["Example State University"]
    assert not any(o["kind"] == "keep_target" for o in options)


async def test_every_prompt_the_agent_suggests_is_in_scope():
    """Clicking one of the agent's own suggestions must never get an out-of-scope refusal."""
    from app.agent.journey import STEPS
    from app.api.v1.routes.agent import _suggestions
    from app.services.intent_service import IntentClassifier

    profile = SimpleNamespace(
        preferred_course="Medicine", jamb_score=240, preferred_universities=["UNILAG"]
    )
    prompts = (
        [s.prompt for s in STEPS]
        + _suggestions(profile, {}, None)
        + [
            "Show me safer alternatives",
            "What matters most when choosing?",
            "Federal universities only",
            "Close to my state",
            "Keep my course, any school",
            "Help me choose a course and institutions that fit me",
        ]
    )
    classifier = IntentClassifier()
    for prompt in prompts:
        assert await classifier.classify(prompt) != Q.out_of_scope, prompt


def test_review_removes_fabricated_tables_and_flags_bad_claims():
    from app.agent.formatter import tidy
    from app.agent.verifier import audit_tables

    answer = (
        "Here is a checklist.\n"
        "| University | Cut-off (2025-26) | Source |\n|---|---|---|\n"
        + "| Abuja University | 68 + 68% | x |\n" * 20
        + "Register by the CAPS deadline (usually mid‑July). Apply to ABU (University of Abuja)."
    )
    text, removed = audit_tables(tidy(answer), {"student": ["hi"]})
    assert removed[0]["unsupported"] == ["68"] and "| Abuja University" not in text
    assert "[!WARNING]" in text
    result = verify(text, {"student": ["hi"]})
    assert result["timing"] == ["mid‑July"]
    assert result["identity"] == [
        "ABU is Ahmadu Bello University, Zaria, not “University of Abuja”."
    ]
    # Supported tables survive, deduplicated; verified dates are not flagged.
    ok = "| School | Cut-off |\n|---|---:|\n| UNILAG | 270 |\n| UNILAG | 270 |\n| UI | 250 |"
    kept, removed = audit_tables(tidy(ok), {"structured": {"cutoffs": [270, 250]}})
    assert removed == [] and kept.count("UNILAG") == 1
    assert verify("Closes 26 November.", {"r": "2026-11-26T00:00:00"})["timing"] == []


def test_official_links_are_recommended_and_untrusted_links_unlinked():
    from app.agent.links import guard_links, recommend
    from app.services.local_knowledge import match_university

    labels = [
        link["label"] for link in recommend("How do I upload my O'Level?", Q.olevel_requirement)
    ]
    assert labels[0] == "JAMB e-Facility"
    post_utme = recommend("Post-UTME at UNILAG?", Q.post_utme_info, match_university("UNILAG"))
    assert post_utme[0]["url"] == "https://www.unilag.edu.ng"
    assert recommend("hello", Q.greeting) == []
    text, removed = guard_links(
        "Use [JAMB](https://www.jamb.gov.ng), [this](https://jamb-portal.example.com/login), "
        "https://fake.com/x and [source](https://news.example.org/a)",
        {"news.example.org"},
    )
    assert "[JAMB](https://www.jamb.gov.ng)" in text and "[source](" in text
    assert "jamb-portal.example.com" not in text and "`fake.com`" in text
    assert removed == ["https://jamb-portal.example.com/login", "https://fake.com/x"]


async def test_chat_refers_to_platforms_and_strips_invented_links(client, records, monkeypatch):
    prompts = capture_llm(
        monkeypatch, "Upload it on [the portal](https://jamb-upload.example.com) today."
    )
    reply = await say(client, records["user"], "How do I upload my O'Level results to JAMB?")
    agent = reply["structured_context"]["agent"]
    assert agent["links"][0]["url"] == "https://efacility.jamb.gov.ng"
    assert "https://efacility.jamb.gov.ng" in prompts[-1][0]["content"]
    assert "example.com" not in reply["content"] and "the portal" in reply["content"]
    assert agent["verification"]["links_removed"] == ["https://jamb-upload.example.com"]
