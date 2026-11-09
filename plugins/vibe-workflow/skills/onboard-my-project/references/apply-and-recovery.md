# Configure, activate and recover

Load this guide for authorized `apply`, `resume`, or `rollback`. Follow [Project Onboarding](../../../policies/10-project-onboarding.md) for configuration and [Project Scope](../../../policies/01-project-scope.md) for affected ownership. Load command, runtime, qualification and merge procedures only when the selected operation uses them.

## Authority and entry

The coordinator verifies actual user or standing authority for the exact operation and effects. Follow [onboarding authority](../../../policies/10-project-onboarding.md#onboarding-authority), including full-request coverage and limited-request boundaries. References, JSON and digests bind the decision but cannot authenticate intent. Keep the approved plan, manifest and original source/package pins unchanged throughout recovery.

For canonical schema 2, call [`run_verified_operation(verified_decision, plan_dir, proposal)`](../../../scripts/onboard_coordinator.py). The decision binds the operation, private plan directory, frozen plan digest and approved manifest/approval. `bookkeeping_scope` names subjects and authorities only for selected runtime bookkeeping. Omit `creation` for file configuration. The bare JSON CLI cannot supply verified coordinator authority.

Retained schema 1 uses `apply_plan`, `resume_plan`, or `rollback_plan`, or `onboard apply|resume|rollback --plan-dir PATH` with bounded JSON on stdin. Its discovery record is active; it has no configured state. Use its installed schema and active-enrollment rules. Do not translate a canonical operation into legacy deletion or invent an inactive flag.

## File configuration and undo

A file-only canonical operation contains local write/delete actions, `execution:null`, exact preimages/postimages and file-preimage recovery. Prior and proposed client entries must be configured, with no shared-policy transition. Commands, external changes, activation, prior active enrollment and shared dependencies use their scoped procedures below.

Keep seven protections:

1. Verified authority for the exact operation and effects.
2. A supported, bounded journal whose consumed effects, receipts and backups agree.
3. Correct checkout identity and safe target paths, parents and links.
4. Current bytes, modes and physical ownership matching a recorded transaction state.
5. Valid original backup bytes/modes or recorded original absence.
6. Overlapping-operation locks and checks for relevant live or uncertain writers.
7. Durable intent before mutation, atomic replacement/deletion, fsync, readback and resumable receipts.

Keep recovery material private, outside repositories and instruction discovery. Accept bounded regular files; reject unsafe links. Before mutation, preserve originals and flush the backup and prepared receipt. Record transaction-created directories before using them. Recheck mutable ownership immediately before replacement and after external actions or released locks. Advisory locks do not prevent external editors.

Rollback restores owned preimages in reverse dependency order. Preserve user edits, replacement files and uncertain ownership. Remove only recorded transaction-created empty directories. A partial rollback continues with `rollback`; `resume` continues application. Repeated completed rollback verifies the restored state and performs no duplicate effects.

Undo reads the supported journal, receipts and backups. It does not require the original executing package, available Toolkit sources, unchanged unrelated files or HEAD, machine-wide review history, native qualification, or runtime evidence republication. Preserve original provenance and retained databases/evidence. A missing or replaced previously allocated runtime leaves writer ownership uncertain and blocks affected recovery. Runtime retirement and evidence cleanup are separately scoped operations.

Configuration publishes the exact approved configured record and completes with exit 0 when its effects succeed. It grants no managed task readiness. Use `runtime:null` when runtime state is not needed; a planned runtime location does not create a database. Defer database birth until an explicitly selected managed-runtime preparation/activation operation requires it.

## Apply selected setup changes

Use the plan-pinned Toolkit procedures for setup actions that need them. Preserve complete catalog assessment and mandatory coverage; do not rebuild assessment during undo. Missing sources block only dependent setup actions. Apply compatible dependency groups, preserve product behavior, data, customizations, local changes and the current stack. Check relevant versions, peers, engines and lockfiles before dependency changes. Reuse verified equivalents. No forced installs, automatic audit downgrades, redundant dependencies or framework/provider migration to match examples.

Major incompatibilities, migrations, new costs, destructive or out-of-project changes require authority covering those effects. A reviewed section label does not authorize losing other content. Metadata-only inventory is not an exact preimage or recovery backup. Safe reruns preserve existing sections and registrations.

## Commands, active runtime and shared changes

Commands require exact bounded effects/resources, preconditions and a concrete recovery method. Persist attempt identity and retain exclusion while running. Reconcile the command and all writable descendants before completion, rollback or replay; uncertain termination remains pending. Never perform generic command rollback or blind replay. Require proven idempotency before replay. Missing proof blocks dependent work only.

Active runtime and shared-policy changes retain their existing authority, source identity, current permission, dependency and affected-writer checks. Preserve unrelated members and history. Suspend only owned affected active entries before changing supporting configuration. Restore prior active enrollment only after fresh compatibility and stopped-writer proof. Later joins, external authority changes or unresolved shared consumers require scoped reconciliation. File-only recovery cannot bypass these controls.

Explicit runtime preparation uses owned D4 birth for an absent database, original baseline and receipts. Startup and task start never create it. This is a separate effect requiring explicit scope; it is not a prerequisite for file configuration or undo.
