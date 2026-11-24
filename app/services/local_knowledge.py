"""Reviewed baseline knowledge and deterministic answers requiring no external AI.

Only institution identities and general processes live here. Scores, fees, dates,
requirements and eligibility must come from year-specific administrator records.
"""

import csv
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.core.gemini_client import safe_web_url
from app.models.chat import QueryIntent

REVIEWED_ON = "2026-11-13"
NUC_FEDERAL = "https://www.nuc.edu.ng/nigerian-univerisities/federal-univeristies/"
NUC_STATE = "https://www.nuc.edu.ng/nigerian-univerisities/state-univerisity/"
FAQ = "https://jamb.gov.ng/FAQ"
CAPS = "https://jamb.gov.ng/caps"
SUPPORT = "https://support.jamb.gov.ng/faqs"
NO_EVIDENCE = (
    "I don't have verified local evidence for that question yet. Tell me the university, course "
    "and admission year, or check JAMB's official guidance: https://www.jamb.gov.ng/."
)

NUC_PRIVATE = "https://www.nuc.edu.ng/nigerian-univerisities/private-univeristies/"
NUC_SOURCES = {"federal": NUC_FEDERAL, "state": NUC_STATE, "private": NUC_PRIVATE}
DIRECTORY_REVIEWED_ON = "2026-11-24"
DIRECTORY_FILE = Path(__file__).resolve().parents[1] / "data" / "nuc_universities.csv"
# Short names that several institutions share are never used alone as aliases.
AMBIGUOUS_ALIASES = {"kogi state university", "northwest university", "federal university"}


def _load_directory():
    """All NUC-listed universities: identity only (no accreditation or admission status).

    The NUC tables have no state column; states come from the institution's location
    (``state_basis``). Obvious NUC typos are corrected in ``name``; ``nuc_name`` keeps
    the original spelling.
    """
    with DIRECTORY_FILE.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    first = Counter(normalize(r["name"].split(",")[0]) for r in rows)
    directory = []
    for r in rows:
        parts = [p.strip() for p in r["name"].split(",")]
        aliases = [a.strip() for a in r["aliases"].split(";") if a.strip()]
        if len(parts) > 1 and first[normalize(parts[0])] == 1:
            aliases.append(parts[0])
        if len(parts) > 2:
            aliases.append(f"{parts[0]} {parts[1]}")
        if r["nuc_name"]:
            aliases.append(r["nuc_name"])
        aliases = [a for a in dict.fromkeys(aliases) if normalize(a) not in AMBIGUOUS_ALIASES]
        directory.append(
            (r["name"], r["abbreviation"] or None, r["state"], r["type"], r["website"] or None,
             aliases, int(r["established"]) if r["established"] else None, r["nuc_name"] or None)
        )  # fmt: skip
    return directory


@dataclass(frozen=True)
class Article:
    title: str
    pattern: str
    text: str
    source_url: str


ARTICLES = [
    Article(
        "Check course and subject requirements",
        r"subject|combination|o.?level|waec|neco|ibass|brochure|direct entry|eligible|eligibility",
        "Use JAMB's IBASS Eligibility Checker to check the chosen institution and programme's published requirements. Confirm the admission cycle and any special conditions before applying. https://eligibility.jamb.gov.ng/",
        FAQ,
    ),
    Article(
        "JAMB registration",
        r"register|registration|cbt|e.?pin",
        "Complete registration at a JAMB-approved CBT centre. Take your profile code and e-PIN, check your personal details before biometric confirmation, and retain your registration slip. Obtain the current year's fees and dates from JAMB.",
        FAQ,
    ),
    Article(
        "Duplicate registrations",
        r"register.*twice|duplicate registration|more than once",
        "Do not register twice to correct an error. Request a correction through JAMB instead.",
        FAQ,
    ),
    Article(
        "CAPS admission status",
        r"caps|admission status|accept.*admission|admission.*offer",
        "CAPS manages the admission process and lets candidates confirm provisional offers. Check your own status through JAMB e-Facility. Ensure your O-Level results are uploaded; an institution's recommendation still requires JAMB processing. An offer is not guaranteed by meeting a score alone.",
        CAPS,
    ),
    Article(
        "O-Level upload and pending admission",
        r"upload|awaiting|not admitted|admission in progress|transfer approval",
        "For an offer that has not appeared on CAPS, check that your O-Level results are uploaded and verified, that the institution has recommended you to JAMB, and whether a transfer offer awaits your approval.",
        SUPPORT,
    ),
    Article(
        "Profile code",
        r"profile code|nin|55019|66019",
        "JAMB's support guidance says to send NIN, a space, and your NIN to 55019 or 66019 using your own registered phone number. Check the official instructions before sending personal details; never post your NIN or profile code in this chat.",
        SUPPORT,
    ),
]


