# Tools and multiple agents

**Status: design, not yet proven on a project.** ea-customer-bot answers from content only. Treat
this as the starting design, validate it on the first project that needs tools, and update this
file with what changed (and the AD numbers).

## Decide first (record as ADs)

- **Does it need a model to choose tools?** If the intent can be classified by rules or a cheap
  classifier, route to a fixed node that calls the tool deterministically. A tool-choosing model
  call costs latency on every turn (principle 10).
- **One agent or several?** Start with one graph. Split into agents only when prompts or tool sets
  conflict (different personas, permissions or models), and record why.
- **Read or write tools?** Write tools (bookings, changes, payments, messages) need authenticated
  users, confirmation and audit: plan the identity provider in Phase 0.

## Tools as a seam

```
src/<pkg>/tools/
  __init__.py      ToolSet: the registry; get_tools(settings) picks fake/local/cloud per tool
  <system>.py      one module per external system: Protocol, local stub, real client
```

- Each tool has a typed input schema (pydantic), a docstring the model reads, a timeout, and a
  small result type. Errors become a result the model can explain ("the booking system is
  unavailable"), never a stack trace.
- **Identity comes from the server, never the model.** The user ID, account and tenant come from
  the verified token in the graph's `config["configurable"]`; the model supplies only the
  parameters. A tool refuses an account the user does not own.
- **Allow-list per node or agent**: bind only the tools that node may call
  (`llm.bind_tools(subset)`).
- **Write actions need confirmation:** the graph pauses with LangGraph `interrupt()`, the UI shows
  what will happen (a `confirm` stream event), and the action runs only on an explicit yes.
  Idempotency key per action; audit log (who, what, when, result, trace ID), without PII.
- **Tool output is untrusted data.** It can carry prompt injection (emails, documents, web pages).
  Pass it as data in a clearly labelled block, never as instructions; run the guardrail on
  external text; never let tool output choose the next tool's identity parameters.
- **Caps:** at most N tool calls per turn, and a total time budget; then answer with what you
  have.
- **Local first:** each system has a local stub (fixture data or a mock server in Compose) so the
  stack runs without credentials or VPN. Real clients are a setting.
- **MCP:** tools exposed through an MCP server (or the cloud's gateway) are still behind the tool
  seam; the service never imports an MCP client directly.

## Graph shapes

**Router + tool node** (prefer): rules, then a router (rules or a small model) choose a path;
each path calls its tools in a `ToolNode` and ends in one answering node that streams.

```
route ─▶ plan (model picks tools from the allow-list) ─▶ tools (ToolNode) ─▶ plan … ─▶ generate ─▶ END
                                                     (max N loops)
```

**Supervisor + sub-agents** (when justified): each sub-agent is a compiled subgraph with its own
prompt (in Langfuse), its own tools and possibly its own model. A supervisor node routes by
writing `next_agent` into state; sub-agents return structured results, not chat. Only the final
answering node streams tokens to the user.

- Cap hops between agents (state counter) to stop loops.
- Pass sub-agents only the context they need, not the whole conversation.
- Keep safety rules in `route` before the supervisor; sub-agents inherit them.

## Streaming and the contract

- Only the answering node's model output becomes `token` events. `_StreamedTurn` matches on node
  names: a model call in another node must never reach the user, so give every model call its own
  node.
- New events: `status` with the tool's display name ("Checking your booking…"); `confirm` for
  write actions; optionally `tool_result` for UI cards. Update all consumers (see
  `architecture.md`).

## Tracing

- Each tool call is an observation (name, masked arguments, latency, outcome); each sub-agent a
  span named after it. LangGraph's callback handler does most of this; check that the session and
  trace name propagate into subgraphs (run the turn inside `tracing.attributes`).
- Scores: tool errors per turn, confirmations declined, hops used.

## Testing

- Fake tools in `tests/fakes.py` that record calls. Assert on the calls, not on the model's words.
- Scenarios gain `tools:` expectations per turn (called, not called, arguments), and a check that
  write tools never run without confirmation.
- A test that a tool refuses an identifier not owned by the user.

## Cloud notes

- AWS: AgentCore Gateway (tools as MCP targets) and AgentCore Identity (outbound OAuth to the
  client's systems). See `ai-project-aws`.
- Azure: Azure API Management in front of tools, managed identity to Azure resources, Entra ID
  for users. See `ai-project-azure`.
