# D2: Native runtime implementation plan

25 October 2026. Implementation and bounded native qualification are recorded in [D2 validation](d2-validation.md). The bundled controlled Stop-held Interrupt case passed; ordinary read-first correlation, desktop UI and complete workflow enforcement remain unqualified.

**Goal:** add durable task state, a narrow Codex action guard, conservative invalidation, bounded Stop handling and independent diagnostics.

**Basis:** the [D2 deliverable](onboard-my-project-plan.md#5-small-deliverables) and [approved contracts](../policies/00-index.md). Use Python 3.11+ and its standard library. Keep each step independently reviewable.

## Scope and current evidence

- Native operation is the default. D2 introduces no backend framework.
- Keep Codex and Claude startup behavior. Qualify the new lifecycle on Codex first; Claude lifecycle follows D8. D2 does not require a Claude account.
- D3–D4 own onboarding and recovery; D5 owns check/review verification and readiness; D6 owns worker model/effort selection, dispatch and resource reservations while preserving coordinator settings; D7 owns repository enforcement. D2 does not grant these capabilities early.
- Use disposable enrollment fixtures until D4. Existing startup-only enrollment must not silently enable writes to runtime state or guards. No real project enrollment or settings changes belong to this slice.

Repository inspection found reusable scope, strict JSON and safe record readers in `runtime/startup.py`. The dispatcher renders every failure as a startup diagnostic; the validator accepts only `SessionStart`. Both need event-specific changes. The existing 54 startup tests and package validation passed during planning.

Installed Codex CLI `0.163.0` and desktop-bundled `0.164.0-alpha.11.1` expose hook discovery/trust and execution notifications. Their schemas were inspected without model calls. D1 startup evidence does not qualify new events.

The [Codex hooks contract](https://learn.chatgpt.com/docs/hooks) supports denying canonical `apply_patch` calls, post-tool observations and Stop continuation. Passing should emit no permission override. Unsupported pre-tool responses can leave execution running. Hosted/specialized tools and later `write_stdin` input prevent complete interception. Stop is a continuation mechanism, not a merge or completion security boundary.

The [app-server interface](https://learn.chatgpt.com/docs/app-server) has no inspected direct hook-run endpoint. User shell commands cannot stand in for model tool calls. Native qualification therefore needs a verified event-producing path, normally a bounded native model turn with the existing authorized model, provider and authentication. Do not add a mock provider, proxy or API-key fallback.

## Concrete runtime decisions

### Activation and state

Add an optional `runtime` object to the existing enrollment record: `schema_version: 1` and `state_dir`, an absolute canonical machine-local directory outside repositories and plugin caches. Its absence preserves D1 behavior. Malformed configuration is an error, not a fallback to another location. D4 will eventually provision it; D2 tests supply it explicitly.

Use one SQLite database, `state_dir/runtime.sqlite3`, with short transactions and a 100 ms busy timeout. Only explicit task controls initialize owned state; hooks and `doctor` never create a missing database. Require a private current-user directory and regular database files; reject symlinks, unsafe permissions and incompatible schemas. Keep journal files private. Filesystem support must be qualified locally; no network filesystem support claim.

Persist only:

| Record | Required content |
| --- | --- |
| Checkout | Project ID, canonical checkout/common Git identity and monotonically increasing change generation |
| Task/binding | Task ID, native client/session, scope reference, read-only/editable mode, state, revision and accepted policy/runtime/schema pins |
| Condition | Stable ID, reason/reference, affected action (`patch` or entire bound task), optional current-turn continuation and consumed count |
| Receipt | Event/client/session/turn/tool-call identity needed for idempotence; no raw tool output or transcript |

Bind by project, checkout/common Git directory, native client and session; never by session string alone. Startup, resume and compaction cannot replace bindings or version pins. A different task requires an explicit binding change. Local records are not a security boundary against another process with the same user permissions.

Task states remain `planned`, `active`, `awaiting_user`, `blocked`, `verifying`, `cancelled`; reserve `ready` for D5's verifier. A pause is `blocked` with a pause reason. No D2 control can declare checks passed, approve a user decision or grant permissions. Decision references document existing authority; they do not create it.

Allowed transitions: `planned` to `active`; `active` to `verifying`; `verifying` to `active` for further work. Any non-cancelled state may enter `awaiting_user`, `blocked` or `cancelled`. A resolved wait/block may return to `planned`, `active` or `verifying` with a resolution reference. Same-state updates may revise conditions; cancellation is terminal. Mode and version pins are immutable within a task. Patching requires `active`, editable mode and no applicable blocker; `planned` and `verifying` deny it.

### Hook behavior

| Event | D2 behavior |
| --- | --- |
| `SessionStart` | Preserve orientation; for runtime-enabled fixtures add the local runtime pointer and native session identity within the existing 150-word context limit. Read existing state only. |
| `PreToolUse` | Match `^apply_patch$`. Deny unless a compatible bound task permits patching as defined above. Eligible tasks pass with empty output and unchanged arguments. |
| `PostToolUse` | Observe all supported local tools. Conservatively advance checkout generation once per distinct receipt, including failed or unknown operations. Do not infer successful checks, parse shell commands or replace tool results. |
| `Stop` | Return empty JSON unless an explicit, actionable continuation meets the conditions below. Never interpret final assistant prose as proof. |
| `Interrupt` | Revoke continuation and record a pause only for a correlated task/turn. A delayed interrupt must not affect a newer binding; unknown correlation records uncertainty without mutating its task. Never restart or prevent interruption. |

This guard prevents supported patch calls; it does not make a session fully read-only. Shell edits, unmapped tools, external writers, ownership and merges remain outside its coverage. A blocker affects only its recorded action/task; independent tasks and read-only investigation continue.

A generation increment makes earlier evidence stale; no increment does not prove freshness. Late post-tool events invalidate the checkout without being reassigned to a newly bound task. Deduplicate only a qualified stable identity: client/session/turn/tool-call/event kind. Missing identity means invalidate on every observation and report deduplication unverified; never substitute a payload hash. Conflicting ID reuse invalidates conservatively and records uncertainty. D5 must compare actual content/base/check definitions/environment/policy again, regardless of hook receipts.

Continuation defaults to zero. The coordinator may record at most one for a specific runnable condition, only within existing task authorization, with the observed turn ID and any smaller user budget. Store that task/turn association; a guessed ID grants nothing. Consume it atomically before emitting a continuation. Never rearm the same condition through edits, retries, resume or process restart. Missing turn identity, `stop_hook_active == true`, paused/cancelled/awaiting-user/blocked state, unresolved decisions, external blockers or exhausted budget all suppress continuation. A new user turn invalidates the old turn's request. Qualify interruption behavior before enabling automatic continuation.

Retain bounded strict input and sanitized diagnostics. Ordinary hooks keep the five-second timeout; Interrupt uses one second. No network, model calls, full tests or repository-wide content scans in hooks. Missing/broken state cannot yield readiness. When a running guard can safely identify its enrolled scope, deny its guarded action on state failure; otherwise report uncertainty. A skipped, crashed or timed-out hook may not block anything: measure native behavior and report the protection gap.

### Task controls and diagnostics

Extend the existing dispatcher; do not add an MCP server or another CLI package:

- `task start --cwd PATH`: bounded JSON stdin containing `client`, `session_id`, `task_id`, `scope_ref`, `mode` and `decision_ref`. Validate enrollment, initialize state if absent and bind the task as `planned`. Repeating the same request is idempotent. Replacing another current binding requires `replaces: {task_id, expected_revision}` matching it atomically; otherwise reject. Preserve the old task/history, increment its revision and revoke its pending continuation. Rebinding does not prove old work stopped or release resource ownership.
- `task update --cwd PATH`: bounded JSON stdin with the same identity, `expected_revision`, next non-ready state, reason/reference and scoped conditions. Use compare-and-swap; preserve consumed continuation history. Validate transitions and reject implicit version changes or removal of unresolved decisions without a recorded resolution.
- `task status --cwd PATH --client codex --session ID`: return the current binding, revision, blockers, generation and limitations without creating state.
- `doctor --cwd PATH --client codex --executable PATH`: inspect package, enrollment, database compatibility and native hook registration/trust independently of hook execution. Query `hooks/list` through the selected executable's app-server: five seconds per request, fifteen seconds overall, then stop only the process doctor started. Never trust/enable/repair hooks, launch a model turn or expose authentication/configuration contents.

CLI controls return JSON and exit `0` on success, `1` on invalid/blocked operations. `doctor` returns `0` only for its explicitly reported checks, `1` for detected failures and `2` for incomplete observations. Its result is not workflow readiness.

Doctor reports inspectable configuration, versions/digests and current trust separately from execution coverage. It leaves historical/current execution unverified and points to the manually reviewed validation report; it does not scrape that report as proof. A fresh standalone app-server inspection does not prove the already-running desktop session loaded those definitions. Unsupported or unobservable state stays explicit. Automated comparison with trusted execution evidence belongs to D5; no evidence database is needed now.

## Implementation steps

Use a failing behavioral test, smallest implementation and focused rerun for each step. Preserve existing uncommitted work; this plan does not authorize commits or publishing.

### 1. Reuse scope and define event boundaries

**Files:** modify `runtime/startup.py`, `runtime/adapters/codex.py`, `scripts/workflow.py`; add `runtime/engine.py`, `tests/test_runtime.py`.

- [x] Extract `resolve_project(event, package) -> ProjectScope | None` from `orient()`. `ProjectScope` contains validated project/checkout identity, policy digest and optional runtime settings. Keep `orient()` and shared startup rendering compatible.
- [x] Add `normalize_runtime(payload) -> RuntimeEvent` and `render_runtime(result) -> dict | None` in the Codex adapter. Define `RuntimeEvent` and `HookResult` in the new `runtime/engine.py`: event kind, native identity, optional turn/call/tool fields and only needed observations; result action `pass`, `deny`, `context` or `continue` with a sanitized reason.
- [x] Validate event-specific fields, CLI/event agreement, input limits and output schemas. Ignore transcript paths and unused payload contents. Keep Claude's shared startup adapter and registration unchanged.
- [x] Prove legacy/unrelated/wrong-client/worktree behavior, malformed events and event-specific errors. Final integrated suite passed; see [validation](d2-validation.md).

### 2. Persist scoped task controls

**Files:** add `runtime/state.py`, `tests/test_state.py`; extend `scripts/workflow.py` and runtime tests.

- [x] Implement `start_task(scope, request)`, `update_task(scope, request)` and `task_status(scope, client, session_id)`, returning the documented JSON records. Keep database access in this module.
- [x] Implement `process_event(scope, event) -> HookResult`: read state and apply the engine's decision in one bounded transaction. The engine's pure `evaluate(event, snapshot) -> (HookResult, changes)` returns only the generation, receipt, condition and task updates allowed above.
- [x] Test restart persistence, identity collisions, allowed/rejected transitions, idempotent start, explicit/rejected rebinding, preserved old history, revision conflicts, version pins, forbidden ready transitions, private paths and corrupt/locked/schema-incompatible state. Confirm hooks cannot initialize state.
- [x] Run focused state/runtime tests; see [validation](d2-validation.md).

### 3. Implement the patch guard and invalidation

**Files:** extend `runtime/engine.py`, `runtime/state.py`, Codex adapter, dispatcher and their tests.

- [x] Prove read-only and missing/blocked/incompatible task denial; editable pass must contain no permission grant or input rewrite.
- [x] Implement post-tool generation/receipt handling. Keep tool responses out of persistence and context; never convert process success into verification evidence.
- [x] Test scoped partial blockers, unknown tools, nonzero tool results, duplicate/conflicting/out-of-order/missing-identity receipts, late completion after rebinding and separate checkouts. Confirm independent tasks remain usable.
- [x] Run runtime/state tests. Native proof is recorded separately in step 6.

### 4. Implement bounded Stop and interruption

**Files:** extend engine, state, adapter and runtime/state tests.

- [x] Implement atomic consumption keyed by task and stable condition, with the turn and budget checks above. Record interruption without emitting a continuation.
- [x] Test concurrent Stop requests produce at most one continuation; restart, edits and retries do not reset it. Test pause, new user turn, cancellation, pending decision, external blocker, missing identity, zero budget and state failure produce none. A delayed Interrupt after rebinding must preserve the new task and its continuation.
- [x] Run runtime/state tests. A Stop that permits ending must not mark the task ready.

### 5. Wire hooks and independent doctor

**Files:** add `runtime/doctor.py`, `tests/test_doctor.py`, `clients/runtime.md`; modify `hooks/codex.json`, `scripts/validate.py`, dispatcher, `clients/codex.md`, `clients/startup.md` and `clients/index.md`.

- [x] Implement `inspect_runtime(scope, executable) -> report`, with bounded native discovery and no repair/model calls. Test missing, disabled, untrusted, modified, unavailable, timeout and mismatched-version observations using sanitized protocol fixtures.
- [x] Register each Codex event above exactly once, synchronously. Extend the validator's explicit event/command/timeout expectations; retain exact startup and Claude duplicate-registration checks. Changed definitions require fresh native trust.
- [x] Document only runtime configuration, controls, loading triggers and coverage limits in the client files. Keep provenance and qualification details in `docs/`.
- [x] Run all unit tests, package validation and `git diff --check`; see final results in [validation](d2-validation.md).

### 6. Qualify native behavior and record limits

**Files:** add `docs/d2-validation.md` with actual observations; update client coverage statements only after proof.

- [x] Use disposable repositories/state, reviewed hook definitions and normal client trust. Preserve the authorized model, provider, account and permission settings; do not copy credentials or weaken isolation to make a probe run. If an isolated authorized path is unavailable, mark native qualification blocked rather than substituting a fixture.
- [x] Observe CLI read-only denial, editable patch, post-tool invalidation including delayed completion, bounded continuation and correlated interruption. On the bundled app-server, observe read-only/editable guards, invalidation and bounded continuation, plus one controlled correlated Interrupt while unchanged Stop output was held. The earlier ordinary two-tool Interrupt attempts remain unqualified; this controlled case does not remove the read-first correlation limit. See [validation](d2-validation.md).
- [x] On CLI, separately test unenrolled/legacy enrollment, missing/disabled/untrusted/modified hooks, malformed output, crash, timeout and state lock. Record actual tool outcomes; do not infer fail-closed behavior from intended responses. Bundled failure cases remain untested.
- [x] Preserve Claude startup regressions. Desktop UI, Claude lifecycle and model policy compliance remain unqualified.
- [x] Record versions, candidate digests, trust/settings, outcomes and limitations. Local dispatcher p95 and context limits were measured; native p95 and token savings are not claimed. Root's final independent review was clear. Bundled failure and delayed-Post matrix cases remain untested.

## Completion boundary

D2 implementation and its bounded native qualification are recorded in [D2 validation](d2-validation.md). Observations cover the listed CLI paths and bundled core/controlled-interrupt paths only; they do not qualify the untested bundled fault/delay matrix or all turn-correlation orders. Passing package tests alone is insufficient. No onboarding, readiness, cross-vendor dispatch or merge-enforcement claim follows from D2.
