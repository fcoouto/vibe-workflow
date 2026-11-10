# Smart Routing redesign: value ranking, learning and one specialist roster

Status: approved by the user, 2026-11-10. Base: `main` at `5ed0784`.

This design changes parts of two earlier documents and leaves the rest of them in force:

- [Smart Routing design, 8 November](2026-11-08-usage-routing-opencode-subscriptions-design.md): it replaces sections 5.1 (Jev), 5.3 (optional model ranker) and 11 ("later learning"), and adds one rule to section 7.2 (when OpenRouter may be chosen).
- [C.3 wiring design](2026-11-10-smart-routing-c3-wiring-design.md): it replaces decision 5's per-model route manifest.

It also changes one rule in [guidance publication](../../../clients/guidance-publication.md#strategy-table-refresh): the ranking table may be recompiled by a due job, not only in a maintenance session.

Two related designs follow separately: session titles on OpenCode and Claude Code, and the docs and code unification.

## 1. Intent

Routing should pick the most cost-effective model that is capable of the task. It should not pick the strongest model by default, and it should not pick the cheapest. It should also get better with use: each finished task is evidence about which model did that kind of work well in this project.

Three things stand in the way today:

- The workflow has five different role lists, so the same worker has a different name in routing, settings, delegation, records and visible labels.
- Route data is tied to exact model names, so a new model version needs new committed data and a new live qualification.
- Nothing reads finished tasks back into the ranking.

## 2. Goals, non-goals and anti-goals

**Goals**

- Offer only capable models, ordered by value.
- Use a subscription while it has room. Spend dollars only when the user enabled that and the subscription cannot serve.
- Improve the order per project from finished tasks, with no model call.
- Adopt a new model version on the day the vendor recommends it.
- Use one specialist roster and one Task Profile template everywhere.
- Keep the whole path lightweight.

**Non-goals**

- Jev, in any role.
- A model call per assignment.
- A trials or exploration mechanism for new models.
- Per-project roster customization. One roster ships; changing it is a later design. (Coordinator's call.)
- API Usage changes. It stays deferred until after release; this design only fixes its place in the order.
- Session titles and the wider vocabulary cleanup, which have their own designs.

**Anti-goals**

- A model name in code, rules, skills, templates or shipped data.
- A learned score that lowers a capability floor or adds a candidate.
- A launch that waits for a download, a ranking model or an analysis.
- Stored task text, finding text or file paths in learning records.

## 3. Constraints

- **Lightweight rule (binding).** On-demand reads only, no timers, polling or daemons, no model call to measure usage, short bounded processes that fail closed, cheap `prepare` and `check`. Each added cost is stated in section 5.8 with its lighter alternative.
- **Exact replay.** The strict replay must reproduce every admission record. It never calls a model and never reads live data.
- **File size.** Seven files are over the 1,000-line limit with fixed ceilings in `scripts/validate.py`: `runtime/routing.py`, `runtime/usage_budget.py`, `runtime/evidence.py`, `runtime/delegation.py`, `runtime/changes.py`, `runtime/state.py` and `runtime/adapters/codex.py`. New logic goes into new modules; changes to those files must not add lines.
- **No API keys before release**, except the OpenRouter key the user approved for the gateway check.
- **Model names** may appear only in downloaded data in user state, in learning records, in the user's own settings, in test fixtures with invented names, and in dated evidence records.

## 4. Approaches considered

**How to rank**

| Approach | Model calls per day | Outcome |
|---|---|---|
| A. A ranking model adjusts the order, cached per kind of task | About 2 to 5 | Optional later step, section 5.11 |
| B. A ranking model call for every assignment | 20 to 60, plus seconds per launch | Rejected |
| C. Ranking table plus per-project learned scores | 0 | **Chosen** |

The user chose C on 2026-11-10. An evaluation the same day showed a model ranker matching a guidance rubric in 34 of 36 choices, the same as the table. The learning loop is what improves routing, and it needs no model.

**In what order to build**

| Approach | Outcome |
|---|---|
| 1. Canary first, redesign after | Rejected: the live session would qualify per-model routes and a replay format that this design replaces |
| 2. Foundation first, one live session, learning after | **Chosen** |
| 3. Everything before the canary | Rejected: longest wait, and learning would be built with no real data |

## 5. Design

### 5.1 Vocabulary

| Term | Meaning |
|---|---|
| Specialist | One entry of the roster. It replaces every earlier role name. |
| Task Profile | The structured description of a task that routing reads. |
| Path | A way to reach models: harness, provider and sign-in type, with its billing class and usage windows. No model. |
| Route | A path plus one model and one effort. Generated on each machine. |
| Candidate | A model, effort and harness that the ranking table accepts for a kind of task. Called a strategy in code today. |
| Ranking table | The compiled table of candidates per kind of task. Called the strategy table in code today. |
| Shortlist | At most 10 candidates for one assignment, in value order. |
| Learned score | A per-project measure of how a candidate performed on one kind of task. |

### 5.2 Specialist roster

One roster replaces the five role lists (`routing.ROLES`, `routing_settings.ROLES`, `routing_evidence.CONTROL_ROLES`, `worker_visibility.ROLES`, `delegation.NATIVE_ROLES`, and the two translation tables `routing_settings.PROFILE_ROLES` and `delegation_routing.ROLES`). There is no second vocabulary and no translation layer. The same identifier appears in code, documents, stored records and visible labels.

Each entry has:

- `id`, for example `code-reviewer`;
- `label`, for example "Code Reviewer";
- `when_to_use`, one sentence that is also the rule for choosing the specialist;
- `work_kind`, the kind of work the vendor guides and public benchmarks describe (section 5.5);
- `writes`, whether the specialist may change files;
- `reviews`, whether the independent-review rules apply to it.

**Proposal (coordinator), accepted by the user.** The last three are attributes of the specialist, not a second role name. Code needs them because it applies different floors to work that writes files and separate rules to reviews, and because public evidence describes kinds of work, not job titles. `work_kind` replaces the free-text `task_family` field, so a profile no longer carries both a role and a family.

**Proposal (coordinator): the shipped roster** is the toolkit's representative roster of 23 specialists with a display label for each: `orchestrator`, `code-reviewer`, `security-reviewer`, `typescript-reviewer`, `react-reviewer`, `react-build-resolver`, `test-engineer`, `qa-automation-engineer`, `database-architect`, `devops-engineer`, `backend-specialist`, `frontend-specialist`, `seo-specialist`, `performance-optimizer`, `product-manager`, `product-owner`, `project-planner`, `code-archaeologist`, `debugger`, `explorer-agent`, `documentation-writer`, `penetration-tester`, `security-auditor`. One entry is added: `software-engineer` ("Software Engineer"), for implementation that no narrower specialist covers, because the toolkit list has no general implementer.

**Proposal (coordinator): work kinds and attributes.** Six work kinds: `implementation`, `review`, `investigation`, `planning`, `testing`, `writing`.

| Specialist | Label | Work kind | Writes | Reviews |
|---|---|---|---|---|
| `orchestrator` | Orchestrator | planning | no | no |
| `code-reviewer` | Code Reviewer | review | no | yes |
| `security-reviewer` | Security Reviewer | review | no | yes |
| `typescript-reviewer` | TypeScript Reviewer | review | no | yes |
| `react-reviewer` | React Reviewer | review | no | yes |
| `react-build-resolver` | React Build Resolver | implementation | yes | no |
| `test-engineer` | Test Engineer | testing | yes | no |
| `qa-automation-engineer` | QA Automation Engineer | testing | yes | no |
| `database-architect` | Database Architect | implementation | yes | no |
| `devops-engineer` | DevOps Engineer | implementation | yes | no |
| `backend-specialist` | Backend Specialist | implementation | yes | no |
| `frontend-specialist` | Frontend Specialist | implementation | yes | no |
| `seo-specialist` | SEO Specialist | implementation | yes | no |
| `performance-optimizer` | Performance Optimizer | implementation | yes | no |
| `software-engineer` | Software Engineer | implementation | yes | no |
| `product-manager` | Product Manager | planning | no | no |
| `product-owner` | Product Owner | planning | no | no |
| `project-planner` | Project Planner | planning | no | no |
| `code-archaeologist` | Code Archaeologist | investigation | no | no |
| `debugger` | Debugger | investigation | no | no |
| `explorer-agent` | Explorer Agent | investigation | no | no |
| `documentation-writer` | Documentation Writer | writing | yes | no |
| `penetration-tester` | Penetration Tester | review | no | yes |
| `security-auditor` | Security Auditor | review | no | yes |

The `when_to_use` sentences are the toolkit's, kept in the template.

**How today's structural roles map onto this (coordinator's call).**

- `worker` and `review` disappear as names. Code that branches on them reads `writes` and `reviews`.
- `coordinator` becomes `orchestrator`. As today, the orchestrator is never routed: the user's coordinator keeps its own model.
- `check` is not a specialist. A check run uses no model, and delegation already identifies it by its operation kind (`check`). That field stays; the role value goes.
- `researcher` and `repair` in the routing settings disappear. Research is done by an investigation specialist. Repair is done by the specialist that did the work.

**Explicit choices in settings (coordinator's call).** `worker_choices` is keyed by specialist identifier. This machine has no production routing settings file, and the package has no release yet, so nothing is migrated. A settings file that still has an old key is left untouched; its old entries match nothing, and `check` reports them for a one-time re-key.

**Stored history.** Records written before the change keep their old role names. They are read as history only: shown as written, never written again and never used for learning. The rename covers code, policies, client contracts, skills, templates and tests in one change, with the old values still accepted when reading stored state.

### 5.3 Task Profile template

`templates/task-profile.json` is the single source. It holds a contract version, every field with its allowed values, one definition and one example for each complexity and risk level, the floors, and the roster of section 5.2. It stays near 2 KB without the roster.

- Code validates profiles against it. A test pins the constants in `runtime/routing.py` to it, since that file cannot grow.
- The Task Profile section of `templates/worker-brief.md` is generated from it, and `scripts/validate.py` fails on drift.
- The coordinator fills the profile from the template when it writes the brief.
- When complexity, risk or the review policy is missing, `prepare` does not block. It applies the floors, as today, and returns the list of missing fields. The coordinator can fill them from the template and call `prepare` again before dispatch; if it launches as is, the profile counts as floor-filled (section 5.9). No model fills a profile.
- The specialist is required and has no floor. `prepare` refuses a profile without a roster specialist, as it refuses an unknown role today. The work kind comes from the specialist, so it is never missing.
- The installed package must ship `templates/`, because routing reads it at run time for the first time. The plan confirms this.

### 5.4 Paths and generated routes

The route manifest splits in two.

**Shipped path rules** (`runtime/routing_routes.json` becomes a list of paths). Each path names harness, provider, sign-in type, billing class, usage pool, window identifiers and unit, and whether the launch binding can be enforced. No model, no effort, no demand.

**Generated routes**, in user state beside the routing settings. On each machine, routing combines the qualified paths with the model catalog and the ranking table and writes the exact routes. Each route carries a demand value: how much of each window one launch uses.

- **Qualification.** The live session qualifies paths, not models. A path is qualified on a machine by its recorded live result. A new model on a qualified path needs no new session.
- **Demand.** A path starts with the demand measured for it in the live session. Each model's demand is then updated from the window use its settled launches already record. A model with no launches starts with its predecessor's value (section 5.7), or with the highest value in the same pool.
- **Listed is not entitled.** When a provider refuses a listed model for the account, the route is marked unavailable for that machine and the next candidate is tried.

### 5.5 Ranking table and shortlist

The ranking table keeps its shape: rows per kind of task, each with ordered candidates. A row is now `work_kind`, complexity and risk.

The row shape changes, so the vendor guidance already published is turned into rows once more before the canary, through the existing guidance publication procedure. That is one model read per guide page and a pre-canary item in the plan.

**Who qualifies for a row ("Similar or Better Capability").**

1. The vendor guide names a model and effort for that kind of work. Those are the row's reference candidates.
2. A model from any vendor also qualifies when public benchmark data for that kind of work shows it scoring at least as well as a reference candidate, within a stated tolerance.
3. Nothing else qualifies. A row never holds a model below the bar, and bands only rise, as today.

Rule 2 is new logic, not an extension. Today a benchmark only supports a candidate that a guidance row already names, and benchmark records carry no score. Score-based qualification arrives with the importer in slice L2. Until then rows hold only the candidates the vendor guides name, which is what the canary runs on.

**How the row is ordered.** By expected cost for an accepted result, lowest first. The public list price from the catalog is the common yardstick, because window units cannot be compared across providers. The catalog input gains a price field for this. Before a project has results, that is the list price alone. Once learned scores apply (section 5.9), it is the list price times the rework factor, divided by first-pass success.

**The shortlist** for one assignment is the row's order after the context-size and constraint filters, cut to 10. It is never padded.

**Proposal (coordinator).** The tolerance and the price formula (how input and output prices combine) are fixed in the plan and pre-registered before the canary. A benchmark result that states no effort level qualifies the model only at its default effort.

### 5.6 Choosing the funding

Admission walks the shortlist in this order:

1. **Subscription routes**, under the existing window-pressure logic.
2. **API Usage routes**, when the user enabled API Usage. The existing arithmetic between subscription use and grant expiry is unchanged.
3. **OpenRouter routes**, when the OpenRouter Integration is enabled, and only in two cases:
   - no shortlisted candidate has a subscription or API Usage route on this machine (a capability gap);
   - every subscription pool that serves the shortlist has a fresh reading at or above the high-pressure threshold `H`.
4. **Otherwise the assignment waits** and reports why. This replaces the old separate fallback step.

Details (coordinator's calls unless marked):

- **Within the subscription class the allocator is unchanged**, which is the user's decision to keep the window-pressure logic. Value order decides between candidates in one pool. Between two pools, the pool with more room for its window wins, even when its best candidate costs more by list price.
- **"At or above `H`"** means the window's fresh reading, used plus currently held over the limit. It is not the pace-projected value the API Usage arithmetic uses, so a fast-burning but half-empty window does not open a dollar route.
- **"Every subscription pool"** means the pools qualified on this machine.
- **OpenRouter is a strict last pass.** It takes no part in the share accounting between subscription and API Usage. It stays a dollar route under the existing organization and ledger gates.
- An unread or stale window never opens OpenRouter: an unknown is not a full window.
- Explicit choices in the user's settings keep working as today.

### 5.7 New models and model families

A new version is a new candidate. Code never parses a model name.

- The catalog supplies `family` and `release_date` for each model.
- A model is treated as the **successor** of the current pick when it is in the same family, its release date is newer, and the refreshed vendor guide names it for that kind of work.
- The successor takes the predecessor's place in the row at once. It inherits the predecessor's learned score as a weak starting value, worth at most 5 results, and its own results then replace it.
- The predecessor stays as the next candidate until the catalog drops it. Its history stays in the learning records under its name.
- A **new line** with no predecessor takes the place the vendor guide gives it, with a neutral score. No trials mechanism exists.
- If a successor does worse on a kind of task, the scores move it down and routing reports it as a maintenance finding.

### 5.8 Data refresh

Three due jobs keep the data current. Each has an `expires_on` value. `scripts/workflow.py` and `scripts/routing.py` compare it with the clock when they start, which costs one small file read. A due job runs once in a short, bounded, detached process, with one runner at a time and a backoff after failure. No session waits for it, and a failed job leaves the last verified data in place.

| Job | Period | What it does | Added cost | Lighter alternative |
|---|---|---|---|---|
| Model catalog | Daily | Downloads the public catalog (models, prices, limits, efforts, family, release date) | One download of about 6 MB | A conditional request that downloads only on change, or a weekly period |
| Benchmarks | Weekly | Downloads public benchmark results | One download per source | Vendor guides only |
| Vendor guides | Daily | Downloads each guide page and compares its digest | One small download per page | Check only when the catalog shows a new model in a family in use |

After any change the ranking table is recompiled locally, with no network or model access. When a guide page changed, `prepare` and `check` report it, and the next session turns the page into rows once through the existing guidance publication procedure. That is one model read per changed page, a few per month.

### 5.9 Learning loop

**Record.** When a root task settles with a canonical acceptance and no unresolved hold, one analysis row is written per worker assignment. It is extracted from records the workflow already writes; no model is called. It holds:

- the specialist, complexity and risk, and whether the profile was filled by floors;
- the route, model name, family, effort, harness and the shortlist reference used;
- accepted or rejected;
- review rounds, repairs, rescues and escalations;
- finding counts by severity;
- tokens, window units and dollars;
- elapsed time.

A task without a canonical acceptance is recorded as incomplete and not scored. The analysis is idempotent and must run before the 14-day pruning of admission records. It is also available as a one-shot `analyze` operation.

**Score.** Per project, per kind of task (specialist, complexity, risk) and candidate:

- **First-pass success:** accepted with at most one review round and no rescue, escalation or critical or high finding.
- **Rework factor:** all tokens for the task, including review and repair, over the worker's own tokens.

**Fairness.** Candidates are compared only within one kind of task. A candidate is not reordered below 5 results and counts fully near 20. Only the last 60 days count. A floor-filled profile counts half. An escalated task's failure is charged to the level that failed. A thin kind of task borrows from its `work_kind` when that has at least 10 results. A new score version is published only when a kind of task gains 5 results.

**Limits.** Scores reorder only the candidates a row already holds. A candidate that keeps failing moves last and is reported; raising a floor stays the user's decision.

**Storage.** In user state, per project, outside the checkout: an append-only analysis log bounded to 60 days, and a derived score file whose digest is the score version.

### 5.10 Replay and promotion

- Each admission record stores the shortlist order it used, the score version and the generated-routes version. The strict replay feeds the recorded order back in, so it is exact and needs no data files.
- This needs a dated pre-registration edit in `docs/smart-routing-validation.md` and new fields in `tests/qualification/replay_export.py` before the canary.
- Scores are recorded from the start but not applied. A chronological replay trains on the earlier half of the history and tests on the later half.
- **The promotion test (coordinator's call).** Each task ran one candidate, so candidates are compared by their rates per kind of task, not task by task. For every kind of task where at least two candidates have enough results in the later half, the test takes the candidate the learned order puts first and the candidate the table order puts first, and compares their realised cost per accepted result in the later half. Scores are promoted only if the learned pick is at least as good in a stated share of those kinds of task. The share and the minimum samples are pre-registered in the plan before slice L3 reads the later half.
- Promotion and rollback are one-shot operator commands.

### 5.11 Optional ranking model

Built last and off by default. A lightweight model at low effort, taken from the subscription with the most headroom, may move a candidate at most two places within a row. It sees the kind of task and a candidate table, never task text. Its answer is cached per kind of task and data snapshot; a miss never delays a launch. It is capped at 20 calls a day, never uses dollars, and is switched on only if the replay of section 5.10 shows it beats the table with learned scores.

### 5.12 One ranker for guidance and routing

Worker guidance stops being a separate reading path. In every project the coordinator gets its model advice from the same shortlist. In a managed project routing also admits and funds the launch. In other projects the shortlist is advice, limited to the paths connected on the machine.

### 5.13 Removing Jev

Remove the `jev` parameter of `ensure_profile`, the `'jev'` provenance value, both control roles (`profiler` and `ranker`) with the control rows the table refresh handles for them, their tests and fixtures, and the Jev text in the 8 November design and the plan. Production never passed a Jev function, so no stored record carries the value. No ranking model runs under approach C; slice L4 adds its own control call if it is ever built. (Removing `ranker` now is the coordinator's call.)

### 5.14 No model names

- `runtime/usage_canary.py` and its reference take the lightest model the catalog lists for the provider instead of a fixed one.
- The display-name correction in `runtime/adapters/codex.py` moves to catalog data.
- The live-session runbook takes the model as a parameter.
- The covered areas are `runtime/`, `scripts/`, `policies/`, `clients/`, `skills/`, `templates/`, `AGENTS.md` and shipped JSON files. `docs/` is outside the check; its one procedure, the runbook, is fixed by hand. Tests are outside it and use invented names. (Coordinator's call.)
- `scripts/validate.py` fails when a file in the covered areas contains a model name from the downloaded catalog. The repository has no CI, so the check runs where the catalog is. On a machine with no catalog it reports the check as not run instead of passing. (Coordinator's call: a shipped list of names to search for would break the rule it enforces.)

## 6. Build order

**Foundation, before the live session**

| Slice | Content | Check |
|---|---|---|
| F1 | Task Profile template with the roster; validation and drift check; `prepare` returns missing fields | Full suite |
| F2 | Roster rename across code, policies, contracts, skills, templates and tests; old values readable in stored state | Full suite |
| F3 | Jev removal | Affected modules |
| F4 | Path rules and generated routes; runbook qualifies paths | Full suite |
| F5 | New row shape and catalog price; shortlist with value order; OpenRouter as the last class; new replay fields and the pre-registration edit; guidance rows published again | Full suite |
| F6 | Model names removed and the `validate.py` check | Affected modules |

Then one live session and the canary, on the lasting shape. Until the due jobs exist, the catalog and guides are refreshed by hand as today.

**After the canary**

| Slice | Content |
|---|---|
| L1 | Analysis rows and scores, recorded and not applied. It lands within 14 days of the canary, or the canary's admission records are exported first. |
| L2 | Due jobs, the catalog and benchmark importers, model family and the successor rule |
| L3 | Chronological replay, promotion and rollback |
| L4 | Optional ranking model |

Each slice is one PR with non-overlapping files, built and reviewed as the earlier slices were.

## 7. Who decided what

**The user decided:** the goal; the funding order; approach C with the gated ranking model; removing Jev; per-project learning with model names stored; no model names in the covered areas; family as data with the successor rule; no trials; path rules with generated routes; one roster with labels and no mapping; the template under `templates/`; the daily and weekly refresh by due job; build order 2.

**The coordinator proposed, and the user accepted with the design on 2026-11-10:**

1. The `work_kind`, `writes` and `reviews` attributes, the six work kinds, each specialist's values, and dropping `task_family` (5.2).
2. The roster of 23 plus `software-engineer`, with no per-project customization (5.2).
3. `coordinator` becomes `orchestrator`; a check run is an operation kind, not a specialist; `researcher` and `repair` disappear (5.2).
4. `worker_choices` keyed by specialist, with no migration (5.2).
5. Old role names stay readable as history and are excluded from learning (5.2).
6. List price as the common cost yardstick (5.5).
7. The two cases that open OpenRouter, `H` on the fresh reading as "nearly full", and waiting in place of the old fallback step (5.6).
8. An inherited score worth at most 5 results (5.7).
9. The learning thresholds: 5, 20, 60 days, half weight, 10 for borrowing (5.9).
10. The promotion test (5.10).
11. Removing the `ranker` control role now (5.13).
12. The areas the model-name check covers (5.14).

**Not decided here:** which API Usage switch wins, whether Claude Code becomes an advisory host, the glossary, and the OpenCode client scope. They belong to the unification and client designs.

## 8. Open checks

These are facts the plan must verify before a slice relies on them:

- Whether settled launches record window use precisely enough to learn demand per model.
- Whether each harness reports the exact model version it served, so a provider's silent update under an unchanged name is detected.
- Whether the benchmark sources can be downloaded without a credential and may be used this way. A source that fails either test is left out.
- Whether a provider's own documents state replacements anywhere, beyond the guide's positions.
- Whether the installed package ships `templates/`.
- The catalog's `family` field is uneven (one family holds 15 models) and is missing on about 5% of entries. A model without a family is never a successor.

## 9. Left to the builder

File and field names in user state, the module split, the exact digest inputs, the backoff values, the benchmark-to-`work_kind` assignment table in the importer, and the text of reason codes, within the constraints of section 3.
