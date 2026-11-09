# Smart Routing live qualification on OpenCode 2.2.6 (9 November 2026)

**Outcome: stopped in stage 3 on a blocker.** Stages 0–2 matched the contract. Stage 3 (P0.8 accounting) could not account a managed attempt in which the managed session ran a `subagent: true` command (see [Blocker](#blocker)). The rest of stage 3, two stage-2 checks that run after completion, and stage 4 did **not run**. Nothing here closes a gate by itself; the [capability matrix](smart-routing-capability-matrix.md#live-re-qualification-9-november-2026) records what changed.

- Harness: [`tests/qualification/opencode_live.py`](../tests/qualification/opencode_live.py) acted as the managed launcher. [`opencode_inspect.py`](../tests/qualification/opencode_inspect.py) re-read the stopped run without any model call. Pure parts are tested in `tests/test_qualification_harness.py`.
- Evidence: [`qual-results.json`](../tests/fixtures/smart-routing/probes/qual-results.json) (run 3, plus the re-read) and [`qual-call-ledger.json`](../tests/fixtures/smart-routing/probes/qual-call-ledger.json). Both are sanitized: ids are placeholders, and no paths, names, tokens or passwords are included.
- Model calls: **5 of the 14 authorized**, all `claude-agent/haiku#low`. Four requests were blocked before sending (0 tokens).

## Setup

- **Server.** An isolated `opencode serve` (OpenCode 2.2.6) on a random localhost port, with separate XDG config, data, state and cache directories under a new `0700` temporary directory. It ran with a minimal environment: `HOME` was kept so the `claude-agent` provider could use the local Claude login, plus a dummy `QUAL_PROBE_TOKEN` variable for the shell check.
  - `claude-agent` was connected through its external `claude-login` method, which stores a method reference only.
  - `serve` takes no `--standalone` flag; a plain `serve` without `--service` already runs a private server. It prints a per-run password, which the harness reads but never outputs.
- **Plugins.** Three, in this order, from the project's own `opencode.json`. Nothing was installed globally.
  1. `qual-pre`: a recorder.
  2. The routing shim, with `python`, `settings` and `ledger` pinned to a temporary routing store.
  3. `qual-post`: a recorder that also guards before sending. It blocks any model other than `claude-agent/haiku`, any title except the one allowed, any session on hold, and anything beyond the budget.

  `qual-post` only sees what the shim let through, so its count is the transmitted count. The pre/post pairs show the hooks ran in this configured order.
- **Cleanup.** The server and routing child were stopped and the temporary directory was removed. No process was left.
- **Not separately controlled.** OpenCode's own catalog and update traffic. The harness sets the V1-era `OPENCODE_DISABLE_AUTOUPDATE` and `OPENCODE_DISABLE_MODELS_FETCH` variables, and whether 2.2.6 honors them was not verified. The server also watched `~/.claude/skills`, read-only.

## Results

Legend: **pass** observed as the contract says; **deviation** observed but differs in a detail; **blocker**; **unobserved** did not run.

| Stage / check | Expected | Observed | Result |
|---|---|---|---|
| 0 plugin load | shim and recorders loaded | `vibe-workflow.routing`, `qual-pre`, `qual-post` in `GET /api/plugin` | pass |
| 0 `routing.health` before a ledger | `not_ready` | `not_ready` / `ledger_absent`, HTTP 200, 70 ms (child start-up) | pass |
| 0 `set authority_required=true` | `updated` | `updated`, `authority_required: true` | pass |
| 0 `prepare` | admitted | admitted, `included_subscription`, pool `sub` | pass |
| 0 `routing.health` after `prepare` | `ok` | `ok`, child `up`, 14 ms | pass |
| 0 `status` | `authority_required`, `unstamped_open_holds: 0` | both; `unbound_open_holds: 1` (no `workflow` binding in this run) | pass |
| 1a derived session and message ids | accepted; exactly one request | same ids stored and returned; 1 hook invocation, 1 transmitted, 1 assistant message, outcome succeeded | pass |
| 1b same prompt, same message id | no second request; original returned | HTTP 200 with the original id; 0 hook invocations, 0 transmitted; but a second `idle` record was appended | pass, deviation |
| 1c read back by derived id | found | HTTP 200, same id, `user` | pass |
| 1d OpenCode's own ids | never `ses_` + 32 lowercase hex | session and message ids: 26-character bodies of digits, lower and upper case | pass (n = 1 session, 2 message ids) |
| 1e ordering with derived ids | not reordered by id | `msg_0…` and `msg_f…` derived ids stayed where inserted among server ids; listing is insertion order, not lexicographic | pass |
| 2 `dispatch` | token once; derived ids | `dispatched`, 43-character token (never printed), derived ids, `reconcile_hint.authoritative: false`; repeat answered `already_dispatched` with no token | pass |
| 2 `routing.health` before launch | `ok` | `ok` | pass |
| 2 `attach` with a wrong token | refused | refused: HTTP 500 `rpc.internal` "RPC call failed" (the shim's `attach_refused` code is not visible to the caller) | pass, deviation |
| 2 `attach` with the token | attached | `attached`, about 1 ms | pass |
| 2 managed request | allowed, completes | 1 primary and 1 title transmitted; finish `stop`, outcome succeeded | pass |
| 2 checks carry attempt, session and token | every check | inferred: `allow` needs `authority: verified` from the command; the wire traffic was not captured | pass (inferred) |
| 2 `webfetch` for the attached session | denied | `permission.evaluate` via the permission API: `ask` before the shim, `deny` after it, response effect `deny`; listener 0 hits (no fetch was attempted, because the evaluation was triggered without a model call) | pass (evaluation only) |
| 2 shell environment while attached | credential-like names stripped | `QUAL_PROBE_TOKEN` present before the shim, absent after it | pass |
| 2 subagent on another model | blocked before sending | `subagent: true` command with `claude-agent/sonnet#low`: child session created directly (no `tool.execute.before`), its request blocked by the shim with `route_mismatch`; 0 transmitted. The child's failure then started a parent turn, which the shim allowed (attached attempt) and the recorder held | pass; see findings |
| 2 `detach` with a wrong token, then the token | refused, then detached | HTTP 500 `rpc.internal`, then `detached` | pass |
| 2 prompt while not attached ("without a token") | blocked, 0 tokens | `attach_required`; 0 transmitted, no assistant message | pass |
| 2 prompt after a wrong-token attach | blocked, 0 tokens | `attach_required`; 0 transmitted | pass |
| 3 re-read, `usage_from_messages` | usage for the attempt | refused: `session not in the attempt` (the command child, linked only by `parentID`) | **blocker** |
| 3 title by subtraction | title remainder known | root only: `title_unknown` (`unknown_record`: the child's `synthetic` result message). Session total 4/12/0/0/5592 (input/output/reasoning/cache read/cache write), the primary message 2/4/0/0/4425; the recorder saw exactly one title transmitted | not confirmed |
| 3 `track_events`, `reconcile_with_state` | no gap; zero deltas | not reached | unobserved |
| 3 `record complete`, settlement, clean `status` | window settlement; clean | not run | unobserved |
| 3 `generate` requests | record if any | none seen | — |
| 2 closed attempt after `record complete` | `attempt_closed` | not run | unobserved |
| 2 interactive session, routing child killed | passes untouched | not run (interactive derived-shape sessions did pass with the child up in stage 1) | unobserved |
| 4 lost acknowledgment drill | hold stays counted; `record uncertain`; `reconcile_uncertain` not applicable to subscription | not run | unobserved |

### Latency

Shim time is the interval between the `qual-pre` and `qual-post` hooks, which includes the routing child's round trip for managed sessions. These are small samples:

| Hook | n | Median | Max |
|---|---|---|---|
| `model.request` primary | 3 | 0.64 ms | 0.76 ms |
| `model.request` title | 1 | 3.13 ms | 3.13 ms |
| `permission.evaluate` | 1 | 0.52 ms | 0.52 ms |
| `shell.create.before` | 6 | 0.29 ms | 0.60 ms |

RPC round trips from the launcher:

- `health`: 2–14 ms warm, 70 ms with the child's start-up.
- `attach`: about 1 ms; it makes two child checks.
- `detach`: about 1 ms.
- One-shot `routing.py` commands: 60–90 ms, mostly interpreter start-up.

## Blocker

**A managed attempt that runs a `subagent: true` command cannot be accounted.**

The command creates the child session directly through the server, with `parentID` set to the managed session. No subagent tool call appears in the parent's messages. [`opencode_accounting`](../runtime/opencode_accounting.py) finds children only through subagent tool parts, so it behaves as follows:

- It refuses the attempt when the launcher includes the child, as the wiring's "every spawned descendant session" requires (`session not in the attempt`).
- It does not flag the child when the launcher leaves it out: `child_session_unaccounted` is not raised, so the child's spend would be silently missing.

In this run the child spent nothing, because the shim blocked it. On the admitted route, though, a command child would be allowed (it is a managed descendant) and would spend. That is a P0.8 gap, "usage attributed or marked unknown", and for an API-funded hold an undercount.

The same flow also delivers the child's result to the parent as a `synthetic` message. The adapter does not know that type, so the title remainder becomes unknown, and on an API route every cost becomes `null`. That direction is conservative.

Possible fixes, for the user to choose:

1. Link children by a verified `parentID` (from `session.get`) as well as by tool parts. Flag any session whose parent chain reaches the root but which is not included.
2. Normalize `synthetic` messages as non-model records.
3. Have managed worker profiles deny commands that spawn subagents.

## Other findings

- **Command subagents bypass the spawn hook.** `subagent: true` commands do not pass through `tool.execute.before`, so the shim's spawn check (`subagent_model_required`, the spawn route check) never runs for them. The shim still blocked the child at its first `model.request`, through the lineage it learned from `session.created` (observed). A blocked child's failure starts an extra parent turn on the attempt's route.
- **RPC refusals are opaque to the caller.** OpenCode returns `attach_refused`, `detach_refused` and similar as HTTP 500 `rpc.internal` "RPC call failed". A launcher therefore cannot tell a refusal from a child failure. Both must already be treated as "do not send".
- **A duplicate prompt adds an `idle` record.** Re-sending a prompt with the same message id appended an `idle` (succeeded) record without any request. Accounting ignores `idle`, but a launcher waiting on `idle` should key on its own message.
- **API facts the harness relies on.**
  - Message pages return `cursor: {previous, next}`.
  - `next` stays set on the last page, which comes back empty.
  - `order` together with `cursor` is HTTP 400.
  - Synthetic input with `resume: false` goes to the session inbox, not the history.

  The first two harness runs stopped on the paging behaviour, after one stage-1 call each.

## Open questions

- Whether OpenCode 2.2.6 makes catalog or update requests at start-up despite the environment flags.
- Whether `generate` requests create messages: none occurred.
- Whether real `webfetch` tool execution, rather than permission evaluation alone, is blocked for a managed session: it needs a model call to trigger.
- `reconcile_uncertain` applies only to dollar holds. An uncertain subscription hold has no operator path in the command and keeps its concurrency place. This comes from the contract text; stage 4 did not observe it.

## What remains

1. Decide and fix the [blocker](#blocker). Then re-run stages 3 and 4 and the two post-completion checks of stage 2: `--through 4` repeats stages 1–2, about 4 calls, and the remaining authorization is 9.
2. Run a canary on a real project (S3.7) with `workflow`-bound preparation (`unbound_open_holds: 0`) and `--project`.
3. Install the plugin globally (decision 3 in the matrix), which this run deliberately did not do.
4. Run the Codex, OpenAI and ChatGPT live checks (matrix "Live checks still open"); none was made here.
