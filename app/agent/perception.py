"""Sensors: turn a student's message and its environment into a structured percept.

Everything here is deterministic and local: no provider quota is spent to perceive.
Recognised names are *perception hints* used to route tools and memory. They never
become admissions facts; facts still come only from sourced records.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models.chat import QueryIntent
from app.services.local_knowledge import match_university, normalize

GRADES = ("A1", "B2", "B3", "C4", "C5", "C6", "D7", "E8", "F9")

# Common programme names used only to *recognise* what a student is talking about.
# Aliases map to a short canonical form so substring lookups match catalogue names
# such as "Medicine and Surgery".
COURSE_ALIASES = {
    "mbbs": "Medicine",
    "medicine and surgery": "Medicine",
    "medicine": "Medicine",
    "nursing science": "Nursing",
    "nursing": "Nursing",
    "law": "Law",
    "pharmacy": "Pharmacy",
    "dentistry": "Dentistry",
    "veterinary medicine": "Veterinary Medicine",
    "computer science": "Computer Science",
    "software engineering": "Software Engineering",
    "cyber security": "Cyber Security",
    "cybersecurity": "Cyber Security",
    "information technology": "Information Technology",
    "mass communication": "Mass Communication",
    "mass comm": "Mass Communication",
    "electrical engineering": "Electrical Engineering",
    "electrical electronics engineering": "Electrical Engineering",
    "mechanical engineering": "Mechanical Engineering",
    "civil engineering": "Civil Engineering",
    "chemical engineering": "Chemical Engineering",
    "petroleum engineering": "Petroleum Engineering",
    "computer engineering": "Computer Engineering",
    "architecture": "Architecture",
    "microbiology": "Microbiology",
    "biochemistry": "Biochemistry",
    "medical laboratory science": "Medical Laboratory Science",
    "medical lab science": "Medical Laboratory Science",
    "physiotherapy": "Physiotherapy",
    "anatomy": "Anatomy",
    "physiology": "Physiology",
    "radiography": "Radiography",
    "optometry": "Optometry",
    "public health": "Public Health",
    "business administration": "Business Administration",
    "banking and finance": "Banking and Finance",
    "political science": "Political Science",
    "international relations": "International Relations",
    "estate management": "Estate Management",
    "agriculture": "Agriculture",
    "psychology": "Psychology",
    "sociology": "Sociology",
    "statistics": "Statistics",
    "geology": "Geology",
    "english and literary studies": "English and Literary Studies",
}
# Subject-like names count as a *course* only with an explicit study cue.
SUBJECT_LIKE_COURSES = {
    "mathematics": "Mathematics",
    "physics": "Physics",
    "chemistry": "Chemistry",
    "biology": "Biology",
    "english": "English",
    "economics": "Economics",
    "accounting": "Accounting",
    "geography": "Geography",
}

UTME_SUBJECTS = {
    "english": "English",
    "use of english": "English",
    "mathematics": "Mathematics",
    "maths": "Mathematics",
    "math": "Mathematics",
    "physics": "Physics",
    "chemistry": "Chemistry",
    "biology": "Biology",
    "economics": "Economics",
    "government": "Government",
    "literature in english": "Literature in English",
    "literature": "Literature in English",
    "crs": "Christian Religious Studies",
    "crk": "Christian Religious Studies",
    "irs": "Islamic Religious Studies",
    "geography": "Geography",
    "commerce": "Commerce",
    "accounting": "Principles of Accounts",
    "principles of accounts": "Principles of Accounts",
    "agricultural science": "Agricultural Science",
    "agric": "Agricultural Science",
    "further mathematics": "Further Mathematics",
    "history": "History",
    "civic education": "Civic Education",
    "yoruba": "Yoruba",
    "igbo": "Igbo",
    "hausa": "Hausa",
    "french": "French",
    "arabic": "Arabic",
    "fine art": "Fine Art",
    "music": "Music",
    "home economics": "Home Economics",
    "computer studies": "Computer Studies",
    "technical drawing": "Technical Drawing",
}
# O'Level abbreviations students type alongside grades.
OLEVEL_ALIASES = {
    "eng": "english",
    "english language": "english",
    "english": "english",
    "maths": "mathematics",
    "math": "mathematics",
    "mth": "mathematics",
    "mathematics": "mathematics",
    "bio": "biology",
    "biology": "biology",
    "chem": "chemistry",
    "chemistry": "chemistry",
    "phy": "physics",
    "phys": "physics",
    "physics": "physics",
    "econs": "economics",
    "eco": "economics",
    "economics": "economics",
    "govt": "government",
    "government": "government",
    "lit": "literature in english",
    "literature": "literature in english",
    "agric": "agricultural science",
    "agricultural science": "agricultural science",
    "geo": "geography",
    "geography": "geography",
    "civic": "civic education",
    "civic education": "civic education",
    "commerce": "commerce",
    "crs": "christian religious studies",
    "crk": "christian religious studies",
    "irs": "islamic religious studies",
    "further maths": "further mathematics",
    "further mathematics": "further mathematics",
    "computer": "computer studies",
    "data processing": "data processing",
    "accounting": "financial accounting",
    "financial accounting": "financial accounting",
    "technical drawing": "technical drawing",
}

STATES = [
    "Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa", "Benue", "Borno",
    "Cross River", "Delta", "Ebonyi", "Edo", "Ekiti", "Enugu", "Gombe", "Imo", "Jigawa",
    "Kaduna", "Kano", "Katsina", "Kebbi", "Kogi", "Kwara", "Lagos", "Nasarawa", "Niger",
    "Ogun", "Ondo", "Osun", "Oyo", "Plateau", "Rivers", "Sokoto", "Taraba", "Yobe",
    "Zamfara", "FCT",
]  # fmt: skip

# Affect lexicon (English + common Nigerian Pidgin). Crisis cues are handled first.
CRISIS = re.compile(
    r"\b(kill myself|end my life|take my (own )?life|suicid\w*|want to die|wan die|"
    r"no reason to live|better off dead|hurt myself)\b",
    re.I,
)
AFFECT_LEXICON = {
    "anxious": r"\b(worried|worry|scared|afraid|nervous|anxious|panic\w*|fear\w*|tense|"
    r"stress\w*|what if i fail|i'?m not sure i can|e dey worry me)\b",
    "discouraged": r"\b(failed|fail(ed)? jamb|low score|sad|disappoint\w*|depress\w*|cry\w*|"
    r"hopeless|give up|giving up|useless|i don tire|tire me|not good enough|"
    r"no hope|all hope|frustrat\w* (?:me|everything))\b",
    "frustrated": r"\b(confus\w*|frustrat\w*|annoy\w*|nobody (?:is )?answer\w*|no one helps|"
    r"wahala|this is hard|so hard|tired of|fed up|doesn'?t make sense)\b",
    "positive": r"\b(happy|excited|glad|thank god|grateful|i passed|i got admission|"
    r"admitted|great news|yay|congrat\w*)\b",
}
PIDGIN = re.compile(
    r"\b(abeg|wetin|una|dey|na im|shey|abi|wahala|how far|i don|make i|wey|sabi|oya|"
    r"wan|e be like|no be)\b",
    re.I,
)

THANKS = re.compile(r"\b(thanks?|thank you|thx|tnx|appreciate (it|you)|e se|nagode|daalu)\b", re.I)
GOODBYE = re.compile(r"\b(bye|goodbye|good night|goodnight|see you|later|talk later|ciao)\b", re.I)
AFFIRM = re.compile(
    r"^\s*(yes|yeah|yep|yup|ok(ay)?|sure|alright|go ahead|please do|do it|sounds good|"
    r"i agree|correct|that'?s right|exactly|oya)\b",
    re.I,
)
DENY = re.compile(
    r"^\s*(no|nope|nah|not really|i don'?t (want|like)|none of (these|them|those))\b", re.I
)
OPTION_CHOICE = re.compile(
    r"\b(?:option|choice|number|no\.?)\s*([1-9]|[a-e])\b|"
    r"\b(first|second|third|fourth|1st|2nd|3rd|4th)\s*(?:one|option)?\b",
    re.I,
)
ORDINALS = {
    "first": 0,
    "1st": 0,
    "second": 1,
    "2nd": 1,
    "third": 2,
    "3rd": 2,
    "fourth": 3,
    "4th": 3,
}
NEGATION = re.compile(r"\b(not|haven'?t|hasn'?t|didn'?t|never|yet to|no be|neither)\b", re.I)
FIRST_PERSON = re.compile(r"\b(i|i'?m|i'?ve|my|me|mine)\b", re.I)

COURSE_CUE = re.compile(
    r"\b(?:study|studying|read|reading|course(?: is| of)?|apply(?:ing)? for|applied for|"
    r"admission (?:in|into|for)|offer|want to do|chose|choice is|programme|program)\s+"
    r"(?:to\s+)?(?:study\s+)?([a-z][a-z &]{2,40})",
    re.I,
)
GOAL_CUE = re.compile(
    r"\b(i want|i wish|i'?d like|i plan|i'?m applying|i applied|my (?:first )?choice|"
    r"my course|i chose|i'?m interested|my dream|i hope)\b",
    re.I,
)

INSTITUTION_TYPE = re.compile(
    r"\b(federal|state|private)\s+(?:universit(?:y|ies)|schools?|institutions?|only)\b", re.I
)

# Journey milestones the student can report in conversation.
MILESTONES = {
    "get_profile_code": r"(got|have|received|generated|created) (my )?(jamb )?profile code",
    "register_utme": r"(registered|did my registration|completed (my )?registration|"
    r"have registered) (for )?(jamb|utme)|(jamb|utme) registration (is )?(done|complete)",
    "print_exam_slip": r"printed (my )?(exam|examination|reprinted)? ?slip",
    "write_utme": r"(wrote|written|sat for|took|have written|finished) (the |my )?(jamb|utme)",
    "check_result": r"(checked|seen|saw|got) (my )?(jamb |utme )?results?|my (jamb )?result (is|came) out",
    "upload_olevel": r"(uploaded|upload is done|have uploaded) (my )?(o.?level|waec|neco)",
    "post_utme_register": r"(registered|applied|filled|bought) (for |the )?(the )?post.?utme",
    "write_post_utme": r"(wrote|written|sat for|took|finished) (the |my )?post.?utme|screening (is )?done",
    "accept_caps": r"accepted (my |the )?(admission|offer)( on caps)?",
    "pay_acceptance": r"paid (my |the )?acceptance( fee)?",
}
STAGE_HINTS = [
    ("admitted", r"\b(i (?:have been|was|got) admitted|i got admission|offered admission)\b"),
    ("post_utme", r"\b(wrote|written|sat for|finished) (the |my )?post.?utme\b"),
    ("post_jamb", r"\b(wrote|written|sat for|finished) (the |my )?(jamb|utme)\b|\bi scored\b"),
    ("direct_entry", r"\bdirect entry\b.*\b(i|my)\b|\b(i|my)\b.*\bdirect entry\b"),
    ("pre_jamb", r"\b(preparing for|yet to write|want to register for|planning to write) (the )?(jamb|utme)\b"),
]  # fmt: skip


@dataclass
class Percept:
    text: str
    intent: QueryIntent
    entities: dict = field(default_factory=dict)
    stated: set = field(default_factory=set)  # entities the student asserted about themselves
    affect: str = "neutral"
    affect_intensity: float = 0.0
    acts: set = field(default_factory=set)
    option_choice: int | None = None
    milestones_done: list = field(default_factory=list)
    stage_hint: str | None = None
    pidgin: bool = False
    brief: bool = False
    unknown_institution: bool = False

    def has_facts(self):
        return bool(self.entities)


def _first_match(text, table):
    """Return the canonical value for the longest alias present as whole words."""
    padded = f" {normalize(text)} "
    for alias in sorted(table, key=len, reverse=True):
        if f" {normalize(alias)} " in padded:
            return table[alias]
    return None


def extract_score(text):
    patterns = [
        r"\b(?:scored|i got|i have|i had|my\s+(?:jamb\s+|utme\s+)?score\s*(?:is|was|of)?|"
        r"(?:jamb|utme)\s+score\s*(?:is|was)|with)\s*:?\s*(\d{2,3})\b(?!\s*(?:credits?|subjects?|days?|"
        r"years?|weeks?|months?|%|percent|naira|k\b|people|students?))",
        r"\b(\d{3})\s*(?:in|on|for|at)\s*(?:the\s*)?(?:jamb|utme)\b",
        r"\b(\d{3})\s*/\s*400\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            value = int(match.group(1))
            # "with 50" / "got 20" are rarely UTME totals; keep plausible aggregates.
            if 100 <= value <= 400:
                return value
    return None


def extract_olevel(text):
    grades = {}
    alias = "|".join(sorted((re.escape(a) for a in OLEVEL_ALIASES), key=len, reverse=True))
    grade = "|".join(GRADES)
    for match in re.finditer(rf"\b({alias})\b\s*[:=\-–]?\s*\(?\b({grade})\b", text, re.I):
        grades[OLEVEL_ALIASES[match.group(1).casefold()]] = match.group(2).upper()
    for match in re.finditer(rf"\b({grade})\b\s*(?:in|for)\s*\b({alias})\b", text, re.I):
        grades.setdefault(OLEVEL_ALIASES[match.group(2).casefold()], match.group(1).upper())
    return grades


def extract_utme_subjects(text):
    if not re.search(
        r"\b(subjects?|combination|i (?:wrote|took|offered|registered|chose|picked)|"
        r"my jamb (?:subjects|combination))\b",
        text,
        re.I,
    ):
        return []
    padded = f" {normalize(text)} "
    found = []
    for alias in sorted(UTME_SUBJECTS, key=len, reverse=True):
        key = f" {normalize(alias)} "
        if key in padded:
            canonical = UTME_SUBJECTS[alias]
            if canonical not in found:
                found.append(canonical)
            padded = padded.replace(key, " ")
    # A UTME combination is four subjects; more usually means O'Level talk.
    return found if 2 <= len(found) <= 4 else []


def extract_course(text, catalog_courses=()):
    lowered = normalize(text)
    for name in sorted(catalog_courses, key=len, reverse=True):
        if name and f" {normalize(name)} " in f" {lowered} ":
            return name
    course = _first_match(text, COURSE_ALIASES)
    if course:
        return course
    cue = COURSE_CUE.search(text)
    if cue:
        return _first_match(cue.group(1), SUBJECT_LIKE_COURSES)
    return None


def extract_universities(text, catalog_universities=()):
    """Return recognised institution names (catalogue first, then the reviewed directory)."""
    names = []
    padded = f" {normalize(text)} "
    for uni in catalog_universities:
        name, abbr = uni["name"], uni.get("abbreviation")
        if f" {normalize(name)} " in padded or (
            abbr and re.search(r"\b" + re.escape(abbr) + r"\b", text, re.I)
        ):
            names.append(name)
    for row in match_university(text):
        if not any(normalize(row["name"]) == normalize(n) for n in names):
            names.append(row["name"])
    return names


def extract_state(text):
    match = re.search(
        r"\b(?:i'?m from|i am from|state of origin(?: is)?|indigene of|from)\s+([a-z ]{3,20}?)"
        r"(?:\s+state\b|[,.!?]|$)",
        text,
        re.I,
    )
    if not match:
        return None
    candidate = normalize(match.group(1))
    for state in STATES:
        if normalize(state) == candidate:
            return state
    return None


def detect_affect(text):
    if CRISIS.search(text):
        return "crisis", 1.0
    scores = {
        label: len(re.findall(pattern, text, re.I)) for label, pattern in AFFECT_LEXICON.items()
    }
    label = max(scores, key=scores.get)
    if not scores[label]:
        return "neutral", 0.0
    intensity = min(1.0, 0.4 + 0.2 * scores[label] + 0.1 * text.count("!"))
    return label, round(intensity, 2)


def detect_milestones(text):
    done = []
    for task, pattern in MILESTONES.items():
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        # "I haven't registered for JAMB" is the opposite of a completed milestone.
        window = text[max(0, match.start() - 24) : match.start() + 6]
        if NEGATION.search(window):
            continue
        done.append(task)
    return done


def perceive(text, intent, catalog_courses=(), catalog_universities=()):
    """Sense a single message. Pure function: safe to call without a database."""
    p = Percept(text=text, intent=intent)
    first_person = bool(FIRST_PERSON.search(text))

    score = extract_score(text)
    if score is not None:
        p.entities["jamb_score"] = score
        if first_person or re.search(r"\b(scored|got)\b", text, re.I):
            p.stated.add("jamb_score")
    years = re.findall(r"\b(20\d{2})\b", text)
    if years:
        p.entities["year"] = int(years[-1])
    course = extract_course(text, catalog_courses)
    if course:
        p.entities["course"] = course
        if GOAL_CUE.search(text):
            p.stated.add("course")
    universities = extract_universities(text, catalog_universities)
    if universities:
        p.entities["universities"] = universities
        if GOAL_CUE.search(text):
            p.stated.add("universities")
    elif re.search(
        r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(University|Polytechnic|College)\b", text
    ) and not re.search(r"\b(which|what|any|other|best|good)\s+(university|school)", text, re.I):
        # A named institution we cannot recognise must not widen into "all schools".
        p.unknown_institution = True
    grades = extract_olevel(text)
    if grades:
        p.entities["olevel"] = grades
        p.stated.add("olevel")
    subjects = extract_utme_subjects(text)
    if subjects and not grades:
        p.entities["utme_subjects"] = subjects
        if first_person:
            p.stated.add("utme_subjects")
    state = extract_state(text)
    if state:
        p.entities["state_of_origin"] = state
        p.stated.add("state_of_origin")
    kind = INSTITUTION_TYPE.search(text)
    if kind:
        p.entities["institution_type"] = kind.group(1).lower()

    p.affect, p.affect_intensity = detect_affect(text)
    if THANKS.search(text):
        p.acts.add("thanks")
    if GOODBYE.search(text) and len(text.split()) <= 8:
        p.acts.add("goodbye")
    if AFFIRM.search(text):
        p.acts.add("affirm")
    if DENY.search(text):
        p.acts.add("deny")
    choice = OPTION_CHOICE.search(text)
    if choice:
        token = (choice.group(1) or choice.group(2) or "").casefold()
        if token.isdigit():
            p.option_choice = int(token) - 1
        elif len(token) == 1:
            p.option_choice = ord(token) - ord("a")
        else:
            p.option_choice = ORDINALS.get(token)
        if p.option_choice is not None:
            p.acts.add("select")
    if "?" in text or re.match(
        r"^\s*(what|which|when|where|how|who|why|can|is|are|do|does|should)\b", text, re.I
    ):
        p.acts.add("question")
    p.milestones_done = detect_milestones(text)
    for stage, pattern in STAGE_HINTS:
        if re.search(pattern, text, re.I) and not NEGATION.search(text):
            p.stage_hint = stage
            break
    p.pidgin = bool(PIDGIN.search(text))
    p.brief = len(text.split()) <= 6
    return p
