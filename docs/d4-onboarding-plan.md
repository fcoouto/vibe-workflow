# D4: Reversible project onboarding

Implementation plan · 26 October 2026 · **Original scope approved for implementation. See [validation](d4-validation.md) for implemented behavior, review and observed limits.**

**Goal:** apply an explicitly approved, current D3 plan through recoverable, idempotent operations. Keep project enrollment inactive until all required adoption changes qualify. A successful apply records configuration and observations; it does not establish client execution or future task readiness.

**Approved follow-up, not implemented here:** after D3 scope/identity support, D4 owns recoverable adoption in normal task-entry coordinator preflight before dependent writes, plus membership recovery in `runtime/changes.py` with `runtime/startup.py`/`runtime/state.py`, under the [canonical membership/update rules](onboard-my-project-plan.md#membership-updates-and-recovery). Carry into policies 01/10 and `skills/onboard-my-project/references/apply-and-recovery.md`. Reuse owned baselines; separate shared-policy from member runtime updates. Prove standing-scope adoption without repeated approval or startup mutation, removal preserving active/uncertain ownership, independent member recovery with shared dependencies, and rollback after later joins/user edits/active pins. Current checkout enrollment below does not implement this seam; preserve its transaction safety and accepted D6 upgrade behavior.

**Basis:** [D3 plan](d3-onboarding-plan.md), [Project Onboarding policy](../policies/10-project-onboarding.md), [approved deliverable](onboard-my-project-plan.md#5-small-deliverables), [source coverage](policy-sources.md), and OpenAI's installed `skill-creator` standard. Keep the entry skill concise and load apply/recovery details only when needed. Runtime uses Python 3.11+ and the standard library.

Current D4 explicitly rejects schema-2 private Project plans immediately after loading, before transaction validation, worktree locks or journal creation. This protects the existing schema-1 transaction path; it does not deliver Project adoption or schema-2 enrollment.

## Scope and reuse

- Add `runtime/changes.py`, focused tests in `tests/test_changes.py`, and extend `scripts/workflow.py` with only `onboard apply`, `onboard resume` and `onboard rollback`. Add narrow dispatcher cases to `tests/test_runtime.py`.
- Reuse D3's private plan directory and plan identity, source/package pins, exact action proposal, input fingerprints and revision checks. The caller supplies `--plan-dir`; D4 does not provision a shared runtime data directory or alter startup/state contracts.
- The coordinator must verify actual user/session authorization through trusted host evidence and record its reference. A bounded, versioned D4 apply input binds that reference to the exact D3 plan revision/digest, D4 manifest digest/content, target checkout and approved scope; runtime verifies the binding and freshness. A changed manifest invalidates approval for affected work. JSON cannot authenticate human intent, and D3 free-text authorization/evidence grants no authority. Add no authentication service. If required host/user evidence is unavailable, remain pending.
- Keep native operation as the default. Optional integrations remain separate from project onboarding.
- Do not add a generic installer catalog or arbitrary command rollback mechanism. Use Toolkit procedures and the exact actions from the approved plan.

## Transaction identity, locks and journal

Resolve the selected checkout's worktree-specific Git directory and common Git directory using the existing Git checkout resolution pattern, with inherited `GIT_*` selectors stripped. A linked worktree must remain isolated from sibling worktrees even though they share a common Git directory. Give each checkout a stable private transaction lock location that is independent of caller-selected plan directories. Never treat the common Git directory alone as checkout identity.

Hold the per-checkout advisory lock across each apply, resume or rollback operation, including owned long-running commands. This serializes D4 transactions even when callers use different plan directories. It does not lock out editors or external tools. Shared/global effects without an appropriate resource lock remain unsupported and pending manual coordination; D4a needs no general lock framework. Use a distinct short journal lock for durable journal reads/writes; do not hold that lock during commands. Lock contention returns a bounded busy result, not a retry loop. D4 must not hold the D2 SQLite task-state lock while onboarding work runs.

Journal transaction identity, exact frozen plan pin/digest, approved authorization reference, action IDs/order/dependencies, original and intended postimage fingerprints, durable backup references, operation receipts, observed results, enrollment ownership and state. Keep recovery files outside repositories and instruction discovery, private to and owned by the current user, bounded regular files; reject symlinks and hardlinks. Write journal records with same-directory atomic replacement; fsync prepared/outcome/state file replacements and their containing directory before returning. The D4 apply input supplements D3 without changing its schema: a bounded versioned manifest binds the plan revision/digest and authorization reference, and gives each action exact effects/resources, preconditions, and recovery/reconciliation method. Reject missing/unknown required data, identity, revision, pin, authorization-binding or unrelated current-input mismatch before dependent mutation. On resume, reconcile transaction-owned outputs through durable receipts against the frozen approved proposal; do not compare them as if they were unrelated input changes or silently accept a refreshed D3 digest.

`apply` accepts the plan directory and this bound manifest; it prepares and starts only currently authorized, fresh actions. `resume` accepts the same transaction and manifest binding, reconciles actual state, and continues only when safe. `rollback` accepts the transaction and its approved recovery scope. Return a sanitized state, completed/blocked action IDs and precise next step; exit `0` only for completed operation, `1` for invalid/stale/unsafe input or busy, and `2` for pending, blocked or partial recovery. Missing evidence or effects blocks only dependent actions; no request can self-authorize through JSON.

States are `prepared`, `applying`, `interrupted`, `applied`, `blocked`, `rollback_partial` and `rolled_back`. Journal transitions are durable and idempotent. A failure or crash leaves enough data to reconcile actual state before continuing; never infer success from a missing process or infer failure from a missing receipt.

## Filesystem mutation protocol

For each approved file write or delete, preserve a durable backup before mutation, including required metadata such as executable mode; fsync the backup file and its parent directory. Then persist and flush a prepared receipt containing the original fingerprint and intended postimage (or intended absence) before changing the target. For replacement, write a same-directory temporary file, fsync its contents and metadata, rename it over the target, then fsync the parent directory. Delete only after the receipt is durable, then fsync the parent. Read back actual state and persist the observed outcome. Rollback uses the same temp-file/fsync/rename/parent-fsync sequence. A backup is recovery material, not enrollment or activation.

On resume or rollback, reconcile the target against the original fingerprint, intended postimage and durable receipts. If it matches the operation's result, continue idempotently. If it contains an unrelated user edit or otherwise matches neither known state, preserve it and block that dependency for manual reconciliation. Never overwrite an unrecognized state. Restore only transaction-owned content that still matches the recorded operation result; reverse dependency order during rollback.

For command or other external-effect actions, require the D4 manifest's exact command/effect, bounded possible mutation paths/resources, preconditions and concrete recovery/reconciliation method. This supplements the D3 action schema; it does not change it. Unknown or incomplete effect sets remain pending manual operation. Do not run an effect whose possible changes cannot be bounded and recovered; do not attempt generic command rollback. Never blindly rerun a command after an uncertain crash: inspect its approved effects and receipts, then establish completion, resume only under a proven idempotency contract, or block for manual intervention. Shared/global effects without suitable resource serialization remain pending. Commands run without the journal lock but while holding the per-checkout transaction lock.

## Enrollment ownership and order

Enrollment is mandatory for successful onboarding. Startup treats a valid `.vibe-workflow/project.json` as active and has no inactive flag. Stage a new record outside discovery and atomically install it last, after all applicable mandatory policy and mechanism changes qualify. Journal activation/deactivation with transaction ownership; reuse trusted scoped approval when it covers activation, without a redundant prompt. Configured hooks, an approval record, or a structurally valid file alone do not qualify client execution.

For an authorized update to existing enrollment, preserve the prior record and ownership separately; before changing supporting configuration, deactivate only the approved enrollment scoped to this transaction by staging/removing its active discovery record. Refuse while affected task writers remain active; confirm writers stopped and reconcile their tasks/evidence first. Do not hold SQLite locks across onboarding. Rollback deactivates transaction-owned enrollment first, restores configuration in reverse dependency order, then restores prior enrollment last only after current checkout, client, policy and runtime compatibility checks pass. A stale prior record stays inactive. If enrollment changes externally, preserve it as-is and block dependent configuration rollback; never deactivate or overwrite externally owned enrollment. Keep backups and partial journal state until recovery is complete.

## Toolkit procedures and skill loading

Use the plan-pinned Toolkit revision, same as D3 resume; a fresh D3 run resolves `main` once and records its revision. Apply only compatible action groups from that pinned procedure set, preserving complete coverage and recording actual proof. Do not create a custom installer catalog. An incomplete source bundle or changed pin blocks only dependent actions.

Require the general review and applicable post-implementation security review required by policy, plus safe rerun checks. These are adoption proof and do not turn planning into a review gate. Load a focused `references/apply-and-recovery.md` for `apply`, `resume` and `rollback`, with applicable contracts, exact Toolkit procedures and only the plan/schema details needed for application. Do not rerun the D3 assessment or reload the whole procedure.

## Implementation and proof

**D4a — transaction engine.** Implement the smallest module and dispatcher for apply/resume/rollback, using the versioned D4 input supplement and proving crash-safe recovery in focused tests. Preserve D3 artifacts and caller-supplied plan-directory behavior.

**D4b — Toolkit adoption.** Execute applicable Toolkit installation/update/reuse by compatible dependency groups, mode-aware configuration and canonical project documents, quality work, integration checks and initial native harness evaluation. Keep complete catalog coverage, actual proof, safe rerun and required general plus applicable post-implementation security review. Qualify client behavior with an authorized disposable project and temporary enrollment where needed so observing native hooks does not depend on enrolling a live project. Full live project enrollment is unnecessary for implementation proof; actual client observations are recorded separately, and unobserved mandatory operation remains unqualified.

For both slices, assign Sol6.1 at high effort to runtime/transaction tests, Luna at medium effort to skill/procedure documentation, and independent Sol review. Small substeps do not each require a separate merge.

Focused acceptance cases must cover:

1. Exact authorization binding rejects changed revision, digest, checkout, source/package pins, current inputs or scope; D3 free text alone cannot authorize.
2. Two plan directories targeting the same checkout serialize apply/resume/rollback; linked worktrees do not block each other. An external edit during a transaction is preserved and does not get mistaken for lock protection.
3. Crash injection at backup, prepared-receipt, temp-file write/fsync, rename, parent-fsync, outcome-record and journal-state boundaries resumes by reconciliation without data loss or duplicate effects.
4. Writes, deletes and mixed-file replacements recover only when current content matches original or the recorded operation result. User edits and changed dependencies block safely.
5. Unbounded command effects and missing recovery procedures remain pending; a crash after a command never causes blind re-execution.
6. New enrollment is mandatory and activated last after qualification. Existing enrollment updates deactivate only scoped owned approved enrollment, wait for affected task writers to stop and reconcile, preserve the prior record, and restore it last only after identity/client/policy/runtime validation. Stale prior records remain inactive; external enrollment changes and active writers block safely.
7. Safe rerun creates no duplicate registrations or changes. Full pinned Toolkit coverage, actual proof, applicable general/security reviews and unresolved blockers are reported separately from package validation and observed client execution. D4b uses a disposable selected project; temporary enrollment may support client qualification without claiming live-project onboarding.

Extend package validation only for the new required paths. Run focused tests, the full unit suite, `python3 -B scripts/validate.py`, skill-creator validation for the entry skill and `git diff --check`. Keep package/helper proof separate from actual native client execution; unobserved execution remains unqualified. No live project enrollment is required for D4 implementation proof.

## Completion boundary

D4 is complete only when D4a transaction recovery and D4b Toolkit adoption/proof both meet acceptance, including mandatory enrollment after qualification, and unrelated user changes remain intact. `applied` or enrollment activation does not itself mean the project is ready for tasks or merge. D5–D7 retain future-task evidence verification, dispatch and merge enforcement.
