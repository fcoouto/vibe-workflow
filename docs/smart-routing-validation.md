# Smart Routing cutover criteria

- **Status:** Pre-registration draft, written before any canary starts (plan Task S2.7, amendments A11 and A14). Edited on 9 November 2026, before any pilot run: added the [pilot canary](#pilot-canary-bounded-not-the-pre-registered-canary). The user chose a bounded pilot because the pre-registered canary cannot run yet. No existing number was changed.
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

The 200-assignment minimum applies to the cutover replay over recorded historical assignments. The three committed fixtures in `tests/fixtures/smart-routing/arrivals/` are small synthetic regression fixtures (about 50 arrivals in total); they check the replay harness and do not meet this minimum.

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

## Pilot canary (bounded, not the pre-registered canary)

The user approved this pilot on 9 November 2026. It is registered here before it runs.

**Why a pilot.** The pre-registered canary (60 admissions over up to 7 days) cannot run yet, for two reasons:

1. The replay minimum is not met. Cutover replay needs 200 recorded historical assignments; the committed fixtures are about 50 synthetic arrivals.
2. No production launcher is wired (plan Task C.3).

The pilot therefore does **not** satisfy the canary above, and cutover stays blocked. It exercises the enforced path on the production routing state within the narrow bounds below.

| Bound | Value |
|---|---|
| Project allowlist | Exactly `/Users/fcouto/dev/git/vibe-workflow`. Any other project or working directory is refused. |
| Routes | The subscription route `claude-agent/haiku#low` only. No API Usage, no organization, no OpenAI or Codex. |
| Sample | Up to 20 admissions, across at least three roles (implementer, reviewer, researcher, each through an explicit worker choice) and two complexity bands. |
| Requests | At most 1 real request per admission, plus one title request in the whole pilot. The durable call ledger caps the pilot at 24 real requests, separate from the 9 qualification calls that remain. |
| Concurrency | The defaults (4 total, 2 per account), never raised. The driver runs one admission at a time. |
| Maximum duration | 24 hours of wall-clock time. |
| Abort conditions | Any funding violation, unresolved launch, recovery error, non-haiku request, `status` anomaly, or cap breach. |
| Kill switch | Smart Routing OFF (`set smart_routing=false`), run automatically on abort. Turning it back on is the user's explicit action. |

**Mechanism.** The harness ([`opencode_live.py --canary`](../tests/qualification/opencode_live.py)) runs as follows:

1. **Setup.**
   - It uses the production default routing settings and ledger, with `--project` pinned to the allowlisted checkout and no test inputs.
   - It starts an isolated OpenCode server (its own XDG directories).
   - It loads the routing plugin only through a project-local config that it creates and later removes.
2. **Refusals before any write.** The harness refuses to start unless all of the following hold:
   - `routing.health` is `ok`;
   - the plugin-chain probes pass;
   - Smart Routing is ON, API Usage is off and no organization is enabled;
   - `authority_required` is on;
   - `status` is clean: no uncertain, `cost_unknown` or overrun holds, no blocked organization, `unstamped_open_holds: 0` and no open attempt.
3. **Each admission**, in order:
   1. `prepare`;
   2. `health`;
   3. `dispatch`;
   4. `attach` with the token;
   5. one tiny prompt with the derived ids;
   6. wait until idle;
   7. re-read the session, every message page and the session lineage;
   8. compute usage with `usage_from_messages`, passing `children` and `lineage`;
   9. `record complete {usage}`;
   10. `detach`;
   11. check `status`.

**Deviations, recorded in advance:**

- **Config location.** The project-local config lives in an excluded subdirectory (`.vibe-qual-canary/`), not at the project root. The user's running desktop OpenCode may have this project open, and a root config would load the recorder guard and the shim into it. Config discovery walks ancestors only, so a subdirectory config reaches only the isolated server.
- **Window spend cap not measurable.** The canary's subscription bound ("10% of each window") cannot be measured on this host, because window telemetry is not observable here. The durable call cap is the proxy.
- **Synthetic observation.** `prepare` receives one fixed, synthetic window observation (no collector exists).
- **Admission latency is single-caller.** The pilot is sequential, so the "admission transaction with four concurrent callers" budget is measured with one caller only; it is reported as such.
- **What the cap counts.** The call cap counts OpenCode `model.request` hooks, not upstream API calls.

**Pass criteria for the pilot:**

- 20 admissions with zero abort conditions.
- Every settlement is a `complete` subscription settlement, with units recorded and no dollar charge.
- `status` is clean at the end: no `cost_unknown`, uncertain or blocked state, and `unstamped_open_holds: 0`.
- Latency p95 within the budgets above, measured on this host through a persistent `routing.py --serve` child: warm preparation 50 ms, cold preparation 500 ms, admission transaction 100 ms (single caller).
- Every blocked path blocked at 0 tokens.
- Zero disturbance of interactive sessions or of the user's own OpenCode directories.

Results go to `tests/fixtures/smart-routing/probes/pilot-results.json` and to Results below, separately from unit tests.

## Results

Not yet run. Record the replay report, the disagreement rate, any re-execution outcome, the latency measurements and the canary outcome here, separately from package validation and unit tests.
