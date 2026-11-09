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

[`opencode_plugin.ts`](../runtime/adapters/opencode_plugin.ts) is an OpenCode V2 server plugin with no dependencies; it runs in the runtime embedded in `opencode`. It holds no selection or funding logic: it asks the routing command and enforces the answer. It never logs, and its errors carry only a fixed reason code (`routing: blocked (<reason>)`), never prompts, headers, environment values, dispatch authorities or other payload content. Its field extraction mirrors the adapter, and the tests compare the two.

### Managed and interactive sessions

Only managed sessions are protected (user decision in the capability matrix). A session is **managed** when a managed launcher attached it in this plugin instance with `routing.attach`, or when its parent chain reaches an attached session. A session is **pending** when its ID has the shape of a ledger-derived ID (`ses_` and 32 lowercase hex characters) and an open attempt of the ledger owns it, or when its parent chain reaches such a session, and nobody has attached it here yet. A session is **closed** when its ID is a finished attempt's derived ID. Every other session is **interactive**: the shim sends no check, starts no child process and blocks nothing for it, even when the routing command is missing or broken.

Pending sessions are blocked until the launcher attaches them (`attach_required`): model requests and subagent spawns throw without a check and `webfetch` is denied. A closed session, and every session recorded under it, has its model requests and spawns blocked (`attempt_closed`), also after the launcher detached the root while subagents still ran. "Recorded" means two things. Sessions recorded as managed descendants while the root was attached are kept past detach (at most 4096, oldest forgotten first), and any session whose chain reaches one of them is blocked as well. Parent links from creation events after detach live only in the 4096-entry parent cache; once a link is evicted there, only those kept records and the closed sessions protect what lies below it. This relies on the same assumption about derived-shape IDs as below. Neither kind strips shell credentials or turns on `session.get` lookups; only an attached root does, so a stale entry can never break interactive work. This only adds protection: it stops a worker that reaches the server API from running a session under an open or finished attempt's derived ID before or after its launcher.

