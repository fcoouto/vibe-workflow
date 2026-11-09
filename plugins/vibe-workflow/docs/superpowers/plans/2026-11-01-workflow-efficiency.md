# Workflow Efficiency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Native inline execution with one independent final review is recommended for this small, connected change; the user chooses the execution method after reading this plan.

**Goal:** Reduce repeated context and diagnostic output during implementation and review, while preserving current verification and recovery mechanisms.

**Architecture:** Add optional compact views to existing native delegation and external-worker status commands. Derive them from existing records, preserve detailed consumers and authoritative evidence, and route monitoring through these views with existing completion events. Reuse working cache, freshness, and failure controls rather than creating another registry.

**Tech Stack:** Existing Python 3.11+, standard library, unittest, Markdown, Codex POSIX runtime; no new dependency.

**Spec:** [Approved design and impact-scan refinements](../specs/2026-11-01-workflow-efficiency-design.md).

## Global Constraints

- Preserve the full Toolkit workflow, acceptance checks, security, ownership, permissions, and the user's model/provider/reasoning choices.
- No additional coordinator, generic tool interception layer, service, dashboard, database, model call, or continuous monitoring system is included.
- Keep skill entry points within 150 words. Define each rule once and preserve source-to-contract coverage.
- Compact progress is an observation, never current readiness, authorization, stopped-worker proof, or merge authority.
- Preserve existing detailed consumers and meanings; never rewrite a saved result to produce its compact view.
- Use existing update/recovery procedures when installing changed pinned content. Never silently repin active tasks.
- Keep package validation, synthetic checks, observed client execution, and measured savings separate.
- Preserve existing unrelated working-tree changes. Baseline includes them; commit only task-owned hunks, never whole dirty files without inspecting staged content.

## Review Focus

1. A completed report with unsettled ownership must remain visibly unsettled (Task 1).
2. More assignments than fit in the compact view must not hide additional blockers/conflicts (Task 1).
3. Missing or malformed worker results must not look healthy; viewing must preserve digest-bound bytes (Task 2).
4. An unchanged failed refresh and a restored missing executable must retain their existing retry/recovery behavior (Task 3).
5. Changed source or runtime/policy pins must still invalidate applicable evidence; a progress view must not bypass that boundary (Tasks 1 and 4).

## File Map and Scope

| Files | Responsibility |
| --- | --- |
| `runtime/delegation.py`, `scripts/workflow.py` | Native compact projection and opt-in CLI flag. No schema change or new lifecycle observation. |
| `scripts/openrouter_workers.py` | Read an existing result once and expose a compact view. Preserve runner, verifier, publisher, and persisted result. |
| `tests/test_delegation.py`, `tests/test_runtime.py`, `tests/test_openrouter_workers.py` | Projection, CLI routing, privacy, and unchanged authority checks. |
| `policies/03-agent-delegation.md`, `policies/07-context-and-handoff.md` | Canonical event/status/report retrieval and durable invocation-fix guidance. Reuse other policies without restating them. |
| `clients/delegation.md`, `skills/openrouter-workers/references/procedure.md`, `docs/policy-sources.md` | Concrete commands, selective loading, and authoring coverage. |

Existing tests in evidence, Council, observability, startup, settings, and guidance provide regression checks. Their production modules need no planned edits. No change to hook registration, enrollment schema, SQLite schema, repository merge gate, Council topology, credential storage, or external enablement is planned.

## Task 1: Compact Native Delegation Status

**Files:** Modify `runtime/delegation.py:delegation_status`, `scripts/workflow.py:control_main`, and `clients/delegation.md`; test in `tests/test_delegation.py` and `tests/test_runtime.py`.

**Interfaces:**
- Extend `delegation_status(scope, client, session_id, *, compact=False) -> dict`; existing calls retain their current shape and meaning.
- Add `_compact_delegation_status(report: dict) -> dict` in the same module. Consume the already validated detailed report, without additional native observation, evidence verification, or persisted summary.
- Add `--compact` only to `workflow.py delegation status`. No changes to `task status` or `evidence status`.

