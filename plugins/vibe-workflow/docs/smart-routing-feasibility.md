# Smart Routing — baseline and feasibility (Task 0.1)

Plan: [Smart Routing implementation plan](superpowers/plans/2026-11-08-smart-routing.md) v1.7. Design: [Smart Routing design](superpowers/specs/2026-11-08-usage-routing-opencode-subscriptions-design.md) v1.5. Recorded 2026-11-08.

## Implementation baseline

- **Revision:** vibe-workflow `main` at `a4e0f1b9ec3b67a623ce868f3eee756714f2f575`.
- **Relation to the inspected baseline:** the design inspected `d73252a77d1e784f8a7d6f637bc661467485178d`. That commit is an ancestor of the chosen baseline, so every inspected file is present.
- **Reconciliation:** the plan said the authoring checkout lacked `runtime/usage_*.py`, `runtime/external_delegation.py` and `skills/manage-usage-routing/`. On this baseline all three exist. Work starts from `main` in dedicated worktrees (one branch per slice), so the earlier `codex/project-repository-adjustments` changes are neither required nor carried over. Newer `main` changes win over any older branch.
- **Not on this baseline:** `skills/llm-council/` and `skills/review-my-project/` (design "Checkout" features F08 and F09). `skills/review-my-project/` landed on `main` in PR #110, after this baseline, so F09 is no longer blocked. `skills/llm-council/` is still absent from `main` and lives only on the backup branch `codex/project-repository-adjustments`, so F08 stays blocked until it is ported.

## Feature owners on this baseline (F01–F17)

| ID | Feature | Baseline owner files | Slice |
|---|---|---|---|
| F01 | Prepare my Workspace | `skills/prepare-my-workspace/` | C.2a |
| F02 | Onboard my Project | `skills/onboard-my-project/`, `runtime/onboarding.py`, `runtime/startup.py` | S2.1, C.2a |
| F03 | Workstreams | `skills/workstreams/`, `runtime/workstreams.py`, `runtime/delegation.py`, `clients/workstreams*.md` | S2.2, S3, C.2b |
| F04 | Manage API Usage | `skills/manage-usage-routing/`, `runtime/usage_*.py` | S1, C.2c |
| F05 | Manage OpenRouter Integration | `skills/manage-openrouter/`, `runtime/openrouter_settings.py`, `scripts/openrouter.py` | C.2c |
| F06 | OpenRouter proposals | `skills/openrouter-proposals/`, `scripts/openrouter_proposals.py` | C.2c |
| F07 | OpenRouter workers | `skills/openrouter-workers/`, `scripts/openrouter_workers.py` | C.2c |
| F08 | LLM Council | **Absent on `main`** (checkout feature) | C.2d, blocked |
| F09 | Review my Project | `skills/review-my-project/` (landed on `main` in PR #110, after this baseline) | C.2d |
| F10 | Manage Observability | `skills/manage-observability/`, `runtime/observability*.py`, `runtime/langfuse.py` | S3.6, C.2e |
| F11 | Planning, native delegation, continuation | `policies/02-task-planning.md`, `policies/03-agent-delegation.md`, `runtime/delegation.py`, `runtime/external_delegation.py`, `clients/delegation*.md` | S2.2, C.2b |
| F12 | Resource admission, capacity recovery | `runtime/resource_admission.py`, `runtime/resource_execution.py`, `runtime/parent_recovery.py`, `clients/workstreams-resources.md`, `clients/workstreams-recovery.md` | S3.5, C.2b |
| F13 | Tests, security/code review, evidence | `policies/04`–`06`, `runtime/evidence.py`, `runtime/review_storage.py`, `clients/evidence*.md` | S3.6, C.2e |
| F14 | Worktrees, delivery, merge assurance | `clients/delivery.md`, `runtime/delivery.py`, `scripts/delivery_coordinator.py`, `scripts/repository_gate.py` | S3.6, C.2e |
| F15 | Thread naming, visibility, Habblaud | `clients/workstreams-naming.md`, `clients/worker-visibility.md`, `runtime/title_sync.py`, `scripts/worker_visibility.py` | S2.3, C.2a |
| F16 | Context/handoff, structural evidence | `policies/07-context-and-handoff.md`, Workstreams context rules | C.2b |
| F17 | Startup, maintenance, diagnostics | `clients/startup.md`, `runtime/startup.py`, `runtime/doctor.py`, `policies/09-workflow-maintenance.md`, `clients/guidance-publication.md` | S2.3, C.2a |

All paths other than F08 and F09 were confirmed present on the baseline revision.

## Test toolchain

- Standard-library `unittest`, run as `python3 -B -m unittest <module>` or `python3 -B -m unittest discover -s tests`. Python 3.11+ (3.15.2 observed). No package manager, linter or CI.
- Package check: `python3 -B scripts/validate.py` (does not check client execution).
- The full suite takes more than 20 minutes locally; slice work runs the relevant modules and the package check.
- At the baseline no `tests/fixtures/` directory exists; `main` has since gained `tests/fixtures/project_review/`. `tests/fixtures/smart-routing/` is created by Task 0.3.
- OpenCode V2 shim loader and test runner are not yet pinned (P0.1, Task 0.4). Nothing on this baseline runs TypeScript.

## Baseline results

- `python3 -B -m unittest tests.test_usage_budget tests.test_usage_routing`: 35 tests, OK.
- `python3 -B scripts/validate.py`: passed (package only).
- Full-suite result on this revision: not recorded here.

## Gate status (P0)

Every proof below is `unverified`; none is `qualified`.

| Gate | Status |
|---|---|
| P0.0 Billing/terms | unverified (Task 0.3 needs current vendor terms and observed billing) |
| P0.1 Source/runtime | unverified (baseline and owners are recorded here; shim loader, runner and latency are not) |
| P0.2–P0.8 | unverified |

S1 can merge without P0.0, P0.3 and P0.4; live funding stays disabled until they pass. S2 and later slices need their gates before they merge.