def normalize(text):
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


UNIVERSITIES = _load_directory()
_KEYS = [
    [k for k in dict.fromkeys(normalize(a) for a in [name, abbr or "", *aliases]) if k]
    for name, abbr, _s, _k, _w, aliases, _e, _n in UNIVERSITIES
]


def _directory_entry(row):
    name, abbr, state, kind, website, aliases, established, nuc_name = row
    return dict(
        name=name,
        abbreviation=abbr,
        state=state,
        university_type=kind,
        website_url=website,
        established=established,
        source_url=NUC_SOURCES[kind],
        reviewed_on=DIRECTORY_REVIEWED_ON,
    )


def match_university(text):
    """Recognise named universities; a longer name wins over a name it contains."""
    padded = f" {normalize(text)} "
    spans = []
    for index, keys in enumerate(_KEYS):
        for key in keys:
            start = padded.find(f" {key} ")
            if start >= 0:
                spans.append((start, start + len(key) + 1, index))
    kept = [
        s for s in spans
        if not any(o[0] <= s[0] and s[1] <= o[1] and (o[1] - o[0]) > (s[1] - s[0]) for o in spans)
    ]  # fmt: skip
    return [_directory_entry(UNIVERSITIES[i]) for i in dict.fromkeys(s[2] for s in kept)]


STATE_NAMES = {
    normalize(s): s
    for s in (
        "Abia|Adamawa|Akwa Ibom|Anambra|Bauchi|Bayelsa|Benue|Borno|Cross River|Delta|Ebonyi|Edo|"
        "Ekiti|Enugu|Gombe|Imo|Jigawa|Kaduna|Kano|Katsina|Kebbi|Kogi|Kwara|Lagos|Nasarawa|Niger|"
        "Ogun|Ondo|Osun|Oyo|Plateau|Rivers|Sokoto|Taraba|Yobe|Zamfara|FCT"
    ).split("|")
}
STATE_NAMES.update({"abuja": "FCT", "federal capital territory": "FCT"})
DIRECTORY_CUE = re.compile(
    r"\b(list|which|what|how many|number of|all( the)?|show|name|universities in|"
    r"universities (are )?(there|in))\b",
    re.I,
)


def directory_query(text):
    """Answer directory questions ("private universities in Ogun", "how many federal…")."""
    if not re.search(r"universit", text, re.I) or not DIRECTORY_CUE.search(text):
        return None
    if match_university(text):
        return None  # a specific institution was named
    kind = None
    for match in re.finditer(r"\b(federal|state|private)(?:[- ]owned)?\s+universit", text, re.I):
        before = normalize(text[: match.start()]).split()
        # "Lagos State University" names an institution, not a type.
        if (
            match.group(1).lower() == "state"
            and before
            and (before[-1] in STATE_NAMES or " ".join(before[-2:]) in STATE_NAMES)
        ):
            continue
        kind = match.group(1).lower()
    padded = f" {normalize(text)} "
    state = None
    for key, value in STATE_NAMES.items():
        # "Niger" alone is usually the river or the country; require "Niger State"/"in Niger".
        if key == "niger" and not re.search(r"\bniger state\b|\bin niger\b", text, re.I):
            continue
        if f" {key} " in padded:
            state = value
            break
    rows = [
        _directory_entry(r)
        for r in UNIVERSITIES
        if (not kind or r[3] == kind) and (not state or r[2] == state)
    ]
    return {
        "filters": {"type": kind, "state": state},
        "count": len(rows),
        "universities": [
            {k: e[k] for k in ("name", "university_type", "state", "website_url", "established")}
            for e in rows
        ],
        "source_url": NUC_SOURCES[kind] if kind else NUC_FEDERAL,
        "sources": sorted({NUC_SOURCES[r["university_type"]] for r in rows}),
        "reviewed_on": DIRECTORY_REVIEWED_ON,
        "note": "NUC-approved universities (identity only; not programme accreditation or "
        "admission status). States are assigned from each institution's location.",
    }


