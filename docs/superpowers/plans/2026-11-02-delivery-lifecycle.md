# Task Delivery Lifecycle Implementation Plan

> **Historical record:** test file names below are as written at the time. Several were later merged into `tests/test_delivery.py`, `tests/test_delivery_github.py`, `tests/test_delivery_reconcile.py` and `tests/test_workstreams*.py`; test class and method names are unchanged.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Follow the user's chosen execution method and the repository's worker-selection rules; this document does not dispatch workers.

**Goal:** Deliver reviewed changes through tracked PRs, with manual merge by default, optional authorized agent merge, accurate `WAITING`/`DONE` titles, and remote-only branch cleanup.

**Architecture:** Extend canonical onboarding and existing task/evidence records; keep D5 implementation proof separate from D7 delivery observation. Reuse native worktree management, the shared ownership ledger, and Thread Naming. Add a focused delivery coordinator and GitHub adapter, not another state database or scheduler.

**Tech Stack:** Existing Python standard library, SQLite authority/artifact storage, unittest, Git, supported native client tools, and an existing authorized GitHub connection. The executable adapter can use installed `gh`; do not install tools, add credentials, or change authentication implicitly.

**Spec:** [Task delivery and client owned worktrees](../specs/2026-11-02-delivery-lifecycle-design.md).

**Review base:** Published `origin/main` at `9c364e5d9bd80ec23aa677b8b237f4bd00a7d718`. The feature-only commits were replayed onto this base on `codex/delivery-lifecycle-foundations`; the original `codex/delivery-lifecycle` branch remains intact. Current main's strict runtime/policy pins, configured-only entry handling, shared ledger, explicit evidence cleanup and ordinary title repair remain in place.

## Execution checkpoint

This is a partial implementation, prepared for source review rather than full lifecycle acceptance. Implemented: canonical policy adoption and task overrides; admitted workspace binding; bounded provider reads and existing-PR reconciliation; ordinary/linked completion and retention guards; bounded pending inventory; accepted scope extensions; guarded GitHub metadata/merge and conditional Git deletion primitives. See [execution evidence](../../delivery-validation.md) for verification and boundaries.

High-level checkpoint/commit/push, READY/reopen receipts, agent merge and remote cleanup orchestration are not implemented. Companion acceptance remains conservatively pending. Production native-client execution/independent descendant-stop authority and D7 target enforcement remain external prerequisites. No callback, caller JSON, hook completion or successful process exit supplies those authorities. No enrollment, live activation, provider mutation or background watcher was configured. The checklist below remains the full target; passing a library fixture does not complete its production task.

Implementation decisions refine two provider details in Tasks 4 and 6. Existing PR title/body writes cannot preserve concurrent human edits through an unsupported conditional PATCH. Workflow state therefore uses `[BLOCKED]`/`[READY]` labels and append-only, operation-marked checkpoint comments. Existing human title/description are never replaced. Merge uses synchronous REST with an exact head SHA, rather than `gh pr merge`, which may implicitly enqueue or enable auto-merge. A separate qualified source must also bind the target through the effect: GitHub's stock expected-head condition does not provide that guarantee. This source is unavailable and merge remains disabled by default.

## Global Constraints

- Manual merge is the onboarding default; explicit per-task overrides preserve target-specific authority and revocation. Publication and remote deletion are separately authorized.
- Separate worktrees are the implementation default. Reuse a suitable isolated checkout and prefer native creation. Preserve opt-outs; failed isolation never silently permits primary-checkout edits.
- Local worktree cleanup is client-owned. Do not remove local worktrees/branches, archive chats, alter retention, or add a fallback deletion engine.
- Reuse the existing nine task states unchanged. `WAITING` includes merge waits; task reasons explain the dependency. Thread `READY` means ready to begin, while PR `[READY]` means ready to merge.
- Preserve required checks, security assessment/review, independent review, current candidate binding, ownership reconciliation, immutable identities, manual title edits, and existing limits.
- Keep state outside disposable worktrees and package caches. Reuse existing task records, artifact ownership, transactions, conditions, and linked-task support.
- No new scheduler, watcher, webhook installation, Actions dependency, service, model/provider change, billing route, or runtime Toolkit fetch. Preserve the user's coordinator settings.
- Package tests, provider fixtures, native client behavior, and live repository enforcement are separate evidence. This plan authorizes no live PR creation, merge, branch deletion, repository-rule change, or credential setup.

