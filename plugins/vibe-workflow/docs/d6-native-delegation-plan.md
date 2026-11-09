# D6: Native delegation and ownership

**Status:** the bounded standalone Mac CLI pilot passed on 5 November 2026.
Scope reviewed with Astra on 4 November 2026. This plan replaces the earlier
construction sequence and its stronger default qualification gates.

**Goal:** let a coordinator delegate a bounded change to a native worker, avoid
conflicting managed edits, and accept independently checked and reviewed work.

**Architecture:** reuse native dispatch, the existing assignment ledger, native
observations and check/review evidence. Python records admission and results;
the coordinator uses its existing native tools. Add no launcher, scheduler,
service, database migration or new environment requirement.

**Basis:** [Agent Delegation](../policies/03-agent-delegation.md),
[Context and Handoff](../policies/07-context-and-handoff.md) and the
[D6 deliverable](onboard-my-project-plan.md#5-small-deliverables).
Python 3.11+ and the existing standard-library runtime remain sufficient.

## Approved cooperative scope

Use the native client's ordinary trust boundary. Workers follow their briefs;
reservations coordinate participating writers. They are not filesystem isolation.
Keep the user's coordinator model, reasoning, provider, authentication and
permissions. Select a suitable native worker using available controls and cached
platform guidance under policy 03. Record unavailable settings honestly.

The useful workflow is:

1. Define the result, editable files, shared resources, dependencies and checks.
2. Reserve that scope and launch the native worker once.
3. Observe its actual identity, completed work and effects; reconcile the worker
   so dependent checks and review can proceed.
4. Run applicable checks and obtain independent review of the same candidate.
5. Reconcile remaining operations and accept the checked, reviewed result.

Ordinary native delegation already exists and does not require managed runtime
setup. D6 adds coordinated reservations and verified handoff where supported.
Do not claim ordinary delegation alone qualifies the managed D6 implementation.
A user-selected protection remains required for the operation it protects.

### Normal completion and uncertain ownership

Worker lifecycle reconciliation requires the exact assignment, child and
successful native turn; accounted-for foreground work with no observed unfinished
activity; and independent inspection of changes. It releases the completed
worker's claims so dependent checks and review can run. Final result acceptance
separately requires the applicable passing checks, independent review of the
current candidate and reconciliation of the remaining operations.
The coordinator must have no admitted or issued follow-up outstanding and must
pause further submissions through reconciliation. A later turn needs new
admission and conflict checks. Recheck the assignment, candidate, pins and
competing claims before release; changed evidence requires reassessment.

Trust the native host's ordinary successful-completion behavior. Do not require
continuous independent access to hidden queues, retry internals or every process
that could write. Missing that extra access is not itself an uncertain outcome.
A worker's report or terminal status alone is still insufficient: the activity,
result and effects must agree. This establishes cooperative completion, not
universal stopped-writer proof.

Interrupted, timed-out, failed or ambiguously launched work retains ownership,
capacity and consumed budget. So do missing required activity/results, known
unfinished commands, pending follow-ups, contradictory observations, unexpected
background work or unexplained effects. Dependent work waits; independent work
may continue. No automatic takeover, replacement or recovery is part of this
slice. A deadline, cancellation acknowledgement or new run ID cannot release an
uncertain owner. Uncontrolled external writers remain a stated limitation.

### Keep only controls with a concrete purpose

| Keep in core D6 | Practical purpose |
| --- | --- |
| Bounded brief, suitable worker and finite attempt/time limits | Keep delegated work relevant and affordable without changing user settings. |
| Shared reservations, dependency and capacity checks | Prevent participating workers and checks from conflicting over files or services. |
| Stable dispatch identity and one launch permit | Prevent duplicate writers after replay or a lost response. |
| Exact native identity and current candidate | Prevent accepting another worker's result or stale work. |
| Applicable checks and independent review | Establish that the change works and meets the request. |
| Retained uncertain ownership | Prevent overwriting work that may still be active. |

Independent host-internal access, forced marker waits, exact-patch canaries and
child-hook witness choreography are not core D6 completion requirements.
Enforced patch-only tools, per-worker containment, descendant shutdown proof and
automatic takeover are deferred guarantees. The completed pilot used the native
Mac CLI. Docker, Lima and other VM routes are outside its scope.

## Existing implementation and boundaries

The ledger, guidance cache, native identity reader, duplicate/conflict checks and
D5 review integration already exist. Do not repeat their original implementation
or schema-2 migration plan; current runtime configuration and SQLite use schema 3.
Keep historical databases, pins, claims and evidence with their original readers.

| Current source | Supported behavior and limits |
| --- | --- |
| `runtime/delegation.py`: `_prepare_native`, `_admission` | Existing reservations and once-only dispatch work offline. Managed admission still requires the selected setup/hook capabilities. A simpler plan does not bypass them. |
| `runtime/adapters/codex.py`: `normalize_runtime`, `_cooperative_activity` | Binds patch hooks to the concrete native child and reads direct or wrapped synchronous patches, optional prepared waits and tool-free reviews. The selected Mac canary used its child hook receipts without a foreground wait. |
| `runtime/delegation.py`: `_validate_role_proof`, `_confirm_cooperative_completion` | Version-3 proof binds observed activity and coordinator-owned follow-ups. Version-2 cooperative and strict proofs retain their original meanings. |
| `runtime/evidence.py`: `_check_admission`, `prepare_review` | Matching registered check jobs honor D6 dependencies. A reviewer can inspect the exact candidate from a reconciled canary worker without reusing its pre-edit witness. Started checks retain strict reconciliation. |
| `runtime/evidence.py`: `_verify_member` | Cooperative evidence supports local qualification completion only. Ordinary D5 readiness, D4 recovery and D7 merge evidence still require stronger proof. |

The [pilot record](superpowers/plans/2026-11-04-d6-successor-release-packet.md)
records the actual native result and its limits. Keep selected protections and
original evidence bindings when qualifying another route.

## Implementation and offline acceptance

Use task-by-task implementation and independent review. Keep related runtime,
consumer, test and instruction changes in the same reviewed slice. Workers do
not commit or publish implicitly; use current main when integrating completed work.

### 1. Align normal completion — implemented

**Files:** `runtime/delegation.py`, `tests/test_delegation*.py`,
`tests/test_evidence.py` and the native delegation instructions.

- [x] Observed failing worker and reviewer cases before accepting the new proof.
- [x] Added proof version 3 with `no_outstanding_coordinator_followups:true`
  and `coordinator_submissions_paused:true`. It preserves activity/effect,
  identity, fresh candidate and conflict checks without hidden-host assertions.
  Version 2 is still read under its original semantics; mixed fields reject.
- [x] Verified normal handoff, replay, wrong identity, stale activity/candidate,
  pending work, interruption, missing results and strict-consumer rejection.
  Uncertainty retains claims and consumed budget. Astra cleared this slice.

### 2. Make the complete cooperative flow usable

**Files:** `runtime/delegation.py`, `runtime/evidence.py`,
`tests/test_delegation*.py`, `tests/test_evidence.py` and the check/delegation
instructions. The Codex adapter binds patch events to the child and the existing
reservation. The state schema is unchanged.

- [x] Reproduced and fixed a planned check starting before its worker dependency.
  Matching D6 check jobs now use the existing dependency validator before
  reservation or execution; unrelated D5 checks keep their existing rules.
- [x] Reproduced and fixed post-edit reviewer admission demanding the original
  canary preimage. A read-only reviewer can now bind the exact current candidate
  from a same-task cooperative worker that assessed the canary change. The
  worker keeps its original witness; the reviewer's new record cannot prove a
  pre-edit guard observation. Unexplained edits still reject.
- [x] Exercised the whole selected guarded-canary flow offline: reservation,
  actual guard decision on fixture events, synthetic native observation, edit,
  worker reconciliation, a real local registered check, independent-review
  fixtures, and candidate-bound qualification completion. The external check
  lifecycle proof is explicitly synthetic. Strict readiness and merge claims
  remain unavailable.

The registered canary check used the pinned Mac shell, builtins and the exact
file bytes. Independent inspection established that this command launches no
child work. The executor recorded exit zero on the unchanged candidate. Use
each arbitrary project runner's own check and writer evidence.

### 3. Verify the slice and complete one bounded pilot

- [x] The tested runtime revision `764c39b7` passed **86 affected offline tests**,
  package validation and `git diff --check`. The earlier 66-case implementation
  run is historical evidence. Package validation reports client execution separately.
- [x] The user released the bounded Mac pilot. A reserved native worker made one
  guarded edit, the registered whole-file check passed, and a tool-free Astra
  reviewer accepted the same candidate. Runtime verified qualification and
  retained earlier failed claims under their original owners. See the
  [completed pilot packet](superpowers/plans/2026-11-04-d6-successor-release-packet.md).

**D6 qualification completion:** one supported native route can reserve, delegate, check, review
and reconcile a bounded useful change, with no duplicate/conflicting managed
writer and no release of uncertain ownership. Report offline implementation and
actual native observations separately. Do not require every client surface or
stronger enforcement capability to finish this bounded D6 slice.

## Separate work, preserved requirements

- **Managed setup and child guards:** the
  [held managed-qualification record](superpowers/plans/2026-11-03-d6-managed-lifecycle-pilot.md)
  retains D4 bootstrap, the exact patch/marker sequence and original limits.
  Use it only when that stronger route is selected; it is not the core checklist.
  Existing protected routes still need their actual supported admission.
- **Strict recovery, readiness and merge:** D4, D5 and D7 own their guarantees.
  Preserve historical proof meaning and selected protections. Cooperative
  acceptance does not grant READY, publication, merge or old-claim cleanup.
- **Other surfaces, visibility and external routes:** qualify Desktop/Claude,
  optional Habblaud display and external workers separately. Preserve the shared
  [role declaration contract](../clients/worker-visibility.md); display does not
  gate execution. D6 creates no non-native fallback or account change.
- **Linked jobs and retention:** retain the approved
  [cross-repository follow-up](onboard-my-project-plan.md#cross-repository-tasks-and-delivery)
  and [retention work](onboard-my-project-plan.md#evidence-retention). They are
  separate slices, not prerequisites for this single-repository pilot. Existing
  shared-resource, alias and uncertain-owner protections continue to apply.
- **Broader enforcement and measurement:** D7 owns repository assurance; D9 owns
  broader behavioral qualification and measured usage. No duplicate D6 campaign
  or usage ledger is needed. Follow policy 03 for guidance refresh and worker
  selection instead of restating its procedure here.
