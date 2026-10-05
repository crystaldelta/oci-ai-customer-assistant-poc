"""Behaviour scenarios: scripted conversations and the checks run on each turn.

Each file in `scenarios/` holds the scenarios of one category (its file name).
A turn is a customer message, what the assistant is expected to do, and an
`ideal` answer in words. The checks here are deterministic, so they run locally
on any model (principle 4); whether an answer is good enough is judged against
`ideal` on the production model in Phase 6, where the scenarios become the eval
dataset (AD-17).

Checks per turn, each one passing, failing or skipped:

    behaviour  the route the service reports (`behaviour` on its result);
               skipped when it reports none
    sources    every named page is cited; `sources: []` means nothing is cited
    mentions   each phrase appears (case-insensitive); a list is any-of
    avoids     none of these phrases appears
    grounded   every phone number or price in the answer appears in the cited
               content or in `allowed_numbers` (principle 5); always run
    format     no tables or headings, which a chat window renders poorly; always
               run
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from assistant.ingest import read_page

SCENARIOS_DIR = Path(__file__).parent / "scenarios"
# The page titles in the content, which `sources` may name. Committed, because a project's real
# content may not be; `scripts/run_scenarios.py --write-titles` refreshes it from CONTENT_DIR.
SITE_TITLES = Path(__file__).parent / "site_titles.txt"

# What the assistant does with a turn. The graph reports one per turn.
Behaviour = Literal[
    "answer",  # answers from the content, or says it does not cover the question
    "clarify",  # asks one question because the answer depends on the customer's situation
    "emergency",  # the fixed, rule-based safety response (AD-12), before any model call
    "handoff",  # complaints and account-specific requests: points to a human contact route
    "decline",  # out of scope: says so clearly
]


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    behaviour: Behaviour
    sources: list[str] | None = None  # None: not checked
    mentions: list[str | list[str]] = Field(default_factory=list)
    avoids: list[str] = Field(default_factory=list)
    allowed_numbers: list[str] = Field(default_factory=list)


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: str
    expect: Expect
    ideal: str


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    turns: list[Turn] = Field(min_length=1)
    category: str = ""  # set from the file name


def load(directory: Path = SCENARIOS_DIR) -> list[Scenario]:
    scenarios = []
    for path in sorted(directory.glob("*.yaml")):
        for raw in yaml.safe_load(path.read_text()) or []:
            scenarios.append(Scenario(**raw, category=path.stem))
    ids = [s.id for s in scenarios]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"duplicate scenario IDs: {', '.join(duplicates)}")
    return scenarios


def site_titles(path: Path = SITE_TITLES) -> set[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return {line for line in lines if line and not line.startswith("#")}


def content_titles(content_dir: Path) -> set[str]:
    """The page titles in a content folder."""
    return {read_page(p)[1]["title"] for p in content_dir.glob("*.md")}


def write_site_titles(content_dir: Path, path: Path = SITE_TITLES) -> int:
    """Write `content_dir`'s page titles to `path`; returns how many."""
    titles = sorted(content_titles(content_dir))
    header = f"# Page titles in {content_dir.name}/. Regenerate: scripts/run_scenarios.py --write-titles\n"
    path.write_text(header + "".join(f"{t}\n" for t in titles), encoding="utf-8")
    return len(titles)


# ------------------------------------------------------------------ checks

# Phone numbers (13, 1300, 1800, landlines and mobiles) and prices. 000 is too
# short to match, so the emergency number never needs allowing.
_NUMBERS = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?"
    r"|\b1[38]00(?:[\s-]?\d){6}\b"
    r"|\b13(?:[\s-]?\d){4}\b"
    r"|(?<!\d)(?:\+61\s?|\(?0)[2-478]\)?(?:[\s-]?\d){8}\b",
    re.IGNORECASE,
)


# Models write typographic hyphens and quotes ("follow-up" with U+2011, "you'll"
# with U+2019), which a plain substring match would miss.
_TYPOGRAPHIC = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u00a0": " ",
        "\u2009": " ",  # thin space
        "\u202f": " ",  # narrow no-break space, as in "133 466"
    }
)


