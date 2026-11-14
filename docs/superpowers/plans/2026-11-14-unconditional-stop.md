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
- [x] Commit, then run `python3 -B scripts/test_sharded.py test_opencode_workflow test_features`. Expected: all selected checks pass.
- [x] Obtain one independent behavior/security review of the whole branch and address substantive findings.
- [x] Publish a separate draft/BLOCKED PR (#302).

## Qualification continuation

- [x] Reassess the candidate's native activation path, ownership and new bounded allocation under the qualification procedure.
- [x] Record the original shared-activation blocker and completed independent work; no native behavior case ran in that preflight.
- [x] Verify a private account-free native read/edit path, independently reviewed by Opus.
- [x] Obtain Astra's full-drill design assessment and execute one bounded campaign.
- [ ] Complete final-stop, repeated-denial, unrelated-work and explicit-continuation native cases.
- [ ] Resolve the failed campaign's shared-settings preservation uncertainty before accepting aggregate qualification.

## Progress

Status: source verified and published as draft/BLOCKED PR #302. Native qualification
is partial after a provider transport failure; see the [current report](../../qualification/2026-11-14-unconditional-stop.md).
Native title synchronization remains paused under the existing user direction.

At the source-implementation checkpoint, the task checkout was clean on
`fix/unconditional-stop`; primary was clean on `refs/heads/main` at the base above.
Installed package and personal configuration matched their original observations.
Later native observations and the preservation discrepancy are recorded below.
The historical plan in the other worktree is not reopened.

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

Committed candidate `67d790c25d5fb29c09e25da217da9b3caecb9959` passed
`python3 -B scripts/test_sharded.py test_opencode_workflow test_features`:
49 Python checks, zero skipped, including 79 nested Node cases. One independent
native child reviewed behavior, regressions and relevant permission/state security;
it independently reran all 79 Node cases and substantiated no findings.
The reviewer remained read-only. No scanner or native behavior campaign ran.

The review's exclusions remain deliberate: unchanged conversational parsing and
documented fail-open/reload/memory limits are not hardened by this narrow fix.
Already admitted writes may still finish physically; completion no longer clears
the final stop. Native event ordering and shared activation need actual proof.

## Original native qualification blocker (historical)

A fresh zero-model preflight observed the existing OpenCode 2.3.0 service still
loading the original installed adapter, not this candidate. Four bounded native
operations completed in 34 ms: no-start discovery, server identity, active-session
identifiers and plugin metadata. Another session was active outside this task's
ownership. No service-wide held-file or repair-history inventory is established.

The existing supported account route would replace the shared plugin registration,
which can discard in-memory state across affected locations. A project-local
same-ID append/removal is not a proven replacement; a private authenticated
environment has not been established. Do not infer safe activation from idle
sessions, copy credentials, alter the installed export directly, or repeat a closed
allocation. Personal configuration still matched its pre-task digest.

Native qualification is **not passed**. No model campaign, config write, reload,
service restart or deployment occurred. Source checks and review are complete
independently. Next owner: the user and affected session owners establish a quiet
change window with existing work/history safely reconciled, or authorize a separate
supported authenticated test environment. Then allocate a new bounded native
attempt for this committed candidate; A1/B1/B2 remain closed and consumed.

Remote `main` advanced to `08a42604638e56e7f33c9956f0b5e632e7713401` during work.
Its changes affect only the Codex adapter and two native-record test modules,
disjoint from this change. The reviewed TypeScript source and checks are unchanged;
no rebase, primary mutation or merge was performed.

## Private native continuation — 14 November

The isolated account-free route supersedes the original shared-activation blocker
for provider-independent testing. It does not authorize changing shared settings,
copying credentials, reloading other sessions or activating the installed package.
Opus reviewed the isolation design; a corrected separately authorized four-request
smoke proved original native read/edit/status execution. The initial failed smoke
and all closed A1/B1/B2 allowances remain preserved.

Astra accepted the genuine maximum-effort shortcut: three edit/check rounds,
one fresh read-only child, then two further rounds to the final stop. The full
drill used a new 120-request, 200-operation, 45-minute allocation with no retries
or implicit renewal. Actual OpenCode 2.2.6 requests used public zero-cost
`opencode/deepseek-v4.1-flash-free#max`, with private HOME/XDG/database and an
owned sandboxed `serve --stdio` process.

Observed: both canaries reached re-evaluation after three failing immutable
checks; the real foreground native child completed read-only and released both
once; a fourth edit/check round completed. Request 26 failed before fifth-round
tools with `provider.transport`: `EPROTO: The TLS handshake failed. Does the server speak TLS on this port?`
The observer vetoed the proposed retry. No retry or replacement campaign ran.

Source and protected fixture hashes matched. The private process exited zero,
was reaped, and its process group was absent. Shared service identity and selected
Claude directory metadata matched, but the aggregate shared-settings fingerprint
did not. Per-path before hashes were not retained, so attribution is unavailable;
do not claim full preservation or restore another owner's files.

The final stop, repeated root/child refusals, unrelated work, named continuation
and separate passing repair remain unobserved natively. Existing 79-case source
proof remains applicable because runtime/tests are byte-identical. No deployment,
merge or release occurred. A separately authorized fresh attempt is needed:
the failed campaign is closed, and its in-memory state ended with its process.
Keep PR #302 draft/BLOCKED and PR #294 deferred, draft/BLOCKED and unmerged.
