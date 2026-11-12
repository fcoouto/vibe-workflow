# Smart Routing cutover criteria

- **Status:** Pre-registration draft, written before any canary starts (plan Task S2.7, amendments A11 and A14). Edited on 9 November 2026, before any pilot run: added the [pilot canary](#pilot-canary-bounded-not-the-pre-registered-canary). The user chose a bounded pilot because the pre-registered canary cannot run yet. No existing number was changed. Edited again on 9 November 2026, still before any pilot run, after the independent review of the pilot harness: the pilot runs on a dedicated canary ledger, the refusal order is stated as implemented, the working-tree check and its residual risks are added, and the probes' calls are counted against the pilot cap. No number was changed. A third edit on 9 November 2026, still before any pilot run, corrects one stale fact: the remaining qualification budget is 9 of 14 spent, 5 remain (it read "9 remain"). It also states what happens when the guard probe fails for another reason, and adds the ancestor-config refusal. No threshold changed. Edit 4: pilot cap 24 -> 28 before attempt 2. Reason: attempt 1 aborted at admission 1 on a harness defect (TypeError in the accounting wrapper) after 2 real requests; the cap is raised by 4 so a second attempt can still run the pre-registered 20 admissions; no other number changed; both attempts are reported. The user is told of this edit; the durable call ledger carries it as an appended cap line. Edit 5, before attempt 2 and changing no number: the settled-accounting re-read rule and titled sessions for admissions 2 to 20 (see Mechanism). [Edit 6](#edit-6-10-november-2026-replay-against-the-available-history), on 10 November 2026 before any replay has run: the 200-assignment replay minimum becomes every eligible past session, labelled by fixed metadata rules, and its disagreement rate becomes advisory only; the strict replay runs only on real admission records, over the exported pilot records before the canary (where it must pass) and again at C.1, and quality re-execution is deferred to C.1. The replaced text is kept and marked superseded. No latency budget, canary bound or pilot number changed. [Edit 7](#edit-7-11-november-2026-redesigned-replay-ranking-and-canary), registered on 11 November 2026 before any replay, ranked canary or label under it: specialist roles, the strict replay of ranked and worker-choice records, historical replay labels, value order, subscription-only selection, benchmark tolerance, the promotion test and the canary's cap interpretation. Earlier text, including Edit 6, is kept; dated pointers mark the sections Edit 7 amends, and its points govern the redesigned work where they differ. [Edit 8](#edit-8-11-november-2026-no-worker-count-admission), registered on 11 November 2026: no count of open workers admits or refuses; saved caps and declared D6 worker counts are history, and the pilot's one-at-a-time driver is a qualification protocol, not admission policy.
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

> **Superseded on 10 November 2026 by [Edit 6](#edit-6-10-november-2026-replay-against-the-available-history).** The minimum of 200 recorded assignments, in the first row and in the paragraph after the table, no longer applies. The other four rows keep their values and now apply only to the strict replay over real admission records.

> **Amended on 11 November 2026 by [Edit 7](#edit-7-11-november-2026-redesigned-replay-ranking-and-canary) points 1 to 3.** "Roles" means specialists (point 1). What the strict replay recomputes for ranked records is in point 2, and how worker-choice records are checked is in point 3. The four rows above keep their values.

> **Amended on 11 November 2026 by [Edit 8](#edit-8-11-november-2026-no-worker-count-admission) point 3.** "Concurrency cap exceeded" is no longer a funding violation; the other parts of that row and the other rows are unchanged.

## Disagreement and quality re-execution

A **disagreement** is a recorded historical assignment whose accepted model and effort, or whose funding class, differs from the strategy the new ranker and allocator choose for it. Replay reports the rate and which assignments disagree.

| Criterion | Proposed value |
|---|---|
| Disagreement rate that triggers quality re-execution | above 25% of replayed assignments, or any disagreement in the highest risk band |
| Re-execution sample | up to 30 flagged assignments, picked in a fixed order recorded before the run, within a total budget of 5% of one funding pool's canary spend cap |
| Pass condition | accepted rate under the new strategy is no more than 10 percentage points below the recorded rate, judged by the original acceptance checks |

If the trigger is not met, no re-execution is required. If re-execution fails its pass condition, cutover stops and the report lists the failing assignments.

> **Superseded on 10 November 2026 by [Edit 6](#edit-6-10-november-2026-replay-against-the-available-history).** The disagreement rate is advisory only and triggers nothing. Quality re-execution is deferred to C.1 and is not in force until its trigger, sample and comparison are registered again.

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

> **Superseded on 10 November 2026 by [Edit 6](#edit-6-10-november-2026-replay-against-the-available-history).** "Only after replay passes" now means the strict replay over the exported pilot records has passed and the historical replay has run and its advisory report is recorded under Results. The check below that replay "still reproduces the canary's admission records exactly" is the strict replay at C.1.

> **Amended on 11 November 2026 by [Edit 7](#edit-7-11-november-2026-redesigned-replay-ranking-and-canary) points 1, 3 and 9.** "Roles" in the sample means specialists (point 1). The canary accepts only `mode: ranked` (point 3). Point 9 sets how it runs on generated routes with the admitted identity and pool, and how the subscription spend cap in the table is counted, reset and enforced.

| Bound | Proposed value |
|---|---|
| Project allowlist | Exactly one named project, written here before the start. Other projects and clients stay on the current selectors. |
| Minimum sample | 60 admitted assignments, covering at least three roles, two complexity bands and each funding class that is enabled |
| Spend cap per funding pool | API grant: 5% of the issued allowance. Each subscription pool: 10% of each of its windows. |
| Maximum duration | 7 days, or the minimum sample plus 20 extra admissions, whichever comes first |
| Concurrency caps | The defaults (4 total, 2 per provider account); not raised during the canary |
| Abort condition | Any funding violation, any unresolved launch, any recovery error, or any bound above exceeded |
| Kill switch | Set Smart Routing OFF. It stops new automatic admissions without a model call; running workers finish within their budgets and keep their claims. |

> **Amended on 11 November 2026 by [Edit 8](#edit-8-11-november-2026-no-worker-count-admission) points 1 and 4.** The "Concurrency caps" bound is retired: no count of open attempts limits admission. Real quotas, money, host resources, ownership and task authority still bind.

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
| Requests | At most 1 real request per admission, plus one title request in the whole pilot. The durable call ledger caps the pilot at 28 real requests (24 before Edit 4), including the probes' exposure, separate from the qualification budget (9 of 14 spent, 5 remain). |
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

   **Settled accounting (Edit 5).** Before `record complete`, step 8 re-reads when the result may still be catching up: an `unknown` reason of `message_in_progress`, `negative_remainder` or `title_unknown`, or a title sent but not counted. It re-reads at most 5 times, 1 s apart, and accepts the first read where:
   - that condition has cleared;
   - the set of message ids is unchanged;
   - no token class of the session total has decreased.

   A decrease or a changed message set aborts (`recovery_error`, `accounting_regressed`). Accounting refusals are never retried. A result still unsettled after the limit is recorded `complete` with the deviation `title_unaccounted`. Only the first admission's session is created without a title, so it observes the one real title request; sessions 2 to 20 are titled at creation, so OpenCode sends no title request for them, and the guard still blocks any unexpected one at 0 tokens.

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

## Edit 6 (10 November 2026): replay against the available history

Registered on 10 November 2026, before any historical or cutover replay has run, on the user's instruction of that day: "No problem, reduce the value to the amount of past assignments we have." It replaces the text marked superseded in [Allocator replay](#allocator-replay), [Disagreement and quality re-execution](#disagreement-and-quality-re-execution) and [Canary](#canary); that text stays as written. No latency budget, canary bound or pilot number changed.

**Why.** The 200-assignment minimum assumed that recorded assignments carry what the strict checks need. Past worker sessions record the model, the variant or effort, timestamps and token counts, but not the task family, the complexity or risk band, the funding state at arrival, or acceptance. Only admission records carry those. Today the only ones are the pilot's 20, made with a fixed synthetic window observation. The 15 runs in the usage ledger are legacy S1 calls, not routing admissions, and are excluded. So the replay is split in two.

**Design rule (the user's, 10 November 2026).** The replay is lightweight: one read-only pass per store, metadata columns only, no model calls, and results computed locally.

| | Historical replay | Strict replay |
|---|---|---|
| Input | Every eligible past session ([sample](#sample)) | Real admission records only: the pilot's 20, then the canary's |
| Labels | Inferred by the [labelling rules](#labelling-rules) | Read from the admission records |
| Result | An advisory report; never a pass or a fail | Pass or fail on the four unchanged rows of [Allocator replay](#allocator-replay): 0 funding violations, 0 resurrected capacity, 100% of class and route decisions reproduced, 0 duplicate or rejected arrivals leaving an admission record |
| When | Before the canary | Before the canary over the exported pilot records, where it must pass; again at C.1 over the pilot's and the canary's records |

A single strict failure still stops cutover, and before the canary it also stops the canary, until the cause is fixed and the full strict replay passes again.

### Sample

The historical replay uses **every eligible past session**, whatever their number. A read-only count on 10 November 2026 found about 1,349 past worker sessions: 258 OpenCode child sessions in this repository, 1,014 Codex child threads in the old `vibe-coding-workflow` checkouts and 77 in the `opus` store. These counts are context, not a target. The report states the number actually found and how many each exclusion removed.

A session is **eligible** when it is a child worker or reviewer session: it has a parent session or thread, a model id, a start timestamp and a last-update timestamp. Excluded, and counted by reason:

- sessions with no parent (coordinator and interactive sessions);
- sessions without a model id or without either timestamp;
- sessions updated within the hour before the replay starts, which may still be running;
- title, compaction and summary sessions that the host marks as such, because they are not assignments;
- qualification and pilot sessions, which ran in isolated stores that the replay does not scan;
- a session id seen again in another checkout or worktree, which counts once: the copy with the earliest start time is kept (ties: store name, then path, in byte order; the field-mapping dated edit below names each store).

The replay checks the qualification and pilot exclusion by id: it refuses to report while any eligible session's id matches a session id recorded by a qualification or pilot run (the run fixtures and the exported pilot ledger), rather than relying only on those stores not being scanned. If those session ids cannot be confirmed, the same rule as for store fields applies: a dated edit here says how the exclusion is checked, or the replay does not run.

The strict replay's sample is the pilot's 20 admissions plus every canary admission (at least 60 by the canary bound). Together they must cover at least three roles and two complexity bands, as before.

### Labelling rules

These rules apply to the historical replay only. Before they were written, the only look at the stores was the read-only count of 10 November 2026: the session counts per store above, and which metadata fields exist (model, variant or effort, timestamps and token counts, but no task family, band, funding state at arrival or acceptance). The author of these rules saw no grouped model or effort values and no title keyword counts. No label and no replay result existed when they were fixed. They use metadata only: agent type, title, provider, model, variant or effort, timestamps and token counts. A prompt, message, tool call or output is never read. Any label may be `unknown`; the report counts the unknowns of every field.

> **Amended on 11 November 2026 by [Edit 7](#edit-7-11-november-2026-redesigned-replay-ranking-and-canary) points 1 and 4.** "Role" means specialist (point 1). Point 4 maps rule 1's outcomes to specialists and makes rule 2 the work kind of that specialist. The rules below stay as written.

1. **Role.** The first rule that matches:
   1. The agent type, split into words on every non-alphanumeric character and matched regardless of case: a word `reviewer` or `review` gives `reviewer`; `explore`, `explorer` or `researcher` gives `researcher`; `repair` gives `repair`. When an agent type holds words from two groups (such as `review-explorer`), the first group in the stated order wins, as in rule 1.2. An agent type with none of these words (such as `general`, `default` or `worker`) goes to the next rule.
   2. Title keywords: the title is split into words on every non-alphanumeric character, and the words are matched regardless of case, in this order of groups (the first group with a match wins): `review`, `reviewer`, `audit`, `verify` or `verification` gives `reviewer`; `research`, `investigate`, `investigation`, `explore`, `survey`, `scout` or `diagnose` gives `researcher`; `repair` or `rescue` gives `repair`; `implement`, `implementation`, `fix`, `build`, `add`, `refactor`, `write`, `update` or `migrate` gives `implementer`.
   3. Otherwise `unknown`.

   A title is prompt-derived text: hosts often generate it from the first prompt. It is read in memory only for this match and is never stored, logged or committed; extracted rows exclude titles.
2. **Task family**, from the role: `implementer` and `repair` give `implementation`, `reviewer` gives `review`, `researcher` gives `research`, and `unknown` stays `unknown`.
3. **Complexity band**, the higher of two bands. Generated tokens are the host's output count with reasoning counted once: where a store's output count already includes reasoning, reasoning is not added (stated per store in the field mapping below); input and cache tokens are left out because hosts count them differently. Its band: under 5,000 `low`, 5,000 to 19,999 `medium`, 20,000 to 59,999 `high`, 60,000 or more `critical`. Duration (last update minus start): under 5 minutes `low`, 5 to under 20 minutes `medium`, 20 to under 60 minutes `high`, 60 minutes or more `critical`. Zero is a value (band `low`); a missing count or timestamp makes that band `unknown`. With only one band known, that one; with neither, `unknown`.
4. **Risk band:** always `unknown`, because no session metadata carries risk.
5. **Funding class**, from the provider or harness. `openrouter` always gives `dollar`, because OpenRouter is prepaid credit; a recorded auth mode never changes it. For every other provider, a recorded explicit auth mode always decides: a ChatGPT or Claude subscription login gives `included_subscription`, an API key gives `dollar`. Without one: OpenCode's `claude-agent` provider gives `included_subscription`; a Codex thread, OpenCode's `openai` provider and a Claude Code subagent session give `unknown`. Anything else gives `unknown`.
6. **Historical route:** the model id and the variant or reasoning effort as recorded; a missing effort is `unknown`.

An `unknown` task family or band is passed to the router as a missing field, so the router applies its own conservative floors, as in production (`FLOORS` in `runtime/routing.py`: family `general`, complexity `high`, risk `high`).

**Store fields.** Before any run, a dated edit here pre-registers each store's field mapping: which field gives the parent, agent type, title, provider, auth mode, model, effort, timestamps, output and reasoning counts and the title, compaction and summary markers, and whether output includes reasoning. It is written after inspecting the store schemas only, never their rows. If a named value or field does not exist in a store, that edit says how the rule applies there, or the replay does not run.

**Assumptions for the historical replay.** The window use and headroom at each arrival are unknown. The pre-registered assumption is that every arrival sees one fixed synthetic observation in which every subscription window is fresh at 0% use. Settings are the defaults: Smart Routing ON, API Usage off and no organization, so the router never chooses a dollar route. The concurrency caps are the defaults, with occupancy rebuilt from the sessions' own timestamps; an arrival that would wait is counted as `pending` and not compared. Arrivals run in start-time order, ties broken by session id. The strategy table and route manifest are those at the replay's commit, recorded by digest in the report. While the manifest qualifies no route, the router cannot choose; the historical replay therefore runs only after a live session has qualified at least one route with its measured demand (user decision 5 in the plan). Under these assumptions the historical replay measures route choice only; it says nothing about funding or capacity.

The sample, exclusions, labelling rules, keyword lists, thresholds and these assumptions are pre-registered as written. Any later change needs a dated edit here before the replay runs. The first replay report is kept; a re-run after a rule change reports both results.

### Metrics

- **Disagreement rate: advisory only.** A disagreement is a replayed session whose router-chosen model, effort or funding class differs from its historical one; with an `unknown` historical effort only the model and funding class are compared. The report gives the rate overall and by role, complexity band, funding class and store, with the counts of unknowns, `pending` arrivals and historical `dollar` sessions. Because the labels are inferred, the rate is never a pass or fail gate, never blocks cutover and triggers nothing.
- **Strict criteria: pass or fail**, the four unchanged rows above, computed only on real admission records.

### Order

1. **Before the canary: the strict replay over the exported pilot records.** It must pass; it gates the canary.
2. **Before the canary, after a route is qualified live** (user decision 5): the historical replay runs once and its advisory report is recorded under Results. It must have run; its result blocks nothing.
3. **Canary start gate:** steps 1 and 2 are done. This is what the Canary's "only after replay passes" now means. It is stricter than the original wording, not weaker: a strict pass on real admission records is still required before the canary, and the historical replay is required as well.
4. **Canary** (plan S3.7), within its unchanged bounds.
5. **C.1:** the strict replay runs again over the pilot's 20 and the canary's admission records. The canary's own pass condition that "replay still reproduces the canary's admission records exactly" is this strict replay.

**Keeping the admission records.** Retention removes a closed attempt's admission record after 14 days and its quota snapshot detail (`pressure`, `snapshot_id`) after 24 hours (plan S3.6a). The pilot ran on 9 November 2026, so its snapshot detail is already gone and its records are removed from about 23 November 2026. On 10 November 2026 the coordinator exported the pilot's canary ledger read-only (SQLite backup API from a `mode=ro` connection) to `~/.local/state/vibe-workflow-replay/pilot-ledger-2026-11-09.sqlite3`, owner-only (0600): 1 ledger row, 21 events and 21 roots; `integrity_check` ok; 110,592 bytes. The snapshot detail, older than 24 hours, was already gone when it was taken. The pilot's window observation was the harness's fixed synthetic one, so its values remain known without that detail; exact reproduction from this export is untested until C.1. The canary harness exports every canary admission record with its snapshot detail at the end of each canary run and every 12 hours, so no export lands on the 24-hour expiry boundary and no field the strict replay needs expires first; the qualification harness work builds this export. A record that lacks a field exact reproduction needs counts as not reproduced, which is a strict failure.

> **Correction (11 November 2026, registered with [Edit 7](#edit-7-11-november-2026-redesigned-replay-ranking-and-canary)).** Edit 6 stays as written; this note corrects one of its facts and qualifies another. First, the exported pilot ledger holds 21 admission records, all in `worker_choice` mode with the old role names, not the 20 that Edit 6 counts; the difference is explained under [Results](#results) before a strict pass is recorded. Second, the statement that the pilot's snapshot detail was already gone does not mean the records lost their constraints, which Edit 7 point 3 checks: each of the 21 still holds its stored constraints, checked on 10 November 2026 without reading any other field. No replay ran. Edit 7 point 3 says how these records are replayed.

### Privacy

- Stores are opened read-only: SQLite through a plain `mode=ro` URI (not `immutable=1`, which skips the write-ahead log of a live store), other files for reading only. Nothing is written to or locked in a store.
- Only the metadata named in the labelling rules is read; message, prompt, part, tool and output content never is.
- Extracted rows and exported admission records stay in an owner-only local directory outside every repository (`~/.local/state/vibe-workflow-replay/` by default).
- Only aggregate results are committed: counts, rates, label distributions, exclusion counts and the digests of the table and manifest. No session id, title, timestamp or per-session row is committed.

### Quality re-execution

Deferred to C.1 with the canary. Re-execution needs each assignment's brief and acceptance checks; past sessions record no acceptance and their prompts are not read, so no past session is re-executed. Before C.1 runs, a dated edit here must register the trigger, sample, comparison and baseline for re-executing canary admissions; the baseline is chosen in that registration. Until then no re-execution is in force; the earlier budget (at most 30 assignments within 5% of one pool's canary spend cap) remains the ceiling.

## Edit 7 (11 November 2026): redesigned replay, ranking and canary

Registered on 11 November 2026. The user approved this content on 10 November 2026, including the canary cap interpretation of point 9 (call 36). Approval did not register it; this section does. It changes pre-registered text for the [redesign](superpowers/plans/2026-11-10-smart-routing-redesign.md) (plan Task F5.1). The earlier text, including Edit 6, stays as written; where the two differ, the points below govern the redesigned work. Beside earlier text it adds only the dated correction after "Keeping the admission records" in Edit 6's [Order](#order) and dated, non-normative pointers in Status, [Allocator replay](#allocator-replay), [Canary](#canary) and [Labelling rules](#labelling-rules).

**What existed when it was registered.** Under this amendment, no replay had run, the ranked canary had not started and no label had been produced: no strict or historical replay, no replay label and no learning label. This refers only to the redesigned, governed work. It does not change earlier facts: the bounded pilot canary ran on 9 November 2026, its results are recorded under [Results](#results), and its 21 admission records were exported on 10 November 2026.

**Registration is not permission.** Registering this edit fixes the rules before the work they govern. It does not authorize a live run, open a live budget, qualify a route, or make the canary or a release ready. Each still needs its own gates and approval.

1. **Vocabulary.** "Role" means specialist. The strict sample covers at least three specialists and two complexity bands.
2. **What the strict replay recomputes for ranked records.** From each record's stored detail alone:
   - the class, from `shares`, `bucket`, `epoch`, `feasible` and the earlier records. The earlier records are the ones the router itself counts: same ledger, same settings revision and at most 14 days older;
   - the route, from the stored subscription candidates in the stored order. No gateway decision is produced or replayed this release. New ranked paid records fail the release contract rather than being accepted without replay.

   The four pass or fail rows of the [allocator replay](#allocator-replay) are unchanged.
3. **Worker-choice records, old and new.** The exported pilot ledger holds 21 admission records, all in `worker_choice` mode with the old role names. Edit 6 says 20; the difference is explained under Results before a pass is recorded. Each of the 21 still holds its constraints (checked on 10 November 2026 without reading any other field), so the sentence in Edit 6 that the pilot's snapshot detail was gone is corrected by a dated note. For these records and new `worker_choice` admissions:
   - the class must be the funding class of the record's billing class;
   - every stored constraint must have a cost no greater than its headroom, and the record's reservation in that window must fit the headroom;
   - the two ledger rows (no funding violation, no resurrected capacity) apply as for any record.

   A worker-choice record without constraints fails and is counted. Old roles are read as history. New worker-choice records have `ranking.shortlist: []` and no `pressure.candidates`; they do not claim a ranked route was reproduced. The report counts them separately. The canary accepts only `mode: ranked`.
4. **Historical replay labels.** Rule 1's outcomes become specialists: `reviewer` gives `code-reviewer`, `researcher` gives `explorer-agent`, `implementer` and `repair` give `software-engineer`. Rule 2 becomes the work kind of that specialist. An unknown band still takes the router's floors.
5. **Value order.** The price of a candidate is `(3 × input + output) / 4` of its list prices per million tokens. A candidate without a price comes last. Ties go to the lower effort, then to the guidance order. One shortlist takes the first 10 candidates with a subscription route the launching host can send, after static checks. Usage freshness and feasibility checks follow the cut and do not refill it.
6. **Subscription-only selection.** Keep the existing overlapping-window, reservation and pressure equations. API share is zero because ranked paid routes are ineligible this release. If no shortlisted subscription candidate fits, wait with the existing reason; unknown or stale usage never permits a paid fallback. API allowance arithmetic remains unchanged for next release. Gateway decision/replay rules are deferred under the approved cuts.
7. **Benchmark tolerance:** 2%, applied from slice L2.
8. **Promotion test,** registered before slice L3 reads the later half: a kind of task counts when at least two candidates have at least 5 results each in the later half; at least 3 kinds of task must count; scores are promoted only if the learned pick is at least as good as the table pick in at least 80% of them.
9. **Canary.** It runs only in ranked mode on generated routes; dispatch, attach and the off-route guard use the admitted identity and pool. Keep the existing sample, concurrency and latency bounds and seven-day maximum. **Approved cap interpretation (call 36), registered here:** the 10% allowance is 100 thousandths per approved pool/window over the whole run, accumulated from positive observed increases, including unrelated usage. Any reset of an approved demanded window ends the run incomplete as `window_usage_unknown`; a reset never replenishes the allowance, and reseeding cannot erase an unknown delta. This adds a stricter effective deadline: finish before the earliest such reset, which may be less than five hours away for the Claude pool. Reserve only within the remaining allowance and abort an observed overrun. Insufficient remaining allowance releases any undispatched admission as `not_dispatched` and ends incomplete as `allowance_exhausted`. Preflight reports full-reservation coverage using the configured reservation margin, time to earliest reset, and the 60-admission minimum; it predicts neither actual usage nor guaranteed completion. A coverage shortfall requires explicit start approval acknowledging the incomplete-sample risk. F7.2 defines the calculation and enforcement. Keep local-state `--serve` preparation for registered latency measurements. Pause for a seed only before the reset deadline while cumulative usage remains observable; never admit a probe on stale data. Approval settles the interpretation; it does not register the amendment or authorize a live run. This registration does not authorize a live run either.

## Edit 8 (11 November 2026): no worker-count admission

Registered on 11 November 2026 for plan Task F5, checkpoint A1, after the user decided that no count of open workers limits admission. The earlier text, including Edit 7, stays as written, and the pilot results stay as recorded: the pilot ran under the then-current caps of 4 total and 2 per account. Where the two differ, the points below govern current and future work. Beside earlier text this edit adds only dated, non-normative pointers in Status, [Allocator replay](#allocator-replay) and [Canary](#canary).

1. **No count cap.** Native, subscription and API admission never refuses or waits because of how many attempts are open, in total or on one account. The `concurrency_cap` reason is gone, and the default caps of 4 total and 2 per account no longer exist. Real limits still bind: subscription windows and quotas, provider throttling, measured host resources, physical resource ownership, finite task authority (attempts, resource units, deadline) and money.
2. **Saved caps are history.** A `caps` value already saved in routing settings or in the ledger stays readable and is never rewritten or enforced. Status reports it as `legacy_caps` with `enforced: false`, and settings warn `caps_not_enforced`. D6 plans may omit `max_parallel_workers`, `host_capacity` and `capacity_ref`. A legacy plan with all three is still validated and reported as `capacity.legacy_declared` with `enforced: false`.
3. **Replay rows.** In the [allocator replay](#allocator-replay), "concurrency cap exceeded" is no longer a funding violation. The other rows are unchanged: no protected allowance breached, no window over-committed, no claim beyond headroom, no resurrected capacity, exact replay and no admission record left by a refused arrival.
4. **Canary and pilot protocol.** The canary's "Concurrency caps" bound and the preflight refusal "concurrency caps not raised" are retired. The pilot driver's one-admission-at-a-time rule, with no open attempt before each admission, is the qualification protocol of that harness. It is a measurement choice, not admission policy, and it adds no global limit to any launcher or coordinator.
5. **Not changed by this edit.** API Usage stays deferred now and is required next. Ranked selection stays subscription-only (Edit 7, point 6). Production OpenRouter stays deferred. The 20% API-money buffer is a separate requirement from subscription-window margins; today one `margin_bp` setting (default 2,000 basis points) pads both, and separating them is pending financial work. This edit makes no claim that any pending financial or fallback implementation passes.
6. **Downgrade.** A package from before this edit rejects new D6 plans without the three count fields, and assignments stored with `host_capacity: null`. No reverse reader was authorized or proved. To go back, keep the state as it is and reconcile it with a compatible package; never delete or rewrite its history, and never downgrade it automatically.

Registration is not permission: this edit authorizes no live run, budget, qualification or release.

## Edit 9 (12 November 2026): store field mapping for the historical replay

Registered on 12 November 2026, before any historical replay has run and before its runner exists (plan Task F8.1, step 1). This is the dated field-mapping edit that Edit 6's **Store fields** paragraph requires. It changes no rule, keyword, threshold, exclusion, assumption or result above; where a field is missing, it states how the existing rule already applies, or that the replay does not run.

**How it was written.** Only the store schemas were read: each SQLite store was opened through a plain `mode=ro` URI, and only `sqlite_schema` and `PRAGMA table_info` were queried. No session, thread, message, part or token row was selected, and no session file, title, value, grouped count, model distribution, credential, qualification record or pilot record was read. No public source definition was available offline, and no network call was made, so a field's meaning is stated only where its name and type settle it. Everything else is marked unproved, and the runner treats it as missing.

**The three stores.** Edit 6's count named them; the count did not record their paths, so they are identified here from the host formats:

- **A. OpenCode store:** OpenCode's SQLite database in its data directory, table `session_v2`, sessions of this repository (matched through `project` and `project_directory`). No legacy JSON session directory exists beside it.
- **B. Codex store:** Codex's thread-state SQLite database (`state_5.sqlite`) in the Codex home, table `threads`, threads whose `cwd` is in the old `vibe-coding-workflow` checkouts.
- **C. The `opus` store:** not identified. No separate store named `opus` was found in the Codex home, the OpenCode data directory or the `opus` checkout, and the 10 November count did not record which host format it read. It may be store B's threads with an `opus` working directory, or a Claude Code project's subagent files, which are session content and have no schema to read here. The sample is every eligible session of the three stores, so the replay does not run until a dated edit names store C and maps it, or records the user's decision to leave it out.

**Field mapping.** "Absent" means no column carries it, so Edit 6's rule for a missing value applies.

| Edit 6 field | A. OpenCode `session_v2` | B. Codex `threads` |
|---|---|---|
| Session id (dedup) | `id` | `id` |
| Parent | `parent_id`; null means no parent | `thread_spawn_edges.parent_thread_id` by `child_thread_id`; else `attached_thread_parent_id`. Without either, no parent. `source` and `thread_source` are not read: their values are unproved |
| Agent type | `agent` | `agent_role`; `agent_nickname` and `agent_path` are not read |
| Title (in memory only) | `title` | `title`; `name`, `preview` and `first_user_message` are never read |
| Provider | Inside `model` (TEXT); its encoding is unproved, so provider is `unknown` until a dated edit registers it from a source definition | `model_provider` |
| Auth mode | Absent; the account and credential tables are never read. Rule 5 applies without an auth mode: `claude-agent` gives `included_subscription`, `openai` gives `unknown` | Absent (`creator_account_id` is an identity, not an auth mode, and is never read). Rule 5: a Codex thread gives `unknown` |
| Model | Inside `model`; same as provider: `unknown` until its encoding is registered | `model` |
| Variant or effort | Not a column; whether `model` holds it is unproved, so `unknown` | `reasoning_effort` |
| Start | `time_created` (INTEGER); its unit is unproved | `created_at_ms`, else `created_at` (INTEGER); their units are unproved |
| Last update | `time_updated`, with the same unit condition | `updated_at_ms`, else `updated_at`, with the same unit condition |
| Output count | `tokens_output` | Absent: `tokens_used` is one total whose parts are unproved, and the per-turn counts are only in rollout files, which are session content and never read |
| Reasoning count | `tokens_reasoning` | Absent |
| Output includes reasoning | Unproved | Not applicable |
| Title, compaction and summary markers | No marker column. `time_compacting` marks a session that compacted, not a compaction session, so it does not exclude one. No session is excluded by this reason | No marker column; no thread is excluded by this reason |

**How the rules apply where a field is unproved or absent.**

- **Generated tokens in store A.** Whether `tokens_output` already includes `tokens_reasoning` is unproved, so neither "add" nor "do not add" can be chosen under rule 3. The token band of store A is `unknown` until a dated edit settles this from a source definition; the duration band applies once the timestamp unit is registered. Store B's token band is `unknown` because it has no output count.
- **Store A's provider, model and effort.** While the encoding of `model` is unregistered, every store A session has no model id and is excluded under Edit 6 ("sessions without a model id"). The replay over store A therefore produces no labels until a dated edit registers that encoding. This is the effect of the existing rule, not a new one.
- **Timestamps in both stores.** A timestamp is used only after a dated edit registers its unit from a source definition. Until then it is missing: the session is excluded under Edit 6 ("without either timestamp"), and the duration band and the one-hour still-running exclusion cannot be computed.
- **Title, compaction and summary sessions.** The count by this reason is 0 in both stores, and the report states that no host marker exists rather than implying that none occurred.

**Qualification and pilot exclusion.** The ids to block come from the qualification run fixtures and the exported pilot ledger, as Edit 6 says. Neither was read for this edit, so neither the ids nor the field that holds them is confirmed. The historical replay therefore stays blocked until an approved caller supplies validated exclusion metadata: the ids, their sources and how each was checked. With no exclusion basis the runner refuses to report. Synthetic test fixtures may exercise this check; they grant no authority to run over real history.

**What this leaves blocked.** Before a historical run, dated edits must still register store C, the encoding of store A's `model`, the timestamp units of both stores and whether store A's output count includes reasoning, and an approved caller must supply the exclusion metadata. Until then the replay would exclude every session or refuse to report, so it does not run.

**Schema identity.** The private report records a SHA-256 of each store's schema. Before a run, the runner compares the live schema with the mapped tables and columns; a missing mapped column makes that field missing, and a renamed or retyped one stops the run until a dated edit maps it again.

Registration is not permission: this edit authorizes no historical run, live qualification or release, and the run still waits for the prerequisites in Edit 6's [Order](#order).

### Supplement (12 November 2026): field meanings from public source

Added later on 12 November 2026, before any historical replay has run. Edit 9 above stays as written; it records what the schemas alone showed. This supplement settles three of its unproved meanings from the hosts' public source code. No store was opened again and no row was read.

- **Store B timestamps.** In Codex's public source (`openai/codex`, commit `77fde36ae112437a8ce23e752209099ee04a0d50` of 10 November 2026), the thread reader selects `created_at_ms` and `updated_at_ms` as the thread's start and last update (`codex-rs/state/src/runtime/threads.rs`). It reads them as epoch milliseconds, and a value below 1,577,836,800,000 is an older row in seconds, multiplied by 1,000 (`epoch_millis_to_datetime` in `codex-rs/state/src/model/thread_metadata.rs`). The replay uses the same two columns with the same rule. It does not read the second-precision `created_at` and `updated_at`, and a null value is missing.
- **Store A `model`.** In OpenCode's public source (`anomalyco/opencode`, formerly `sst/opencode`, commit `101776024cc69bafb3f9dc90027ab4d1eab40a2a`), `session_v2.model` is JSON with `id`, `providerID` and an optional `variant` (`packages/core/src/session/sql.ts`). The replay takes the model id from `id`, the provider from `providerID` and the variant from `variant`. A value that is not such JSON or has no `id` means no model id.
- **Store A timestamps.** The same source sets `time_created` and `time_updated` from `Date.now()`, so they are epoch milliseconds (`packages/core/src/database/schema.sql.ts`).

**Still unproved.** At that OpenCode commit, `tokens_output` holds visible output and `tokens_reasoning` is counted separately (`packages/core/src/session/usage.ts`). Rows written by earlier versions, or copied from older session storage, may follow another definition, and this was not proved. Store A's token band therefore stays `unknown`, as in Edit 9. Also unchanged: the parts of store B's `tokens_used`, the values of `source` and `thread_source`, the absent auth modes and host markers, store C, and the exclusion basis.

**Limits.** These are current upstream revisions, not proved to be the versions that wrote every stored row. The local schemas match the mapped columns by name and type, and the runner stops on any change to a mapped column. Until store C is mapped, an approved caller supplies the exclusion metadata and a route is qualified live, the historical replay refuses to report. This supplement authorizes no run.

## Results

Replay, disagreement, re-execution and the pre-registered canary: not yet run.

The live qualification runs are recorded in the [qualification report](smart-routing-qualification.md), not here, because they test gates rather than these criteria: the [stages 0–4 re-run](smart-routing-qualification.md#re-run-stages-04-harness-3f87159), the [descendants and S3.3d probes](smart-routing-qualification.md#live-probe-run-descendants-and-s33d-harness-85ae5f4), the [refused ChatGPT attempts 5 and 6](smart-routing-qualification.md#chatgpt-attempts-5-and-6) and the [successful ChatGPT attempt 7](smart-routing-qualification.md#chatgpt-attempt-7).

### Pilot canary, run 1 (9 November 2026): aborted at admission 1

Attempt 1 of the pilot.

- **Run.** Harness at `0a19e6c`, `--canary --project /Users/fcouto/dev/git/vibe-workflow --admissions 20`, with the default dedicated canary state, a fresh isolated directory and `claude-agent/haiku#low` only. Evidence: [`pilot-attempt1-results.json`](../tests/fixtures/smart-routing/probes/pilot-attempt1-results.json) (harness output, unchanged; renamed from `pilot-results.json` when attempt 2 was recorded) and [`qual-call-ledger.json`](../tests/fixtures/smart-routing/probes/qual-call-ledger.json) (exported from the durable ledger).
- **Before admission 1.** Every refusal check passed, including a clean `status` (no holds, 0 open attempts):
  - The canary state was created on first use: `authority_required` on (canary ledger only), Smart Routing ON, API Usage off, no organization.
  - The plugin-chain probes passed. The guard probe was refused by the guard with `guard_variant`, and the pending probe was refused by the shim with `attach_required`, before `qual-post` saw it. Both settled at 0.
- **Admission 1** (implementer, low band). The pilot sent the managed request and its one title: 2 calls, settled against the 2 reserved. Then the harness's accounting step raised a `TypeError`. The driver aborted with `recovery_error` (detail `accounting: TypeError`, phase `launched`), recorded the hold `uncertain` (the launch had happened) and ran the kill switch, which reported `updated`.
- **Cause, unknown at the time of the run, found offline afterwards: a harness defect.** `Run.__init__` assigned the pilot's account id to `self.account` (`self.pool, self.account = ...`). That shadowed the method `Run.account(session)`, so `CanaryEnv.account` called the string `"anthropic:pilot-canary"` and raised `TypeError: 'str' object is not callable`. The attribute was added after the qualification re-run, which had used the method successfully. The run stopped as pre-registered and was not retried.
- **State left behind, in the canary state only.** Smart Routing is OFF (settings revision 2), and one `uncertain` hold is open (1 open attempt), as accepted in the pre-registration. Recovery is the coordinator's decision. The production routing state was not touched.

| Pass criterion (pre-registered) | Observed | Result |
|---|---|---|
| 20 admissions with zero abort conditions | 0 completed; aborted at admission 1 (`recovery_error`) | **fail** |
| Every settlement a `complete` subscription settlement, units recorded, no dollar charge | No settlement: the hold was recorded `uncertain` before `record complete`; no charge (subscription only, no organization) | **fail** (not reached) |
| `status` clean at the end | 1 `uncertain` hold, 1 open attempt, Smart Routing OFF (after the kill switch) | **fail** |
| Latency p95 within budgets: warm preparation 50 ms, cold 500 ms, admission transaction 100 ms (single caller) | Not measured: the driver records an admission's latencies only on completion, and none completed. Shim hook time was `model.request` 0.44 ms median / 0.69 ms max (n=2, including the guard probe) and title 3.17 ms (n=1). | **unobserved** |
| Every blocked path blocked at 0 tokens | Guard probe (`haiku#high`) and pending probe both blocked, 0 sent; 1 off-route request (the guard probe) was recorded and not sent | pass (n=2) |
| Zero interactive-session disturbance; user's OpenCode directories unchanged | No interactive session was run. The user's OpenCode config, data, state and cache showed 0 unexpected changes (1 desktop-app churn entry in `~/.local/share/opencode`). Report-only: `~/.claude` had 3 entries added, 1 changed and 1 removed, and `~/.claude.json` changed. | pass for the directories; interactive sessions unobserved |

- **Calls.** Pilot 2 of 24 (both probes reserved 1 and settled at 0; admission 1 reserved 2 and sent 2). Qualification remains 9 of 14. No open reservations.
- **Cleanup (verified by the harness, and checked again afterwards):**
  - No processes left; isolated directory removed.
  - `.vibe-qual-canary/` removed, and `.git/info/exclude` byte-identical.
  - Project HEAD and the full `git status --porcelain=v1 -z --untracked-files=all` output identical to before the run.

**What attempt 1 does not show.** It is not the pre-registered canary, and cutover stays blocked. It is subscription-only. Window telemetry is not observable on this host, so the call cap was the proxy. One admission ran: no role other than implementer and no band other than low was exercised, and no latency budget was measured.

**After attempt 1** (no model call, no server):

- The defect was fixed test-first: the attribute was renamed `route_account`.
- An end-to-end unit test now drives whole pilot admissions through a live-shaped OpenCode double with the real `routing.py` on temporary stores. It covers one admission, admissions 2 to 20 with no further title, and a clean 20-admission run with window settlements and no charge.
- Aborts now record a sanitized exception origin, the timing of each operation even when the admission is partial, and payload shapes for aborted admissions.
- The canary state was reset by deleting the canary directory with the harness's guarded `--reset-canary-state`, after confirming no harness process was alive. The durable call ledger was left as it was (pilot 2 spent), and the pilot cap was then raised to 28 by Edit 4.

### Pilot canary, attempt 2 (9 November 2026): 20 of 20 admissions, no abort

- **Run.** Harness at `94f9e0e`, `--canary --project /Users/fcouto/dev/git/vibe-workflow --admissions 20`, with the default dedicated canary state (created fresh on first use), a fresh isolated directory and `claude-agent/haiku#low` only. It ran under Edits 4 and 5: pilot cap 28, the settled-accounting re-read rule, and titled sessions for admissions 2 to 20. Single run, not split.
- **Evidence.** [`pilot-results.json`](../tests/fixtures/smart-routing/probes/pilot-results.json) is the harness output, unchanged; [`qual-call-ledger.json`](../tests/fixtures/smart-routing/probes/qual-call-ledger.json) is exported from the durable ledger.
- **Before admission 1.** Every refusal check passed: a clean `status` (no holds, 0 open attempts), `routing.health` `ok` and no refusals. The canary state was created: `authority_required` on (canary ledger only), Smart Routing ON, API Usage off, no organization. The plugin-chain probes passed: the guard probe was refused by the guard with `guard_variant`, and the pending probe was refused by the shim with `attach_required` before `qual-post` saw it. Both settled at 0.
- **Admissions.** All 20 completed: prepare, health, dispatch, attach, one prompt with derived ids, settled accounting, `record complete`, detach and `status`.
  - **Roles × bands:** implementer, reviewer and researcher (7, 7 and 6 admissions), each in the low and medium bands.
  - **Titles:** admission 1 sent its request and the one title (`title.state` `known`). Admissions 2 to 20 each sent one request and no title (`title.state` `zero`, sessions titled at creation).
  - **Re-reads:** 0 in all 20 admissions; 0 `title_unaccounted` deviations; no accounting `unknown` reason in any admission.
  - **Settlement:** every one a window settlement (`win:pilot-sub/5h` released), charged 0.
  - **Working tree:** checked after admissions 5, 10, 15 and 20 and at the end; no new dirty path, no change to an already-dirty path, and HEAD unchanged.

| Pass criterion (pre-registered) | Observed | Result |
|---|---|---|
| 20 admissions with zero abort conditions | 20 completed, no abort, no deviation | **pass** |
| Every settlement a `complete` subscription settlement, units recorded, no dollar charge | 20 of 20 `record complete` settled as window settlements, releasing the `win:pilot-sub/5h` claim; charged 0; no organization and no API usage | **pass** |
| `status` clean at the end | no holds, 0 open attempts, `unstamped_open_holds: 0`; no `cost_unknown`, uncertain, blocked or awaiting-reconciliation state; Smart Routing still ON (the kill switch was not needed) | **pass** |
| Warm preparation p95 ≤ 50 ms | `prepare` through the persistent `routing.py --serve` child: n=20, median 1.36 ms, p95 1.85 ms, max 1.86 ms | **pass** |
| Cold preparation p95 ≤ 500 ms | No cold-path preparation was timed separately (every timed `prepare` ran on the already-running child). The slowest `prepare` was 1.86 ms. | **unobserved** |
| Admission transaction p95 ≤ 100 ms (single caller, as pre-registered) | `dispatch`: n=20, median 1.32 ms, p95 1.75 ms, max 1.75 ms | **pass** (single caller) |
| Every blocked path blocked at 0 tokens | Guard probe (`haiku#high`, `guard_variant`) and pending probe (`attach_required`): 2 of 2 blocked, 0 sent. The 1 off-route request recorded was the guard probe, not sent. No unexpected title occurred to block. | **pass** (n=2) |
| Zero interactive-session disturbance; the user's directories unchanged | No interactive session was run. The user's OpenCode config, data, state and cache showed 0 unexpected changes (2 desktop-app churn entries in `~/.local/share/opencode`). Report-only: `~/.claude` had 22 entries added, 2 changed and 2 removed (Claude CLI session state), and `~/.claude.json` changed. Project HEAD, the full porcelain status and `.git/info/exclude` were identical before and after. | **pass** for the directories and the project; interactive sessions **unobserved** |

Other per-operation timings, all n=20:

| Operation | Median | p95 | Max |
|---|---|---|---|
| `health` | 1.69 ms | 2.61 ms | 2.62 ms |
| `attach` | 1.72 ms | 2.35 ms | 2.36 ms |
| Shim check (`qual-pre` → `qual-post`) | 0.84 ms | 1.18 ms | 7.16 ms |
| `record complete` | 1.78 ms | 2.35 ms | 2.37 ms |

The title request's shim time was 2.76 ms (n=1).

- **Calls.** Pilot 23 of 28: attempt 1 spent 2, and attempt 2 sent 21 (20 requests and 1 title); both probes reserved 1 each and settled at 0. Qualification remains 9 of 14. No open reservations.
- **Cleanup**, verified by the harness and checked again afterwards:
  - no processes left; isolated directory removed;
  - `.vibe-qual-canary/` removed; `.git/info/exclude` byte-identical;
  - project HEAD and `git status --porcelain=v1 -z --untracked-files=all` identical;
  - the production routing state was not touched.

  The canary state remains, clean.

**What the pilot does not show.**

- It is not the pre-registered canary: it ran 20 admissions in one run rather than 60 over up to 7 days. Cutover stays blocked; replay has not met its 200-assignment minimum, and no production launcher is wired.
- It is subscription-only, on `claude-agent/haiku#low`, with tiny tool-less prompts and explicit worker choices; neither the strategy table nor the ranker was used.
- It ran one admission at a time on a dedicated canary ledger with a fixed synthetic window observation. The admission-transaction budget was measured with a single caller, not four.
- Window telemetry is not observable on this host, so the call cap was the proxy for the per-window spend cap.
- Cold preparation, interactive sessions, compaction, retries, subagent spawns and any failure path after admission were not exercised.