- [ ] **Write failing projection and CLI tests.** Reuse `AdmissionTests` and the existing delegation CLI fixture. Include `test_compact_status_preserves_unsettled_ownership`, `test_compact_status_discloses_omitted_assignments`, and `test_compact_status_does_not_verify_or_launch`.

  For an admitted/observed worker, assert the following against `delegation_status(..., compact=True)`:

  ```python
  self.assertTrue(summary['observation_only'])
  self.assertEqual(summary['view'], 'compact')
  self.assertGreater(summary['unsettled_count'], 0)
  self.assertEqual(summary['assignments'][0]['attempt_id'], prepared['attempt_id'])
  self.assertNotIn('claims', summary['assignments'][0])
  self.assertNotIn('binding', summary['assignments'][0])
  self.assertNotIn('task', summary)
  ```

  In separate pure-projection fixtures, use 21 valid assignment rows including an omitted uncertain row: assert `assignment_count == 21`, `len(assignments) == 20`, `details_required is True`, and counts cover all 21 rows. Also test empty/missing runtime, multiple task IDs under one session, conflicting shared resources, settled versus uncertain assignments, and an unavailable observation reference. Patch verifier/native-launch functions to fail if called; compare task rows and retained artifacts before/after. Preserve existing ledger-corruption rejection and CLI enrollment/pin checks.

- [ ] **Observe intended failure.** Run `python3 -B -m unittest tests.test_delegation tests.test_runtime`. New tests must fail because compact support is absent, not because unrelated baseline tests already fail.
- [ ] **Implement the projection and route.** Return `view`, `observation_only`, original `ok`, optional `revision`/`pins`, `assignment_count`, `unsettled_count`, `conflict_count`, distinct existing blocker messages, existing limitations, `details_required`, and at most 20 assignment summaries. Put unsettled rows first, then stable attempt-ID ordering. Each row contains `attempt_id`, owning `task_id`, `role`, original assignment `status`, existing outcome if available, and existing observation/reconciliation references. Absent facts stay null; assignment status is not native turn status. Do not inspect additional report/history bodies to populate missing facts. Set `details_required` when rows are omitted or conflict detail is necessary; document rerunning the same command without `--compact`. Keep budgets/capacity evaluation and ledger-validation boundaries unchanged internally; the projection grants no launch decision.
- [ ] **Verify and document.** Rerun the same tests; require no new failures. Show that repeated compact status neither starts task state nor changes task revision. Document that current freshness still requires ordinary observation/verification and that compact output does not promise cheaper underlying ledger validation.
- [ ] **Commit only Task 1 hunks** after reviewing the staged diff: `feat: add compact native delegation status`.

## Task 2: Compact External Worker Results

**Files:** Modify `scripts/openrouter_workers.py` and `skills/openrouter-workers/references/procedure.md`; test in `tests/test_openrouter_workers.py`.

**Interfaces:**
- Add `status(run: Path, *, compact=False) -> dict` in the existing script, used by its current `status` branch.
- Add `--compact` for the `status` action only; reject its use with `run` or `verify` before preparation or any mutation.
- Read `result.json` once. Detailed output preserves its current parsed value. Compact output is a separate dict with `view: "compact"`, `observation_only: true`, `task_id`, recorded `state`, `response_available`, sanitized `failure`, recorded `local_execution_stopped`/`native_fallback_ready` (null if absent), and `result_ref: {path, sha256}` over the bytes actually read. It contains no response text, tool events, or accounting history. A reference is a locator and integrity binding, not permission or evidence acceptance.

- [ ] **Write failing tests** in `WorkerTests`: `test_compact_status_preserves_result_bytes`, `test_compact_status_keeps_failure_and_stop_uncertainty`, `test_compact_status_rejects_malformed_result`, and `test_compact_flag_is_status_only`.

  Prepare a run and write a representative valid saved-result fixture containing private response/tool sentinels. Snapshot its bytes, task state, and directory contents, then assert:

  ```python
  self.assertEqual(self.api.status(run), result)
  compact = self.api.status(run, compact=True)
  self.assertTrue(compact['observation_only'])
  self.assertNotIn('PRIVATE_RESPONSE', json.dumps(compact))
  self.assertEqual(compact['result_ref']['sha256'], hashlib.sha256(before).hexdigest())
  self.assertEqual((run / 'result.json').read_bytes(), before)
  self.assertIsNone(compact['local_execution_stopped'])  # fixture omits this fact
  ```

  Cover prepared/running/awaiting-verification/accepted/rejected/failed/unresolved/not-launched fixtures, missing files, malformed JSON/state fields, a failed run with retained partial response, and changing saved results between two reads. Assert no subprocess, credential lookup, Habblaud import, result write, or new progress event. Assert invalid flag use causes no run-directory or state change.