## Review Focus

1. Client deletes a worktree before manual merge: pending delivery survives; remote observation works without recreating that checkout (Tasks 2/6).
2. Lost PR-create or merge acknowledgment: discover the actual outcome before retrying, with no duplicate PR or repeated merge (Tasks 3/4/6).
3. Reused branch, changed target, fork, or hostile remote configuration: never mutate another destination or delete newer work (Tasks 3/7).
4. A child completes while parent integration remains: no premature parent `DONE`, averaged meters, or title-based acceptance (Task 5).
5. Settings/authority change while a task waits: preserve its accepted plan, honor revocation, and never resume paused work from a remote event alone (Tasks 1/5/6).

## Existing implementation and dependency boundary

| Existing owner | Current behavior to reuse or correct |
| --- | --- |
| `runtime/startup.py`: `decode_project_record`, `canonical_scope_projection`, `acquire_project_entry`; `runtime/onboarding.py`: `record_plan`; `runtime/changes.py`: `_canonical_record`, `apply_plan` | Canonical Project/Repository/checkout identities and recoverable D3/D4 adoption. Repository fields are strictly validated; new policy must participate in accepted scope and update/recovery. |
| `runtime/state.py`: `start_task`, `update_task`, `task_status` | Immutable task binding, revisions, conditions, milestones, and explicit `resolution_ref` when leaving `WAITING`/`BLOCKED`/`PAUSED`. Ordinary updates cannot set `DONE`. |
| `runtime/evidence.py`: `_verify_member`, `verify_completion`, `verify_linked`, `_revalidate_linked`, `evidence_status` | Local and linked proof already exist. Both successful completion paths can set `DONE` without requiring delivery. Linked plan v2 has one delivery per member and an internal trusted caller, not a public general-purpose API. |
| `runtime/title_sync.py`; `scripts/workflow.py`: `title_report` | Render canonical status and accepted milestones, persist pending writes, respect manual edits, and repair through existing events. Do not add merge logic to the renderer. |
| `runtime/repository_gate.py`: `evaluate`; `scripts/repository_gate.py` | Offline fixture decisions only; the CLI always fails closed. Neither fixture input nor `evidence_valid` authorizes merge. |
| `scripts/onboard_coordinator.py` | Existing pattern for a callable coordinator-owned operation boundary whose decision references bind independently verified authority. Reuse the pattern, not its onboarding-specific class. |

The [D7 plan](../../d7-repository-gate-plan.md) and [GitHub App plan](../../d7-github-app-plan.md) remain prerequisite owners of protected evidence collection, publishing and repository enforcement. An installed App or successful API read does not qualify those capabilities. Build and test this lifecycle without bypassing that boundary: draft publication and factual delivery observation can be qualified separately; READY and agent merge stay blocked on targets lacking their required D7 proof. Completing this plan means all claimed paths are qualified, or clearly reported as incomplete due to those dependencies—not calling the whole feature complete after mocks pass.

## File and interface decisions

Create only three runtime surfaces:

- `runtime/delivery.py`: delivery policy validation, task-owned publication plan/operations, reconciliation, completion requirements, and bounded pending reads. It uses existing state/artifact storage.
- `runtime/adapters/github_delivery.py`: bounded provider reads and exact Git/GitHub operations. No task-state or title decisions.
- `scripts/delivery_coordinator.py`: callable native coordinator entry for verified authority, native worktree observations, D5/D6/D7 source acquisition, and operation dispatch. No JSON `trusted:true`, executable path from PR content, or caller-supplied "passed" object establishes authority.

