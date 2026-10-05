"""Terminal client for multi-turn conversations against the API.

Streams answers from /api/ask/stream and keeps the thread ID between turns, so
follow-up questions have context. Only needs httpx.

Usage (from the project root, with the API running):
    python scripts/chat.py
    python scripts/chat.py --api http://localhost:8000 "How can I pay my bill?"
    python scripts/chat.py --agentcore http://localhost:8080

--agentcore talks to the AgentCore entry point (/invocations) instead of the
API; the conversation is a session ID this client makes up, as AgentCore's
callers do.
Type /new to start a new conversation, /quit (or Ctrl-D) to exit.
"""

import argparse
import json
import sys
import uuid

import httpx

SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"


def new_session_id() -> str:
    """An AgentCore session ID: at least 33 characters."""
    return f"chat-{uuid.uuid4().hex}"


def stream_turn(
    client: httpx.Client,
    api: str,
    question: str,
    thread_id: str | None,
    agentcore: bool = False,
) -> str | None:
    """Send one question, print the streamed answer and sources, and return the thread ID.

    With `agentcore`, `api` is the entry point and `thread_id` the session ID.
    """
    payload: dict = {"question": question}
    if thread_id and not agentcore:
        payload["thread_id"] = thread_id
    url, headers = f"{api}/api/ask/stream", {}
    if agentcore:
        url, headers = f"{api}/invocations", {SESSION_HEADER: thread_id or new_session_id()}
    sources: list[dict] = []
    with client.stream("POST", url, json=payload, headers=headers, timeout=None) as res:
        if res.is_error:
            # Refusals (rate limit, turn limit, bad thread) carry a readable detail.
            res.read()
            try:
                body = res.json()
                detail = body.get("detail") or body.get("error") or res.text
            except ValueError:
                detail = res.text
            print(f"[{res.status_code}] {detail}", file=sys.stderr)
            return thread_id
        for line in res.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[len("data: ") :])
            kind = event.get("type")
            if kind == "start":
                thread_id = event.get("thread_id", thread_id)
            elif kind == "status":
                print(f"\033[2m[{event.get('stage')}]\033[0m", file=sys.stderr)
            elif kind == "token":
                print(event.get("text", ""), end="", flush=True)
            elif kind == "replace":
                # The guardrail blocked the answer above (AD-18); a browser replaces it.
                print("\n\033[2m[answer withdrawn by the guardrail]\033[0m", file=sys.stderr)
                print(event.get("text", ""), end="", flush=True)
            elif kind == "sources":
                sources = event.get("sources", [])
            elif kind == "error":
                print(f"\n[error] {event.get('message')}", file=sys.stderr)
            elif kind == "done":
                print()
    print_sources(sources)
    return thread_id


def print_sources(sources: list[dict]) -> None:
    """One line per page, numbered as the answer cites it ("[1]")."""
    pages: dict[int, dict] = {}
    for s in sources:
        pages.setdefault(s["page"], s)
    if pages:
        lines = ("  ".join(filter(None, [f"[{n}]", s["source"], s.get("url")])) for n, s in sorted(pages.items()))
        print("\033[2mSources:\n  " + "\n  ".join(lines) + "\033[0m")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--agentcore", metavar="URL", help="AgentCore entry point URL, used instead of the API")
    parser.add_argument("question", nargs="*", help="ask one question and exit")
    args = parser.parse_args()

    api, agentcore = args.agentcore or args.api, bool(args.agentcore)
    thread_id = None
    with httpx.Client() as client:
        if args.question:
            stream_turn(client, api, " ".join(args.question), thread_id, agentcore)
            return 0

        print("Ask a question. /new starts a new conversation, /quit exits.")
        while True:
            try:
                question = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not question:
                continue
            if question in ("/quit", "/exit"):
                return 0
            if question == "/new":
                thread_id = None
                print("Started a new conversation.")
                continue
            try:
                thread_id = stream_turn(client, api, question, thread_id, agentcore)
            except httpx.HTTPError as exc:
                print(f"[error] {exc}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
