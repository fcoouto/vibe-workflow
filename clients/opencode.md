# OpenCode

Load this file only when a task implements, tests or diagnoses Smart Routing on OpenCode: the [adapter](../runtime/adapters/opencode.py), the routing plugin shim, or a routed OpenCode launch. OpenCode titles have their own [title contract](workstreams-titles.md#opencode-sessions). OpenCode is not an onboarding or startup client, and nothing here enrolls a project or qualifies OpenCode for the workflow.

## What the adapter does

The adapter turns OpenCode payloads into small, typed records for the routing command. It is pure: it reads no files, starts no process and makes no network call. It covers OpenCode 2.2.6, the version the probes ran on.

- `normalize_hook(hook, payload)` covers the four hooks routing uses: `model.request`, `tool.execute.before`, `permission.evaluate` and `shell.create.before`. The plugin passes the hook name because payloads do not carry it. Output includes the session and message IDs, agent, tool and tool class, request kind (`primary`, `title`, `compaction`, `generate`, otherwise `unknown`), the requested model, route identity, and for a subagent call the agent and any requested `provider/model#variant`.
- `normalize_event(event)` covers lineage and usage events: session created (with parent and agent), step started and ended (model, tokens, cost, rate-limit telemetry), usage updated, model selected and execution outcome. Each durable event keeps its per-session `durable_seq` so a gap can be detected. Other event types come back as `kind: "unknown"` with their sequence number and no body.
- `normalize_message(message)` covers stored messages: model, variant, finish, tokens, cost, `native.rateLimit` windows (0–1 fractions), and subagent tool calls with their child session.
- `probe(capabilities)` classifies already-observed capabilities without launching OpenCode (see below).

What never leaves the adapter: prompts, message and reasoning text, compaction summaries, tool output, command text, and header or environment values. Headers become a list of names, a base URL becomes its origin, and environment variables become names, with likely credential names listed so the shim can strip them. Command text yields only best-effort launch markers such as `opencode`, `claude` or `curl`.

Missing data stays unknown (`None`), never guessed. Task, stage and attempt are not in OpenCode payloads; the dispatch record binds them by message ID. Hooks carry no parent session; it comes from the session-created event or a stored subagent call. A malformed, oversized or wrongly typed payload raises `AdapterError` with a fixed reason that never repeats the payload. Session, message, event and tool-call IDs must match their expected patterns.

## Route identity

Route identity is the provider, model alias, requested variant and catalog family. The resolved model is always `unknown`, because OpenCode stores only the requested alias. The harness is `claude-agent-sdk via opencode` for the Claude Agent provider; other providers are named by provider.

## What can be enforced

The [capability matrix](../docs/smart-routing-capability-matrix.md#opencode-enforcement-p01-p05p08) is the authority for which paths can be blocked before anything is sent and which cannot. In short, `probe` always reports these as unsupported: plugin load failure (OpenCode continues without the plugin), commands run inside the shell, the resolved model, provider retries, blocking a model switch before the next request, session IDs from the environment, and event replay.

The user's decisions of 8 November 2026 apply:

1. Only managed launches are protected from plugin failure. Before every managed launch the launcher calls the plugin's `routing.health` for the session's canonical directory and refuses to send if it fails. Interactive sessions are not blocked.
2. Workers keep full shell access, so those profiles are advisory-only and get no funding guarantee. Enforcement applies only to profiles that deny `shell`, MCP tools, Code Mode `execute` and `webfetch` by policy, or allow a narrow list.
3. The plugin is installed globally and enforces nothing outside the canary project until cutover.
4. Route identity is as described above, with the resolved model unknown.

`probe` returns `managed_launch: "refuse"` when the health check failed or a required hook is missing, and `enforcement_profile: "enforced"` only when the launch is allowed, the version is the qualified one and the worker profile has no unmediated tools. Anything else is `advisory`.

## Evidence limits

Tests drive the adapter with the committed, sanitized probe fixtures in `tests/fixtures/smart-routing/probes/`. Hook entries there are the probe plugin's summaries: their key sets and recorded values are real, while header, base URL and environment values were never recorded and are supplied by the tests. A passing test is not observed OpenCode execution, and a new OpenCode version needs the probes re-run before its results are relied on.