New local procedure: `clients/delivery.md`, loaded only for worktree preparation, publication, delivery reconciliation or remote cleanup. Existing onboarding and completion entry points link it selectively. Do not add a new skill or status registry.

Use these interfaces across tasks (new names are implementation decisions):

```python
# runtime/delivery.py
def validate_policy(value: dict) -> dict: ...
def effective_policy(saved: dict, override: dict | None) -> dict: ...
def operate(scope, action: str, request: dict, *, owner=None) -> dict: ...
def completion_requirements(scope, db, task: dict) -> dict: ...
def pending(scope, *, cursor: str | None = None, limit: int = 32) -> dict: ...

# runtime/adapters/github_delivery.py
class GitHubDelivery:
    def __init__(self, *, executable, host, run): ...
    def observe(self, publication: dict) -> dict: ...
    def execute(self, action: str, operation: dict) -> dict: ...

# scripts/delivery_coordinator.py
class DeliveryOperationOwner:
    def __init__(self, verified_decision, *, native, repository, gate): ...
    def run(self, scope, action: str, request: dict) -> dict: ...
```

`operate` actions are `plan`, `bind-worktree`, `checkpoint`, `ready`, `reopen`, `reconcile`, `merge`, `cleanup`, and `status`. Mutations use existing client/session/task/scope identity, positive `expected_revision`, and decision references. External effects additionally use stable `operation_id`, exact publication IDs, and an admitted owner. Store durable intent before effects and acknowledge actual readback afterward. Replays with changed inputs fail; uncertain attempts remain pending. Do not keep database transactions open during network or native tool calls.

Policy shape: `{version:1, merge:"manual"|"agent", separate_worktrees:bool, targets:[{provider,host,repository,target,merge_method,publication_ref,merge_ref,remote_cleanup_ref}], decision_ref}`. Target identity must be observed, not inferred from directory names. `merge_method` is an adopted `merge`, `squash`, `rebase`, or `queue`; authorization references may be null for unapproved operations. `effective_policy` resolves a task override `{merge,decision_ref}` but does not authenticate permission. The owner rechecks authority before each mutation.

Delivery plan shape: `{version:1, decision_ref, policy, workspace, publications, companions}`. `workspace` records the accepted checkout reference, native/client owner, base, and verified isolation or explicit exception. Each publication records stable `id`, repository/host/target, owned branch, candidate reference, nullable PR identity until publication, dependencies and whether delivery is required. Required unpublished work remains in this list. Companions use existing canonical task references and shared acceptance; do not infer relationships from title family codes. Bound publication/companion lists to 64 and requests to the existing 128 KiB ceiling; fail explicitly rather than truncate.

Store the managed plan artifact and operation/observation references under the existing task's optional `delivery` member. Snapshot the adopted policy; do not resolve defaults again on resume. Keep implementation proof and delivery proof distinct. `completion_requirements` returns `{delivery_required, delivery_complete, pending, blockers}` using retained authenticated artifacts and current ownership/plan bindings; it performs no remote calls and writes no title. `pending` returns known records plus a continuation cursor, never a filesystem scan or a claim that omitted pages are complete.

### Task 1: Adopt repository policy and freeze task decisions

**Files:** Create `runtime/delivery.py`, `tests/test_delivery.py`. Modify `runtime/startup.py`, `runtime/onboarding.py`, `runtime/changes.py`, `runtime/state.py`, `tests/test_startup.py`, `tests/test_onboarding.py`, `tests/test_changes.py`, and `tests/test_state.py`.

