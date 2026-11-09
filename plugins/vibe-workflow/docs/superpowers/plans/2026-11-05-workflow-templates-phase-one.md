# Workflow Templates Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Clarify ordinary versus managed instruction loading, prove two useful example patterns, and simplify delegation code only where the audit establishes a safe improvement.

**Architecture:** Keep policy decisions with their current owners and request validation in existing Python operations. Add examples beside their consumers and test them through existing disposable fixtures. Treat code cleanup as a bounded, evidence-based outcome; no new template engine, schema authority or execution gate.

**Tech Stack:** Python standard library, `unittest`, existing Markdown/JSON instruction reader and native Codex tooling. No new dependencies.

**Spec:** [Workflow templates and behavioral verification](../specs/2026-11-05-workflow-template-design.md).

**Status:** Completed and merged into `main` through [PR #103](https://github.com/fcoouto/vibe-coding-workflow/pull/103), commit `7dd2bdb`. Steps and intermediate local-only statements below are historical execution records, not work to repeat. Follow-on execution: [Phase 2 implementation record](2026-11-05-workflow-templates-phase-two.md).

**Scope:** Closed record for design Phase 1 only. The linked Phase 2 plan incorporates the approved lifecycle/reporting direction; reusable evaluation and further expansion remain conditional Phases 3–4. This record grants no new implementation, publication or rollout authority.

## Global Constraints

- Use the exact existing worktree: `/Users/fcouto/.codex/worktrees/usage-routing-delegation-registration/vibe-coding-workflow`.
- Preserve local commit `860ac89` and newer changes on `main`; do not recreate the earlier role-instruction cleanup.
- Keep ordinary work useful without a runtime database or repository enforcement.
- Retain one role declaration per subagent assignment, reuse it on follow-up, and keep display failures nonblocking. Native thread owners need no role badge.
- Preserve user permissions, chosen model/reasoning/authentication, existing project documents and selected protections.
- Keep the unresolved OpenAI API run and its reservation on hold; do not retry, release, relabel or reconcile it during this work.
- No external provider calls, global hooks/cache changes, client restarts, active-chat interruption or release archives.
- Each wrapper rule has one owner; skill entry points remain within 150 words. Routine work must not fetch Toolkit.
- Package tests, synthetic observations and actual client execution are separate evidence.
- Reuse existing validators and fixtures. A template does not authorize input or replace checks at real trust/state boundaries.

## Review Focus

1. Ordinary tasks entering through README or a review request must avoid managed-only procedures while retaining ordinary review requirements — Task 1 and Task 4 cases R1/R2.
2. A syntactically accepted plan with unresolved security or missing behavior proof must not be presented as completion — Task 2 example assertions and Task 4 case R3.
3. Invalid role metadata must preserve original spawn inputs and must not create new assignment directories — Task 3 characterization if a refactor is proposed.
4. Direct legacy callers and later-read contexts must still validate their own input; a reused child must keep its frozen declaration — Task 3 direct-entry/reuse checks.
5. Basic setup must preserve user edits and selected protections without acquiring task-runtime authority — Task 1 existing regressions and Task 4 case R2.

## Complexity acceptance

The expected result is less work for routine agents, with a small, justified increase in authoring tests. This section is an acceptance condition for this change, not another routine workflow step or approval gate.

| Area | Expected effect | Acceptable added cost |
|---|---|---|
| Ordinary fix, review and delegation | No new required step, form, tool call or approval; fewer irrelevant procedures loaded | None from this template pilot. Preserve existing applicable requirements. |
| Selected managed requests | Less reconstruction from dense prose; one authoritative validator | A relevant example may add guide length if it improves correct request construction; replace overlapping explanation instead of maintaining both. |
| Runtime code | Same or fewer branches and validation paths where safe | An abstraction must remove actual duplication or prevent a demonstrated defect. Do not add helper layers just to avoid one cheap repeated check. |
| State and infrastructure | No new persistent record, service, dependency, hook or schema authority | None in Phase 1. |
| Maintenance | A few focused example/regression tests using existing fixtures | Accept when they catch example drift or a concrete failure; avoid tests that restate prose or implementation. |

In the existing completion note, compare only the affected before/after paths: required actions, loaded instruction scope/size, code branches/helpers and maintained artifacts/tests. Include observed omissions or request failures where available; time/tokens only when exposed. No new score, registry or reporting tool. Reject additions that merely move or duplicate complexity. If a larger example has no demonstrated utility, shorten or remove it. Do not trade away required protection to hit a line-count target.

## Execution preparation and ownership

Observed planning basis: branch `codex/consolidate-role-instructions`, HEAD `860ac89`. Local `main` was `1e1f0d0`, four commits ahead and one behind this branch. Newer work affects naming and several shared instruction files. These observations are not permanent assumptions.

- [x] Recheck branch, HEAD, worktree changes and applicable instructions. Read both design and plan. Preserve the two planning documents and any later user edits.
- [x] Inspect current integration-branch differences in affected files before implementation. Reconcile against current `main` through the existing authorized Git process before merging; do not switch this worktree or rewrite commits merely to begin a task. Any changed interface invalidates the affected plan detail until inspected.
- [x] Reuse current relevant check evidence where unaffected. Run the owning task's baseline when needed for reproduction or a behavior-preserving refactor; do not run a duplicate blanket suite before the task-level checks below. Record existing failures and continue independent work.
- [x] Retain a private baseline copy of the relevant instructions for the bounded native comparison in Task 4. It is an evaluation input, not another installed package or a new worktree.
- [x] For the onboarding case, reuse verified complete catalog coverage and the required guides from one authoring revision. The existing template research alone is not the complete Toolkit catalog. Obtain any missing source material once during evaluation authoring; routine fix/review cases must not fetch it.

Use useful native child ownership under the saved Workstreams preference, preserving Astra/ultra. Task boundaries are reviewable outcomes, not a requirement to spawn one agent per task. Tasks 1–3 have disjoint primary files and may run in one independent wave after preflight; combine small related work when coordination costs more than it saves. Keep each outcome with its child through fixes. Coordinator owns Git, `clients/instruction-map.json`, `docs/policy-sources.md`, acceptance records and final integration. Workers do not regenerate shared maps or publish independently. Prepare each assignment through the existing role owner; do not repeat role rules in this plan.

Self-review each outcome, then use one bounded independent review covering the complete ready wave, shared integration changes and applicable security impact. Review every changed outcome within that scope before dependent work; do not add a separate reviewer per task and then repeat the same whole-diff review. Reuse accepted review/check evidence for unchanged inputs. Review later substantive deltas when necessary; additional rounds need a concrete unresolved concern. Commits, if already authorized by the effective delivery policy, occur only after the outcome passes its checks and review; otherwise retain reviewable local diffs.

## Task 1: Correct loading boundaries and show basic onboarding

**Files:** Modify `README.md`, `clients/evidence.md`, `policies/00-index.md`, and `skills/onboard-my-project/references/reviewed-configuration.md`. Inspect `clients/runtime.md`, `clients/delegation.md`, `skills/onboard-my-project/references/procedure.md` and evidence-guide backlinks; change a caller only if it has the same broad routing defect. Do not rewrite managed operation procedures.

**Interfaces:** Consumes the existing ordinary/managed boundary in `clients/codex.md` and maintenance trigger in `policies/09-workflow-maintenance.md`. Produces precise loading triggers and a basic-setup example; no runtime API changes. Task 2 relies on the corrected common evidence scope. Task 4 observes actual routing.

- [x] **Audit entry paths.** Run `rg -n 'evidence\.md|09-workflow-maintenance\.md' README.md clients policies skills -g '*.md'`. Classify each match in the task record as ordinary entry, selected-managed dependency or reference. Current defects include README's broad evidence entry and the opening of `clients/evidence.md`. `clients/runtime.md` is already an explicitly selected runtime contract; retain its valid managed dependency.
- [x] **Edit the evidence trigger and README pointer.** Say that the evidence client procedure applies to explicitly selected managed check/review/completion operations. Point ordinary work to its current native/shared policies. Preserve required general/security review, accepted checks, recovery, retention and all managed input limits. Do not copy ordinary policy procedures into the client guide.
- [x] **Align the maintenance index row.** Include initial setup and material instruction/memory changes in the index's routing summary, preserving the owner's other triggers. Keep the full procedure solely in `policies/09-workflow-maintenance.md`.
- [x] **Replace the misleading generic onboarding example with two clearly scoped examples.** The basic example adds useful project guidance, runs native project checks and retains private recovery material; it neither creates a runtime DB nor requires task activation. Describe startup integration and missing proof truthfully under the existing onboarding owner. Keep the current registration/activation example as a selected-managed example where relevant. Reuse the existing scope/changes/next/decisions/status/recovery/records layout; add no form or state store.
- [x] **Verify existing behavior and source coverage.** Run `python3 -B -m unittest tests.test_relevant_onboarding tests.test_unenrolled_checkout`. Existing cases already cover file-only creation/undo, later edits, selected inputs and denial of managed execution from relevant inspection. Do not add tests that merely match new prose. Record the changed triggers for Task 4 and hand the exact edited paths to the coordinator for map regeneration.
- [x] **Self-review and hand off the outcome.** Check for lost requirements, duplicate instructions, misleading activation language and out-of-scope caller edits. Supply the diff and actual command outcome for the shared independent review. Suggested commit subject, only if authorized after review: `docs: clarify managed evidence and basic onboarding`.

## Task 2: Make evidence-plan examples executable

**Files:** Modify `clients/evidence-plan.md` and `tests/test_evidence.py`. Reuse `tests/test_workstreams_efficiency.py::DocumentedRequestTests` as the extraction pattern; do not refactor it or change `runtime/evidence.py` just to accept an example.

**Interfaces:** `EvidenceTests('runTest')`, `setUp()`, `doCleanups()`, `request(**fields)`, and `git(*args)` provide an isolated accepted repository and current task identity. Invoke `runtime.evidence.plan_evidence(scope, request)`; assert its `ok`, `task.revision` and stored plan via the existing `_load_plan(scope, identity)`. The public request remains the common identity/revision envelope plus `plan`. No new schema or runtime field is introduced.

- [x] **Add `DocumentedEvidencePlanTests(unittest.TestCase)` with a private example reader.** Read only named JSON fences in `clients/evidence-plan.md`, following the current regex/`json.loads` pattern. Resolve whole-value placeholders recursively from fixture values. Register fixture cleanup and use a fresh fixture for each accepted example. Avoid a generalized template parser or dependency.
- [x] **Write and run the focused example tests before adding the fences.** Their first failure should be the absent example marker. Planned markers are `<!-- example:evidence-plan-minimal -->` and `<!-- example:evidence-plan-read-only-check -->`. Run `python3 -B -m unittest tests.test_evidence.DocumentedEvidencePlanTests`.

Required tests and assertions:

```python
# test_minimal_plan_example_executes_against_runtime_validator
before = request['expected_revision']
result = api.plan_evidence(fixture.scope, request)
self.assertTrue(result['ok'])
self.assertEqual(result['task']['revision'], before + 1)
self.assertEqual(api._load_plan(fixture.scope, fixture.identity), request['plan'])
self.assertEqual(request['plan']['security_impact']['status'], 'unresolved')
self.assertNotEqual(result['task']['state'], 'DONE')

# test_read_only_check_example_preserves_thresholds
self.assertTrue(result['ok'])
self.assertEqual(stored['checks'][0]['thresholds'], {'exit_codes': [0]})
self.assertEqual(stored['checks'][0]['baseline']['status'], 'not-run')
```

Also add `test_plan_examples_reject_missing_and_unknown_fields`: remove envelope `expected_revision`, remove plan `criteria`, and add unknown envelope, plan and check fields. Each invalid request must raise `state.StateError` and leave task revision unchanged. Add `test_plan_example_rejects_stale_revision`: accept a valid saved request once, replay that request, assert `StateError('Task revision conflict')`, and confirm no second revision change. Resolve all fixtures and examples before applying each invalid mutation.

- [x] **Write the minimal request example.** Its outer object contains `plan` only, with an explicit instruction to combine it with the already documented current identity/revision fields. Use version `1`, `decision_ref: '<ACCEPTED_DECISION_REF>'`, one criterion `{id:'acceptance', checks:[], behavior:true}`, and `checks:[]`. Both review scopes use `applicability:'required'`, `independent:true` and scoped reason placeholders. `security_impact` uses `status:'unresolved'` with reason/reference placeholders. Include `base:'<BASE_COMMIT>'`, `comparison:'endpoint'`, `nontrivial:true`, `instructions:[]`, `exclusions:[]`, and `review_limit:1`.
- [x] **Explain the minimal example's limits.** Behavior-only acceptance still needs later observed proof. Unresolved security prevents completion. Empty instruction/exclusion arrays are illustrative and valid only for their accepted scope. A plan accepted by the API is neither evidence of passed checks nor execution authorization. Keep all current field bounds, conditional resources, baseline signatures and rule-change requirements in the same owner guide; improve layout without dropping them.
- [x] **Add one executable check-component variant instead of repeating the full plan.** The named second fence contains the check object: `id:'unit'`, `argv:'<ACCEPTED_ARGV>'`, `cwd:'.'`, `timeout:3`, `env:{PATH:'<CHECK_PATH>'}`, read-only effects with an inspection reference and all four effect flags false, `baseline:{status:'not-run', reference:'<BASELINE_REFERENCE>'}`, `dependencies:['app.py']`, and `thresholds:{exit_codes:[0]}`. State the exact composition: put this object in `plan.checks` and use `criteria:[{id:'acceptance', checks:['unit'], behavior:false}]`. `<ACCEPTED_ARGV>` must become a real approved argv array, not a literal executable string.
- [x] **Bind test placeholders to actual fixture values.** Use the current fixture task's accepted decision, `fixture.git('rev-parse', 'HEAD')`, fixture-only reasons/references, `os.defpath`, and a deterministic argv that checks the fixture's `app.py` content using `sys.executable`. The test validates the example through the real operation; it need not launch a native reviewer or run the planned check. Do not claim a baseline executed merely because its definition was accepted.
- [x] **Run the focused and relevant regression checks.** Run `python3 -B -m unittest tests.test_evidence.DocumentedEvidencePlanTests tests.test_workstreams_efficiency.DocumentedRequestTests`, then `python3 -B -m unittest tests.test_evidence`. Expected: all pass; no validator relaxation, accepted-rule change or real provider execution.
- [x] **Self-review and hand off the outcome.** Confirm examples match real fields and negative tests reach the intended boundary. Include it in the shared independent review. Suggested commit subject, only if authorized after review: `test: validate documented evidence plan examples`.

## Task 3: Audit and qualify delegation code simplification

**Files:** Inspect `runtime/worker_visibility.py`, `runtime/habblaud.py`, `scripts/worker_visibility.py`, managed call sites in `scripts/workflow.py`, and their callers. Potential edits are limited to `runtime/worker_visibility.py` and focused tests in `tests/test_worker_visibility.py`; expand only for an actual dependent caller. Do not rewrite Habblaud or introduce a validation framework.

**Interfaces:** Preserve `prepare_assignment(request, *, context=None, context_directory=None)`, `prepare_context(request, *, context=None, context_directory=None, reuse=False)`, and `prepare_native(path, request, *, reuse=False, observed=None)`, including results and failure behavior. `prepare_managed` must preserve the admitted prompt/report and attach only display preparation metadata. No `validated=True`/`skip_validation` bypass, new mandatory field or execution blocker.

- [x] **Record the complete caller and boundary map.** Use `rg -n 'prepare_assignment\(|prepare_context\(|prepare_native\(|assignment_context_path\(' runtime scripts tests -g '*.py'`. Distinguish in-memory input from stored contexts and native observations. Record where directory creation, locks, reads and atomic saves happen. Do not confuse `runtime/delegation.py::_prepare_native` with the presentation helper of the same name.
- [x] **Disposition the known duplicate before editing.** `prepare_assignment` validates the role before context-path creation; `prepare_native` validates it for direct/legacy callers. The unchanged role is checked twice, but simply deleting either call loses a boundary or changes side-effect ordering. Retain it unless a small consolidation uses one validation path for all callers while preserving early rejection. Extra helper layers solely to save one cheap validation call are not an improvement. Record retain/consolidate/remove and why in this task's completion note; a justified retain result completes the audit.
- [x] **If a genuinely smaller consolidation is identified, pin the missing observable behavior first.** In `NativeVisibilityTests`, add `test_invalid_assignment_role_does_not_create_context_directories`: for `None`, `{}`, an unknown role, Boolean version and an extra declaration field, assert `prepared is False`, unchanged input/name/message, no permission decision and unchanged directory inventory. Exercise both explicit context directory and an isolated existing `CODEX_HOME` with no visibility directory. Add `test_legacy_prepare_rejects_invalid_role_without_context_file` through the legacy CLI entry. These are characterization tests: establish their passing baseline before a behavior-preserving refactor, not an artificial red test.
- [x] **Implement only the justified local consolidation, or keep the current code.** Preserve every independent persisted-input check: `read_context` validates its envelope, not the nested declaration. Keep locked rereads, private/symlink protections, marker/digest exclusivity, frozen-context comparisons and native parent/child binding. Do not remove tests merely because they expose a caller the new example does not use. If safe consolidation requires larger architecture changes, record the reason and leave that work outside this slice.
- [x] **Verify existing public outcomes.** Run `python3 -B -m unittest tests.test_worker_visibility`. Relevant cases include original-input fallback, bound context preservation, omitted-role retry, marker idempotence, same-child reuse, restart restoration, both managed prepare routes and continuation preparation versus consumption. If code changed, also run `python3 -B -m unittest tests.test_openrouter_workers tests.test_delegation tests.test_native_reviews`. No live provider call is part of these checks.
- [x] **Review and finish the outcome.** If code changed, the review must account for all entry points and failure side effects. If retained, state that duplicate computation was confirmed but safe net simplification was not established. Do not report reduced latency or tokens without measurement. Commit only a real reviewed change under existing authorization; no empty cleanup commit.

## Task 4: Integrate, observe routing and remove stale material

**Files:** Update `docs/policy-sources.md` and regenerate `clients/instruction-map.json` after accepted source edits. Record results in this plan's execution section; keep raw traces/private fixture artifacts outside the repository. No new report registry, test service or installed hook.

**Interfaces:** Consumes accepted Task 1–3 diffs and test reports. Existing `scripts/read_instructions.py --prepare` owns generated-map format; `scripts/validate.py` verifies package consistency. Native checks use the candidate's exact instructions in disposable contexts, not a claim that the installed plugin upgraded itself.

- [x] **Reconcile source coverage and remove superseded guidance.** Map every changed rule/example to the surviving owner, including Toolkit pin `21b71b68679c11184f49c23168852014bf6dde8b` for newly adopted source requirements. Retain historical pin meanings. Remove the old ambiguous wording where replaced, stale cross-references and unused trial material; preserve required legacy inputs, fallbacks and held accounting state.
- [x] **Regenerate the map once and verify package checks.** Run `python3 -B scripts/read_instructions.py --prepare`, `python3 -B -m unittest tests.test_prepared_instructions`, `python3 -B scripts/validate.py`, and `git diff --check`. Regenerate again only after a later edit to a mapped source. Expected: exact map parity, tests pass, package reports `validation: passed`; its `client_execution: not_checked` is not a live result.
- [x] **Run bounded native routing cases in fresh child contexts.** Use the same native model/effort/authentication and minimal disposable inputs, with candidate instructions explicitly supplied. Do not create user-owned chats, restart Desktop or update global caches. Prepare every child through the existing shared assignment procedure. Keep evaluator expectations outside its brief; inspect observed tool reads/actions and actual artifacts, not only the child's report.

| Case | Task input | Required observed outcome |
|---|---|---|
| R1: ordinary fix | Tiny disposable Python bug, native checks/review, no managed controls selected | Applicable ordinary scope/planning/check/review policies; no managed evidence/runtime procedure or state DB; actual fix/check outcome supported. |
| R2: basic setup | Disposable non-Next project, existing custom instructions, useful setup guidance, no advanced controls | Complete catalog applicability assessment using the already pinned authoring source, relevant file inspection, native checks and recoverable changes; no forced registration/task activation; user content preserved. |
| R3: managed plan | Explicitly selected managed evidence-plan preparation in the existing disposable accepted fixture | Loads common evidence and plan guide; valid request; distinguishes accepted plan, unresolved security and later checks/review. No real runtime/account is enrolled by this case. |
| R4: maintenance routing | Initial setup and material instruction change as separate scoped prompts | Loads maintenance when applicable; reuses relevant evidence; does not invent an optimization trial, paid grader or client restart. |

- [x] **Compare only the relevant evidence.** Use the retained baseline for an observed routing failure and one candidate comparison; if the baseline was already correct, report candidate smoke evidence without claiming an improvement. Do not rerun to manufacture a pass. Select only changed cases; unresolved observations remain not run and prevent claiming that specific behavior qualified.
- [x] **Qualify changed delegation presentation only if Task 3 changes it.** Observe one fresh native worker's first badge and a follow-up on that same child, with no duplicate role declaration. Record the candidate preparation code and actually loaded hook/receiver versions separately; this is not proof of an installed-package upgrade. Compare the actual model display metadata; do not hardcode model names or treat helper output as rendering proof. Exercise persisted/restart behavior synthetically in existing tests; no live restart here. No new OpenRouter/OpenAI API run is needed for this native refactor.
- [x] **Inspect the final diff and finish review.** Verify source coverage, no lost protections or unrelated changes, examples' real validators, code-audit disposition and separate package/native results. Required independent review uses the exact final candidate and applicable security-impact assessment. Resolve findings within scope; unavailable observations stay explicit.
- [x] **Clean only owned evaluation resources.** Retain useful results, then remove this slice's disposable copies/trial assets. Do not delete existing research, active package versions, user files or held-run evidence. Preserve the reviewable diff and local `860ac89`; publication, merge and workflow rollout require their own existing authorization.

## Completion and execution handoff

Phase 1 acceptance required recorded Task 1–4 outcomes, passing selected checks, relevant native routing observations, an explicit code-audit disposition and the complexity acceptance above. Those outcomes were accepted within their stated limits. Completion did not require deleting useful code. Phase 2 is planned from these results and has not started automatically.

The saved Workstreams preference supports native child ownership with coordinator integration and independent review. Reuse owners for fixes; do not ask again merely to select that existing method. This planning request supplies no instruction to publish, merge or roll out the result.

## Planning verification — historical pre-execution checkpoint

Before execution, source interfaces and caller paths were inspected. An Astra/ultra investigator independently located the evidence fixture, accepted request shape and missing negative-plan coverage; the coordinator checked those against the real validator. The plan was self-reviewed against design Phase 1, including the five review-focus cases. Implementation and tests were still pending at that checkpoint; their subsequent outcomes appear below.

Authoring checks passed: package validation (`python3 -B scripts/validate.py`), local plan/spec links, code-fence balance and whitespace. Package output explicitly reports client execution not checked.

## Execution record

Implementation was authorized by the user on 5 November 2026. Base was `860ac89`; local `main` was inspected at `c4a4eb8` (six newer commits plus this branch's local instruction-consolidation commit). The implementation stage initially excluded integration/publication. The user subsequently authorized integration, recorded below, preserving newer naming/handoff changes.

Coordinator: native chat `01a1aba1-7e01-7b82-8afd-1dbea16e5623`, display family `W28`. This existing chat predates the selected naming revision; its title is preserved. New implementation children use `C` under that explicit association. These native identities are not evidence of enforced isolation.

| Outcome | Owner | Writable scope | State |
|---|---|---|---|
| Guidance and executable examples (Tasks 1–2) | `01a1abda-511a-72e0-bcb5-64ac38eeadc9` | Named guidance files and `tests/test_evidence.py` | Accepted; implemented and independently reviewed |
| Delegation audit (Task 3) | `01a1abda-7b71-7f12-8979-128e7df61505` | `runtime/worker_visibility.py`, `tests/test_worker_visibility.py` if justified | Accepted RETAIN disposition; 60 tests passed; no patch |
| Shared source/map, native observations, integration and acceptance (Task 4) | Coordinator | Shared generated/provenance files and this record | Accepted; complete within stated limits |

The user-approved two-owner/single-integrated-review method supersedes the execution skill's heavier fresh-agent/per-task-review defaults. Existing task records provide recovery; no extra workflow ledger is introduced. Baseline instructions were retained privately from the exact base. Raw reports and disposable cases live outside the checkout in the existing investigation directory.

Record accepted task outcomes, actual commands/results, native observations, complexity comparison, review and cleanup below. Planned or self-reported results do not establish acceptance.

### Check evidence

| Check | Observed result |
|---|---|
| Relevant onboarding and unenrolled checkout regressions | 16 tests passed |
| New documented examples before examples existed | Expected red: four methods/eight failures, all missing the named example |
| Documented evidence and existing delegation examples | Five tests passed through existing runtime operations |
| Full evidence regression | 56 tests passed, including the four new example methods |
| Worker visibility regression | 60 tests passed; no runtime or test change from the audit |
| Prepared instruction regression | 30 tests passed after final source/map synchronization |
| Package validator | Passed; its native client execution field remains `not_checked` |
| Whitespace | Passed |

The intermediate map check failed while the documentation owner was replacing
the guide's field inventory. The ordinary candidate also reported that stale-map
package check rather than repairing an unauthorized source. After source freeze,
the coordinator regenerated the map and reran the affected 30-test suite and
package validator successfully. This was an authoring synchronization issue, not
an observed routing or fixture-fix failure.

### Native observations

- R1 used fresh baseline/candidate children with the same configured native model
  (`gpt-6.1-sol`) and requested reasoning (`high`). Actual completed instruction
  commands show the baseline selected `clients/evidence.md`; the candidate did
  not. Both fixed the same disposable bug and passed the same check. Coordinator
  inspection and additional order/duplicate-preservation assertions passed.
- R2 added useful setup guidance and a README link, retained private preimages and
  assessed all 23 catalog entries. Coordinator readback verified unchanged team
  instructions/code and a passing native check, with no local runtime
  registration. Actual reads included maintenance during initial setup. Startup,
  trust/discovery remain explicit limits. The initially missing Harness2 authoring
  guides were later obtained and checked at the same pin; the existing local
  maintenance procedure supplies the native adaptation. No framework was installed.
  This is documentation-scope behavior evidence, not full client qualification.
- R3 loaded the common evidence and operation guide and accepted the documented
  minimum through the real operation in a disposable accepted fixture. Revision
  advanced from 1 to 2; state stayed `TRIAGE`, stored plan matched and attempts
  stayed empty. Security impact, behavioral proof and independent reviews were
  correctly reported as outstanding. The observations remain synthetic managed
  state inside actual native execution, not live managed-client qualification.
- R4 reused the same native Tester without another role declaration. Actual
  procedure reads selected maintenance for the material instruction change, and
  existing positive/empty plus four negative-input cases passed. No application
  defect or optimization trial was invented.
- Integrated Astra/ultra review found no material issue in the frozen eight-file
  candidate: specification, correctness, security and complexity passed. Its R2
  fixture policy-link gap was corrected by the same owner, then verified by the
  coordinator against actual files and exact recovery hashes.

### Complexity disposition

Runtime code, schemas, dependencies, services and execution steps are unchanged.
The 712-word managed evidence contract was absent from the candidate ordinary
task's observed selections. No token, latency or reliability improvement is
claimed. The candidate also chose to load delegation and Workstreams guidance,
so this single comparison does not establish lower total loaded context or fewer
commands. The operation-specific plan guide grew from 195 to 474 words and the
reviewed onboarding guide from 488 to 704 words; these selectively loaded
examples and variants cost authoring context, while ordinary tasks avoid them.
The implementation adds four focused test methods (86 lines) to catch example
drift and malformed/stale input. No production parser, repeated role declaration,
blocker, form or extra status store was introduced. Removing the cheap duplicate
role check would lose a boundary or add helper machinery, so it was retained.

### Acceptance and cleanup

At local acceptance, Phase 1 was implemented and its required output scope complete;
conditional refactor tests, broader route suites and live badge requalification
were inapplicable because the audit adopted no runtime change. Checked steps
above include those explicit conditional dispositions, not claims they executed.

The independent reviewer verified all eight source hashes; they remain unchanged
through final acceptance. The policy-index route in the disposable setup guide
and its retained after-image/journal match. The coordinator closed the temporary
authoring-source gap with the two pinned Harness2 references and mapped them to
the existing native maintenance procedure, without executing an upstream prompt.

Private reports, fixture contents, hashes and recovery evidence are retained in
the investigation record. Evaluation-owned package copies and working fixture
directories were removed after retaining their inputs/results. No active cache,
held run/reservation, historical user evidence or other chat was altered.

At that checkpoint, package validation and whitespace checks passed. Runtime
code/API/schema and dependencies were unchanged. The working diff and both
planning documents were local and uncommitted; no push, merge, publication or
workflow rollout had occurred. Later integration is recorded below.
At Phase 1 completion, fresh Desktop startup/discovery/trust, live managed
integration and reliability or cost improvements were unqualified. The subsequent
bounded Desktop follow-up below refines only the observed startup scope.
Phases 2–4 did not start.

### Fresh Desktop follow-up — 5 November 2026

The user launched and reviewed trust for fresh chat `01a1abfc-27b5-7f82-8bac-97309545741f`
(`Run Phase 1 Desktop qualification`) in the prepared disposable project. Native
metadata records originator `Codex Desktop`, backend `0.166.0-alpha.6`; installed
app metadata is version `26.1029.61003`, build `24614`. Native turn settings show
`gpt-6.1-sol`, `ultra`, existing `never` approval and `danger-full-access`; the
qualification did not change those choices.

The native record contains the candidate's exact startup developer context at
zero-based record 12, before the first tool at record 21. It matches the
independently computed reviewed dispatcher output. The first tool ran the delivered bootstrap
successfully; the task did not manually invoke SessionStart. The arithmetic
check failed before its one-operator correction and passed after. Coordinator
review and additional assertions confirmed the result, unchanged accepted test,
no runtime state and all 328 unchanged candidate file hashes. The candidate is
the frozen qualification snapshot; this later authoring note does not repin it.

Positive result: project-local candidate startup orientation and ordinary
instruction consumption observed in a fresh Desktop chat. This is not an app
restart, marketplace installation test or formal managed-enforcement activation.
No per-run hook ID/receipt is claimed. User-confirmed native trust, unchanged
reviewed configuration, unique exact output and native record order provide the
bounded provenance; Computer Use access to `com.openai.codex` remained denied.

The user also launched fresh unrelated-project chat
`01a1ac1c-1be1-7761-9d94-99bae7215868` (`Run unrelated-project negative control`).
Its native record contains no candidate orientation before the first tool at
zero-based record 20. The hook and local plugin override match the positive
fixture byte for byte; no workflow enrollment or runtime state exists there. The fixture is
unchanged, and all 328 candidate hashes still match, with no unlisted files.
Both disposable project entries were subsequently observed as `trusted`.

Negative result: expected absence of workflow context observed. Native hook
execution and hook-specific trust were not witnessed, so the cause of that
silence remains inconclusive. Project trust alone does not establish hook
execution. The local subprocess preflight is not counted as native proof.
Both qualification turns completed; the broader managed, marketplace and app
restart claims remain unqualified. No active chats, global model/provider
configuration or held API run/reservation were changed by the qualification.

Independent Astra/ultra review found no material issue in that bounded result.
After confirming both qualification chats were idle, the coordinator archived
the five exact disposable configuration/enrollment files and removed those
files. Chat working directories, Git repositories, the arithmetic result and
frozen candidate/evidence remain available. Cleanup did not edit native trust
records, global settings, other chats or held state. At this Desktop checkpoint,
the working change was local and uncommitted; this check did not publish or roll
out the package. Subsequent integration is recorded below.

Detailed private proof: `desktop-qualification/native-startup-proof.json`,
`native-negative-proof.json`, `positive-report.md`, `negative-report.md`,
`coordinator-verification.json`, `final-review.md`, `cleanup.json`, the exact
configuration archive and candidate manifest in the existing investigation
directory.

### Phase 1 integration — 5 November 2026

The user authorized integration into `main`. The reviewed Phase 1 snapshot was
committed as `cd2fb90`; the earlier completed role-instruction consolidation
`860ac89` remains in its history. The target inspected after fetching is
`c4a4eb856b7c491d706148acb760bad066f4489c`. Reconciliation preserves its newer
naming, handoff, runtime and release metadata. The only textual merge conflict
was the generated instruction map, regenerated from the reconciled owners.

Integration verification covers the current merged instruction/test tree.
The existing native Desktop observations remain scoped to their frozen candidate
and are not a new installed-package or rendered-badge qualification. Required
checks, independent review and the actual merge result are recorded in the
private Phase 1 integration evidence. No plugin release, marketplace update,
installed-hook change, active-chat interruption or held-run mutation is included.

Final result: [PR #103](https://github.com/fcoouto/vibe-coding-workflow/pull/103)
merged as `7dd2bdb7046c80e1ccd046986213353bad830774`. The merged tree
`3dddbf2bf74fe7c1effc16a9d2a13cb94f66a3b5` exactly matched the independently
reviewed candidate. All 163 selected tests, package validation and whitespace
checks passed. Local and remote `main` were synchronized; the existing worktree
was retained clean. The completed role consolidation remains an ancestor.
No installed-package rollout occurred, and the held API run/reservation remained
untouched. The private integration receipt preserves the exact candidate, checks,
review and merge observations; this result does not broaden Desktop qualification.