- [ ] **Observe intended failure.** Run `python3 -B -m unittest tests.test_openrouter_workers`.
- [ ] **Implement `status` and the CLI flag.** Reuse `safe_failure` for controlled failure metadata. Validate the compact view's consumed field types, including real booleans; malformed/missing required identity/state fails through the existing error path. Do not infer success, current liveness, stopped descendants, or final-report completeness from a response being present. Keep detailed parsing compatibility and all execution/verification paths unchanged.
- [ ] **Verify focused compatibility.** Run `python3 -B -m unittest tests.test_openrouter_workers tests.test_council_flow tests.test_council_recovery tests.test_council_direct`. Require unchanged acceptance/digest/recovery behavior; this is synthetic regression coverage, not full Council requalification. Update the procedure to use completion events and compact status for monitoring, detailed results for verification, and `progress.jsonl` for a specific diagnostic need.
- [ ] **Commit only Task 2 hunks:** `feat: add compact external worker status`.

## Task 3: Route the Workflow Through Existing Safeguards

**Files:** Modify `policies/03-agent-delegation.md`, `policies/07-context-and-handoff.md`, and `docs/policy-sources.md`. Update only affected references in the Task 1/2 procedures. Existing guidance/settings tests provide evidence; add a focused test only if a required case is missing.

**Interfaces:** Consume the two compact status commands above and existing guidance/prerequisite/recovery APIs. Produce no new registry, schema, retry loop, or general tool interceptor.

- [ ] **Verify covered failure/freshness cases.** Run `python3 -B -m unittest tests.test_delegation.GuidanceTests tests.test_openrouter_settings tests.test_openrouter_proposals`. Check existing assertions for same-trigger failed-refresh suppression, one concurrent refresh owner, retained original timestamps, capability-change invalidation, compatible version-only reuse, and existing proposal-cache expiry/backoff. Do not replace the proposal cache's 24-hour validity with native guidance's different refresh rules.
- [ ] **Verify the missing-executable boundary without adding persistence.** Reuse the configured executable fixture, remove it, and confirm managed admission fails before spawning. Restore the fixture executable and confirm readiness is re-observed. If this specific regression is absent, add `test_missing_executable_blocks_launch_and_restoration_recovers` to `tests/test_openrouter_workers.py`, exercising its real preparation/admission boundary. A passing baseline confirms existing coverage and requires no production change. Preserve ordinary failed-test and transient-retry treatment. No other uncovered recurring failure is established by the current scan; do not invent one to justify state.
- [ ] **Edit each instruction at its owner.** Policy 03 owns event/bounded waiting, compact progress before detail, and one final report retrieval per relevant observation. Policy 07 owns storing a verified invocation repair in its responsible configuration/instruction with scope and invalidation conditions. Reference existing policies 05/09/11 for proof reuse, maintenance, and qualification instead of copying their rules. Add no universal preflight, blanket log ban, shortened required report, or weakened independent review. Map changes and proofs in `docs/policy-sources.md`; reuse existing source coverage and the approved user refinement. Any genuinely missing Toolkit requirement needs authoring research before changing its contract, never a routine-task upstream fetch.
- [ ] **Walk representative instruction paths.** For ordinary coding, reviewer handoff, stalled worker, failed refresh, changed source, and missing permission, record which contracts load and what follows. Require independent review/completion proof, targeted diagnostic expansion for a stall, dependent-only blocking, unchanged current permission checks, and no unrelated Council/setup/observability loading. Keep the walkthrough in task evidence, not a new runtime instruction file.
- [ ] **Validate and commit.** Run `python3 -B scripts/validate.py`; rerun only affected focused tests if code changed. Commit task-owned hunks as `docs: route monitoring through compact workflow status`.

## Task 4: Integration Proof and Update Handoff

**Files:** No planned production edits. Use the existing design/plan and task evidence for results; add no permanent measurement service or extra report artifact.

**Interfaces:** Completed Tasks 1–3, existing verification APIs, and the existing authorized update/recovery workflow in `clients/runtime.md` and onboarding recovery procedures.