**Interfaces:** Implement `validate_policy`, `effective_policy`, and the `plan`/`status` portion of `operate`; extend canonical repository entries with optional versioned `delivery_policy`. Include it in accepted projections and D4 postimages, rollback and qualification bindings. Missing policy in historical enrollment grants no new authority; newly adopted policy defaults to manual/separate worktrees. New editable tasks must explicitly record their delivery scope or an evidenced no-publication decision before completion.

- [ ] Add `DeliveryPolicyTests.test_defaults_and_task_override`: default selection is `manual` and `separate_worktrees=True`; an override selects only that task's mode, requires a decision reference, and cannot mutate the saved policy. Add rejection cases for unknown fields, invalid target/method, duplicate identities and unbounded lists.

  Core assertions against a complete adopted manual-policy fixture:

  ```python
  original = deepcopy(saved)
  self.assertEqual(effective_policy(saved, None)["merge"], "manual")
  self.assertTrue(effective_policy(saved, None)["separate_worktrees"])
  self.assertEqual(effective_policy(saved, {"merge": "agent", "decision_ref": "user:task-override"})["merge"], "agent")
  self.assertEqual(saved, original)
  ```
- [ ] Add adoption tests for explicit worktree opt-out, manual-only target, revoked agent permission, interrupted D4 apply/rollback, and original pinned tasks after a policy change. Assert old records retain their bytes and current tasks retain their effective policy.
- [ ] Run `python3 -B -m unittest tests.test_delivery tests.test_startup tests.test_onboarding tests.test_changes tests.test_state -q`; confirm the new assertions fail for the missing policy behavior, not fixture setup.
- [ ] Implement only the policy/plan persistence and current authority checks above. Use the existing canonical update procedure; do not bootstrap a second authority or reinterpret historical records. Runtime fingerprint changes require the normal fresh-state transition.
- [ ] Run the same command to PASS, update policies 01/02/10 and onboarding references for these decisions, and independently review the adoption/rollback boundary. Commit this slice as `feat: adopt task delivery policies`.

### Task 2: Prepare native worktrees and preserve publication ownership

**Files:** Modify `runtime/delivery.py`, `runtime/state.py`, `runtime/delegation.py`, `tests/test_delivery.py`. Create `scripts/delivery_coordinator.py`, `tests/test_delivery_coordinator.py`, and `clients/delivery.md`. Update `clients/codex.md` and `clients/claude-code.md` only for their supported preparation paths.

**Interfaces:** Implement `DeliveryOperationOwner.run` and `operate(..., "bind-worktree", ...)`. The owner invokes supported native tools outside state transactions, observes actual repository/common-Git/checkout identity, adopts it through existing D4 entry when needed, then records the binding. A supplied path or child title alone cannot prove isolation or admission.

- [ ] Add tests asserting an existing task-owned isolated checkout is reused, editable primary-checkout work requests native isolation, read-only work creates none, and failed isolation cannot admit edits without the explicit exception. Test parent instruction and enrollment discovery after the native cwd changes.
- [ ] Add a lost-native-create-response case: retain operation identity, discover its existing native attachment, and never create a second worktree on blind retry. Reject foreign/pinned/shared ownership substitution.
- [ ] Run `python3 -B -m unittest tests.test_delivery tests.test_delivery_coordinator -q` and observe the new failures.
- [ ] Implement native owner callbacks using the actual available client mechanism. Keep pending records at the stable accepted authority; bind publications to immutable repository/task identity, not the temporary path. From a surviving enrolled checkout, allow later reads of that owned delivery record without rebinding the original task or inventing a missing checkout. Preserve explicit client/user cleanup ownership.
- [ ] Pass the same tests, including removal of a disposable worktree after READY with its pending record still readable. Assert no local removal, archive, retention change, or local-branch deletion command is emitted. Review the isolation and missing-checkout boundary; commit `feat: prepare client-owned delivery worktrees`.

### Task 3: Acquire provider facts and qualify the execution boundary

