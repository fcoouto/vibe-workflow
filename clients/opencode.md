# OpenCode

Load this file only when a task implements, tests or diagnoses Smart Routing on OpenCode: the [adapter](../runtime/adapters/opencode.py), the [routing plugin shim](#pre-transmission-shim), or a routed OpenCode launch. OpenCode titles have their own [title contract](workstreams-titles.md#opencode-sessions). OpenCode is not an onboarding or startup client, and nothing here enrolls a project or qualifies OpenCode for the workflow.

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

## Pre-transmission shim

[`opencode_plugin.ts`](../runtime/adapters/opencode_plugin.ts) is an OpenCode V2 server plugin with no dependencies; it runs in the runtime embedded in `opencode`. It holds no selection or funding logic: it asks the routing command and enforces the answer. It never logs, and its errors carry only a fixed reason code (`routing: blocked (<reason>)`), never prompts, headers, environment values or other payload content. Its field extraction mirrors the adapter, and the tests compare the two.

### Managed and interactive sessions

Only managed sessions are protected (user decision in the capability matrix). A session is **managed** when a managed launcher attached it in this plugin instance with `routing.attach`, or when its parent chain (read with `session.get`, at most 8 levels) reaches an attached session. Every other session is **interactive**: the shim sends no check, starts no child process and blocks nothing for it, even when the routing command is missing or broken.

Attachments live in the plugin instance's memory, and each project directory spelling gets its own instance. A managed launcher therefore, before every attempt, calls `routing.health` and then `routing.attach` for the session's canonical directory (symlinks resolved), and refuses to send if either fails. Attaching through another spelling of the path reaches a different instance and protects nothing (observed).

### What the shim does for a managed session

- `model.request`: checks the request with its kind (`primary`, `title`, `compaction` or `generate`). An unknown kind or a malformed model blocks without a check.
- `tool.execute.before` for `subagent`: checks the spawn with kind `subagent` and the requested `provider/model#variant`. A spawn without an explicit model, or with a malformed one, blocks without a check.
- Every other tool, including MCP and Code Mode nested calls: no check. The worker profile's policy deny rules govern them; see [What can be enforced](#what-can-be-enforced).
- `permission.evaluate`: sets `webfetch` and any action listed in the attachment's `deny_actions` to `deny`. It only tightens; a configured `deny` is final and never reaches the hook.
- `shell.create.before`: the hook carries no session ID, so while any session is attached in this instance every shell started by the instance loses its environment variables whose names look like credentials (the adapter's rule) or cannot be classified. Interactive shells in the same instance lose them too for that time.

A session passes only when the answer is `allow`. Everything else blocks the request before it is sent: a `blocked` answer, `unmanaged`, a timeout, an unreadable line, an answer with an unknown ID or another version, a missing state, the child exiting, a failed parent lookup while any session is attached, or a request over 4 KiB.

### Routing child

The shim starts one `python3 -I -B scripts/routing.py --serve` child per plugin instance on first use, with the `--settings` and `--ledger` pins from the plugin options and an environment without credential names. Requests are JSON lines `{"v": 1, "op", "id", ...}` and answers are matched by ID, so concurrent hooks share the child. Each request has about 500 ms, plus a one-time start-up allowance of 1 s after a spawn. On a stall, exit, unreadable line or unknown ID, the shim kills the child, starts a new one and retries once; if that also fails, the hook throws `routing_unavailable`.

Plugin options: `python` (interpreter, default `python3`), `settings`, `ledger` and `timeout_ms` (at most 5000).

### Plugin RPC

The plugin registers RPC `routing` (`POST /api/rpc/routing/<method>` with `{"input": {...}}` and the `x-opencode-directory` header):

- `health` `{}`: the child must answer a `status` ping with `ok`. Returns `{state: "ok", child: "up" | "respawned", version: 1, respawn_ms}`; anything else is an error (observed HTTP 500 with the child down; HTTP 400 `rpc.unavailable` when the plugin did not load).
- `attach` `{session_id, attempt_id, funding_pool, deny_actions?}`: marks the session managed with the admitted attempt and pool that every check uses. Re-attaching replaces the attempt and releases inherited children. At most 256 sessions.
- `detach` `{session_id}`: returns the session and the children found through it to interactive.

### The `unmanaged` answer

`{"v": 1, "state": "unmanaged"}` is the answer Task S3.4 adds for a `check` about a session the ledger does not know. The S3.1 `check` needs an attempt ID, so the shim queries only attached sessions today. For an attached session `unmanaged` contradicts the launcher and blocks (`unexpected_unmanaged`).

### Residual risks

- A plugin instance that restarts or reloads during an attempt forgets its attachments; until the launcher attaches again, that session runs unguarded.
- A managed session in an instance the launcher did not attach (another directory spelling, another server) is unguarded.
- Commands run inside an allowed shell, and a plugin that failed to load, stay unenforceable (capability matrix).

## Evidence limits

Tests drive the adapter with the committed, sanitized probe fixtures in `tests/fixtures/smart-routing/probes/`. Most hook entries there are the probe plugin's summaries: their key sets and recorded values are real, while header, base URL and environment values were never recorded and are supplied by the tests. `s3-3-shim-live.json` holds full sanitized payloads recorded with this shim loaded, and what the shim blocked. The shim's own tests run under `node --test` with a fake routing child; a passing test is not observed OpenCode execution, and a new OpenCode version needs the probes re-run before its results are relied on.
