"""Official platforms: where a student actually completes each admissions task.

The agent refers students to these (and to institution websites from the NUC
directory) when an action is involved, and the link guard removes any other link a
model writes, so a student is never sent to an invented or look-alike site.
URLs checked reachable on 2026-11-24.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.models.chat import QueryIntent


@dataclass(frozen=True)
class Platform:
    id: str
    label: str
    url: str
    purpose: str


PLATFORMS = {
    p.id: p
    for p in [
        Platform("jamb", "JAMB portal", "https://www.jamb.gov.ng", "UTME registration news, e-PIN and slips"),
        Platform("efacility", "JAMB e-Facility", "https://efacility.jamb.gov.ng",
                 "results, O'Level upload, change of course or institution, CAPS access"),
        Platform("caps", "JAMB CAPS", "https://jamb.gov.ng/caps", "admission status and accepting offers"),
        Platform("ibass", "JAMB IBASS eligibility checker", "https://eligibility.jamb.gov.ng",
                 "subject combinations and requirements by course and institution"),
        Platform("jamb_faq", "JAMB FAQ", "https://jamb.gov.ng/FAQ", "official answers to common questions"),
        Platform("jamb_support", "JAMB support", "https://support.jamb.gov.ng/faqs",
                 "profile code, NIN and registration problems"),
        Platform("waec", "WAEC result checker", "https://www.waecdirect.org", "check WAEC results"),
        Platform("neco", "NECO result checker", "https://results.neco.gov.ng", "check NECO results"),
        Platform("nimc", "NIMC (NIN)", "https://nimc.gov.ng", "National Identification Number enrolment"),
        Platform("nuc", "NUC university directory", "https://www.nuc.edu.ng",
                 "the official list of approved universities"),
    ]
}  # fmt: skip

# (pattern on the message, platforms) — most specific first.
TOPICS = [
    (r"profile code|\bnin\b|55019|66019", ["jamb_support", "nimc"]),
    (r"o.?level|waec|neco|upload", ["efacility", "waec", "neco"]),
    (
        r"\bcaps\b|admission status|accept(?:ed|ing)? (?:my )?(?:admission|offer)|offer",
        ["caps", "efacility"],
    ),
    (r"change of (?:course|institution)|change (?:my )?(?:course|institution)", ["efacility"]),
    (r"(?:check|see|print).{0,12}result|result.{0,12}(?:out|released|check)", ["efacility"]),
    (r"subject|combination|eligib|ibass|requirement|qualif", ["ibass"]),
    (r"regist|e.?pin|\bcbt\b|slip|reprint|mock", ["jamb", "jamb_faq"]),
    (r"(?:list|how many|which|all).{0,30}universit|approved universit|accredit", ["nuc"]),
]
INTENT_DEFAULTS = {
    QueryIntent.subject_combination: ["ibass"],
    QueryIntent.olevel_requirement: ["ibass", "efacility"],
    QueryIntent.course_eligibility: ["ibass"],
    QueryIntent.jamb_process: ["jamb"],
}
INSTITUTION_FIRST = {QueryIntent.post_utme_info, QueryIntent.cutoff_lookup}
MAX_LINKS = 3
TRUSTED_SUFFIXES = (".gov.ng", ".edu.ng")
TRUSTED_HOSTS = {urlsplit(p.url).hostname for p in PLATFORMS.values()}


def _link(label, url, purpose, kind):
    return {"label": label, "url": url, "purpose": purpose, "kind": kind}


def recommend(text, intent, universities=()):
    """Links worth giving for this message: only when there is something to do."""
    if intent in (QueryIntent.greeting, QueryIntent.out_of_scope):
        return []
    ids = []
    for pattern, platforms in TOPICS:
        if re.search(pattern, text, re.I):
            ids += platforms
    if not ids:
        ids = list(INTENT_DEFAULTS.get(intent, []))
    links = []
    sites = [
        _link(f"{u['name'].split(',')[0]} website", u["website_url"], "official admissions information",
              "institution")
        for u in universities if u.get("website_url")
    ][:2]  # fmt: skip
    # Post-UTME notices and departmental cut-offs are published by the institution.
    if intent in INSTITUTION_FIRST or re.search(r"post.?utme|screening", text, re.I):
        links += sites
        sites = []
    links += [
        _link(p.label, p.url, p.purpose, "platform")
        for p in (PLATFORMS[i] for i in dict.fromkeys(ids))
    ]
    links += sites
    seen, unique = set(), []
    for link in links:
        if link["url"] not in seen:
            seen.add(link["url"])
            unique.append(link)
    return unique[:MAX_LINKS]


def directive(links):
    if not links:
        return ""
    listed = "; ".join(f"[{link['label']}]({link['url']}) — {link['purpose']}" for link in links)
    return (
        "Where the student must act on an official platform, link it inline as Markdown using "
        f"these exact URLs: {listed}. Do not invent or guess any other URL."
    )


# ── Link guard ─────────────────────────────────────────────────────────────

MD_LINK = re.compile(r"\[([^\]\n]+)\]\(\s*<?(https?://[^)\s>]+)>?\s*\)")
BARE_URL = re.compile(r"(?<![(<\[])\bhttps?://[^\s)<>\]]+")


def _host(url):
    try:
        return (urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def trusted(url, extra_hosts=()):
    host = _host(url)
    if not host:
        return False
    hosts = {h.removeprefix("www.") for h in TRUSTED_HOSTS | set(extra_hosts)}
    return host in hosts or host.endswith(TRUSTED_SUFFIXES)


def evidence_hosts(*collections):
    """Hosts of every URL in the supplied evidence (records, documents, web sources)."""
    found = set()

    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            found.update(_host(u) for u in re.findall(r"https?://[^\s\"'<>)]+", value))

    for collection in collections:
        walk(collection)
    return {h for h in found if h}


def guard_links(text, extra_hosts=()):
    """Unlink URLs outside official domains and the evidence; keep the visible text."""
    removed = []

    def markdown(match):
        label, url = match.group(1), match.group(2)
        if trusted(url, extra_hosts):
            return match.group(0)
        removed.append(url)
        return label

    text = MD_LINK.sub(markdown, text)
    protected = [m.span() for m in MD_LINK.finditer(text)]

    def bare(match):
        if any(start <= match.start() < end for start, end in protected):
            return match.group(0)
        url = match.group(0).rstrip(".,;:")
        if trusted(url, extra_hosts):
            return match.group(0)
        removed.append(url)
        return f"`{_host(url)}`" + match.group(0)[len(url) :]

    return BARE_URL.sub(bare, text), list(dict.fromkeys(removed))
