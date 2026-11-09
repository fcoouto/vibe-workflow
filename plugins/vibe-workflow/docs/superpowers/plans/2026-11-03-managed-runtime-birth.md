# Managed-runtime birth without unrelated machine history

## Authorized design

The user requested a real no-history regression, a minimal implementation,
validation and independent review. Preserve ownership, pins, recovery and WIP.
The existing D4 birth protocol remains authoritative. Add an explicit absent
machine-history witness instead of preparing temporary-review storage or
pretending that its original ready provenance exists.

Preserve version-1 history records unchanged. Version 2 represents only a
genuinely absent machine configuration, its canonical path and existing physical
parents, with empty owner and screening collections. Its callback remains bound
to the original live acquisition, scope and deadline. An appearing configuration,
changed parent or expired acquisition invalidates an in-progress birth.

Existing prior/peer authority, writer, descendant, journal, baseline and exact-pin
checks remain unchanged. For completed version-2 births, later unrelated machine
storage must not prevent retained-history checks; currently related owners remain
conflicts. Preserve original certificates rather than rewriting their history.
Ordinary read-only enrolled accounting can use that original absent witness;
optional controls requiring temporary storage retain their own prerequisites.

Rejected alternatives: preparing unrelated storage creates an unrequested effect;
silently accepting an empty version-1 history invents ready provenance; skipping
history checks loses related owners and race detection.

## Single implementation slice

Files: `runtime/review_storage.py`, `runtime/changes.py`, `tests/fixture_canonical_birth.py`,
`tests/test_birth_without_machine_history.py`, and affected owning documentation.
No dependencies, schema migration, package repinning, live runtime preparation,
installation, trust, native qualification, publication or cleanup is selected.
Implement in an isolated worktree based on main `b0a2fd3`; leave primary WIP alone.

- [x] Remove the fixture's hidden `prepare_storage` call for `history=False`.
- [x] Observe the genuine no-history regression fail at the ready-history gate.
- [x] Add the bounded absent witness and preserve the ready-history path.
- [x] Cover absent/existing parent, unsafe or appearing config, parent changes,
  expired/escaped callbacks, retained ownership and interrupted recovery.
- [x] Run focused birth/history, existing-store, recovery and file-only checks,
  then package validation and relevant broader tests.
- [x] Obtain independent general/security review of the exact candidate.

Validation: 15 focused cases passed. All 78 selected compatibility/recovery tests
passed after the shared startup mock was corrected to forward `managed` without
weakening its assertions. The mock change received independent follow-up review.
Package validation passed. See [the validation record](../../d4-validation.md).

Before integration, merge newer main `c010e7f`, regenerate the single stale runtime
instruction-map entry and preserve newer startup behavior. Expanded integration
coverage is cumulative 207/207: 205 passing cases plus the two corrected
authority-path assertions on a focused rerun. Independent review covers these
small adaptations; no production behavior was changed after that expanded run.

## Review focus

Absence is not stopped-writer proof. Broken links and malformed/interrupted
configuration must not count as absence. Known missing peers remain protected.
No callback may renew its deadline or outlive its acquisition. Historical ready
certificates keep strict provenance, while unrelated later storage cannot erase
or replace an absent certificate. Preserve relevant related owners and receipts.

Independent review found a stale-at-issuance absence race and a destination that
created its own missing machine parent. Add final capture checks and reject that
destination before mutation. Existing private machine parents remain supported.
Preserve exact original-operation snapshots: later unrelated storage can block
reapplying the original transaction, while retained checks/accounting and file-only
rollback remain available. No original evidence is rewritten.

Package/fixture results establish source behavior only. Report actual client
execution separately; this slice authorizes no new native qualification run.
