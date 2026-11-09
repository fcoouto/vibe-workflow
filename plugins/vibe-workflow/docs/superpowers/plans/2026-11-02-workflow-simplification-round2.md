# Workflow simplification, Round 2

The user authorized another pass against unnecessary complexity. The planning
diagnosis remains parked, upgrade compatibility remains a separate investigation,
and Genesis owns its backbone, provenance and retention changes.

## Scope

Use the existing checkout on `codex/workflow-simplification-round2`, based on
merged Round 1 (`a0f1767`). Keep this pass within ordinary delegation:

1. Remove direct-dependency loops from `_admission` and `_coordinator_job` only
   where the existing `validate_dependencies` call has already established the
   same facts, including transitive freshness and latest settled results.
2. Share one task-scoped ledger observation inside each reconciliation transaction.
   Keep the second transaction's fresh observation after external proof and
   candidate capture, and retain every current owner plus the task's own history.

Preserve request schemas, immutable records, replay identity, claims, stop proof,
budgets, failure behavior and historical readers. No new cache, service, approval
step, configuration or lifecycle mechanism is needed. Do not modify other chats'
checkouts, live runtime state or installed plugin caches. A later merge requires
its own integration decision; Round 1's completed merge does not publish Round 2.

## Candidate rejected

Worker briefs repeat resources and dependencies from their accepted job. Removing
those input fields while preserving complete worker prompts and immutable replay
would introduce normalization rules. That tradeoff is not justified by this
bounded pass, so the public contract stays intact.

## Proof and progress

- [x] Establish baseline for worker/check/coordinator dependencies, transitive
  reopening, reconciliation replay and intervening ownership changes.
- [x] Reproduce duplicate ledger observations; add a focused regression that
  preserves both transaction boundaries.
- [x] Make the two internal simplifications after Genesis releases its temporary
  delegation-module coordination hold.
- [x] Run affected delegation, workstream and resource checks; validate the package
  with `python3 -B scripts/validate.py`; inspect the diff and obtain one independent
  final review. Record actual results and remaining execution limits here.

Package fixtures do not establish native execution or Genesis retention behavior.

## Results

- Genesis released the delegation module after its `e8ff81b` checkpoint. This
  branch changes only `_admission`, `_coordinator_job` and `reconcile_assignment`
  in runtime code, plus one regression test and this plan.
- Ten existing baseline tests passed. The new regression failed with four ledger
  reads before the refactor, then all eleven focused tests passed with one read
  inside each transaction, on opposite sides of candidate capture.
- Final affected suites passed: `tests.test_delegation` (128 tests), plus
  `tests.test_workstreams`, `tests.test_delegation_scale`,
  `tests.test_current_ledger` and
  `tests.test_resource_execution.ResourceDependencyQueueTests` (38 tests).
  The scale fixture includes 51 chained admissions, retry and review settlement.
- `python3 -B scripts/validate.py` passed. `git diff --check` passed.
- Independent native Astra/high review found no blockers. It confirmed that
  dependency validation subsumes both deleted loops, and that reconciliation
  retains current foreign owners, task history, replay checks and a fresh second
  transaction. Runtime code shrinks by nine lines; no new mechanism is introduced.
- Review noted that a retired assignment with corrupt ledger data can now report
  the ledger error first. Both paths reject before external capture or mutation;
  no recovery or authorization behavior is relaxed.

No public contract or instruction path changed. Upgrade compatibility and the
planning/onboarding diagnosis remain separate. The complete repository suite,
live client execution, installed plugin activation and Genesis retention were
not tested in this round. Nothing was installed, published or merged.
