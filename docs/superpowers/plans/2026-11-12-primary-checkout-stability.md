# Primary Checkout Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adopt one consistent primary-checkout stability contract for every onboarded repository.

**Architecture:** Task Planning owns the rule; delivery owns the provider-neutral procedure. Existing task-entry, startup, onboarding/update and template paths route to those owners without activating managed controls. Historical settings stay readable, with explicit reconciliation before new-policy work.

**Tech Stack:** Markdown contracts, native Git observations, Python unittest, existing instruction reader and package validator.

**Spec:** [Approved design](../specs/2026-11-12-primary-checkout-stability-design.md).

## Global Constraints

- Every onboarded repository, existing and future; no Workstreams/runtime/GitHub prerequisite.
- No hooks, watcher, database, new dependency, permission change or automatic checkout repair.
- Preserve historical records, active-task pins, user changes and exact enrollment boundaries.
- Instructions and observations reduce mistakes and detect drift; they are not hard enforcement.
- All edits, commits and publication use this task worktree; primary checkout stays on `main`.

## Review Focus

- Review-only PR checkout must load the procedure before a switch, not merely before editing.
- A non-`main` designation comes from accepted records, not whatever HEAD currently points to.
- Linked worktrees and path aliases resolve the primary through Git metadata.
- Legacy opt-outs remain readable but cannot silently exempt new-policy work.
- An unenrolled task worktree keeps instruction discovery without inheriting managed authority.

### Task 1: Adopt and reconcile the contract

**Files:** `policies/00-index.md`, `policies/01-project-scope.md`, `policies/02-task-planning.md`, `policies/07-context-and-handoff.md`, `policies/10-project-onboarding.md`; `clients/delivery.md`, `clients/index.md`, `clients/startup.md`, `clients/graphify-maintenance.md`; `skills/onboard-my-project/references/procedure.md`; `templates/project/shared-instructions.md`, `templates/project/quality.md`, `templates/setup-record.md`; affected current/historical guidance in `README.md`, `docs/development.md`, `docs/setup.md`, `docs/delivery-validation.md`, `docs/onboard-my-project-plan.md`, `docs/policy-sources.md`, `docs/release-notes.md`.

**Interfaces:** Existing policy triggers and relative links; no schema or callable production interface changes.

- [x] Inspect ownership, callers and existing instructions; baseline focused tests (58 passed).
- [x] Add focused regression tests for complete/selective procedure delivery and executable Git observations. Demonstrate failure against the baseline before authoring the contract; entry-point routing additionally receives fresh-context evaluation in Task 2.
- [x] Amend the shared rule/procedure and reconcile every live Markdown contradiction found by targeted repository-wide search. Preserve historical evidence with supersession notes.
- [x] Document adoption, branch designation, legacy opt-out reconciliation and unenrolled-worktree guidance explicitly.
- [x] Commit source before running package-dependent tests. The map regeneration performed for #256 was superseded by #248's current-source reader; do not regenerate a stored map.
- [x] Run `python3 -B scripts/validate.py`, focused instruction/onboarding/delivery tests and `git diff --check` (135 tests passed at `0450a09`; final clarifications require the current-candidate recheck below).

### Task 2: Evaluate routing and review the exact candidate

**Files:** Same candidate; append verification evidence here and provenance in `docs/policy-sources.md`.

**Interfaces:** Fresh native workers consume the actual project entry point and selected raw scenarios, not the author's expected policy list. Reviewer reads exact committed candidate and base.

- [x] Walk representative task-entry routes and document required/unrelated procedures.
- [x] Run bounded fresh-context scenario evaluation covering the Review Focus and explicit exception/dirty-state preservation. Retain actual observations; do not claim restarted-client delivery.
- [x] Request fresh independent Fable source/semantic/security review, including the Markdown conflict audit. Fix substantiated findings within scope; run current-candidate checks before publication readiness.
- [ ] Publish a draft PR, mark ready only with current evidence, and merge under existing project permission. Verify remote merge separately from local synchronization.
- [ ] Verify primary symbolic HEAD stayed `refs/heads/main`; synchronize by fast-forward only when clean/unoccupied. Run Graphify maintenance only if the accepted project choice enables it.

