"""Set up a Langfuse project for this assistant: score configs and the health dashboard.

Reads what to create from `deploy/langfuse-setup.json` and the project's keys from the
environment or `.env` (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`). Safe to run
again: it creates only what is missing, matched by name, and changes nothing that exists.
Prompts are seeded separately by `scripts/push_prompts.py`. Steps the API does not cover are
printed at the end.

    .venv/bin/python scripts/setup_langfuse.py --dry-run   # show what it would create
    .venv/bin/python scripts/setup_langfuse.py

Dashboards use Langfuse's unstable dashboard API; if the server lacks it, the dashboard is
skipped with a warning and the score configs are still created.
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SETUP_FILE = ROOT / "deploy" / "langfuse-setup.json"


class Langfuse:
    def __init__(self, host: str, public_key: str, secret_key: str) -> None:
        self.base = host.rstrip("/") + "/api/public"
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=self.headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read() or b"{}")

    def list_all(self, path: str) -> list[dict]:
        items, page = [], 1
        while True:
            sep = "&" if "?" in path else "?"
            result = self.request("GET", f"{path}{sep}page={page}&limit=50")
            items += result.get("data", [])
            if page >= result.get("meta", {}).get("totalPages", 1):
                return items
            page += 1


def setup_score_configs(lf: Langfuse, configs: list[dict], dry_run: bool) -> None:
    existing = {c["name"]: c for c in lf.list_all("/score-configs")}
    for config in configs:
        name = config["name"]
        if name in existing:
            if existing[name].get("dataType") != config["dataType"]:
                print(f"  ! score config {name}: exists as {existing[name].get('dataType')}, not {config['dataType']}")
            else:
                print(f"  = score config {name}")
            continue
        print(f"  + score config {name} ({config['dataType']})")
        if not dry_run:
            lf.request("POST", "/score-configs", config)


def setup_dashboard(lf: Langfuse, dashboard: dict, dry_run: bool) -> None:
    name = dashboard["name"]
    try:
        existing = {d["name"]: d for d in lf.list_all("/unstable/dashboards")}
    except urllib.error.HTTPError as error:
        print(f"  ! dashboard {name}: skipped, the server has no dashboard API ({error.code})")
        return
    if name in existing:
        print(f"  = dashboard {name} (left as it is; delete it in Langfuse to recreate)")
        return
    print(f"  + dashboard {name}, {len(dashboard['widgets'])} widgets")
    if dry_run:
        return
    created = lf.request(
        "POST", "/unstable/dashboards", {"name": name, "description": dashboard.get("description", "")}
    )
    for i, widget in enumerate(dashboard["widgets"]):
        made = lf.request("POST", "/unstable/dashboard-widgets", widget)
        # A 12-column grid: two 6x6 tiles per row.
        placement = {
            "type": "widget",
            "widgetId": made["id"],
            "x": (i % 2) * 6,
            "y": (i // 2) * 6,
            "width": 6,
            "height": 6,
        }
        lf.request("POST", f"/unstable/dashboards/{created['id']}/placements", placement)
        print(f"    + {widget['name']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="show what would be created")
    parser.add_argument("--file", type=Path, default=SETUP_FILE, help="the setup definition")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env", override=False)
    public_key, secret_key = os.environ.get("LANGFUSE_PUBLIC_KEY"), os.environ.get("LANGFUSE_SECRET_KEY")
    host = os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
    if not (public_key and secret_key):
        print("Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY (environment or .env) for the target project.")
        return 2

    setup = json.loads(args.file.read_text())
    lf = Langfuse(host, public_key, secret_key)
    print(f"Langfuse project at {host}{' (dry run)' if args.dry_run else ''}")
    try:
        print("Score configs")
        setup_score_configs(lf, setup.get("score_configs", []), args.dry_run)
        print("Dashboards")
        for dashboard in setup.get("dashboards", []):
            setup_dashboard(lf, dashboard, args.dry_run)
    except urllib.error.HTTPError as error:
        print(f"Langfuse refused a request: {error.code} {error.read().decode(errors='replace')[:300]}")
        return 1
    except urllib.error.URLError as error:
        print(f"Langfuse is unreachable at {host}: {error.reason}")
        return 1

    print("\nStill to do by hand:")
    for step in setup.get("manual_steps", []):
        print(f"  - {step}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
