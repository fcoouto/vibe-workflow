# Smart Routing Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Smart Routing pick the most cost-effective capable model from one specialist roster, on routes generated per machine from model-free path rules, with an exact replay, and prepare the per-project learning loop.

**Architecture:** One JSON template (`templates/task-profile.json`) holds the Task Profile contract and the roster; every role list is replaced by it. Shipped path rules name no model; each machine generates its routes from qualified paths and the compiled ranking table, which is keyed by work kind and ordered by list price at compile time. Admission walks the shortlist in the funding order subscription, API Usage, OpenRouter, wait, and stores what the strict replay needs.

**Tech Stack:** Python 3 standard library only, `unittest` through `scripts/test_sharded.py`, the existing SQLite ledger, JSON files in user state.

**Spec:** [Smart Routing redesign](../specs/2026-11-10-smart-routing-redesign-design.md), approved by the user on 10 November 2026. It amends the [8 November design](../specs/2026-11-08-usage-routing-opencode-subscriptions-design.md) and the [C.3 wiring design](../specs/2026-11-10-smart-routing-c3-wiring-design.md). The [8 November plan](2026-11-08-smart-routing.md) stays the record of the merged slices, the pre-canary requirements and the cutover tasks; this plan owns slices F1 to F8 and L1 to L4.

## Global Constraints

- **Lightweight rule (binding).** On-demand reads only; no timers, polling or daemons; no model call to measure usage; short bounded processes that fail closed; cheap `prepare` and `check`. A task that adds a cost states it with the lighter alternative.
- **Exact replay.** The strict replay reproduces every admission record, calls no model and reads no live data.
- **File size.** Each Python file is at or under 1,000 lines. The files with a fixed ceiling in `scripts/validate.py:21-33` must not grow: `runtime/routing.py` 1506, `runtime/delegation.py` 3369, `runtime/evidence.py` 8505, `runtime/changes.py` 6074, `runtime/state.py` 2613, `runtime/usage_budget.py` 1553, `runtime/adapters/codex.py` 1579, `runtime/setup_observation.py` 1526. All but `runtime/state.py` and `runtime/setup_observation.py` (1515 lines) are exactly at their ceiling, so a change in them replaces lines and adds none. A PR that shrinks one lowers its ceiling in the same PR. `scripts/routing.py` has 940 lines; new operations go into runtime modules and the script only dispatches.
- **No model name** in `runtime/`, `scripts/`, `policies/`, `clients/`, `skills/`, `templates/`, `AGENTS.md` or shipped JSON. Tests use invented names. Dated evidence records under `docs/` keep theirs.
- **No API key or organization** before release, except the OpenRouter key approved for the gateway check. API Usage stays deferred; this plan only fixes its place in the funding order.
- **Stored history.** Records written before a change keep their old values. They are read as written, never rewritten and never used for learning.
- **No stored task text, finding text or file paths** in learning or replay records.
- **Tests run under a private home:** `T=$(mktemp -d); HOME="${T:?}" python3 -B scripts/test_sharded.py <modules>; rm -rf "${T:?}"`. The full suite is the same command with no module. `python3 -B scripts/validate.py` runs before every push.
- **Delivery.** One PR per slice with the owned files listed in the slice. An Opus worker builds it and an independent Opus reviewer checks it. The coordinator verifies the exact head, runs the listed gate on the merged tree and merges with `gh pr merge --merge --match-head-commit <sha>`.

## Review Focus

Each line names a condition the design implies and the task whose tests pin it.

1. **A settings file or stored plan still uses an old role name.** It must load, match nothing new, and be reported once; it must never fail the whole settings file. Pinned in Task F2.2 (`test_legacy_worker_choice_key_is_reported_not_fatal`) and F2.3 (`test_stored_plan_with_legacy_roles_still_reads`).
2. **The template file is missing or damaged in an installed package.** `prepare` must block with one reason and admit nothing. Pinned in Task F1.1 (`test_missing_or_invalid_template_fails_closed`) and F2.2 (`test_prepare_blocks_when_the_template_is_unavailable`).
3. **A window reading is stale or absent while every other pool is full.** OpenRouter must stay closed, because an unknown is not a full window and a route nobody has read is not a missing route. Pinned in Task F5.3 (`test_stale_or_unread_window_never_opens_gateway`, `test_unobserved_pool_is_not_a_capability_gap`).
4. **A path is qualified but the table has no candidate for it,** or the table exists and no path is qualified. Activation must refuse with a reason that says which. Pinned in Task F4.3 (`test_activation_reports_no_generated_route`, `test_activation_reports_no_qualified_path`).
5. **A ranking table compiled before the change is still in user state.** Ranking must report it as outdated and fall back to explicit choices, never read a four-part row. Pinned in Task F2.2 (`test_table_from_the_old_schema_is_reported_outdated`).

## Slices and order

| Slice | Content | Gate | Starts after |
|---|---|---|---|
| F1 | Task Profile template with the roster, loader, generated brief section, `prepare` returns missing fields | Full suite | now |
| F3 | Jev removal | Affected modules | now, in parallel with F1 |
| F2 | Roster rename and the `work_kind` row key | Full suite, two reviewers | F1 and F3 |
| F4 | Path rules, path qualification, generated routes | Full suite | F2 |
| F5 | List price and value order, shortlist per funding step, funding order, replay fields, pre-registration Edit 7 | Full suite | F4 |
| F6 | Model names removed, `validate.py` check | Affected modules | F5 |
| F7 | Strict replay runner; canary harness in ranked mode | Affected modules | F6 |
| F8 | Historical replay runner | Affected modules | F5, in parallel with F6 and F7 |

