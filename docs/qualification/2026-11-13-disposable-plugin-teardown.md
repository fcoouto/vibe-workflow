# Disposable form-gating teardown investigation

## Scope and plan

The user requested diagnosis and the smallest offline-verified controller repair.
This is plugin teardown, not Git cleanup or shipped gate integration. Original
candidate `864b10dd4a3fa403d7f830717d329471d6949bac` and PR #289 remain unchanged.
PR #289 must remain draft/BLOCKED. No live allocation is available.

Task destination: branch `fix/disposable-plugin-teardown`, created from local
`main` at `0ce70fbeea70f98999e69d5394a8414f17fed927` in a fresh native worktree.
Primary remains on `main`; its four pre-existing untracked client files are not
task work. Original qualification worktree is clean at the published candidate.
Policies: current repository scope/planning/handoff/development, integration,
test, security, review and publication contracts. Setup authority remains the
primary checkout's `docs/setup.md`. No feature setting change is needed.

Design: keep the original controller and evidence immutable. Put a revised
controller in a new private task directory. Extract only its unload observation
and final-report handling into `tests/qualification/form_gate_cleanup.py`, a
development-only helper with no command-line entry point. Keep the original
form cancellation and exact configuration-ownership check in the controller.
Use fake time and transport for regression tests; never execute native cases.
Do not add a general cleanup framework, dependency, service or settings control.

- [x] Inspect original sources, receipts, digests and installed lifecycle code.
- [ ] Reproduce early observation failure and lost reporting with offline tests.
- [ ] Pace observations inside the same 30-second/10-operation limits, reserve
      final RPC/process checks, and retain unknown/error outcomes as failures.
- [ ] Wire the revised private controller; test deadlines, timeouts, budgets,
      configuration failures and simultaneous case/cleanup failures.
- [ ] Validate, commit, run affected committed tests, review and publish draft
      BLOCKED findings. Independent review and live qualification stay explicit.

Acceptance: delayed and immediate unload can pass only with timely positive
observations; never-unload, API faults, exhausted time/count and missing evidence
cannot pass. Case and cleanup results survive independently. No mutation replay,
deadline extension, budget reset or manual recovery is part of success.

## Investigation

Findings, verification and remaining uncertainties are recorded below as work
advances. Original private receipts are not revised by this report.