## Execution record

- Checkout: native OpenCode worktree `primary-checkout-stability`, branch `docs/primary-checkout-stability`, base `7356f68f0a776ab901a7c57fab7b7d9514dc8b40`.
- Primary checkout: `/Users/fcouto/dev/git/vibe-workflow`, clean `main` before worktree creation.
- Implementation: coordinator executes the tightly coupled instruction reconciliation; independent Fable review follows. User authorized proceeding after design review; no additional implementation delegation.
- Naming: `￻▰▱▱▱▱ Primary checkout stability`, readback confirmed before implementation; workflow-owned topic, no known manual pause.
- Baseline: `test_graphify_maintenance_instructions`, `test_onboarding_coverage_rule`, `test_project_review_instructions`, `test_delivery`: 58 tests passed, 106.2 seconds, isolated test homes.
- Final source/security review: independent Fable at `e904e6a`, findings fixed in `2c3859a` and `0450a09`; no runtime/enforcement change. Fixes cover metadata-pointer fixture, local uncommitted-result retention, setup/source-write boundaries and shared sync ownership.
- Scenario evaluation: two fresh Fable contexts, 11 supplied cases each, at `e904e6a` and `0450a09`. The second reached the complete procedure for all cases and applied the corrected retention, exception, designation and onboarding decisions. Follow-ups clarified the direct non-Graphify sync pointer, non-overlapping new-task policy during deferred deployment and tracked-versus-ignored registration sequencing. The suggested completion-route ambiguity was not substantiated: policy 07 already explicitly loads the naming owner's PR gate, including no-PR scope exemptions, at completion; that evaluator had not reached/read the completion owner.
- Checks at `0450a09`: expanded 8-module run passed 135 tests in 133.2 seconds; package validation passed without warnings; whitespace passed. Final documentation-only clarifications are rechecked before readiness. No restarted-client delivery, sandbox protection or fleet rollout is claimed.
- Publication/integration outcomes and final candidate checks belong in the PR's verification record; the remaining delivery boxes are pending remote observations, not an assertion of completion.
- Deployment: no installed package, other repository, hook, permission or enrollment has been changed by this task.

## Reconcile open #248 with merged #256

The user requested integration and focused validation, not merging #248. Work in
the native `reconcile-pr248-checkout` worktree, continuing #248's original head
`0474764ab9dee4ac4218c5fd648768301693ef39` on a separate local branch. Leave the
original peer worktree untouched. Current integration base is
`796729dd3fe89cc5e8afa9cee227231f268ef84d`, which includes #256 and subsequent
changes. No installed-client or other-project update is part of this task.

- [x] Baseline #248's reader and package tests: 73 passed before integration.
- [x] Merge current main without rewriting PR history; preserve main's retired
  document deletions and newer runtime/managed-host behavior.
- [x] Keep the stored map, `build_manifest` and `--prepare` writer removed.
  Preserve #248's single trigger table while carrying #256's expanded task-entry
  and review-only triggers, checkout rule and complete delivery procedure.
- [ ] Adapt the newer template-helper consumer to the map-free reader; exercise
  current-source delivery and revision invalidation before and after the fix.
- [ ] Remove active regeneration requirements from surviving newer guidance;
  keep historical evidence explicitly historical.
- [ ] Commit and run focused reader, template, startup, checkout, package and
  managed-host checks plus package validation and whitespace checks.
- [ ] Obtain independent review of conflict resolutions; fast-forward the
  existing PR branch only if its observed head still matches the starting head.
  Record outcomes in #248 and retain its draft/blocked status and no-merge scope.

Rollback before publishing uses the existing PR head; after publishing, recovery
uses explicit follow-up commits. Do not force-push, reset a peer branch, restore
the stored map or switch the primary checkout. No adherence experiment is rerun.