The order follows the files the slices share: `runtime/routing.py`, `runtime/routing_evidence.py`, `runtime/routing_observations.py`, `scripts/routing.py`, `scripts/validate.py`, `runtime/routing_launch.py` and `tests/qualification/canary.py`. Slices that run in parallel own disjoint files: F1 does not touch `scripts/validate.py` or `runtime/routing.py`, and F8 touches only its own two files and one section of the validation document.

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
| `runtime/routing_funding.py` | F5 | The ranked decision moved out of `runtime/routing.py`, the shortlist per funding step, the rule that opens OpenRouter, the admission record builder |
| `runtime/model_names.py` | F6 | Display-name lookup and the model-name check `scripts/validate.py` calls |
| `tests/qualification/replay_strict.py` | F7 | Strict replay over exported admission records |
| `tests/qualification/replay_history.py` | F8 | Advisory historical replay over past session metadata |
| `runtime/routing_learning.py` | L1 | Analysis rows and scores |
| `runtime/due_jobs.py` | L2 | Due-job check, one detached runner, backoff |

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
- [ ] **Step 4: Run** the full suite and `scripts/validate.py`. Expected: OK.
- [ ] **Step 5: Commit** `feat(routing): prepare reports the profile fields that took floors`.

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

Shared core. One PR with the commits in task order; the full suite must pass on the last commit, and each commit message names the modules that pass at that commit. Two independent reviewers: one for `runtime/` and `scripts/`, one for contracts, documents and tests.

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
- [ ] **Step 4: Run** the same modules plus `test_workstreams 'test_openrouter*' test_evidence test_changes test_usage_codex_host`. Expected: OK.
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
- [ ] **Step 4: Run the full suite** and `scripts/validate.py`. Expected: OK, exit 0. Lower every ceiling whose file shrank.
- [ ] **Step 5: Commit** `docs(routing): one roster in policies, contracts and skills, with a vocabulary gate`.

---

## Slice F4: Path rules and generated routes

**Owned files:** `runtime/routing_routes.json`, `runtime/routing_paths.py`, `runtime/routing_observations.py`, `runtime/routing_evidence.py` (strategy `provider` and `endpoint`), `runtime/codex_rate_limits.py`, `runtime/routing_launch.py`, `runtime/opencode_launcher.py`, `scripts/openrouter_proposals.py`, `scripts/routing.py` (`CAPABILITIES`, `_collect`, the `qualify_path` and `unqualify_path` operations), `tests/test_routing_paths.py`, `tests/test_routing_observations.py`, `tests/test_routing_managed_projects.py`, `tests/test_routing_command.py`, `tests/test_routing_codex_rate_limits.py`, `tests/test_routing_launch.py`, `tests/test_qualification_demand.py`, `tests/qualification/demand.py`, `tests/qualification/live_modes.py`, `tests/openrouter_fixtures.py`, `docs/smart-routing-qualification.md`, `docs/smart-routing-capability-matrix.md`, the C.3 wiring design (decision 5 marked replaced).

### Task F4.1: Path rules and the path qualification record

**Interfaces:**
- Produces, in `runtime/routing_paths.py`:
  - `RULES: Path` (`runtime/routing_routes.json`), `QUALIFICATIONS_FILE = 'path-qualifications.json'`, `ROUTES_FILE = 'routes.json'`; both files live beside the routing settings.
  - `load_rules(path=RULES) -> dict`; raises `ValueError('route_manifest_invalid')`.
  - `qualifications(settings_path) -> dict[str, dict]`; `{}` when the file is absent; raises `ValueError('path_qualifications_invalid')`.
  - `qualify(settings_path, path_id, *, demand, evidence, account=None, now) -> dict` and `unqualify(settings_path, path_id) -> dict`, written atomically with mode 0600.
  - `pool_windows(rules, pool) -> set[str]`: the window ids the rules declare for a pool.

**Shipped file (fixed):** `{"version": 2, "paths": [...]}`. A path has:

| Field | Meaning |
|---|---|
| `path`, `harness`, `provider` | The id and the two values a table strategy must match. No two paths share a `(harness, provider)` pair, so a strategy generates at most one route. |
| `sign_in` | `subscription`, `api_key` or `gateway_key` |
| `billing_class`, `pool`, `account`, `binding_enforceable` | As in today's manifest route |
| `windows` | The window ids a launch is charged in |
| `window_unit` | Required on a subscription path (`utilization_thousandths`); absent on a gateway path, whose one window is `credit` in micro-dollars |
| `integration_route` | Required on a gateway path: `workers` or `proposals` |
| `host`, `advisory`, `status` | Optional |

A path has no `model`, `effort`, `endpoint` or `demand`; `load_rules` refuses a path that has one, a duplicate `(harness, provider)` pair, a subscription path without `window_unit` and a gateway path without `integration_route`. The shipped paths:

| `path` | `harness` | `provider` | `billing_class` | `pool` | `windows` |
|---|---|---|---|---|---|
| `opencode-claude-subscription` | `claude-agent-sdk via opencode` | `claude-agent` | `included_subscription` | `claude-subscription` | `5h`, `7d` |
| `opencode-chatgpt-subscription` | `opencode provider openai` | `openai` | `included_subscription` | `chatgpt-subscription` | `7d` |
| `codex-chatgpt-subscription` | `codex-native` | `openai` | `included_subscription` | `chatgpt-subscription` | `7d` |
| `openrouter-workers` | the OpenCode shim's harness name for `openrouter` | `openrouter` | `gateway` | `openrouter` | `credit` |
| `openrouter-proposals` | `openrouter-proposal` | `openrouter` | `gateway` | `openrouter` | `credit` |
| `test-only-subscription` | `test` | `test` | `included_subscription` | `test-only-subscription` | `5h` |

The first three come from the draft manifest in #227. The Codex path keeps `host: codex_native` and `advisory: true`. The two gateway paths carry `integration_route` `workers` and `proposals`. The test path has `status: test_only`, and `qualify` refuses it outside test inputs. The builder checks each `harness` string against what the launchers compare (`routing_launch.HOSTS`, `harnessFor` in the OpenCode plugin) before fixing it.

