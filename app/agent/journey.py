"""Admissions journey: a managed checklist of process steps per stage.

Steps are general JAMB/CAPS procedure, linked to official guidance. They hold no
dates, fees or scores; those come from year-specific records when available.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.services.local_knowledge import CAPS, FAQ, SUPPORT

STAGES = ("pre_jamb", "post_jamb", "post_utme", "admitted")


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    stage: str
    hint: str
    guide_url: str
    prompt: str  # what the student can ask the agent about this step


STEPS = [
    Step("choose_course", "Choose your course and institutions", "pre_jamb",
         "Pick a course and 2–4 institutions you'd be happy to attend.", FAQ,
         "Help me choose a course and institutions that fit me"),
    Step("check_requirements", "Confirm subject & O'Level requirements", "pre_jamb",
         "Check the UTME subjects and O'Level grades for your course on IBASS.",
         "https://eligibility.jamb.gov.ng/", "What subjects and O'Level grades do I need for my course?"),
    Step("get_profile_code", "Get your JAMB profile code", "pre_jamb",
         "Create a profile code with your NIN from your own phone number.", SUPPORT,
         "How do I get my JAMB profile code?"),
    Step("register_utme", "Register for UTME", "pre_jamb",
         "Register at a JAMB-accredited CBT centre with your profile code and e-PIN.", FAQ,
         "How do I register for JAMB?"),
    Step("print_exam_slip", "Print your exam slip", "pre_jamb",
         "Print the slip showing your exam date, time and centre.", FAQ,
         "When and how do I print my JAMB exam slip?"),
    Step("write_utme", "Write the UTME", "pre_jamb",
         "Arrive early with your slip; follow the centre's instructions.", FAQ,
         "Give me tips for the day of my UTME"),
    Step("check_result", "Check your UTME result", "post_jamb",
         "Check your result through JAMB's official channels.", FAQ,
         "How do I check my JAMB result?"),
    Step("upload_olevel", "Upload your O'Level results", "post_jamb",
         "Make sure your WAEC/NECO results are uploaded to JAMB — CAPS needs them.", SUPPORT,
         "How do I upload my O'Level results to JAMB?"),
    Step("post_utme_register", "Register for Post-UTME screening", "post_jamb",
         "Apply on your institution's portal when its screening notice is published.", CAPS,
         "When is Post-UTME registration for my target school?"),
    Step("write_post_utme", "Complete Post-UTME screening", "post_utme",
         "Attend the screening and keep your documents ready.", CAPS,
         "How is the Post-UTME aggregate usually calculated?"),
    Step("monitor_caps", "Monitor your CAPS status", "post_utme",
         "Check JAMB CAPS regularly for recommendations and offers.", CAPS,
         "How do I check my admission status on CAPS?"),
    Step("accept_caps", "Accept your offer on CAPS", "admitted",
         "Accept (or reject) the provisional offer on CAPS.", CAPS,
         "How do I accept my admission on CAPS?"),
    Step("pay_acceptance", "Pay acceptance fee & complete clearance", "admitted",
         "Use only your institution's official portal for payments.", CAPS,
         "What happens after I accept admission on CAPS?"),
]  # fmt: skip
STEP_BY_ID = {s.id: s for s in STEPS}


def stage_of(profile, beliefs=None):
    stage = beliefs.get("stage") if beliefs else None
    if not stage and profile is not None:
        stage = getattr(profile, "academic_stage", None)
    if stage == "direct_entry":
        return "post_jamb"
    return stage if stage in STAGES else "pre_jamb"


def journey_state(profile):
    prefs = getattr(profile, "preferences", None) or {}
    state = prefs.get("journey")
    return dict(state) if isinstance(state, dict) else {}


def mark(profile, step_ids, done=True, by="chat"):
    """Persist step completion in ``profile.preferences`` (reassigned so JSONB updates)."""
    if profile is None:
        return []
    state = journey_state(profile)
    changed = []
    for step_id in step_ids:
        if step_id not in STEP_BY_ID:
            continue
        current = state.get(step_id, {}).get("done", False)
        if current == done:
            continue
        state[step_id] = {
            "done": done,
            "by": by,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        changed.append(step_id)
    if changed:
        prefs = dict(getattr(profile, "preferences", None) or {})
        prefs["journey"] = state
        profile.preferences = prefs
    return changed


def overview(profile, beliefs=None):
    stage = stage_of(profile, beliefs)
    state = journey_state(profile)
    reached = STAGES.index(stage)
    steps = []
    for step in STEPS:
        record = state.get(step.id, {})
        # Earlier-stage steps are implicitly complete once a later stage is reached,
        # unless the student explicitly unticked them.
        implied = STAGES.index(step.stage) < reached and "done" not in record
        steps.append(
            {
                "id": step.id,
                "title": step.title,
                "stage": step.stage,
                "hint": step.hint,
                "guide_url": step.guide_url,
                "prompt": step.prompt,
                "done": bool(record.get("done")) or implied,
                "by": record.get("by") or ("stage" if implied else None),
            }
        )
    done = sum(s["done"] for s in steps)
    upcoming = next(
        (s for s in steps if not s["done"] and STAGES.index(s["stage"]) >= reached), None
    )
    upcoming = upcoming or next((s for s in steps if not s["done"]), None)
    return {
        "stage": stage,
        "steps": steps,
        "completed": done,
        "total": len(steps),
        "next": upcoming,
    }
