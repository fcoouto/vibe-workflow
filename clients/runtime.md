# Codex task runtime

Read this contract only when a Codex task needs explicit task controls, a `doctor` report, or lifecycle troubleshooting. Startup-only projects do not need it. For native delegated work only, also read [native delegation](delegation.md). For planned checks, review, completion, or local merge evidence, also read [task checks and review evidence](evidence.md). Shared policy remains in the [policy index](../policies/00-index.md); native qualification status and procedures are in [validation guidance](../docs/d2-validation.md).

For code changes to canonical Project/Repository scope or state, also consult [the internal schema-3 boundary](#internal-canonical-schema-3). Routine task controls remain under the public contract below.

This CLI and its evidence, delegation, doctor and retirement APIs remain Codex-only. Read the [Claude Code prepared guard](claude-code.md#prepared-file-guard) only when the task concerns Claude Code support.

## Runtime authority

For a fresh canonical Project scope, the selected client configuration uses:

```json
"runtime": {"schema_version": 3, "state_dir": "/absolute/private/path"}
```

Choose a canonical absolute state directory outside all repositories and plugin caches. The directory and files must be private to the current user. Invalid or conflicting authority produces a diagnostic; startup never creates or repairs state. Owned D4 birth creates canonical `runtime.sqlite3`, its original baseline and receipts; `task start` rejects an absent database. Runtime-enabled legacy enrollment grants no current task, readiness or mutation authority, even with an absent, empty, retired-only or live store. Use its original pinned package. Legacy enrollment without runtime retains startup-only orientation. For historical inspection and updates, load [Change a pinned runtime](#change-a-pinned-runtime). Startup may mention the state path, but does not expose task records in context.

All participating sessions and checkouts with potentially overlapping resources must use one approved shared state authority. Different worktrees or private databases do not establish isolation. If authority or alias coverage is unknown, serialize or block affected work until ownership can be reconciled. Reservations coordinate participating owners; uncontrolled external writers remain a limitation.

Canonical Project authority is schema 2; its runtime configuration and SQLite store are schema 3. Retained formats and operation-proof versions remain distinct. For enrollment fields and diagnostics, read [shared startup](startup.md). Schema validation does not authenticate host intent or establish native execution.

## Controls

Run controls from the installed package's `scripts/workflow.py`, using the enrolled checkout as `--cwd`. Start and update take one JSON object on stdin; status and doctor take flags. Examples:

```sh
python3 -I -B /path/to/package/scripts/workflow.py task start --cwd /checkout < start.json
python3 -I -B /path/to/package/scripts/workflow.py task update --cwd /checkout < update.json
python3 -I -B /path/to/package/scripts/workflow.py task status --cwd /checkout --client codex --session SESSION
python3 -I -B /path/to/package/scripts/workflow.py doctor --cwd /checkout --client codex --executable /path/to/codex
```

Append `--prepare-observability` to `task status` to prepare optional local data from the same report. It adds `observability_preparation`: `{status:"prepared",data:...}` or `{status:"unavailable"}`. Without the flag, output is unchanged. Ordinary status revalidation still applies; the flag enables neither collection nor exports.

`start.json` requires `client:"codex"`, `session_id`, `task_id`, `scope_ref`, `mode` (`editable` or `read-only`), and `decision_ref`. Optional `replaces` is exactly `{task_id, expected_revision}` and is required to explicitly replace a current binding. A new task starts `TRIAGE` at revision 1. Repeating the same start identity is idempotent; task identity, mode, references and policy/runtime pins cannot be silently changed. Rebinding preserves history and revokes old continuations. Replacement/rebinding cannot clear unresolved assignments, including retained historical ownership; reconcile exact attempts first.

`update.json` requires `client`, `session_id`, `task_id`, `scope_ref`, positive `expected_revision`, `state`, `reason`, and `reference`. Optional `resolution_ref`, `conditions`, `resolutions` and `milestones` follow the schemas below. A revision conflict requires reading status and preparing a fresh update. The canonical task states are `TRIAGE`, `READY`, `WORKING`, `REVIEW`, `WAITING`, `BLOCKED`, `PAUSED`, `DONE` and terminal `CANCELLED`. These exact values are used internally and in managed titles. `READY` means scoped and eligible to begin; only successful evidence completion verification can set `DONE`. Ordinary task updates cannot declare completion. `TRIAGE` can enter `READY`, `WORKING` or `REVIEW`; `READY` can enter `WORKING` or `REVIEW`; `WORKING` and `REVIEW` can alternate. Any non-cancelled task can enter a wait, block, pause or cancellation. Resuming `WAITING`, `BLOCKED` or `PAUSED` requires `resolution_ref` and a target of `TRIAGE`, `READY`, `WORKING` or `REVIEW`. New authorized work can reopen `DONE` as `WORKING` or `REVIEW`, preserving earlier evidence while ending its current completion authority. Cancellation is terminal, even for same-state updates. Same-state updates can revise conditions and milestones. Native turns, check attempts and resource reservations retain their own protocol statuses; they are not task states.

A condition is exactly `{id, kind, action, reason, reference}` with optional `continuation`; `kind` is `blocker`, `decision`, or `runnable`, and `action` is `patch` or `task`. A continuation is exactly `{turn_id, budget}`; budget is integer `0` or `1`, and zero disables continuation. A resolution is exactly `{id, reference}` and resolves a previously recorded condition explicitly. Unknown fields reject. IDs are stable; kind/action cannot change. Omitted or empty conditions preserve history. At most 64 conditions/resolutions per request and 64 condition records per task. Condition IDs are at most 128 characters; other control text is nonblank, printable and at most 1024 characters (except IDs); task IDs are at most 128. References identify evidence or user input; they never grant authorization. Only explicit resolution clears a pending blocker or decision. Mode and pins are immutable.

An optional `milestones` list records the existing task plan's accepted progress. Each entry is exactly `{id, weight, accepted, reference}`: a unique printable ID of at most 128 characters, an integer weight from 1 to 1,000,000, a boolean acceptance flag, and a nonblank printable reference of at most 1024 characters. At most 64 entries are allowed. Supplied lists replace the current milestone plan atomically with the task revision; omission preserves it. The coordinator records accepted milestones from actual evidence or user acceptance, not worker self-reports. These weights affect presentation only and never satisfy completion verification.

Success prints a JSON object with `ok:true`, `task` (or null), checkout `generation`, `blockers`, and `limitations`. Errors print `{"ok":false,"error":"..."}`. Controls return 0 on success and 1 on error. `doctor` prints its structured report and exits 0 for passed inspected checks, 1 for detected failure, or 2 for incomplete/unsupported observation. A doctor result does not establish desktop execution.

For managed display titles, read [title synchronization](workstreams-titles.md).

## Coordinator resource claims

Use these model-free controls only when already authorized coordinator work needs a shared resource reservation. They require no native plan, discovery, model selection or guidance. Direct checks reserve their own resources through [checks and reconciliation](evidence-checks.md), with the [common fields](evidence.md#request-fields).

```sh
python3 -I -B /path/to/package/scripts/workflow.py delegation coordinator reserve --cwd /checkout < reservation.json
python3 -I -B /path/to/package/scripts/workflow.py delegation reconcile --cwd /checkout < reconciliation.json
```

Reservation takes the common identity/revision plus stable `dispatch_id`, exact `claims`, `operation:{id,kind:"coordinator-work"}`, `reason`, `ownership_ref` and `stop_conditions`. Use `reserve_coordinator`, `_claims` and `_validate_operation_proof` in [runtime/delegation.py](../runtime/delegation.py) for the bounded schemas. Replay returns the same reservation without another operation or native launch permission. Match an applicable existing D6 coordinator job; otherwise the task's accepted scope/decision and explicit resources suffice. Reservation never grants Git, commit, publishing or settings permission and consumes no native attempt/resource units.

Reconciliation takes identity/revision, `attempt_id`, private `host_proof:{path,sha256}` and an independent `assessment` of outcome, changed paths, result, partial changes and supporting evidence. The version-2 `coordinator-operation-reconciliation` proof binds the exact operation, claims and private operation/resource observations. Verify that the operation and its writable descendants stopped and affected resources are quiescent; these claims do not assert that the live parent coordinator stopped. Retain original D5/D6 plans and candidate bindings only when present at reservation; never invent them for a task-only operation. JSON integrity checks cannot authenticate host evidence. Uncertain operations retain ownership until exact reconciliation.

## Guard and continuation limits

The Codex runtime hooks require configured runtime state; absent configuration preserves startup-only behavior. After a valid runtime scope is established, state or pin failure denies guarded `apply_patch`; invalid configuration itself produces a diagnostic and does not establish the runtime guard. Skipped, crashed or timed-out hooks may allow execution. The only write guard is canonical `apply_patch` during a `WORKING` editable task without a pending applicable blocker or decision. Other tools, shell commands and external edits remain outside the guard. Post-tool events conservatively advance checkout generation; generation is invalidation metadata, not a check or review result.

Stop continuation is opt-in through one runnable condition, current correlated turn, budget exactly 1, no unresolved blockers/decisions and eligible `WORKING`/`REVIEW` state. At most one continuation is consumed per task/turn, even across conditions, edits, retries, process restarts and concurrent Stop events; a genuinely new bound turn has its own allowance. Consumption occurs before continuation output, so it cannot be rearmed for that task/turn. A PostToolUse event with a previously unseen turn suppresses old-turn continuation without associating that completion with the task or pausing it. If Post arrives before canonical Pre establishes a turn binding, a later Pre for that same turn cannot adopt it; continuation or correlated pause requires a turn first established by canonical Pre. Missing or uncertain correlation passes without continuation. Interrupt can pause a correlated task; see [native qualification limits](../docs/d2-validation.md). Task state and references do not prove permission or task ownership. Readiness requires current evidence verification; status rechecks `DONE` tasks against actual content. A `DONE` result does not grant merge authority.

## Change a pinned runtime

The uniform task vocabulary is a pinned runtime change. Old M task records and E lowercase schema-3 history keep their original packages, readers, paths, evidence, receipts and pins. E `ready` meant completion; it is not M `READY`. The composed binary is not a universal historical reader. Supported M history may be inspected only through explicit read-only D4 inspection with original proof and shape checks; E lowercase schema-3 history remains with its original E package and reader.

Load this section only for an authorized runtime update or recovery. Follow [application and recovery](../skills/onboard-my-project/references/apply-and-recovery.md) for D4's approved plan and mutation controls. Reconcile affected writers with their supported old pinned runtime before retiring historical tasks. Retirement preserves original pins/history and cannot declare `DONE`; its exact proof requirements remain in [task retirement](evidence-retirement.md).

For a fresh scope, create canonical Project authority and its schema-3 database only through the approved D4 birth operation after it verifies proposed, actual and backup authority coverage and all affected writers. The database is seeded with its original baseline and ownership receipts; task start refuses to create it directly. Preserve existing M schema-2 and E schema-3 authorities, artifacts, packages, paths and pins unchanged. Do not migrate in place, copy old successful evidence, silently repin tasks or roll back into an old active runtime. A separate state directory does not clear old ownership. Unknown authority, an unsupported old runtime or unverified writer/descendant state blocks the affected transition.

D4's shared authority guard protects relevant reservation scans through enrollment changes. A protected long operation keeps a durable owner reference while running; a crash or elapsed time does not clear it. Use supported resume/rollback to reconcile that exact operation and its descendants before continuing. Package checks and successful activation do not establish native client execution.

## Internal canonical schema 3

This section applies only when changing or diagnosing canonical Project/Repository scope and state internals. Public Codex task controls use canonical Project authority schema 2 and fresh runtime/SQLite schema 3 through the approved D4 birth boundary above. The private Project-record schema 2 decoder returns a `CanonicalRecord`; `resolve_canonical_scope` yields an `AcceptedScope` only after authority, checkout, client and qualification observations. Canonical task state uses schema 3. That scope binds immutable project, repository, checkout and client identity, accepted-scope digest, policy/runtime pins, and physical checkout identity. Stored task identity and pins cannot be silently replaced. Schema-3 writes require the accepted scope and checked physical authority; they do not authorize bypassing the existing transaction guards.

Public task/evidence/delegation handlers use canonical acquisition and scope comparison. Runtime-enabled legacy enrollment does not supply current authority; retain its original pinned reader. Only independently observed qualification facts can yield an active canonical scope; receipt strings or cached shapes alone cannot. Private Project-plan onboarding commands are separate. Internal linked verification can check the bounded complete participant set only from independently checked trusted-coordinator observations; it does not create membership or qualify native execution. For lifecycle closure scope and remaining limits, load [canonical evidence lifecycle](evidence-internals.md) only for internal storage, linked verification or lifecycle work.

The private read-only `state._lifecycle_identity` reader resolves original schema-3 identity, pin/request digests and age from live rows or exact retirement markers in a physically checked authority. A retired assignment marker does not retain the removed assignment artifact pointer; the D4 certificate consumer must compare that pointer with retained owner-issued proof. This identity lookup does not establish closure, completion or cleanup eligibility.
