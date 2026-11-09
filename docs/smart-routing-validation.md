# Smart Routing cutover criteria

- **Status:** Pre-registration draft, written before any canary starts (plan Task S2.7, amendments A11 and A14). Edited on 9 November 2026, before any pilot run: added the [pilot canary](#pilot-canary-bounded-not-the-pre-registered-canary). The user chose a bounded pilot because the pre-registered canary cannot run yet. No existing number was changed. Edited again on 9 November 2026, still before any pilot run, after the independent review of the pilot harness: the pilot runs on a dedicated canary ledger, the refusal order is stated as implemented, the working-tree check and its residual risks are added, and the probes' calls are counted against the pilot cap. No number was changed. A third edit on 9 November 2026, still before any pilot run, corrects one stale fact: the remaining qualification budget is 9 of 14 spent, 5 remain (it read "9 remain"). It also states what happens when the guard probe fails for another reason, and adds the ancestor-config refusal. No threshold changed.
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

The pilot therefore does **not** satisfy the canary above, and cutover stays blocked. It exercises the enforced path on a dedicated canary routing state (see the deviations) within the narrow bounds below.

| Bound | Value |
|---|---|
| Project allowlist | Exactly `/Users/fcouto/dev/git/vibe-workflow`. Any other project or working directory is refused. |
| Routes | The subscription route `claude-agent/haiku#low` only. No API Usage, no organization, no OpenAI or Codex. |
| Sample | Up to 20 admissions, across at least three roles (implementer, reviewer, researcher, each through an explicit worker choice) and two complexity bands. |
| Requests | At most 1 real request per admission, plus one title request in the whole pilot. The durable call ledger caps the pilot at 24 real requests, separate from the qualification budget (9 of 14 spent, 5 remain). |
| Concurrency | The defaults (4 total, 2 per account), never raised. The driver runs one admission at a time. |
| Maximum duration | 24 hours of wall-clock time. |
| Abort conditions | Any funding violation, unresolved launch, recovery error, non-haiku request, `status` anomaly, or cap breach. |
| Kill switch | Smart Routing OFF (`set smart_routing=false`), run automatically on abort. Turning it back on is the user's explicit action. |

**Mechanism.** The harness ([`opencode_live.py --canary`](../tests/qualification/opencode_live.py)) runs as follows:

1. **Setup.**
   - It uses a dedicated canary routing state: settings and ledger pinned to an owner-only directory outside every repository and run directory (default `~/.local/state/vibe-workflow-qualification/canary/`), never the production defaults. `--project` is pinned to the allowlisted checkout, and no test inputs are used.
   - On first use the harness creates that state: `set authority_required=true` (one-way, on the canary ledger only), Smart Routing ON, API Usage off and no organization. Pilot admissions use their own account and pool ids (`anthropic:pilot-canary`, `pilot-sub`).
   - It starts an isolated OpenCode server (its own XDG directories).
   - It loads the routing plugin only through a project-local config that it creates and later removes.
2. **Refusals before any write to the project or any server start.** Only the canary state's own creation comes first. The harness refuses unless:
   - Smart Routing is ON, API Usage is off and no organization is enabled;
   - `authority_required` is on;
   - `status` is clean: no uncertain, `cost_unknown` or overrun holds, no blocked organization, nothing awaiting reconciliation, `unstamped_open_holds: 0`, no open attempt, and concurrency caps not raised;
   - the settings are readable and the ledger is ready, which is exactly when `routing.health` answers `ok`;
   - enough pilot budget remains.

   An unclean canary state is refused with reset advice: the operator deletes the canary directory after confirming no harness run is alive.
3. **Before any admission, once the server runs.** The `routing.health` RPC answers `ok`, and the plugin-chain probes pass; the guard probe must be refused by the guard with `guard_variant`. Otherwise the harness refuses, and removes the project config and the server. In particular, if the guard probe is refused for any reason other than the expected `guard_variant` text (its exact text was not observed live through this check), the pilot stops before any admission, and the two probe reservations settle at 0 because nothing was sent. The harness also refuses when the project or any ancestor directory up to `/` holds an `opencode.json`, `opencode.jsonc` or `.opencode/`, because OpenCode would load it.
4. **Each admission**, in order:
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

   The project working tree is checked at the start, after every 5 admissions and at the end, using HEAD, `git status --porcelain=v1 -z` and content hashes of every changed or untracked file. A change to a path that was not already dirty at the start aborts the pilot. Changes to already-dirty paths (other sessions edit this repository) and HEAD moves are reported. Any exception or signal during an admission also aborts it. The recovery depends on how far the admission got:
   - before dispatch: `not_dispatched`;
   - dispatched but not launched: left as is and reported, because no proof of non-launch exists;
   - a launch may have happened: `uncertain`.

   The kill switch then runs.

**Deviations, recorded in advance:**

- **Config location.** The project-local config lives in an excluded subdirectory (`.vibe-qual-canary/`), not at the project root. The user's running desktop OpenCode may have this project open, and a root config would load the recorder guard and the shim into it. Config discovery walks ancestors only, so a subdirectory config reaches only the isolated server.
- **Window spend cap not measurable.** The canary's subscription bound ("10% of each window") cannot be measured on this host, because window telemetry is not observable here. The durable call cap is the proxy.
- **Synthetic observation.** `prepare` receives one fixed, synthetic window observation (no collector exists).
- **Admission latency is single-caller.** The pilot is sequential, so the "admission transaction with four concurrent callers" budget is measured with one caller only; it is reported as such.
- **What the cap counts.** The call cap counts OpenCode `model.request` hooks, not upstream API calls. The two plugin-chain probes each reserve one call of exposure, which counts against the pilot cap until it is settled at the count actually sent (0 when they are refused, as designed).
- **Dedicated canary ledger instead of the production ledger.** Reason: subscription holds have no recovery path (`reconcile_uncertain` covers dollar holds only), so an aborted pilot admission would permanently occupy production concurrency places. An aborted pilot may leave a stuck uncertain hold in the canary ledger. That is accepted: the canary directory is reset by deleting it, and the production routing state is untouched.
- **Residual risks (not proven).**
  - **Tool-less sessions, read from code only.** The `claude-agent` provider in OpenCode 2.2.6 starts Claude Code with no built-in tools (`tools: []`), no settings sources, hooks disabled, a strict MCP config and `permissionMode: "dontAsk"`, allowing only OpenCode's own tools. This was read from the provider code in the installed binary, not observed at run time. OpenCode's own tools are denied by the session's permission rules (deny all), but they may still be offered to the model.
  - **The working-tree check** sees only what git reports. Ignored files, files outside the checkout, and a change that is reverted between two checks are not seen.
  - **Concurrent editing.** A concurrent session that creates a new file aborts the pilot (conservative). A change by the model to an already-dirty path cannot be told apart from a concurrent edit.
  - **Writes under HOME.** The claude CLI may still write its session state under HOME (`~/.claude/projects`, `~/.claude.json`), which is reported only.

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
