"""`scripts/new_project.py` on a copy of the starter in a temporary folder. No network.

The full check, pytest passing in the renamed copy, is in the README ("Start a new project");
this test checks what the script changes and what it must leave alone.
"""

import shutil

from assistant import config
from scripts import new_project

IGNORE = shutil.ignore_patterns(".git", ".venv", "node_modules", "dist", "data", "__pycache__", "*.egg-info")


def _copy(tmp_path):
    root = tmp_path / "copy"
    shutil.copytree(config.ROOT, root, ignore=IGNORE)
    return root


def _run(root, *extra, safety="yes"):
    return new_project.main(
        [
            "--safety-rules",
            safety,
            "--root",
            str(root),
            "--project",
            "acme-helper",
            "--package",
            "acme_helper",
            "--client",
            'Acme "Services"',
            "--link-prefix",
            "https://www.acme.example/",
            *extra,
        ]
    )


def test_the_package_project_brand_and_link_prefix_are_renamed(tmp_path, capsys):
    root = _copy(tmp_path)

    assert _run(root) == 0

    assert not (root / "src" / "assistant").exists()
    package = root / "src" / "acme_helper"
    assert (package / "service.py").is_file()
    service = (package / "service.py").read_text()
    assert "from acme_helper import config" in service and "from assistant" not in service
    assert 'TRACE_TAGS = ["acme-helper"]' in service
    assert "acme_helper.api:app" in (root / "Dockerfile").read_text()
    compose = (root / "docker-compose.yml").read_text()
    assert "${COMPOSE_PROJECT_NAME:-acme-helper}" in compose
    assert "LANGFUSE_INIT_PROJECT_NAME: acme-helper" in compose
    assert "acme_helper.ingest" in compose
    pyproject = (root / "pyproject.toml").read_text()
    assert 'name = "acme-helper"' in pyproject and 'acme_helper = ["prompts/*.md"' in pyproject
    assert 'LANGFUSE_PREFIX = "acme-helper/"' in (package / "prompts" / "__init__.py").read_text()
    brand = (root / "frontend" / "src" / "brand.ts").read_text()
    assert 'name: "Acme \\"Services\\""' in brand and 'linkPrefix: "https://www.acme.example/"' in brand
    assert 'allowed_link_prefix: str = "https://www.acme.example/"' in (package / "config.py").read_text()
    assert "next steps" in capsys.readouterr().out.lower()


def test_the_english_word_and_the_test_fixtures_are_left_alone(tmp_path):
    root = _copy(tmp_path)

    _run(root)

    # "assistant" as a word, not the package.
    assert "the assistant is expected to do" in (root / "evals" / "scenarios.py").read_text()
    assert 'role: "user" | "assistant"' in (root / "frontend" / "src" / "App.tsx").read_text()
    # The tests keep their synthetic link prefix.
    assert '"ALLOWED_LINK_PREFIX": "https://example.invalid/"' in (root / "tests" / "conftest.py").read_text()


def test_it_runs_once_and_then_deletes_itself(tmp_path, capsys):
    root = _copy(tmp_path)

    _run(root)

    assert not (root / "scripts" / "new_project.py").exists()
    assert not (root / "tests" / "test_new_project.py").exists()


def test_a_dry_run_changes_nothing(tmp_path, capsys):
    root = _copy(tmp_path)
    before = (root / "src" / "assistant" / "service.py").read_text()

    assert _run(root, "--dry-run") == 0

    assert (root / "src" / "assistant" / "service.py").read_text() == before
    assert (root / "scripts" / "new_project.py").exists()
    assert "Would change" in capsys.readouterr().out


def test_with_safety_rules_they_stay_on(tmp_path, capsys):
    root = _copy(tmp_path)

    _run(root)

    assert "safety_rules: bool = True" in (root / "src" / "acme_helper" / "config.py").read_text()
    assert (root / "evals" / "scenarios" / "emergency.yaml").exists()
    assert "approved wording" in capsys.readouterr().out


def test_without_safety_rules_they_are_off_and_their_scenarios_go(tmp_path, capsys):
    root = _copy(tmp_path)

    _run(root, safety="no")

    assert "safety_rules: bool = False" in (root / "src" / "acme_helper" / "config.py").read_text()
    assert "# SAFETY_RULES=false" in (root / ".env.example").read_text()
    assert not (root / "evals" / "scenarios" / "emergency.yaml").exists()
    # The mechanism and its tests stay, so the rules can be turned on later.
    assert (root / "src" / "acme_helper" / "rules.py").exists()
    assert '"SAFETY_RULES": "true"' in (root / "tests" / "conftest.py").read_text()
    assert "Safety rules are off" in capsys.readouterr().out
