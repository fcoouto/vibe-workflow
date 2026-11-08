# Smart Routing cutover criteria

- **Status:** Pre-registration draft, written before any canary starts (plan Task S2.7, amendments A11 and A14).
- **Numbers:** Every number below is a **proposed value, not a measured result**. Change a number only by editing this file before the run it governs; record the edit and its reason. Results never change a threshold after the fact.
- **Related:** [design](superpowers/specs/2026-11-08-usage-routing-opencode-subscriptions-design.md) sections 10.4 and 13, and the [plan](superpowers/plans/2026-11-08-smart-routing.md).

Cutover (plan Task C) replaces the current selectors only when the replay criteria and the canary criteria both pass. Otherwise the current selectors keep dispatching. There is no shadow or simulation mode: a route is active or it is not. Replay runs ordinary tests over isolated ledgers; the canary is real, enforced routing inside the bounds below.

## Allocator replay

Replay feeds recorded arrivals, in order, through the pure allocation functions and an isolated temporary ledger (`tests/test_routing_replay.py`). It never touches the live ledger, network or a model.

| Criterion | Proposed value |
|---|---|
| Minimum recorded assignments replayed | 200, covering at least three roles and two complexity bands |
| Funding violations (protected allowance breached, window over-committed, concurrency cap exceeded, claim admitted beyond headroom) | 0 |
| Resurrected capacity (stale or older observation, or a reset, raising headroom or clearing a hold) | 0 |
| Class and route decisions reproduced from admission records alone | 100% |
| Duplicate or rejected arrivals that leave an admission record | 0 |

Any single failure stops the cutover until the cause is fixed and the full replay passes again.

## Disagreement and quality re-execution

A **disagreement** is a recorded historical assignment whose accepted model and effort, or whose funding class, differs from the strategy the new ranker and allocator choose for it. Replay reports the rate and which assignments disagree.

| Criterion | Proposed value |
|---|---|
| Disagreement rate that triggers quality re-execution | above 25% of replayed assignments, or any disagreement in the highest risk band |
| Re-execution sample | up to 30 flagged assignments, picked in a fixed order recorded before the run, within a total budget of 5% of one funding pool's canary spend cap |
| Pass condition | accepted rate under the new strategy is no more than 10 percentage points below the recorded rate, judged by the original acceptance checks |

If the trigger is not met, no re-execution is required. If re-execution fails its pass condition, cutover stops and the report lists the failing assignments.

## Latency budgets

Measured on the canary host with local state only; the numbers cover routing work, not the worker's own run.

| Operation | Proposed budget (p95) |
|---|---|
| Warm preparation: profile and ranking cache hit, class and route choice, reservation | 50 ms |
| Cold preparation: no cache, table lookup, floors for gaps, reservation | 500 ms |
| Admission transaction with four concurrent callers | 100 ms |
| Optional Jev call (only where configured) | 5 s timeout, then conservative floors |

A promoted model ranker is out of scope here and has its own S4 evaluation. Report medians as well as p95. A missed budget is reported with its cause; it blocks cutover until fixed or until the budget is edited here before a new run.

## Canary

The canary enables enforced routing for one explicitly chosen project on OpenCode (plan Task S3.7). It starts only after replay passes and these bounds are recorded.

| Bound | Proposed value |
|---|---|
| Project allowlist | Exactly one named project, written here before the start. Other projects and clients stay on the current selectors. |
| Minimum sample | 60 admitted assignments, covering at least three roles, two complexity bands and each funding class that is enabled |
| Spend cap per funding pool | API grant: 5% of the issued allowance. Each subscription pool: 10% of each of its windows. |
| Maximum duration | 7 days, or the minimum sample plus 20 extra admissions, whichever comes first |
| Concurrency caps | The defaults (4 total, 2 per provider account); not raised during the canary |
| Abort condition | Any funding violation, any unresolved launch, any recovery error, or any bound above exceeded |
| Kill switch | Set Smart Routing OFF. It stops new automatic admissions without a model call; running workers finish within their budgets and keep their claims. |

The canary passes when the minimum sample is reached with no abort condition, replay still reproduces the canary's admission records exactly, and the latency budgets hold. Record admissions, blocks, latency and settlement against this file.

## Results

Not yet run. Record the replay report, the disagreement rate, any re-execution outcome, the latency measurements and the canary outcome here, separately from package validation and unit tests.
