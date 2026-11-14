# Unconditional Stop Implementation Plan

> **For agentic workers:** Use `executing-plans` for inline execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deny every edit of a finally stopped file until separate explicit user direction clears it through existing controls.

**Architecture:** Change only the shared state machine's final decision and completion handling. Preserve the adapter, earlier escalation stages, configured permissions and existing root-user message recognition.

**Tech Stack:** TypeScript, Node built-in tests, Python package checks.

**Spec:** The user's 14 November authorization: make the stop unconditional, then continue qualification; pause only for another blocker. This plan records that narrow requirement, not the deferred approval-panel design.

## Global Constraints

- No custom approval panel, automatic continuation, new parser, dependency or controller.
- Preserve prior failed trials and consumed allocations; PR #294 remains deferred, draft/BLOCKED and unmerged.
- Work only in `fix/unconditional-stop`, from `ff4d44ee65f649086f588e16afde9f8148cf7ebb`; primary stays on `main`.
- Preserve user settings, authentication, other sessions and existing fail-open limits.
- Source verification does not establish native qualification or authorize shared activation.
- Publish this separate change under the repository's existing draft-PR policy.

## Review Focus

- Repeated or mixed-file permissions must never offer approval for a stopped file.
- A completion admitted before the stop must not clear or alter stopped state.
- Children and later diagnoses cannot clear the final stop.
- Unrelated files, roots and ordinary configured approval retain existing behavior.
- Only existing explicit root-user control permits subsequent edits; casual approval does not.

## Task 1: Preserve the final stop

**Files:** Modify `runtime/adapters/opencode_workflow/gate.ts`, the adjacent completion comment in `index.ts`, `tests/opencode_workflow/gate.test.ts`, `tests/opencode_workflow/plugin.test.ts`, and `clients/opencode-escalation-gate.md`.

**Interfaces:** `Gate.editDecision(session, files)` returns denial for every held stage. `Gate.edited(session, files)` ignores stopped entries. Existing root-user control and adapter hooks remain unchanged.

- [x] Run all three existing Node suites: baseline 77/77 passed.
- [x] Replace final approval expectations; add late-completion and explicit-user continuation regressions. Assert denial, unchanged stopped status and preserved unrelated work.
- [x] Run all three Node suites. Expected: final approval and late-completion regressions fail against unchanged production code.
- [x] Change `editDecision` to deny and `edited` to skip stopped entries. Update only stale adjacent comments and the owning client guide.
- [x] Run all three Node suites and package validation. Expected: all pass; retain any existing package-size warning.
- [ ] Commit, then run `python3 -B scripts/test_sharded.py test_opencode_workflow test_features`. Expected: all selected checks pass.
- [ ] Obtain one independent behavior/security review of the whole branch, address substantive findings and publish a separate draft/BLOCKED PR.

## Qualification continuation

- [ ] Reassess the candidate's native activation path, ownership and new bounded allocation under the qualification procedure.
- [ ] Run only authorized, isolated native cases if prerequisites hold; otherwise record the exact blocker and completed independent work.

## Progress

Status: WORKING. Inline implementation; no worker currently active. Native title synchronization remains paused under the existing user direction.

Verified task checkout is clean on `fix/unconditional-stop`; primary is clean on `refs/heads/main` at the base above. Installed package and personal configuration remain unchanged. The historical plan in the other worktree is not reopened.

Test-first result: baseline 77/77 passed. With only test changes, 73/79 passed
and six expected regressions failed: four still offered approval, and two late
completion cases lost the stopped entry. Production edits followed that result.

All 79 Node cases now pass. Initial green run caught an invalid synthetic child
identifier (`ses_after_stop`); the adapter correctly ignores malformed identities.
Corrected the fixture to `ses_afterstop`, without changing production identity
validation. Package validation passed with the existing 75% export-size warning.
There is no linter, type checker, package manager or build step here. Selected
committed checks cover the sole Python wrapper and feature settings/defaults;
the full Python suite is not required for this state-machine-only change.
