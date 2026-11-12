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
- [ ] Add focused regression tests for entry-point routing, complete/selective procedure delivery and executable Git observations. Demonstrate failure against the baseline before authoring the contract.
- [ ] Amend the shared rule/procedure and reconcile every live Markdown contradiction found by targeted repository-wide search. Preserve historical evidence with supersession notes.
- [ ] Document adoption, branch designation, legacy opt-out reconciliation and unenrolled-worktree guidance explicitly.
- [ ] Regenerate instruction map; commit source before running package-dependent tests.
- [ ] Run `python3 -B scripts/validate.py`, focused instruction/onboarding/delivery tests and `git diff --check`.

### Task 2: Evaluate routing and review the exact candidate

**Files:** Same candidate; append verification evidence here and provenance in `docs/policy-sources.md`.

**Interfaces:** Fresh native workers consume the actual project entry point and selected raw scenarios, not the author's expected policy list. Reviewer reads exact committed candidate and base.

- [ ] Walk representative task-entry routes and document required/unrelated procedures.
- [ ] Run one bounded fresh-context scenario evaluation covering the Review Focus and explicit exception/dirty-state preservation. Retain actual observations; do not claim restarted-client delivery.
- [ ] Request fresh independent Fable source/semantic/security review, including the Markdown conflict audit. Fix substantiated findings within scope and rerun invalidated checks.
- [ ] Publish a draft PR, mark ready only with current evidence, and merge under existing project permission. Verify remote merge separately from local synchronization.
- [ ] Verify primary symbolic HEAD stayed `refs/heads/main`; synchronize by fast-forward only when clean/unoccupied. Run Graphify maintenance only if the accepted project choice enables it.

## Execution record

- Checkout: native OpenCode worktree `primary-checkout-stability`, branch `docs/primary-checkout-stability`, base `7356f68f0a776ab901a7c57fab7b7d9514dc8b40`.
- Primary checkout: `/Users/fcouto/dev/git/vibe-workflow`, clean `main` before worktree creation.
- Implementation: coordinator executes the tightly coupled instruction reconciliation; independent Fable review follows. User authorized proceeding after design review; no additional implementation delegation.
- Naming: `￻▰▱▱▱▱ Primary checkout stability`, readback confirmed before implementation; workflow-owned topic, no known manual pause.
- Baseline: `test_graphify_maintenance_instructions`, `test_onboarding_coverage_rule`, `test_project_review_instructions`, `test_delivery`: 58 tests passed, 106.2 seconds, isolated test homes.
- Deployment: no installed package, other repository, hook, permission or enrollment has been changed by this task.
