"""Belief state: per-conversation working memory layered over the long-term profile.

Working memory lives in ``Conversation.student_context`` (JSONB). The profile is the
student's long-term memory and is only changed with their approval (the agent
*proposes* a patch). Facts carry provenance so answers can say where they came from.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

MEMORY_VERSION = 1
TOPIC_LIMIT = 12

# Working-memory key -> profile attribute (long-term memory).
PROFILE_FIELDS = {
    "jamb_score": "jamb_score",
    "course": "preferred_course",
    "universities": "preferred_universities",
    "olevel": "olevel_results",
    "state_of_origin": "state_of_origin",
    "stage": "academic_stage",
}
LABELS = {
    "jamb_score": "UTME score",
    "course": "course",
    "universities": "target institutions",
    "olevel": "O'Level grades",
    "utme_subjects": "UTME subjects",
    "state_of_origin": "state of origin",
    "institution_type": "institution preference",
    "stage": "admissions stage",
    "year": "admission year",
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Beliefs:
    """What the agent currently believes about the student and this conversation."""

    def __init__(self, working: dict | None, profile=None):
        working = deepcopy(working or {})
        if working.get("v") != MEMORY_VERSION:
            working = {"v": MEMORY_VERSION}
        self.memory = working
        self.memory.setdefault("facts", {})
        self.memory.setdefault("focus", {})
        self.memory.setdefault("topics", [])
        self.memory.setdefault("turn", 0)
        self.profile = profile
        self.changes: list[str] = []
        self.conflicts: list[dict] = []

    # ── reading ──────────────────────────────────────────────────────────────

    @property
    def facts(self):
        return self.memory["facts"]

    @property
    def focus(self):
        return self.memory["focus"]

    def profile_value(self, key):
        attr = PROFILE_FIELDS.get(key)
        if not attr or self.profile is None:
            return None
        value = getattr(self.profile, attr, None)
        if key == "stage" and value == "pre_jamb":
            return None  # the server default carries no information
        return value or None

    def get(self, key):
        """Conversation facts outrank the profile: the current request is more recent."""
        if key in self.facts:
            return self.facts[key]["value"]
        return self.profile_value(key)

    def source(self, key):
        if key in self.facts:
            return "said in this chat"
        if self.profile_value(key) is not None:
            return "from your profile"
        return None

    def focus_course(self):
        return self.focus.get("course") or self.get("course")

    def focus_universities(self):
        return self.focus.get("universities") or self.get("universities") or []

    # ── belief revision ─────────────────────────────────────────────────────

    def observe(self, percept):
        """Fold a percept into memory; record changes and conflicts with the profile."""
        self.memory["turn"] += 1
        turn = self.memory["turn"]
        entities = percept.entities
        # Focus = what we're talking about now (decays after three silent turns).
        for key in ("course", "universities", "year"):
            if key in entities:
                self.focus[key] = entities[key]
                self.focus[f"{key}_turn"] = turn
            elif turn - self.focus.get(f"{key}_turn", turn) > 3:
                self.focus.pop(key, None)
                self.focus.pop(f"{key}_turn", None)
        # Facts = what the student asserted about themselves.
        stated = dict(entities)
        if "course" in stated and "course" not in percept.stated:
            stated.pop("course")
        if "universities" in stated and "universities" not in percept.stated:
            stated.pop("universities")
        stated.pop("year", None)
        if percept.stage_hint:
            stated["stage"] = percept.stage_hint
        for key, value in stated.items():
            previous = self.get(key)
            if key == "olevel" and isinstance(previous, dict):
                value = {**previous, **value}
            if key == "universities" and isinstance(previous, list):
                value = list(dict.fromkeys([*value, *previous]))[:6]
            if previous != value:
                self.changes.append(key)
                profile_value = self.profile_value(key)
                if profile_value not in (None, [], {}) and profile_value != value:
                    self.conflicts.append({"field": key, "profile": profile_value, "chat": value})
            self.facts[key] = {"value": value, "turn": turn, "at": _now()}
        if percept.intent.value not in ("greeting", "out_of_scope"):
            topics = self.memory["topics"]
            label = percept.intent.value
            if entities.get("course"):
                label += f":{entities['course']}"
            if not topics or topics[-1] != label:
                topics.append(label)
            del topics[:-TOPIC_LIMIT]
        self.memory["last_affect"] = percept.affect

    # ── long-term memory proposals ──────────────────────────────────────────

    def profile_patch(self):
        """Fields the student stated that differ from their saved profile."""
        patch = {}
        for key in self.changes:
            attr = PROFILE_FIELDS.get(key)
            if not attr:
                continue
            value = self.facts[key]["value"]
            saved = self.profile_value(key)
            if key == "universities":
                value = list(dict.fromkeys([*(saved or []), *value]))[:6]
            if key == "olevel":
                value = {**(saved or {}), **value}
            if value != saved:
                patch[attr] = value
        return patch

    # ── serialisation ───────────────────────────────────────────────────────

    def known(self):
        """Human-readable snapshot with provenance, for prompts and the UI."""
        rows = []
        for key in (
            "jamb_score",
            "course",
            "universities",
            "utme_subjects",
            "olevel",
            "state_of_origin",
            "institution_type",
            "stage",
        ):
            value = self.get(key)
            if value in (None, [], {}):
                continue
            if isinstance(value, list):
                shown = ", ".join(value)
            elif isinstance(value, dict):
                shown = ", ".join(f"{s.title()} {g}" for s, g in value.items())
            else:
                shown = str(value).replace("_", " ")
            rows.append(
                {"key": key, "label": LABELS[key], "value": shown, "source": self.source(key)}
            )
        return rows

    def summary(self):
        """Deterministic rolling summary: survives the history window without an LLM call."""
        parts = []
        known = self.known()
        if known:
            parts.append(
                "Student facts: "
                + "; ".join(f"{r['label']} {r['value']} ({r['source']})" for r in known)
            )
        if self.memory["topics"]:
            topics = [t.replace("_", " ").replace(":", " – ") for t in self.memory["topics"][-6:]]
            parts.append("Topics so far: " + ", ".join(topics))
        negotiation = self.memory.get("negotiation")
        if negotiation:
            status = negotiation.get("status")
            chosen = negotiation.get("accepted")
            if status == "accepted" and chosen:
                parts.append(f"Agreed plan: {chosen['label']}")
            elif status == "open":
                parts.append("Open decision: the student is weighing alternative options.")
        return ". ".join(parts)[:4900] or None

    def snapshot(self):
        return deepcopy(self.memory)
