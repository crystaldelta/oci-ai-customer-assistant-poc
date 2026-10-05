"""The `guardrail` seam: content checks on questions and answers, chosen by
`GUARDRAIL`.

- `noop` (local default): logs what it would check and allows everything.
- `bedrock`: the Bedrock Guardrail `GUARDRAIL_ID` at `GUARDRAIL_VERSION`, in
  `AWS_REGION`, through `ApplyGuardrail` (AD-18). Its definition is
  `deploy/guardrail.json`; see `docs/guardrail.md`.

The service checks a question after the behaviour rules, so the emergency
reply never depends on the guardrail, and checks a written answer with the
passages it was written from, for the grounding check. Fixed replies are not
checked. On Azure, Azure AI Content Safety would be another class here.

Logs record sizes and the types found, never the text: PII must not reach the
logs either.
"""

import logging
from dataclasses import dataclass, field
from typing import Literal, Protocol

from assistant.config import Settings

log = logging.getLogger("ai-starter")


@dataclass(frozen=True)
class GuardrailResult:
    """What a check decided.

    `message` is the fixed text to show instead, when `allowed` is False. `text` is the checked
    text with details masked, when the guardrail masked any. `findings` names what it detected,
    by policy and type ("pii:PHONE"), whether or not it acted; `scores` holds the grounding
    check's scores ("grounding", "relevance"), for an answer.
    """

    allowed: bool
    message: str | None = None
    text: str | None = None
    findings: tuple[str, ...] = ()
    scores: dict[str, float] = field(default_factory=dict)


ALLOWED = GuardrailResult(allowed=True)

# Shown when the guardrail blocks without giving its own message.
BLOCKED_MESSAGE = "Sorry, I can't help with that here. If anyone is in danger, call 000."


class Guardrail(Protocol):
    def check(
        self,
        text: str,
        *,
        source: Literal["input", "output"],
        grounding: str | None = None,
        query: str | None = None,
    ) -> GuardrailResult: ...


class NoopGuardrail:
    def check(self, text, *, source, grounding=None, query=None) -> GuardrailResult:
        log.debug("guardrail (noop): would check %s of %d characters", source, len(text))
        return ALLOWED


# ApplyGuardrail's limits for the contextual grounding check, in characters. A longer answer is
# checked without it.
GROUNDING_SOURCE_LIMIT = 100_000
QUERY_LIMIT = 1_000
GUARDED_LIMIT = 5_000


class BedrockGuardrail:
    """A Bedrock Guardrail, applied to the text on its own (`ApplyGuardrail`), not on the model
    call, so it works whichever model answers.

    A failed check allows the text and logs a warning: the safety replies come from the
    behaviour rules, which never depend on it. `client` is for tests, which pass a stub;
    otherwise the boto3 client is made on the first check, so construction makes no network
    call.
    """

    def __init__(self, guardrail_id: str, version: str, region: str, client=None) -> None:
        if not guardrail_id or not version:
            raise ValueError("GUARDRAIL=bedrock needs GUARDRAIL_ID and GUARDRAIL_VERSION")
        self.guardrail_id = guardrail_id
        self.version = version
        self.region = region
        self._client = client

    def _get_client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            # A short wait: the check is on every turn's path.
            timeouts = Config(connect_timeout=2, read_timeout=5, retries={"max_attempts": 2, "mode": "standard"})
            self._client = boto3.client("bedrock-runtime", region_name=self.region, config=timeouts)
        return self._client

    def check(self, text, *, source, grounding=None, query=None) -> GuardrailResult:
        content = [{"text": {"text": text}}]
        if grounding and query and len(text) <= GUARDED_LIMIT:
            content = [
                {"text": {"text": grounding[:GROUNDING_SOURCE_LIMIT], "qualifiers": ["grounding_source"]}},
                {"text": {"text": query[:QUERY_LIMIT], "qualifiers": ["query"]}},
                {"text": {"text": text, "qualifiers": ["guard_content"]}},
            ]
        try:
            response = self._get_client().apply_guardrail(
                guardrailIdentifier=self.guardrail_id,
                guardrailVersion=self.version,
                source=source.upper(),
                content=content,
                # FULL also returns what was found without action, and the grounding scores.
                outputScope="FULL",
            )
        except Exception as exc:  # noqa: BLE001 — a failed check must not stop the answer
            log.warning("guardrail: the %s check failed (%s); allowed", source, type(exc).__name__)
            return ALLOWED
        result = to_result(response)
        if result.findings:
            log.info("guardrail: %s %s: %s", source, _outcome(result), ",".join(result.findings))
        return result


def _outcome(result: GuardrailResult) -> str:
    if not result.allowed:
        return "blocked"
    return "masked" if result.text else "found"


# Each policy's list of entries in an assessment, and the label it is reported under.
_POLICIES = [
    ("topicPolicy", "topics", "topic", "name"),
    ("contentPolicy", "filters", "content", "type"),
    ("wordPolicy", "customWords", "word", None),
    ("wordPolicy", "managedWordLists", "word", "type"),
    ("sensitiveInformationPolicy", "piiEntities", "pii", "type"),
    ("sensitiveInformationPolicy", "regexes", "regex", "name"),
    ("contextualGroundingPolicy", "filters", "grounding", "type"),
]


def _entries(response: dict):
    """Every entry of every policy in the response's assessments, with its label ("pii:PHONE")."""
    for assessment in response.get("assessments", []):
        for policy, entries, label, kind in _POLICIES:
            for entry in assessment.get(policy, {}).get(entries, []):
                yield entry, f"{label}:{entry[kind]}" if kind else label


def to_result(response: dict) -> GuardrailResult:
    """An `ApplyGuardrail` response as a `GuardrailResult`. The matched text is never kept."""
    findings: list[str] = []
    scores: dict[str, float] = {}
    blocked = False
    for entry, name in _entries(response):
        if name.startswith("grounding:") and "score" in entry:
            scores[entry["type"].lower()] = entry["score"]
        # With `outputScope=FULL` an entry says whether it was detected; otherwise only
        # detections are listed.
        if entry.get("detected", entry.get("action") != "NONE"):
            findings.append(name)
            blocked = blocked or entry.get("action") == "BLOCKED"
    findings = list(dict.fromkeys(findings))
    outputs = "".join(o.get("text", "") for o in response.get("outputs", []))
    found = {"findings": tuple(findings), "scores": scores}
    if blocked:
        return GuardrailResult(allowed=False, message=outputs or BLOCKED_MESSAGE, **found)
    masked = outputs if response.get("action") == "GUARDRAIL_INTERVENED" and outputs else None
    return GuardrailResult(allowed=True, text=masked, **found)


def get_guardrail(settings: Settings) -> Guardrail:
    if settings.guardrail == "noop":
        return NoopGuardrail()
    if settings.guardrail == "bedrock":
        return BedrockGuardrail(settings.guardrail_id, settings.guardrail_version, settings.aws_region)
    raise NotImplementedError(f"GUARDRAIL={settings.guardrail} is not supported")