On `session.created` for a session it does not know, and on a hook for one, the shim asks the routing child a read-only `check` with only that `session_id` (see [the `unmanaged` answer](#the-unmanaged-answer)). It asks only about derived-shape IDs. It assumes OpenCode's own IDs never have that shape: the 2.2.6 server generated 26-character mixed-case bodies for sessions and messages in the [live qualification](../docs/smart-routing-qualification.md) (n=1 run), never `ses_` and 32 lowercase hex. If the assumption holds, interactive sessions are never asked about. Each question is shared while in flight. Creation events and hooks each have at most 8 questions in flight: past the event limit a creation is not asked about (and nothing is remembered, so its first hook asks), and past the hook limit the hook's session counts as unmanaged for now. A hook waits for a question at most one request budget (about 500 ms, plus the start-up allowance after a spawn). Answers:

- `authority_required`, `dispatch_required`, `attempt_uncertain` or `hold_state_unsupported` (an open attempt owns it): pending, even when the answer arrives late. A pending entry lasts 5 minutes, then the ledger is asked again. At most 1024 are recorded; beyond that a session is still blocked when asked about and is asked again next time.
- `attempt_closed`: closed, remembered for up to 1024 sessions (oldest forgotten first; a forgotten one is asked again). A finished attempt that has aged out of the ledger answers `unmanaged` instead, so its session then becomes interactive.
- `unmanaged`, a timeout and any store or request error (for example `ledger_absent`): interactive, remembered for 5 s. The hook limit: interactive for now, not remembered.

Attaching a pending session makes it a root and its recorded pending children its managed descendants. After detach a derived session the ledger still knows is pending again (or closed once the attempt finished).

The shim learns parents from `session.created` events. A new session whose parent is already attached or already a managed descendant becomes a **managed descendant**, at any depth; these records are never evicted. That is the only evidence a session is managed besides attachment: a session created while something is attached but under any other parent is interactive. For a session it has not recorded, the shim walks the parent chain using a cache of seen sessions and, only while a root is attached, `session.get` (at most 8 lookups of about 300 ms each per question; a failed lookup is remembered for 5 s, so it does not wait again). The session is managed if the chain reaches a root or managed descendant. Otherwise it is interactive: a missing or invalid session ID, a root, a cycle, a chain that cannot be determined, and a chain longer than 64 sessions. An attached session counts as a root.

Each attached root has its own budget of 256 recorded managed descendants, so one attempt's tree never limits another's. When a root's budget is used up, its managed subagent spawns are blocked (`descendant_limit`). A descendant created beyond that budget by another path, such as the API, blocks every session of that root with the same reason until the root is detached. The instance also has a memory ceiling of 4096 recorded descendants across roots: at the ceiling only new managed descendants, and spawns, of the root creating them are blocked. Detaching a root clears its descendants, count and blocking. Interactive sessions are never affected.

Attachments, with their dispatch authority, live only in the plugin instance's memory, and each project directory spelling gets its own instance. A managed launcher therefore uses the session's canonical directory (symlinks resolved) for every call below. Attaching through another spelling of the path reaches a different instance and protects nothing (observed).

### Handshake

A managed launch runs these steps in order and refuses to send if any of them fails:

1. `scripts/routing.py prepare` admits the attempt (and creates the ledger).
2. `routing.health` answers `ok`.
3. `scripts/routing.py dispatch` returns `dispatched` with the `authority` token and the derived `session_id` and `message_id`. The launcher keeps the token in memory for the attempt and never logs or stores it.
4. `routing.attach {session_id, attempt_id, authority, funding_pool, model, deny_actions?}` for the attempt's own derived session (never a child; children are learned from creation events). The shim verifies the token and the session's ownership through the child before it records anything.
5. The launcher creates the session and sends the prompt with the derived IDs. Every `check` the shim sends for that session and its child sessions carries `attempt_id`, the hook's own `session_id` and the token as `authority`.
6. `routing.detach {session_id, attempt_id, authority}` after the attempt.

What the shim forwards to the routing child is limited to the request facts below, the attempt ID, the session ID and the token; the token goes nowhere else (not to OpenCode, not into an error, health or RPC result). If the plugin instance restarts during an attempt, the launcher attaches again with the same token; a `detach` refused because the restarted instance has no attachment needs no retry.

### What the shim does for a managed session

- `model.request`: checks the request with its kind (`primary`, `title`, `compaction` or `generate`). An unknown kind or a malformed model blocks without a check.
- `tool.execute.before` for `subagent`: checks the spawn with kind `subagent` and the requested `provider/model#variant`. A `subagent: true` command never reaches this hook: the server creates its child session directly, and the shim checks the child at its first `model.request` through lineage (observed: a child on another model was blocked with `route_mismatch`). Managed spawns must name their model: a spawn without one (`subagent_model_required`) or with a malformed one blocks without a check. A route without a variant never matches an admission, because the admitted identity includes it.
- Every other tool, including MCP and Code Mode nested calls: no check. The worker profile's policy deny rules govern them; see [What can be enforced](#what-can-be-enforced).
- `permission.evaluate`: sets `webfetch` and any action listed in the attachment's `deny_actions` to `deny`, with the shim's message; an action that is already denied keeps its message. It only tightens; a configured `deny` is final and never reaches the hook.
- `shell.create.before`: the hook carries no session ID, so while any session is attached in this instance every shell started by the instance loses its environment variables whose names look like credentials. The rule is the adapter's `credential_name`: after splitting camelCase (`githubToken`, `dbPassword`) and, for unusual names, punctuation, a name matches when one of its `_` segments is an API key, key, token, secret(s), password, `PASSWD`, `PASS`, passphrase, credential(s), `CREDS`, auth, `OAUTH`, cookie, private, `JWT`, `PAT`, `ACCESSKEY`, `SECRETKEY`, DSN, connection string or a database, Redis, MongoDB, Postgres, MySQL or AMQP `_URL`/`_URI`, or when it ends in `PASSWORD`/`PASSWD` (`PGPASSWORD`) or `_PWD` (`MYSQL_PWD`, but not `PWD`). Ordinary names such as `PATH`, `HOME`, `PWD`, `TERM`, `EDITOR`, `GIT_AUTHOR_NAME`, `npm_config_cache` and `TOKENIZERS_PARALLELISM` stay. Interactive shells in the same instance lose them too for that time; for example `SSH_AUTH_SOCK` goes, so git over SSH fails in that instance until the attached sessions are detached. Not stripped: names that only point at credential files (`AWS_PROFILE`, `KUBECONFIG`), other URL variables that may embed credentials (`HTTP_PROXY`, `PIP_INDEX_URL`), and the files themselves.

A managed session passes only when the answer is `allow` with `authority: "verified"` for its attempt (otherwise `authority_unverified`). Everything else blocks the request before it is sent: a `blocked` answer, `unmanaged`, a timeout, an unreadable line, an answer with an unknown ID or another version, a missing state, the child exiting, an undeterminable chain with the evidence above, or a request over 4 KiB.

### Routing child

The shim starts one `python3 -I -B scripts/routing.py --serve` child per plugin instance on first use, with the `--settings` and `--ledger` pins from the plugin options and an environment without credential names. Requests are JSON lines `{"v": 1, "op", "id", ...}` and answers are matched by ID, so concurrent hooks share the child. Each request has about 500 ms, plus a one-time start-up allowance of 1 s after a spawn. On a stall, exit, unreadable line or unknown ID, the shim kills the child, starts a new one and retries once; if that also fails, the hook throws `routing_unavailable`. Killing the child fails every request waiting on it, and each of those is retried once on the new child, so one stalled request can delay the others by one timeout. After two such failures in a row the shim fails fast for about 1 s before starting a child again; it never gives up for good.

When the plugin shuts down, the child is killed, requests in flight fail, and no child is started again.

Plugin options: `python` (interpreter, default `python3`), `settings`, `ledger` and `timeout_ms` (at most 5000).

### Plugin RPC

The plugin registers RPC `routing` (`POST /api/rpc/routing/<method>` with `{"input": {...}}` and the `x-opencode-directory` header):

- `health` `{}`: asks the child for `status` within the request budget. Returns `{state: "ok", child: "up" | "respawned", version: 1, respawn_ms}` only when the settings are readable and the ledger exists and opens; `{state: "not_ready", reason: "settings_unavailable" | "ledger_absent" | "ledger_unavailable", version: 1}` when the child answers but cannot yet answer a check; and an error when the child does not answer (observed HTTP 500 with the child down; HTTP 400 `rpc.unavailable` when the plugin did not load). A launcher sends only after `ok` (see [Handshake](#handshake)).
- `attach` `{session_id, attempt_id, authority, funding_pool, model, deny_actions?}`: `session_id` must be a derived-shape ID and `model` is the dispatched route as `provider/model#variant`. A malformed or missing field, including any other session ID, is `invalid_attach` and reaches no child. Otherwise the shim, before recording anything:
  1. sends the child a strict `check` with the attempt, the session, the token and that route (kind `primary`, the given pool), and accepts only `allow` with `authority: "verified"` for that attempt (another attempt's session is `session_mismatch` there);
  2. asks the child about the session alone and accepts only `authority_required`, so the ledger already owns it as a dispatched hold's session. With step 1 that makes it the attempt's own derived session; the command returns no owner field, so ownership is inferred this way;
  3. then applies its local limits: a session managed under another attempt, and more than 256 attached sessions (children are not counted; an attachment that is never detached keeps its place).

  Every refusal after the shape check is the same `attach_refused`, whatever the reason (wrong token, unknown or closed attempt, unsent admission, another attempt's session, a session the ledger does not own, the limits, an unavailable child), so a caller without the token learns nothing about the ledger or this instance. The same attempt may attach again; that keeps the stricter rules, because `deny_actions` only grow. Returns `{state: "attached", session_id}`.
- `detach` `{session_id, attempt_id, authority}`: only for an attached root with the same attempt and token (always compared, in constant time, also when nothing is attached); everything else, including a descendant, an unattached session and a repeated detach, is `detach_refused`, and a malformed field is `invalid_detach`. Returns `{state: "detached", session_id}` and releases the session and the children found through it.

OpenCode 2.2.6 returns every RPC refusal or plugin error to the caller as HTTP 500 `rpc.internal` ("RPC call failed") without the reason code, so a launcher treats any non-200 answer as refuse.

So a client of the server without the token, such as an interactive agent or a worker that reaches the server through a shell, can neither attach an interactive session nor detach a managed one, and a token holder can attach only its own attempt's derived session.

### The `unmanaged` answer

`{"v": 1, "state": "unmanaged"}` is the S3.4 answer for a `check` with only a `session_id` the ledger does not know. The shim sends such a check only to ask about a derived-shape session ID (see [Managed and interactive sessions](#managed-and-interactive-sessions)). The command validates a `request` before it resolves the session, so the question carries fixed placeholder facts (provider `opencode`, kind `primary`) that are never used. For an attached session `unmanaged` contradicts the launcher and blocks (`unexpected_unmanaged`).

### Residual risks

- A plugin instance that restarts or reloads during an attempt forgets its attachments; until the launcher attaches again, that session runs unguarded.
- A managed child that was not recorded as a managed descendant passes as interactive when its `session.get` lookup fails (or returns anything but that session), and also when its chain to the root is longer than 64 sessions. It goes unrecorded when its creation event was missed, when its first request arrives before its creation event, when the event stream has stopped (lineage then relies on lookups for the life of the instance), or when it falls beyond both the descendant budget and the over-limit record.
- A session created through the server API without a parent and with an ID that is not derived (or derived but unknown to the ledger) is a root and interactive, so a worker that can reach the API (for example through a shell) can still run unmanaged sessions that way; it cannot spend an attempt's admission, because that needs the token. Only profiles that deny shell, Code Mode `execute` and `webfetch` keep workers away from the API.
- A derived session the ledger knows passes as interactive while the ledger cannot answer (absent, busy, a child failure or a timeout), until the shim asks again after 5 s, or while 8 hook questions are already in flight. A burst of derived-looking creations does not let one through, but it can delay learning about a session until its first hook asks.
- Sessions of a finished attempt pass as interactive when their record is gone: the attempt aged out of the ledger, or the session was forgotten (more than 4096 sessions kept past detach, or the lineage cache evicted it and it was never recorded under an attached root). A child created through the API under a pending session is blocked only if its creation event was seen (with no root attached, the shim makes no lookups).
- A token holder can attach only its own attempt's derived session. Ownership is inferred from two command answers (step 2 above), not from an owner field.
- The token is in the plugin's memory and in the `routing.attach` request body, so other code in the server process, or anything that can read the server's requests, could see it; whether OpenCode logs RPC input has not been observed.
- A managed session in an instance the launcher did not attach (another directory spelling, another server) is unguarded.
- Commands run inside an allowed shell, and a plugin that failed to load, stay unenforceable (capability matrix).

## Usage accounting and resync

[`runtime/opencode_accounting.py`](../runtime/opencode_accounting.py) turns a re-read OpenCode attempt into the usage report the routing command's `record` takes. Like the adapter, it is pure, and it reads only the adapter's normalized messages and events. Errors are `AccountingError` with a fixed reason that never repeats the payload.

- **Source of record.** Usage comes from stored message tokens and the session's token total. `session.usage.updated` is volatile and never replayed, so it is never used. Step events carry the same tokens but are not counted either.
- **`usage_from_messages(messages, session_total, session_id=…, funding=…, complete=…, session_cost=…, children=…, lineage=…)`** accounts one attempt.
  - Each assistant message is a `primary` request and each compaction message a `compaction` request, with the message ID as `receipt`, in creation order.
  - Above 32 requests (one report's limit) they are grouped by kind, model and variant, without receipts. Above 32 routes they are grouped by kind with model `unknown`, every cost `null` and the reason `too_many_routes`.
  - The result's `usage` is the S3.6a `{source, requests}` object, or `null` when nothing was requested. The result also has `totals`, the root's `title`, per-session `sessions`, `message_ids`, `unknown` reasons, `reported_cost` and the latest `rate_limit` observation.
  - Identical duplicate messages count once; a message ID with different content is refused.
  - Normalized messages carry no session ID, so a message belongs to the session state that lists it. The same message under two sessions is refused.
- **Child sessions.** A parent's token total excludes its subagent sessions (the probes show this), so the attempt's spend includes them only when the caller passes them as `children`, each as `normalize_session` output plus its messages.
  - A child links to its parent in two ways, at any depth: a subagent call's `child_session_id` in an included session, or the child's own `parent_session_id` (the `parentID` from `session.get`). A `subagent: true` command creates its child directly, with no subagent call in the parent, so `parentID` is its only link (live re-qualification, 9 November 2026).
  - These make every cost unknown:
    - a spawned session that is not included (`child_session_unaccounted`);
    - a subagent call with no child session, for example one that errored, was aborted or is still starting (also `child_session_unaccounted`), because it may have spent before losing its link;
    - tool calls the adapter dropped (`tool_calls_dropped`).
  - Every included session must be reachable from the root through these links. An unreachable session (including an island of sessions spawning or parenting each other), a cycle, a link back to the root, a `parent_session_id` outside the attempt or contradicting a subagent call's link, and a root whose parent is in the attempt are all refused. A child that two sessions reference counts once.
  - **Lineage.** `lineage` is the launcher's list of `{session_id, parent_session_id}` for every session it knows of under the root, from session listing and lineage events. Command children count like any other descendant. Any listed session whose recorded parent chain reaches an included session, but that is not itself included, adds `child_session_unaccounted`; sessions whose chain leaves the list are ignored. A listed session with two parents, a parent that contradicts an included session's link, or a cycle is refused (`invalid lineage`); more than 1024 entries is `too many sessions`. The accounting trusts the launcher: a child left out of both `children` and `lineage`, or lineage that lies about a parent, settles complete with that child's spend uncounted. Therefore, for API-funded routes the launcher must merge the plugin's own recorded descendant set (to be exposed by a shim RPC, follow-up S3.3c) with its session listing and treat any mismatch as `child_session_unaccounted`; until that exists, API-funded managed attempts are not qualified. Subscription routes are not affected in dollars. Launchers must supply `lineage`: without it the result carries `lineage_not_checked`, which makes an API route's cost unknown (the hold ends `cost_unknown`) rather than silently complete, and is only a flag on subscription routes.
  - Each child's tokens, title remainder and unknowns count like the root's.
- **Finished messages.** A message is finished when it has a completion time or a finish reason, or when an idle message was created after it. The recorded compaction message has neither of its own, and the idle message after it is the only sign that it ended. An unfinished message is still counted with its current tokens, but its session's title is unknown and every cost becomes `null` (`message_in_progress`).
- **Title tokens by subtraction.** A title request creates no message, so its tokens are the session total minus the sum of its messages, per token class (`title_tokens`).
  - The result is unknown when:
    - any class would go negative;
    - the message list may be incomplete (`complete: false` for a paging gap);
    - a message's tokens are missing, or a message has not finished;
    - an unknown record, or tokens on a non-model message, appear. The adapter returns a `synthetic` message (how a command child's result reaches its parent; only its type was recorded) as `kind: "synthetic"` only when it has no model, no non-zero cost and no tool parts, otherwise as unknown, so a child link on it is never lost silently. A `synthetic` record is never a request and never an unknown record, but tokens on it count as unexpected;
    - the session total lacks a class.
  - A non-zero remainder becomes one `title` request with model `unknown`.
  - Whether `generate` requests also create no message is unverified. If they do, their tokens are in this remainder too.
- **Cost.** Units stay separate: tokens, USD micro-units and quota fractions are never added. Cost is never derived from tokens.
  - With `funding: "subscription"` the source is `host_message_totals` and every `cost_usd_micros` is `null`. OpenCode's figure appears only in `reported_cost` (`basis: "provider_estimate"`): a provider's token-price estimate is not proof of out-of-pocket spend.
  - With `funding: "api"` the source is `estimate`. OpenCode computes cost locally from a price catalog and reports 0 for unpriced or custom models, so its figure is not a receipt. S3.6a charges an estimate at no less than the admitted estimate and releases at most the padding.
  - On API routes each message's cost becomes micro-units, rounded up. The title's cost is the session cost minus the messages' costs.
  - A zero or missing cost for a request that used tokens is unknown. So is a missing or non-positive cost difference for a title with tokens, and so is any unknown above.
  - When any cost is unknown, every cost is `null`, so the hold stays `cost_unknown` and is never released on a zero.
  - A message with no tokens and cost 0 stays 0.
- **Event gaps.** `track_events(events, state)` checks `durable.seq` per aggregate and returns `resync_needed` with the aggregate, the last in-order sequence number and the reason. The reasons are `gap`, `out_of_order`, `seq_conflict` (same number, another event), `reset` (0 again), `version_changed` and `joined_late` (first seen after 0). An exact duplicate is only counted, and volatile events are ignored. The input state is never changed.
- **Resync.** After a gap, and always at attempt end, the launcher re-reads the sessions and their messages and calls `reconcile_with_state(previous, {session_id, messages, tokens, cost?, complete?, children?, lineage?}, funding=…, tracker=…)`.
  - Totals are recomputed from the state, never accumulated, so repeating a resync changes nothing.
  - The result reports token deltas, added and missing messages, and `regressed` classes (a deleted or reverted message). These are reported, never charged back; acting on them belongs to S3.6b-2.
  - With `tracker`, it also re-baselines every included session (`mark_resynced`).
- **Rate limits.** `rate_limit_observation(record)` passes Claude Agent `native.rateLimit` through as an observation with explicit units: 0–1 fractions, resets in epoch seconds, and the observation time (completion, else creation) in epoch milliseconds. Windows the adapter could not read, such as a percentage, are listed as unknown. A utilization above 1, or a reset that looks like milliseconds, is refused. Window headroom comes from this telemetry, never from token arithmetic.
- **`normalize_session(session)`** reads a `session.get` record into `{session_id, parent_session_id, tokens, cost}`. It validates the totals with the adapter's message normalizer, because the adapter has no session normalizer.
- **`consistency_report(session_state)`** returns bounded diagnostics: message counts by type, duplicates, conflicts, unknown records, missing tokens or models, unfinished messages, spawned, included and unaccounted child sessions, listed descendants that are not included and lineage conflicts and loops (`lineage_unaccounted`, `lineage_conflicts`, `lineage_loops`, each `null` without `lineage`), subagent calls without a child session, dropped tool calls, and whether the title remainder is known. It counts problems instead of raising, and carries IDs, counts and field names only.

**Wiring (not yet connected).**
1. The launcher waits until the attempt's sessions are idle: the root's run has ended and no subagent is still running.
2. It re-reads the root session with `session.get`, then every page of its messages, then every descendant session the same way, including command children found only through `parentID`.
3. It normalizes these with the adapter and `normalize_session`.
4. It calls `usage_from_messages` with the hold's funding, the descendants as `children` and every session it knows of under the root as `lineage`.
5. It sends `result["usage"]` in `record complete {usage}`.

It sets `complete: false` for any session whose paging was not exhaustive. Re-reading before the sessions are idle gives an unknown cost rather than a low one. During the attempt it feeds `/api/event` records to `track_events` and resyncs when one is flagged. This assumes one root session per attempt; a session reused across attempts would need the previous accounting as a baseline. The ledger side and the launcher belong to S3.6b-2 and later tasks.

Observed in the live re-qualification (9 November 2026), for launchers:
- A `subagent: true` command does not pass through `tool.execute.before`, so the shim's spawn checks never see it. The shim stopped the child at its first model request through the parent lineage it learned from `session.created`. The blocked child's failure still started an extra parent turn on the attempt's route, which costs the parent.
- RPC refusals such as `attach_refused` reach the caller as HTTP 500 `rpc.internal` ("RPC call failed") with no reason code, so a launcher cannot tell a refusal from a routing-child failure and must treat both as "do not send".

## Evidence limits

Tests drive the adapter with the committed, sanitized probe fixtures in `tests/fixtures/smart-routing/probes/`. Most hook entries there are the probe plugin's summaries: their key sets and recorded values are real, while header, base URL and environment values were never recorded and are supplied by the tests. `s3-3-shim-live.json` holds full sanitized payloads recorded with this shim loaded, and what the shim blocked. The shim's own tests run under `node --test` with a fake routing child and with the real `routing.py --serve` on pinned temporary stores (prepare, dispatch, attach with the token, checks); the authority handshake was observed live once on OpenCode 2.2.6 with `claude-agent/haiku#low` ([live qualification](../docs/smart-routing-qualification.md): attach, allowed request, blocked unattached and wrong-token sessions, detach), while settlement, the closed-attempt block and the lost-acknowledgment drill were not reached. Accounting rests on three recorded sessions with complete message lists: in one, the remainder after subtracting the messages is the title request (2 input, 9 output, 605 cache-read and 549 cache-write tokens); in the two with titles given by the user it is zero. The fixtures contain no `generate` request, no message deleted or reverted, no paging, no provider-reported non-zero cost, no API-funded route, no child session's messages (only its total) and no observed sequence gap: the event excerpt skips sequence numbers only because it is an excerpt. A passing test is not observed OpenCode execution, and a new OpenCode version needs the probes re-run before its results are relied on.
