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

Only managed sessions are protected (user decision in the capability matrix). A session is **managed** when a managed launcher attached it in this plugin instance with `routing.attach`, or when its parent chain reaches an attached session. Every other session is **interactive**: the shim sends no check, starts no child process and blocks nothing for it, even when the routing command is missing or broken.

The shim learns parents from `session.created` events. A new session whose parent is already attached or already a managed descendant becomes a **managed descendant**, at any depth; these records are never evicted. That is the only evidence a session is managed besides attachment: a session created while something is attached but under any other parent is interactive. For a session it has not recorded, the shim walks the parent chain using a cache of seen sessions and `session.get` (at most 8 lookups of about 300 ms each per question; a failed lookup is remembered for 5 s, so it does not wait again). The session is managed if the chain reaches a root or managed descendant. Otherwise it is interactive: a missing or invalid session ID, a root, a cycle, a chain that cannot be determined, and a chain longer than 64 sessions. An attached session counts as a root.

Each attached root has its own budget of 256 recorded managed descendants, so one attempt's tree never limits another's. When a root's budget is used up, its managed subagent spawns are blocked (`descendant_limit`). A descendant created beyond that budget by another path, such as the API, blocks every session of that root with the same reason until the root is detached. The instance also has a memory ceiling of 4096 recorded descendants across roots: at the ceiling only new managed descendants, and spawns, of the root creating them are blocked. Detaching a root clears its descendants, count and blocking. Interactive sessions are never affected.

Attachments live in the plugin instance's memory, and each project directory spelling gets its own instance. A managed launcher therefore, before every attempt, calls `routing.health` and then `routing.attach` for the session's canonical directory (symlinks resolved), and refuses to send if either fails. Attaching through another spelling of the path reaches a different instance and protects nothing (observed).

### What the shim does for a managed session

- `model.request`: checks the request with its kind (`primary`, `title`, `compaction` or `generate`). An unknown kind or a malformed model blocks without a check.
- `tool.execute.before` for `subagent`: checks the spawn with kind `subagent` and the requested `provider/model#variant`. Managed spawns must name their model: a spawn without one (`subagent_model_required`) or with a malformed one blocks without a check. A route without a variant never matches an admission, because the admitted identity includes it.
- Every other tool, including MCP and Code Mode nested calls: no check. The worker profile's policy deny rules govern them; see [What can be enforced](#what-can-be-enforced).
- `permission.evaluate`: sets `webfetch` and any action listed in the attachment's `deny_actions` to `deny`, with the shim's message; an action that is already denied keeps its message. It only tightens; a configured `deny` is final and never reaches the hook.
- `shell.create.before`: the hook carries no session ID, so while any session is attached in this instance every shell started by the instance loses its environment variables whose names look like credentials. The rule is the adapter's `credential_name`: after splitting camelCase (`githubToken`, `dbPassword`) and, for unusual names, punctuation, a name matches when one of its `_` segments is an API key, key, token, secret(s), password, `PASSWD`, `PASS`, passphrase, credential(s), `CREDS`, auth, `OAUTH`, cookie, private, `JWT`, `PAT`, `ACCESSKEY`, `SECRETKEY`, DSN, connection string or a database, Redis, MongoDB, Postgres, MySQL or AMQP `_URL`/`_URI`, or when it ends in `PASSWORD`/`PASSWD` (`PGPASSWORD`) or `_PWD` (`MYSQL_PWD`, but not `PWD`). Ordinary names such as `PATH`, `HOME`, `PWD`, `TERM`, `EDITOR`, `GIT_AUTHOR_NAME`, `npm_config_cache` and `TOKENIZERS_PARALLELISM` stay. Interactive shells in the same instance lose them too for that time; for example `SSH_AUTH_SOCK` goes, so git over SSH fails in that instance until the attached sessions are detached. Not stripped: names that only point at credential files (`AWS_PROFILE`, `KUBECONFIG`), other URL variables that may embed credentials (`HTTP_PROXY`, `PIP_INDEX_URL`), and the files themselves.

A managed session passes only when the answer is `allow`. Everything else blocks the request before it is sent: a `blocked` answer, `unmanaged`, a timeout, an unreadable line, an answer with an unknown ID or another version, a missing state, the child exiting, an undeterminable chain with the evidence above, or a request over 4 KiB.

