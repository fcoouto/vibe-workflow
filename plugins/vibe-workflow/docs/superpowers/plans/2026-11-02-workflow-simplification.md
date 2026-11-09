# Workflow Simplification Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement the accepted changes in this checkout. Preserve native coordinator settings and obtain one independent final review.

**Goal:** Implement the ten approved simplifications while preserving actual authority, data, ownership and feature requirements.

**Architecture:** Reuse current transaction, state and native boundaries. Remove controls from unrelated operations; keep original historical records and supported readers.

**Tech Stack:** Python standard library, SQLite, POSIX files, Markdown instructions.

**Spec:** `docs/superpowers/specs/2026-11-02-workflow-simplification-design.md`.

## Global constraints

- No live activation, runtime-store edits, plugin-cache refresh, merge or release archive.
- Keep user permissions, provider/model choices, customizations and existing dirty checkouts intact.
- Preserve the seven approved recovery protections and exact retention requirements.
- Entry points remain within 150 words; update source coverage and representative instruction paths.
- Package validation and native execution are separate claims.

## Review focus

- Configured versus suspended active authority must not share a blanket bypass.
- Compatible execution must preserve original pins rather than silently repin records.
- Completed checks and heavy phases must not lose uncertain descendant ownership.
- Narrow ledger queries must retain exact replay, dependency and conflict coverage.
- Reduced instruction loading must retain required review, security and merge procedures.

## Tasks

1. **Scope and presentation (audit 1, 10):** modify `runtime/startup.py`, `scripts/workflow.py`, `runtime/title_sync.py` and their contracts. Add regressions to `tests/test_unenrolled_checkout.py` and title integration tests for configured editing, protected active errors, single acquisition, unchanged-event transport avoidance and pending/manual-edit repair. Run focused suites after observing the new failures.
2. **Recovery (audit 2):** retain PR30 and inspect `_file_configuration`/`_scoped_writers` in `runtime/changes.py`; test unrelated active membership without permitting affected active/shared changes. Run file configuration, existing-store admission and rollback suites.
3. **Compatibility (audit 3):** preserve historical fingerprints while decoupling supported current readers from unrelated executing bytes in startup/state and their evidence consumers. Test compatible source changes, unsupported formats, accepted-scope drift and historical identity preservation. Update currentness tests and contracts together.
4. **Checks and evidence (audit 4, 5):** trace execution/publication/closure consumers before reducing proof and duplicate work. Keep uncertain writable commands protected; test producer receipt integrity, interrupted execution and historical retention. Avoid overlapping Genesis's unmerged provenance format; reuse only integrated formats unless the full change is independently verified here.
5. **Current ownership and capacity (audit 6, 7):** narrow admission reads in `runtime/delegation.py` and consumers, retaining bounded historical inspection where requested. Reuse sequential resource occupancy in `runtime/resource_execution.py`/`runtime/resource_admission.py`; test replay, changed demand, simultaneous phases and uncertainty.
6. **Onboarding and loading (audit 8, 9):** derive deterministic proposal fields using existing onboarding observations; keep semantic decisions explicit. Complete public request references, shorten unconditional routing and update policy source coverage. Validate small direct work, dispatch, check, recovery and merge instruction paths.
7. **Integration:** run `python3 -B scripts/validate.py`, the complete unittest suite and diff checks; independently review behavior, safety and all ten acceptance outcomes. Fix evidenced regressions and record remaining native qualification limits without claiming operational readiness.

## Progress

- [x] Isolated checkout from merged recovery baseline; primary checkout left untouched.
- [x] Baseline characterized: initial full baseline interrupted; focused failing regressions established before changes.
- [x] Task 1: scope and presentation.
- [x] Task 2: recovery.
- [x] Task 3: retain strict matching; automatic upgrade compatibility deferred by the user's pre-merge decision.
- [x] Task 4: checks and evidence.
- [x] Task 5: current ownership and capacity.
- [x] Task 6: onboarding and loading.
- [x] Task 7: isolated package integration and independent review; Genesis composition remains separate.

## Decisions and evidence

The user approved implementation directly from the audit; no repeated design or execution-method approval is required. Native implementation remains in this chat. This plan is the single progress record; redundant per-task tracking files are unnecessary for this coupled deletion work.


### First implementation and review

