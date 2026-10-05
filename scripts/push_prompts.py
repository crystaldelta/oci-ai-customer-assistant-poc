"""Seed Langfuse prompt management with the repo's prompt files (AD-11).

Prompts are edited in Langfuse, so by default this only creates the prompts
Langfuse lacks: the first time, and when code adds a prompt. Each is created
with the `PROMPT_LABEL` (`production`) label, and its version is recorded in
`src/assistant/prompts/versions.json`; commit that file.

A prompt that differs from its Langfuse version is left alone, because the
Langfuse one is usually the newer edit (`pull_prompts.py` copies it here).
`--force NAME` replaces it with a new version from the repo, after showing the
difference: for a reviewed repo change that code depends on.

Usage (needs LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_HOST):
    python scripts/push_prompts.py                   # create missing prompts
    python scripts/push_prompts.py --dry-run         # show what would happen
    python scripts/push_prompts.py --force system    # replace one with the repo text
"""

import argparse
import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assistant import prompts, tracing  # noqa: E402
from assistant.config import settings  # noqa: E402

try:
    from scripts.pull_prompts import fetch  # noqa: E402 — imported as a package in the tests
except ImportError:
    from pull_prompts import fetch  # noqa: E402


def push(client, prompts_dir: Path, label: str, *, force=(), dry_run: bool = False, out=print) -> int:
    versions = prompts.read_versions(prompts_dir)
    unknown = set(force) - set(prompts.NAMES)
    if unknown:
        out(f"No such prompt: {', '.join(sorted(unknown))}")
        return 1
    recorded, failed = {}, []
    for name in prompts.NAMES:
        text = prompts.read_file(prompts_dir, name)
        current = fetch(client, name, label)
        if current is not None and current.prompt.strip() == text:
            out(f"unchanged  {name} v{current.version}")
            if versions.get(name) != current.version:
                recorded[name] = current.version
            continue
        if current is not None and name not in force:
            out(f"differs    {name}: Langfuse v{current.version} is kept (pull it, or --force {name})")
            continue
        reason = prompts.problem(name, text)
        if reason:
            out(f"refused    {name}: the repo file fails the checks: {reason}")
            failed.append(name)
            continue
        if current is not None:
            before = current.prompt.strip().splitlines()
            diff = difflib.unified_diff(before, text.splitlines(), f"langfuse v{current.version}", "repo", lineterm="")
            out("\n".join(diff))
        if dry_run:
            out(f"would push {name}")
            continue
        created = client.create_prompt(
            name=prompts.LANGFUSE_PREFIX + name,
            prompt=text,
            labels=[label],
            type="text",
            commit_message="Pushed from the repo by scripts/push_prompts.py",
        )
        recorded[name] = created.version
        out(f"pushed     {name} v{created.version}")
    if recorded and not dry_run:
        prompts.write_versions(prompts_dir, {**versions, **recorded})
    client.flush()
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, without pushing")
    parser.add_argument("--force", nargs="+", default=[], metavar="NAME", help="replace these with the repo text")
    args = parser.parse_args()
    client = tracing.client(settings)
    return push(client, settings.prompts_dir, settings.prompt_label, force=args.force, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
