"""The admissions agent: sense -> believe -> deliberate -> act -> (generate) -> verify -> reflect.

Specialists collaborate on a shared blackboard. Each reads the percept and beliefs and
contributes observations (tool results), nudges, options, directives or a local reply.
The manager arbitrates: it bounds how much reaches the student and the model, so the
answer stays focused, and it keeps one clarifying question per turn at most.

Planning is deterministic. Consequential facts (cut-offs, eligibility, dates) come from
tools over verified records, not from a model's function-calling, so behaviour is the
same whichever provider answers, including the no-provider local mode.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.agent import journey, links
from app.agent.memory import Beliefs
from app.agent.perception import Percept, perceive
from app.agent.tools import AdmissionsTools, band, related_courses
from app.core.gemini_client import safe_web_url
from app.models.chat import QueryIntent

logger = logging.getLogger(__name__)
LAGOS = ZoneInfo("Africa/Lagos")
MAX_NUDGES, MAX_OPTIONS, MAX_SUGGESTIONS = 2, 4, 4
ANALYSIS_INTENTS = {
    QueryIntent.course_eligibility,
    QueryIntent.school_recommendation,
    QueryIntent.cutoff_lookup,
}


@dataclass
class Nudge:
    kind: str
    severity: str  # urgent | soon | info
    title: str
    detail: str
    source_url: str | None = None
    prompt: str | None = None


@dataclass
class Option:
    id: str
    kind: str  # same_course_elsewhere | related_course | keep_target
    label: str
    university: str | None = None
    course: str | None = None
    cutoff: int | None = None
    margin: int | None = None
    year: int | None = None
    source_url: str | None = None


@dataclass
class Blackboard:
    percept: Percept
    beliefs: Beliefs
    intent: QueryIntent
    query: str
    name: str | None = None
    observations: dict = field(default_factory=dict)
    actions: list = field(default_factory=list)
    directives: list = field(default_factory=list)
    nudges: list = field(default_factory=list)
    options: list = field(default_factory=list)
    suggestions: list = field(default_factory=list)
    ask: str | None = None
    profile_patch: dict = field(default_factory=dict)
    journey: dict = field(default_factory=dict)
    journey_updates: list = field(default_factory=list)
    local_reply: str | None = None
    safety: bool = False
    resolution: dict | None = None
    links: list = field(default_factory=list)

    def act(self, tool, summary):
        self.actions.append({"tool": tool, "summary": summary})

    def suggest(self, *prompts):
        for prompt in prompts:
            if prompt and prompt not in self.suggestions:
                self.suggestions.append(prompt)


def _days(delta):
    return max(0, int(delta.total_seconds() // 86400))


def _date(value):
    return value.astimezone(LAGOS).strftime("%a %d %b %Y") if value else "?"


# ═══════════════════════════════════════════════════════════════════════════
# Specialists
# ═══════════════════════════════════════════════════════════════════════════


class Counsellor:
    """Social intelligence: affect, tone, safety, greetings and small talk."""

    name = "counsellor"

    async def contribute(self, board: Blackboard, agent: "AdmissionsAgent"):
        p = board.percept
        name = f", {board.name}" if board.name else ""
        if p.affect == "crisis":
            board.safety = True
            board.local_reply = (
                f"I'm really sorry you're feeling this way{name}. You matter far more than any "
                "exam score or admission list, and you don't have to carry this alone.\n\n"
                "Please talk to someone you trust right now — a parent, relative, teacher, "
                "religious leader or friend. If you are in immediate danger, call **112** "
                "(Nigeria's emergency number) or go to the nearest hospital.\n\n"
                "Many students who missed their first target still got admitted later through "
                "another school, course, or the next UTME. When you're ready, I'm here to help "
                "you work out a plan, one step at a time."
            )
            board.act("safety_protocol", "Responded with care and emergency guidance")
            return
        tone = {
            "anxious": "The student sounds anxious. Open with one calm, reassuring sentence, "
            "then give clear, concrete steps. Avoid alarming wording.",
            "discouraged": "The student sounds discouraged. Acknowledge the feeling in one "
            "sentence, then show realistic paths forward. Never promise admission.",
            "frustrated": "The student sounds frustrated or confused. Be extra clear: short "
            "sentences, numbered steps, no jargon.",
            "positive": "The student is in good spirits. Match their energy briefly.",
        }.get(p.affect)
        if tone:
            board.directives.append(tone)
        if p.pidgin:
            board.directives.append(
                "The student writes in Nigerian Pidgin. Reply in simple, friendly English; "
                "a light Pidgin phrase is welcome if natural."
            )
        if p.brief and board.intent not in (QueryIntent.greeting, QueryIntent.out_of_scope):
            board.directives.append(
                "The student wrote briefly: keep the answer short and scannable."
            )
        if "thanks" in p.acts and board.intent != QueryIntent.greeting:
            board.directives.append("The student thanked you; acknowledge it in a few words.")

    @staticmethod
    def greeting(board: Blackboard, recall: dict | None):
        hour = datetime.now(LAGOS).hour
        part = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
        name = f" {board.name}" if board.name else ""
        lines = [f"Good {part}{name}! 👋"]
        course = board.beliefs.get("course")
        unis = board.beliefs.get("universities") or []
        if recall and recall.get("course"):
            where = f" at {recall['universities'][0]}" if recall.get("universities") else ""
            lines.append(
                f"Welcome back — last time we were looking at **{recall['course']}**{where}."
            )
        elif course:
            where = f" at {unis[0]}" if unis else ""
            lines.append(f"Good to see you. You're aiming for **{course}**{where}.")
        else:
            lines.append(
                "I'm JAMB Guide — I can check your eligibility, compare cut-off marks, plan your "
                "subject combination and keep your admission steps on track."
            )
        return lines


class MemoryKeeper:
    """Belief revision against long-term memory; proposes profile updates."""

    name = "memory"

    async def contribute(self, board: Blackboard, agent):
        beliefs = board.beliefs
        board.profile_patch = beliefs.profile_patch()
        for conflict in beliefs.conflicts:
            label = conflict["field"].replace("_", " ")
            board.directives.append(
                f"The student just gave a different {label} ({conflict['chat']}) from their saved "
                f"profile ({conflict['profile']}). Use the new value for this conversation."
            )
        if board.profile_patch:
            board.act("memory", "Noted new details: " + ", ".join(sorted(board.profile_patch)))


class JourneyManager:
    """Manages the admissions checklist: records progress and picks the next step."""

    name = "journey"

    async def contribute(self, board: Blackboard, agent):
        profile = agent.profile
        p = board.percept
        done = list(p.milestones_done)
        if "jamb_score" in p.stated:
            done += ["write_utme", "check_result"]  # a known score implies both
        if done and profile is not None:
            board.journey_updates = journey.mark(profile, list(dict.fromkeys(done)), by="chat")
            if board.journey_updates:
                titles = [journey.STEP_BY_ID[s].title for s in board.journey_updates]
                board.act("journey", "Ticked off: " + "; ".join(titles))
                board.directives.append(
                    "Congratulate the student briefly: their journey checklist now shows "
                    f"{', '.join(titles)} as done."
                )
        board.journey = journey.overview(profile, board.beliefs)
        nxt = board.journey["next"]
        if nxt and board.intent in (
            QueryIntent.greeting,
            QueryIntent.general_admission,
            QueryIntent.jamb_process,
        ):
            board.suggest(nxt["prompt"])


class EligibilityAnalyst:
    """Runs eligibility tools whenever the student's goal and score are known."""

    name = "analyst"

    async def contribute(self, board: Blackboard, agent: "AdmissionsAgent"):
        tools = agent.tools
        p, beliefs = board.percept, board.beliefs
        negotiating = (beliefs.memory.get("negotiation") or {}).get("status") in (
            "open",
            "revising",
        )
        relevant = (
            board.intent in ANALYSIS_INTENTS
            or negotiating
            or ("jamb_score" in p.entities and beliefs.focus_course())
        )
        if not relevant or board.intent in (QueryIntent.greeting, QueryIntent.out_of_scope):
            return
        if p.unknown_institution:
            board.directives.append(
                "The institution the student named is not in the verified records. Say so; do "
                "not substitute another institution's figures."
            )
            return
        course, score = beliefs.focus_course(), beliefs.get("jamb_score")
        if board.intent in (QueryIntent.course_eligibility, QueryIntent.school_recommendation):
            if not course:
                board.ask = "course"
            elif score is None:
                board.ask = "jamb_score"
        if not (tools.available and course and score is not None):
            return
        year = beliefs.focus.get("year")
        rows = await tools.eligibility_snapshot(course, score, year)
        board.observations["eligibility_snapshot"] = rows
        if not rows:
            board.act("cutoff_lookup", f"No official {course} cut-offs are on file for comparison")
            board.directives.append(
                f"No verified {course} cut-off marks are on file for this cycle; do not quote any."
            )
            return
        board.act(
            "eligibility_snapshot",
            f"Compared {score} with {len(rows)} official {course} cut-off(s) for {rows[0]['year']}",
        )
        subjects, olevel = beliefs.get("utme_subjects"), beliefs.get("olevel")
        if subjects and olevel:
            result = await tools.full_eligibility(course, score, subjects, olevel, year)
            board.observations["eligibility_check"] = {
                "status": result.status,
                "assessments": result.assessments,
                "reason": result.reason,
            }
            board.act(
                "eligibility_check", f"Full requirements check: {result.status.replace('_', ' ')}"
            )
        else:
            missing = [
                x for x, v in (("UTME subjects", subjects), ("O'Level grades", olevel)) if not v
            ]
            board.directives.append(
                "This snapshot compares scores only. To confirm subject and O'Level requirements, "
                f"the student can share their {' and '.join(missing)}."
            )


