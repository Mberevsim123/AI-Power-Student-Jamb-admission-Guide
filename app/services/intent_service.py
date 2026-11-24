"""
IntentClassifier
----------------
Classifies a student's message into a QueryIntent using keyword heuristics
first (fast, zero-cost), falling back to an LLM call for ambiguous cases.
"""

import logging
import re

from app.core.config import settings
from app.models.chat import QueryIntent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword rules — ordered from most specific to most general
# ---------------------------------------------------------------------------

_RULES: list[tuple[QueryIntent, list[str]]] = [
    (
        QueryIntent.cutoff_lookup,
        [
            "cut.?off",
            r"\bscore\b.*\badmit",
            r"\bminimum score\b",
            "cut off mark",
            "cut-off mark",
            "cutoff mark",
            "admission score",
        ],
    ),
    (
        QueryIntent.subject_combination,
        [
            "subject combination",
            "jamb subject",
            "which subject",
            "what subject",
            "subjects? for",
            "jamb combination",
        ],
    ),
    (
        QueryIntent.course_eligibility,
        [
            "am i eligible",
            "can i study",
            "qualify for",
            "my score.*course",
            "good enough",
            "will i be admitted",
            "can i get admission",
        ],
    ),
    (
        QueryIntent.school_recommendation,
        [
            "which school",
            "best school",
            "recommend.*university",
            "university.*recommend",
            "where can i",
            "school.*admit me",
            "school.*my score",
            r"choose (?:a |my |the )?(?:course|school|university|institution)",
            r"(?:course|school|universit(?:y|ies)|institutions?).{0,20}(?:fit|suit|match)(?:es)? me",
            r"which (?:course|programme|program)",
            r"course(?:s)? (?:to study|for me)",
            r"\balternatives?\b|\bsafer (?:option|school|choice)s?\b|\bbackup (?:school|option)s?\b",
            r"what matters (?:most )?when choosing",
            r"close to my (?:state|home)|near (?:my )?(?:home|state)",
            r"\bany school\b|\b(?:federal|state|private) (?:universities|schools) only\b",
        ],
    ),
    (
        QueryIntent.olevel_requirement,
        [
            "o.?level",
            "waec",
            "neco",
            "credit",
            "five credit",
            "olevel",
            "o level requirement",
            "waec result",
        ],
    ),
    (
        QueryIntent.post_utme_info,
        [
            "post.?utme",
            "post utme",
            "screening",
            "departmental cut.?off",
            "aggregate score",
            "jamb.*post utme",
        ],
    ),
    (
        QueryIntent.jamb_process,
        [
            "register.*jamb",
            "jamb registration",
            "jamb portal",
            "print.*slip",
            "jamb.*date",
            "when.*jamb",
            "jamb.*form",
            "how to.*jamb",
            "jamb mock",
            "jamb result",
            "check.*result",
            r"\b(profile code|e.?pin|nin|cbt)\b",
        ],
    ),
    (
        QueryIntent.general_admission,
        [
            "how.*admission",
            "admission.*process",
            "direct entry",
            "change.*course",
            "change.*institution",
            "admission.*letter",
            "acceptance fee",
            r"\b(ibass|brochure|transfer approval)\b",
            r"upload.*results?|awaiting.*results?",
            r"\b(jamb|utme|caps|admissions?|universit(?:y|ies)|polytechnic|tertiary|institutions?)\b",
        ],
    ),
    (
        QueryIntent.greeting,
        [
            r"^hi[\s!.?]*$",
            r"^hello[\s!.?]*$",
            r"^good (morning|afternoon|evening)",
            r"^hey[\s!.?]*$",
            "how are you",
            "what can you do",
        ],
    ),
]


def _compile(patterns: list[str]) -> re.Pattern:
    combined = "|".join(f"(?:{p})" for p in patterns)
    return re.compile(combined, re.IGNORECASE)


_COMPILED_RULES: list[tuple[QueryIntent, re.Pattern]] = [
    (intent, _compile(patterns)) for intent, patterns in _RULES
]


class IntentClassifier:
    """
    Sync-first keyword classifier with an optional async LLM fallback.
    """

    async def classify(self, text: str) -> QueryIntent:
        intent = self._keyword_classify(text.strip())
        if intent == QueryIntent.out_of_scope:
            from app.services.local_knowledge import match_university

            if match_university(text):
                return QueryIntent.general_admission
        if intent != QueryIntent.out_of_scope:
            return intent

        if not settings.INTENT_LLM_FALLBACK:
            return QueryIntent.out_of_scope

        # Fallback: ask the LLM (import here to avoid circular deps)
        try:
            return await self._llm_classify(text)
        except Exception:
            logger.warning("LLM intent fallback failed — returning out_of_scope")
            return QueryIntent.out_of_scope

    def _keyword_classify(self, text: str) -> QueryIntent:
        for intent, pattern in _COMPILED_RULES:
            if pattern.search(text):
                return intent
        return QueryIntent.out_of_scope

    async def _llm_classify(self, text: str) -> QueryIntent:
        from app.core.llm_client import LLMClient

        valid = [i.value for i in QueryIntent]
        prompt = [
            {
                "role": "system",
                "content": (
                    "You are an intent classifier for a JAMB/university admissions chatbot. "
                    f"Return ONLY one of these labels: {', '.join(valid)}"
                ),
            },
            {"role": "user", "content": f"Classify this student query:\n\n{text}"},
        ]
        llm = LLMClient()
        resp, _ = await llm.complete(prompt, max_tokens=20)
        label = resp.strip().lower().split()[0]

        for intent in QueryIntent:
            if intent.value == label:
                return intent

        return QueryIntent.out_of_scope