- [ ] **Check source/pin boundaries and existing consumers.** Run `python3 -B -m unittest tests.test_evidence tests.test_state tests.test_observability tests.test_observability_caller tests.test_startup`. Confirm changed candidate/pins still reject stale readiness, ordinary task status still revalidates, observability still receives its original full schema, and historical records do not grant fresh authority. Inspect staged/unstaged changes to separate pre-existing failures. Do not rerun already-passing Task 1/2 suites absent relevant changes or failures.
- [ ] **Run final package validation.** `python3 -B scripts/validate.py` must pass. Obtain required independent diff/security-impact review over the exact final candidate and affected callers. Resolve blocking findings and rerun only affected proof. Use the user's coordinator-preserving worker selection rules; no new external model/provider is implied.
- [ ] **Compare one coding task.** In equivalent disposable baseline/candidate environments, use a small bug fix with a failing reproduction, implementation, tests, and independent review. Keep model/effort, permissions, initial inputs, and acceptance criteria equal. Count compact/detail reads, returned bytes, launches/retries, elapsed time, verified outcome, and attributable tokens only if available. Exercise an existing failure case to show preserved suppression rather than claiming new savings. No paid/external execution or live user-project installation is implied. If the host cannot support the comparison, record it as not run and report only package/synthetic proof.
- [ ] **Prepare update instructions, then stop at the applicable activation boundary.** Identify changed policy/runtime pins, installed-package destination if known, and any affected active owners/tasks. Existing authorized updates may include reconciliation; otherwise installation remains a separate requested operation. Preserve the old accepted runtime for needed recovery, settle or safely stop affected writers with existing procedures, then reconcile enrollment/task transitions before activating new pinned content. Never rewrite old successful evidence, silently migrate schema, or disable pin validation. A version-label-only update needs no new procedure; future pinned-content changes can require reconciliation again. Missing old-runtime/owner proof blocks only that activation, not delivery of the tested patch.

## Self-Review and Execution Handoff

Spec coverage: compact status and observation limits are Tasks 1–2; freshness, existing failure controls, selective loading, and handoffs are Task 3; preserved verification, comparison, and deployment are Task 4. The five Review Focus cases are assigned above. Additional failure persistence is conditional and currently unjustified, as recorded in the approved refinement; no requirement is silently replaced by a general cache.

Exact flag/function names match across tasks. Existing detailed shapes, raw Council result files, and task/evidence status semantics remain unchanged. The plan does not claim runtime speed gains solely from smaller JSON or require broad live requalification of indirectly connected features.

Recommended execution: native inline implementation, followed by required independent final review. The user may instead choose a fresh implementer/reviewer per task. Review this plan and select the method before implementation, as required by the invoked Writing Plans skill.

## Execution record — 2 November 2026

Native inline implementation selected by the user. Added the two opt-in compact views, command guidance, shared policy refinements, and focused tests. Tests live together in `tests/test_compact_status.py` to share setup and avoid extending unrelated pre-existing dirty test files. Production consumers retain detailed defaults and digest-bound result files.

The 10 focused tests pass, including populated native assignment/task/artifact nonmutation, omission disclosure, CLI routing, malformed worker state, result-byte integrity and missing-executable restoration. The independent reviewer requested populated-assignment coverage; that gap was fixed. Existing refresh/prerequisite controls were reused without a new cache. Instruction walks cover coding, independent review, stalls, stale source, failed refresh and missing permission, preserving selective loading and dependent-only blocking.

Package validation passes. A native assignment fixture returned 3,929 bytes in detailed mode and 1,132 bytes in compact mode; this is a fixture byte comparison, not measured token or live-task savings. Live baseline/candidate agent comparison and installation were not run. The package still records pending schema-2 native lifecycle qualification; no installed-client qualification is inferred here.

Two existing regression failures were independently reproduced in a reconstructed task-start baseline: `LifecycleFoundationTests.test_lifecycle_assignment_date_is_owner_assigned_once_and_replay_preserves_it` (`Source original task age/pins differ`) and `LinkedVerificationTests.test_delivery_source_change_during_registration_is_only_delivery_unknown` (`Linked delivery changed after source intent; publication remains incomplete`). These unrelated failures were retained. Test logs and task-start copies are under `/tmp/vibe-efficiency-baseline` for this local run.

