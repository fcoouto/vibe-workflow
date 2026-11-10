# Smart Routing Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Smart Routing automatically select and launch the most cost-effective capable model through qualified subscription connections, using one specialist roster and model-free path rules, with an exact replay, and prepare the per-project learning loop.

**Architecture:** One JSON template (`templates/task-profile.json`) holds the Task Profile contract and the roster; every role list is replaced by it. Shipped path rules name no model; each machine generates its routes from qualified paths and the compiled ranking table, which is keyed by work kind and ordered by list price at compile time. This release admits from one subscription shortlist under the existing window-pressure allocator, or waits, and stores what strict replay needs. API Usage ships next release; OpenRouter decision and execution work are deferred separately.

**Tech Stack:** Python 3 standard library only, `unittest` through `scripts/test_sharded.py`, the existing SQLite ledger, JSON files in user state.

**Spec:** [Smart Routing redesign](../specs/2026-11-10-smart-routing-redesign-design.md), approved by the user on 10 November 2026. It amends the [8 November design](../specs/2026-11-08-usage-routing-opencode-subscriptions-design.md) and the [C.3 wiring design](../specs/2026-11-10-smart-routing-c3-wiring-design.md). The [8 November plan](2026-11-08-smart-routing.md) stays the record of the merged slices, the pre-canary requirements and the cutover tasks; this plan owns slices F1 to F8 and L1 to L4.

**Required in this release, clarified by the user on 10 November 2026:** profile the task, rank suitable models, select an eligible subscription connection, launch the admitted model with its supported effort, and record the outcome. Returning a recommendation or requiring the user to choose and launch it is insufficient. F1 supplies the profile, F4 supplies client bindings, F5 preserves subscription allocation, and F7 verifies the complete flow. Here, `gateway` means paid OpenRouter; it does not include OpenCode's included subscription allowance.

### Release scope amendment: connected subscriptions and native clients

The user's latest instruction supersedes earlier release-scope statements in this plan and the linked design: **API Usage does not ship in this release and must ship in the next release.** This explicitly resolves the earlier attribution ambiguity. OpenRouter remains deferred separately. Existing API allowance arithmetic is retained, not enabled or removed.

| Client | Required automatic selection and launch | Task owner |
|---|---|---|
| OpenCode | The suitable models available through its connected ChatGPT/Codex subscription, Claude subscription and OpenCode subscription. Discover connections and supported model/effort choices; do not hard-code this machine's old catalog. | F4.4–F4.5 |
| Codex | Only models available through its native subscription connection. Never route through Claude, OpenCode Go or paid API endpoints. | F4.4, F4.6 |
| Claude Code | Only models available through its native subscription connection. Never route through Codex, OpenCode Go or paid API endpoints. | F4.6 |

The coordinator supplies a Task Profile as part of its existing reasoning; code validates it and applies the documented floors. No separate profiling or ranking model call is introduced. Explicit user model choices remain hard constraints. Coordinator model, reasoning, permissions and global configuration remain unchanged.

**Allocation stays shared.** Retain `runtime/routing_allocator.py` and design sections 6–7: overlapping windows constrain admission together; `fundable_work = min(headroom / expected_cost)` and the binding reset horizon determine within-class pressure. Do not sum window percentages or multiply pool weight by its model count. Retain `API_share = 1 - (1 - S) * (1 - E)` and allowance protections for next release; with no eligible API route this release, API share is zero. ChatGPT access through OpenCode shares its pool with native Codex; Claude access through OpenCode shares its pool with native Claude. OpenCode's own subscription has separate capacity, never a third copy of those pools.

**Qualification is not feature scope.** Decision C permits Codex-native to remain unqualified for the scheduled OpenCode canary only. It does not permit omitting native selection/launch from the release. F4.6 must demonstrate both native-client flows before claiming release support; any missing live evidence is reported for that path, without relabeling advice as launch or expanding the current live-call budget.

**Approved simplifications, 10 November 2026.** The user accepted removing offline gateway decision/replay work, its failure taxonomy and dual shortlists; measuring subscription-only record size before any capacity increase; simplifying duplicate canary previews; focused checks per task with one full-suite gate per final slice where listed; and one independent F2 reviewer covering all affected areas. Historical replay F8 and all registered replay/canary requirements remain. Call 36 and the rest of Edit 7 remain proposals, not approved cap/reset changes or live-call authority.

**Open correctness blockers from the expanded-scope review.** F4 still needs a concrete zero-token Claude usage source and settled-window producer; harness-specific guidance/catalog, model-ID/alias and effort mappings (including effort-free compiler handling); qualified demand for native Codex, native Claude and Go; an observable Go subscription-only billing boundary excluding Extra Usage; and a supported Claude native launch bridge. Manual seeds after idle time do not satisfy routine automatic routing. Shared pools do not prove shared demand. These cuts do not resolve those blockers or establish release readiness.

## Global Constraints

- **Lightweight rule (binding).** On-demand reads only; no timers, polling or daemons; no model call to measure usage; short bounded processes that fail closed; cheap `prepare` and `check`. A task that adds a cost states it with the lighter alternative.
- **Exact replay.** The strict replay reproduces every admission record, calls no model and reads no live data.
- **File size.** Each Python file is at or under 1,000 lines. The files with a fixed ceiling in `scripts/validate.py:21-33` must not grow: `runtime/routing.py` 1506, `runtime/delegation.py` 3369, `runtime/evidence.py` 8505, `runtime/changes.py` 6074, `runtime/state.py` 2613, `runtime/usage_budget.py` 1553, `runtime/adapters/codex.py` 1579, `runtime/setup_observation.py` 1526. All but `runtime/state.py` and `runtime/setup_observation.py` (1515 lines) are exactly at their ceiling, so a change in them replaces lines and adds none. A PR that shrinks one lowers its ceiling in the same PR. `scripts/routing.py` has 940 lines; new operations go into runtime modules and the script only dispatches.
- **No model name** in `runtime/`, `scripts/`, `policies/`, `clients/`, `skills/`, `templates/`, `AGENTS.md` or shipped JSON. Tests use invented names. Dated evidence records under `docs/` keep theirs.
- **No API Usage or paid fallback this release.** No new API-spending key or organization is authorized. An already connected OpenCode subscription may use a provider-managed key; key-based authentication alone does not make included usage API Usage. F4.5 uses the client's connection without exporting its secret. No account purchase, connection change, Extra Usage enablement or live call is authorized by this plan. API Usage is required next release.
- **Stored history.** Records written before a change keep their old values. They are read as written, never rewritten and never used for learning.
- **No stored task text, finding text or file paths** in learning or replay records.
- **Tests run under a private home:** `T=$(mktemp -d); HOME="${T:?}" python3 -B scripts/test_sharded.py <modules>; rm -rf "${T:?}"`. The full suite is the same command with no module. `python3 -B scripts/validate.py` runs before every push.
- **Proportionate gates.** Run focused modules per task, covering affected consumers. For slices whose gate is the full suite, run it once on the final committed slice candidate; repeat only when changes, failures or base/integration differences invalidate that proof. The coordinator reuses matching candidate/base evidence rather than repeating an unchanged full run. Commit before checks and leave the checkout unchanged during them.
- **Delivery.** One PR per slice with the owned files listed in the slice. An Opus worker builds it and an independent Opus reviewer checks it. The coordinator verifies the exact head, runs the listed gate on the merged tree and merges with `gh pr merge --merge --match-head-commit <sha>`.

## Review Focus

Each line names a condition the design implies and the task whose tests pin it.