def _normalise(text: str) -> str:
    return text.translate(_TYPOGRAPHIC).lower()


def _compact(text: str) -> str:
    return re.sub(r"[\s()-]", "", text).lower()


@dataclass
class Check:
    name: str
    status: Literal["pass", "fail", "skip"]
    detail: str = ""


def _result(name: str, ok: bool, detail: str = "") -> Check:
    return Check(name, "pass" if ok else "fail", detail)


def _check_behaviour(expect: Expect, response: dict) -> list[Check]:
    reported = response.get("behaviour")
    if reported is None:
        return [Check("behaviour", "skip", "not reported")]
    return [_result("behaviour", reported == expect.behaviour, f"expected {expect.behaviour}, got {reported}")]


def _check_sources(expect: Expect, cited: list[str]) -> list[Check]:
    if expect.sources is None:
        return []
    if not expect.sources:
        return [_result("sources", not cited, f"cited {cited}" if cited else "")]
    missing = [s for s in expect.sources if s not in cited]
    return [_result("sources", not missing, f"missing {missing}" if missing else "")]


def _check_phrases(expect: Expect, answer: str) -> list[Check]:
    text = _normalise(answer)
    checks = []
    for phrase in expect.mentions:
        options = [phrase] if isinstance(phrase, str) else phrase
        checks.append(_result("mentions", any(_normalise(o) in text for o in options), " | ".join(options)))
    for phrase in expect.avoids:
        checks.append(_result("avoids", _normalise(phrase) not in text, phrase))
    return checks


def _check_grounded(expect: Expect, answer: str, sources: list[dict]) -> list[Check]:
    grounding = _compact(" ".join([*(s.get("content", "") for s in sources), *expect.allowed_numbers]))
    invented = [m.group() for m in _NUMBERS.finditer(answer) if _compact(m.group()) not in grounding]
    return [_result("grounded", not invented, f"not in the sources: {invented}" if invented else "")]


# A table's separator row, or a Markdown heading, at the start of a line.
_TABLE_OR_HEADING = re.compile(r"^[ \t]*(?:(?:\|[ \t]*)?:?-{3,}:?[ \t]*\||#{1,6}[ \t])", re.MULTILINE)


def _check_format(answer: str) -> list[Check]:
    found = _TABLE_OR_HEADING.search(answer)
    return [_result("format", not found, f"table or heading: {found.group().strip()!r}" if found else "")]


def check_turn(expect: Expect, response: dict) -> list[Check]:
    """Run every check on one response: `{"answer", "sources", "behaviour"?}`."""
    answer = response.get("answer") or ""
    sources = response.get("sources") or []
    cited = list(dict.fromkeys(s["source"] for s in sources))
    return [
        *_check_behaviour(expect, response),
        *_check_sources(expect, cited),
        *_check_phrases(expect, answer),
        *_check_grounded(expect, answer, sources),
        *_check_format(answer),
    ]


# ------------------------------------------------------------------ running

# One turn: `(question, thread_id or None) -> response`. The response needs
# `answer`, `sources` and `thread_id`; it raises if the turn could not run.
Ask = Callable[[str, str | None], dict]


@dataclass
class TurnResult:
    user: str
    answer: str
    checks: list[Check] = field(default_factory=list)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and all(c.status != "fail" for c in self.checks)


@dataclass
class ScenarioResult:
    scenario: Scenario
    turns: list[TurnResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.turns) == len(self.scenario.turns) and all(t.passed for t in self.turns)


def run(scenario: Scenario, ask: Ask) -> ScenarioResult:
    """Hold the scenario's conversation in one thread, checking each turn."""
    result = ScenarioResult(scenario)
    thread_id = None
    for turn in scenario.turns:
        try:
            response = ask(turn.user, thread_id)
        except Exception as exc:  # the rest of the conversation depends on this turn
            result.turns.append(TurnResult(turn.user, "", error=f"{type(exc).__name__}: {exc}"))
            break
        thread_id = response["thread_id"]
        result.turns.append(TurnResult(turn.user, response.get("answer") or "", check_turn(turn.expect, response)))
    return result
