# OpenCode

Load this file only when a task implements, tests or diagnoses Smart Routing on OpenCode: the [adapter](../runtime/adapters/opencode.py), the routing plugin shim, or a routed OpenCode launch. OpenCode titles have their own [title contract](workstreams-titles.md#opencode-sessions). OpenCode is not an onboarding or startup client, and nothing here enrolls a project or qualifies OpenCode for the workflow.

## What the adapter does

The adapter turns OpenCode payloads into small, typed records for the routing command. It is pure: it reads no files, starts no process and makes no network call. It covers OpenCode 2.2.6, the version the probes ran on.

- `normalize_hook(hook, payload)` covers the four hooks routing uses: `model.request`, `tool.execute.before`, `permission.evaluate` and `shell.create.before`. The plugin passes the hook name because payloads do not carry it. Output includes the session and message IDs, agent, tool and tool class, request kind (`primary`, `title`, `compaction`, `generate`, otherwise `unknown`), the requested model, route identity, and for a subagent call the agent and any requested `provider/model#variant`.
- `normalize_event(event)` covers lineage and usage events: session created (with parent and agent), step started and ended (model, tokens, cost, rate-limit telemetry), usage updated, model selected and execution outcome. Each durable event keeps its per-session `durable_seq` so a gap can be detected. Other event types come back as `kind: "unknown"` with their sequence number and no body.
- `normalize_message(message)` covers stored messages: model, variant, finish, tokens, cost, `native.rateLimit` windows, and subagent tool calls with their child session.
- `probe(capabilities)` classifies already-observed capabilities without launching OpenCode (see below).

What never leaves the adapter: prompts, message and reasoning text, compaction summaries, tool output, command text, and header or environment values. Headers become a list of names, a base URL becomes its origin, and environment variables become names, with likely credential names listed so the shim can strip them. Command text yields only best-effort launch markers such as `opencode`, `claude` or `curl`.

Missing data stays unknown (`None`), never guessed. Field names carry units: times end in `_ms` (epoch milliseconds), rate-limit resets in `_s` (epoch seconds), and utilization is `utilization_fraction` (0–1); a value above 1, such as a percentage, stays unknown. Task, stage and attempt are not in OpenCode payloads; the dispatch record binds them by message ID. Hooks carry no parent session; it comes from the session-created event or a stored subagent call. A malformed, oversized or wrongly typed payload raises `AdapterError` with a fixed reason that never repeats the payload. Session, message, event and tool-call IDs must match their expected patterns.

## Route identity

Route identity is the provider, model alias, requested variant and catalog family. The resolved model is always `unknown`, because OpenCode stores only the requested alias. The harness is `claude-agent-sdk via opencode` for the Claude Agent provider; other providers are named by provider.

## What can be enforced

The [capability matrix](../docs/smart-routing-capability-matrix.md#opencode-enforcement-p01-p05p08) owns which paths can be blocked before sending, which cannot, and the user's decisions about them. `probe` applies those facts to observed capabilities and returns:

- `unsupported`: fixed reason codes, always including the matrix's unsupported paths (`ALWAYS_UNSUPPORTED` in the adapter), plus any that apply to this host.
- `managed_launch: "refuse"` when the `routing.health` check failed or a required hook is missing; otherwise `"allow"`.
- `enforcement_profile: "enforced"` only when the launch is allowed, the version is the qualified one, `shell` is denied, and MCP tools, Code Mode `execute` and `webfetch` are each denied or narrowed. Otherwise `"advisory"`. Allowing `shell` in any form, even a narrow pattern, keeps the profile advisory.

`probe` does not judge what counts as narrow. The launcher reports each tool's state from the worker profile's policy rules, and that profile owns the allowed list.

## Evidence limits

Tests drive the adapter with the committed, sanitized probe fixtures in `tests/fixtures/smart-routing/probes/`. Hook entries there are the probe plugin's summaries: their key sets and recorded values are real, while header, base URL and environment values were never recorded and are supplied by the tests. A passing test is not observed OpenCode execution, and a new OpenCode version needs the probes re-run before its results are relied on.