1. **A settings file or stored plan still uses an old role name.** It must load, match nothing new, and be reported once; it must never fail the whole settings file. Pinned in Task F2.2 (`test_legacy_worker_choice_key_is_reported_not_fatal`) and F2.3 (`test_stored_plan_with_legacy_roles_still_reads`).
2. **The template file is missing or damaged in an installed package.** `prepare` must block with one reason and admit nothing. Pinned in Task F1.1 (`test_missing_or_invalid_template_fails_closed`) and F2.2 (`test_prepare_blocks_when_the_template_is_unavailable`).
3. **A window reading is stale or absent while every other pool is full.** Admission waits with the existing reason and sends nothing paid. Pinned in F5.3 (`test_stale_or_missing_subscription_window_waits`, `test_no_feasible_subscription_never_falls_back_to_paid`). No gateway availability calculation is needed.
4. **A path is qualified but the table has no candidate for it,** or the table exists and no path is qualified. Activation must refuse with a reason that says which. Pinned in Task F4.3 (`test_activation_reports_no_generated_route`, `test_activation_reports_no_qualified_path`).
5. **A ranking table compiled before the change is still in user state.** Ranking must report it as outdated and fall back to explicit choices, never read a four-part row. Pinned in Task F2.2 (`test_table_from_the_old_schema_is_reported_outdated`).
6. **A value that only a test supplies.** A rule may read a route, a window, a host or a record field only when production writes it. The table and the traces in [Production data flows](#production-data-flows) name the writer of each, and each trace has a test that runs on the production source, the real stores and a real time gap. Pinned in `tests/test_routing_production_flows.py` (F4, F5) and `tests/test_qualification_canary.py` (F7.2).
7. **A launcher is admitted a route it cannot send.** Admission must consider only the routes of the launching host. Pinned in Task F4.4 (`test_managed_prepare_without_harnesses_is_refused`, `test_codex_routed_admission_considers_only_codex_routes`) and F5.3 (`test_opencode_ranked_admission_never_selects_a_gateway_route`).

## Slices and order

| Slice | Content | Gate | Starts after |
|---|---|---|---|
| F1 | Task Profile template with the roster, loader, generated brief section, `prepare` returns missing fields | Full suite | now |
| F3 | Jev removal | Affected modules | now, in parallel with F1 |
| F2 | Roster rename and the `work_kind` row key | Full suite, one independent reviewer | F1 and F3 |
| F4 | Path rules, generated routes, connected OpenCode subscriptions and native-only Codex/Claude launch | Full suite plus client-specific launch evidence | F2 |
| F5 | List price and value order, one subscription shortlist, existing allocation, replay fields, pre-registration Edit 7 | Full suite | F4 |
| F6 | Model names removed, `validate.py` check | Affected modules | F5 |
| F7 | Strict replay runner; canary harness in ranked mode on the admitted route | Affected modules | F6 |
| F8 | Historical replay runner | Affected modules | F5, in parallel with F6 and F7 |

The order follows the files the slices share: `runtime/routing.py`, `runtime/routing_evidence.py`, `runtime/routing_observations.py`, `scripts/routing.py`, `scripts/validate.py`, `runtime/routing_launch.py`, `runtime/opencode_launcher.py`, `runtime/delegation_routing.py`, `runtime/openrouter_worker_managed.py`, `scripts/openrouter_proposals.py`, `runtime/usage_budget.py`, `tests/test_routing_production_flows.py`, `tests/qualification/server.py` and `tests/qualification/canary.py`. Slices that run in parallel own disjoint files: F1 does not touch `scripts/validate.py` or `runtime/routing.py`, and F8 touches only its own two files and one section of the validation document.

**Differences from design section 6, and why (coordinator's calls):**

- **The new row key moves from F5 to F2.** The role is the first part of today's row key, so removing role names rewrites the key. Doing it in F5 would need an interim mapping in F2, which the design forbids.
- **F7 and F8 are new.** The 8 November plan requires a strict replay that passes before the canary and a historical replay that has run ([pre-canary item 7](2026-11-08-smart-routing.md#pre-canary-requirements)). No runner exists for either: `tests/test_routing_replay.py` replays synthetic arrivals only, and `tests/qualification/replay_export.py` only exports.
- **F6 follows F5** because the usage canary takes its model from the priced table.

## File structure

| File | Slice | Responsibility |
|---|---|---|
| `templates/task-profile.json` | F1 | The single source: contract version, fields, level definitions, floors, work kinds, roster |
| `runtime/task_profile.py` | F1, F2 | Loads and validates the template, roster accessors, renders the brief section |
| `runtime/routing_paths.py` | F4 | Path rules, path qualification record, route generation, activation data check |
| `tests/test_routing_production_flows.py` | F4, F5 | One test per production entry point, on the production source and the real stores |
| `runtime/routing_funding.py` | F5 | The ranked decision moved out of `runtime/routing.py`, one subscription shortlist, existing allocation and the admission record builder |
| `runtime/model_names.py` | F6 | Display-name lookup and the model-name check `scripts/validate.py` calls |
| `tests/qualification/replay_strict.py` | F7 | Strict replay over exported admission records |
| `tests/qualification/replay_history.py` | F8 | Advisory historical replay over past session metadata |
| `runtime/routing_learning.py` | L1 | Analysis rows and scores |
| `runtime/due_jobs.py` | L2 | Due-job check, one detached runner, backoff |

## Production data flows

Two reviews blocked this plan for one reason: a rule read a value that only a test supplied. This section is the check against that. It binds every task below.

**The method, for the builder and the reviewer of each slice:**

1. Every value a rule reads has a row in the table below that names its production writer. A rule that reads a value with no writer says so and fails closed.
2. Every collector-backed production entry point has a trace below and a test on `routing_observations.LocalSource` with real temporary stores. P1 and P6 exercise gaps longer than 900 seconds. A `StaticSource` or a hand-built route list does not prove those flows. P7 deliberately tests the separate caller-observation contract.
3. Static eligibility checks precede the shortlist cut; usage freshness and feasibility follow it. Tests prove that late refusals do not refill the cut or trigger reads for another pool (F5.2).
4. Recheck each revision's changed contracts against all consumers (including fixtures and nested launches), derived values against their producers, decision references against both plans, and new semantics against pre-registration. Mirrored outputs need explicit mappings. A writer/reader table alone does not prove these dependencies closed.

**Writers and readers after this plan:**

| Value | Production writer | Production readers | Before the release |
|---|---|---|---|
| Path rules | Shipped in `runtime/routing_routes.json` | `LocalSource`, the readers, `seed_window`, the activation gate | F4.1 baseline plus F4.5/F4.6 subscription bindings |
| Path qualification | The operator operation `qualify_path` | `LocalSource`, the dispatch checks, `seed_window`, the activation gate | The scheduled canary covers the measured OpenCode paths; additional release paths require their own evidence |
| Table strategies with `provider`, `endpoint` and `list_price_micros` | `routing_evidence.compile_table`, from the published guidance and the catalog input | `_rank`, route generation | Published again before the live session |
| Route of a ranked strategy | Generated in memory at each `prepare`, from the rules, the qualifications and the table `_rank` loaded | The collector, `routing._candidate`, the dispatch checks | No file holds it |
| Route of an explicit choice | Generated in memory from `assignment.choice` on the qualified path with its harness and provider | `routing._choice_matches`, the dispatch checks | No file holds it |
| Route of a settings worker choice | Generated in memory from the specialist's `worker_choices` | `routing._choice_matches` | No file holds it |
| The launching host's harnesses | Each launcher, through `routing_launch.prepare_request(harnesses=…)` (Task F4.4) | `routing.rank`, the collector's host filter | Required in a managed project |
| Claude pool windows | `opencode_launcher._finish` after a settled subscription launch; the operator operation `seed_window` | The collector, through `LocalSource.windows` | Nothing reads the pool without a launch. The first launch, and any launch more than 900 seconds after the last reading, waits for a seed. This predates the plan (C.3 decision 6). |
| ChatGPT pool windows | The Codex reader `codex_rate_limits.refresh`, outside `--serve`; `seed_window`; `_finish` when the harness reports a reading (not verified for this provider) | The collector | The reader runs only for a kept route on this pool |
| OpenCode subscription windows and connection identity | F4.5's bounded client usage adapter, using OpenCode's existing connection | The collector and existing window store | Includes short, weekly and monthly limits; no Console cash balance is subscription headroom |
| Native launch settings and outcome | F4.6's native host binding and native dispatch result | Shared admission/settlement and client-facing result | Native model/effort only; observed settings stay unknown where the host does not expose them |
| A window's `protected` share | None | `routing._candidate` | Always 0 |
| `not_entitled` mark | `opencode_launcher._finish`, keyed by route id and account | The collector | A strategy route and a choice route of the same model have different ids, so a mark on one does not carry to the other |
| Gateway `credit` window | **None** | The collector | A gateway route is left out as `window_unobserved` |
| OpenRouter balance observation | **None** | `_funding_verified`, `routing._candidate` | A gateway route is refused as `funding_unverified` |
| The organization a gateway pool draws on | **None** | The collector | No gateway route can be admitted |
| Replay fields of the admission record | The record builder in `runtime/routing_funding.py` (Task F5.4) | The strict replay, L1 | |
| Shared pre-window shortlist | `routing_observations.shortlist`, reused by `collect` and internal `Service.preview` | Admission and request-specific refresh need | No ledger/window read or admission during preview; approval uses paths and pinned table inputs, not six stored preview lists |
| `routes_version` | `LocalSource.routes_version` at collector preparation and preview | Admission audit; canary preflight change check, never strict-replay regeneration | `None` when the caller supplies the observations |

**The entry points.** Each test below is in `tests/test_routing_production_flows.py` unless another file is named. The task that owns each test lists it again.

- **P1, the OpenCode launcher, ranked.** `opencode_launcher.launch` reads its catalog once and passes the host's harnesses to `routing_launch.launch`. `_prepare` ranks, then asks the collector for the routes of the ranked strategies on this host. The collector reads the windows, `routing_funding.choose` picks the route, `admitted_check` checks it against the path and the catalog, and `_finish` stores the settled reading. Test (F4.4, extended in F5.3): `test_opencode_ranked_launch_on_the_local_source`. With no stored window the admission is pending with `window_unobserved`. After `seed_window` it admits a Claude subscription route. The settled reading is stored, and a second admission within 900 seconds needs no seed. After the clock moves past 900 seconds the admission is pending with `stale_observation`. No file other than the window store and the ledger was written.
- **P2, Codex native.** `delegation_routing.admit` sends the one harness `codex-native`. A routed request considers only Codex routes. An explicit or inherited model gets one choice route on the Codex path. Test (F4.4): `test_codex_explicit_choice_admits_on_a_qualified_path`, with the Codex path qualified by the test and its window seeded; and `test_codex_routed_admission_considers_only_codex_routes`.
- **P3, OpenRouter workers and proposals.** Both send an explicit choice with the coordinator's endpoint. The choice route carries that endpoint, and the dispatch check compares it. Tests (F4.2): `test_openrouter_choice_keeps_its_endpoint_on_the_local_source`, and `test_gateway_route_without_a_balance_producer_stays_out_in_production`: with a qualified path and no other state, admission fails at the first missing input, normally `integration_disabled`. With synthetic local integration readiness only, it reaches `window_unobserved`; supplying a credit window without a balance observation still cannot admit. Do not use live secrets or a `StaticSource` to prove this flow. F4.4 also traces the worker's nested OpenCode preparation and verifies refusal plus settlement without lifting `API_FUNDING_QUALIFIED`; this closes the failure path, not the deferred gateway consumer.
- **P4, a settings worker choice.** With Smart Routing off, or with an empty ranking, `routing._decide` takes the specialist's `worker_choices`. Test (F4.2): `test_settings_choice_admits_without_a_table`: no table file, one qualified path, a seeded window; the admission is in `worker_choice` mode.
- **P5, the operator operations.** `qualify_path`, `unqualify_path`, `seed_window` and activation. Tests (F4.2, F4.3): `test_seed_needs_a_qualified_path_not_a_table`, `test_activation_gate_reads_the_services_settings_path`, `test_unqualified_path_refuses_a_held_admission_at_dispatch`.
- **P6, the canary harness.** Test (F7.2, in `tests/test_qualification_canary.py`): `test_canary_runs_on_the_local_source_across_a_stale_gap`.
- **P7, `prepare` where the caller supplies the observations** (a project that is not managed; the pilot ran this way). No collector runs, so no route is generated and no host rule applies. Test (F5.4): `test_unmanaged_prepare_record_has_null_routes_version`.
- **P8, OpenCode's own subscription.** F4.5's `test_opencode_subscription_ranked_launch_on_the_local_source` exercises a connected included-allowance route through discovery, fresh usage, the existing allocator and `opencode_launcher.launch`; a disconnected, exhausted or paid-overflow route sends nothing. It includes a third-provider candidate so a two-provider-only implementation cannot pass.
- **P9, native-client launch.** F4.6's `test_native_clients_rank_and_launch_on_the_local_source` exercises the same task at low and high capability requirements on Codex and Claude Code. Each host selects only its native catalog, dispatches the chosen settings and records completion. A test that stops at an admission response does not prove this trace.

**Pre-release scope: A/C approved; B clarified as required ranked subscription launch:**

- **A gateway admission.** Nothing writes the `credit` window, the balance observation or the organization. This predates the plan: the managed OpenRouter hosts have never admitted outside their self-checks. The user approved deferring production OpenRouter in decision A under [Coordinator calls](#coordinator-calls-for-the-users-review).
- **The OpenRouter decision layer.** Deferred with production execution under the approved cuts. No gateway shortlist, pool-state calculation or new gateway replay fields ship. Existing dollar-host refusals, explicit-choice compatibility and settlement/recovery remain; `opencode_launcher.API_FUNDING_QUALIFIED` stays `False`.
- **Native-client qualification in the scheduled canary.** The Codex path may stay unqualified under decision C for that session. F4.6 still delivers native-only routing for Codex and Claude Code; separate missing evidence blocks the affected release claim, not unrelated implementation.
- **A read of the Claude pool without a launch.**

---

## Slice F1: Task Profile template

**Owned files:** `templates/task-profile.json`, `runtime/task_profile.py`, `tests/test_task_profile.py`, `templates/worker-brief.md`, `scripts/routing.py` (`_prepare` only), `tests/test_routing_command.py`, `clients/delegation-requests.md`, `docs/policy-sources.md`. F1 touches neither `runtime/routing.py` nor `scripts/validate.py`, which F3 owns while the two run in parallel.

### Task F1.1: The template and its loader

**Files:**
- Create: `templates/task-profile.json`, `runtime/task_profile.py`, `tests/test_task_profile.py`

**Interfaces:**
- Produces, in `runtime/task_profile.py`:
  - `TEMPLATE: Path` (the package's `templates/task-profile.json`)
  - `class TemplateUnavailable(ValueError)`
  - `load(path=TEMPLATE) -> dict`, validated and cached by path, size and modification time; raises `TemplateUnavailable`
  - `roster() -> dict[str, dict]`, id to entry, in template order
  - `specialist(identifier) -> dict | None`, roster only

**Template shape (fixed):**

```json
{
  "contract_version": 2,
  "fields": {
    "role": {"required": true, "values": "roster", "meaning": "the specialist id"},
    "complexity_band": {"values": ["low", "medium", "high", "critical"], "floor": "high"},
    "risk_band": {"values": ["low", "medium", "high", "critical"], "floor": "high"},
    "review_policy": {"values": ["none", "self", "independent", "strict"], "floor": "independent"},
    "uncertainty": {"values": ["low", "medium", "high"]},
    "context_tokens": {"type": "integer", "minimum": 0, "maximum": 1000000000},
    "tools": {"type": "list"}
  },
  "write_floors": {"complexity_band": "medium", "risk_band": "medium"},
  "levels": {"complexity_band": {}, "risk_band": {}},
  "work_kinds": ["implementation", "review", "investigation", "planning", "testing", "writing"],
  "roster": []
}
```

The specialist is stored under the key `role`, as today; only its values change (see [Coordinator calls](#coordinator-calls-for-the-users-review), call 5). The template's `fields.role.values` is `roster`; the reserved value `check` of Task F2 is not a specialist and is not in the template.

**Level text (fixed copy).** Each `levels` entry is `{"definition": ..., "example": ...}`:

| Field | Level | Definition | Example |
|---|---|---|---|
| complexity | low | One file or one mechanical change with an obvious solution. | Fix a typo, rename a local variable, update a constant. |
| complexity | medium | A few files in one component, following a pattern that already exists. | Add an option to an existing command, with its test. |
| complexity | high | Several components, a new design choice, or behaviour that is hard to test. | Add a module with its own state and callers in two components. |
| complexity | critical | A change across shared core, concurrency or stored formats, where a wrong design is costly to undo. | Change the ledger's reservation rules or a stored record format. |
| risk | low | A mistake is caught by existing checks and affects only this task. | Documentation, comments, test-only changes. |
| risk | medium | A mistake could break one feature and is easy to revert. | A change to one command's output or validation. |
| risk | high | A mistake could lose data, spend money, weaken a safety rule or break many callers. | Funding, permissions, migrations, shared state. |
| risk | critical | A mistake could be irreversible or expose a secret. | Credential handling, destructive operations, release steps. |

**Roster (fixed copy).** `work_kind`, `writes` and `reviews` are the values of [design section 5.2](../specs/2026-11-10-smart-routing-redesign-design.md#52-specialist-roster). The `when_to_use` sentences follow the toolkit's roster at commit `790a895` (`docs/tools/02-subagent-orchestration.md`, Part A), written in this repository's wording because the repository rule is to write local contracts and not copy toolkit text:

| `id` | `label` | `when_to_use` |
|---|---|---|
| `orchestrator` | Orchestrator | Coordinates a task that spans several areas and delegates each part to other specialists. |
| `code-reviewer` | Code Reviewer | Reviews any code change for bugs, error handling and test coverage. |
| `security-reviewer` | Security Reviewer | Reviews a change for common web vulnerabilities, committed secrets, authentication flaws and vulnerable dependencies. |
| `typescript-reviewer` | TypeScript Reviewer | Reviews TypeScript or JavaScript for type safety, async correctness, injection and prototype pollution. |
| `react-reviewer` | React Reviewer | Reviews React code for hook rules, server and client component boundaries, accessibility and render performance. |
| `react-build-resolver` | React Build Resolver | Fixes a broken build or development server: bundler configuration, compile errors, missing types. |
| `test-engineer` | Test Engineer | Writes unit and integration tests, test first, including edge cases. |
| `qa-automation-engineer` | QA Automation Engineer | Writes end-to-end tests and pipeline quality gates for a critical user flow. |
| `database-architect` | Database Architect | Designs schemas, migrations, indexes and query strategy. |
| `devops-engineer` | DevOps Engineer | Works on deployment, pipelines, infrastructure and production operations. |
| `backend-specialist` | Backend Specialist | Builds API endpoints, server-side business logic and data persistence. |
| `frontend-specialist` | Frontend Specialist | Builds interface components, layout, styling and frontend structure. |
| `seo-specialist` | SEO Specialist | Works on metadata, structured data, search indexing and visibility in AI search. |
| `performance-optimizer` | Performance Optimizer | Removes a bottleneck that profiling has already identified. |
| `software-engineer` | Software Engineer | Implements a change that no narrower specialist covers. |
| `product-manager` | Product Manager | Clarifies undefined or ambiguous requirements before a work item exists. |
| `product-owner` | Product Owner | Turns a business goal into acceptance criteria for a work item. |
| `project-planner` | Project Planner | Breaks a feature into ordered, executable tasks. |
| `code-archaeologist` | Code Archaeologist | Explains legacy or undocumented code before it is changed. |
| `debugger` | Debugger | Finds the root cause of a bug, crash or flaky test before any fix is proposed. |
| `explorer-agent` | Explorer Agent | Maps an unfamiliar or complex codebase before a change is planned. |
| `documentation-writer` | Documentation Writer | Writes or updates READMEs, API documentation and runbooks on request. |
| `penetration-tester` | Penetration Tester | Simulates attacks against a real authentication flow or release. |
| `security-auditor` | Security Auditor | Reviews defence in depth and models threats before an important release. |

**Validation rules in `load`:** the file is at most 16 KB; `contract_version` is an integer; every `levels` field has exactly the field's values, each with a non-empty definition and example of at most 200 characters; roster ids are unique and match `[a-z][a-z0-9-]{1,63}`; `label` is non-empty; `when_to_use` is at most 200 characters; `work_kind` is in `work_kinds`; `writes` and `reviews` are booleans; no entry both writes and reviews; `reviews` is true exactly when `work_kind` is `review`, which is what lets the ledger stage follow the work kind (Task F2.3).

- [ ] **Step 1: Write the failing tests** in `tests/test_task_profile.py`:

```python
class TemplateTests(unittest.TestCase):
    def test_template_pins_the_routing_constants(self):
        fields = task_profile.load()['fields']
        self.assertEqual(tuple(fields['complexity_band']['values']), routing.COMPLEXITY_BANDS)
        self.assertEqual(tuple(fields['risk_band']['values']), routing.RISK_BANDS)
        self.assertEqual(tuple(fields['review_policy']['values']), routing.REVIEW_POLICIES)
        self.assertEqual(tuple(fields['uncertainty']['values']), routing.UNCERTAINTY)
        for name in ('complexity_band', 'risk_band', 'review_policy'):
            self.assertEqual(fields[name]['floor'], routing.FLOORS[name])
        self.assertEqual(task_profile.load()['write_floors'], routing.WRITE_FLOORS)

    def test_roster_is_the_24_specialists(self):
        roster = task_profile.roster()
        self.assertEqual(len(roster), 24)
        self.assertEqual(roster['code-reviewer']['label'], 'Code Reviewer')
        self.assertEqual((roster['debugger']['work_kind'], roster['debugger']['writes'], roster['debugger']['reviews']),
                         ('investigation', False, False))
        self.assertIsNone(task_profile.specialist('worker'))

    def test_every_level_has_a_definition_and_an_example(self): ...
    def test_template_without_the_roster_stays_under_3_kb(self): ...
    def test_missing_or_invalid_template_fails_closed(self):
        # absent file, invalid JSON, a duplicate roster id, an unknown work kind, a file over 16 KB
        # each raises task_profile.TemplateUnavailable
    def test_template_ships_in_the_package(self):
        self.assertIn('templates/task-profile.json', package_manifest.export_members(ROOT))
```

- [ ] **Step 2: Run** `scripts/test_sharded.py test_task_profile`. Expected: FAIL, `No module named 'runtime.task_profile'`.
- [ ] **Step 3: Write the template and `runtime/task_profile.py`** with the interfaces above. Read the JSON directly; `scripts/read_instructions.py` accepts only Markdown templates.
- [ ] **Step 4: Run** `scripts/test_sharded.py test_task_profile test_package_manifest test_package`. Expected: OK.
- [ ] **Step 5: Commit** `feat(routing): task profile template with the specialist roster`.

### Task F1.2: Generated brief section and drift check

**Files:**
- Modify: `runtime/task_profile.py`, `templates/worker-brief.md`, `clients/delegation-requests.md:97-102`, `docs/policy-sources.md`
- Test: `tests/test_task_profile.py`

**Interfaces:**
- Produces: `BRIEF_START = '<!-- task-profile:start (generated from templates/task-profile.json) -->'`, `BRIEF_END = '<!-- task-profile:end -->'`, `render_brief(template: dict) -> str`, `brief_drift(root: Path) -> list[str]` (empty when `templates/worker-brief.md` holds exactly the rendered block between the markers).

In F1 the block has a `- Task profile:` line, the level table above and the floors, and a test pins the drift. F2 adds the specialist line and the roster table to the same block, and Task F2.5 adds the `scripts/validate.py` hook.

- [ ] **Step 1: Write the failing tests** `test_brief_section_matches_the_template` (`brief_drift(ROOT) == []`) and `test_brief_drift_is_reported` (a copy with one edited definition returns one message naming `templates/worker-brief.md`).
- [ ] **Step 2: Run** them. Expected: FAIL, `render_brief` is not defined.
- [ ] **Step 3: Implement** `render_brief` and `brief_drift`; add the block to `templates/worker-brief.md`. Replace the field list in `clients/delegation-requests.md` with a pointer to the template, keeping its rules (unknown fields rejected, floors, separate from guidance `profile_ids`). Add the roster's source row to `docs/policy-sources.md`.
- [ ] **Step 4: Run** `scripts/test_sharded.py test_task_profile test_prepared_instructions` and `scripts/validate.py`. Expected: OK, exit 0.
- [ ] **Step 5: Commit** `feat(routing): generate the brief's task profile section and pin its drift`.

### Task F1.3: `prepare` returns the missing fields

**Files:**
- Modify: `scripts/routing.py:420-468` (`_prepare`)
- Test: `tests/test_routing_command.py`

**Interfaces:**
- Produces: every `prepare` answer that built a profile (`admitted`, `pending`, and `blocked` after the profile step) carries `missing_fields: list[str]`, the profile's `uncertain_fields`. It never blocks because of them.

- [ ] **Step 1: Write the failing tests** `test_prepare_reports_missing_profile_fields` (a job with no profile: the answer includes `complexity_band`, `risk_band` and `review_policy` in `missing_fields`, and its state is not `blocked`) and `test_prepare_reports_no_missing_fields_for_a_complete_profile` (`missing_fields == []`).
- [ ] **Step 2: Run** `scripts/test_sharded.py test_routing_command`. Expected: FAIL, `KeyError: 'missing_fields'`.
- [ ] **Step 3: Implement** by adding the key to the answers `_prepare` builds, in at most six added lines.
- [ ] **Step 4: Commit** `feat(routing): prepare reports the profile fields that took floors`.
- [ ] **Step 5: Run** F1's full-suite gate and `scripts/validate.py` on that final committed slice candidate. Expected: OK.

---

## Slice F3: Jev removal

**Owned files:** `runtime/routing.py`, `runtime/routing_evidence.py`, `scripts/routing_table.py`, `tests/test_routing_profiles.py`, `tests/test_routing_replay.py`, `tests/test_smart_routing_features.py`, `tests/test_routing_evidence.py`, `tests/test_routing_table.py`, `tests/fixtures/smart-routing/guidance.json`, `tests/fixtures/smart-routing/terms.json` (deleted), `clients/guidance-publication.md`, the 8 November design, `scripts/validate.py` (ceiling only).

### Task F3.1: Remove Jev and the control roles

**Interfaces:**
- Produces:
  - `routing.ensure_profile(job) -> dict`, with no `jev` parameter; `routing._jev_view` is gone; `routing.PROVENANCE == ('coordinator', 'metadata', 'floor')`.
  - `routing_evidence.compile_table(guidance, catalog, benchmarks, *, now=None)`. `CONTROL_ROLES`, `_terms`, `_control_allowed` and the `control_plane_terms` drop reason are gone.
  - `scripts/routing_table.py`: `INPUTS = ('guidance', 'catalog', 'benchmarks')`; a `terms.json` left in the sources folder is ignored.
- The accounting stages `profiling` and `routing` in `usage_budget.STAGES` stay, because stored ledger events may carry them.

- [ ] **Step 1: Change the tests first.** Delete the Jev cases and `CountingJev`; add `test_ensure_profile_takes_no_jev` (`ensure_profile(job, jev=lambda *a: {})` raises `TypeError`), `test_provenance_has_no_jev` and `test_control_rows_are_rejected` (a guidance row with role `profiler` or `ranker` raises the compiler's "Unknown row role" error).
- [ ] **Step 2: Run** `scripts/test_sharded.py test_routing_profiles test_routing_evidence test_routing_table test_routing_replay test_smart_routing_features`. Expected: FAIL in the three new tests.
- [ ] **Step 3: Remove the code,** the fixture's control rows and `terms.json`. Lower `runtime/routing.py`'s ceiling in `scripts/validate.py` to its new line count.
- [ ] **Step 4: Update the documents.** In `clients/guidance-publication.md` remove the terms input from "Strategy table refresh". In the 8 November design mark sections 5.1, 5.3 and 11 as replaced by the redesign, with a link; do not rewrite them.
- [ ] **Step 5: Run** the modules of step 2 plus `test_line_ceilings test_prepared_instructions`, then `scripts/validate.py`. Expected: OK, and `grep -rni jev runtime scripts policies clients skills templates` prints nothing.
- [ ] **Step 6: Commit** `refactor(routing): remove Jev and the control roles`.

---

## Slice F2: Roster rename and the work-kind row key

Shared core. One PR with the commits in task order; focused checks run per task and the full suite passes on the final committed candidate. One independent reviewer covers `runtime/`, `scripts/`, contracts, documents and tests, including stored-state compatibility. Each commit's evidence names its passing modules.

**The rules for the whole slice:**

| Today | After F2 |
|---|---|
| Job and record field `role` with `worker`, `review`, `coordinator`, `check` | The field stays `role`. Its value is a roster id, or the reserved value `check` for a check run, which uses no model and is not a specialist (see [call 15](#coordinator-calls-for-the-users-review)). |
| A branch on `role == 'review'` | `task_profile.reviews(role)` |
| A branch on `role == 'worker'` or membership in `NATIVE_ROLES` | `task_profile.routed(role) and not task_profile.reviews(role)`, or `task_profile.routed(role)` |
| `role == 'coordinator'` | `task_profile.orchestrates(role)`, true for `orchestrator` and for the stored value `coordinator`. New records write `task_profile.ORCHESTRATOR`. |
| `role == 'check'` | `role == task_profile.CHECK`; the value does not change |
| A review job's claims must be read-only | Every specialist with `writes: false` must have read-only claims |
| Profile field `task_family`, floor `general` | Gone. The profile carries `work_kind`, taken from the specialist. A supplied `task_family` is accepted and ignored. |
| Row key `role\|task_family\|complexity\|risk` | `work_kind\|complexity\|risk` |
| `worker_choices` keyed by `implementer`, `reviewer`, `researcher`, `repair` | Keyed by roster id |
| Admission `bucket = [role, complexity]` | `bucket = [work_kind, complexity]` |
| Visibility `role` from seven labels | A roster id; the visible label is the roster `label` |

New input is checked against the roster only. Stored records are read through `task_profile.stored`, which also knows the seven old names.

Three names look like roles and are not, so they stay: the operation kinds `coordinator-work` and `operation-coordinator`, the provenance value `coordinator`, and the accounting stages in `usage_budget.STAGES`.

### Task F2.1: Roster accessors

**Files:**
- Modify: `runtime/task_profile.py`
- Test: `tests/test_task_profile.py`

**Interfaces:**
- Produces:
  - `ORCHESTRATOR = 'orchestrator'`, `CHECK = 'check'`
  - `routed(identifier) -> bool`: a roster id other than the orchestrator
  - `orchestrates(identifier) -> bool`: `orchestrator` or the stored value `coordinator`
  - `reviews(identifier) -> bool`, `writes(identifier) -> bool`, `work_kind(identifier) -> str | None`; each answers for roster ids and, through `stored`, for old names
  - `stored(identifier) -> dict | None`: the roster entry, or for an old name a fixed entry with `legacy: True`. The old names: `worker`, `implementer`, `repair` (implementation, writes); `review`, `reviewer` (review, reviews); `researcher` (investigation); `coordinator` (the orchestrator entry).
  - `LEGACY: tuple[str, ...]`, those seven names. No other module defines them.

- [ ] **Step 1: Write the failing tests** `test_attributes_answer_for_roster_ids`, `test_old_names_read_as_history` (`stored('review')['legacy'] is True`, `reviews('review')`, `orchestrates('coordinator')`, `specialist('review') is None`), `test_check_is_reserved_and_not_a_specialist` (`specialist('check') is None`, `routed('check') is False`, `stored('check') is None`) and `test_unknown_names_have_no_attributes` (`stored('wizard') is None`, `routed('wizard') is False`).
- [ ] **Step 2: Run** `scripts/test_sharded.py test_task_profile`. Expected: FAIL, attributes not defined.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** again. Expected: OK.
- [ ] **Step 5: Commit** `feat(routing): roster accessors and the one reader for old role names`.

### Task F2.2: Routing core, table and settings

**Files:**
- Modify: `runtime/routing.py:26-236,243-320,549-613`, `runtime/routing_evidence.py:133-188`, `runtime/routing_settings.py:14-18,57-72,201-214`, `scripts/routing.py`, `scripts/routing_table.py`, `tests/fixtures/smart-routing/*.json`
- Test: `tests/test_routing_profiles.py`, `tests/test_routing_ranker.py`, `tests/test_routing_evidence.py`, `tests/test_routing_settings.py`, `tests/test_routing_command.py`, `tests/test_routing_admission.py`, `tests/test_task_profile.py`

**Interfaces:**
- Produces:
  - `routing.CONTRACT_VERSION == 2`; `routing.ROLES` is gone; `routing.FLOORS == dict(complexity_band='high', risk_band='high', review_policy='independent')`; `SEMANTIC_FIELDS == ('complexity_band', 'risk_band')`.
  - `routing.ensure_profile(job)` requires `task_profile.specialist(job['role'])` and raises `ProfileUnavailable` otherwise, including when the template is unavailable. The profile it returns has `role` and `work_kind` and no `task_family`.
  - `routing.row_key(work_kind, complexity_band, risk_band) -> str`.
  - `routing.resolve_row(table, profile)`: the exact row, then the nearest stricter band for the same work kind, else `no_recommendation`. `ranking_conservative` is gone.
  - `routing.TABLE_SCHEMA == 2` and `routing_evidence.SCHEMA = routing.TABLE_SCHEMA` (the evidence module imports `routing`, not the reverse). `routing.rank` adds the reason `table_outdated` and returns no strategy when the table is not empty and its `schema` differs. An empty table still reports `table_empty`.
  - A guidance row is `{work_kind, complexity_band, risk_band, strategies}`; a row with `role` or `task_family` raises "Invalid guidance row". A benchmark matches on `work_kind`.
  - `routing_settings`: `ROLES`, `PROFILE_ROLES` and `EXPLICIT_ROLES` are gone. `set` accepts `worker_choices` keys that are routed roster ids, else `invalid_worker_choices`. On load, any other key is left in the file, dropped from the resolved values and listed in `settings_warnings: [{'code': 'worker_choice_key_unknown', 'key': <key>}]`. `assignment_role(profile_role, explicit=None)` returns the profile role, raises `coordinator_not_routed` for the orchestrator and `role_mismatch` when `explicit` differs.
  - `scripts/routing.py`: `check` and `status` return `settings_warnings`; `PROFILE_FIELDS` has `work_kind` in place of `task_family`; the assignment passed to admission carries `work_kind`, which admission uses for the bucket.

**Cost:** the template is read and validated once per process and re-checked with one `stat` call per use. Lighter alternative: compile the roster into a Python constant; rejected because the template must stay the single source.

- [ ] **Step 1: Change the tests first.** Fixtures use roster ids and work kinds. New tests:
  - `test_profile_needs_a_roster_specialist` (`role='worker'` raises `ProfileUnavailable`)
  - `test_prepare_blocks_when_the_template_is_unavailable` (`profile_unavailable`, nothing admitted)
  - `test_supplied_task_family_is_ignored` (no `profile_invalid`, no `task_family` in the result)
  - `test_row_key_has_three_parts`, `test_no_weaker_or_other_work_kind_row_is_used`
  - `test_table_from_the_old_schema_is_reported_outdated`, `test_empty_table_is_still_reported_empty`
  - `test_legacy_worker_choice_key_is_reported_not_fatal` (a file with `implementer` loads; `settings_warnings` names it; a prepare for `software-engineer` does not use it)
  - `test_set_refuses_a_non_roster_worker_choice_key`
  - `test_bucket_is_work_kind_and_complexity`
  - In `tests/test_task_profile.py`, tighten the pin: `routing.FLOORS` equals the template's floors exactly, and `routing.CONTRACT_VERSION == load()['contract_version']`.
- [ ] **Step 2: Run** the test modules above. Expected: FAIL in the new tests.
- [ ] **Step 3: Implement.** Keep `runtime/routing.py` at or below its ceiling.
- [ ] **Step 4: Run** the same modules. Expected: OK.
- [ ] **Step 5: Commit** `refactor(routing): profiles, rows and worker choices use the roster`.

### Task F2.3: Delegation, ledger stages and the other role constants

**Files:**
- Modify: `runtime/delegation.py`, `runtime/delegation_routing.py:42,165,330,456`, `runtime/routing_launch.py:42,405,428`, `runtime/opencode_launcher.py:853`, `runtime/usage_budget.py:84` (comment only), `runtime/openrouter_worker_managed.py:28`, `scripts/openrouter_proposals.py:551-554`, `runtime/changes.py`, `runtime/evidence.py`, `runtime/adapters/codex.py:1047,1513`, `runtime/setup_observation.py:1222`
- Test: `tests/test_delegation*.py`, `tests/fixture_delegation.py`, `tests/test_routing_accounting*.py`, `tests/test_routing_launch.py`, `tests/test_openrouter_*.py`, `tests/test_workstreams.py`
- Test: `tests/test_usage_routing.py` (its launcher profile and role fixtures also follow the roster).

**The sites.** The builder lists them with this command and replaces each in place by the rule table above; the PR description carries the list with one line per site:

```bash
grep -nE "'(worker|review|coordinator|implementer|reviewer|researcher|repair)'" \
  runtime/delegation.py runtime/evidence.py runtime/changes.py runtime/delegation_routing.py \
  runtime/routing_launch.py runtime/opencode_launcher.py runtime/adapters/codex.py runtime/setup_observation.py
```

On `main` at `d36da833` they include, in `runtime/delegation.py`: the plan job roles (:940-955), the coordinator claims (:973), `NATIVE_ROLES` (:1021 and its 14 uses), the assignment record (:1111-1124), the verifier and coordinator bindings (:1148, :1211-1215), :1517, the coordinator job selection (:1819-1856) and `_reserve_operation` (:1874-2028). In `runtime/evidence.py`: :1097, :2993. In `runtime/changes.py`: :2392. The sites that compare with `'check'` (`delegation.py:1225,1716-1721,1912,1980,2028,3182`, `evidence.py:5396-5415`) do not change.

`runtime/delegation.py`, `runtime/evidence.py` and `runtime/changes.py` have no spare line. Every replacement is on its own line, and the one new import joins an existing import line.

**Interfaces:**
- Produces:
  - `delegation.NATIVE_ROLES`, `delegation_routing.ROLES`, `routing_launch.PROFILE_ROLE` and `openrouter_worker_managed.ROLE` are gone.
  - A new delegation plan accepts a job whose `role` is a roster id or `check`. A stored plan or record with an old name still reads and behaves as before, including the attempt count of a stored `coordinator` job.
  - The ledger stage of an admission stays `usage_budget.ROLE_STAGES.get(bucket[0], 'worker')`. A new bucket starts with the work kind, so `review` gives the stage `review` and every other work kind gives `worker`. The map keeps its old keys, because stored buckets start with an old role name. `usage_budget.STAGES` is unchanged. A repair is another attempt of the same job.
  - The Codex adapter's native task name keeps its two prefixes, `worker_` and `review_`. They are name markers: the caller passes `review` when `task_profile.reviews(role)`, else `worker`.
  - OpenRouter proposals use the specialist `explorer-agent`. An OpenRouter worker uses the specialist its brief names, and `software-engineer` when it names none. Self-checks use `software-engineer`.

- [ ] **Step 1: Change the fixtures and tests first.** New tests in `tests/test_delegation.py`: `test_plan_accepts_roster_specialists_and_check_jobs`, `test_new_plan_refuses_an_old_role_name`, `test_stored_plan_with_legacy_roles_still_reads` (write a plan artifact with `worker`, `review`, `coordinator` and `check`, then read the task: no error, same behaviour as before), `test_stored_coordinator_attempts_still_count` (a stored `coordinator` record counts against the orchestrator job's `max_attempts`), `test_non_writing_specialist_needs_read_only_claims` (`debugger` with a write claim is refused). In `tests/test_routing_accounting.py`: `test_stage_follows_the_buckets_work_kind` (`['review', 'low']` gives `review`; `['implementation', 'low']` and `['investigation', 'low']` give `worker`; the stored `['reviewer', 'low']` still gives `review`).
- [ ] **Step 2: Run** `scripts/test_sharded.py 'test_delegation*' 'test_routing_accounting*' test_routing_launch`. Expected: FAIL in the new tests.
- [ ] **Step 3: Implement.** The oversized files must end at or below their ceilings.
- [ ] **Step 4: Run** the same modules plus `test_workstreams 'test_openrouter*' test_evidence test_changes test_usage_codex_host test_usage_routing`. Expected: OK.
- [ ] **Step 5: Commit** `refactor(delegation): jobs, stages and launchers use the roster`.

### Task F2.4: Visible labels

**Files:**
- Modify: `runtime/worker_visibility.py:15-34`, `clients/worker-visibility.md`, `skills/openrouter-workers/references/procedure.md:79`
- Test: `tests/test_worker_visibility.py`, `tests/fixture_worker_visibility.py`, `tests/test_native_visibility_assignment.py`

**Interfaces:**
- Produces: `worker_visibility.ROLES` is gone. A declaration is `{"version": 2, "role": <roster id>, "specialty": ..., "custom_role_name"?: ...}`. The displayed role is the roster `label`, so a badge reads `Code Reviewer · <model display name>`. A stored version 1 declaration is shown as written; a new version 1 declaration is refused with `invalid_visibility_declaration`.

- [ ] **Step 1: Write the failing tests** `test_declaration_takes_a_roster_id_and_shows_its_label`, `test_new_version_1_declaration_is_refused`, `test_stored_version_1_declaration_is_shown_as_written`.
- [ ] **Step 2: Run** `scripts/test_sharded.py test_worker_visibility test_native_visibility_assignment`. Expected: FAIL.
- [ ] **Step 3: Implement** and rewrite the role paragraph of `clients/worker-visibility.md` (lines 15-34).
- [ ] **Step 4: Run** again. Expected: OK.
- [ ] **Step 5: Commit** `refactor(visibility): worker labels come from the roster`.

### Task F2.5: Policies, contracts, skills, brief and the vocabulary gate

**Files:**
- Modify: `policies/03-agent-delegation.md`, `clients/*.md`, `skills/**/*.md` that name a role, `templates/worker-brief.md` (regenerated), `runtime/task_profile.py` (`render_brief` adds the `- Specialist:` line and the roster table), `tests/qualification/canary.py:49-50` and the other harness files that name a role, `docs/policy-sources.md`, `scripts/validate.py` (the drift hook and the ceilings)
- Create: `tests/test_roster_vocabulary.py`

**Interfaces:**
- Produces:
  - `scripts/validate.py` raises `ValueError` with the message of `task_profile.brief_drift` when the brief's block differs from the template.
  - `tests/test_roster_vocabulary.py` scans `*.py`, `*.js`, `*.mjs`, `*.ts`, `*.json` and `*.md` under `runtime/`, `scripts/`, `policies/`, `clients/`, `skills/` and `templates/` for six names written as a quoted or backticked value: `worker`, `review`, `implementer`, `reviewer`, `researcher`, `repair`. `coordinator` and `check` are outside the gate: the first is also the product's word for the user's main session and a provenance value, the second is an operation kind. `ALLOWED` is a tuple of `(path, name, reason)`; it starts with `runtime/task_profile.py` (the reader), `runtime/usage_budget.py` (accounting stages and stored bucket labels) and `runtime/adapters/codex.py` (the two task-name prefixes). Each further entry needs a reason that names the non-role meaning.

- [ ] **Step 1: Write the failing tests** `test_no_old_role_name_outside_the_allowlist` (expected: FAIL, listing every remaining use) and, in `tests/test_task_profile.py`, `test_validate_fails_on_brief_drift`.
- [ ] **Step 2: Rewrite** policy 03's specialist selection (choose the specialist by its `when_to_use` sentence in `templates/task-profile.json`; the orchestrator is never routed), the client contracts and the skills. Regenerate the brief block. In the canary harness use `software-engineer`, `code-reviewer` and `explorer-agent`.
- [ ] **Step 3: Check the instruction paths** with three representative tasks, as `AGENTS.md` requires: writing a worker brief, setting a worker choice, and reading a stored task with old roles. Record in the PR which instructions loaded.
- [ ] **Step 4: Lower every ceiling whose file shrank and commit** `docs(routing): one roster in policies, contracts and skills, with a vocabulary gate`.
- [ ] **Step 5: Run** F2's full-suite gate and `scripts/validate.py` on that final committed slice candidate. Expected: OK, exit 0.

---

## Slice F4: Path rules and generated routes

**Owned files:** `runtime/routing_routes.json`, `runtime/routing_paths.py`, `runtime/routing_observations.py`, `runtime/routing_evidence.py` (strategy `provider` and `endpoint`), `runtime/codex_rate_limits.py`, `runtime/routing_launch.py`, `runtime/opencode_launcher.py`, `runtime/delegation_routing.py`, `runtime/openrouter_worker_managed.py`, `scripts/openrouter_proposals.py`, `runtime/routing.py` (`_valid_assignment` and `_valid_observations` only, replacing lines), `scripts/routing.py` (`CAPABILITIES`, `ASSIGNMENT_FIELDS`, `_rank`, `_collect`, `_prepare`, `_self_check`, `activation_gate`, the `qualify_path` and `unqualify_path` operations), `tests/test_routing_paths.py`, `tests/test_routing_production_flows.py`, `tests/test_routing_observations.py`, `tests/test_routing_managed_projects.py`, `tests/test_routing_command.py`, `tests/test_routing_codex_rate_limits.py`, `tests/test_routing_launch.py`, `tests/test_delegation_routing.py`, `tests/test_opencode_launcher.py`, `tests/test_openrouter_workers_managed.py`, `tests/test_openrouter_proposals_managed.py`, `tests/test_qualification_demand.py`, `tests/qualification/demand.py`, `tests/qualification/live_modes.py` (its one `load_manifest` call), `tests/qualification/server.py` (the harness set in collector mode), `tests/openrouter_fixtures.py`, `docs/smart-routing-qualification.md`, `docs/smart-routing-capability-matrix.md`, the C.3 wiring design (decision 5 marked replaced).

F4 reads [Production data flows](#production-data-flows) first. Its routes exist only in memory: generation and route lookup write nothing. Existing window refresh and ledger writes retain their separate contracts.

**Additional owned consumers:** `tests/test_routing_seed_window.py`, `tests/test_smart_routing_features.py`, `tests/test_usage_routing.py`, `tests/qualification/gateway.py`, `tests/test_qualification_gateway.py`, `tests/qualification/managed_state.py`. Every collector-mode `prepare` fixture supplies `constraints.allowed_harnesses`; every direct `prepare_request` caller supplies its required `harnesses`. The seed tests use `seedable`, and the qualification fixtures retain their fixed `StaticSource` routes through the new source interface. F4 owns these adaptations, not a later slice.

**Release-amendment ownership:** F4 also owns `runtime/opencode_subscription.py`, `runtime/native_routing.py`, `runtime/adapters/opencode.py`, `runtime/adapters/opencode_plugin.ts` (identity parity only), `tests/test_opencode_subscription.py`, `tests/test_native_routing.py`, `tests/test_opencode_plugin.py`, `clients/opencode.md`, `clients/codex.md`, `clients/claude-code.md`, `policies/03-agent-delegation.md`, and `docs/policy-sources.md`. F4.5 and F4.6 run serially under F4 ownership; they must not race earlier F4 changes in shared files.

### Task F4.1: Path rules and the path qualification record

**Interfaces:**
- Produces, in `runtime/routing_paths.py`:
  - `RULES: Path` (`runtime/routing_routes.json`) and `QUALIFICATIONS_FILE = 'path-qualifications.json'`, which lives beside the routing settings. There is no route file.
  - `load_rules(path=RULES) -> dict`; raises `ValueError('route_manifest_invalid')`.
  - `qualifications(settings_path) -> dict[str, dict]`; `{}` when the file is absent; raises `ValueError('path_qualifications_invalid')`.
  - `qualify(settings_path, path_id, *, demand, evidence, account=None, now) -> dict` and `unqualify(settings_path, path_id) -> dict`, written atomically with mode 0600.
  - `resolved(rules, qualified) -> list[dict]`: the qualified paths, each with its rule fields, its `demand`, and the account that applies. Every reader of a path's account calls it.
  - `path_of(route_id) -> str`: the text before the first `:`.
  - `pool_windows(rules, pool) -> set[str]`: the window ids the rules declare for a pool.
  - `dollar_harnesses(rules) -> set[str]`: the harnesses of the paths whose `billing_class` is not `included_subscription`.

**Shipped file:** `{"version": 2, "paths": [...]}`, at most 16 paths. The table below is the baseline, extended by F4.5/F4.6 before release. A path has:

| Field | Meaning |
|---|---|
| `path`, `harness`, `provider` | The id and the two values a table strategy must match. The id matches `[a-z0-9-]+` and has at most 32 characters, so it never holds the `:` that separates it in a route id. No two paths share a `(harness, provider)` pair, so a strategy generates at most one route. |
| `sign_in` | `subscription`, `api_key` or `gateway_key` |
| `billing_class`, `pool` | The values a route carries today |
| `account` | The account label of the pool; a qualification may replace it (below) |
| `binding_enforceable` | Required on a subscription path, where the collector and `routing._credential_reasons` read it. Left out on a gateway path, where nothing reads it. |
| `windows` | The window ids a launch is charged in |
| `window_unit` | Required on a subscription path (`utilization_thousandths`); absent on a gateway path, whose one window is `credit` in micro-dollars |
| `integration_route` | Required on a gateway path: `workers` or `proposals` |
| `host`, `advisory`, `status` | Optional |

A path has no `model`, `effort`, `endpoint` or `demand`; `load_rules` refuses a path that has one, a malformed id, a duplicate `(harness, provider)` pair, a subscription path without `window_unit` or `binding_enforceable`, and a gateway path without `integration_route`. The shipped paths:

| `path` | `harness` | `provider` | `billing_class` | `pool` | `account` | `binding_enforceable` | `windows` |
|---|---|---|---|---|---|---|---|
| `opencode-claude-subscription` | `claude-agent-sdk via opencode` | `claude-agent` | `included_subscription` | `claude-subscription` | `claude-plan` | `true` | `5h`, `7d` |
| `opencode-chatgpt-subscription` | `opencode provider openai` | `openai` | `included_subscription` | `chatgpt-subscription` | `chatgpt-plan` | `true` | `7d` |
| `codex-chatgpt-subscription` | `codex-native` | `openai` | `included_subscription` | `chatgpt-subscription` | `chatgpt-plan` | `true` | `7d` |
| `openrouter-workers` | `opencode provider openrouter` | `openrouter` | `gateway` | `openrouter` | as in `tests/openrouter_fixtures.py` | absent | `credit` |
| `openrouter-proposals` | `openrouter-proposal` | `openrouter` | `gateway` | `openrouter` | as in `tests/openrouter_fixtures.py` | absent | `credit` |
| `test-only-subscription` | `test` | `test` | `included_subscription` | `test-only-subscription` | as in today's shipped test route | `true` | `5h` |

The first three come from the draft manifest in #227 (`origin/smart-routing-route-manifest:runtime/routing_routes.json`). The Codex path keeps `host: codex_native` and `advisory: true`; `advisory` refuses dollar routes only, so the subscription path is still enforceable. The two gateway paths carry `integration_route` `workers` and `proposals`. The test path has `status: test_only`, and `qualify` refuses it outside test inputs. The builder checks each `harness` string against what the launchers compare (`routing_launch.HOSTS`, `harnessFor` in the OpenCode plugin) before fixing it.

A qualification is `{path_id: {demand: {window: int}, account, evidence, qualified_at}}`. `qualify` requires a path from the rules, demand keys equal to the path's `windows`, and a non-empty `evidence` of at most 128 characters (the live result's id). A subscription demand is an integer from 1 to 1000, in thousandths of the window. A gateway demand is `{credit: N}`, micro-dollars per launch, from 1 to 1,000,000.

**The account.** A qualification's `account`, when it has one, replaces the rule's; otherwise the rule's applies. `resolved` is the only place that decides it.

**The gateway paths ship unqualified, and no step before the release qualifies one** (decision A). The demand contract below is for the slice that later adds the gateway's producers.

**The gateway demand (future call 19, not a release approval).** `N` is the largest launch cost a live gateway check observed, taken from the provider receipts, rounded up. Every route on the path reserves it until L1 records cost per model. Consequence: a model that costs more than the measured one is under-reserved at admission. The real cost is still settled from the receipt, and the organization limit and the key's own limit still stop the spend. Stricter alternative, not chosen: scale the reservation by each strategy's list price relative to the measured model's; it needs the measured model's price in the qualification record and one more rule in the replay. Decide this with the deferred gateway implementation, not to approve subscription routing.

- [ ] **Step 1: Write the failing tests** in `tests/test_routing_paths.py`: `test_shipped_path_rules_are_valid_and_name_no_model`, `test_path_with_a_model_or_demand_is_refused`, `test_path_id_shape_is_enforced`, `test_two_paths_with_one_harness_and_provider_are_refused`, `test_subscription_path_needs_a_window_unit_and_gateway_path_an_integration_route`, `test_shipped_subscription_paths_are_binding_enforceable`, `test_qualify_checks_the_path_and_the_demand_windows`, `test_gateway_demand_is_micro_dollars_within_its_bound`, `test_a_qualifications_account_replaces_the_rules`, `test_dollar_harnesses_are_those_of_the_non_subscription_paths`, `test_qualification_file_is_private_and_atomic`, `test_unreadable_qualifications_fail_closed`.
- [ ] **Step 2: Run** `scripts/test_sharded.py test_routing_paths`. Expected: FAIL, no module.
- [ ] **Step 3: Implement** and replace the shipped file.
- [ ] **Step 4: Run** again. Expected: OK.
- [ ] **Step 5: Commit** `feat(routing): model-free path rules and the path qualification record`.

### Task F4.2: Route generation in memory

**Interfaces:**
- Consumes: a compiled table whose strategies carry `provider` and, when it differs from the harness, `endpoint`. `routing_evidence._catalog` accepts an optional `provider` (text, at most 64 characters) per catalog entry; `compile_table` copies it onto each strategy and stops dropping `endpoint` (`runtime/routing_evidence.py:183`). `strategy_id` is computed as before, so ids do not change.
- Produces, in `runtime/routing_paths.py`, all pure:
  - `strategy_route(path, strategy) -> dict`: the route of one table strategy on one resolved path, with the fields the collector and `routing._valid_route` require:
    - `route` (`<path>:<strategy_id>`), `status: 'qualified'`, `path`, `strategy_id`;
    - `provider`, `model`, `harness`, and `effort`, where a strategy without an effort gets the text `'none'`, as today's gateway routes have;
    - `billing_class`, `pool`, `account` and `demand` (the path's qualified demand);
    - `window_unit` and `binding_enforceable` on a subscription path, `integration_route` on a gateway path;
    - `endpoint` when the strategy has one.
  - `choice_route(path, choice) -> dict`: the route of one explicit choice, with the same fields, **no `strategy_id` key**, the choice's `endpoint` when it has one, and the id `<path>:choice:<digest>`. The digest is the first 16 hex characters of the SHA-256 of the canonical JSON of `[provider, model, effort, harness, endpoint or null]`.
  - `generate(rules, qualified, table, choices) -> dict` with `routes` and `skipped`. For each qualified path: one strategy route per distinct table strategy with the path's `harness` and `provider`, and one choice route per choice with them. Two strategies with the same route identity and different endpoints generate no route; both are listed in `skipped` with the reason `endpoint_ambiguous`. Today `validate_routes` would refuse the whole list.
  - `selection(assignment, settings, strategy_ids) -> tuple[str, list]`: which routes one request needs. It repeats the mode rule of `routing._decide` exactly:

    | Request | Mode | Routes |
    |---|---|---|
    | `assignment.choice` is set | `choice` | The one choice route of that choice |
     | Smart Routing on, no choice, a non-empty ranking | `ranked` | Only the strategy routes of the ranked ids, in ranking order; the collector cuts after the host filter and static checks, to 64 in F4 and 10 subscription candidates in F5.2 |
    | Anything else | `choices` | One choice route per entry of the specialist's `worker_choices`, at most 16 |

    A choice never uses a strategy route, and a ranked request never uses a choice route. Filter choice routes among entries without `strategy_id` using `routing._choice_matches` with `spec: route` and `cls: routing.FUNDING_OF.get(route.get('billing_class'))`, never by a generated id; this preserves fixed `StaticSource` ids and funding matching. Ranked selection matches `strategy_id` in ranking order. A ranked request has at most 64 routes in F4 and 10 after F5.2; settings choices remain bounded at 16 and an explicit choice at one.
- `routing_launch.choice` gains an optional `endpoint` (text, at most 256 characters), which the two OpenRouter hosts send; `routing_launch.route_identity` still replaces their provider with `openrouter`. `routing._valid_assignment` accepts it, replacing a line. A choice on a gateway path without an endpoint is valid.
- The source interface in `runtime/routing_observations.py`. `LocalSource` and `StaticSource` both have every method, so the self-checks and the harness, which install a `StaticSource` through `default_source`, keep working:

  | Method | `LocalSource` | `StaticSource` |
  |---|---|---|
  | `manifest(table=None, choices=())` | `generate(load_rules(), qualifications(settings_path), table, choices)['routes']`; `[]` without a settings path | Its fixed list |
  | `admitted_route(route_id, identity=None)` | The qualified resolved path named by `path_of(route_id)`, with `route`, `path`, `pool`, `account` and `billing_class`; reject a malformed route suffix or, when supplied, an identity whose provider/harness differs from the path | The qualified fixed route with that id and matching identity when supplied, or `None` |
  | `pins_endpoint(route_id, identity, endpoint)` | Whether `route_id` equals the id `choice_route` gives that identity and endpoint on that path. `False` for a strategy route: no launcher sends a ranked gateway admission (decision B). | Whether the fixed route's `endpoint` equals it, as today |
  | `seedable(pool, window)` | Whether a qualified subscription path on the pool lists the window, and no dollar path draws on the pool | As `_subscription_window` decides today, from the fixed list |
  | `routes_version(table)` | 16 hex characters of the SHA-256 of the rules, the qualifications and the table's version | `None` |

  `load_manifest`, `MANIFEST` and the top-level `window_unit` are gone.
- `routing_observations.collect(..., table=None, strategy_ids=None, choices=(), harnesses=None)`. `scripts/routing.py` passes them: `_rank` returns the table it loaded beside the ranking, and `_prepare` calls `selection` on the resolved settings. `ASSIGNMENT_FIELDS` is unchanged, because `endpoint` travels inside `choice`. The collector works in this order:
   1. the routes of the request from `source.manifest(table, choices)`, restricted by choice identity or ranked strategy ids as `selection` specifies;
  2. the host filter of Task F4.4;
  3. the reasons that need no window: an invalid route, a `not_entitled` mark, the integration and credential checks;
  4. in F4, the first `MAX_ROUTES` routes in ranking order; in F5, replace that cut with Task F5.2's subscription-only cut over the whole host-filtered ranking, with no earlier 64-id truncation;
  5. the Codex reader, only when a route still kept is a subscription route on the ChatGPT pool and `refresh_windows` is set;
  6. the windows of the kept routes' pools.

   Its result gains `observations['routes_version']`; `routing._valid_observations` accepts the key.
- **One shared shortlist, without admission.** Extract collector steps 1–4 into `routing_observations.shortlist(project, assignment, *, settings_path, table, strategy_ids, choices, harnesses, source=None) -> dict` returning `{routes, excluded, shortlist, seen, routes_version}`. `routes` are the kept, statically checked routes; `shortlist` holds strategy ids, not route ids (empty in choice modes). It reads rules, qualifications, entitlement marks and local OpenRouter status only for explicit gateway choices surviving host filtering. Ranked paid routes are excluded before any funding read. It reads no windows or ledger, starts no reader or network call, and writes nothing. `collect` calls this same function, then performs steps 5–6 and existing explicit-choice funding verification; preserve static credential facts needed there. F4 uses its provisional 64-route cut; F5 replaces that cut here with `subscription_shortlist`.
- **Internal request preview consumer.** In `scripts/routing.py`, extract `_prepare`'s assignment validation, settings/profile resolution, `_rank` and `selection` into `Service._select(body) -> dict` with `assignment`, `settings`, `profile`, `ranking`, `table`, `mode`, `strategy_ids`, `choices`, `harnesses`. `_prepare` and `Service.preview(body)` both use it. `preview` accepts only collector-mode prepare bodies (no caller observations), applies the same request and harness validation, lazy-loads the collector and calls `shortlist`. It returns `{state: 'previewed', mode, ranking, shortlist, routes, excluded, routes_version}`; refusals use the existing blocked reason contract. It does not call workflow binding, `_ledger`, expiry or admission. This is one in-process request-specific need calculation for F7, not a second approval-list generator, protocol operation or `FIELDS` entry. F4 owns `_select` and `preview` in addition to its listed script methods.
- **Import direction.** Observations imports paths and, from F5, funding; funding may import routing; routing imports funding only inside `_decide`; paths imports routing/stdlib. Keep the script's collector import lazy. No preview implementation in the harness or funding module duplicates ranking, selection or static checks.
- Rename `_refresh_windows` to `refresh_needed(specs, settings_path, marks=())`, preserving its existing need/freshness/backoff behavior. `collect` and F7 pass only the routes kept by `shortlist`; those routes already passed entitlement checks. Preview itself never calls it.
- The dispatch checks stop reading a route list and ask the source:
   - `routing_launch.admitted_check` (:207) calls `admitted_route(admission['route'], admission['route_identity'])`, checks that its pool and billing class equal the admission's, then checks the `not_entitled` marks and catalog, as today. Decode the identity with the existing identity representation; do not introduce another encoding.
  - `opencode_launcher._account_of` (:621) takes the account from `admitted_route`.
  - `openrouter_proposals._endpoint` (:534), which the workers share, calls `pins_endpoint` with the request's identity and endpoint.
- `routing_observations._subscription_window` (the `seed_window` operation) calls `seedable`. A seed needs a qualified path and no table.
- `codex_rate_limits.declared_windows(rules)` returns `routing_paths.pool_windows(rules, POOL)`, and `read_windows` loads the rules in place of the manifest (:71, :324). `tests/qualification/live_modes.py:108` loads the rules the same way.

**Consequences, stated so nobody finds them later:**

- A `not_entitled` mark on a strategy route does not carry to the choice route of the same model, and the reverse. Each is found again by its own refused launch.
- The dispatch check is by path. A strategy that left the table after an admission still dispatches within its hold's lifetime, because the admission's `route_identity` and the catalog still pin what is sent. After `unqualify_path`, a held admission on that path is refused at dispatch.
- A settings worker choice whose harness and provider match no qualified path has no route, and the request waits with `worker_choice_unavailable`; a per-request explicit choice gives `explicit_choice_unavailable`.

**Cost:** each managed `prepare` reads the rules and qualifications and scans the already-bounded ranking table that `_rank` loaded. At most 64 ranked routes enter the snapshot in F4, 10 in F5; generation may inspect more before the cut. Each dispatch check reads the same two small files. Route reads write no route cache. Lighter alternative: stream generation through the same filters if measured latency needs it; a persistent cache adds invalidation and writes to read paths.

Until L1 records window use per launch, every route on a path uses the path's measured demand.

- [ ] **Step 1: Write the failing tests:**
   - In `tests/test_routing_paths.py`: `test_generation_combines_qualified_paths_with_table_strategies`, `test_unqualified_path_generates_nothing`, `test_strategy_without_a_provider_generates_nothing`, `test_strategy_without_an_effort_gets_the_text_none`, `test_gateway_route_carries_endpoint_and_integration_route`, `test_subscription_route_carries_the_window_unit`, `test_choice_route_has_no_strategy_id_and_a_stable_id`, `test_choice_route_id_changes_with_the_endpoint`, `test_ambiguous_endpoints_skip_only_those_strategies`, `test_demand_comes_from_the_path_qualification`, `test_every_generated_route_passes_the_collectors_checks` (each route gives `_manifest_reason(route) is None`), `test_route_selection_agrees_with_the_decision_mode` (over a grid of Smart Routing on and off, with and without a choice, an empty and a non-empty ranking: selection modes `choice` and `choices` map to recorded `worker_choice`; `ranked` maps to `ranked`).
  - In `tests/test_routing_observations.py`: `test_not_entitled_mark_still_excludes_a_generated_route`, `test_local_source_without_a_settings_path_has_no_route`, `test_collect_reads_only_the_ranked_strategies` (80 table strategies, 5 ranked ids: 5 routes considered, none listed as excluded for the other 75), `test_collector_route_count_bound`, `test_static_source_answers_the_whole_interface`, `test_reader_runs_only_for_a_kept_chatgpt_route` (a Claude-only request never starts the Codex reader, with the ChatGPT reading stale).
  - In `tests/test_routing_production_flows.py`, on `LocalSource` and the real stores in a temporary directory:
     - `test_read_paths_write_nothing`: with a fresh window (so no reader is needed), `prepare` one-shot, `prepare` under `--serve`, `admitted_check`, `_account_of` and `_endpoint` leave route inputs unchanged and create no route cache. Admission may write its ledger; test route lookup separately with all state files byte-identical.
    - `test_explicit_choice_outside_the_row_still_admits`
    - `test_settings_choice_covered_by_another_rows_strategy_still_admits`
    - `test_settings_choice_admits_without_a_table`
    - `test_openrouter_choice_keeps_its_endpoint_on_the_local_source`
    - `test_gateway_route_without_a_balance_producer_stays_out_in_production`
    - `test_seed_needs_a_qualified_path_not_a_table`
    - `test_unqualified_path_refuses_a_held_admission_at_dispatch`
    - `test_a_strategy_that_left_the_table_still_dispatches_its_held_admission`
  - In `tests/test_routing_launch.py`: `test_admitted_check_accepts_an_explicit_choice_route`, `test_choice_carries_an_optional_endpoint`.
   - In `tests/test_routing_codex_rate_limits.py`: `test_declared_windows_come_from_the_path_rules`.
   - In `tests/test_routing_observations.py`: `test_choice_filter_keeps_static_source_ids`, `test_shortlist_and_collect_share_the_pre_window_cut`, `test_shortlist_never_reads_windows_or_ledger_or_refreshes` (make those operations fail if called; compare all state files before/after).
   - In `tests/test_routing_command.py`: `test_preview_and_prepare_share_selection`, `test_preview_never_opens_a_ledger`, `test_preview_requires_collector_mode_and_harnesses`, `test_preview_is_not_a_protocol_operation`.
- [ ] **Step 2: Run** `scripts/test_sharded.py test_routing_paths test_routing_production_flows test_routing_observations test_routing_evidence test_routing_codex_rate_limits test_routing_launch test_routing_command`. Expected: FAIL.
- [ ] **Step 3: Implement.** Move the fixtures in `tests/openrouter_fixtures.py`, `tests/qualification/managed_state.py` and the route helpers in `tests/test_routing_observations.py` to the new interface. Keep `runtime/routing.py` at its ceiling and the script within 1000 lines by sharing the selection code.
- [ ] **Step 4: Run** the same modules plus `'test_openrouter*' test_routing_dispatch test_opencode_launcher 'test_qualification*'`. Expected: OK.
- [ ] **Step 5: Commit** `feat(routing): generate routes in memory from qualified paths and the ranking table`.

### Task F4.3: Activation gate, the operator operation and the runbook

**Interfaces:**
- Produces:
  - `routing_paths.gate(settings_path) -> list[str]`: `[]`, or one of `['route_manifest_invalid']`, `['path_qualifications_invalid']`, `['no_qualified_path']`, `['no_generated_route']`. It reads the packaged rules, and the qualification file and the table file beside `settings_path`, and nothing else. `no_generated_route` means no table strategy has the harness and provider of a qualified path. `routing_observations.manifest_check` and the reason `route_manifest_unqualified` are gone.
  - **How the gate reaches user state.** The activation checks run in a child with a temporary `HOME` that reads only the package (`scripts/routing.py:108-139`, `:808-819`). The `route_manifest` entry of `CAPABILITIES` gets the new kind `paths` and the target `runtime.routing_paths:gate`. `_self_check(kind, target, settings_path)` passes the service's absolute settings path as a fifth argument to the child, and the child calls the gate with it. `activation_gate(settings_path)` receives the path from its one caller (`scripts/routing.py:727`). The other kinds ignore the argument.
  - `scripts/routing.py`: two one-shot operator operations, refused under `--serve` like the other operator operations: `qualify_path` with `{path, demand, evidence, account?}` and `unqualify_path` with `{path}`. Their bodies live in `runtime/routing_paths.py`; the script only dispatches.
  - `tests/qualification/demand.py` (`measure-demand`):
    - each reading is tagged with the path id of the route the harness launched, in place of the harness's own pool label; `suggest` groups by path;
    - for each subscription path with a usable sample in every one of its windows, it prints one `qualify_path` request with the measured demand and the result id;
    - **a window whose every sample is `below_resolution`** (the reported reading did not move; this is not proof of precise launch usage) gets the proposed demand 10, and the output names the window and reason beside the request (call 29);
    - a path with a window that has no sample at all gets no request, and the output says which window is missing.

    It names no manifest route. The `gateway` mode prints no request (decision A).

- [ ] **Step 1: Write the failing tests:**
  - In `tests/test_routing_managed_projects.py`: `test_activation_reports_no_qualified_path`, `test_activation_reports_no_generated_route`, `test_activation_reports_an_invalid_rules_file`, `test_activation_passes_with_one_generated_route`.
  - In `tests/test_routing_production_flows.py`: `test_activation_gate_reads_the_services_settings_path` (two settings directories, one with a qualified path and a table: the real child process passes for that one and reports `no_qualified_path` for the other).
  - In `tests/test_routing_command.py`: `test_qualify_path_is_one_shot_and_validates_its_request`.
  - In `tests/test_qualification_demand.py`: `test_measure_demand_maps_readings_to_path_ids`, `test_measure_demand_prints_one_qualify_request_per_path`, `test_a_window_below_resolution_gets_the_floor_demand_and_says_so`, `test_a_path_with_an_unmeasured_window_gets_no_request`.
- [ ] **Step 2: Run** those four modules. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Update the runbook** (`docs/smart-routing-qualification.md:7-145`): the scheduled live session qualifies its measured OpenCode paths; after `measure-demand` the operator runs `qualify_path`, then `seed_window` for each demanded window, then activation. A new model on a qualified path needs no session unless its allowance/demand semantics differ (F4.5). The gateway check and Codex path qualify nothing in this scheduled OpenCode canary. F4.5/F4.6 name the separate evidence still needed for the release matrix. Mark decision 5's route manifest in the C.3 wiring design as replaced. Fix the stale sentence at `docs/smart-routing-qualification.md:613` (`reconcile_subscription` exists since #192) and add a dated note under the table row at line 530 without changing the recorded result.
- [ ] **Step 5: Run** the modules of Step 2 and `scripts/validate.py`. Expected: OK.
- [ ] **Step 6: Commit** `feat(routing): activation and the runbook qualify paths`.

### Task F4.4: The host rule

A ranked admission must not pick a route its launcher cannot send. Today the host is checked only after the admission, in `admitted_check`, and a failed check closes the hold as `not_dispatched` with no second try: the task fails where it should have waited or taken another route.

**Interfaces:**
- Produces:
  - `routing_launch.host_harnesses(host, catalog=(), *, dollar=False, rules=None) -> tuple[str, ...]`, sorted:

    | Host | Harnesses |
    |---|---|
     | `codex_native` | `codex-native` |
     | `claude_native` (F4.6) | `claude-native` |
    | `openrouter_proposal` | `openrouter-proposal` |
    | `openrouter_worker` | `opencode provider openrouter` |
    | `opencode` | The harnesses of the catalog's entries that a path of the rules names, minus `routing_paths.dollar_harnesses(rules)` unless `dollar` is set |

     The OpenCode launcher passes `dollar=API_FUNDING_QUALIFIED`, which stays `False` (decision B). The rules have at most 16 paths, so the set has at most 16 entries. Trace the managed worker's nested `execute → opencode_launcher.launch → routing_launch.launch → Routed.handle` preparation too: its ordinary OpenCode host may have an empty set and refuse before the duplicate `prepare`. Keep that refusal under decision B; do not widen the OpenCode host or toggle the qualification constant. `execute`'s `finally` calls `_settle_run`, so the previously known attempt still settles or remains explicitly recoverable. The later gateway-consumer slice must resolve this nested host contract before enabling that path.
  - `routing_launch.prepare_request(..., harnesses)`: a required keyword, sent as `constraints.allowed_harnesses`. When the caller's own constraints already have `allowed_harnesses`, the two sets are intersected. An empty set is refused before any request is sent, with `harness_required`.
  - Every production caller passes it: `routing_launch.launch` for the OpenCode launcher, which reads its catalog once before `prepare` and uses the same catalog for `admitted_check`; `delegation_routing.admit`; `openrouter_worker_managed`; `scripts/openrouter_proposals.py`.
  - `scripts/routing.py` `_prepare`: a request that uses the collector (no `observations` in the body) and has no `constraints.allowed_harnesses`, or an empty one, is refused with `harness_required`, also when Smart Routing is off. A request that supplies its own observations is not checked, because its caller already chose the routes.
  - The collector drops every route whose `harness` is outside the set, strategy routes and choice routes alike, before any other step (step 2 of Task F4.2). A dropped route is not listed in `excluded`: for this host it does not exist. `routing.rank` already narrows the ranking on `allowed_harnesses` (`runtime/routing.py:276`).
  - `tests/qualification/server.py` sends the harness set of the providers its isolated server is signed in to, whenever it strips the observations for the collector (`:304-307`).

**Host scope.** A pool counts only when the launching host can send on it. A Codex launch with a full ChatGPT pool waits; it does not see the Claude pool, which it could not use (call 31). Deferred OpenRouter work must preserve this boundary.

- [ ] **Step 1: Write the failing tests:**
  - In `tests/test_routing_launch.py`: `test_host_harnesses_per_host`, `test_opencode_host_excludes_dollar_harnesses_until_api_funding_is_qualified`, `test_prepare_request_intersects_the_callers_harnesses`, `test_an_empty_harness_set_is_refused_before_prepare`.
  - In `tests/test_routing_command.py`: `test_managed_prepare_without_harnesses_is_refused` (also with Smart Routing off), `test_prepare_with_caller_observations_needs_no_harnesses`.
  - In `tests/test_routing_observations.py`: `test_host_filter_drops_routes_silently_before_every_other_step`, `test_pool_windows_are_read_only_for_paths_the_host_can_send`.
   - In `tests/test_routing_production_flows.py`: `test_opencode_ranked_launch_on_the_local_source` (trace P1, with the four time points it lists), `test_codex_routed_admission_considers_only_codex_routes` (Claude and Codex paths qualified, the Claude strategies ranked first: the Codex host is admitted a Codex route, never a Claude one), `test_codex_explicit_choice_admits_on_a_qualified_path`.
   - The ranked OpenCode launch test covers all three subscription connection families after F4.5, with synthetic local stores and a transport double: no explicit model/choice, a nonempty ranking, `mode: ranked`, and the transport receives exactly the admitted provider/model/effort. Include disconnected and same-provider paid connections. In F5 extend it with the preferred subscription route lacking headroom while another eligible subscription route fits: select and launch that route, never merely return advice or dispatch OpenRouter.
   - In `tests/test_delegation_routing.py`, `tests/test_opencode_launcher.py`, `tests/test_openrouter_workers_managed.py` and `tests/test_openrouter_proposals_managed.py`: one test each that the request the caller sends carries its host's harnesses.
   - In `tests/test_openrouter_workers_managed.py`: `test_nested_prepare_refusal_settles_the_known_attempt`, without patching `API_FUNDING_QUALIFIED=True`; assert no provider send, the existing finalizer runs and no silently leaked hold remains. This synthetic failure-path test does not qualify production gateway dispatch.
- [ ] **Step 2: Run** those modules. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the modules of Step 2 and `scripts/validate.py`. Expected: OK. F4's full-suite gate follows F4.6.
- [ ] **Step 5: Commit** `feat(routing): admission considers only the routes of the launching host`.

### Task F4.5: OpenCode subscription connection and allowance binding

**Files:** create `runtime/opencode_subscription.py`, `tests/test_opencode_subscription.py`; modify F4's path rules, collector, launcher, adapter identity parity, production-flow tests, capability matrix and OpenCode contract.

**Existing evidence, checked 10 November 2026:** [Go documentation](https://opencode.ai/docs/go/) distinguishes included allowance from optional Extra Usage. [OpenCode V2 API](https://opencode.ai/docs/api) exposes `GET /api/experimental/usage/limits`: `data.workspace`, optional `data.go.{rolling,weekly,monthly}` meters (`percent`, `limited`, `resetsAt` in milliseconds), and separate cash `budgets`. These are documentation facts, not qualification of the installed client. The page's model-dependent allowances and the aggregate Go meters require a checked normalization/demand mapping; token counts or Console budgets cannot stand in for quota readings.

**Interfaces and boundaries:** reuse the existing OpenCode connection and request transport. `opencode_subscription.normalize_limits(payload, *, observed_at) -> dict` returns the existing window-store input shape for the connected workspace, or an explicit unavailable result; it reads no credentials. `refresh_needed` calls one bounded usage read only for a kept stale OpenCode-subscription candidate, outside served `prepare` and `check`. Prefer a compatible existing client snapshot over a new read. Add no polling, inference probe or independent login. `opencode_launcher.launch` still owns sending and settlement.

- [ ] **Step 1: Establish the binding before coding it.** Inspect the installed client's provider/model and usage schemas without secrets or inference. Record the supported connection identity, subscription-only endpoint, units, reset semantics, shared-pool scope and supported variants in the capability matrix. Use the provider ID the client actually exposes, not a guessed constant from a different client version. Confirm how model-dependent allowances map to its aggregate meters and to candidate demand. If those facts are unavailable, report the narrow release blocker; do not mark a fake binding supported.
- [ ] **Step 2: Write failing tests** `test_connected_subscription_is_distinct_from_console_cash`, `test_disconnected_provider_is_not_ranked`, `test_go_windows_preserve_units_and_reset_semantics`, `test_model_specific_demand_does_not_duplicate_shared_headroom`, `test_missing_meter_is_unknown`, `test_extra_usage_or_unknown_billing_is_not_admitted`, and `test_refresh_is_stale_and_needed_only`. Fixtures use invented model names and actual schema shapes. Missing short, weekly or monthly constraints cannot become spare capacity.
- [ ] **Step 3: Add the model-free path and its adapter.** Classify included usage as `included_subscription`, with its own account-bound pool. Retain rolling-window semantics; do not apply the fixed-window stale-full shortcut to Go. Keep paid Extra Usage and Console pay-as-you-go ineligible. Use the existing provider-side subscription-only boundary where observable; if paid spillover cannot be excluded, explain why that connection is unavailable rather than turn on spending. Keys remain inside OpenCode. If verified Go demand needs candidate-specific normalization, derive it from observed provider metadata in generated routes and cover the effect in F5/F7; never add model literals to path rules or assume Claude's demand floor applies.
- [ ] **Step 4: Prove P8** in `test_opencode_subscription_ranked_launch_on_the_local_source`, on `LocalSource` and temporary stores, through actual launcher logic with only transport replaced. A capable Go candidate can win and launch; all three windows constrain it; aliases sharing one allowance do not create capacity; exhausted, disconnected and paid-only connections send nothing. When only one connection exists, routing still works without requiring the other two.
- [ ] **Step 5: Run** `scripts/test_sharded.py test_opencode_subscription test_routing_paths test_routing_observations test_opencode_launcher test_opencode_plugin test_routing_production_flows`. Expected: OK. Record installed-client binding/usage evidence separately from synthetic transport proof. Commit `feat(routing): include connected OpenCode subscription allowance`.

**Cost:** reuse the catalog already fetched by the launcher, plus at most one bounded zero-token usage request when stale and needed. The lighter option is the client's already-produced usage snapshot; if unavailable, existing operator seeding remains recovery, not a reason to invent capacity.

### Task F4.6: Native-only selection and launch for Codex and Claude Code

**Files:** create `runtime/native_routing.py`, `tests/test_native_routing.py`; modify `runtime/routing_launch.py`, `runtime/delegation_routing.py`, `scripts/routing.py`, path rules, relevant routing tests, `clients/codex.md`, `clients/claude-code.md`, `policies/03-agent-delegation.md` and `docs/policy-sources.md`.

**Approach:** keep one profile/ranker/allocator. Reuse Codex's existing managed delegation path where selected. Add Claude's missing native host and a thin ordinary native-client binding; do not port Codex's task-state database or enforcement machinery to Claude. Native dispatch is advisory: the coordinator automatically invokes the supported native launch with the selected settings, and records the native result. This is automatic execution by the agent, not a recommendation requiring user model selection.

**Interfaces:** `native_routing.prepare(service, *, client, assignment, job, catalog, profile=None) -> dict` prepares and checks one native subscription admission through the existing service, returning the admitted identity and attempt or the existing pending/blocked response. It never accepts a cross-provider catalog or caller-supplied quota observations. `native_routing.complete(service, *, attempt_id, outcome) -> dict` uses existing settlement/recovery events, not another ledger. The native client contract places the native spawn between these calls, only once after admission. Reuse existing dispatch/hold state for lost responses; an uncertain send must never be retried blindly. Host identity comes from the actual client adapter. These entry points need a supported native command/tool bridge, identified and tested in Step 1; a Python helper without a client caller does not complete this task.

- [ ] **Step 1: Bind the actual native launch controls and caller.** Trace Codex's existing preparation/dispatch/observation flow. For Claude, inspect its supported native subagent invocation and installed definitions, then name the command/tool bridge and model/effort mapping in `clients/claude-code.md`. Official [subagent documentation](https://code.claude.com/docs/en/sub-agents) distinguishes per-call aliases from full IDs in definitions and lists effort precedence. Only catalog settings the actual launch can express are eligible; do not change coordinator/global defaults to force a match. Record unsupported settings as unavailable. Decide the thin bridge before adding helpers; no broad lifecycle extension.
- [ ] **Step 2: Write failing tests** `test_native_profile_changes_model_selection`, `test_codex_never_selects_a_foreign_route`, `test_claude_never_selects_a_foreign_route`, `test_native_spawn_receives_the_admitted_settings`, `test_native_explicit_choice_is_not_substituted`, `test_native_unavailable_route_sends_nothing`, `test_native_unknown_send_is_not_retried`, and `test_native_completion_settles_once`. P9's `test_native_clients_rank_and_launch_on_the_local_source` must invoke the real bridge with a native-tool double, not stop at `prepare`. Include unsupported effort, subscription exhaustion and shared-pool reservations across OpenCode/native aliases.
- [ ] **Step 3: Implement the bindings.** Add `claude_native` with provider `anthropic`, harness `claude-native`, a path on `claude-subscription`, and advisory host handling. Map host catalog aliases from observed client data, not name heuristics. Apply the same host filter before ranking for both native clients. Reuse `prepare_request`, `admitted_check`, dispatch and settlement; keep explicit choice and duplicate-attempt rules. Ordinary native routing needs routing state only, not Codex D5/D6 task state. Already selected managed task controls retain their existing requirements.
- [ ] **Step 4: Close activation and instruction consumers.** Update the current Claude-only `advisory_host_unmanaged` refusal only when the native bridge is functional. Test Claude-only enrollment, mixed clients and unavailable bindings; status/disable/recovery still work. Update policy 03's assertion that Claude has no routing launcher and the native client load paths together. Follow instruction-authoring provenance and representative-task checks; preserve existing managed Codex guarantees.
- [ ] **Step 5: Commit and verify** `feat(routing): route native clients to native subscription models`. Run `scripts/test_sharded.py test_native_routing test_delegation_routing test_routing_launch test_routing_managed_projects test_routing_production_flows test_prepared_instructions`, then F4's full gate on that final committed candidate. Expected: OK. Record a separate bounded native-client launch check for each client before claiming observed support; obtain any missing live budget first. Decision C does not supply that budget.

**Cost:** no extra model turn for profiling/ranking; local selection and the intended native worker call only. No new native pre-send enforcement system. Missing live qualification stays visible per client rather than expanding the OpenCode canary into a universal qualification campaign.

When F4 merges, the coordinator marks #227 `[SUPERSEDED]` and closes it.

---

## Slice F5: Value order, funding order and replay fields

Shared core. **Owned files:** `runtime/routing_evidence.py`, `runtime/routing.py`, `runtime/routing_funding.py`, `runtime/routing_paths.py` (replace F4's provisional cut), `runtime/routing_observations.py` (the shared cut), `runtime/usage_budget.py` (capacity comment; constants only after measured approval), `scripts/routing.py`, `scripts/validate.py` (ceilings only), `tests/test_routing_evidence.py`, `tests/test_routing_ranker.py`, `tests/test_routing_funding_order.py`, `tests/test_routing_funding.py`, `tests/test_routing_funding_property.py`, `tests/test_routing_funding_capacity.py`, `tests/test_routing_gates.py`, `tests/test_routing_production_flows.py`, `tests/fixture_routing_funding.py`, `tests/test_routing_admission.py`, `tests/test_routing_observations.py`, `tests/qualification/replay_export.py`, `tests/test_qualification_replay_export.py`, `docs/smart-routing-validation.md`, `clients/guidance-publication.md`.

### Task F5.1: Pre-registration Edit 7

**Files:**
- Modify: `docs/smart-routing-validation.md` (a new section after Edit 6)

This task merges first within F5, before any code of F5 runs against real records. Its content needs the user's approval, because it changes pre-registered text. Edit 7 says:

1. **Vocabulary.** "Role" means specialist. The strict sample covers at least three specialists and two complexity bands.
2. **What the strict replay recomputes for ranked records.** From each record's stored detail alone:
   - the class, from `shares`, `bucket`, `epoch`, `feasible` and the earlier records. The earlier records are the ones the router itself counts: same ledger, same settings revision and at most 14 days older;
   - the route, from the stored subscription candidates in the stored order. No gateway decision is produced or replayed this release. New ranked paid records fail the release contract rather than being accepted without replay.

   The four pass or fail rows of the allocator replay are unchanged.
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
9. **Canary.** It runs only in ranked mode on generated routes; dispatch, attach and the off-route guard use the admitted identity and pool. Keep the existing sample, concurrency and latency bounds and seven-day maximum. **Proposed cap interpretation (call 36):** the 10% allowance is 100 thousandths per approved pool/window over the whole run, accumulated from positive observed increases, including unrelated usage. Any reset of an approved demanded window ends the run incomplete as `window_usage_unknown`; a reset never replenishes the allowance, and reseeding cannot erase an unknown delta. This adds a stricter effective deadline: finish before the earliest such reset, which may be less than five hours away for the Claude pool. Reserve only within the remaining allowance and abort an observed overrun. Insufficient remaining allowance releases any undispatched admission as `not_dispatched` and ends incomplete as `allowance_exhausted`. Preflight reports full-reservation coverage using the configured reservation margin, time to earliest reset, and the 60-admission minimum; it predicts neither actual usage nor guaranteed completion. A coverage shortfall requires explicit start approval acknowledging the incomplete-sample risk. F7.2 defines the calculation and enforcement. Keep local-state `--serve` preparation for registered latency measurements. Pause for a seed only before the reset deadline while cumulative usage remains observable; never admit a probe on stale data. This interpretation is a new registration proposal, not an unchanged bound or current permission.

- [ ] **Step 1: Write the section** with the nine points, dated, stating that no replay, canary or label existed when it was registered. Add the dated correction beside Edit 6's sentence about the pilot's snapshot detail (`docs/smart-routing-validation.md:233`), without changing Edit 6's text.
- [ ] **Step 2: Run** `scripts/validate.py` and `scripts/test_sharded.py test_prepared_instructions`. Expected: OK.
- [ ] **Step 3: Commit** `docs(routing): pre-register Edit 7 for the redesigned replay and ranking`.

### Task F5.2: Move the ranked decision, then list price and value order

`runtime/routing.py` has no spare line after F3 lowers its ceiling, so the first commit of this task only moves code.

**Interfaces:**
- Produces:
   - `runtime/routing_funding.py` holds the ranked branch of `routing._decide` (:565-588) and the record builder (:596-611), moved without a change in behaviour. `routing._decide` imports it locally and calls it; `routing_funding` may import `routing`, never the reverse at module import time. Lower `routing.py`'s ceiling to its new line count.
  - A catalog entry accepts an optional `list_price: {"input": number, "output": number}`, in dollars per million tokens, both finite and not negative.
  - Each compiled strategy carries `list_price_micros: int | None`, `round((3 * input + output) / 4 * 1_000_000)`.
  - `compile_table` orders each row by: priced before unpriced, `list_price_micros` ascending, the effort's index in the model's `supported_efforts` ascending (no effort counts as 0), then the guidance order. The catalog contract in `clients/guidance-publication.md` states that `supported_efforts` is listed from the lowest to the highest; the compiler cannot check it.
  - `routing.rank` returns the whole filtered order, as today. `routing_funding.SHORTLIST_MAX = 10`; pure `subscription_shortlist(strategy_ids, routes) -> list[str]` returns the first 10 distinct ranked ids with a statically eligible, host-reachable `included_subscription` route. Its `routes` input has already passed F4's shared pre-window checks. Paid routes consume no places. No global 64-id cut precedes it.
  - The collector uses that function in its shared `shortlist` and carries the ordered ids as `observations['shortlist']`, before any window read. Later reader/window/candidate refusals consume their place; no refill or extra-pool read follows. No permanent/temporary reason taxonomy or gateway-only exclusion metadata is added.
  - `routing._valid_observations` accepts the optional `shortlist`: at most 10 distinct bounded string ids. In ranked admission, require its ids to be a ranking-order subset of the request's ranked ids; refuse malformed values with the existing invalid-observation response. Routes used for candidates must match those ids and still pass subscription, host, credential and feasibility checks. A missing route is a refusal, never reconstructed authority.
  - For caller observations without `shortlist`, apply the same non-window eligibility checks to their supplied routes, then `subscription_shortlist`, before `_candidate`. Do not interpret `excluded` reason strings to infer a missing route. A supplied valid shortlist may narrow candidates but never widen eligibility. This preserves unmanaged snapshots while bounding their replay fields. Choice modes retain their existing identity, funding and 16-entry bounds.

**Why the cut follows route eligibility (call 18, revised by the approved cuts).** The cheapest ten table entries may all be unreachable or paid-only. Scan the bounded table's full filtered order so a later subscription candidate remains reachable. Usage failures after the cut do not trigger a second search.

- [ ] **Step 1: Move the code** and run `scripts/test_sharded.py 'test_routing_*'`. Expected: OK with no test changed. Commit `refactor(routing): move the ranked decision and the record builder to routing_funding`.
- [ ] **Step 2: Write the failing tests:**
  - In `tests/test_routing_evidence.py`: `test_price_is_three_parts_input_one_part_output` (input 1.0 and output 5.0 give `2_000_000`), `test_rows_are_ordered_by_price_then_effort_then_guidance_order`, `test_unpriced_candidates_come_last`, `test_invalid_list_price_is_refused`.
   - In `tests/test_routing_funding_order.py`: `test_subscription_shortlist_takes_first_ten_eligible_ids` (15 ids, the 3 cheapest paid-only: ids 4–13 survive), `test_a_not_entitled_route_takes_no_place`, `test_an_unobserved_route_keeps_its_place`, `test_shortlist_is_never_padded`, `test_caller_snapshot_is_cut_before_window_feasibility`, `test_invalid_shortlist_cannot_expand_the_ranking`.
   - In `tests/test_routing_observations.py`: `test_collector_keeps_only_the_subscription_shortlist`, `test_reader_runs_only_for_shortlisted_chatgpt_routes`, `test_subscription_candidate_beyond_position_64_is_not_lost`, `test_post_cut_refusal_does_not_refill_the_shortlist`, `test_preview_collect_and_decision_keep_the_same_shortlist` (including a late refusal and a caller snapshot with more than ten candidates).
- [ ] **Step 3: Run** the three modules. Expected: FAIL.
- [ ] **Step 4: Implement.**
- [ ] **Step 5: Run** them and `test_routing_table test_routing_ranker test_routing_command`. Expected: OK.
- [ ] **Step 6: Commit** `feat(routing): order rows by list price and keep one subscription shortlist`.

### Task F5.3: The funding order

**Files:**
- Create: `tests/test_routing_funding_order.py` (started in F5.2)
- Modify: `runtime/routing_funding.py`, `runtime/routing.py`, `tests/test_routing_funding.py`, `tests/test_routing_funding_property.py`, `tests/fixture_routing_funding.py`

**Interfaces:**
- Produces, in `runtime/routing_funding.py`:
  - `choose(candidates, shares, rows, bucket, epoch, feasible, now, horizon) -> tuple[str | None, str | None]`: the chosen route and its class, pure. Candidates are the feasible subscription entries in shortlist order. Use `allocator.choose_class` and `allocator.choose_route` unchanged. Reject a non-subscription candidate or feasible class with `ValueError('billing_class_excluded')`; do not silently replay a paid record as a subscription.
- `routing._decide`, ranked mode, filters paid routes before `_candidate`, even for caller snapshots or enabled legacy API settings. It passes `shares={api: 0.0, subscription: 1.0}` and `feasible=['subscription']` when a subscription candidate exists, otherwise returns the existing pending response. No gateway decision/reason is computed.
- Preserve overlapping-window feasibility, held reservations, binding reset horizons and within-pool value-order tie breaking. Do not weight a pool by its number of models. Preserve the existing API allowance equations and their direct unit tests for next release; no active API allocation is introduced here.
- Explicit and settings choices keep their hard identity constraints, existing funding gates and recovery behavior. Synthetic legacy paid-choice coverage does not authorize a paid production path. Native and OpenCode subscription hosts continue excluding dollar harnesses; `API_FUNDING_QUALIFIED` remains `False`.

**Production scope.** Required automatic subscription selection and launch use this decision. Gateway opening rules, gateway-only taxonomy and dual shortlists are deferred with OpenRouter execution. Existing gateway input validation and safe refusal/settlement remain compatibility requirements, not new ranked fallback work.

- [ ] **Step 1: Write the failing tests** in `tests/test_routing_funding_order.py`: `test_subscription_allocation_preserves_window_pressure`, `test_multiple_models_do_not_multiply_pool_weight`, `test_all_demanded_windows_constrain_admission`, `test_stale_or_missing_subscription_window_waits`, `test_no_feasible_subscription_never_falls_back_to_paid`, `test_ranked_api_route_is_excluded_even_with_legacy_enablement`, `test_no_route_at_all_waits_with_a_reason`. Assert existing allocator outputs, including different per-candidate costs on one pool; test missing, stale, exhausted and non-fitting subscription inputs beside otherwise feasible synthetic paid routes.
   - In `tests/test_routing_production_flows.py`: extend P1 through ranked funding and add `test_opencode_ranked_admission_never_selects_a_gateway_route`, with both path types present and the subscription full; assert pending, no dollar hold and no dispatch. When another eligible subscription fits, assert the actual launcher sends its admitted identity and settles once.
- [ ] **Step 2: Run** `scripts/test_sharded.py test_routing_funding_order`. Expected: FAIL.
- [ ] **Step 3: Implement,** keeping `runtime/routing.py` at or below its ceiling. Update the mixed ranked fixtures in `tests/test_routing_funding_property.py:19-42` and `tests/fixture_routing_funding.py`: paid candidates are ineligible in ranked mode. Preserve legacy explicit-choice and API arithmetic regression coverage.
- [ ] **Step 4: Run** `scripts/test_sharded.py 'test_routing_funding*' test_routing_admission test_routing_allocator test_routing_replay`. Expected: OK.
- [ ] **Step 5: Commit** `feat(routing): select subscription routes without paid fallback`.

### Task F5.4: Replay fields in the admission record and the export

**Interfaces:**
- Produces, in the admission record (built in `runtime/routing_funding.py` since F5.2):
   - `ranking`: `table_version`, `profile_digest`, plus `shortlist` (the subscription strategy ids before window checks, at most 10), `strategy_id` (the chosen one, or `None`), `score_version` (`None` until L3), `routes_version` (from the source, or `None` for caller observations or a `StaticSource`). The route version is audit data, not a replay dependency; replay never regenerates its digest.
  - `risk_band`, `floor_filled` (whether `missing_fields` was not empty), and `strategy: {provider, model, effort, harness}`. `scripts/routing.py` passes `risk_band` and `floor_filled` in the admitted assignment; `work_kind` is there since F2.2.
  - In `pressure`, which is the snapshot detail removed after 24 hours:
     - `epoch`, `feasible` (`['subscription']`), `minimum_horizon`, and the effective `allowed_harnesses` (empty for a caller snapshot without that constraint);
     - `candidates`: `[{route, strategy_id, cls, pool, constraints}]`, only feasible subscription candidates, in shortlist order. The strategy id links each candidate to `ranking.shortlist`, including caller snapshots whose route ids are arbitrary. Each constraint stores `window`, `headroom`, `cost`, `resets_at`; omit the repeated pool, which its candidate already names. Admission and replay expand that pool when calling the allocator. Costs stay per candidate, not per pool. No funding-step tag, gateway state or gateway-only threshold is stored.
   - In `worker_choice` mode, store an empty shortlist and no `pressure.candidates`; keep the existing chosen-route constraints. Edit 7 point 3 applies.
   - **Capacity measurement (call 33, revised by the approved cuts):** keep existing admission, whole-ledger, soft-limit, hold and history bounds initially. Measure compact serialized subscription-only records before proposing any increase. Include ten candidates with the largest supported demanded-window set (Go has three), maximum supported identity/harness fields, bindings and Unicode escaping; also exercise oversized caller snapshots. Preserve `record_too_large` refusal before any hold is written and never truncate replay detail.

     Extend the maximal-ledger fixture with the actual new record shape across all retained slots and prove settlement still fits above the soft limit and below 90% of the existing hard limit. Report both per-record size and maximal-ledger size. If either fails, stop with measurements and a smallest sufficient proposal; no 7,168-character allowance, 15 MB hard limit or reduction in retained history/holds is pre-approved. A future hard-limit proposal must state its old-reader rollback impact. Estimates are not capacity proof.
  - `tests/qualification/replay_export.py` exports the records as stored, with a manifest field `format: 2`; records without `ranking.shortlist` are exported unchanged.
- `ADMISSION_FIELDS` in `scripts/routing.py`, what a client may see, gains nothing.

- [ ] **Step 1: Write the failing tests:**
   - In `tests/test_routing_admission.py`: `test_admission_record_carries_the_replay_fields`; `test_class_and_route_recompute_from_the_record_alone` (call `routing_funding.choose` with only record fields, earlier records and `at`, across subscription pools and different candidate costs); `test_two_routes_on_one_pool_keep_their_own_costs`; `test_largest_shipped_path_record_fits_the_size_limit` (ten subscription candidates, largest supported demanded-window set, maximum identity/harness fields and binding); `test_oversized_replay_detail_refuses_without_a_hold`; `test_new_worker_choice_record_uses_the_legacy_replay_contract`; `test_snapshot_detail_is_removed_after_a_day_and_the_rest_stays`.
   - In `tests/test_qualification_replay_export.py`: `test_export_keeps_the_replay_fields_and_old_records`.
   - In `tests/test_routing_production_flows.py`: `test_unmanaged_prepare_record_has_null_routes_version`, with caller observations, no `excluded` and no `rejected`; exact replay succeeds with no live source.
   - Retain `tests/test_routing_gates.py`'s bound assertion; extend `tests/test_routing_funding_capacity.py`'s maximal-record fixture with the measured subscription replay shape and prove settlement still fits above the soft limit, below 90% of the hard limit. A capacity failure follows the measurement/approval rule above, not an automatic constant increase.
- [ ] **Step 2: Run** those five modules. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Update** `clients/guidance-publication.md`: a guidance row is `{work_kind, complexity_band, risk_band, strategies}`; a catalog entry carries `provider`, `list_price` and `supported_efforts` from the lowest to the highest.
- [ ] **Step 5: Commit** `feat(routing): admission records store what the strict replay recomputes`.
- [ ] **Step 6: Run** F5's full-suite gate and `scripts/validate.py` on that final committed slice candidate. Expected: OK.

---

## Slice F6: No model names

**Owned files:** `runtime/usage_canary.py`, `runtime/adapters/codex.py:596-597`, `runtime/routing_evidence.py` (`display_names`), `runtime/model_names.py`, `clients/opencode.md:22,350`, `skills/manage-usage-routing/references/first-canary.md:30`, `skills/openrouter-workers/references/procedure.md`, `scripts/validate.py`, `tests/test_model_names.py`, `tests/test_usage_canary*.py`, `tests/qualification/safety.py`, `tests/qualification/drill.py`, `tests/qualification/launcher_probes.py`, `tests/qualification/canary.py`, `tests/qualification/live_modes.py`, `docs/smart-routing-qualification.md`.

### Task F6.1: Remove the names

**Additional owned consumers:** `tests/qualification/server.py`, `tests/qualification/opencode_live.py` (currently 985 lines), `tests/qualification/pilot_env.py`, `tests/qualification/opencode_inspect.py`, `tests/test_qualification_harness.py`, `tests/test_qualification_canary.py`. Complete route handoff through these importers in F6 before removing `safety.MODEL` and `safety.ROUTE`; keep each Python file within its ceiling. F7 builds ranked per-attempt selection on this working handoff, not on broken imports awaiting its next PR.

**Interfaces:**
- Produces:
  - `usage_canary`: the `_MODEL` constant is gone. The canary uses the cheapest priced strategy for its provider in the compiled table, and refuses with `canary_model_unavailable` when the table has none. The reasoning constant stays. Design section 5.14 says "the lightest model the catalog lists"; no catalog is on the machine before L2, so the table stands in for it (coordinator's call 20), and L2 may switch to the catalog.
  - A catalog entry accepts an optional `display_name`; the compiled table carries `display_names: {model: name}`. The Codex adapter's one hard-coded correction is replaced by `model_names.display_name(model, settings_path)`, which reads that map from the table file, at no added lines in `runtime/adapters/codex.py`. **Cost:** one read of the table file when a display name is resolved. Lighter alternative: drop the correction and show the vendor's spelling.
   - The live harness takes the route as `--route PROVIDER/MODEL#VARIANT`; `tests/qualification/safety.py` has no `MODEL` or `ROUTE` constant and checks every launch against the route named in the approval. Pass the parsed route through server construction/control, `Run.prompt`, `CanaryEnv` and inspection/off-route helpers. Preserve the explicit single-route pilot/isolation behavior and require a route instead of inventing a default. Adapt their callers and qualification test fixtures in this slice.

- [ ] **Step 1: Write the failing tests:** `test_canary_takes_the_cheapest_priced_model_for_its_provider`, `test_canary_refuses_without_a_priced_model`, `test_display_name_comes_from_the_table`, and in the qualification tests `test_harness_refuses_a_launch_outside_the_approved_route`.
- [ ] **Step 2: Run** `scripts/test_sharded.py 'test_usage_canary*' test_routing_evidence 'test_qualification*' test_usage_codex_host`. Expected: FAIL.
- [ ] **Step 3: Implement,** and reword the four document lines so they name no model; where a line records an observation, point to the dated record under `docs/`. Make the runbook take the route as a parameter.
- [ ] **Step 4: Run** again. Expected: OK.
- [ ] **Step 5: Commit** `refactor: no model name in code, contracts or skills`.

### Task F6.2: The `validate.py` check

**Interfaces:**
- Produces: `scripts/validate.py` returns `model_names: 'passed' | 'not_checked'` and raises `ValueError` naming the file and line on a match. The names are every `model` and display name in the compiled table in user state (and in the downloaded catalog once L2 exists). A name matches as a whole token, ignoring case, in `runtime/`, `scripts/`, `policies/`, `clients/`, `skills/`, `templates/`, `AGENTS.md` and shipped `*.json`; `assets/`, `docs/` and `tests/` are outside. With no table it reports `not_checked`, as `client_execution` does.

Tests run under a private home, so they see `not_checked`. The coordinator runs `python3 -B scripts/validate.py` once with the real home, read-only, at each merge gate after the production table exists.

- [ ] **Step 1: Write the failing tests** in `tests/test_model_names.py`: `test_a_table_model_in_a_covered_file_fails_with_file_and_line`, `test_the_same_name_under_docs_tests_or_assets_passes`, `test_a_name_inside_a_longer_word_does_not_match`, `test_without_a_table_the_check_is_not_run`.
- [ ] **Step 2: Run** `scripts/test_sharded.py test_model_names`. Expected: FAIL.
- [ ] **Step 3: Implement** in `runtime/model_names.py` (created in Task F6.1), which `validate` calls.
- [ ] **Step 4: Run** `scripts/test_sharded.py test_model_names test_validate_release test_package` and `scripts/validate.py`. Expected: OK.
- [ ] **Step 5: Commit** `feat(validate): fail on a model name in the covered areas`.

---

## Slice F7: Strict replay runner and the ranked canary

**Owned files:** `tests/qualification/replay_strict.py`, `tests/test_qualification_replay_strict.py`, `tests/qualification/canary.py`, `tests/qualification/pilot_env.py`, `tests/qualification/opencode_live.py` (route handoff only; currently 985 lines), `tests/qualification/server.py`, `tests/qualification/safety.py`, `tests/test_qualification_canary.py`, `tests/test_qualification_harness.py`, `docs/smart-routing-qualification.md` (replay and ranked-canary steps).

### Task F7.1: The strict replay runner

**Interfaces:**
- Consumes: the export directory of `replay_export.export`, the record fields of Task F5.4, and `routing_funding.choose`, the pure function admission itself calls. It does not reimplement selection or a deferred gateway decision.
- Produces: `replay_strict.run(directory) -> dict` with `records`, `legacy_records`, `class_reproduced`, `route_reproduced`, `funding_violations`, `resurrected_capacity`, `stray_records`, `specialists`, `bands`, `passed`; and `python3 -B -m tests.qualification.replay_strict --dir <directory>`, which prints the report without ids, titles or timestamps and exits 1 unless `passed`.
- It opens nothing but the export directory, calls no model and uses no network. Per ledger identity it walks the records in `at` order and applies Edit 7's points 2, 3 and 6. The earlier records it gives to `choose` are filtered as `routing.decision_rows` filters them: same settings revision, at most 14 days older. Ranked records must have subscription billing/class and the fixed subscription-only shares; a new ranked paid record fails and is counted, never silently skipped.
- Accept `routes_version: None`; no route-table lookup or choice-route regeneration is needed. Require candidate strategy ids to follow `ranking.shortlist` order and the chosen candidate's id to equal `ranking.strategy_id`; a mismatch fails. Expand each compact constraint with its candidate's pool. Apply point 3 to every `worker_choice` record, including new empty-shortlist records; report that coverage separately from ranked class/route reproduction. A missing ranked snapshot fails rather than falling back to the legacy rule.

- [ ] **Step 1: Write the failing tests** with synthetic exports and invented names: `test_reproduced_export_passes`, `test_a_changed_class_fails`, `test_a_reordered_shortlist_fails`, `test_a_duplicate_attempt_is_a_stray_record`, `test_reserved_beyond_the_stored_headroom_is_a_funding_violation`, `test_a_later_record_with_more_headroom_and_no_reset_is_resurrected_capacity`, `test_ranked_paid_record_fails_and_is_counted`, `test_earlier_records_follow_the_routers_own_filters`, `test_legacy_worker_choice_records_follow_the_legacy_rule` (a copy of the pilot's record shape: one constraint, no `snapshot_id`), `test_a_record_without_snapshot_detail_fails_and_is_counted`, `test_the_report_holds_no_identifier`.
- [ ] **Step 2: Run** `scripts/test_sharded.py test_qualification_replay_strict`. Expected: FAIL, no module.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** again. Expected: OK.
- [ ] **Step 5: Commit** `test(qualification): strict replay over exported admission records`.

### Task F7.2: The canary harness runs ranked on the admitted route

**Interfaces:**
- In `canary` mode on the approved production ledger, `plan_assignments` sends no explicit choice or synthetic observation. Rotate `software-engineer`, `code-reviewer` and `explorer-agent` over `low` and `medium` complexity bands. Refuse `table_outdated`, `table_empty`, `no_generated_route` and `no_qualified_path`; abort any returned admission whose `mode != 'ranked'` before dispatch.
- Before the run, approve the qualified OpenCode subscription path ids and pinned table inputs, restricted to providers the isolated server is signed in to. Record the existing `routes_version` (rules, qualifications and table version), table content digest, effective host constraints and settings revision in the existing approval record. Use the pinned table/rules to report structural coverage of the six profiles and qualified demand; do not create six preview identity lists. Structural coverage is not a promise that windows are feasible. A changed input requires renewed preflight and approval. Pilot and isolation modes retain the single explicit `--route` contract.
- `CanaryEnv` retains each returned admission by attempt. Dispatch and attach facts come from that admission's identity, billing class and pool, checked by `routing_launch.admitted_check` against the same catalog and qualified path. `Run.prompt`, the server guard and the off-route check receive that admitted identity for the specific attempt. Require subscription billing, an approved path, a strategy/identity present in the pinned table, and exact equality to the admission; membership somewhere in approved inputs is insufficient. Replace the fixed model guard and fixed `pilot-sub` attach values. Keep write-ahead call reservations and title-call accounting intact.
- Keep `prepare` on the existing `--serve` child. F4.4 supplies its effective host harnesses; it reads local state only, preserving the registered 50 ms warm and 500 ms cold preparation budgets. Before each preparation, outside its latency timer, call the shared `Service.preview` once for that exact request and reject changed approval inputs. Pass only its kept subscription routes to `routing_observations.refresh_needed`; this delegates stale-and-needed ChatGPT reads to the existing bounded reader with `FRESH_SECONDS` (900 seconds) and backoff, plus any separately qualified F4.5 reader. Then use `window_status` to identify missing/stale demanded windows and whether any eligible pool is fresh; `more > 0` is incomplete status, not proof of freshness. Preview does no usage read itself. Record preview/refresh duration separately; no model, timer, polling or retry loop. Recheck pinned inputs, the admitted identity and current qualified path before dispatch; release an unsent admission if inputs changed after preview. Never refresh all qualified pools merely because they are stale.
- After each settled launch, convert and store only reported windows through `opencode_launcher.windows` and `routing_observations.store_window`, as `_finish` does. Use the observation's actual timestamp, not settlement time. A launch longer than 900 seconds may still leave stale data. Telemetry has not been proved to contain both Claude windows; a missing `7d` is not refreshed by a reported `5h`. Before the next admission, if no eligible pool is fresh, stop with a resumable `seed_required` result and name the needed pool/window; resume only after an operator seed that preserves observable cap accounting. Never invent a fresh observation or launch a probe. An unknowable cumulative delta takes the incomplete outcome of Edit 7 point 9 instead.
- `--serve` does not clear abandoned-window debits today. Keep that conservative behavior: the canary never drops a debit or manufactures a clearing admission. Report a debit that prevents progress for operator reconciliation through the existing one-shot procedure. Any unresolved launch or recovery error still aborts. This avoids inventing a standalone automatic-clear operation; `subscription_reconcile.clear_debits` currently runs only inside a validated admission transaction.
- Enforce Edit 7 point 9 only after its approval/registration. Keep each pool/window's cumulative positive observed increases from the seeded baseline, including unrelated use. Before dispatch, compare the admission's reservation plus this run's other unsettled reservations against the remaining 100-thousandths allowance. On insufficient allowance, release the undispatched admission as `not_dispatched` and end incomplete with `allowance_exhausted`. An observed overrun aborts; actual cost can exceed the reservation, so this is not a provider-side hard stop. At existing preflight, pre-dispatch and post-settlement boundaries, `now >= earliest_reset` ends incomplete as `window_usage_unknown`, even if no canary launch crossed the reset. Any observed earlier reset or ambiguous decrease/gap does the same. Release any undispatched hold; finish accounting/recovery for already dispatched work without sending more. Do not replenish, restart the deadline or erase unknown usage by reseeding. Before that deadline only, missing telemetry may pause for evidence while the cumulative delta can still be established. Keep the existing seven-day maximum and all concurrency, recovery, funding and unresolved-launch checks; the reset deadline is stricter.
- **Reservation-coverage and deadline preflight (call 36 proposal, not a usage forecast).** For each approved pool/window, take `d_w` as the largest qualified demand among the pinned table's subscription routes on approved paths and `r_w = Ledger._reserve(d_w, None, settings['margin_bp'])`, reusing admission's calculation for these generated subscription routes. This conservative bound needs no per-profile approval lists. Report `floor(100 / r_w)` and the conservative minimum across demanded windows beside the 60-admission minimum, six-profile structural coverage and enabled funding classes. With margin 2000 basis points, demand 10 reserves 12, so coverage is 8 full reservations; this does not predict actual usage. Read the existing bounded `LocalSource.windows(pool, now)` snapshot once per approved pool for each demanded window's `reset_at`; require known future resets and record their minimum as `earliest_reset`. Missing data requires an operator seed or a request-specific stale-and-needed refresh, not an eager refresh of every approved pool. Show time remaining beside coverage. These are local store reads outside preview and the preparation timer. A coverage value below 60 requires explicit start approval acknowledging that lower observed usage might permit more admissions but completion before the reset is not guaranteed. Missing demand, profile coverage or reset metadata blocks start; a reset already passed is stale/unknown even when `window_status.fresh` is true. Neither approval nor low observed deltas relaxes the cap, reset deadline or sample minimum. Report fewer than 60 usable admissions as incomplete, never a pass. No extra model call or timer is needed. None of this paragraph is authorized for a live run until call 36 is resolved and registered.
- The builder maps every row of the existing Canary table in `docs/smart-routing-validation.md` to its check and reported evidence: one project, 60 admissions, three specialists, two bands, each enabled funding class, window cap, seven days/80-admission stop, concurrency, aborts and kill switch. Strict replay and latency budgets still gate a pass. No pass is inferred merely from successful calls.

**Cost (calls 32, 34 and 36):** one pinned-input preflight and one shared local preview per request, store writes from telemetry already produced and a stale-and-needed zero-token reader call when required. No six-profile approval snapshots, new model calls or persistent route cache. Removing the remaining request preview would lose the stale-AND-needed check unless preparation supplied an equivalent shared selection; refreshing all stale qualified pools is not an accepted shortcut. Switching all prepares to one-shot would mix reader startup into the registered local-state latency budget. Existing debits remain conservative and may require operator recovery.

- [ ] **Step 1: Write failing tests** in `tests/test_qualification_canary.py`: `test_canary_admissions_are_ranked`, `test_canary_aborts_a_non_ranked_admission`, `test_canary_refuses_to_start_without_generated_routes`, `test_canary_dispatches_the_admitted_route`, `test_canary_attaches_the_admitted_pool`, `test_guard_requires_the_admitted_identity_not_just_an_approved_one`, `test_canary_stores_the_settled_window`, `test_canary_prepare_stays_served`, `test_canary_refreshes_only_a_needed_stale_chatgpt_pool`, `test_canary_never_clears_abandoned_debits`, `test_canary_window_cap_survives_a_reset`, `test_canary_refuses_demand_beyond_remaining_allowance`, `test_canary_stops_after_a_window_overrun`.
   - Also pin `test_canary_uses_one_shared_request_preview`, `test_approval_pins_paths_and_table_without_six_preview_lists`, `test_changed_approval_inputs_require_new_approval` (table content, qualification, rules, host constraints or settings revision), `test_preview_never_refreshes_a_nonshortlisted_pool`, `test_partial_window_telemetry_does_not_refresh_the_missing_window`, `test_cap_preflight_reports_reservation_coverage_not_a_usage_forecast`, `test_low_coverage_requires_explicit_start_approval`, `test_allowance_exhausted_releases_before_stopping_incomplete`, `test_reset_gap_ends_incomplete_without_replenishment`, `test_unsettled_reservations_reduce_remaining_allowance`. These tests use synthetic stores and no live call. Call 36-specific cases remain gated on its approval before implementation/registration.
   - The coverage test asserts demand 10, margin 2000, reservation 12 and coverage 8; a changed margin uses the same helper as admission. `test_preflight_reports_earliest_demanded_reset`, `test_reset_deadline_ends_incomplete_even_without_a_crossing_launch` and `test_recent_observation_after_saved_reset_is_not_seed_ready` pin the stricter deadline, including release of an undispatched hold and settlement of already sent work.
- [ ] **Step 2: Add the production-shaped P6 test** `test_canary_runs_on_the_local_source_across_a_stale_gap`: real service, `LocalSource`, real temporary stores, deterministic transport doubles only. Seed, rank, dispatch/attach the admitted facts, settle/store, advance over 900 seconds, assert the Claude path needs a seed, then seed and resume. Separately exercise the ChatGPT refresh with a fake reader executable and no production credentials. Verify served preparation never starts it.
- [ ] **Step 3: Run** `scripts/test_sharded.py test_qualification_canary test_qualification_harness`. Expected: FAIL.
- [ ] **Step 4: Implement**, keeping each Python file within its limit and resolving call 36 before its dependent steps. Add the strict replay command, pinned-path/table preflight, seed/resume steps and per-bound evidence map to the runbook. Extend F7.1 tests for null route versions, new worker-choice records and missing ranked detail.
- [ ] **Step 5: Run** `scripts/test_sharded.py 'test_qualification*'` and `scripts/validate.py`. Expected: OK.
- [ ] **Step 6: Commit** `test(qualification): the canary uses ranked admissions and real window state`.

After F7 and F8 have both merged, the coordinator runs the strict replay over the exported pilot ledger and records the result, and the explanation of the 21st record, under Results in `docs/smart-routing-validation.md`, in a PR of its own. Waiting for F8 keeps that edit from colliding with F8's edit of the same document.

---

## Slice F8: Historical replay runner

**Owned files:** `tests/qualification/replay_history.py`, `tests/test_qualification_replay_history.py`, `docs/smart-routing-validation.md` (the store field-mapping edit only; F5.1 has merged by then, and Results are written by the coordinator after the run).

### Task F8.1: Field mapping and the runner

Edit 6 requires a dated edit that maps each store's fields before any run, written from the store schemas only.

**Interfaces:**
- Produces: `replay_history.run(stores, *, settings_path=None) -> dict`, the advisory report of Edit 6: sessions found, exclusions by reason, label distributions, the disagreement rate overall and by specialist, band, funding class and store, and the counts of unknowns and pending arrivals. It opens each store read-only (`mode=ro`), reads only the metadata columns the mapping names, holds titles in memory for the keyword match only, and writes extracted rows only under `~/.local/state/vibe-workflow-replay/`.

- [ ] **Step 1: Inspect the schemas** of the three stores (table and column names only, no rows) and write the dated field-mapping edit. Commit it alone: `docs(routing): pre-register the historical replay's store fields`.
- [ ] **Step 2: Write the failing tests** with synthetic stores: `test_eligibility_and_each_exclusion_is_counted`, `test_labels_follow_the_registered_rules` (Edit 6 with Edit 7's specialist mapping), `test_a_session_seen_twice_counts_once`, `test_a_qualification_session_id_blocks_the_report`, `test_no_title_or_session_id_reaches_the_report`, `test_stores_are_opened_read_only`.
- [ ] **Step 3: Run** `scripts/test_sharded.py test_qualification_replay_history`. Expected: FAIL, no module.
- [ ] **Step 4: Implement.**
- [ ] **Step 5: Run** again. Expected: OK.
- [ ] **Step 6: Commit** `test(qualification): advisory historical replay over session metadata`.

The run itself happens after a path is qualified live, as Edit 6 orders.

---

## Before the live session

These join the [pre-canary requirements](2026-11-08-smart-routing.md#pre-canary-requirements) of the 8 November plan:

1. F1 to F8 are merged.
2. **The guidance is published again in the new row shape.** The coordinator runs the existing guidance publication procedure once: each vendor guide page is read by a model once and turned into rows of `work_kind`, complexity and risk; the catalog input gains `provider`, `list_price` and `display_name`; `scripts/routing_table.py refresh` compiles the production table. Until the due jobs of L2 exist, the catalog and guides are refreshed by hand.
3. Edit 7 is registered (Task F5.1) and the strict replay over the exported pilot ledger has passed (after F7).
4. `scripts/validate.py` has run once with the real home and reports `model_names: passed`.
5. Under approved decisions A and C: skip the gateway check and leave the gateway and Codex-native paths unqualified. Run the approved isolation probes, drill and demand measurement; qualify only measured OpenCode subscription paths. No OpenRouter key or organization is needed for this session. Under clarified B, the router must automatically select and launch models on those subscription paths; the ranked canary cannot be replaced by recommendation-only or explicit-choice launches.
6. Use this order: compiled table → measured `qualify_path` requests → `seed_window` for every demanded window → activation → historical replay and its recorded advisory report → approved ranked canary. The strict pilot replay has already passed. Seeding itself requires qualification, not a table.
7. Record the approved subscription paths, pinned table/route inputs and existing call caps before the canary, arrange its quiet window, and seed again after a stale gap where no eligible pool is fresh. Exact admitted identity still controls each send. Remove the project from `managed_projects` after the approved canary. The user schedules the live session; this plan grants no new live calls.

**Release coverage beyond this canary:** F4.5's OpenCode subscription binding and F4.6's two native clients must have their synthetic end-to-end checks and supported-client evidence recorded. The existing two-provider canary cannot establish three-provider or native-client coverage. Reuse existing observations where applicable; request only the missing bounded checks under separate approved caps. Lack of one connection on this machine does not require every user to connect it, but leaves its live support claim unverified.

## After the canary: L1 to L4

These are outlines with the decisions already fixed. Each is expanded into tasks after the canary, when real records exist.

**L1, analysis rows and scores (recorded, not applied).** It lands within 14 days of the canary, or the canary's admission records are exported first.

- Module `runtime/routing_learning.py`. Storage per project under `routing/projects/<key>/` in user state, where `<key>` is the existing `routing_observations.assignment_scope` digest: `analysis.jsonl` (append-only, 60 days) and `scores.json`, whose digest is the score version.
- Trigger: the `settle` operation, when the root has a canonical acceptance and no unresolved hold, and a one-shot `analyze` operation. Rows are idempotent by root and attempt.
- A row is built from the admission record of Task F5.4 and the root report. Records with an old role name are skipped.
- **New recording L1 must add,** because today's records lack it: the window readings before and after each launch, taken from readings the launch already makes; review rounds, rescues and escalations per assignment; finding counts by severity; elapsed time; and the served model version where the harness exposes one.
- **Starting demand** (design section 5.4): once window use is recorded per model, a model with no launch on a path starts from its predecessor's value in the same family, or from the highest value recorded on the pool when it has no predecessor. Until a model has its own value, the path's qualified demand applies.
- Score rules as in design section 5.9: first-pass success, rework factor, at least 5 results to reorder, full weight near 20, 60 days, half weight for a floor-filled profile, borrowing from the work kind at 10 results, a new score version when a kind of task gains 5 results.

**L2, due jobs and importers.**

- Module `runtime/due_jobs.py`; a small `routing/due.json` with one `expires_on` per job, read at the start of `scripts/workflow.py` and `scripts/routing.py`. A due job runs once in a bounded detached process, with one runner at a time and a backoff, following the lock and backoff in `runtime/codex_rate_limits.py`. No detached-process pattern exists in the repository today, so L2 builds it.
- Importers for the catalog (a conditional request, so an unchanged catalog costs no download), the benchmarks and the vendor guide digests. The table is recompiled locally after a change.
- Model family and the successor rule of design section 5.7; a model without a family is never a successor.
- Benchmark qualification with the 2% tolerance of Edit 7. A benchmark result that names no effort level qualifies a model only at its default effort (design section 5.5).
- **Changed guide pages** (design section 5.8): the vendor-guide job stores a digest per page and calls no model. `prepare` and `check` report `guidance_page_changed` with the page ids until the guidance is published again. `clients/guidance-publication.md` changes so that a due job may recompile the table from an unchanged guidance file with a new catalog or new benchmarks, while turning a changed page into rows stays a model read in a maintenance session.
- **To verify at the start of L2:** that each benchmark source downloads without a credential and may be used this way; whether a vendor states replacements anywhere beyond its guide; and how the catalog's model ids map to the ids each harness uses, since the two differ on some paths.

**L3, chronological replay, promotion and rollback.** The promotion test of Edit 7, one-shot `promote_scores` and `rollback_scores` operations, and the value order `list price × rework factor ÷ first-pass success` once promoted. It replaces tasks S4.1 and S4.2 of the 8 November plan.

**L4, optional ranking model.** As design section 5.11, off by default, built only if the L3 replay shows it beats the table with learned scores.

## Required next release: API Usage

This is a user requirement, not an optional backlog item. It carries forward the older plan's [managed API Usage send path](2026-11-08-smart-routing.md#follow-ups-from-merged-work).

- [ ] Add direct subscription-linked API allowance discovery, fresh balance/expiry observations and secure credential binding for the supported providers. Reuse the existing allowance ledger and readers; do not let OpenRouter consume a direct grant.
- [ ] Wire the existing API Usage execution entry points through `prepare`, `dispatch`, per-request `check` and completion/uncertain settlement. Keep activation, allowance ceiling (98% by default), protected balance, reservations and uncertainty rules.
- [ ] Activate the retained subscription/API equation `API_share = 1 - (1 - S) * (1 - E)` only for eligible enabled API routes. Test expiry pressure, overlapping subscription windows, grant exhaustion, purchased-credit exclusion and API Usage OFF preserving subscription routing.
- [ ] Run the missing billing and dispatch qualification under the next release's explicit funding scope, then record actual provider cost and rollback/recovery behavior. OpenRouter decision/launcher delivery remains a separate deferred item; API Usage does not wait for OpenRouter.

This release preserves those equations and existing recovery/configuration controls but adds no paid-route launch. Learning remains required under L1–L3; the API deferral does not defer or remove learning.

## Open checks of design section 8

| Check | Finding | Where it is handled |
|---|---|---|
| Window use per launch | Not recorded. The store keeps only the latest reading per pool and window, with no before, after or per-model value. | L1 adds the readings; until then a route uses its path's demand (F4). |
| Served model version | Not recorded. OpenCode stores `resolved_model: "unknown"` and the Codex adapter stores no execution model. | L1 records it where a harness exposes it; otherwise a silent provider update cannot be detected, and the plan says so. |
| Benchmark sources | Needs network access and a terms check. | Start of L2. |
| Vendor replacement statements | Needs network access. | Start of L2. |
| The package ships `templates/` | Yes: the package is the tracked files minus `EXCLUDED` in `runtime/package_manifest.py`. `scripts/read_instructions.py` accepts only Markdown templates, so code reads the JSON directly. | F1.1 pins it with a test. |
| Catalog `family` is uneven | Confirmed in the design. | L2: no family, no successor; the vendor guide naming the model is the deciding condition. |

## Coordinator calls for the user's review

Decisions this plan makes that the design left open or that differ from it:

1. The row key change moves from F5 to F2, and slices F7 and F8 are added (see [Slices and order](#slices-and-order)).
2. The `when_to_use` sentences are written locally from the toolkit's meaning, not copied.
3. The level definitions and examples in Task F1.1 are new text.
4. A specialist that neither writes nor reviews (planning and investigation) takes delegation's worker path with read-only claims.
5. The stored field key stays `role`; its values become specialist ids. Renaming the key would change the wire contract of every client and need a second reader for stored records. The unification design can revisit it.
6. The admission bucket is work kind and complexity, not specialist and complexity, so the share accounting does not thin out over 24 specialists.
7. **Deferred with OpenRouter.** Its future step remains separate from subscription/API share accounting; existing records keep accounting class `api` and billing class `gateway`. No new gateway decision fields ship now.
8. **Deferred with OpenRouter.** Gateway pool-full rules no longer gate this release; subscription admission keeps existing window feasibility and pressure arithmetic.
9. Each per-request explicit choice gets its own generated route on a qualified path even outside the table. When the decision uses settings choices, each gets a choice route regardless of coverage by another table row. Choice routes have no `strategy_id`; ranked mode uses strategy routes only.
10. Old `worker_choices` keys load, match nothing and are reported in `settings_warnings`; today an unknown key fails the whole file.
11. OpenRouter proposals use `explorer-agent`; an OpenRouter worker defaults to `software-engineer`.
12. The `terms` input of the table compiler is removed with the control roles, since nothing else reads it.
13. Edit 7's nine points, among them: 2% tolerance; promotion at 5 results, 3 kinds of task and 80%; the legacy rule for the pilot's 21 records; the dated correction of Edit 6's sentence that the pilot's snapshot detail was gone.
14. The model-name check takes its names from the compiled table until the downloaded catalog exists.

Added after the independent review of this plan. Calls 15 and 20 still need disposition. The approved cuts defer calls 16–17 and replace call 18 as stated below:

15. **`check` stays a reserved value of `role`** for a check run, which uses no model and is not a specialist. Design section 5.2 says the role value goes. Removing it means a new stored shape and about 25 rewritten sites in three files that have no spare line (`runtime/delegation.py`, `runtime/evidence.py`, `runtime/changes.py`), for no change a user can see. Alternative: do that rewrite as its own slice after the canary.
16. **Deferred: stale-full gateway inference.** No new stale-reading exception is implemented for subscription admission. Future gateway work must resolve fresh-only versus reset-based evidence before adopting it.
17. **Deferred: non-fitting pools opening a gateway.** A subscription request with no fitting candidate waits. No gateway pool-state taxonomy is needed now.
18. **Approved simplification: one subscription shortlist after host and static eligibility checks.** Keep the first 10 eligible subscription ids from the full filtered ranking; paid/unreachable entries consume no places. No prior 64-id cut, dual lists or permanent/temporary taxonomy. Later window/reader refusals consume their place rather than causing refill or extra reads.
19. **Future gateway demand remains one value per path**, the largest live observed receipt cost, rounded up in micro-dollars (F4.1). Decision A defers producing it. A dearer model can be under-reserved until L1; provider receipts settle real cost, and the organization and key limits still apply. Stricter alternative: scale by list price.
20. **The usage canary takes the cheapest priced strategy of the table,** because no catalog is on the machine before L2. Design section 5.14 says the lightest model the catalog lists.
21. `coordinator` becomes `orchestrator` through one reader, `task_profile.orchestrates`, which also accepts the stored value. The operation kinds `coordinator-work` and `operation-coordinator`, the provenance value `coordinator` and the accounting stages keep their names; the vocabulary gate covers six old names and leaves `coordinator` and `check` out.
22. The ledger stage follows the bucket's work kind: `review` gives the stage `review`, every other work kind `worker`. The template enforces that a specialist reviews exactly when its work kind is `review`.
23. OpenRouter has two paths, workers and proposals, because the two harnesses differ; no two paths may share a harness and provider.
24. Generation skips two strategies that share a route identity and differ in endpoint, with a reason, where today the whole manifest would be refused.
25. Design section 5.12 (advice in a project that is not managed) has no task here; see the self-review.

### Product decisions after the root-cause diagnosis

**Decision status, 10 November 2026:** the user approved A ("Production OpenRouter: Ok to defer") and C ("Ok for leaving it unqualified for now"). A replaces the earlier requirement to include production OpenRouter workers in this live session. The user then clarified B: "the Router should still select a model and launch it (via subscription connections)"; this is required now, while OpenRouter-specific work may follow in a later release. The earlier term "ranked gateway launcher" conflated that core requirement with the paid OpenRouter adapter. The plan's other flagged calls and Edit 7 still need approval.

26. **Decision A — gateway decision and execution. Approved: defer both until after release.** The current production path has no writer for the `credit` window, fresh OpenRouter balance, or organization binding. The later user-approved cuts also remove the offline opening rule, gateway shortlist and new gateway replay fields from this release. Leave gateway paths unqualified and skip the live gateway check; preserve existing refusal, explicit-choice compatibility and recovery. The future slice must supply the missing producers, ranked consumer and its own replay registration, resolve `activation.funding_ref` to an explicitly funded ledger organization, add bounded stale-and-needed balance reads, and fix the nested host contract. That future scope still needs authorization and budget; it is not hidden inside F4 or F5.
27. **Decision B — ranked model selection and launch. Clarified: required now through subscription connections.** Reuse `opencode_launcher.launch` and the F4.4 host-aware admission path; F5 selects the eligible subscription strategy and the launcher sends exactly its admitted provider/model/effort, then settles the attempt. No explicit model selection by the user is required. F7's ranked canary proves this end to end. Only the paid OpenRouter-specific decision/execution layer may follow after release under A. Its future execution adapter must connect ranked admissions to the managed OpenRouter worker with endpoint pinning, reservation and settlement; today's explicit-choice worker does not establish that handoff. `API_FUNDING_QUALIFIED=False` blocks that dollar path, not the required subscription launcher. This clarification closes the earlier ambiguous timing question without approving the remaining plan calls.
28. **Decision C — Codex-native demand. Approved for the scheduled OpenCode canary only:** leave `codex-chatgpt-subscription` unqualified in that session. The latest client requirement adds native-only Codex and Claude routing to this release (F4.6). It does not approve proxy demand or additional live calls; collect the missing client evidence separately before claiming release support.

### Additional flagged calls

29. **Below-resolution demand: 10 thousandths per window.** This is a conservative starting reservation, not a measured cost bound. The diagnosis proposed 1; that lighter alternative admits more work but reserves less uncertainty. Missing samples still prevent qualification.
30. **Per-request choice ids and path-based dispatch checks.** Hash identity plus endpoint into a stable choice id; check the qualified path and admission identity at dispatch. A table removal does not revoke a live hold; unqualification does. Entitlement marks do not cross between strategy and choice ids.
31. **Host-reachable pools only.** Funding decisions consider only paths the launching host can send. A full Codex pool cannot borrow a Claude or gateway route its host cannot dispatch. Empty/missing collector-mode harness constraints fail closed, even with Smart Routing off.
32. **Staleness remains fail-closed in routing.** Store settled readings in the canary and pause for an operator seed after a stale gap when no eligible pool is fresh. No Claude probe launch or extra model call. This recovery preserves the scheduled canary's safety; it does not resolve the routine Claude automatic-routing blocker recorded above.
33. **Approved simplification: measure subscription-only record size first.** Keep existing size, history and hold bounds while measuring F5.4's ten-candidate records and maximal ledger. Preserve the below-90% settlement proof and `record_too_large` refusal. The earlier 7,168-character/15 MB proposal is withdrawn; a measured failure needs a new scoped decision, not an automatic increase or history reduction.
34. **Approved simplification: one request preview and pinned approval inputs.** Approve subscription paths and table inputs, without six duplicate preview identity lists. Retain one shared non-admitting preview for each request's stale-AND-needed refresh outside served preparation's latency timer. Exact admitted-identity checks and conservative abandoned debits remain. Call 36 and Edit 7 point 9 still need separate approval/registration.
35. **Generate routes in memory, with no `routes.json`.** This departs from design section 5.4's stored routes. The already-loaded table plus rules and qualifications produce each request's routes; route reads write nothing. `routes_version` hashes those inputs for audit and canary preflight change detection; strict replay consumes stored candidates without regenerating that digest and also accepts a null version.
36. **Proposed whole-run window allowance and incomplete outcomes.** Approve Edit 7 point 9's cumulative 100-thousandths cap per pool/window, including unrelated positive use, with no reset replenishment. Any reset of an approved demanded window ends incomplete as `window_usage_unknown`, so the run must finish before the earliest reset (potentially less than five hours for Claude), not merely within seven days. Preflight shows that time remaining and margin-adjusted reservation coverage: demand 10 at margin 2000 reserves 12, allowing 8 full reservations, not 10. Insufficient reservation room ends incomplete as `allowance_exhausted`, releasing an undispatched hold first. Coverage below 60 requires explicit start approval and never promises sample completion. The demand floor is not measured usage. Alternative: define and approve a different cap-accounting/reset rule before registration/run; neither treating unobserved pre-reset use as zero nor charging a reservation instead is authorized by this proposal.

37. **Latest user scope, approved as requirements:** API Usage is excluded now and required next release; OpenCode routes among available connected ChatGPT/Codex, Claude and OpenCode subscriptions; Codex and Claude Code route only to native models. This adds F4.5/F4.6 and narrows C to the scheduled canary. The user also requested an independent critical review for unnecessary controls. Detailed adapter bindings remain subject to that review; the requirement does not authorize live calls or paid overflow.

38. **Approved cuts, with registered requirements preserved.** Defer gateway-only decision/replay work; use one subscription shortlist without its gateway failure taxonomy; measure before raising capacity; simplify duplicate canary previews; run focused checks per task and one final full-suite gate where required; use one independent F2 reviewer covering all affected areas. Keep F8, strict replay, sample minimum, registered caps, latency, concurrency and recovery requirements. This approval does not approve call 36, the whole plan, runtime implementation or a live run.

## Self-review

- **Spec coverage.** 5.1 and 5.2: F1.1, F2. 5.3: F1. 5.4: F4; the starting demand of a model with no launch in L1. 5.5: F2.2 (row key), F5.2 (price, order, shortlist); benchmark qualification and the default-effort rule in L2. 5.6: F5.3 for subscription allocation; API Usage is next-release work and gateway decision/execution is deferred by the approved scope amendment. 5.7 and 5.8: L2, including the changed-page report and the guidance-publication rule. 5.9: L1. 5.10: F5.1, F5.4, F7, L3. 5.11: L4. 5.12: not covered, see below. 5.13: F3. 5.14: F6. Section 8: the table above.
- **Not covered here:**
  - Design section 5.12, advice in a project that is not managed. Today `prepare` in such a project requires observations from its caller (`scripts/routing.py:425`) and returns strategy ids, not advice a person can read. The OpenCode workflow client design (#243) owns the session-start advice; it will consume the subscription shortlist of F5.2 and the generated routes of F4, and its plan adds the task.
  - Session titles and the vocabulary unification, which have their own designs.
- **Independent review.** A first review of this plan returned six blocking findings: the OpenRouter path could not become a valid route; the record was not enough for an exact replay; the legacy rule relied on data the validation document says is gone (it is not: the export was checked); removing `check` and `coordinator` was under-scoped; F4 broke manifest readers it did not own; and an unread pool could look like a capability gap. Each is fixed in the task it concerns, and the calls it forced are 15 to 25.
- **Round 2 and diagnosis (historical).** Head `986c7230` received a second BLOCK (three blocking and nine non-blocking findings). The independent root-cause diagnosis found tests supplying data with no production writer. That revision added the producer/reader table, P1–P7 traces, host constraints, write-free route lookup, reason classification, unsupported production paths and admitted-identity canary checks. The approved cuts now remove only the gateway-specific classification/decision work; the production-source proofs remain.
- **Deliberate differences from the diagnosis.** Mode-specific routes replace its combined 37-route proposal (now 10 ranked subscription candidates, 16 settings choices or one explicit choice). Route-version hashes cover rules, qualifications and table version; the canary also pins table content instead of maintaining six approval lists. Below-resolution demand remains proposed as 10 rather than 1. Served preparation retains latency comparability, request-specific stale-and-needed refresh and conservative recovery. Existing capacity bounds remain until maximum-fixture measurement supports an approved change.
- **Round 3 and bounded corrections.** Head `16b8596d` received four medium blockers (consumer ownership, older gateway prerequisites, shortlist preview and cap registration) and five low findings. C1–C6 address the correction delta, including derived values and nested calls, not only stored production inputs. The reported nested hold leak was disproved by `execute`'s `finally` settlement; its empty-harness refusal remains deliberately closed pending a gateway consumer. Partial telemetry, actual capacity and live latency remain implementation/qualification checks, not proven facts.
- **Latest amendment self-review.** Task profiling is F1; the three OpenCode subscription families are F4.4/F4.5; native-only launch and activation consumers are F4.6; allocation remains F5; live proof stays scoped to each client; next-release API Usage has explicit work above. The prior technical PASS at `390485f9` does not cover this expanded client scope. F4.5's installed-client allowance mapping and F4.6's native bridge are evidence-dependent implementation steps, not existing proven capabilities.
- **Approved-cut self-review.** F4's shared selection, F5's admission/replay producer, F7's strict replay and canary consumers, coordinator calls and the older plan now use the same subscription-only scope. P8/P9 have named end-to-end tests. No registered validation text changed. Expanded-scope review blockers remain open: Claude telemetry/catalog/bridge, harness effort/identity publication, native/Go demand and Go paid-overflow exclusion. Removing complexity does not supply those missing facts.
- **Approval boundary.** A/B/C and calls 37–38 record user scope choices. Calls 16–17 are deferred; 18, 33 and 34 are revised by the approved cuts. Calls 15 and 20, remaining additional calls 29–32 and 35–36, and Edit 7 still need disposition. Future gateway demand call 19 belongs to its deferred release and does not gate subscription-only approval. These choices do not approve the whole plan, runtime implementation or a live run.
- **Type consistency.** `task_profile.specialist` is roster-only and `stored` also reads old names, in every task. Route fields in F4.2 are the ones `_manifest_reason`, `_valid_route`, `_candidate` and `_choice_matches` read, and a test runs every generated route through the collector's check. F4 preview/collect carry one `shortlist`; F5 stores feasible subscription candidates and F7 calls the same `routing_funding.choose` with those fields. No gateway replay interface remains.
