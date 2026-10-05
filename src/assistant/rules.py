"""The rules `route` applies before any model call: danger to life, complaints and account
requests, and messages the assistant declines.

**This is where the client's domain rules go.** The patterns below are examples that make the
mechanism work on day one: replace them with the safety-critical situations agreed with the
client (a gas smell for an energy retailer, fraud for a bank, self-harm for a public service),
and record who approved the fixed reply. Add every phrasing, and its look-alikes that must not
match, to `tests/test_rules.py`.

Rules only, no model, so detection never depends on what a model makes of the message. The
danger rules are biased towards false positives: sending someone with an ordinary question to
000 costs little, missing someone in danger could cost a life.

Messages are matched in lower case with doubled letters collapsed ("bleeding" and the typo
"bleding" both read as "bleding"), so the patterns below are written collapsed too: "bleding",
"kil", "colapsed", "cal an ambulance".
"""

import re
from typing import Literal

Rule = Literal["danger", "handoff", "decline"]

# The page the emergency reply cites as its one source, or None for a reply that cites nothing.
# A dict with `source` (the title) and `url`, on the client's site. When set, add "[1]" to
# `prompts.REQUIRED["emergency"]` so an edited reply cannot lose the citation.
EMERGENCY_PAGE: dict | None = None

# Misspellings that collapsing doubled letters does not fix, by the word they stand for. Keys
# are collapsed too: "emmergency" arrives as "emergency", "sucide" needs this map.
_MISSPELLINGS = {
    "sucide": "suicide",
    "suicde": "suicide",
    "emergancy": "emergency",
    "emergeny": "emergency",
    "ambulence": "ambulance",
    "unconcious": "unconscious",
}


def normalise(text: str) -> str:
    """Lower case, straight apostrophes, doubled letters collapsed, known misspellings fixed."""
    text = text.lower().replace("’", "'").replace("‘", "'")
    text = re.sub(r"([a-z])\1+", r"\1", text)
    return re.sub(r"[a-z']+", lambda m: _MISSPELLINGS.get(m.group(), m.group()), text)


def _near(a: str, b: str, words: int = 5) -> str:
    """`a` and `b` within `words` words of each other, in either order."""
    gap = rf"\W+(?:[\w']+\W+){{0,{words}}}?"
    return rf"(?:\b(?:{a})\b{gap}\b(?:{b})\b|\b(?:{b})\b{gap}\b(?:{a})\b)"


# EXAMPLE danger rules: someone in danger. Replace or extend with the client's domain.
_DANGER = re.compile(
    "|".join(
        [
            r"\b(?:someone|anyone|somebody|my (?:child|son|daughter|partner|wife|husband|mum|dad|friend)) "
            r"(?:is |might be |has been )?(?:in danger|hurt|injured|unconscious|not breathing|colapsed)\b",
            r"\b(?:can'?t|cannot|not) breath\w*",
            r"\bunconscious\b|\bcolapsed\b|\bbleding (?:heavily|badly|a lot)\b",
            r"\b(?:kil|hurt|harm) (?:myself|himself|herself|themselves)\b|\bself[- ]?harm\w*|\bsuicid\w*",
            r"\boverdos\w*",
            r"\b(?:it'?s|this is|we have|i have|there'?s) an emergency\b",
            r"\bcal (?:an )?ambulance\b|\bneds? an ambulance\b",
            _near(r"house|building|kitchen|rom|car", r"on fire|burning|fire", 3),
        ]
    )
)

# Complaints and requests only the customer's account can answer: the human contact route.
_HANDOFF = re.compile(
    "|".join(
        [
            r"\b(?:make|lodge|file|raise|submit|have|put in) (?:an? )?(?:formal )?complaint\b",
            r"\bwant to complain\b|\bcomplain(?:ing)? about\b|\bombudsman\b|\bescalate\b",
            r"\b(?:second|third|fourth|fifth|\d+(?:st|nd|rd|th)) time (?:i'?ve|i have|we'?ve) "
            r"(?:contacted|caled|rung|emailed|writen|chased)",
            r"\b(?:nobody|no one|no-one) (?:has |have )?(?:fixed|helped|caled|got back|responded|replied)",
            r"\bhow much do i owe\b|\bmy (?:curent |acount )?balance\b",
            r"\bwhen is my (?:next )?(?:bil|payment) due\b",
            r"\b(?:please|can you|could you|would you|i want you to|i ned you to)\s+"
            r"(?:change|update|cancel|close|check|lok up|lokup|pause|stop|set up|remove|ad)\s+my\b",
        ]
    )
)

# Attempts to change the assistant's instructions. Add the client's competitors here, if it
# should not compare itself with them (`_near(<names>, r"cheap\w*|beter|best|compare\w*|vs")`).
_DECLINE = re.compile(
    "|".join(
        [
            r"\bignore (?:al |any |your |the |my )*(?:previous |prior |above |earlier |system )*"
            r"(?:instructions?|prompts?|rules)\b",
            r"\b(?:system|developer|hiden) prompt\b",
            r"\b(?:print|reveal|show|repeat|tel me) (?:me )?(?:your|the) (?:instructions|prompt|rules)\b",
            r"\bjailbreak\w*|\bpretend (?:you are|you're|to be)\b",
        ]
    )
)


def classify(text: str, *, danger: bool = True) -> Rule | None:
    """The rule a customer message falls under, or None to answer it as usual.

    `danger=False` (`SAFETY_RULES=false`) skips the danger rules, for a project with no
    safety-critical situations.
    """
    text = normalise(text)
    if danger and _DANGER.search(text):
        return "danger"
    if _DECLINE.search(text):
        return "decline"
    if _HANDOFF.search(text):
        return "handoff"
    return None
