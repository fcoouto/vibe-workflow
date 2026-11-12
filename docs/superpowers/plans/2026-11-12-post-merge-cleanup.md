# Opt-in Post-merge Cleanup Implementation Plan

> **For agentic workers:** Use `executing-plans` to implement this approved plan inline. Track steps here.

**Goal:** Add conservative, opt-in cleanup after confirmed task PR merges.

**Architecture:** One canonical procedure coordinates existing native tools. A read-only helper checks local Git facts and the supported remote configuration; no mutation executor, watcher, journal or automatic recovery.

**Tech Stack:** Python 3.11+ standard library, Git, native client tools.

**Spec:** `docs/superpowers/specs/2026-11-12-post-merge-cleanup-design.md`.

## Global constraints

- `cleanup.enabled` defaults to `false`; `delete_remote_branch` defaults to `true`.
- Preserve scoped permission, explicit refusals, session revocation and active policy pins.
- Native tools own removal; no force, reset, stash, or blanket recursive deletion.
- Stop partial cleanup. A later explicit request is required; no automatic recovery.
- Existing project settings are not enabled by implementing this feature.
- Publish, independently review and merge under the existing repository rules.

## Review focus

- Wrong destination with the same branch/SHA must not become eligible remote cleanup.
- Ignored or hidden index changes must not pass as a clean removable worktree.
- A linked checkout must never be accepted as the designated primary.
- A stale ref or another worktree attachment must retain the local branch.
- Unknown native effects and lost acknowledgments must stop remaining actions.

## Task 1: Inspect and document native removal

- [x] Create a disposable native worktree; test tracked and untracked refusal.
- [x] Test ignored content and observe branch side effects.
- [x] Record behavior and limits in the canonical delivery procedure.

Observed on OpenCode v2.2.6/macOS: native `worktree_remove` without force refused
modified tracked and untracked files. It removed an ignored synthetic file and
the worktree, leaving `test/cleanup-removal-probe` at its original commit.
No application/user data was involved. Codex and Claude removal are unobserved.

## Task 2: Feature and read-only checks

Files: new `features/cleanup.json`, `runtime/cleanup.py`, `scripts/cleanup.py`,
`tests/test_cleanup.py`; extend `tests/test_features.py`.

Interface: `inspect(primary, primary_branch, worktree, branch, head, merge,
removed=False) -> dict`. Returns `status`, `reason`, and verified facts on success;
`eligible` means local checks passed, never permission or provider verification.
`remote_destination(primary, remote, repository) -> str` accepts only ordinary
same-repository GitHub HTTPS configuration; unsupported paths raise `ValueError`.
The CLI exposes those read-only checks and no deletion command.

- [x] Add failing tests and commit before the repository's test runner.
- [x] Run `python3 -B scripts/test_sharded.py test_cleanup test_features`; confirm missing feature/helper failures.
- [x] Implement the smallest checks using `runtime.features` and `runtime.startup.read_git`.
- [ ] Commit and run the same tests; require all pass.

## Task 3: Procedure and integration

Files: `clients/delivery.md`, `policies/08-merge-readiness.md`,
`skills/onboard-my-project/references/procedure.md`,
`assets/harness-evaluation/cases.md`.

- [x] Add opt-in question after merge permission; preserve Graphify/memory default rules.
- [x] Specify merge evidence, synchronization, ownership, helper use, native removal,
  conditional deletion, and stop-on-partial outcomes in one procedure.
- [x] Add the policy trigger and representative onboarding/cleanup cases.
- [ ] Validate, commit, then run affected tests: `test_cleanup test_features
  test_checkout_stability_instructions test_graphify_maintenance_instructions
  test_prepared_instructions`.
- [ ] Independent whole-change review; resolve findings, publish `[BLOCKED]` draft,
  promote only with current evidence, merge and synchronize the primary.

## Execution record

Base: `6840d5333043f73b30bd52cdd9d776194f754b9b`; branch `feat/opt-in-cleanup`.
Native task worktree created by OpenCode. Primary observed clean on `main`.
Title baseline: `￻▰▱▱▱▱ Opt-in post-merge cleanup`.
Ruling: keep deterministic checks read-only and native effects in the procedure.
This avoids a second executor while preserving native authorization boundaries;
the cost is instruction-following rather than mechanical action enforcement.
The user's implementation request authorizes proceeding without another plan approval.

Red check on `439dd3f`: 47 tests, expected missing cleanup module/manifest and
changed feature-summary failures; other existing feature tests passed.
Ruling: retain the local ref until remote cleanup settles or is explicitly skipped,
then delete it conditionally. This lets the same helper recheck after worktree
removal without a recovery record or a second remote-only command. A failed push
leaves the local branch for a later explicit attempt.

First combined run on `975d840`: cleanup 19/19, checkout 4/4, Graphify 6/6,
instruction loader 41/41 passed. Feature tests 45/46: the revised summary test
incorrectly expected all features from `status workstreams --summary`, which is
intentionally filtered. Restore that expectation and test the unfiltered command
separately; no production resolver change is needed.