- Scope tests passed (6); startup/title suites passed (99); reviewed package compatibility and historical readers passed (6); resource suites passed (45). These are intermediate results, superseded by final integration below.
- Initial complete-suite run passed 11 modules before being deliberately stopped for review fixes. It is not a full-suite pass.
- Independent Astra/high review found three actionable defects: previously qualified configured records could bypass hooks; phase release could race a pending launch; native lookup/task startup still scanned unrelated settled history. Each was reproduced before its fix. Nine scope/phase regressions and two lifecycle/ledger regressions then passed.
- Ruling: arbitrary subprocesses still require independent descendant reconciliation. Only executor-confirmed nonlaunch settles automatically; a declaration of read-only intent is not containment.
- Ruling: phase release now needs a durable completed execution receipt plus independent descendant proof. Old reservations without that receipt retain whole-owner reconciliation. Pending launch cannot supply a completed receipt.
- Initial compatibility ruling was superseded before merge: the accepted-only PR30 exception did not bind the installed target. The user chose strict matching, so the exception was removed without repinning records or introducing another approval mechanism.
- Ruling: retain historical closure genealogy and protected-store discovery. Remove retention scans from each publication and share one validated ownership observation across completion checks in the same database transaction. A focused regression reduced repeated ledger reads from three to one. Broader provenance-graph redesign remains a future round; no historical fact is rewritten.
- Ruling: schema-2 onboarding derives repeated identities, file hashes and evidence digests. Semantic scope, complete catalog coverage and exact D4 authorization remain explicit.
- Package validation passed; installed/native execution remains untested. The complete suite is rerun after review fixes before final delivery.

The follow-up integration run was also stopped before source changes to address the review's repeated-observation gap. Neither interrupted run is counted as a full-suite pass. Final source is frozen for the complete integration run below.

### Complete suite and corrective verification

The uninterrupted run completed all 41 test modules, covering 874 test executions
in 1,433.6 seconds. It exposed 14 failures in four modules: the narrowed
file-configuration predicate bypassed existing-store admission for runtime-bearing
membership changes; recording phase completion erased an admission warning; and
an efficiency assertion still expected no completion-receipt write.

The correction retains existing-store admission when runtime-bearing membership
changes coexist with active membership. All existing rejection tests remain
unchanged. Phase completion preserves the admission warning. The efficiency test
accepts the required receipt while continuing to reject an unused status read.
The affected evidence, admission, file-configuration, resource-execution,
resource-admission and resource-efficiency modules are rerun after these fixes.
A final focused regression also covers cancellation after phase completion: the
receipt must survive, and stopped proof remains mandatory.

All six affected modules passed, covering 140 test executions including the new
cancellation regression. The resource-admission module was rerun after its final
one-line cancellation compatibility correction (24 tests passed). The remaining
35 modules retain their passing results from the complete run. No second full
suite run is claimed after these narrow corrections.

Final package validation and diff checks passed. Entry points contain 148 words
for onboarding, 143 for environment preparation and 143 for workstreams. The
direct-task static reading route is 2,654 words versus 3,924 before this round;
later applicable test, security and review procedures are excluded from that
count. No token, latency or native-execution improvement is claimed from it.

Native client execution, live installation, combined Genesis behavior and
production repository gate enforcement were not exercised. The branch and
worktree are retained for the agreed integration decision.

### Genesis handoff

The user requested direct alignment with the Genesis Impl chat. Genesis owns the
workflow backbone, execution provenance and retention redesign. Both candidates
remain isolated; neither chat edits the other's checkout. Genesis's bounded
retention failure remains separate and is not resolved by our sweep scheduling or
ledger reuse. No Genesis test allowance is spent by this chat.

Composition must retain Genesis's execution publication frame through result
attachment, authenticated execution readers, private provenance and retained
source rules. Started or uncertain subprocesses retain independent stopped-child
reconciliation. Package compatibility remains strict: no PR30, Genesis or composed
source generation receives an automatic cross-version exception.

The full source candidate remains the tested unit. Proposed logical slices are
not yet independently verified commits. Integration order remains a shared
decision after qualification; no merge, installation or live activation occurs
in this implementation round.

### Pre-merge corrective review

After the user authorized merging, Genesis supplied an independent final review
of immutable `b3d83b6`. It found two blockers: the PR30 exception accepted unknown
installed targets, and resource-queue eligibility omitted settled prerequisite
assignments. Both were reproduced by focused failing tests.

The user explicitly chose strict version matching. Automatic cross-version
compatibility is deferred. Queue eligibility now uses the existing task-scoped
ledger: current owners plus that task's settled history. A genuine three-job chain
checks that completed transitive prerequisites preserve FIFO and that an active
dependent still blocks prerequisite reopening. No Genesis source, fingerprint
approval or execution allowance is changed by these corrections.

Focused corrective verification passed six tests: strict package equality and
unchanged persisted provenance, real transitive FIFO/reopening protection,
current-plus-own-history selection, and direct/transitive stale prerequisite
checks. Package validation and diff checks also passed. Earlier unaffected
evidence remains reusable because these corrections only remove the upgrade
exception and restore the existing task-scoped selection at queue eligibility;
they do not alter evidence publication, file recovery, onboarding or title behavior.
