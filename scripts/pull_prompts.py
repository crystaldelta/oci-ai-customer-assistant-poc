"""Copy the Langfuse prompts into the repo files, the service's fallback (AD-11).

Prompts are edited in Langfuse. At the start of each release, before building
the images, run this, review `git diff src/assistant/prompts/` and commit
it, so the image's fallback is what is live. It writes each `PROMPT_LABEL`
(`production`) version to `src/assistant/prompts/<name>.md` and records
the version numbers in `versions.json`, which the service compares with
Langfuse to report a stale fallback. A version that fails the service's checks
(`prompts.problem`) is refused, and the file left as it is.

Usage (needs LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_HOST):
    python scripts/pull_prompts.py           # write the Langfuse versions
    python scripts/pull_prompts.py --check   # write nothing; exit 1 if any differ
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assistant import prompts, tracing  # noqa: E402
from assistant.config import settings  # noqa: E402


def fetch(client, name: str, label: str):
    """The labelled Langfuse version of a prompt, uncached; None if Langfuse lacks it."""
    from langfuse.api import NotFoundError

    try:
        return client.get_prompt(prompts.LANGFUSE_PREFIX + name, label=label, cache_ttl_seconds=0, max_retries=0)
    except NotFoundError:
        return None


def pull(client, prompts_dir: Path, label: str, *, check: bool = False, out=print) -> int:
    versions = prompts.read_versions(prompts_dir)
    pulled, differs, failed = {}, [], []
    for name in prompts.NAMES:
        fetched = fetch(client, name, label)
        if fetched is None:
            out(f"missing    {name}: not in Langfuse with label {label!r}; run scripts/push_prompts.py")
            failed.append(name)
            continue
        text, version = fetched.prompt.strip(), fetched.version
        reason = prompts.problem(name, text)
        if reason:
            out(f"refused    {name} v{version}: {reason}; fix it in Langfuse")
            failed.append(name)
            continue
        if text == prompts.read_file(prompts_dir, name) and versions.get(name) == version:
            out(f"unchanged  {name} v{version}")
            continue
        differs.append(name)
        if check:
            out(f"differs    {name}: Langfuse v{version}, repo v{versions.get(name, 'unknown')}")
            continue
        (prompts_dir / f"{name}.md").write_text(text + "\n", encoding="utf-8")
        pulled[name] = version
        out(f"pulled     {name} v{version}")
    if pulled:
        prompts.write_versions(prompts_dir, {**versions, **pulled})
    if check and differs:
        out("The repo files differ from Langfuse: run scripts/pull_prompts.py, review the diff and commit it.")
    return 1 if failed or (check and differs) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 if any prompt differs")
    args = parser.parse_args()
    return pull(tracing.client(settings), settings.prompts_dir, settings.prompt_label, check=args.check)


if __name__ == "__main__":
    sys.exit(main())