**Files:** Create `runtime/adapters/github_delivery.py`, `tests/test_github_delivery.py`. Modify `scripts/delivery_coordinator.py`, `runtime/delivery.py`, `tests/test_delivery_coordinator.py`; extend `docs/d7-validation.md` with separately labeled observations.

**Interfaces:** Implement `GitHubDelivery.observe/execute` behind the owner. `observe` returns bounded repository/PR IDs, head/base refs and commits, draft/state, merged commit/time, check/review/queue facts and unavailable fields. Normalize facts, not trust: the owner independently binds the configured account/host, repository identity, D5/D6 receipts and qualified D7 source. Missing gate capability remains an execution blocker.

- [ ] Add adapter tests for wrong host/repository, fork head ownership, partial pagination, unknown check/review outcomes, malicious ref/URL text, redacted authentication errors, timeouts, and output overflow. Assert structured argv and no shell evaluation, secret output, or inferred successful empty response.
- [ ] Add owner tests proving caller JSON, a fabricated passed check, or `repository_gate.evaluate(...)["evidence_valid"]` cannot enable READY/merge. Preserve the existing offline CLI's always-failing exit and execution blockers.
- [ ] Run `python3 -B -m unittest tests.test_github_delivery tests.test_delivery_coordinator tests.test_repository_gate -q`; confirm missing adapter/trust behavior fails.
- [ ] Implement against the existing authorized connection. Prefer exposed supported connector capabilities during native operation; the Python runner uses a deliberately selected installed `gh` executable and existing auth without reading tokens. Pin host and exact repository/PR; inspect effective target rules, check-source identity and bypasses through the existing D7 owner. Never turn an unavailable required observation into inapplicability.
- [ ] Pass fixture tests and record the read-only capability result. Before promoting real READY/merge support, complete the existing D7 protected collector/publisher qualification and per-target enforcement probes. Keep its approved on-demand local publisher architecture and permissions; do not replace it with a new service or silently expand the installed App's read-only Contents/PR permissions. Commit `feat: observe delivery through qualified repository sources`.

### Task 4: Publish recoverable draft PRs and readiness checkpoints

**Files:** Modify `runtime/delivery.py`, `runtime/adapters/github_delivery.py`, `scripts/delivery_coordinator.py`, `tests/test_delivery.py`, `tests/test_github_delivery.py`, `tests/test_delivery_coordinator.py`, and `clients/delivery.md`.

**Interfaces:** Implement `checkpoint`, `ready`, and `reopen`. Checkpoint payload records milestone, explicit publishable paths, snapshot, initial title/description and append-only checkpoint content. Reuse the accepted plan's milestones; no fixed new global checkpoint counter. External effects remain guarded by the durable `operation_id` and frozen input digest.

- [ ] Add `test_checkpoint_creates_one_draft_blocked_pr`: only explicit owned files are committed/pushed; resulting PR is draft `[BLOCKED]`; unrelated changes and human title/body text survive. No diff produces neither an empty commit nor a PR.
- [ ] Add replay tests after uncertain commit, push, PR creation, checkpoint comment and ready promotion. A lost create acknowledgment discovers the existing PR; ambiguous ownership remains pending. A changed request cannot reuse the operation ID.
- [ ] Add tests that READY requires current local/general/security proof and all required authoritative remote checks/reviews/mergeability. Unknown remote facts or unqualified D7 block promotion. `reopen` must withdraw `[READY]` before dependent edits; failed withdrawal cannot silently permit implementation.
- [ ] Run `python3 -B -m unittest tests.test_delivery tests.test_github_delivery tests.test_delivery_coordinator -q` to observe failures; implement the three operations using existing candidate capture and D6 resource claims. Keep publication error reporting precise: verified local work may exist with `PR update pending`.
- [ ] Pass those tests, attach the resulting PR through the native attachment mechanism when available in qualified execution, and verify bounded readback preserves human edits. Independent review precedes `feat: publish recoverable delivery checkpoints`.

