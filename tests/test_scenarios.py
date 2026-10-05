"""The behaviour scenarios in evals/scenarios/: they load, they name pages in the content,
their checks work, and every conversation runs through the service on fakes.
Whether the answers pass is for scripts/run_scenarios.py against a real model.
"""

import pytest

from assistant import config
from assistant import service as svc
from evals import scenarios
from evals.scenarios import Expect, check_turn

# The kinds of conversation every project keeps (Phase 4); add the client's own categories.
# `emergency` only while the safety rules are on (the project's default, not the tests' pin).
REQUIRED_CATEGORIES = {"follow-up", "handoff", "not-in-content", "out-of-scope"}
if config.Settings.model_fields["safety_rules"].default:
    REQUIRED_CATEGORIES.add("emergency")


def _statuses(expect: Expect, response: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for check in check_turn(expect, response):
        out.setdefault(check.name, []).append(check.status)
    return out


def test_every_scenario_file_loads_and_covers_the_required_categories():
    loaded = scenarios.load()

    assert {s.category for s in loaded} >= REQUIRED_CATEGORIES
    assert all(len(s.turns) >= 1 for s in loaded)


def test_expected_sources_are_pages_in_the_content():
    titles = scenarios.site_titles()

    for scenario in scenarios.load():
        for turn in scenario.turns:
            assert set(turn.expect.sources or []) <= titles, (scenario.id, turn.expect.sources)


CONTENT = config.ROOT / "content"


@pytest.mark.skipif(not any(CONTENT.glob("*.md")), reason="no content in content/ (it may be git-ignored)")
def test_the_title_list_matches_the_content():
    expected = scenarios.content_titles(CONTENT)

    assert scenarios.site_titles() == expected, "run scripts/run_scenarios.py --write-titles"


def test_the_title_list_is_written_from_a_content_folder(tmp_path):
    for name, title in [("a", "Payment options"), ("b", "Moving house"), ("c", "Payment options")]:
        (tmp_path / f"{name}.md").write_text(f"---\ntitle: {title}\n---\n\nText.\n")
    path = tmp_path / "titles.txt"

    assert scenarios.write_site_titles(tmp_path, path) == 2
    assert scenarios.site_titles(path) == {"Payment options", "Moving house"}
    assert path.read_text().startswith("#")


def test_duplicate_ids_are_rejected(tmp_path):
    (tmp_path / "a.yaml").write_text(
        "- {id: x, description: d, turns: [{user: u, ideal: i, expect: {behaviour: answer}}]}"
    )
    (tmp_path / "b.yaml").write_text((tmp_path / "a.yaml").read_text())

    with pytest.raises(ValueError, match="duplicate scenario IDs: x"):
        scenarios.load(tmp_path)


def test_an_unknown_expectation_is_rejected():
    with pytest.raises(ValueError):
        Expect(behaviour="answer", mention=["typo"])


def test_behaviour_is_skipped_until_the_service_reports_it():
    expect = Expect(behaviour="emergency")

    assert _statuses(expect, {"answer": "x"})["behaviour"] == ["skip"]
    assert _statuses(expect, {"answer": "x", "behaviour": "emergency"})["behaviour"] == ["pass"]
    assert _statuses(expect, {"answer": "x", "behaviour": "answer"})["behaviour"] == ["fail"]


def test_sources_mentions_and_avoids():
    response = {
        "answer": "Use the Biller Code on your bill.",
        "sources": [{"source": "Ways to pay your bill", "content": "..."}],
    }
    expect = Expect(
        behaviour="answer",
        sources=["Ways to pay your bill"],
        mentions=["biller code", ["post office", "bill"], "direct debit"],
        avoids=["1234"],
    )

    statuses = _statuses(expect, response)

    assert statuses["sources"] == ["pass"]
    assert statuses["mentions"] == ["pass", "pass", "fail"]
    assert statuses["avoids"] == ["pass"]
    assert _statuses(Expect(behaviour="decline", sources=[]), response)["sources"] == ["fail"]
    assert _statuses(Expect(behaviour="answer", sources=["Moving house"]), response)["sources"] == ["fail"]


def test_mentions_match_typographic_hyphens_quotes_and_spaces():
    expect = Expect(behaviour="answer", mentions=["follow-up", "you'll", "13 12 34"])
    answer = "A follow\u2011up call: you\u2019ll get one. Call 13\u202f12\u202f34."

    assert _statuses(expect, {"answer": answer})["mentions"] == ["pass", "pass", "pass"]


@pytest.mark.parametrize(
    "answer",
    [
        "Call us on 13 22 34.",
        "Call 1300 555 123.",
        "Ring (03) 9876 5432.",
        "Text 0412 345 678.",
        "The fee is $45.50.",
        "Plans start at $1,200 a year.",
    ],
)
def test_numbers_and_prices_not_in_the_sources_fail_grounding(answer):
    assert _statuses(Expect(behaviour="answer"), {"answer": answer, "sources": []})["grounded"] == ["fail"]


def test_numbers_in_the_sources_or_allowed_pass_grounding():
    sources = [{"source": "Contact us", "content": "Call 1300-555-123 or 000 in an emergency."}]

    grounded = _statuses(Expect(behaviour="answer"), {"answer": "Call 1300 555 123, or 000.", "sources": sources})
    allowed = _statuses(
        Expect(behaviour="emergency", allowed_numbers=["131 245"]), {"answer": "Call 13 12 45.", "sources": []}
    )

    assert grounded["grounded"] == allowed["grounded"] == ["pass"]


@pytest.mark.parametrize(
    ("answer", "status"),
    [
        ("| Method | Where |\n|--------|-------|\n| Online | App |", "fail"),
        ("Method | Where\n--- | ---\nOnline | App", "fail"),
        ("## Ways to pay\nBy BPAY.", "fail"),
        ("You can pay:\n- by BPAY\n- by direct debit\n\nCall 000 if anyone is in danger.", "pass"),
    ],
)
def test_tables_and_headings_fail_the_format_check(answer, status):
    assert _statuses(Expect(behaviour="answer"), {"answer": answer})["format"] == [status]


def test_every_scenario_runs_through_the_service(fakes):
    def ask(question, thread_id):
        return svc.answer(question, thread_id=thread_id)

    for scenario in scenarios.load():
        result = scenarios.run(scenario, ask)

        assert [t.error for t in result.turns] == [None] * len(scenario.turns), scenario.id
        assert all(t.checks for t in result.turns)

    # One thread per scenario: the longest conversation reached the model whole.
    longest = max(len(s.turns) for s in scenarios.load())
    assert max(len([m for m in call if m.type == "human"]) for call in fakes.llm.calls) == longest


def test_a_failed_turn_ends_the_scenario():
    scenario = scenarios.load()[0]

    def ask(question, thread_id):
        raise RuntimeError("HTTP 502")

    result = scenarios.run(scenario, ask)

    assert len(result.turns) == 1
    assert result.turns[0].error == "RuntimeError: HTTP 502"
    assert not result.passed
