# Pitfalls hit on ea-customer-bot

Each cost real time. Check this list before debugging and when reviewing.

| Symptom | Cause | Rule |
| --- | --- | --- |
| Every connection refused inside Docker | `load_dotenv(override=True)`: `.env` host URLs beat Compose's container URLs | `override=False`; container URLs in Compose `environment:` |
| A container cannot reach Ollama or another host service | `localhost` inside a container is the container | `host.docker.internal` (+ `extra_hosts` on Linux) |
| First question hangs forever: no error, no CPU | `threading.Lock` in nested lazy getters | `RLock`; test `get_agent()` on a thread with a timeout |
| Stream fails on the checkpointer | `SqliteSaver` refuses the async API | A checkpointer serving sync and async (`ThreadedSqliteSaver`); test both |
| Reasoning text or a list of blocks shown as the answer | `.content` on Converse-style replies; inline `<think>` | `.text`; keep `ReasoningFilter` |
| Statuses or tokens silently vanish | A node renamed without updating the stream matcher | Node names as constants; contract test |
| An internal model call's output reaches the user | A second model call inside the answering node | Every model call in its own node |
| Retrieval returns nonsense after a model change | Embedding model and collection are dimension-locked | Record the model with the index; refuse a mismatch |
| Python aborts at exit (134) on macOS after tests pass | onnxruntime telemetry thread | `ORT_DISABLE_TELEMETRY=1` in package `__init__`, Dockerfile, conftest |
| Image several GB larger | torch pulled in transitively | Build fails if torch is installed |
| Lock from macOS breaks the Linux image | Platform-specific resolution | `uv pip compile --universal` |
| A developer's `.env` changes test results | Settings read at import | conftest pins the environment first |
| Embedded index locked | One client per process for an embedded Qdrant path | Nothing else opens it while the retriever holds it |
| Traces 15 minutes late on Langfuse Cloud | SDK v3 | SDK v4 |
| Generations have no session (no cost per session) | v4 handler sets attributes on the root only when streaming | `propagate_attributes` around each turn |
| Guardrail observations in a separate trace | The node's span is not current on the stream | Find the parent through the handler's run map |
| Model refuses the temperature | Some reasoning models accept only their default | Temperature setting unset by default |
| Production down after a config change | A setting enabled before the image that understands it was deployed | Deploy the image first, then the setting |
| Wrong image deployed | Tag did not match the commit | Tag with the commit SHA from `git rev-parse` at build time |
| A cloud resource disappears after a Terraform apply | Made by hand outside Terraform | Record it in PLAN.md and hand DevOps the definition to import |
| A content release breaks answers | The index changed shape before the agent that reads it was deployed | Deploy the agent before republishing the index |
| Prompt edit in the repo had no effect on the cloud | Langfuse serves the prompts | Edit in Langfuse or push with `--force` |
| Local Langfuse will not start | An image no longer pullable (MinIO) | Pin by digest; check the profile after upgrades |
| Shadowed variable broke a node | A local named `config` hid the `RunnableConfig` parameter | Lint with ruff; avoid reusing `config` as a name |