### Task 5: Make completion delivery-aware and reuse Thread Naming

**Files:** Modify `runtime/delivery.py`, `runtime/evidence.py`, `runtime/state.py`, `scripts/workflow.py`, `tests/test_evidence.py`, `tests/test_canonical_retention.py`, `tests/test_canonical_linked_closure.py`, `tests/test_task_statuses.py`, `tests/test_title_sync_integration.py`, `clients/evidence-completion.md`, `clients/evidence-retirement.md`, and `clients/workstreams-titles.md`. Add `tests/test_delivery_completion.py`. Leave `runtime/task_statuses.py` and title formatting unchanged.

**Interfaces:** Implement `completion_requirements`. Call it before ordinary and linked completion commits and in current-status revalidation. Extend the latest linked aggregate artifact format only as needed to bind each member's delivery-plan digest and complete publication set; retain historical readers. Do not force several PRs into the old single-delivery field or expose `verify_linked` as a raw caller-supplied-observation CLI.

- [ ] Add tests using `EvidenceTests` and canonical linked fixtures: passing implementation with required unmerged PRs yields `implementation_verified=True`, `delivery_complete=False`, and never `DONE`; no-publication scope retains ordinary completion behavior. Block required unpublished work, omitted companions and changed delivery plans.
- [ ] Add cases for one merged/one open PR, child `WAITING` with parent `WORKING`, all children complete with parent integration pending, shared PR deduplication, and adding accepted scope after completion. Parent milestones reflect accepted aggregate outcomes, not child averages or PR counts.
- [ ] Add title assertions through `TitleIntegrationTests`/`NativeFixture`: merge wait renders `WAITING` below five cells; authoritative completion renders `DONE` at five; paused/manual titles and lost title acknowledgments retain existing behavior. Rendering performs no provider call.
- [ ] Run `python3 -B -m unittest tests.test_delivery_completion tests.test_task_statuses tests.test_title_sync tests.test_title_sync_integration -q` and confirm failures before the change.
- [ ] Preserve existing implementation verification artifacts when only delivery is pending. Record a merge-wait reason/condition only when no independent work can advance; otherwise retain the actual working/review phase. `verified` must mean the requested overall completion passed, while `implementation_verified` reports its narrower proof. No transient full-meter `DONE` before changing to `WAITING`.
- [ ] Integrate all completion writers, linked revalidation, retained-source dependencies and retirement guards. Keep pending merge/recovery records protected. Reconciliation must resolve the specific wait through existing `resolution_ref` handling before final verification; never bypass unrelated `PAUSED`/`BLOCKED` conditions or give ordinary updates permission to set `DONE`.
- [ ] Pass the focused command plus `python3 -B -m unittest tests.test_evidence tests.test_canonical_retention tests.test_canonical_linked_closure -q`. Review ordinary/linked/retired boundaries; commit `feat: require delivery before task completion`.

### Task 6: Reconcile manual merges and execute authorized agent merges

**Files:** Modify `runtime/delivery.py`, `runtime/adapters/github_delivery.py`, `scripts/delivery_coordinator.py`, `scripts/workflow.py`, `tests/test_delivery_coordinator.py`, `tests/test_github_delivery.py`, `tests/test_delivery_completion.py`, and `clients/delivery.md`.

**Interfaces:** Implement `pending` and `reconcile`/`merge`. Expose bounded read-only `delivery status` and `delivery pending` through `scripts/workflow.py`; native coordinator entry owns mutation/verified observation, using the original task's stable authority. No public request can submit authoritative merged/check-passed facts.

