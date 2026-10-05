"""Turn a copy of the starter into a new project: rename the package, the project and the brand.

Run it once, in the copy, before anything else:

    cp -R ai-starter acme-helper && cd acme-helper && rm -rf .git .venv frontend/node_modules
    python3 scripts/new_project.py --project acme-helper --package acme_helper \\
        --client "Acme Services" --link-prefix https://www.acme.example/

It changes, in every text file in the copy:

- the package `assistant` (`src/assistant/`, imports, `python -m assistant.…`, the Dockerfile and
  Compose commands, `pyproject.toml`) to `--package`;
- the project name `ai-starter` (pyproject, the Compose project, image tags, the local Langfuse
  project, the Langfuse prompt prefix, trace tags and log names, the front end's package) to
  `--project`;
- the client's name in `frontend/src/brand.ts` to `--client`, and the product line to
  `--product` if given;
- the safety rules (`--safety-rules yes|no`, required, so the choice is made with the client):
  `no` turns off the danger rules (`safety_rules` in `config.py`, `SAFETY_RULES` in
  `.env.example`) and deletes the emergency scenarios. The mechanism and its tests stay, so it
  can be turned on later with `SAFETY_RULES=true`;
- the citation link prefix (`ALLOWED_LINK_PREFIX` in `config.py` and `.env.example`, and
  `linkPrefix` in `brand.ts`) to `--link-prefix`. The tests keep `example.invalid`: their
  fixtures are synthetic.

It is one-shot: once done it deletes itself and its test, and prints the next steps.
`--dry-run` lists the files it would change and changes nothing.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PACKAGE, PROJECT = "assistant", "ai-starter"
CLIENT, LINK_PREFIX = "Example Co", "https://example.invalid/"

# Never walked: generated, installed or version-control folders, and the skills (`.claude/`), which
# name the starter itself.
SKIP_DIRS = {".git", ".venv", "node_modules", "dist", "__pycache__", ".pytest_cache", ".ruff_cache", "data", ".claude"}
TEXT_SUFFIXES = {".py", ".md", ".toml", ".txt", ".yml", ".yaml", ".json", ".ts", ".tsx", ".js", ".css", ".html", ".in"}
TEXT_NAMES = {"Dockerfile", ".env.example", ".gitignore", ".dockerignore"}
# Deleted with --safety-rules no: they expect the emergency reply.
SAFETY_SCENARIOS = Path("evals/scenarios/emergency.yaml")
# This script and its test: left as they are, then deleted.
ONE_SHOT = (Path("scripts/new_project.py"), Path("tests/test_new_project.py"))


def package_patterns(new: str) -> list[tuple[re.Pattern, str]]:
    """Where the package name is code, not the English word "assistant"."""
    return [
        (re.compile(r"\b(from|import) assistant\b"), rf"\1 {new}"),
        (re.compile(r"(?<![\w-])assistant\.(?=[a-z_])"), f"{new}."),  # assistant.api, -m assistant.publish
        (re.compile(r"(?<![\w-])assistant/"), f"{new}/"),  # src/assistant/, assistant/__init__.py
        (re.compile(r"`assistant`"), f"`{new}`"),
        (re.compile(r"(?m)^assistant = \["), f"{new} = ["),  # pyproject package-data
    ]


def text_files(root: Path):
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if rel in ONE_SHOT or any(part in SKIP_DIRS or part.endswith(".egg-info") for part in rel.parts):
            continue
        if path.is_file() and (path.suffix in TEXT_SUFFIXES or path.name in TEXT_NAMES):
            yield path


def rename(text: str, path: Path, args) -> str:
    for pattern, replacement in package_patterns(args.package):
        text = pattern.sub(replacement, text)
    text = text.replace(PROJECT, args.project)
    rel = path.as_posix()
    if rel.endswith("frontend/src/brand.ts"):
        text = text.replace(f'name: "{CLIENT}"', f"name: {_ts(args.client)}")
        if args.product:
            text = re.sub(r'product: "[^"]*"', f"product: {_ts(args.product)}", text)
    if args.safety_rules == "no":
        if rel.endswith("/config.py") and "/tests/" not in rel:
            text = text.replace("safety_rules: bool = True", "safety_rules: bool = False")
        if rel.endswith(".env.example"):
            text = text.replace("# SAFETY_RULES=true", "# SAFETY_RULES=false")
    if args.link_prefix and (
        rel.endswith(("frontend/src/brand.ts", "/config.py", ".env.example")) and "/tests/" not in rel
    ):
        text = text.replace(LINK_PREFIX, args.link_prefix)
    return text


def _ts(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True, help="project name, lower case with hyphens: acme-helper")
    parser.add_argument("--package", required=True, help="Python package, lower case with underscores: acme_helper")
    parser.add_argument("--client", required=True, help='the client\'s name as the page shows it: "Acme Services"')
    parser.add_argument("--product", help='the line under the name on the page: "Customer assistant"')
    parser.add_argument("--link-prefix", help="the client's site, for citation links: https://www.acme.example/")
    parser.add_argument(
        "--safety-rules",
        required=True,
        choices=("yes", "no"),
        help="does the assistant meet safety-critical situations (someone in danger)? Agree it with the client",
    )
    parser.add_argument("--root", type=Path, default=ROOT, help="the copy to change (default: this repo)")
    parser.add_argument("--dry-run", action="store_true", help="list what would change; change nothing")
    args = parser.parse_args(argv)

    if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", args.project):
        parser.error("--project must be lower case letters, digits and single hyphens")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", args.package) or args.package == PACKAGE:
        parser.error("--package must be a new lower-case Python identifier")
    if args.link_prefix and not re.fullmatch(r"https://[^/\s]+/.*", args.link_prefix):
        parser.error("--link-prefix must start with https:// and include the path's slash: https://www.acme.example/")
    root = args.root.resolve()
    src = root / "src" / PACKAGE
    if not src.is_dir():
        parser.error(f"no {src.relative_to(root)}: already renamed, or not a copy of the starter")

    changed = []
    for path in text_files(root):
        text = path.read_text(encoding="utf-8")
        new = rename(text, path.relative_to(root), args)
        if new != text:
            changed.append(path.relative_to(root))
            if not args.dry_run:
                path.write_text(new, encoding="utf-8")
    if not args.dry_run:
        src.rename(root / "src" / args.package)
        for path in ONE_SHOT:
            (root / path).unlink(missing_ok=True)
        if args.safety_rules == "no":
            (root / SAFETY_SCENARIOS).unlink(missing_ok=True)

    verb = "Would change" if args.dry_run else "Changed"
    print(f"{verb} {len(changed)} files, and src/{PACKAGE}/ to src/{args.package}/.")
    if not args.dry_run:
        print("Deleted scripts/new_project.py and its test: they are for the starter only.")
    if args.dry_run:
        print("\n".join(f"  {p}" for p in changed))
        return 0
    if args.safety_rules == "yes":
        safety_step = (
            f"Put the client's safety rules in src/{args.package}/rules.py, with tests/test_rules.py, and\n"
            f"     their approved wording in src/{args.package}/prompts/emergency.md (it must keep 000;\n"
            "     docs/templates/safety-wording-approval.md). The starter's rules and wording are placeholders."
        )
    else:
        safety_step = (
            "Safety rules are off (SAFETY_RULES=false). Record the decision, with the client's agreement,\n"
            "     in docs/ARCHITECTURE.md; turn them on with SAFETY_RULES=true if that changes."
        )
    print(
        f"""
Next steps (see README, "Start a new project"):
  1. git init -b main, then commit this as the starting point.
  2. python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt -e .
     .venv/bin/pytest -q    # passes on the synthetic fixtures before any client content
  3. Fill in docs/PLAN.md (intake answers, Decisions needed) and confirm or supersede each
     decision in docs/ARCHITECTURE.md marked "Inherited from the starter".
  4. {safety_step}
  5. Rewrite the prompts in src/{args.package}/prompts/ for the client's voice and services.
  6. Set the brand: --brand-* in frontend/src/styles.css and the words in frontend/src/brand.ts.
  7. Replace content/ with the client's Markdown (title and url front matter), then refresh
     evals/site_titles.txt (scripts/run_scenarios.py --write-titles), evals/retrieval.yaml and
     the scenarios, and measure RETRIEVAL_MIN_SCORE with scripts/verify_retrieval.py.
  8. docker compose up -d --build, then python scripts/chat.py."""
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
