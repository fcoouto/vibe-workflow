# D4: Recoverable project configuration

Contract revised 2 November 2026 after the self-host pilot exposed unrelated-history gates in file rollback.

## Operation boundaries

- **Configure:** apply exact authorized file changes and publish their configured Project record. Complete without managed runtime creation or native qualification.
- **Activate:** explicitly prepare required runtime state and qualify mandatory adoption, native mechanisms and applicable enforcement. Configuration alone grants no task or merge readiness.
- **Undo configuration:** restore recorded owned preimages, retain history and report conflicts. Use a supported current recovery reader without rewriting original pins.

The operational contract lives in [apply and recovery](../skills/onboard-my-project/references/apply-and-recovery.md). [Project Onboarding](../policies/10-project-onboarding.md) retains complete Toolkit assessment. [Source coverage](policy-sources.md) tracks authoring requirements. [Earlier validation](d4-validation.md) records historical observations, not current admission requirements.

Explicit managed-runtime creation can start without machine-private temporary
history. The birth protocol records a version-2 absence witness without preparing
that unrelated storage. Original ready-history witnesses remain version 1.
Both paths preserve existing D4 authority, writer, peer, journal, baseline and
recovery checks. Completed absence witnesses tolerate later unrelated storage;
new related history blocks dependent use without changing original evidence.
Refuse a destination that would create a missing ancestor of the machine
configuration before mutation, rather than invalidate its own absence witness.

## Essential transaction protections

Verify actual user authority for exact effects. Parse a bounded supported journal and validate the consumed effect/receipt/backup bindings. Lock overlapping resources, check correct checkout and safe physical paths, preserve changed bytes/modes/inodes, verify original backups or absence, and reconcile relevant live or uncertain writers.

Persist intent before mutation. Use atomic replacement or durable deletion, fsync, readback and resumable receipts. Restore in reverse dependency order. Preserve unrelated changes and unknown ownership. Remove only transaction-created empty directories. A partial rollback continues through rollback, never application resume.

File-only recovery does not require upstream availability, exact executing-package equality, unrelated checkout freshness, machine-wide review history, birth genealogy, evidence republication, native qualification or merge gates. Preserve original records as provenance. Runtime retirement and evidence cleanup are separate effects.

Commands, active enrollment and shared dependencies retain their scoped controls. Reconcile writable descendants before command recovery or replay. Suspend only owned affected active entries; restore prior active enrollment only after fresh compatibility. Preserve unrelated members, later joins and active/uncertain ownership. Do not weaken actual runtime source identity, permission or evidence dependency controls.

## Implementation and proof

Reuse `runtime/changes.py` locks, journals, file receipts, replacement/deletion primitives and public coordinator entry. Select the file path from validated actual effects and configured states. Do not add a caller-controlled bypass flag, compatibility service, signer or registry.

Acceptance covers:

1. Configuration completes without a database or native proof.
2. Unrelated history, source edits and missing Toolkit sources do not prevent undo.
3. A newer supported reader recovers an older journal and preserves original provenance.
4. Edited/replaced files, unsafe parents, corrupt backups, unsupported journals and expanded effects are refused.
5. Conflicting transactions and relevant live/uncertain writers block affected recovery.
6. Interrupted writes/deletes recover without data loss; completed rollback is idempotent.
7. Active, shared and command operations retain their relevant controls.
8. The pilot-shaped two-file rollback succeeds in isolation before live recovery.

Run affected tests, package validation and representative instruction-path review. Report package proof separately from native observations. No qualification campaign is implied by file recovery.