class Negotiator:
    """When a goal looks out of reach, proposes trade-offs and resolves the student's choice."""

    name = "negotiator"

    async def contribute(self, board: Blackboard, agent: "AdmissionsAgent"):
        beliefs = board.beliefs
        state = beliefs.memory.get("negotiation")
        if state and state.get("status") in ("open", "revising") and self._resolve(board, state):
            return
        rows = board.observations.get("eligibility_snapshot")
        if not rows:
            return
        course, score = beliefs.focus_course(), beliefs.get("jamb_score")
        targets = {u.casefold() for u in beliefs.focus_universities()}
        target_rows = [r for r in rows if r["university"].casefold() in targets]
        short = [r for r in target_rows if r["margin"] < 0]
        tight = [r for r in target_rows if 0 <= r["margin"] < 10]
        # An explicit "which schools?" always deserves ranked options, target or not.
        recommending = board.intent == QueryIntent.school_recommendation
        if not (short or tight or recommending):
            return
        candidates = [
            r for r in rows if r["margin"] >= 0 and r["university"].casefold() not in targets
        ]
        prefs = self._preferences(beliefs, target_rows)
        if prefs.get("type") and prefs["type_explicit"]:
            filtered = [r for r in candidates if r.get("university_type") == prefs["type"]]
            candidates = filtered or candidates
        ranked = sorted(candidates, key=lambda r: self._utility(r, prefs), reverse=True)
        options = []
        for row in ranked[:2]:
            options.append(
                self._option("same_course_elsewhere", row, row["margin"], self._why(row, prefs))
            )
        for target in (short or tight)[:1]:
            related = related_courses(course)
            if related and agent.tools.available:
                at_target = await agent.tools.cutoffs_at(
                    target["university"], beliefs.focus.get("year")
                )
                fits = [
                    r for r in at_target
                    if score - r["score"] >= 0
                    and any(name.casefold() in r["course"].casefold() for name in related)
                    and course.casefold() not in r["course"].casefold()
                ]  # fmt: skip
                if fits:
                    row = max(fits, key=lambda r: r["score"])  # closest to their ambition
                    options.append(
                        self._option(
                            "related_course",
                            row,
                            score - row["score"],
                            "keeps your first-choice school",
                        )
                    )
            keep = self._option("keep_target", target, target["margin"], "your first choice")
            keep.label = (
                f"Keep {course} at {target['university']} and plan to strengthen your chances "
                "(Post-UTME preparation or a stronger UTME next cycle)"
            )
            options.append(keep)
        options = options[:MAX_OPTIONS]
        if not options:
            board.directives.append(
                "No alternative with a verified cut-off at or below the student's score is on file; "
                "be honest about that and suggest checking more institutions' official lists."
            )
            return
        for index, option in enumerate(options):
            option.id = chr(ord("A") + index)
        board.options = options
        beliefs.memory["negotiation"] = {
            "status": "open",
            "goal": {"course": course, "universities": [r["university"] for r in target_rows]},
            "options": [asdict(o) for o in options],
        }
        board.act("negotiation", f"Proposed {len(options)} option(s) for the student to weigh")
        situation = (
            f"{score} is {-short[0]['margin']} below the {short[0]['year']} cut-off at {short[0]['university']}"
            if short
            else f"{score} is only {tight[0]['margin']} above the cut-off at {tight[0]['university']}"
            if tight
            else "the student asked where they can apply"
        )
        board.directives.append(
            f"Negotiate, don't decide: {situation}. Present the options below as trade-offs "
            "(certainty vs. preference), say which looks strongest on the records and why, then ask "
            "which they prefer or what matters most to them (location, federal/state, course)."
        )
        board.suggest(*(f"Option {o.id}" for o in options[:3]), "What matters most when choosing?")

    @staticmethod
    def _preferences(beliefs, target_rows):
        """Infer what the student values from their goal; explicit statements win."""
        explicit = beliefs.get("institution_type")
        return {
            "type": explicit or (target_rows[0].get("university_type") if target_rows else None),
            "type_explicit": bool(explicit),
            "region": {r.get("state") for r in target_rows if r.get("state")},
            "home": beliefs.get("state_of_origin"),
        }

    @staticmethod
    def _utility(row, prefs):
        """Multi-attribute utility: location, institution type, then admission certainty."""
        value = 0.0
        if row.get("state") in prefs["region"]:
            value += 2
        if prefs["home"] and row.get("state") == prefs["home"]:
            value += 2
        if prefs["type"] and row.get("university_type") == prefs["type"]:
            value += 1.5 if prefs["type_explicit"] else 1
        value += {"comfortable": 1, "borderline": 0.3}.get(band(row["margin"]), 0)
        return value + min(row["margin"], 60) / 1000  # tie-break: safer margin

    @staticmethod
    def _why(row, prefs):
        reasons = []
        if row.get("state") in prefs["region"]:
            reasons.append(f"also in {row['state']}")
        elif prefs["home"] and row.get("state") == prefs["home"]:
            reasons.append("in your home state")
        if prefs["type"] and row.get("university_type") == prefs["type"]:
            reasons.append(f"{prefs['type']} like your target")
        reasons.append("comfortable margin" if row["margin"] >= 10 else "tight margin")
        return ", ".join(reasons)

    @staticmethod
    def _option(kind, row, margin, why):
        sign = f"+{margin}" if margin >= 0 else str(margin)
        return Option(
            id="",
            kind=kind,
            label=f"{row['course']} at {row['university']} (cut-off {row['score']}, you're {sign}; {why})",
            university=row["university"],
            course=row["course"],
            cutoff=row["score"],
            margin=margin,
            year=row["year"],
            source_url=row["source_url"],
        )

    def _resolve(self, board: Blackboard, state):
        p = board.percept
        options = state.get("options", [])
        chosen = None
        if p.option_choice is not None and 0 <= p.option_choice < len(options):
            chosen = options[p.option_choice]
        mentioned = {u.casefold() for u in p.entities.get("universities", [])}
        if not chosen and mentioned:
            chosen = next(
                (o for o in options if (o.get("university") or "").casefold() in mentioned), None
            )
        if not chosen and "affirm" in p.acts and len(options) == 1:
            chosen = options[0]
        if chosen:
            board.resolution = chosen
            state.update(status="accepted", accepted=chosen)
            board.beliefs.focus.update(course=chosen["course"], universities=[chosen["university"]])
            board.beliefs.memory["negotiation"] = state
            board.act("negotiation", f"Student chose option {chosen['id']}: {chosen['label']}")
            board.directives.append(
                f"The student chose: {chosen['label']}. Confirm the choice warmly, then give the "
                "next concrete steps for it (requirements check, Post-UTME, CAPS)."
            )
            patch = dict(board.profile_patch)
            saved = board.beliefs.profile_value("universities") or []
            if chosen.get("university") and chosen["university"] not in saved:
                patch["preferred_universities"] = [*saved, chosen["university"]][:6]
            if chosen.get("course") and chosen["course"] != board.beliefs.profile_value("course"):
                patch["preferred_course"] = chosen["course"]
            board.profile_patch = patch
            board.suggest(
                f"What are the requirements for {chosen['course']} at {chosen['university']}?",
                f"When is Post-UTME at {chosen['university']}?",
            )
            return True
        if "deny" in p.acts:
            state["status"] = "revising"
            board.beliefs.memory["negotiation"] = state
            board.act("negotiation", "Student declined the options; asking what matters most")
            board.directives.append(
                "The student declined the options. Don't push. Ask what matters most to them — "
                "location, federal vs state, or staying with their course — so you can refine."
            )
            board.suggest(
                "Federal universities only", "Close to my state", "Keep my course, any school"
            )
            return True
        return False  # new constraints/questions: let the analyst re-plan with them


