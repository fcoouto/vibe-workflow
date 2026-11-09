# Onboarding file inventory implementation plan

**Goal:** Let large untouched files coexist with onboarding without reading their bodies or weakening required content checks.

**Approved design:** Record metadata separately from verified content; retain strict source, instruction, edit and backup bounds. No streaming hashes or new infrastructure. This chat owns implementation and review.

**Scope:** `runtime/onboarding.py`, its freshness caller in `runtime/changes.py`, focused disposable tests, onboarding instructions and their source-coverage record. Candidate capture keeps the strict default snapshot. Existing checkouts and pilot artifacts remain untouched.

- [x] Add regressions for both plan schemas: large assets, saved-plan roundtrip, dependent evidence/edits, instruction gaps and metadata changes.
- [x] Add an explicit inventory version and metadata-only observations. Preserve complete small-file fingerprints, aggregate bounds, safe path handling and old-plan behavior.
- [x] Make proposal validation distinguish untouched observations from required content. Reuse existing evidence, decisions, document paths and action targets; add no dependency framework.
- [x] Carry inventory semantics through review/apply freshness; keep mutation and backup readers strict.
- [x] Document coverage limits, run focused tests and package validation, then review the complete diff.

Acceptance includes assets above 1 MiB and 8 MiB, no body acquisition for those assets, no secret reads, no link traversal, no metadata-as-content evidence, exact-input downgrade detection, and unchanged source/edit bounds. Unknown content dependencies remain unresolved. Enumeration exhaustion is still incomplete coverage.

Baseline: local `main` at `44de11fa14bb2dddef02a3fbabb521d9bc10ecc4`. Legacy `tests.test_onboarding` fails before edits (4 failures, 22 errors): its fixed revisions conflict with automatic screening. New tests use the existing isolated fixture and observed revisions. Do not change unrelated screening behavior.


## Verification and review

- Focused delivery run: 16 tests passed, including schema-1 application and canonical apply/resume/rollback with a 9 MiB untouched asset.
- Package validation passed; `git diff --check` passed. Native client execution was not checked.
- Existing canonical composition/recovery/rollback run: 33 of 34 passed. `test_lost_current_permission_keeps_committed_intent_and_destination_unpublished` failed once and passed on an isolated rerun; the transient cause is not established. Do not describe the entire broader run as clean.
- Independent native review found one P2: remaining aggregate allowance could change a small file from exact content to metadata, then cause false application drift. The focused reproduction failed before the fix and passed afterward. Metadata eligibility now uses the per-file bound; aggregate exhaustion remains an explicit gap. No remaining actionable review findings.
- Procedure walkthrough covered untouched media, required unread instructions, metadata-as-content evidence, oversized edits and unknown dependencies. Required procedures load; optional integrations stay unloaded.
- No streaming, dependencies, limit increases, logo changes, live enrollment or release archive. Existing source/edit/backup bounds and strict candidate capture remain intact.