def baseline_context(query, intent):
    ctx = {}
    universities = match_university(query)
    if universities:
        ctx["university_directory"] = universities[:5]
    listing = directory_query(query)
    if listing:
        ctx["university_listing"] = listing
    if intent in (QueryIntent.greeting, QueryIntent.out_of_scope):
        return ctx
    # General guides cannot answer numeric, year-specific requirements.
    guides = [a for a in ARTICLES if re.search(a.pattern, query, re.I)]
    if not guides and intent == QueryIntent.general_admission and not universities:
        guides = [ARTICLES[0], ARTICLES[3]]
    if guides:
        ctx["local_guides"] = [
            dict(title=a.title, text=a.text, source_url=a.source_url, reviewed_on=REVIEWED_ON)
            for a in guides[:3]
        ]
    return ctx


def place(state):
    return {"FCT": "FCT (Abuja)", "Unspecified": "state not listed"}.get(state, f"{state} State")


def describe_university(row):
    abbr = f" ({row['abbreviation']})" if row.get("abbreviation") else ""
    kind = row["university_type"]
    article = "an" if kind[0] in "aeiou" else "a"
    since = f", established {row['established']}" if row.get("established") else ""
    site = f" Website: {row['website_url']}." if row.get("website_url") else ""
    return (
        f"{row['name']}{abbr} is {article} {kind} university in {place(row['state'])}{since}.{site}"
    )


def source_link(url):
    url = safe_web_url(url)
    if not url:
        return ""
    return (
        "[Official source](<" + url.replace("<", "%3C").replace(">", "%3E").replace("\n", "") + ">)"
    )