class Referrer:
    """Points the student to the official platform or institution site for the task."""

    name = "referrer"

    async def contribute(self, board: Blackboard, agent):
        from app.services.local_knowledge import match_university

        names = board.percept.entities.get("universities") or board.beliefs.focus_universities()
        universities = [m for name in names[:3] for m in match_university(name)[:1]]
        board.links = links.recommend(board.percept.text, board.intent, universities)
        if board.links:
            board.directives.append(links.directive(board.links))
            board.act("referral", "Linked " + ", ".join(link["label"] for link in board.links))


class DeadlineWatcher:
    """Proactive: watches verified calendars for windows that matter to this student."""

    name = "deadlines"

    async def contribute(self, board: Blackboard, agent: "AdmissionsAgent"):
        if not agent.tools.available or board.safety:
            return
        board.nudges.extend(await agent.deadline_nudges(board.beliefs))


# ═══════════════════════════════════════════════════════════════════════════
# Manager / orchestrator
# ═══════════════════════════════════════════════════════════════════════════


class AdmissionsAgent:
    specialists = (Counsellor(), MemoryKeeper(), JourneyManager(), EligibilityAnalyst(), Negotiator(), Referrer(), DeadlineWatcher())  # fmt: skip

    def __init__(self, db=None, adm_service=None):
        self.tools = AdmissionsTools(db, adm_service)
        self.profile = None

    # ── sense + believe ─────────────────────────────────────────────────────

    async def deliberate(self, *, user, conv, message, intent, query, recall=None):
        self.profile = getattr(user, "profile", None)
        courses, universities = [], []
        if self.tools.available:
            courses, universities = await self.tools.catalog()
        percept = perceive(message, intent, courses, universities)
        beliefs = Beliefs(getattr(conv, "student_context", None), self.profile)
        self._fill_pending_slot(percept, beliefs)
        intent = self._refine_intent(percept, beliefs, intent)
        percept.intent = intent
        beliefs.observe(percept)
        board = Blackboard(
            percept=percept,
            beliefs=beliefs,
            intent=intent,
            query=self._expand_query(query, percept, beliefs, intent),
            name=self._name(user),
        )
        # ── deliberate + act: specialists collaborate on the blackboard ──
        for specialist in self.specialists:
            try:
                await specialist.contribute(board, self)
            except Exception:
                # One failing specialist must not take the conversation down.
                logger.exception("Agent specialist %s failed", specialist.name)
            if board.safety:
                break
        self._arbitrate(board, recall)
        return board

    @staticmethod
    def _name(user):
        profile = getattr(user, "profile", None)
        display = getattr(profile, "display_name", None) if profile else None
        return (display or "").split(" ")[0] or getattr(user, "username", None)

    @staticmethod
    def _fill_pending_slot(percept, beliefs):
        """Answering the agent's own question ("245") is meaningful in context."""
        pending = beliefs.memory.pop("ask", None)
        bare = re.fullmatch(r"\s*(\d{3})\s*\.?\s*", percept.text)
        if pending == "jamb_score" and bare and 100 <= int(bare[1]) <= 400:
            percept.entities["jamb_score"] = int(bare[1])
            percept.stated.add("jamb_score")
        if pending == "course" and "course" in percept.entities:
            percept.stated.add("course")

    @staticmethod
    def _refine_intent(percept, beliefs, intent):
        negotiating = (beliefs.memory.get("negotiation") or {}).get("status") in (
            "open",
            "revising",
        )
        if percept.affect == "crisis":
            return intent
        if intent != QueryIntent.out_of_scope:
            return intent
        if negotiating and (
            percept.acts & {"select", "affirm", "deny"}
            or {"universities", "institution_type", "state_of_origin"} & percept.entities.keys()
        ):
            return QueryIntent.school_recommendation
        if percept.acts & {"thanks", "goodbye"} and not percept.has_facts():
            return QueryIntent.greeting
        if {"jamb_score", "course", "olevel", "utme_subjects"} & percept.entities.keys():
            return QueryIntent.course_eligibility
        if percept.has_facts() or percept.milestones_done or percept.stage_hint:
            return QueryIntent.general_admission
        return intent

    @staticmethod
    def _expand_query(query, percept, beliefs, intent):
        """Rewrite elliptical follow-ups ("what about the cut-off?") with remembered focus."""
        if (
            intent in (QueryIntent.greeting, QueryIntent.out_of_scope)
            or percept.unknown_institution
        ):
            return query
        extra = []
        course = beliefs.focus_course()
        if (
            course
            and "course" not in percept.entities
            and course.casefold() not in query.casefold()
        ):
            extra.append(course)
        broad = re.search(
            r"\b(universities|schools|which school|other|any school|where can)\b", query, re.I
        )
        if (
            not broad
            and intent != QueryIntent.school_recommendation
            and "universities" not in percept.entities
        ):
            unis = beliefs.focus.get("universities") or []
            if unis and unis[0].casefold() not in query.casefold():
                extra.append(unis[0])
        year = beliefs.focus.get("year")
        if year and str(year) not in query:
            extra.append(str(year))
        return query + ("\n" + " ".join(extra) if extra else "")

    def _arbitrate(self, board: Blackboard, recall):
        """Manager: bound attention, order by urgency, one question at most."""
        order = {"urgent": 0, "soon": 1, "info": 2}
        board.nudges.sort(key=lambda n: order.get(n.severity, 3))
        board.nudges = board.nudges[:MAX_NUDGES]
        if board.options:
            board.ask = None  # the negotiation question supersedes slot filling
        if board.ask:
            board.beliefs.memory["ask"] = board.ask
            question = {
                "jamb_score": "their UTME score",
                "course": "the course they want to study",
            }[board.ask]
            board.directives.append(
                f"Ask exactly one follow-up question at the end: {question}. Explain briefly why it helps."
            )
            board.suggest(
                *(
                    ["I scored 250", "I scored 210"]
                    if board.ask == "jamb_score"
                    else ["Medicine", "Computer Science", "Law"]
                )
            )
        urgent = [n for n in board.nudges if n.severity == "urgent"]
        if urgent and board.intent not in (QueryIntent.greeting, QueryIntent.out_of_scope):
            board.directives.append(
                f"End with one short proactive heads-up: {urgent[0].title} — {urgent[0].detail}"
            )
        course = board.beliefs.focus_course()
        if course and board.intent in (QueryIntent.cutoff_lookup, QueryIntent.subject_combination):
            board.suggest(f"Am I eligible for {course}?")
        if board.observations.get("eligibility_snapshot") and not (
            board.options or board.resolution
        ):
            board.suggest("Show me safer alternatives")
        for nudge in board.nudges:
            if nudge.prompt:
                board.suggest(nudge.prompt)
        board.suggestions = board.suggestions[:MAX_SUGGESTIONS]
        if board.intent == QueryIntent.greeting and not board.safety:
            board.local_reply = self._social_reply(board, recall)

    def _social_reply(self, board: Blackboard, recall):
        p = board.percept
        name = f", {board.name}" if board.name else ""
        nxt = (board.journey or {}).get("next")
        if "goodbye" in p.acts:
            return f"All the best{name}! 🎓 Your notes and progress are saved — just say hi when you're back."
        if "thanks" in p.acts:
            line = f"You're welcome{name}! 😊"
            if nxt:
                line += (
                    f" Whenever you're ready, your next step is **{nxt['title']}** — {nxt['hint']}"
                )
            return line
        lines = Counsellor.greeting(board, recall)
        if board.nudges:
            n = board.nudges[0]
            lines.append(f"**Heads-up:** {n.title} — {n.detail}")
        if nxt:
            lines.append(
                f"Your next step: **{nxt['title']}**. Want help with that, or something else?"
            )
        else:
            lines.append("What would you like to work on today?")
        return "\n\n".join(lines)

    # ── proactive sensing of the environment ───────────────────────────────

    async def deadline_nudges(self, beliefs):
        nudges = []
        now = datetime.now(timezone.utc)
        cycle = await self.tools.current_cycle()
        notes = (getattr(cycle, "cycle_notes", None) or {}) if cycle else {}
        source = safe_web_url(notes.get("source_url"))
        # Registration and exam windows only matter before the student has written UTME.
        if cycle and source and journey.stage_of(beliefs.profile, beliefs) == "pre_jamb":
            for start, end, label in (
                (
                    cycle.registration_start,
                    cycle.registration_end,
                    f"JAMB {cycle.year} registration",
                ),
                (cycle.exam_start, cycle.exam_end, f"UTME {cycle.year}"),
            ):
                nudge = self._window(now, start, end, label, source)
                if nudge:
                    nudges.append(nudge)
        for university in list(beliefs.focus_universities())[:3]:
            for row in await self.tools.post_utme(university):
                nudge = self._window(
                    now,
                    row["start_date"],
                    row["end_date"],
                    f"{row['university']} Post-UTME",
                    row["source_url"],
                )
                if nudge:
                    nudge.prompt = f"Tell me about Post-UTME at {row['university']}"
                    nudges.append(nudge)
        return nudges

    @staticmethod
    def _window(now, start, end, label, source):
        if start and now < start and (start - now).days <= 21:
            days = _days(start - now)
            return Nudge("deadline", "soon" if days <= 7 else "info", f"{label} opens soon",
                         f"opens {_date(start)} ({days} day{'s' if days != 1 else ''} away).", source)  # fmt: skip
        if start and end and start <= now <= end:
            days = _days(end - now)
            severity = "urgent" if days <= 3 else "soon" if days <= 10 else "info"
            when = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
            return Nudge(
                "deadline", severity, f"{label} is open", f"closes {when} ({_date(end)}).", source
            )
        return None

    # ── outputs ─────────────────────────────────────────────────────────────

    @staticmethod
    def brief(board: Blackboard):
        """The agent's analysis for the language model, compact and provenance-tagged."""
        lines = ["\n\n### Agent analysis (from verified tools and memory — follow it)"]
        known = board.beliefs.known()
        if known:
            lines.append("Known about the student: " + "; ".join(
                f"{r['label']}: {r['value']} ({r['source']})" for r in known))  # fmt: skip
        rows = board.observations.get("eligibility_snapshot")
        if rows:
            lines.append("Score vs official cut-offs on file (margin = score − cut-off):")
            for r in rows[:8]:
                lines.append(
                    f"- {r['university']} — {r['course']} {r['year']}: cut-off {r['score']}, margin "
                    f"{r['margin']:+d} ({r['band'].replace('_', ' ')}). Source: {r['source_url']}"
                )
        check = board.observations.get("eligibility_check")
        if check:
            lines.append(f"Full requirements check: {check['status']}.")
            for a in check["assessments"][:6]:
                gaps = a["missing_subjects"] + a["failed_olevel"]
                lines.append(
                    f"- {a['university']}: {a['status']}"
                    + (f" (gaps: {'; '.join(gaps)})" if gaps else "")
                )
        if board.options:
            lines.append("Options to negotiate with the student:")
            lines += [f"- Option {o.id}: {o.label}" for o in board.options]
        if board.nudges:
            lines.append(
                "Verified calendar alerts: "
                + "; ".join(f"{n.title}, {n.detail}" for n in board.nudges)
            )
        nxt = (board.journey or {}).get("next")
        if nxt:
            lines.append(f"Student's next journey step: {nxt['title']} ({nxt['hint']})")
        if board.profile_patch:
            lines.append(
                "The app will offer to save the new details to their profile; don't ask them to do it."
            )
        if board.directives:
            lines.append("Directives for this reply:")
            lines += [f"- {d}" for d in board.directives]
        return "\n".join(lines) if len(lines) > 1 else ""

    @staticmethod
    def opener(board: Blackboard):
        """An empathetic first line for local answers when the student is struggling."""
        return {
            "anxious": "Take a breath — let's look at this calmly, step by step.",
            "discouraged": "I hear you. A setback on one list isn't the end of the road; "
            "let's look at real options.",
            "frustrated": "Let's make this simple.",
        }.get(board.percept.affect, "")

    @staticmethod
    def addendum(board: Blackboard):
        """Deterministic rendering of the agent's work for local (no-model) answers."""
        parts = []
        chosen = board.resolution
        if chosen:
            parts.append(
                f"Great choice{', ' + board.name if board.name else ''}! 🎯 **{chosen['course']} at "
                f"{chosen['university']}** — {chosen['year']} cut-off {chosen['cutoff']}"
                + (
                    f", and you're **{chosen['margin']:+d}**."
                    if chosen.get("margin") is not None
                    else "."
                )
                + f" [Official source](<{chosen['source_url']}>)"
            )
            parts.append(
                "**Next steps:**\n\n1. Confirm the UTME subjects and O'Level grades for this course.\n"
                "2. Watch for the institution's Post-UTME notice and register on its official portal.\n"
                "3. Make sure your O'Level results are uploaded to JAMB, then monitor CAPS."
            )
        rows = board.observations.get("eligibility_snapshot") if not chosen else None
        score = board.beliefs.get("jamb_score")
        if rows:
            table = ["| Institution | Cut-off | Your margin | Status |", "|---|---:|---:|---|"]
            status = {
                "comfortable": "✅ Comfortable",
                "borderline": "🟡 Borderline",
                "just_short": "🟠 Just short",
                "out_of_reach": "🔴 Out of reach",
            }
            for r in rows[:8]:
                table.append(
                    f"| [{r['university']}](<{r['source_url']}>) | {r['score']} | "
                    f"{r['margin']:+d} | {status[band(r['margin'])]} |"
                )
            parts.append(
                f"**How {score} compares for {rows[0]['course']} ({rows[0]['year']})** — official cut-offs on file:\n\n"
                + "\n".join(table)
            )
        if board.options:
            parts.append(
                "**Options worth weighing:**\n\n"
                + "\n".join(f"- **{o.id}.** {o.label}" for o in board.options)
                + "\n\nWhich of these feels right — or what matters most to you?"
            )
        if board.ask == "jamb_score":
            parts.append(
                "What was your UTME score? With it I can compare you against every cut-off on file."
            )
        elif board.ask == "course":
            parts.append(
                "Which course do you want to study? I'll check it against the records on file."
            )
        for n in board.nudges[:1]:
            parts.append(f"**Heads-up:** {n.title} — {n.detail}")
        return "\n\n".join(parts)

    @staticmethod
    def event(board: Blackboard):
        """Compact agent state for the client (live SSE event and saved message)."""
        j = board.journey or {}
        return {
            "intent": board.intent.value,
            "affect": board.percept.affect,
            "actions": board.actions,
            "known": board.beliefs.known(),
            "nudges": [asdict(n) for n in board.nudges],
            "options": [asdict(o) for o in board.options],
            "suggestions": board.suggestions,
            "links": board.links,
            "profile_patch": board.profile_patch,
            "ask": board.ask,
            "journey": {
                "updated": board.journey_updates,
                "next": j.get("next"),
                "completed": j.get("completed"),
                "total": j.get("total"),
            }
            if j
            else None,
        }

    # ── reflect ─────────────────────────────────────────────────────────────

    @staticmethod
    def reflect(board: Blackboard, conv, profile):
        """Persist working memory and a rolling summary; count the student's query."""
        conv.student_context = board.beliefs.snapshot()
        summary = board.beliefs.summary()
        if summary:
            conv.summary = summary
        if profile is not None and board.intent not in (
            QueryIntent.greeting,
            QueryIntent.out_of_scope,
        ):
            profile.total_queries = (profile.total_queries or 0) + 1