A qualification is `{path_id: {demand: {window: int}, account, evidence, qualified_at}}`. `qualify` requires a path from the rules, demand keys equal to the path's `windows`, and a non-empty `evidence` of at most 128 characters (the live result's id). A subscription demand is an integer from 1 to 1000, in thousandths of the window. A gateway demand is `{credit: N}`, micro-dollars per launch, from 1 to 1,000,000.

**The gateway demand (coordinator's call 19).** `N` is the largest launch cost the live gateway check observed, taken from the provider receipts, rounded up. Every route on the path reserves it until L1 records cost per model. Consequence: a model that costs more than the measured one is under-reserved at admission. The real cost is still settled from the receipt, and the organization limit and the key's own limit still stop the spend. Stricter alternative, not chosen: scale the reservation by each strategy's list price relative to the measured model's; it needs the measured model's price in the qualification record and one more rule in the replay.

- [ ] **Step 1: Write the failing tests** in `tests/test_routing_paths.py`: `test_shipped_path_rules_are_valid_and_name_no_model`, `test_path_with_a_model_or_demand_is_refused`, `test_two_paths_with_one_harness_and_provider_are_refused`, `test_subscription_path_needs_a_window_unit_and_gateway_path_an_integration_route`, `test_qualify_checks_the_path_and_the_demand_windows`, `test_gateway_demand_is_micro_dollars_within_its_bound`, `test_qualification_file_is_private_and_atomic`, `test_unreadable_qualifications_fail_closed`.
- [ ] **Step 2: Run** `scripts/test_sharded.py test_routing_paths`. Expected: FAIL, no module.
- [ ] **Step 3: Implement** and replace the shipped file.
- [ ] **Step 4: Run** again. Expected: OK.
- [ ] **Step 5: Commit** `feat(routing): model-free path rules and the path qualification record`.

### Task F4.2: Route generation

**Interfaces:**
- Consumes: a compiled table whose strategies carry `provider` and, when it differs from the harness, `endpoint`. `routing_evidence._catalog` accepts an optional `provider` (text, at most 64 characters) per catalog entry; `compile_table` copies it onto each strategy and stops dropping `endpoint` (`runtime/routing_evidence.py:183`). `strategy_id` is computed as before, so ids do not change.
- Produces, in `runtime/routing_paths.py`:
  - `generate(rules, qualified, table, choices) -> dict` with `routes` and `skipped`, pure. For each qualified path and each distinct table strategy with the same `harness` and `provider`, one route with the fields the collector and `routing._valid_route` require:
    - `route` (`<path>:<strategy_id>`), `status: 'qualified'`, `path`, `strategy_id`;
    - `provider`, `model`, `harness`, and `effort`, where a strategy without an effort gets the text `'none'`, as today's gateway routes have;
    - `billing_class`, `pool`, `account`, `binding_enforceable`, and `demand` (the path's qualified demand);
    - `window_unit` on a subscription path, `integration_route` on a gateway path;
    - `endpoint` when the strategy has one.
  - For each explicit worker choice whose harness and provider match a qualified path and that no strategy route covers, one route with the same fields and **no `strategy_id` key**, named `<path>:choice:<first 8 hex of the choice digest>`.
  - Two strategies with the same route identity and different endpoints generate no route; both are listed in `skipped` with the reason `endpoint_ambiguous`. Today `validate_routes` would refuse the whole list.
  - `routes(settings_path, *, rules_path=RULES) -> dict` with `routes`, `skipped` and `routes_version` (16 hex characters of the routes' digest). It reads the table from `strategy-table.json` beside the settings and the resolved `worker_choices` from the settings itself. It regenerates and rewrites `routes.json` only when the stored `inputs` differ: the size and modification time of the table, the settings, the rules file and the qualification file. A missing table or settings file counts as empty.
  - `routing_observations.LocalSource.manifest()` returns `routes(self.settings_path)['routes']`, and `[]` without a settings path. `load_manifest`, `MANIFEST` and the top-level `window_unit` are gone.
  - `routing_observations.collect(..., strategy_ids=None)`: when given, it considers only the generated routes whose `strategy_id` is in the list and the routes without one. `scripts/routing.py` passes the ranking's ids. Each strategy has at most one route, and from F5.2 the collector keeps at most 20 strategy routes (10 per funding step) plus one choice route per routed specialist, so `routing.MAX_ROUTES` (64) is not reached.
  - The other readers of the manifest read the whole generated list by route id and need only the settings path they already have: `routing_launch.admitted_check` (:207), `opencode_launcher._account_of` (:621) and `openrouter_proposals._endpoint` (:534).
  - `codex_rate_limits.declared_windows(rules)` returns `routing_paths.pool_windows(rules, POOL)`, and `read_windows` loads the rules in place of the manifest (:71, :324).

**Cost:** four `stat` calls per `prepare`, and one regeneration (four small file reads, one write) when an input changed. Lighter alternative: regenerate only inside `set`, `routing_table.py refresh` and `qualify_path`; rejected because a table replaced by hand would leave stale routes.

Until L1 records window use per launch, every route on a path uses the path's measured demand.

- [ ] **Step 1: Write the failing tests:**
  - In `tests/test_routing_paths.py`: `test_generation_combines_qualified_paths_with_table_strategies`, `test_unqualified_path_generates_nothing`, `test_strategy_without_a_provider_generates_nothing`, `test_strategy_without_an_effort_gets_the_text_none`, `test_gateway_route_carries_endpoint_and_integration_route`, `test_subscription_route_carries_the_window_unit`, `test_explicit_choice_gets_a_route_without_a_strategy_id`, `test_ambiguous_endpoints_skip_only_those_strategies`, `test_demand_comes_from_the_path_qualification`, `test_routes_are_rewritten_only_when_an_input_changes` (count writes), `test_every_generated_route_passes_the_collectors_checks` (each route gives `_manifest_reason(route) is None`).
  - In `tests/test_routing_observations.py`: `test_not_entitled_mark_still_excludes_a_generated_route`, `test_local_source_without_a_settings_path_has_no_route`, `test_collect_reads_only_the_ranked_strategies_and_choice_routes` (80 generated routes, 5 ranked ids: 5 routes considered, none listed as excluded for the other 75).
  - In `tests/test_routing_codex_rate_limits.py`: `test_declared_windows_come_from_the_path_rules`.
- [ ] **Step 2: Run** `scripts/test_sharded.py test_routing_paths test_routing_observations test_routing_evidence test_routing_codex_rate_limits`. Expected: FAIL.
- [ ] **Step 3: Implement.** Move the fixtures in `tests/openrouter_fixtures.py` and the route helpers in `tests/test_routing_observations.py` to generated routes.
- [ ] **Step 4: Run** the same modules plus `test_routing_launch 'test_openrouter*' test_routing_dispatch`. Expected: OK.
- [ ] **Step 5: Commit** `feat(routing): generate routes from qualified paths and the ranking table`.

### Task F4.3: Activation gate, the operator operation and the runbook

**Interfaces:**
- Produces:
  - `routing_paths.gate(settings_path) -> list[str]`: `[]`, or one of `['route_manifest_invalid']`, `['no_qualified_path']`, `['no_generated_route']`. `routing_observations.manifest_check` and the reason `route_manifest_unqualified` are gone.
  - `scripts/routing.py`: the `route_manifest` capability calls `gate`. Two one-shot operator operations, refused under `--serve` like the other operator operations: `qualify_path` with `{path, demand, evidence, account?}` and `unqualify_path` with `{path}`. Their bodies live in `runtime/routing_paths.py`; the script only dispatches.
  - `tests/qualification/demand.py` (`measure-demand`) prints, for each subscription path, the `qualify_path` request with the measured demand and the result id. The `gateway` mode in `tests/qualification/live_modes.py` prints the same request for the gateway path it checked, with the largest receipt cost as `credit`. Neither names a manifest route.

- [ ] **Step 1: Write the failing tests:** `test_activation_reports_no_qualified_path`, `test_activation_reports_no_generated_route`, `test_activation_reports_an_invalid_rules_file`, `test_activation_passes_with_one_generated_route` (in `tests/test_routing_managed_projects.py`); `test_qualify_path_is_one_shot_and_validates_its_request` (in `tests/test_routing_command.py`); `test_measure_demand_prints_one_qualify_request_per_path` (in `tests/test_qualification_demand.py`).
- [ ] **Step 2: Run** those three modules. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Update the runbook** (`docs/smart-routing-qualification.md:7-145`): the live session qualifies paths; after `measure-demand` and the gateway check, the operator runs `qualify_path` once per path; a new model on a qualified path needs no session. Mark decision 5's route manifest in the C.3 wiring design as replaced. Fix the stale sentence at `docs/smart-routing-qualification.md:613` (`reconcile_subscription` exists since #192) and add a dated note under the table row at line 530 without changing the recorded result.
- [ ] **Step 5: Run the full suite** and `scripts/validate.py`. Expected: OK.
- [ ] **Step 6: Commit** `feat(routing): activation and the runbook qualify paths`.

When F4 merges, the coordinator marks #227 `[SUPERSEDED]` and closes it.

---

## Slice F5: Value order, funding order and replay fields

Shared core. **Owned files:** `runtime/routing_evidence.py`, `runtime/routing.py`, `runtime/routing_funding.py`, `runtime/routing_observations.py` (`leave` and the per-step cut), `runtime/usage_budget.py` (constants only), `scripts/routing.py`, `scripts/validate.py` (ceilings only), `tests/test_routing_evidence.py`, `tests/test_routing_ranker.py`, `tests/test_routing_funding_order.py`, `tests/test_routing_funding.py`, `tests/test_routing_funding_property.py`, `tests/fixture_routing_funding.py`, `tests/test_routing_admission.py`, `tests/test_routing_observations.py`, `tests/qualification/replay_export.py`, `tests/test_qualification_replay_export.py`, `docs/smart-routing-validation.md`, `clients/guidance-publication.md`.

### Task F5.1: Pre-registration Edit 7

**Files:**
- Modify: `docs/smart-routing-validation.md` (a new section after Edit 6)

This task merges first within F5, before any code of F5 runs against real records. Its content needs the user's approval, because it changes pre-registered text. Edit 7 says:

1. **Vocabulary.** "Role" means specialist. The strict sample covers at least three specialists and two complexity bands.
2. **What the strict replay recomputes.** From each record's stored detail alone:
   - the class, from `shares`, `bucket`, `epoch`, `feasible` and the earlier records. The earlier records are the ones the router itself counts: same ledger, same settings revision, at most 14 days older, and not funded by OpenRouter;
   - the route, from the stored candidates of that class in the stored order;
   - whether OpenRouter was open, from the stored pool states and the stored fact of whether a direct route existed.

   The four pass or fail rows of the allocator replay are unchanged.
3. **Records from before the redesign.** The exported pilot ledger holds 21 admission records, all in `worker_choice` mode with the old role names. Edit 6 says 20; the difference is explained under Results before a pass is recorded. Each of the 21 still holds its constraints (checked on 10 November 2026 without reading any other field), so the sentence in Edit 6 that the pilot's snapshot detail was gone is corrected by a dated note. For these records:
   - the class must be the funding class of the record's billing class;
   - every stored constraint must have a cost no greater than its headroom, and the record's reservation in that window must fit the headroom;
   - the two ledger rows (no funding violation, no resurrected capacity) apply as for any record.

   A legacy record without constraints fails and is counted. Their roles are read as history.
4. **Historical replay labels.** Rule 1's outcomes become specialists: `reviewer` gives `code-reviewer`, `researcher` gives `explorer-agent`, `implementer` and `repair` give `software-engineer`. Rule 2 becomes the work kind of that specialist. An unknown band still takes the router's floors.
5. **Value order.** The price of a candidate is `(3 × input + output) / 4` of its list prices per million tokens. A candidate without a price comes last. Ties go to the lower effort, then to the guidance order. Each funding step takes the first 10 candidates of that order that have a route for that step on this machine.
6. **When OpenRouter opens.** Only when no shortlisted candidate has a direct route here, or when every subscription pool with such a route is full. A pool is full when one of its windows has a stored reading at or above `H` that is either fresh or belongs to a window that has not reset yet, or when every reading is fresh and the launch does not fit. An unread or otherwise stale window keeps OpenRouter closed.
7. **Benchmark tolerance:** 2%, applied from slice L2.
8. **Promotion test,** registered before slice L3 reads the later half: a kind of task counts when at least two candidates have at least 5 results each in the later half; at least 3 kinds of task must count; scores are promoted only if the learned pick is at least as good as the table pick in at least 80% of them.
9. **Canary.** It runs in ranked mode on generated routes. Its bounds are unchanged.

- [ ] **Step 1: Write the section** with the nine points, dated, stating that no replay, canary or label existed when it was registered. Add the dated correction beside Edit 6's sentence about the pilot's snapshot detail (`docs/smart-routing-validation.md:233`), without changing Edit 6's text.
- [ ] **Step 2: Run** `scripts/validate.py` and `scripts/test_sharded.py test_prepared_instructions`. Expected: OK.
- [ ] **Step 3: Commit** `docs(routing): pre-register Edit 7 for the redesigned replay and ranking`.

### Task F5.2: Move the ranked decision, then list price and value order

`runtime/routing.py` has no spare line after F3 lowers its ceiling, so the first commit of this task only moves code.

**Interfaces:**
- Produces:
  - `runtime/routing_funding.py` holds the ranked branch of `routing._decide` (:565-588) and the record builder (:596-611), moved without a change in behaviour. `routing._decide` calls them. `routing.py`'s ceiling is lowered to its new line count.
  - A catalog entry accepts an optional `list_price: {"input": number, "output": number}`, in dollars per million tokens, both finite and not negative.
  - Each compiled strategy carries `list_price_micros: int | None`, `round((3 * input + output) / 4 * 1_000_000)`.
  - `compile_table` orders each row by: priced before unpriced, `list_price_micros` ascending, the effort's index in the model's `supported_efforts` ascending (no effort counts as 0), then the guidance order. The catalog contract in `clients/guidance-publication.md` states that `supported_efforts` is listed from the lowest to the highest; the compiler cannot check it.
  - `routing.rank` returns the whole filtered order, as today. The cut is per funding step:
    - `routing_funding.SHORTLIST_MAX = 10`
    - `routing_funding.step_shortlists(strategy_ids, routes, excluded) -> dict` with `direct` and `gateway`: for each step, the first 10 ids of the order that have a route of that step. A route counts when the collector kept it, or left it out for any reason other than `not_entitled`. Pure.
  - The collector uses the same function to keep only the routes of those ids (at most 20 strategy routes), and its `leave` adds `strategy_id`, `pool` and `billing_class` to each `excluded` entry. What a client sees of `excluded` does not change.

**Why the cut is per step (coordinator's call 18).** Design section 5.5 cuts the order to 10 before it looks at routes. With the cheapest first, those 10 can all be models this machine reaches only through OpenRouter, which would open OpenRouter while a subscription model in place 11 could serve. The per-step cut keeps the design's aim: use a subscription while it has room.

- [ ] **Step 1: Move the code** and run `scripts/test_sharded.py 'test_routing_*'`. Expected: OK with no test changed. Commit `refactor(routing): move the ranked decision and the record builder to routing_funding`.
- [ ] **Step 2: Write the failing tests:**
  - In `tests/test_routing_evidence.py`: `test_price_is_three_parts_input_one_part_output` (input 1.0 and output 5.0 give `2_000_000`), `test_rows_are_ordered_by_price_then_effort_then_guidance_order`, `test_unpriced_candidates_come_last`, `test_invalid_list_price_is_refused`.
  - In `tests/test_routing_funding_order.py`: `test_each_step_takes_its_first_ten_with_a_route` (12 ids, the 3 cheapest gateway-only: `direct` holds ids 4 to 12 and `gateway` the 3), `test_a_not_entitled_route_takes_no_place`, `test_an_unobserved_route_keeps_its_place`, `test_shortlists_are_never_padded`.
  - In `tests/test_routing_observations.py`: `test_excluded_entries_name_strategy_pool_and_billing_class`, `test_collector_keeps_only_the_step_shortlists`.
- [ ] **Step 3: Run** the three modules. Expected: FAIL.
- [ ] **Step 4: Implement.**
- [ ] **Step 5: Run** them and `test_routing_table test_routing_ranker test_routing_command`. Expected: OK.
- [ ] **Step 6: Commit** `feat(routing): order each row by list price and cut the shortlist per funding step`.

### Task F5.3: The funding order

**Files:**
- Create: `tests/test_routing_funding_order.py` (started in F5.2)
- Modify: `runtime/routing_funding.py`, `runtime/routing.py`, `tests/test_routing_funding.py`, `tests/test_routing_funding_property.py`, `tests/fixture_routing_funding.py`

**Interfaces:**
- Produces, in `runtime/routing_funding.py`:
  - `GATEWAY_REASONS = ('gateway_capability_gap', 'gateway_windows_full', 'gateway_window_unknown', 'gateway_direct_available')`
  - `pool_state(windows, demanded, held, H, now, fits) -> 'full' | 'room' | 'unknown'`, pure.
  - `gateway_decision(pool_states, direct_route_exists) -> tuple[bool, str]`, pure. Admission and the strict replay both call it.
  - `gateway_open(shortlists, observations, rejected, held, settings, now) -> dict` with `open`, `reason`, `pools` (`[{pool, state}]`, sorted by pool) and `direct_route_exists`. It builds the inputs of `gateway_decision` from the collector's routes, pools and `excluded`, and from `rejected`, the reason `_candidate` gave for each route it refused.
  - `choose(direct, gateway, shares, rows, bucket, epoch, feasible, gateway_state, now, horizon) -> tuple[str | None, str | None]`: the chosen route and its class, pure. The direct candidates go through `allocator.choose_class` and `allocator.choose_route` exactly as today. If no direct route is chosen and `gateway_state['open']`, `allocator.choose_route` picks among the gateway candidates in their order.
- `routing._decide`, ranked mode, calls `choose`. With no route chosen the assignment is pending with its dominant reason and the gateway reason.

**The rules.**

*Does a direct route exist?* Yes when a candidate of the direct shortlist has a subscription route on this machine: one the collector kept, or one it left out for a reason other than `not_entitled`. The shipped rules have no direct API path. When API Usage adds one after the release, its routes count only while API Usage is active, and its slice defines the pool state.

*The state of each subscription pool that has such a route,* over the windows its routes demand:

- `full`: one window has a stored reading where used, protected and held together are at or above `settings['H']` of the limit, and that reading is fresh, or the window is not rolling and its `reset_at` is in the future. Also `full`: every demanded window is fresh and `_candidate` refused every shortlisted route on the pool with `insufficient_headroom`.
- `room`: not full, and every demanded window is fresh.
- `unknown`: anything else, including a pool the collector reported no window for.

*The decision:*

| Condition, in this order | Result | Reason |
|---|---|---|
| No direct route exists | open | `gateway_capability_gap` |
| A direct route exists and there is no pool state | closed | `gateway_window_unknown` |
| A pool has `room` | closed | `gateway_direct_available` |
| A pool is `unknown` | closed | `gateway_window_unknown` |
| Every pool is `full` | open | `gateway_windows_full` |

The reading is the stored one, not the pace-projected value.

**Two calls that differ from the design's word "fresh" (calls 16 and 17).** A reading is fresh for 900 seconds, and only a settled launch or the Codex reader renews it. Without the "has not reset yet" rule, 15 minutes after the last launch on a full pool the reading is stale, OpenRouter closes, and every task waits until the user seeds the window. A full window cannot empty before its reset, so the old reading still proves it. Without the "does not fit" rule, a pool just below `H` whose room is smaller than one launch would block the task and keep OpenRouter closed until the reset.

**Accounting.** The accounting class of an OpenRouter admission stays `api`, because it is a dollar route under the organization and ledger gates; its `billing_class` is `gateway`. `routing.decision_rows` leaves out records whose `billing_class` is `gateway`, so they never move the share between subscription and API Usage. An explicit choice with `funding: 'api'` on a gateway route works as today.

- [ ] **Step 1: Write the failing tests** in `tests/test_routing_funding_order.py`:
  - `test_subscription_with_room_wins_over_gateway`
  - `test_gateway_opens_on_a_capability_gap`
  - `test_a_not_entitled_direct_route_is_a_capability_gap`
  - `test_unobserved_pool_is_not_a_capability_gap` (the only direct route was left out as `window_unobserved`: closed, `gateway_window_unknown`)
  - `test_gateway_opens_when_every_pool_is_at_or_above_H`
  - `test_one_pool_with_room_keeps_gateway_closed`
  - `test_stale_or_unread_window_never_opens_gateway` (a stale reading below `H`, and a pool with no reading)
  - `test_a_full_window_stays_full_until_its_reset` (a stale reading at `H`, not rolling, reset in the future: open; the same after the reset time, or rolling: closed)
  - `test_a_launch_that_does_not_fit_fills_the_pool` (fresh reading below `H`, room smaller than the demand: open)
  - `test_fast_burning_half_empty_window_does_not_open_gateway`
  - `test_a_full_weekly_window_fills_the_pool_even_when_the_short_window_is_empty`
  - `test_gateway_needs_the_integration_and_its_ledger_gates` (the integration off, or no funded organization: pending)
  - `test_gateway_admissions_do_not_move_the_api_share`
  - `test_explicit_gateway_choice_still_admits`
  - `test_no_route_at_all_waits_with_a_reason`
  - `test_gateway_decision_table` (the five rows above, on `gateway_decision` alone)
- [ ] **Step 2: Run** `scripts/test_sharded.py test_routing_funding_order`. Expected: FAIL.
- [ ] **Step 3: Implement,** keeping `runtime/routing.py` at or below its ceiling. Update the mixed ranked shortlists in `tests/test_routing_funding_property.py:19-42` and `tests/fixture_routing_funding.py` to the new order: a gateway candidate is chosen only when the gateway is open.
- [ ] **Step 4: Run** `scripts/test_sharded.py 'test_routing_funding*' test_routing_admission test_routing_allocator test_routing_replay`. Expected: OK.
- [ ] **Step 5: Commit** `feat(routing): OpenRouter is the last step of the funding order`.

### Task F5.4: Replay fields in the admission record and the export

**Interfaces:**
- Produces, in the admission record (built in `runtime/routing_funding.py` since F5.2):
  - `ranking`: `table_version`, `profile_digest`, plus `shortlist` (the strategy ids of the step that chose the route, at most 10), `strategy_id` (the chosen one, or `None`), `score_version` (`None` until L3), `routes_version`.
  - `risk_band`, `floor_filled` (whether `missing_fields` was not empty), and `strategy: {provider, model, effort, harness}`. `scripts/routing.py` passes `risk_band` and `floor_filled` in the admitted assignment; `work_kind` is there since F2.2.
  - In `pressure`, which is the snapshot detail removed after 24 hours:
    - `epoch`, `feasible` (the direct classes that had a candidate), `minimum_horizon` and `H`;
    - `candidates`: `[{route, cls, step, pool, constraints}]` in shortlist order, where `step` is `direct` or `gateway`. The direct candidates are always stored; the gateway candidates only when the direct step chose nothing;
    - `gateway`: `{open, reason, pools: [{pool, state}], direct_route_exists}`.
  - `shares` of a record funded by OpenRouter: the shares the direct step computed, or `{api: 0.0, subscription: 0.0}` when no direct class had a candidate.
  - `usage_budget.MAX_ADMISSION_BYTES` becomes `8192 + MAX_BINDING_BYTES`, and the test below proves the largest record fits. **Cost:** a record grows by about 3 KB for the 24 hours its snapshot detail is kept; the ledger holds one per open or recent attempt. Lighter alternative: store one constraint list per pool; rejected because two paths on one pool have different costs, and the replay would not be exact.
  - `tests/qualification/replay_export.py` exports the records as stored, with a manifest field `format: 2`; records without `ranking.shortlist` are exported unchanged.
- `ADMISSION_FIELDS` in `scripts/routing.py`, what a client may see, gains nothing.

- [ ] **Step 1: Write the failing tests:**
  - In `tests/test_routing_admission.py`: `test_admission_record_carries_the_replay_fields`; `test_class_and_route_recompute_from_the_record_alone` (call `routing_funding.choose` and `gateway_decision` with nothing but the record's fields, the earlier records and the record's `at`; the route and class equal the record's, for a subscription admission, an OpenRouter admission after full pools and one after a capability gap); `test_two_routes_on_one_pool_keep_their_own_costs`; `test_largest_record_fits_the_size_limit` (10 direct and 10 gateway candidates, 2 windows each, 3 pools); `test_snapshot_detail_is_removed_after_a_day_and_the_rest_stays`.
  - In `tests/test_qualification_replay_export.py`: `test_export_keeps_the_replay_fields_and_old_records`.
- [ ] **Step 2: Run** both modules. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Update** `clients/guidance-publication.md`: a guidance row is `{work_kind, complexity_band, risk_band, strategies}`; a catalog entry carries `provider`, `list_price` and `supported_efforts` from the lowest to the highest.
- [ ] **Step 5: Run the full suite** and `scripts/validate.py`. Expected: OK.
- [ ] **Step 6: Commit** `feat(routing): admission records store what the strict replay recomputes`.

---

## Slice F6: No model names

**Owned files:** `runtime/usage_canary.py`, `runtime/adapters/codex.py:596-597`, `runtime/routing_evidence.py` (`display_names`), `runtime/model_names.py`, `clients/opencode.md:22,350`, `skills/manage-usage-routing/references/first-canary.md:30`, `skills/openrouter-workers/references/procedure.md`, `scripts/validate.py`, `tests/test_model_names.py`, `tests/test_usage_canary*.py`, `tests/qualification/safety.py`, `tests/qualification/drill.py`, `tests/qualification/launcher_probes.py`, `tests/qualification/canary.py`, `tests/qualification/live_modes.py`, `docs/smart-routing-qualification.md`.

### Task F6.1: Remove the names

**Interfaces:**
- Produces:
  - `usage_canary`: the `_MODEL` constant is gone. The canary uses the cheapest priced strategy for its provider in the compiled table, and refuses with `canary_model_unavailable` when the table has none. The reasoning constant stays. Design section 5.14 says "the lightest model the catalog lists"; no catalog is on the machine before L2, so the table stands in for it (coordinator's call 20), and L2 may switch to the catalog.
  - A catalog entry accepts an optional `display_name`; the compiled table carries `display_names: {model: name}`. The Codex adapter's one hard-coded correction is replaced by `model_names.display_name(model, settings_path)`, which reads that map from the table file, at no added lines in `runtime/adapters/codex.py`. **Cost:** one read of the table file when a display name is resolved. Lighter alternative: drop the correction and show the vendor's spelling.
  - The live harness takes the route as `--route PROVIDER/MODEL#VARIANT`; `tests/qualification/safety.py` has no `MODEL` constant and checks every launch against the route named in the approval.

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

**Owned files:** `tests/qualification/replay_strict.py`, `tests/test_qualification_replay_strict.py`, `tests/qualification/canary.py`, `tests/test_qualification_canary.py`, `docs/smart-routing-qualification.md` (the replay step).

### Task F7.1: The strict replay runner

**Interfaces:**
- Consumes: the export directory of `replay_export.export`, the record fields of Task F5.4, and the two pure functions admission itself calls: `routing_funding.choose` and `routing_funding.gateway_decision`. It reimplements neither.
- Produces: `replay_strict.run(directory) -> dict` with `records`, `legacy_records`, `class_reproduced`, `route_reproduced`, `funding_violations`, `resurrected_capacity`, `stray_records`, `specialists`, `bands`, `passed`; and `python3 -B -m tests.qualification.replay_strict --dir <directory>`, which prints the report without ids, titles or timestamps and exits 1 unless `passed`.
- It opens nothing but the export directory, calls no model and uses no network. Per ledger identity it walks the records in `at` order and applies Edit 7's points 2, 3 and 6. The earlier records it gives to `choose` are filtered as `routing.decision_rows` filters them: same settings revision, at most 14 days older, not funded by OpenRouter.

- [ ] **Step 1: Write the failing tests** with synthetic exports and invented names: `test_reproduced_export_passes`, `test_a_changed_class_fails`, `test_a_reordered_shortlist_fails`, `test_a_duplicate_attempt_is_a_stray_record`, `test_reserved_beyond_the_stored_headroom_is_a_funding_violation`, `test_a_later_record_with_more_headroom_and_no_reset_is_resurrected_capacity`, `test_a_gateway_record_is_checked_against_its_pool_states` (stored states that give "closed" with a gateway route: fails), `test_earlier_records_follow_the_routers_own_filters`, `test_legacy_worker_choice_records_follow_the_legacy_rule` (a copy of the pilot's record shape: one constraint, no `snapshot_id`), `test_a_record_without_snapshot_detail_fails_and_is_counted`, `test_the_report_holds_no_identifier`.
- [ ] **Step 2: Run** `scripts/test_sharded.py test_qualification_replay_strict`. Expected: FAIL, no module.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** again. Expected: OK.
- [ ] **Step 5: Commit** `test(qualification): strict replay over exported admission records`.

### Task F7.2: The canary harness runs ranked

**Interfaces:**
- Produces: in `canary` mode on the production ledger the harness sends no explicit choice, so admissions are ranked on generated routes; it rotates `software-engineer`, `code-reviewer` and `explorer-agent` over the `low` and `medium` bands; it refuses to start when `prepare` reports `table_outdated` or `table_empty`, or the activation gate reports `no_generated_route` or `no_qualified_path`. The pilot and isolation modes keep their explicit `--route`.

- [ ] **Step 1: Write the failing tests** against the doubles: `test_canary_admissions_are_ranked`, `test_canary_refuses_to_start_without_generated_routes`.
- [ ] **Step 2: Run** `scripts/test_sharded.py test_qualification_canary`. Expected: FAIL.
- [ ] **Step 3: Implement** and add the strict replay command to the runbook before the canary step.
- [ ] **Step 4: Run** `scripts/test_sharded.py 'test_qualification*'`. Expected: OK.
- [ ] **Step 5: Commit** `test(qualification): the canary runs in ranked mode`.

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
5. In the live session each subscription path is qualified with `qualify_path` from the `measure-demand` result and the gateway path from the gateway check's largest receipt, and the historical replay runs once afterwards.

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
7. OpenRouter is its own step in the funding order, but its accounting class stays `api`; the record's `billing_class` says `gateway`.
8. A pool counts as full when any one of its windows is at or above `H`; calls 16 and 17 say which readings count.
9. An explicit worker choice gets a generated route on a qualified path even when the table does not list its model. That route has no `strategy_id`.
10. Old `worker_choices` keys load, match nothing and are reported in `settings_warnings`; today an unknown key fails the whole file.
11. OpenRouter proposals use `explorer-agent`; an OpenRouter worker defaults to `software-engineer`.
12. The `terms` input of the table compiler is removed with the control roles, since nothing else reads it.
13. Edit 7's nine points, among them: 2% tolerance; promotion at 5 results, 3 kinds of task and 80%; the legacy rule for the pilot's 21 records; the dated correction of Edit 6's sentence that the pilot's snapshot detail was gone.
14. The model-name check takes its names from the compiled table until the downloaded catalog exists.

Added after the independent review of this plan. Calls 15 to 18 and 20 depart from the design's wording, so each needs the user's explicit yes:

15. **`check` stays a reserved value of `role`** for a check run, which uses no model and is not a specialist. Design section 5.2 says the role value goes. Removing it means a new stored shape and about 25 rewritten sites in three files that have no spare line (`runtime/delegation.py`, `runtime/evidence.py`, `runtime/changes.py`), for no change a user can see. Alternative: do that rewrite as its own slice after the canary.
16. **A full window stays full until its reset.** A stale reading at or above `H` still counts when the window is not rolling and has not reset. The design says "fresh". Without this, 15 minutes after the last launch on a full pool every task waits until the user seeds the window.
17. **A launch that does not fit fills the pool.** With fresh readings below `H`, a pool whose room is smaller than the launch counts as full. Without this, such a task waits until the reset with OpenRouter closed.
18. **The shortlist is cut per funding step,** not once. Each step takes the first 10 candidates that have a route for it here (Task F5.2). Design section 5.5 cuts first, which can open OpenRouter while a subscription model in place 11 could serve.
19. **The gateway demand is one value per path,** the largest launch cost the live gateway check observed, in micro-dollars (Task F4.1). A dearer model is under-reserved at admission until L1; the receipt, the organization limit and the key's own limit still bound the spend. Stricter alternative: scale by list price.
20. **The usage canary takes the cheapest priced strategy of the table,** because no catalog is on the machine before L2. Design section 5.14 says the lightest model the catalog lists.
21. `coordinator` becomes `orchestrator` through one reader, `task_profile.orchestrates`, which also accepts the stored value. The operation kinds `coordinator-work` and `operation-coordinator`, the provenance value `coordinator` and the accounting stages keep their names; the vocabulary gate covers six old names and leaves `coordinator` and `check` out.
22. The ledger stage follows the bucket's work kind: `review` gives the stage `review`, every other work kind `worker`. The template enforces that a specialist reviews exactly when its work kind is `review`.
23. OpenRouter has two paths, workers and proposals, because the two harnesses differ; no two paths may share a harness and provider.
24. Generation skips two strategies that share a route identity and differ in endpoint, with a reason, where today the whole manifest would be refused.
25. Design section 5.12 (advice in a project that is not managed) has no task here; see the self-review.

## Self-review

- **Spec coverage.** 5.1 and 5.2: F1.1, F2. 5.3: F1. 5.4: F4; the starting demand of a model with no launch in L1. 5.5: F2.2 (row key), F5.2 (price, order, shortlist); benchmark qualification and the default-effort rule in L2. 5.6: F5.3. 5.7 and 5.8: L2, including the changed-page report and the guidance-publication rule. 5.9: L1. 5.10: F5.1, F5.4, F7, L3. 5.11: L4. 5.12: not covered, see below. 5.13: F3. 5.14: F6. Section 8: the table above.
- **Not covered here:**
  - Design section 5.12, advice in a project that is not managed. Today `prepare` in such a project requires observations from its caller (`scripts/routing.py:425`) and returns strategy ids, not advice a person can read. The OpenCode workflow client design (#243) owns the session-start advice; it will consume the per-step shortlist of F5.2 and the generated routes of F4, and its plan adds the task.
  - Session titles and the vocabulary unification, which have their own designs.
- **Independent review.** A first review of this plan returned six blocking findings: the OpenRouter path could not become a valid route; the record was not enough for an exact replay; the legacy rule relied on data the validation document says is gone (it is not: the export was checked); removing `check` and `coordinator` was under-scoped; F4 broke manifest readers it did not own; and an unread pool could look like a capability gap. Each is fixed in the task it concerns, and the calls it forced are 15 to 25.
- **Type consistency.** `task_profile.specialist` is roster-only and `stored` also reads old names, in every task. Route fields in F4.2 are the ones `_manifest_reason`, `_valid_route`, `_candidate` and `_choice_matches` read, and a test runs every generated route through the collector's check. The record fields of F5.4 are the arguments of `routing_funding.choose` and `gateway_decision`, which F7.1 calls.