def render_local_answer(intent, query, context, sources):
    """Render records verbatim with provenance; never infer eligibility or invent facts."""
    lines = []
    for row in context.get("cutoffs", []):
        if row.get("is_official") and safe_web_url(row.get("source_url")):
            lines.append(
                f"- {row['university']} — {row['course']}: **{row['score']}** for **{row['year']}**. {source_link(row['source_url'])}"
            )
    for row in context.get("subject_combinations", []):
        if safe_web_url(row.get("source_url")) and row.get("requirements_year"):
            subjects = ", ".join(row.get("jamb_subjects", [])) or "not recorded"
            grades = (
                ", ".join(f"{s}: {g}" for s, g in row.get("olevel_requirements", {}).items())
                or "not recorded"
            )
            lines.append(
                f"- {row['course']} at {row['university']} ({row['requirements_year']}): UTME subjects: {subjects}. O-Level minimum grades: {grades}. {source_link(row['source_url'])}"
            )
            if row.get("notes"):
                lines.append(f"Published notes: {row['notes']}")
    for row in context.get("post_utme", []):
        if safe_web_url(row.get("source_url")):
            lines.append(
                f"- {row['university']} ({row['year']}): screening starts {row['start_date']} and ends {row['end_date']}; fee NGN {row['fee_ngn']}. Published eligibility: {row['eligibility']}. {source_link(row['source_url'])}"
            )
    registration = context.get("registration", {})
    if registration and safe_web_url(registration.get("source_url")):
        dates = "; ".join(
            f"{k.replace('_', ' ')}: {registration[k]}"
            for k in ("registration_start", "registration_end", "exam_start", "exam_end")
            if registration.get(k)
        )
        lines.append(
            f"JAMB {registration['year']}: {dates or 'dates not recorded'}. {source_link(registration['source_url'])}"
        )
    has_specific_records = bool(lines)
    for row in context.get("university_directory", []):
        lines.append(describe_university(row) + f" {source_link(row['source_url'])}")
    listing = context.get("university_listing")
    if listing:
        f = listing["filters"]
        kind = f"{f['type']} " if f["type"] else ""
        where = (
            ("the FCT (Abuja)" if f["state"] == "FCT" else f"{f['state']} State")
            if f["state"]
            else "Nigeria"
        )
        lines.append(
            f"There are **{listing['count']} NUC-approved {kind}universities** in {where}:"
        )
        shown = listing["universities"][:60]
        head = (
            ["University"]
            + (["Type"] if not f["type"] else [])
            + (["State"] if not f["state"] else [])
        )
        head.append("Est.")
        rows = ["| " + " | ".join(head) + " |", "|" + "---|" * (len(head) - 1) + "---:|"]
        for u in shown:
            name = f"[{u['name']}](<{u['website_url']}>)" if u.get("website_url") else u["name"]
            cols = [name]
            cols += [u["university_type"].title()] if not f["type"] else []
            cols += [place(u["state"])] if not f["state"] else []
            cols.append(str(u.get("established") or "—"))
            rows.append("| " + " | ".join(c.replace("|", "/") for c in cols) + " |")
        lines.append("\n".join(rows))
        if listing["count"] > len(shown):
            lines.append(
                f"_Showing {len(shown)} of {listing['count']}. Narrow it by type (federal, state, "
                "private) or by state._"
            )
        lines.append(" ".join(source_link(url) for url in listing["sources"]))
    for guide in context.get("local_guides", []):
        lines.append(f"{guide['text']} {source_link(guide['source_url'])}")
    for chunk in sources[:3]:
        if safe_web_url(chunk.source_url):
            year = (
                str(chunk.cycle_year)
                if chunk.cycle_year
                else "general guidance; no admission year specified"
            )
            lines.append(
                f"From {chunk.doc_title} ({year}):\n\n> {chunk.snippet[:1800].replace(chr(10), chr(10) + '> ')}\n\n{source_link(chunk.source_url)}"
            )
    if not has_specific_records and intent in (
        QueryIntent.cutoff_lookup,
        QueryIntent.post_utme_info,
        QueryIntent.subject_combination,
        QueryIntent.olevel_requirement,
        QueryIntent.course_eligibility,
        QueryIntent.jamb_process,
    ):
        years = re.findall(r"\b20\d{2}\b", query)
        cycle = f" for {years[-1]}" if years else " for your admission cycle"
        lines.insert(
            0,
            f"I don't have a verified structured record for this request{cycle}. I cannot confirm a cut-off, fee, deadline, subject requirement or eligibility without the matching official publication.",
        )
        next_steps = {
            QueryIntent.cutoff_lookup: "Check the institution's official admissions page for the course and admission year; share the publication if you would like help interpreting it.",
            QueryIntent.post_utme_info: "Check the institution's official admissions page for the relevant cycle's screening notice.",
            QueryIntent.subject_combination: "Check JAMB's IBASS eligibility checker and the institution's published requirements for your course and admission year.",
            QueryIntent.olevel_requirement: "Check JAMB's IBASS eligibility checker and the institution's published requirements for your course and admission year.",
            QueryIntent.course_eligibility: "Check the published requirements for your chosen course, institution and admission year before drawing a conclusion.",
            QueryIntent.jamb_process: "Check JAMB's official guidance for the relevant admission cycle.",
        }
        lines.append(next_steps[intent])
    if has_specific_records:
        lines.append(
            "These are the recorded requirements for the stated year. Meeting them does not guarantee admission; confirm any screening, sitting limits and special conditions with the institution."
        )
    elif intent == QueryIntent.school_recommendation and lines:
        lines.append(
            "Next, compare your preferred courses against each institution's published requirements for your admission year."
        )
    elif intent == QueryIntent.general_admission and lines:
        lines.append(
            "Next, check the official JAMB or institution guidance for the step and admission cycle that apply to you."
        )
    if not lines:
        lines.append(NO_EVIDENCE)
    return (
        "**Local knowledge base** — using saved evidence; no live web verification was performed.\n\n"
        + "\n\n".join(lines)
    )