- [ ] Add tests for user-reported merge causing observation, open PR remaining pending, closed-unmerged requiring disposition, squash/rebase merged-head correspondence, changed reviewed candidate, and queue admission remaining incomplete. An observed bypassed merge is a fact, not retroactive review success.
- [ ] Add manual-policy tests asserting zero merge calls, agent override tests requiring fresh authority and exact head, and revoked permission/target movement/failed gate tests asserting zero mutation. Lost merge acknowledgment reads actual PR state before any retry.
- [ ] Add bounded reconciliation tests: at most 32 known records per page, continuation cursor retained, no invented completeness at exhaustion, no repeat of unchanged observations within the same invocation, and no startup-hook network call. Missing original worktree does not prevent remote delivery observation from a surviving admitted authority.
- [ ] Run `python3 -B -m unittest tests.test_delivery_coordinator tests.test_github_delivery tests.test_delivery_completion -q` to observe failures; implement observation and exact authorized merge. With `gh api`, use explicit repository/PR and synchronous REST merge with exact `sha` and method, through a separately qualified target-binding source; never `--admin`, implicit queue/auto-merge, or `--delete-branch`. Queue paths require separately qualified queue evidence; an unqualified queue remains blocked.
- [ ] Reconcile via user notification, explicit status/cleanup request, task resume, or bounded coordinator preflight at the next repository task. Persist authoritative observations before status/title repair. For a removed checkout, retain the sealed committed candidate and original check/review/ownership provenance; do not claim fresh local execution. If required current proof cannot be established, record remote delivery separately and leave acceptance unresolved rather than fabricate `DONE` or recreate a checkout merely to inspect GitHub.
- [ ] Pass tests, including waiting-reason resolution without releasing an explicit user pause. Review replay/race behavior; commit `feat: reconcile manual and agent PR delivery`.

### Task 7: Clean only eligible remote branches

**Files:** Modify `runtime/delivery.py`, `runtime/adapters/github_delivery.py`, `tests/test_github_delivery.py`, `tests/test_delivery_coordinator.py`, and `clients/delivery.md`. Add `tests/test_delivery_cleanup.py` with disposable bare Git remotes.

**Interfaces:** Implement `cleanup`; return per-publication `absent`, `deleted`, `retained`, `pending`, or `disabled`, independently of delivery completion. Use the same operation intent/replay boundary and current branch ownership/authorization.

- [ ] Add real disposable-remote tests: expected ref deletion succeeds; an advanced or reused ref is retained; already absent succeeds only after a successful lookup. Reject default/protected/foreign branches, open dependent PRs, stacked branches, unresolved writers and unknown ownership. Assert every local branch/worktree remains intact.
- [ ] Run `python3 -B -m unittest tests.test_delivery_cleanup -q` and confirm missing cleanup behavior fails.
- [ ] Prefer already-enabled provider deletion, verifying actual absence. Otherwise delete only the exact owned remote ref with an explicit expected-OID lease, through the qualified Git transport. Inspect remote/push configuration and hooks first; reject unexpected destinations or side effects, do not bypass hooks. Never use unconditional deletion when a changed-ref race cannot be excluded.
- [ ] Qualify the explicit lease with the real disposable remote before enabling it against a provider. If provider/transport cannot honor the required conditional operation, retain the branch with a precise next action. Read back absence after deletion and reconcile lost acknowledgments before retrying.
- [ ] Pass cleanup, provider and coordinator tests, including a ref advancing between preflight and deletion. Independent security review precedes `feat: clean verified remote delivery branches`.

### Task 8: Complete routing, integration proof and client qualification

**Files:** Update `policies/00-index.md`, `policies/01-project-scope.md`, `policies/02-task-planning.md`, `policies/05-tests-and-builds.md`, `policies/07-context-and-handoff.md`, `policies/08-merge-readiness.md`, `policies/10-project-onboarding.md`, `policies/11-tool-integrations.md`; `clients/index.md`, `clients/runtime.md`, `clients/delivery.md`, `clients/evidence-completion.md`, `clients/workstreams-titles.md`; `skills/onboard-my-project/references/procedure.md`, `skills/onboard-my-project/references/apply-and-recovery.md`; `README.md`, `scripts/validate.py`. Add `docs/delivery-validation.md` and `tests/test_delivery_cli.py`; update `tests/test_hook_manifest.py` only for actual routing changes.

