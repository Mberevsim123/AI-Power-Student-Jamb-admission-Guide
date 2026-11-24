"""Post-generation grounding checks for the claims that matter most in admissions.

Language models can produce plausible but unsupported cut-off marks, fees, deadlines
and institution names. These checks compare an answer with the evidence the agent
actually supplied (records, documents, tool results, the student's own words):

* figures in prose (scores, aggregates, percentages, fees) — disclosed if unsupported;
* tables whose figure columns are unsupported — removed and replaced by a notice,
  because a table presents numbers as official data;
* deadlines and timing ("mid-July", "March–April") — disclosed if not on file;
* abbreviation/name pairs such as "ABU (University of Abuja)" — corrected from the
  NUC directory, whatever the source.
"""

from __future__ import annotations

import json
import re

from app.agent.formatter import cells, find_tables
from app.services.local_knowledge import UNIVERSITIES, normalize

SCORE_CLAIM = [
    re.compile(
        r"(?:cut.?off|score|mark|points?|aggregate)[^.\n\d]{0,40}?\b([1-3]\d{2}|400)\b(?![\d,])",
        re.I,
    ),
    re.compile(r"\b([1-3]\d{2}|400)\b(?![\d,])[^.\n\d]{0,20}?(?:cut.?off|marks?|points)\b", re.I),
    re.compile(
        r"(?:cut.?off|score|aggregate|post.?utme)[^.\n]{0,40}?\b(\d{1,3})\s?%",
        re.I,
    ),
]
MONEY_CLAIM = [
    re.compile(r"(?:₦|\bNGN|\bN)\s?(\d{1,3}(?:,\d{3})+|\d{3,})(?:\.\d+)?", re.I),
    re.compile(r"\b(\d{1,3}(?:,\d{3})+|\d{3,})(?:\.\d+)?\s?naira\b", re.I),
]
MONTH = r"(january|february|march|april|may|june|july|august|september|october|november|december)"
DASH = r"[\s\-\u2010\u2011\u2013\u2014]"
# Each pattern captures only the timing phrase itself, so reports stay readable.
TIMING_CLAIM = [
    re.compile(
        rf"\b((?:early|mid|late){DASH}+{MONTH}(?:{DASH}*(?:–|-|to){DASH}*{MONTH})?)\b", re.I
    ),
    re.compile(rf"\b({MONTH}{DASH}*(?:–|-|to){DASH}*{MONTH})\b", re.I),
    re.compile(
        rf"\b(?:deadline|closes?|opens?|held|usually|typically|starts?|ends?|by|in)\b"
        rf"[^.\n]{{0,30}}?\b({MONTH})\b",
        re.I,
    ),
]
FIGURE_HEADER = re.compile(
    r"cut.?off|score|aggregate|mark|fee|amount|cost|price|naira|₦|date|deadline|closing|opening|%",
    re.I,
)
ABBR_NAME = re.compile(r"\b([A-Z][A-Z-]{1,9})\s*\(([^()\n]{4,90})\)")
NAME_ABBR = re.compile(r"((?:[A-Z][\w'’.&-]*,?\s+){1,9}[A-Z][\w'’.&-]*)\s*\(([A-Z][A-Z-]{1,9})\)")
DIRECTORY = {
    row[1].upper(): (row[0], {normalize(a) for a in [row[0], *row[5]] if len(normalize(a)) > 6})
    for row in UNIVERSITIES
    if row[1]
}


def _numbers(text):
    return {n.replace(",", "").lstrip("0") or "0" for n in re.findall(r"\d[\d,]*", text)}


MONTH_NAMES = MONTH.strip("()").split("|")


def _corpus(evidence):
    raw = json.dumps(evidence, default=str, ensure_ascii=False)
    # ISO dates in records ("2026-11-26") support written months ("26 November").
    months = {MONTH_NAMES[int(m) - 1] for m in re.findall(r"\b20\d{2}-(\d{2})-\d{2}", raw)}
    return _numbers(raw), raw.casefold() + " " + " ".join(months)


def _month_supported(month, text):
    return re.search(rf"\b{month[:3].casefold()}", text) is not None