### Routing child

The shim starts one `python3 -I -B scripts/routing.py --serve` child per plugin instance on first use, with the `--settings` and `--ledger` pins from the plugin options and an environment without credential names. Requests are JSON lines `{"v": 1, "op", "id", ...}` and answers are matched by ID, so concurrent hooks share the child. Each request has about 500 ms, plus a one-time start-up allowance of 1 s after a spawn. On a stall, exit, unreadable line or unknown ID, the shim kills the child, starts a new one and retries once; if that also fails, the hook throws `routing_unavailable`. Killing the child fails every request waiting on it, and each of those is retried once on the new child, so one stalled request can delay the others by one timeout. After two such failures in a row the shim fails fast for about 1 s before starting a child again; it never gives up for good.

Plugin options: `python` (interpreter, default `python3`), `settings`, `ledger` and `timeout_ms` (at most 5000).

### Plugin RPC

The plugin registers RPC `routing` (`POST /api/rpc/routing/<method>` with `{"input": {...}}` and the `x-opencode-directory` header):

- `health` `{}`: asks the child for `status` within the request budget. Returns `{state: "ok", child: "up" | "respawned", version: 1, respawn_ms}` only when the settings are readable and the ledger exists and opens; `{state: "not_ready", reason: "settings_unavailable" | "ledger_absent" | "ledger_unavailable", version: 1}` when the child answers but cannot yet answer a check; and an error when the child does not answer (observed HTTP 500 with the child down; HTTP 400 `rpc.unavailable` when the plugin did not load). A launcher sends only after `ok`, so the order is `prepare` (which creates the ledger), then `health`, then `attach` and dispatch.
- `attach` `{session_id, attempt_id, funding_pool, deny_actions?}`: marks the session managed with the admitted attempt and pool that every check uses. Re-attaching replaces the attempt. At most 256 attached sessions; children found through them are not counted, and an attachment that is never detached keeps its place, so further attaches then fail visibly (`attach_limit`) and those launches refuse to send.
- `detach` `{session_id}`: returns the session and the children found through it to interactive.

The RPC checks no caller authority: any client of the server, including an interactive agent or a worker that can reach it through a shell, can attach a session or detach a managed one. Enforced worker profiles deny shell, Code Mode `execute` and `webfetch`, so they cannot reach it. Task S3.4's single-use dispatch authority must gate `attach` and `detach`.

### The `unmanaged` answer

`{"v": 1, "state": "unmanaged"}` is the answer Task S3.4 adds for a `check` about a session the ledger does not know. The S3.1 `check` needs an attempt ID, so the shim queries only attached sessions today. For an attached session `unmanaged` contradicts the launcher and blocks (`unexpected_unmanaged`).

### Residual risks

- A plugin instance that restarts or reloads during an attempt forgets its attachments; until the launcher attaches again, that session runs unguarded.
- A managed child that was not recorded as a managed descendant passes as interactive when its `session.get` lookup fails (or returns anything but that session), and also when its chain to the root is longer than 64 sessions. It goes unrecorded when its creation event was missed, when its first request arrives before its creation event, when the event stream has stopped (lineage then relies on lookups for the life of the instance), or when it falls beyond both the descendant budget and the over-limit record.
- A session created through the server API without a parent is a root and interactive, so a worker that can reach the API (for example through a shell) can run unmanaged sessions. Task S3.4's derived session IDs and dispatch authority must close this; until then only profiles that deny shell, Code Mode `execute` and `webfetch` keep workers away from the API.
- A managed session in an instance the launcher did not attach (another directory spelling, another server) is unguarded.
- Commands run inside an allowed shell, and a plugin that failed to load, stay unenforceable (capability matrix).

## Evidence limits

Tests drive the adapter with the committed, sanitized probe fixtures in `tests/fixtures/smart-routing/probes/`. Most hook entries there are the probe plugin's summaries: their key sets and recorded values are real, while header, base URL and environment values were never recorded and are supplied by the tests. `s3-3-shim-live.json` holds full sanitized payloads recorded with this shim loaded, and what the shim blocked. The shim's own tests run under `node --test` with a fake routing child; a passing test is not observed OpenCode execution, and a new OpenCode version needs the probes re-run before its results are relied on.