Deployment handoff: shared policy/runtime content changed, so use existing authorized installation/update/recovery with accepted-pin reconciliation. Preserve the old runtime and unresolved owners as required. No installed package, enrollment, active task, credential, model setting or external route was changed by this implementation.

Final regression summary: 10 focused compact/prerequisite tests passed; native delegation/runtime checks had 217 passes and the baseline timestamp failure; the worker/Council/evidence run had 353 passes and the baseline linked-delivery failure; 62 consumer tests and 241 final state/startup/settings/cache/freshness tests passed. The remaining long linked-history retirement run was bounded rather than completed; it is outside the modified status paths. Existing ResourceWarnings about unclosed SQLite connections appeared in regression output. Independent review produced no code/security finding; its requested integration coverage was added and passes. Package validation and diff whitespace checks pass. Task-owned hunks are committed separately from earlier user edits.

## Astra takeover verification — 2 November 2026

The four tasks above remain the efficiency scope. The later 416-method historical-test inventory belongs to the separately requested whole-PR #9 review; it is not a measure of this plan's completion or token savings. That audit's expansion was stopped while checking this plan against integration commit `0b30e3f`.

Independent native Astra review found two integration regressions. The CLI's newer title synchronization wrapper could revalidate a completed task and update its title during compact status. Compact delegation status now skips that wrapper; detailed behavior remains unchanged. The native compact command instructions had also disappeared during instruction reorganization. They now live once in `clients/delegation-requests.md#status`, with the flag, row bound, complete counts, detail triggers and observation limits.

The new managed-title regression failed before the fix, then passed. It verifies unchanged task/store/title during compact status, followed by ordinary detailed revalidation of the stale completed task. Coverage also now includes an omitted unsettled assignment and every recorded external-worker state with nullable/real boolean stop facts. No new runtime cache, registry, dependency, external route or monitoring service was added.

Current focused proof: 43 compact/title tests and three source/pin freshness tests passed. The required guidance/settings/proposal run had 66 passes and two settings-fixture failures, both reproduced on untouched `0b30e3f`. Astra diagnosed existing lock files created by canonical setup and a second fixture replacing the first checkout's synthetic qualification callback. Two test-only corrections retain full filesystem nonmutation and reject copied configuration specifically; both corrected tests passed. The other 66 tests were not rerun because their code and fixtures were unchanged. These checks do not establish that the entire PR #9 suite passes.

Tasks 1–3 have implementation and focused behavioral evidence. Task 4 has package/synthetic checks, independent review, instruction walks and the existing update handoff. A current native-assignment fixture returned 4,079 detailed bytes and 1,225 compact bytes (69.97% fewer bytes). This measures one fixture's output only, not tokens, runtime speed or net workflow cost.

The managed baseline/candidate coding comparison uses the plan's explicit **not run** fallback: this host's native collaboration workers lack an established qualified observation bridge to the package's delegation records. Combining unrelated native coding runs with synthetic lifecycle receipts would not measure the managed workflow. Live effectiveness and token savings remain unmeasured; installed-client execution remains unqualified. Installation and activation were not performed. Existing accepted-pin reconciliation and recovery remain required at a later authorized activation; no active task was repinned.

## Focused publication after PR #9 closure — 2 November 2026

The user selected a fresh efficiency-only PR after #9 was closed by the repository reconciliation. The earlier execution records above describe their original candidates; they are not validation claims for this port. Current main is the implementation baseline. The port retains its existing canonical fixtures, consent version 2, worker sizing and read protections, formatter/LSP controls, observability contracts and setup qualification changes.

This slice contains only the native/worker compact views, their CLI routing, progress and durable invocation-repair guidance, source coverage, and focused tests. The worker failure projection includes only facts produced by the current executor; Council-only state and transport fields are not introduced. Existing detailed status remains the default, and compact status does not synchronize titles or revalidate completion. No old fixture, Council, storage or historical-test machinery is imported.

Verification targets the compact views, native dispatcher/title paths, guidance freshness and failed-refresh suppression, worker execution/read-only controls, current consent and source/pin invalidation. The final PR records actual outcomes. Live baseline/candidate token measurement remains unavailable for the previously documented host-qualification reason. Installation and activation remain separate authorized update/recovery operations; this publication does not repin active tasks.