def _identity_errors(answer):
    errors = []
    pairs = [(m[1], m[2]) for m in ABBR_NAME.finditer(answer)]
    pairs += [(m[2], m[1]) for m in NAME_ABBR.finditer(answer)]
    for abbr, claimed in pairs:
        known = DIRECTORY.get(abbr.upper())
        if not known:
            continue
        name, keys = known
        claim = normalize(claimed)
        if not claim or claim in (normalize(name), normalize(abbr)):
            continue
        if any(k in claim or claim in k for k in keys):
            continue
        message = f"{abbr} is {name}, not “{claimed.strip()}”."
        if message not in errors:
            errors.append(message)
    return errors


def audit_tables(answer, evidence, web_cited=False):
    """Remove tables whose figure columns cannot be matched to evidence."""
    if web_cited:
        return answer, []
    numbers, _ = _corpus(evidence)
    lines = answer.split("\n")
    removed = []
    for table in reversed(find_tables(lines)):
        figure_cols = [i for i, h in enumerate(table.header) if FIGURE_HEADER.search(h)]
        if not figure_cols:
            continue
        values = set()
        for row in table.rows:
            row_cells = cells(row)
            for i in figure_cols:
                if i < len(row_cells):
                    values |= _numbers(row_cells[i])
        unsupported = sorted(values - numbers)
        if not unsupported:
            continue
        columns = ", ".join(table.header[i] for i in figure_cols)
        removed.append({"columns": columns, "unsupported": unsupported[:12]})
        notice = [
            "> [!WARNING]",
            f"> I removed a table of figures ({columns}) that I couldn't match to verified "
            "records on file. Check each institution's official admissions page for the "
            "current cycle — I can help you read what you find.",
        ]
        lines[table.start : table.end] = notice
    return "\n".join(lines), list(reversed(removed))


def verify(answer, evidence, web_cited=False):
    """Return the claims checked and those the evidence does not support."""
    numbers, text = _corpus(evidence)
    claims = []
    for pattern in SCORE_CLAIM:
        claims += [("score", m.group(1)) for m in pattern.finditer(answer)]
    for pattern in MONEY_CLAIM:
        claims += [("fee", m.group(1).replace(",", "")) for m in pattern.finditer(answer)]
    timing, covered = [], set()
    for pattern in TIMING_CLAIM:  # most specific first; later patterns skip covered months
        for m in pattern.finditer(answer):
            months = [mo.casefold() for mo in re.findall(MONTH, m.group(1), re.I)]
            if set(months) <= covered:
                continue
            covered |= set(months)
            if not all(_month_supported(month, text) for month in months):
                timing.append(re.sub(DASH + "+", lambda d: d.group(0)[0], m.group(1)).strip())
    claims = list(dict.fromkeys(claims))
    unsupported = [
        {"kind": kind, "value": value}
        for kind, value in claims
        if (value.lstrip("0") or "0") not in numbers
    ]
    identity = _identity_errors(answer)
    timing = list(dict.fromkeys(timing))
    # Web-grounded answers cite live pages we cannot re-read here: attribute, don't flag.
    return {
        "checked": len(claims) + len(timing),
        "unsupported": [] if web_cited else unsupported,
        "web_attributed": [c["value"] for c in unsupported] if web_cited else [],
        "timing": [] if web_cited else timing,
        "identity": identity,
    }


def disclosure(result):
    points = []
    if result["unsupported"]:
        figures = ", ".join(
            f"₦{int(c['value']):,}" if c["kind"] == "fee" else c["value"]
            for c in result["unsupported"]
        )
        points.append(f"I couldn't match {figures} to a verified record on file.")
    if result.get("timing"):
        points.append(
            "Dates and timing ("
            + "; ".join(result["timing"][:4])
            + ") are not from a verified calendar — confirm them on the official portal."
        )
    points += result.get("identity", [])
    if not points:
        return ""
    body = "\n".join(f"> - {p}" for p in points)
    return (
        "\n\n> [!WARNING]\n> **Check before relying on this:** confirm on the official JAMB "
        f"or institution website before acting on it.\n{body}"
    )