**Interfaces:** No new status or skill. Route onboarding to policy adoption, editable task entry to workspace preparation, publication/merge requests to delivery, and read-only questions to ordinary scope. `title_report` remains the only CLI title-repair handoff. Shared rules have one owning contract; clients describe supported invocation only.

- [ ] Add CLI/routing tests asserting read-only status has no provider mutation, hook startup stays read-only, unsupported client capabilities stay explicit, and delivery commands cannot bypass authority or WAITING resolution. Run `python3 -B -m unittest tests.test_delivery_cli tests.test_hook_manifest -q` to observe the new failures, then implement the routing.
- [ ] Walk the spec's twelve acceptance cases using only applicable local instructions. Record loaded paths, decisions, exact tests and gaps in `docs/delivery-validation.md`; keep upstream provenance in the source map and every skill entry within 150 words. Preserve local-cleanup opt-outs and client ownership.
- [ ] Run all new delivery modules and affected tests from Tasks 1–7, then `python3 -B -m unittest discover -s tests -q`, `python3 -B scripts/validate.py`, and `git diff --check`. Fix introduced regressions; do not suppress or remove existing gates.
- [ ] Obtain independent behavior/security review of the final candidate under policy 03, preserving coordinator model/provider/reasoning. Test manual and agent modes on separately authorized disposable targets with real native worktrees, title updates, PRs, conditional merges, provider rules and cleanup. Reuse accepted client/D7 evidence only where its bindings still apply; qualify changed seams. Record absent required capabilities as incomplete claims.
- [ ] Commit the integrated guidance/validation as `docs: qualify task delivery lifecycle`. Report package/fixture proof, observed native behavior, provider qualification, and remaining D7/live-adoption blockers separately. Do not publish, merge, install, or change live rules without the relevant existing authorization.

## Source basis and plan review

Historical inspection at the original planning base `8d21679bdc60f0f8808a156797bcedf086d11480` confirmed the nine-state vocabulary, the then-unconditional ordinary/linked `DONE` transitions, existing title repair/manual-edit controls, strict canonical fields, single-delivery linked schema, and the offline D7 boundary. No alternate worktree's unmerged implementation is assumed available.

[GitHub CLI merge documentation](https://cli.github.com/manual/gh_pr_merge), checked through Context7 and the official page on 2 November 2026, establishes expected-head matching, queue behavior, and that `--delete-branch` affects both local and remote branches. Consequently this plan excludes that flag. [Git push documentation](https://git-scm.com/docs/git-push), checked the same day, specifies explicit expected-ref leases; actual conditional deletion remains a required disposable test, not a conclusion from a read-before-delete check.

Self-review mapping: onboarding/overrides → Task 1; native worktree ownership → Task 2; trusted repository sources → Task 3; draft/checkpoints/READY → Task 4; WAITING/DONE, multiple PRs, parent/child acceptance and retention → Task 5; both merge modes/manual triggers → Task 6; remote-only cleanup → Task 7; selective loading, full verification and client evidence → Task 8. Review Focus inputs are assigned above. That planning record did not change runtime code or qualify future functionality; the execution checkpoint above records subsequent implementation.

Historical documentation-only planning validation on `8d21679bdc60f0f8808a156797bcedf086d11480`: `python3 -B -m unittest tests.test_task_statuses tests.test_title_sync tests.test_title_sync_integration tests.test_repository_gate -q` passed 71 existing tests. `python3 -B scripts/validate.py` passed; plan/design local link targets and whitespace were checked. The full suite and live provider/native delivery probes were not run for this documentation-only task. At that planning checkpoint, the new test modules named above were future deliverables. Current implementation results and remaining gaps belong to the execution evidence linked above.
