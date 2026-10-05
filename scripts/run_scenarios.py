"""Run the behaviour scenarios in evals/scenarios/ and report each turn's checks.

Each scenario is one conversation. By default it goes through the API
(/api/ask), so thread IDs, memory and the limits are exercised as a caller
sees them; `--target service` calls the service in-process instead, with the
host URLs from `.env`. Either way Ollama and Qdrant must be running.

Local runs check function, not answer quality (principle 4): a turn passes on
its deterministic checks only. Exits 1 if any check fails.

Usage (from the project root):
    python scripts/run_scenarios.py
    python scripts/run_scenarios.py --only someone-hurt --only weather -v
    python scripts/run_scenarios.py --api http://localhost:8100   # a stack on another port
    python scripts/run_scenarios.py --write-titles   # after the content changes
"""

import argparse
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from assistant.config import settings  # noqa: E402
from evals import scenarios  # noqa: E402

MARKS = {"pass": "✓", "fail": "✗", "skip": "–"}


def api_ask(client: httpx.Client, api: str):
    def ask(question: str, thread_id: str | None) -> dict:
        payload = {"question": question} | ({"thread_id": thread_id} if thread_id else {})
        while True:
            res = client.post(f"{api}/api/ask", json=payload, timeout=None)
            if res.status_code != 429:
                break
            wait = int(res.headers.get("Retry-After", "5"))
            print(f"  (rate limited; waiting {wait} s)", file=sys.stderr)
            time.sleep(wait)
        if res.is_error:
            raise RuntimeError(f"HTTP {res.status_code}: {res.text[:200]}")
        return res.json()

    return ask


def service_ask(question: str, thread_id: str | None) -> dict:
    from assistant import service

    return service.answer(question, thread_id=thread_id)


def report(result: scenarios.ScenarioResult, verbose: bool) -> None:
    scenario = result.scenario
    print(f"\n{'PASS' if result.passed else 'FAIL'} {scenario.category}/{scenario.id}")
    for n, turn in enumerate(result.turns, 1):
        print(f"  {n}. {turn.user}")
        if turn.error:
            print(f"     error: {turn.error}")
            continue
        for check in turn.checks:
            if verbose or check.status == "fail":
                print(f"     {MARKS[check.status]} {check.name} {check.detail}".rstrip())
        if verbose or not turn.passed:
            print("     > " + turn.answer.replace("\n", "\n       "))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--target", choices=["api", "service"], default="api")
    parser.add_argument("--only", action="append", metavar="ID", help="run only this scenario (repeatable)")
    parser.add_argument("-v", "--verbose", action="store_true", help="show every check and answer")
    parser.add_argument(
        "--write-titles", action="store_true", help="refresh evals/site_titles.txt from CONTENT_DIR, then exit"
    )
    args = parser.parse_args()

    if args.write_titles:
        count = scenarios.write_site_titles(settings.content_dir)
        print(f"Wrote {count} titles to {scenarios.SITE_TITLES}.")
        return 0

    selected = [s for s in scenarios.load() if not args.only or s.id in args.only]
    if not selected:
        print("No scenarios match.", file=sys.stderr)
        return 1

    with httpx.Client() as client:
        ask = api_ask(client, args.api) if args.target == "api" else service_ask
        results = []
        for scenario in selected:
            result = scenarios.run(scenario, ask)
            report(result, args.verbose)
            results.append(result)

    counts = {status: 0 for status in MARKS}
    for result in results:
        for turn in result.turns:
            for check in turn.checks:
                counts[check.status] += 1
    passed = sum(r.passed for r in results)
    print(
        f"\n{passed}/{len(results)} scenarios passed. Checks: {counts['pass']} passed, "
        f"{counts['fail']} failed, {counts['skip']} skipped."
    )
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
