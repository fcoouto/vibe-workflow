# Graphify Post-merge Maintenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh local code maps during post-merge maintenance only for Graphify-enabled projects.

**Architecture:** Extend the existing instruction-driven merge owner, not a new executor. Route synchronization and onboarding to that owner, retain project choices, and verify delivery plus representative decision paths.

**Tech Stack:** Markdown workflow contracts, existing Graphify CLI, Python unittest and prepared instruction loading.

**Spec:** `docs/superpowers/specs/2026-11-10-graphify-post-merge-design.md` (user-approved direction; execution explicitly requested).

## Global Constraints

- Only for Graphify-enabled projects; installed tools alone are not enablement.
- Primary checkout only; preserve dirty files and other writers; fast-forward only.
- No hooks, watchers, model calls, new settings, runtime activation or database.
- A failed refresh leaves maintenance incomplete without undoing the merge.
- Keep initial setup and post-merge commands code-only and without clustering.

## Review Focus

- Disabled, deferred and unknown selection must not silently become enabled.
- Missing generated output must not suppress refresh for an enabled project.
- Remote merge success is not proof that the local primary checkout synchronized.
- Dirty/diverged primary checkout and concurrent changes must preserve user work.
- Failed extraction/query must not turn old output into successful maintenance.
- An inherited output override or symlink must not write another worktree's graph.

### Task 1: Complete the shared maintenance procedure and its loading paths

**Files:** Modify `policies/08-merge-readiness.md`, `policies/00-index.md`,
`docs/development.md`, `skills/onboard-my-project/references/procedure.md`.
Create `clients/graphify-maintenance.md` and `tests/test_graphify_maintenance_instructions.py`.

**Interfaces:** Existing setup decision and merge/synchronization observations in;
agent-executed Graphify maintenance and snapshot-bound result in the existing task report out.

- [ ] Add focused instruction-delivery/routing tests; observe the intended baseline failure.
- [ ] Link the existing merge owner to one selectively loaded maintenance procedure. Route index,
  local development and onboarding to it; retain source coverage and existing requirements.
- [ ] Rerun focused tests plus package validation.
- [ ] Commit before broader checks. Run affected instruction, packaging and startup tests under the current development guide's proportionate-check rules.
- [ ] Have one independent native reviewer assess the diff and walk through enabled,
  disabled, missing-graph, external-merge, unsafe-checkout and refresh-failure cases.
- [ ] Publish a draft PR; promote/merge only with current-base checks and independent review.

## Execution and delivery

Coordinator implements this bounded instruction-only slice directly; independent
review checks the resulting procedure. The user explicitly requested proceeding,
so no additional design/method approval is required. Existing publication and
merge policy applies. Ignore-file PR #203 remains separate and untouched.
No claim of client installation, unattended triggering or cross-worktree freshness.
