# Smart Routing C.3 — caller wiring and selector retirement design

- **Version:** 1.8 draft (phase W code complete, pre-live slices merged; see [revision history](#8-revision-history))
- **Date:** 2026-11-10
- **Status:** Phase W's code is complete: C3-0b (#212), C3-1 (#213) with C3-1b (#220), C3-5 (#214), C3-0a (#215), C3-4a (#216), C3-2 (#217), C3-3 (#218), C3-6 (#221) and C3-4b (#225), plus the table rollback (#211) and the review follow-ups (#222, #224). The pre-live slices are merged too: `seed_window` (#228), the pre-decision-4 fixes (#229), the Codex rate-limit reader (#231), the live-session harness modes (#234) and steps on every agent (#239). The production route manifest (#227) stays open until live demand is measured. All of it is package-tested only: no model call, OpenCode server or live run was used. C3-7 is not built. Where a merged slice deviated from earlier text by a coordinator decision, the section that defines the rule now states the merged behaviour and names the PR. Since v1.4, the risky steps are validated by running throwaway prototypes in a scratch worktree ([Appendix B.0](#b0-prototype-runs-validated-by-running)).
- **Line numbers:** all at `origin/main` `bd5cde6` ([Appendix A](#appendix-a--inventory-at-the-pinned-base)), the base the slices were planned on. Merged code added since is cited as `file:function`. Open user decisions are in [section 7](#7-open-decisions).
- **Plan task:** [C.3 Replace callers and retire selectors](../plans/2026-11-08-smart-routing.md#task-c3-replace-callers-and-retire-selectors). The plan owns task status; this document owns how C.3 is built.
- **Parent design:** [Smart Routing spec v1.5](2026-11-08-usage-routing-opencode-subscriptions-design.md) §3, §4, §8, §10.4 and Appendix A.

This document covers:

- which code paths and instructions choose workers today, and what each must call instead;
- the contract every launcher follows;
- how the old selectors are retired;
- how C.3 splits into pull requests that separate workers can build at the same time.

It links to existing contracts rather than restating them:

- the [routing command](../plans/2026-11-08-smart-routing.md#task-s31-single-routing-command) (S3.1/S3.4);
- [fresh gates](../plans/2026-11-08-smart-routing.md#task-s35-fresh-gates-at-dispatch-rf3) (S3.5);
- [settlement](../plans/2026-11-08-smart-routing.md#task-s36-lineage-settlement-and-retention) (S3.6);
- the [OpenCode handshake, plugin RPC and accounting](../../../clients/opencode.md#handshake);
- PR #192's `reconcile_subscription` op (S3.6c, `clients/opencode.md#uncertain-subscription-holds-s36c` once merged);
- PR #197's C.2 feature contracts (`tests/test_smart_routing_features.py`).

## Design principle: lightweight

The user's binding rule of 10 November 2026, defined here once. It applies to every routing path, collector, reader, launcher and qualification harness mode, and other documents link here instead of restating it.

- **Read usage only on demand.** A usage reading is taken only when the stored one is stale **and** an admission needs it. Nothing reads usage at startup, from `status` or just in case.
- **One reading serves every admission in its freshness window.** Parallel admissions that need the same reading wait for one reader and reuse its result; they never start a second read.
- **No background timers, polling or daemons.** Nothing runs in the background to keep readings fresh. A bounded in-call lock wait (at most the read timeout plus 1 s) is the only wait. Work that must happen periodically, such as an export, happens at the next operation that is due.
- **Zero model calls to measure usage.** Usage and demand come from reads that spend no tokens, or from data that budgeted launches already produce.
- **Short-lived, bounded processes.** A process started to read something has a hard timeout and a graceful stop. A failure fails closed with a fixed reason and backs off; nothing retries in a loop.
- **Reuse what launches already produce.** Prefer readings that a launch, settlement or earlier read already stored over a new read.
- **Keep `prepare` and `check` cheap.** No heavy import on the hot path: a module that only some operations need is imported inside them.
- **State the cost.** A change that adds a process, a read, a network call or import time states that cost and offers the lighter alternative.

## 1. Terms

Each term is defined here once and used with this meaning below.

| Term | Meaning |
|---|---|
| **Selector** | Code or an instruction that decides which model, effort, provider or funding route a delegated worker uses. |
| **Legacy selector** | A selector other than `scripts/routing.py prepare`. They are listed in [section 2](#2-inventory-of-selection-paths). |
| **Legacy writer** | Code that opens new financial exposure in the ledger without a routing admission. Today that is only S1 `Ledger.begin`, called from `usage_routing.Controller._run`. |
| **Launcher** | Trusted code that runs one delegated attempt end to end through the routing command. A coordinator model, a worker or a skill text is never a launcher; they ask a launcher. |
| **Managed launch** | One attempt run by a launcher under the [launcher contract](#3-launcher-contract). |
| **Enforced host** | A host where every model request of the attempt passes `check` before it is sent. Two qualify: OpenCode with the shim and an `enforced` probe, and in-process HTTP launches where the launcher is the only sender (OpenRouter proposals; API Usage once its managed send path exists, [4.3](#43-stop-legacy-admissions)). |
| **Advisory host** | A host where routing chooses and accounts but cannot block a send before it happens. Today: Codex native subagents and Claude Code native subagents. Neither has a qualified pre-transmission hook. |
| **Dollar route** | A route whose billing class spends dollars: direct API (`api_grant`, `api_prepaid`) or `gateway` (OpenRouter). |
| **Observation collector** | Trusted code that builds the `observations` snapshot for `prepare` from local records only ([section 3.3](#33-observation-collector)). |
| **Managed project** | A checkout on the routing settings' `managed_projects` list, or any path inside one. `runtime/routing_settings.py:managed_project(path, settings_path=None)` decides it by file identity (`st_dev`, `st_ino`) of the realpath and its existing parents, so case variants, `..` and symlinks match, and a path that does not exist yet matches through its nearest existing parent. It fails closed: routing settings that exist but cannot be read, or a path that cannot be inspected, count as **managed** (#212). So a corrupt routing settings file also stops every legacy API Usage spending entry ([4.3](#43-stop-legacy-admissions)) until it is repaired. Between wiring and cutover, only managed projects use the new path ([section 4.1](#41-two-phases)). |
| **Activation gate** | The check that decides whether a project may be added to `managed_projects` ([section 4.2](#42-activation-gate)). |

## 2. Inventory of selection paths

The table covers every path that chooses or funds a delegated worker. "Target" is the call sequence after C.3; [section 3.2](#32-call-sequence) defines the steps. Line numbers follow the one base stated in [Appendix A](#appendix-a--inventory-at-the-pinned-base).

| ID | Path (file:function) | What it decides today | Target sequence | Slice |
|---|---|---|---|---|
| N1 | `runtime/delegation.py:_prepare_native` (2055) via `_discovery` (1461), reached from `scripts/workflow.py` `delegation prepare` (300) | Validates a coordinator-supplied `selection{model, effort, mode: explicit/inherit, profile_ids}` against the observed Codex catalog and cached guidance. The coordinator chooses the model from guidance ([clients/delegation.md](../../../clients/delegation.md) "Select suitable worker settings"). It also reserves claims and resources and returns spawn settings. | Delegation reserve (unchanged) → in-process `prepare` with `workflow` and the delegation `attempt_id` → catalog check of the admitted strategy → `dispatch` at the last pre-spawn step → spawn with explicit model and effort → `observe` → `record complete` from `reconcile`. Advisory host. | C3-2 |
| N2 | `runtime/evidence.py:prepare_review` (5691) → `_prepare_native(role='review')` | Same as N1 for an independent reviewer. Independence is checked by evidence, not by route. | As N1, with role `review` (routing role `reviewer`) and an independence constraint from the review policy. | C3-2 |
| N3 | `runtime/delegation.py:prepare_continuation` / `consume_continuation` (3079, 3036) | Reuses a child for unchanged work. The continuation inherits the child's settings. | Reuses the child and its profile, but gets a **fresh** `prepare` and `dispatch` (spec F11). A change of scope or role gets a new profile. | C3-2 |
| N4 | `runtime/workstreams.py:consume_dispatch` (201), `validate_admission` (183) | Workstreams launch gates for a planned job. No model choice. | Gates unchanged. Becomes the last pre-spawn step, where `dispatch` runs for Workstreams jobs. | C3-2 |
| N5 | `runtime/delegation.py:reserve_coordinator` (1947) | Reserves coordinator resources. No model choice. | Never routed (`coordinator_not_routed`). Its usage may join a root through `record observed`. | none |
| C1 | Claude Code native subagents: instruction-only, from [policy 03 worker selection](../../../policies/03-agent-delegation.md#select-capability-and-dispatch) ("Select each worker's model and reasoning…") | The coordinator model picks the subagent model from guidance. No runtime path exists on `main`; D8 (#156–#175) is still open. | Advisory host. In a managed project the coordinator asks a launcher instead of choosing a model (C3-6 text). A runtime path follows D8 and is outside C.3. | C3-6 |
| X0 | Coordinator following [clients/external-delegation.md](../../../clients/external-delegation.md) (lines 22–30) | The actual external choice. The coordinator applies explicit choice, saved preference and "the only eligible route", and otherwise continues natively. | In a managed project the coordinator asks a launcher; explicit choices become `assignment.choice`, and enabled scopes, local preference and `automatic: false` become `constraints`. | C3-4a |
| X1 | `runtime/external_delegation.py:select` (95), `automatic_allowed` (90) | Encodes X0's precedence offline. **No production caller**: only tests call it. | Deleted with no alias. | C3-7 |
| X2 | `runtime/external_delegation.py:check`, `prepare`, `effective_permission`, `native_review` | Source scope, permission, destination and packet provenance, plus review independence. Not a selector. | Kept unchanged, as constraints and pre-send checks inside the launcher. | none |
| A1 | `runtime/usage_routing.py:Controller.evaluate`/`_evaluate` (441, 408), `scripts/usage_routing.py` `evaluate` | Whether an API Usage proposal is `eligible`, `native` or `paused`. **Legacy selector.** | Retired. In phase W a managed project has no API Usage route: every entry refuses (`legacy_route_retired`). After the release, API Usage candidates come from the collector and `prepare` decides. | C3-5; removal C3-7 |
| A2 | `Controller._run` (574) → `Ledger.begin` (643), reached through every entry below | Opens S1 runs and sends through `openai_api`. **Legacy writer.** | Phase W: refused for a managed project ([4.3](#43-stop-legacy-admissions)). The managed send path (`prepare` → `dispatch` → `Ledger.convert_hold` → `openai_api` with a `check` before each request → `record complete` with the measured cost) is a post-release follow-up (#214). | C3-5 (refusal); send path after the release; removal C3-7 |
| A2a | `scripts/usage_routing.py` `run` → `usage_routing.run` (60) | Already disabled: it only raises `live_execution_unavailable` (60–61) and never reaches `Controller._run`. | Deleted in C3-7. | removal C3-7 |
| A2b | `runtime/usage_codex_host.py:execute` (207) → `usage_routing._run_live` (64, call at 252) → `Controller._run_live` (560) → `_run`; reached from `scripts/usage_codex_host.js` (the Codex host bridge) | Live API proposal on the Codex host. | As A2: refused in phase W. | C3-5 |
| A2c | `runtime/usage_canary_host.py:execute` (161) → `usage_canary.run` (70, call at 144) → `usage_routing._run_live` → `_run` | First-canary host, from `skills/manage-usage-routing/references/first-canary.md`. | As A2: refused in phase W. | C3-5 |
| A3 | `Controller.request_handoff`/`claim_handoff` (449, 495); `scripts/usage_routing.py` `handoff`, `claim_handoff` | Queues paused API work for native dispatch in the ledger's `handoffs` table. | Retired. A paused assignment stays `pending` in `prepare` with its reason, and the coordinator prepares it again. Existing records stay as history ([4.4](#44-settle-outstanding-legacy-work)). | C3-5; removal C3-7 |
| A4 | `scripts/usage_routing.py` `reconcile` → `Ledger.reconcile` (1603) | Reports unresolved S1 calls for a scope; settles nothing. | Replaced by `status.ledger.legacy_outstanding` and `settle_legacy_call` (C3-0a). | removal C3-7 |
| O1 | `scripts/openrouter_proposals.py:triage` (154), `run` `begin`/`dispatch` (321) | An advisory candidate list from cached OpenRouter metadata; the caller picks the model. The proposal store records attempts. | Discovery metadata feeds the collector and the evidence compiler. `triage` stays an advisory report that chooses nothing. In a managed project, `begin`/`dispatch` become a managed launch on the `gateway` route and keep `external_delegation.check`, endpoint pinning and `native_review`. | C3-4a |
| O2 | `scripts/openrouter_workers.py:prepare` (101), `_execute` (330) | A caller-chosen OpenRouter model, launched with `opencode run --pure` (OpenCode V1, `plugin: []`, so no shim). | In a managed project, workers run as OpenCode managed launches (C3-3) and keep the read-only permission profile from `configuration` (179). The V1 launch stops admitting. | C3-4b; removal C3-7 |
| R1 | `scripts/routing.py` `prepare`/`check`/`dispatch`/`record`/`settle` | The target decision and admission path. | Called only by launchers, through `managed_service` in-process or the one-shot CLI. | C3-0b, then C3-0a |
| K1 | `scripts/usage_key_setup_host.py` (96, `usage_settings.scope`) | Secure key setup for API Usage. Setup only, not a selector: it opens no spending. | Stays available in managed projects, for the same reason as `configure`/`enable`/`disable` ([4.3](#43-stop-legacy-admissions)). | C3-5 (no change) |
| Q1 | `tests/qualification/opencode_live.py:Run` | Test-only launcher for qualification and the pilot. | Stays test-only. Production code never imports `tests/`. | none |

Council (F08) is not in this repository, so C.3 has no Council caller. Its rule (explicit invocation only; members and independence are hard constraints) is part of the launcher contract and checked by C.2d. Review my Project (F09) delegates through N1/N2, and its skill text changes in C3-6.

## 3. Launcher contract

### 3.1 What a launcher must do

1. **Pin the project.** Run `scripts/routing.py` with `--project <checkout root>` (realpath) for every op; in-process use builds `Service(project=…)`. The refusal code is `project_required`, defined once by the routing service (C3-0b). The service refuses a workflow-bound `prepare` without a pin. As defence in depth, the launcher (C3-1) refuses any launch without a pin, using the same code before it calls the service. `status` must show `project_pinned: true`.
2. **Run a preflight from `status`** before the first `prepare` of a session, and again when `status` changes revision:
   - `authority_required: true`, else `authority_not_required`;
   - `unstamped_open_holds: 0`, else `unstamped_holds_open`;
   - when the assignment could use a dollar route: no `blocked_organizations`, `uncertain_dollar` or `cost_unknown` entries, and `reconcile_hint: null`, else `ledger_reconcile_pending`. Subscription-only assignments may proceed;
   - in phase W only: the checkout is a managed project. Otherwise the caller keeps its current path (`project_not_managed`, which is not an error).
3. **Bind planned work.** Every Workstreams or plan job is prepared with `workflow{client, cwd, session_id, task_id, job_id, attempt_id}` after delegation's own reservation, and uses that reservation's `attempt_id` as the routing `attempt_id`. A planned job without `workflow` is refused (`workflow_required`). Only explicit, unplanned delegation may omit it, and it is then counted in `unbound_open_holds`.
4. **Profile from canonical data.** `job` is `delegation.routing_job(scope, locator)` plus the coordinator's optional `profile`. The launcher never adds fields the canonical job lacks.
5. **Never supply observations.** In a managed project the routing service builds them with the [collector](#33-observation-collector), and a request carrying `observations` is refused.
6. **Validate the admitted strategy against the host's actual catalog** (N1's existing catalog check, the OpenCode provider listing, the OpenRouter endpoint) and against the [entitlement rule](#34-listed-is-not-entitled). On a mismatch the launcher sends nothing, records `not_dispatched` and refuses with `catalog_mismatch` or `route_not_entitled`.
7. **Dispatch immediately before creating anything on the host.** Keep the token in memory only. Pass it only to the shim's `routing.attach` (OpenCode) or to the in-process `check` before each send. Never log, store or return it.
8. **Account and settle** with the host's re-read state. On OpenCode, follow the [wiring order](../../../clients/opencode.md#usage-accounting-and-resync), including `routing.descendants` before `detach`. Send a stable `event_id` per lifecycle event, and `usage` with `cost_usd_micros` on `complete` for every dollar route.
9. **Lost acknowledgment.** If the host launch cannot be confirmed, `record uncertain` and stop. Recovery belongs to the operator ([3.5](#35-operator-recovery)).

### 3.2 Call sequence

```text
preflight(status) ─► [delegation reserve → attempt_id]
   ─► prepare{assignment, job, constraints?, workflow}      (service adds observations)
        pending → report reason, hold nothing, stop
        blocked → report reason, stop
   ─► host catalog + entitlement check ── fail → record not_dispatched, stop
   ─► (OpenCode) routing.health ok
   ─► dispatch{attempt_id, request} → authority, session_id, message_id
   ─► (OpenCode) routing.attach → create session + prompt with derived ids
      (in-process HTTP) check → send, per request (proposals: the check runs before the packet is returned)
      (advisory native) return the route to the coordinator, which spawns with explicit model/effort
   ─► idle → re-read → (OpenCode) routing.descendants → opencode_accounting
   ─► record complete{usage} → (OpenCode) routing.detach → settle (read-only report)
```

`record observed` joins coordinator or auxiliary usage to an open root when it can be observed. Native delegation (C3-2) calls the routing service in-process (`scripts.routing.Service`), so `runtime.routing.admit` runs in the caller's process; the C.2 counting contracts depend on this.

### 3.3 Observation collector

`prepare` trusts the `observations` it is given: billing class, health flags, windows and credentials. Today the caller supplies them; the qualification harness builds them by hand. C.3 changes this in two parts.

**Collector (C3-1).** `runtime/routing_observations.py:collect(project, assignment, *, settings_path, ledger_path, source=None)` reads only local trusted records. It never reads a secret, makes a network call or calls a model; the one exception is the on-demand [Codex window read](#codex-window-reader) (`refresh_windows`, #231). Both store paths are required keywords, and the collector reads only those stores, never `default_settings()` or `default_ledger()`: the routing service passes its own paths, including non-default `--settings` and `--ledger` ones (coordinator decision, #212, #213). The ledger is opened read-only (`mode=ro`, 0.2 s wait); a busy or unreadable ledger leaves dollar routes out as `funding_unverified`.

| Fact | Source |
|---|---|
| Candidate routes and billing class | The route manifest `runtime/routing_routes.json` (C3-1; plan Task 0.3 never created it). It is built from the [capability matrix](../../smart-routing-capability-matrix.md) and its content is subject to [user decision 5](#71-for-the-user); until then it ships only a `test_only` route, so no project can be activated. A route that is absent, `unverified` or `unsupported` is not a candidate. See [manifest shape](#route-manifest-shape). |
| Subscription windows | Rate-limit observations the launcher stores after settlement (`routing_observations.store_window`, written by C3-3's launcher), in a private file next to the routing settings (`window-observations.json`), with their observation time and [unit](#route-manifest-shape). An older observation never replaces a newer one, and one dated more than 60 s ahead of the clock is refused. A route with no stored window is left out (`window_unobserved`), so `prepare` stays `pending` until the operator's [`seed_window`](#seed-window) seeds it (user decision 6, #228); a stored window older than 900 s is kept with `fresh: false` and `admit` reports `stale_observation`. For the `chatgpt-subscription` pool, whose OpenCode route reports no `native.rateLimit`, the [Codex window reader](#codex-window-reader) stores host readings on demand (#231; live P0.2 checks are pre-canary). |
| Dollar organizations | The ledger's organizations and balances (existing `observe` paths). |
| Credentials | Binding readiness metadata (state, scope, reference); the secret is never resolved. A `gateway` route's credential comes from the cached `openrouter_settings.status` report the collector already reads: `{state: 'ready', scope: assignment_scope(report.checkout), ref: report.activation.ref}` (`routing_observations._gateway_credential`, #220). A source's own credential is ignored for gateway routes, and a report that is not ready leaves the route out (`integration_disabled`, `credential_unavailable` or `openrouter_operation_busy`). Direct API routes get no credential from the local source (`credential_unavailable`), because API Usage has no managed route in phase W ([4.3](#43-stop-legacy-admissions)). |
| OpenRouter | `openrouter_settings.status(cwd)` (304) only, **never** `admission`. `integration_enabled` is true when the report is `enabled` and its `routes` include the host's route (`proposals` or `workers`); `local_configuration` must be `'ready'` (what `admission` requires). The report's `revision` is returned as `seen.openrouter_revision` ([return shape](#collector-return-shape)). If `status` fails with `openrouter_operation_busy`, the gateway route is left out with that reason ([lock](#openrouter-lock-and-revision)). |
| Entitlement | The entitlement record ([3.4](#34-listed-is-not-entitled)). |

<a id="collector-return-shape"></a>**Collector return shape (C3-1 owns it; defined once, here).** `collect` returns `{observations, seen}`:
- `observations` is the snapshot `routing.admit` takes (`now`, `routes`, `pools`), plus `excluded: [{route, reason}]`, one fixed reason for every route left out: `route_invalid`, `route_unqualified`, `billing_class_excluded`, `not_entitled`, `entitlement_unavailable`, `openrouter_operation_busy`, `openrouter_unavailable`, `integration_disabled`, `credential_unavailable`, `window_unobserved`, `window_unit_undeclared`, `window_unit_mismatch` or `funding_unverified`;
- `seen = {openrouter_revision: <int or null>}` holds the revisions the collector read: revisions only, never settings content or secrets. `null` means it could not read one, or no gateway route needed it.

C3-1 defines this shape. C3-0b only passes `seen` through: its service echoes it unchanged, as an opaque mapping, in every `prepare` response that follows collection for a managed project (`admitted`, `pending`, and a workflow-binding `blocked`, #212). C3-4a and C3-4b read `seen.openrouter_revision` from that response.

<a id="route-manifest-shape"></a>**Route manifest shape (C3-1, C3-1b; defined once, here).** The manifest is an object, never a bare list: `{version: 1, window_unit, routes: [...]}` (#213; C3-0b's gate refuses any other shape with `manifest_invalid`). Every manifest reader goes through `routing_observations.manifest_routes`, which applies two rules (#220):
- **Window unit.** `WINDOW_UNIT = 'utilization_thousandths'` (`runtime/routing_observations.py`). The manifest's top-level `window_unit` applies to every route that does not declare its own, and every stored window carries `unit`. A subscription route is left out when it declares no unit (`window_unit_undeclared`) or when a window its `demand` names has a missing or different `unit` (`window_unit_mismatch`).
- **Endpoint uniqueness.** `routing_observations.validate_routes` refuses the whole manifest (`route_manifest_endpoint_ambiguous`) when two `qualified` routes share the four route-identity fields but differ by `endpoint`; an absent endpoint counts as a value. The route identity has no endpoint field, so such a pair could be confused at dispatch. A refused manifest makes `collect` fail, so `prepare` answers `observations_unavailable`; `manifest_check` fails; and `routing_launch.admitted_check` answers `route_not_entitled`. Adding the endpoint to the route identity is a follow-up.

**Service factory and workflow resolver (C3-0b; defined once, here).** In-process managed launches build their service only with `scripts.routing.managed_service(checkout, scope=None)`, which is `Service(default_settings(), default_ledger(), project=<realpath of checkout>, workflow=routing_gates.D6Workflow(resolve=…, project=…))`:
- A caller that already holds the canonical delegation scope (C3-2) passes `scope=`, and the reader resolves every locator to that scope. This is the same injection the gate tests use (`D6Workflow(resolve=…)`). The pin is still compared with `scope.repository_root`.
- Without `scope`, the reader uses the default entry-path resolver (`routing_gates._resolve_scope`).

P2 showed this is needed: the default resolver refuses a checkout without recorded qualification (`qualification_unobserved`). Since #219 the resolver's startup errors keep distinct reasons: a canonical-acquisition deadline is `observation_deadline` (`runtime/startup.py:ObservationDeadline`), and a busy runtime state is `runtime/state_errors.py:StateBusy` ("Runtime state is busy"), instead of `qualification_changed` or the generic "unavailable, corrupt, locked or incompatible" message. The routing service still answers every reader failure at `prepare` as `workflow_unavailable`, so each fails closed, and a retry may succeed once the machine is less loaded.

**Service enforcement (C3-0b).** `FIELDS['prepare']` makes `observations` optional. When the service is pinned to a managed project, `prepare` refuses a request carrying `observations` (`observations_not_accepted`) and calls the collector itself. If the collector module is missing or raises, `prepare` is blocked with `observations_unavailable`, so C3-0b fails closed until C3-1 lands. Request observations stay accepted only for unmanaged projects in phase W (the pilot and qualification harness, on their own ledgers) and under `--test-inputs`. C3-7 removes the unmanaged case.

**Source seam (C3-1; test-only use).** This seam is defined once, here. `collect` reads its route manifest and window observations from a source object. With `source=None` it uses `routing_observations.default_source(settings_path)`, which returns the local source (`LocalSource`: the shipped manifest, the stored rate-limit observations and the entitlement record next to the given settings path). `StaticSource(routes, windows, credential)` is the code-built source for tests, the C.2 fixture and the self-check sandboxes. A source has three methods:
- `manifest()`, which returns the route list, each route with its `billing_class`, `pool` and `account` (`LocalSource` passes the manifest's `window_unit` down to each route);
- `windows(pool, now)`, which returns that pool's window observations, each with its observation time. A dollar pool's windows name their ledger organization and its revision, as `routing._funding` requires;
- `credential(route, scope)`, which returns binding readiness metadata (`state`, `scope`, `ref`) for a direct API route, never a secret. Gateway routes never use it ([credentials](#33-observation-collector) row above).

Each manifest route carries its own `demand`, which `routing._incomplete` (377) requires: per window id, a conservative per-request estimate in that window's unit. That is the declared [window unit](#route-manifest-shape) for subscription pools, and micro-USD in the `credit` window for `gateway` and direct API pools. A host with a tighter bound, such as a proposal's `output_tokens` times the endpoint price, may lower it but never raise it.

Ledger organizations and balances are not part of the source; the collector always reads them from the ledger it is given. `funding_verified` is a ledger check only: it is true only when the route's organization is enabled in that ledger and has a balance observation no older than 900 s.

<a id="openrouter-lock-and-revision"></a>**OpenRouter lock and revision.** A round-5 throwaway test established how the lock behaves:
- `openrouter_settings.admission` (366) takes a non-waiting exclusive lock.
- `status` is **not** lock-free. It takes a brief non-waiting shared lock (286), so while another handle holds `admission`, `status` fails with `openrouter_operation_busy`, and a second `admission` fails with `openrouter_storage_or_scope_unavailable`.
- Before the lock, `status` works, and `admission(..., revision=<status.revision>)` succeeds. A changed revision fails with `openrouter_preparation_stale`.

So:
- **The collector** never takes the `admission` lock; it calls `status`. If `status` fails with `openrouter_operation_busy`, the collector leaves the gateway route out with that fixed reason, sets `seen.openrouter_revision` to `null` and never retries in a loop; the assignment is then `pending`. Otherwise it returns the report's revision in `seen` ([return shape](#collector-return-shape)).
- **The OpenRouter hosts** (C3-4a, C3-4b) run routing `prepare` **before** `settings.admission` (`scripts/openrouter_proposals.py:324`, `scripts/openrouter_workers.py:126`). They then call `settings.admission(..., revision=seen.openrouter_revision)`, so a settings change in between fails closed with `openrouter_preparation_stale`. That leaves an admitted hold, which the host closes with `record not_dispatched`.

C3-1 also defines two helpers that every wiring slice and the test helper use, so a test route always matches what a slice requests:
- `routing_launch.route_identity(host, **fields)` maps host-specific fields to `{provider, model, effort, harness}`, the `ROUTE_IDENTITY` fields of `runtime/routing.py` (333). The hosts (`routing_launch.HOSTS`) are:
  - `codex_native`: provider `openai`, harness `codex-native`;
  - `openrouter_proposal`: provider `openrouter`, harness `openrouter-proposal`;
  - `openrouter_worker`: provider `openrouter`, harness `routing_launch.shim_harness('openrouter')`, which is `opencode provider openrouter`;
  - `opencode`: the request's own provider, harness `routing_launch.shim_harness(provider)`;
  - `api_usage`: provider `openai`, harness `api`.

  For both OpenCode hosts the harness is exactly the name the OpenCode shim gives the provider (`harnessFor(provider)` in `runtime/adapters/opencode_plugin.ts`, `HARNESS` in `runtime/adapters/opencode.py`), such as `claude-agent-sdk via opencode` or `opencode provider <p>`, because the shim's `routing.attach` checks an admission with that name; a node test compares the two (#220). For the OpenRouter hosts the request's `provider` is kept as the pinned endpoint (`routing_launch.pinned_endpoint`), outside the identity. A missing effort becomes `none`. Unknown fields are ignored.
- `routing_launch.choice(host, funding, **fields)` returns `route_identity(...)` plus `funding` (`subscription` or `api`). This is the only shape of `assignment.choice` that `routing._valid_assignment` (661) accepts; `api` covers both direct API and `gateway` routes (`FUNDING_OF`, 330).
- `routing_launch.assignment_scope(checkout)` returns the assignment `scope` that credential readiness is compared with.

Only code can replace `default_source`: a test patches the module attribute. No request field, setting, table, environment variable or production command-line flag can select a source, the same rule as the S3.5 workflow reader. The C.2 test helper uses this seam ([6.4](#64-c2-marker-map-and-pr-197-amendments)).

**One exception: the self-check sandbox.** The `--managed-self-check <workdir>` flag ([4.2](#42-activation-gate)) builds its own test source in code. It applies only to stores it creates inside `workdir`. The script refuses the flag unless `workdir` is an empty, owner-only directory outside every repository and outside the default state directory, and it never opens a default path. So the flag selects a sandbox, never a source for real stores.

<a id="seed-window"></a>**First window reading: `seed_window` (#228; user decision 6).** The one-shot operator op `seed_window{pool, window, used, limit, reset_at, length, event_id}` stores the first reading of a subscription window, so the first managed launch on a route is not left `pending`. The op's request contract and replay rules are in [clients/opencode.md](../../../clients/opencode.md#uncertain-subscription-holds-s36c); the coordinator decisions of #228 are:
- `limit` must equal `WINDOW_UNITS` (1000, the full scale of `utilization_thousandths`), so a seed can never show more headroom than a real reading (review B1).
- `length` must equal the host's length for that window id (`RATE_WINDOWS`: `5h` 18,000 s, `7d` 604,800 s). The window ids a pool may be seeded for come from the `demand` of a qualified `included_subscription` manifest route, because plans differ; a pool that any dollar route of any status draws on is refused.
- A seed may replace only a **stale** host reading (older than 900 s). It is refused with `fresh_observation_exists` while a host reading is fresh or dated ahead of the clock. A host reading always replaces a seed, whatever its `observed_at`, so a late stale host reading makes the route stale again (fails closed).
- The op creates the ledger if it is absent, as `prepare` does. `status` lists stored windows with their `source` (`windows`, at most 16; an unreadable store never fails `status`).
- `WINDOW_UNITS` and `RATE_WINDOWS` are defined once in `runtime/routing_observations.py`; `runtime/opencode_launcher.py` re-exports them (#229).
- Under the [lightweight principle](#design-principle-lightweight), `scripts/routing.py` imports `routing_observations` only inside the operations that need it, and its process modules only inside the self-check, so importing the command loads neither (a fresh-interpreter test checks this).

<a id="codex-window-reader"></a>**Codex window reader (#231).** `runtime/codex_rate_limits.py` reads the `chatgpt-subscription` pool's windows from a short-lived `codex app-server` with the user's Codex login, read-only and with no tokens spent. The client contract (protocol messages, window mapping, child environment, reasons) is in [clients/opencode.md](../../../clients/opencode.md#chatgpt-subscription-windows-codex-reader). The design facts:
- **Three methods only:** `initialize`, `initialized` and `account/rateLimits/read`. Any server request, error or unexpected message fails the read unanswered.
- **On demand.** The collector reads only when `refresh_windows` is set, an admissible `included_subscription` route on the pool needs a window, and the stored reading is missing that window or older than 900 s. A fresh reading (a settled launch's, a seed's or an earlier read's) is reused.
- **One reader per pool.** A parallel `prepare` waits up to the read timeout plus 1 s on the pool's lock, then reuses the stored reading; only if the lock is still held is the route excluded with `reader_busy`. A failed read excludes the pool's routes with its fixed `codex_*` reason and starts no new process for 300 s.
- **Bounded process, graceful stop.** Hard timeout 5 s. Stop closes stdin first, so a token refresh in progress can finish; after 0.3 s SIGTERM and then SIGKILL go to the process group, only while the leader is unreaped, so a reused group id is never signalled.
- **`--serve` never reads.** `scripts/routing.py` passes `refresh_windows=not self.serving`: the shim's persistent child must answer within 500 ms, and a read can take up to 5 s and starts a process holding the user's login. Its routes stay stale until a one-shot or launcher `prepare` reads.
- **Operator one-shot.** `scripts/codex_rate_limits.py [--settings PATH]` reads even over a fresh reading or the backoff, never while another read holds the pool.
- **Login.** A read that changes `auth.json` (Codex refreshing its token) is detected by metadata only and stores nothing (`codex_auth_changed`).
- **Cost.** One process per read. In an hour of steady use one pool needs at most four reads (one per freshness window, however many `prepare`s), at most 12 when every read fails (the 300 s backoff), and none under `--serve`. The added `prepare` latency is at most about 5.6 s for the reader and 6 s for a waiter. A real Codex read's time is unmeasured until the live check.

**Qualification harness.** Today the harness sends its own observations: `tests/qualification/canary.py` (278) and `pilot_env.py` (29). The pre-registered canary (S3.7) runs on a managed project, where caller observations are refused. Before the canary, C3-3 updates the harness:
- In canary mode on a managed project, it sends no `observations`; the collector supplies them from the real manifest and stored telemetry. This is the production path the canary measures, without `--test-inputs`, as the validation document requires.
- The bounded pilot mode keeps caller observations on its dedicated unmanaged canary ledger until C3-7. C3-7 moves that mode to `--test-inputs`, which works only with non-default settings and ledger paths and is reported by `status`, so production can never use it.

**Residual risk.**
- The collector trusts local files that the user's own processes can write: settings, the observation cache and the entitlement record.
- A managed OpenCode worker cannot run a shell, but whether it can edit absolute paths outside the project depends on OpenCode's external-directory permission. C3-3's launch profile therefore denies external-directory edits and adds the routing state directory to the shim's `protected_paths`.
- Advisory hosts (Codex, Claude Code) give no such protection. They get no dollar routes ([3.7](#37-what-a-launcher-never-does)), which bounds the damage to subscription window choice.

### 3.4 Listed is not entitled

This is plan follow-up #182.

- A route is a candidate only when the manifest lists it as qualified for that harness and account kind. A catalog listing alone never makes it one.
- When a provider refuses an admitted route as unavailable for the account (OpenCode surfaces OpenAI's `model_not_found` as an HTTP 400), the launcher records the attempt's outcome normally and writes a `not_entitled` mark for that (route, account) to the entitlement record.
- The collector excludes the route until the user clears the mark with a local command. The function exists (`routing_observations.clear_not_entitled(settings_path, route, account)`, #213); the user-facing command is a follow-up.
- C3-3 writes a mark only for the primary session's own HTTP 400 `model_not_found` on the admitted model and variant, and none when the account cannot be read from the manifest (`entitlement_mark: account_unknown`, #218).
- The launcher never retries other routes automatically after such a refusal.

### 3.5 Operator recovery

Operator ops are one-shot only and can never be reached from `--serve` or `record` ([S3.6b-2 rules](../plans/2026-11-08-smart-routing.md#task-s36-lineage-settlement-and-retention)). Since C3-0a (#215) this covers every `scripts/routing.py:OPERATOR_OPS` entry (eight since `seed_window`, #228) and every in-process service as well: only `main()` sets the `Service` capability `one_shot`, and `--serve`, `managed_service` and any other in-process service answer `operator_op_not_served` before reading anything. The launcher prints which one applies and never runs one itself.

| Situation | Op |
|---|---|
| Uncertain dollar hold | `reconcile_uncertain` |
| Uncertain subscription hold | `reconcile_subscription` (PR #192) |
| `cost_unknown` hold | `resolve_cost_unknown` |
| Unconfirmed overrun | `resolve_overrun` |
| Over-bound S1 call, legacy block | `resolve_block` |
| Window debit kept wrongly | `drop_debit` (new, C3-0a) |
| Outstanding legacy S1 call in any open state | `settle_legacy_call` (new, C3-0a; [4.4](#44-settle-outstanding-legacy-work)) |
| No window reading yet on a subscription route | `seed_window` (#228; [3.3](#seed-window)) |

`set managed_projects` is also one-shot only, through the same `one_shot` capability ([4.2](#42-activation-gate)), but it is a settings change, not a recovery op.

### 3.6 Refusal reasons

These are new fixed codes, each defined once by the slice named. Reasons from the routing command pass through unchanged.

| Code | Defined by | Meaning |
|---|---|---|
| `project_required` | C3-0b (service); C3-1 reuses it | No `--project` pin. |
| `observations_not_accepted`, `observations_unavailable` | C3-0b | See [3.3](#33-observation-collector). |
| `managed_path_incomplete`, `invalid_managed_projects` | C3-0b | Activation gate not met ([4.2](#42-activation-gate)); a malformed `managed_projects` list. |
| `legacy_route_retired` | C3-0b (code constant), used by C3-2, C3-4a, C3-4b and C3-5 | A legacy path was reached for a managed project, or a replay of work begun before the project became managed. |
| `authority_not_required`, `unstamped_holds_open`, `ledger_reconcile_pending`, `project_not_managed`, `workflow_required`, `catalog_mismatch`, `route_not_entitled`, `dollar_route_needs_enforced_host` | C3-1 | Launcher preflight and checks. |
| `open_unrouted_assignments`, `managed_during_prepare` | C3-2 | The [activation precondition](#42-activation-gate); a project that became managed during an unmanaged `prepare`, whose new assignment is released with no permit. |
| `host_unhealthy`, `profile_not_enforced`, `api_funding_unqualified`, `attach_failed`, `launch_unconfirmed`, `interrupted` | C3-3 | OpenCode launch. `host_unhealthy` covers any `routing.health` answer other than `ok`, including an RPC error. `launch_unconfirmed` follows `record uncertain`. `interrupted` is the command's answer after a signal ([C3-3](#63-slices)). |
| `routing_reservation_lost` | C3-4a | A proposal `begin` whose [write-ahead entry](#63-slices) recovery already closed, for example after a pause past the 120 s lease. |
| `advisory_host_unmanaged`, `not_enrolled`, `enrolment_unavailable` | Host rule (#224) | Adding a project whose configured or active clients include none in `MANAGED_HOSTS`; a checkout with neither a canonical enrolment record nor a resolving legacy codex scope; an enrolment that cannot be read ([4.2](#42-activation-gate) step 3). |

A refusal before `dispatch` sends nothing. If `prepare` admitted a hold, the launcher closes it with `record not_dispatched`.

### 3.7 What a launcher never does

- Runs a project command (TUI or `session.command`) inside a managed session. A command's `` !`cmd` `` runs outside the `shell` permission and keeps credential-like variables (observed 9 November 2026), so this rule is the protection.
- Sends on a dollar route from an advisory host (`dollar_route_needs_enforced_host`). Advisory hosts get subscription routes and `worker_choices` only.
- Sends on an API-funded OpenCode route before the S3.6 [required positive and negative runs](../../../clients/opencode.md#plugin-rpc) have been observed with real costs (`api_funding_unqualified`). It always passes `shim_descendants`, or `None` when the call failed.
- Reads a key, prints a token, stores a prompt or brief in the ledger, or exports anything. Observability keeps its own opt-in.
- Changes the coordinator's model, effort or session settings, or routes the coordinator.
- Grants merge, push, cleanup, title adoption or acceptance. A routed `complete` is not acceptance; acceptance comes only from `delegation.routing_acceptances`.
- Calls an operator op or `set managed_projects`, or retries a lost acknowledgment with a second `dispatch`.
- Falls back to a legacy selector when `prepare` is pending or blocked.

## 4. Rollout and legacy retirement

### 4.1 Two phases

A14 rules out shadow mode, and the canary needs real wiring (plan S3.7). C.3 therefore lands in two phases.

- **Phase W (wiring).** Slices C3-0a to C3-6 add the managed path to every caller and update the instructions. A caller uses the path only for a managed project; every other checkout keeps its current selector, as plan S2.7 and S3.7 already allow. `managed_projects` starts empty. For a managed project, routing is fully active; there is no decision-only mode.
- **Phase R (retirement).** After C.1 passes and the user decides on cutover, C3-7 removes:
  - the managed-project branch;
  - every legacy selector and the legacy writer;
  - the phase W conditional text in the instructions.

  After that, every checkout uses the managed path.

During phase W, two code paths open exposure in one ledger: routing admissions, and S1 `begin` for unmanaged projects. Both run in the one `Ledger` class and its transactions, so caps and headroom stay shared. C3-7 removes the legacy writer, and no compatibility alias remains.

### 4.2 Activation gate

A project may be added to `managed_projects` only through the one-shot `scripts/routing.py set` with `managed_projects`. `--serve` and every in-process service, `managed_service` included, refuse it with `operator_op_not_served`, as they refuse operator ops ([3.5](#35-operator-recovery)); entries are stored as realpaths. Adding a project passes three checks in `scripts/routing.py:Service._managed_change` and writes nothing if any fails, then a fourth after the write. The code runs step 3 first (#224), then steps 1 and 2:

1. **Activation precondition (coordinator decision, #217).** `delegation_routing.open_unrouted_assignments(checkout)` must list nothing: no prepared, unobserved native worker or review assignment, which may hold a permit granted before the project became managed. Otherwise the answer is `open_unrouted_assignments`. The check is read-only, bounded to 64 ids, and fails closed (`scope_unavailable`, `state_unavailable`). It resolves one scope per enrolled client that keeps runtime state (`runtime/delegation_routing.py:_enrolled_clients`, `_scopes_for`, #222); without a canonical enrolment record it falls back to `codex`. It is not atomic with delegation's reservation, so step 4 repeats it after the write.
2. **Activation gate**, defined once in C3-0b as a fixed list of required capabilities (`scripts/routing.py:CAPABILITIES`). A capability counts only when its behavioural self-check passes; the code merely being present is not enough.
3. **Host rule (C3-6, #221; code-enforced by #224).** A project whose coordinator delegates through a host with no managed launcher, today Claude Code native subagents, is not added until [user decision 2](#71-for-the-user). `scripts/routing.py:_require_managed_hosts` enforces it, read-only, before the other checks, the self-checks and any ledger lock. `delegation_routing.enrolled_hosts(checkout)` lists the clients whose enrolment entry is `configured` or `active`; `pending` and `suspended` entries are no host, and an unknown status raises. A checkout with no canonical record counts as `codex` only when its legacy codex scope really resolves. The answer is:
   - `enrolment_unavailable` when the enrolment cannot be read (unknown enrolment never joins);
   - `not_enrolled` when there is neither a canonical record nor a resolving legacy codex scope;
   - `advisory_host_unmanaged` when none of the listed clients is in `MANAGED_HOSTS = ('codex', 'opencode')`.

   A project with a mix of clients may join; policy 03's hold covers its Claude Code coordinator. `opencode` is forward-compatible: onboarding records only `codex` and `claude-code` entries today, so an OpenCode-coordinated project must be enrolled for `codex` to be activated. User decision 2 may change `MANAGED_HOSTS`. [Policy 03](../../../policies/03-agent-delegation.md#managed-projects) defines the rule for coordinators, and also what such a coordinator does in a project that is already managed.

4. **Re-check after the write (#222).** `Service._recheck_activation` runs `open_unrouted_assignments` again for each added project. If it finds one, a second ordered write removes only the added projects (keeping any concurrent settings change) and the command refuses with `open_unrouted_assignments` and `rolled_back: true`. The rollback retries up to `MAX_ROLLBACK = 3` times on `stale_revision`; if they run out, or the rollback write fails for any other reason, it is still this refusal, with `rolled_back: false` and the failure chained (#224), and C3-2's pre-managed replay refusal is the backstop. Every such refusal carries the first write's `invalidated_attempts`, which that write already invalidated (#224). This closes the inherit-mode activation race.

Removal skips all four.

**Self-check contract.** Each owning phase W slice adds `managed_self_check(workdir) -> list[str]` to a file it owns. It returns an empty list when the check passes, or fixed failure codes otherwise. The check:
- runs the slice's managed path end to end against stores created in `workdir`: a temporary routing settings file, a ledger, a workflow double and a test source through the [source seam](#33-observation-collector);
- makes no network, model, host or secret-store call, and never touches the production defaults or the checkout.

For each capability, the gate:
- creates a fresh owner-only temporary directory;
- runs the check in a child process started with **`sys.executable`** (the interpreter running the gate), never a `python3` looked up on `PATH`. On this Mac, `PATH=/usr/bin:/bin` gives Apple's Python 3.9.6, which cannot import `runtime.routing` (P3);
- puts the **plugin package root** (`runtime.state.PACKAGE`) first on the child's import path, never the project checkout: `[sys.executable, '-I', '-B', '-c', 'import sys; sys.path.insert(0, sys.argv[1]); …', PACKAGE]`. A script check loads `PACKAGE/scripts/<script>.py` by path and calls its `managed_self_check(workdir)`;
- gives the child exactly this environment, inheriting nothing: `PATH=/usr/bin:/bin`, `HOME=<workdir>/home`, `TMPDIR=<workdir>/tmp`, `LANG=C.UTF-8`;
- enforces the 10 s budget with `subprocess.Popen(..., start_new_session=True)` and `communicate(timeout=10)`, killing the child's whole process group (`os.killpg`) on timeout and failing the check with `self_check_timeout`; collecting the killed check's output is itself bounded to 5 s (#212; `subprocess.run(timeout=)` would kill only the child);
- accepts at most 16 codes matching `[a-z0-9_]{1,64}` from the child, and answers anything else as `self_check_invalid`;
- deletes the directory.

A self-check that nests its own child (C3-1's `routing_launch.managed_self_check`, 8 s budget) shares the gate's 10 s budget.

| Capability | Self-check proves | Owner |
|---|---|---|
| Collector and launcher core | `routing_launch.managed_self_check`: preflight refusals; a launch admits through the collector with the test source; caller observations are refused | C3-1 |
| Route manifest accepted | `runtime/routing_routes.json` has at least one route with `status: qualified` (a data check) | C3-1, content per user decision 5 |
| Native delegation | `delegation_routing.managed_self_check`: one in-process routing admission bound to its workflow job; catalog mismatch sends nothing | C3-2 |
| OpenCode launcher | `opencode_launcher.managed_self_check`: against a fake server, every refusal happens before a session is created, and no token appears in output | C3-3 |
| External choice and proposals | `scripts/openrouter_proposals.py --managed-self-check`: `begin` obtains one admission on a test `gateway` route | C3-4a |
| OpenRouter workers | `scripts/openrouter_workers.py --managed-self-check`: `prepare` obtains one admission, and the V1 launch is refused | C3-4b |
| API Usage | `usage_routing.managed_self_check`: every entry refuses before reading settings, and `Ledger.begin` is never reached | C3-5 |
| Instructions | `policies/03-agent-delegation.md` contains the heading `Managed projects` (a text check) | C3-6 |

If any check is missing or fails, the change is refused with `managed_path_incomplete`, with `failing` (the names) and `codes` (`{name: codes}`), and nothing is written. Removing a project is always allowed, and it is also one-shot only. C3-7 deletes the gate, the self-checks, the activation precondition and re-check, and `managed_projects` together.

On `main` at `4bc2641` (after #225), `scripts/routing.py:activation_gate()`, run with a temporary `HOME` and only the self-checks' own sandboxes, answers `{route_manifest: [no_qualified_route]}`: the manifest qualifies no route until user decision 5. So no project can be activated yet.

### 4.3 Stop legacy admissions

- **Phase W, per project.** The refusal sits at the narrowest point every path crosses, so no entry point can bypass it:
  - **API Usage entry points.** Each refuses a managed project with `legacy_route_retired` as its first action. That comes before it opens `usage_settings.controller`, reads API Usage, OpenRouter or credential settings, validates a credential binding, writes configuration or an organization, or touches the ledger. Its only read is the routing settings' `managed_projects`. The entry points are:
    - `usage_codex_host.freeze` (116; A2b's first step, reached through the bridge's `freeze` action, `scripts/usage_codex_host.js` 5 and 61), before `usage_settings.controller` (121);
    - `usage_codex_host.execute` (207; A2b, the bridge's `execute` action), before `usage_settings.controller` (223) and before the credential-binding checks it reaches through `_plan_settings`;
    - `usage_canary_host.execute` (A2c), before `usage_settings.scope` and before `controller(create=True)` and `controller.configure` (185–192), so no S1 configuration or organization is written;
    - `usage_canary.run` (70) and `usage_routing._run_live` (64, before `controller` at 69), both on the A2c path;
    - the module functions `usage_routing.evaluate` (701), `handoff` (717) and `claim_handoff` (724), before `controller(...)` (A1, A3).

    The bridge (`scripts/usage_codex_host.js`) only forwards to `freeze` and `execute`, so it gains no logic of its own. It still relays a refused `execute` as `uncertain: true`; the API Usage procedure says once that `legacy_route_retired` proves nothing was sent (#214).

    The refusal is `runtime/usage_routing.py:refuse_managed(path)`. Its only read is the routing settings' `managed_projects`, through `routing_settings.managed_project`, so it fails closed like that predicate ([managed project](#1-terms)); a `None` path also refuses.

    **Setup actions stay available, by design.** `usage_routing.configure`, `enable`, `disable` and `status`, the CLI actions of the same names, and `scripts/usage_key_setup_host.py` stay available in managed projects during phase W. They change the user's own settings and open no spending. Every spending entry above refuses first, and `api_usage_enabled` is the only spending authority (spec §10.4 step 4). C3-5 tests that enabling API Usage in a managed project still reaches no legacy `Ledger.begin` path.
  - **No managed API Usage route in phase W (coordinator decision, #214).** The user deferred API Usage until after the router is released, so a managed project gets no API Usage send path in phase W: every spending entry refuses, and nothing else spends. The managed send path (`prepare` → `dispatch` → `Ledger.convert_hold` → `openai_api` with a `check` before each request → `record complete` with the measured cost) is a post-release follow-up, not part of C3-5.
  - **Backstop.** `Controller._run` refuses again before `Ledger.begin`, and `Controller._evaluate` and `request_handoff` refuse the same way, in case a future caller skips an entry point.
  - A2a is already disabled ([inventory](#2-inventory-of-selection-paths)).
  - OpenRouter workers: the V1 launch in `openrouter_workers._execute` refuses a managed project.
  - X0 and C1 are instructions; C3-6 and C3-4a change them for managed projects.
- **Phase R, global.** The entry points are deleted. `scripts/usage_routing.py` keeps only `status`, `configure`, `enable`, `disable` and `quote` (setup and observation, which the API Usage skill still needs). `evaluate`, `run`, `handoff`, `claim_handoff` and `reconcile` go, and become usage errors (exit 2). There is no alias under the old names.

**Old consent is never spending authority (spec §10.4 step 4).** Legacy Usage Routing consent, Manual or Smart Balance state, a saved `external_permission`, an organization's configuration and an S1 qualification record are never translated into API spending. The only authority for a direct API route is `api_usage_enabled` in the routing settings, set by an explicit `scripts/routing.py set`. In a managed project, the Manage API Usage procedure tells the agent to run that one-shot `set` alongside `enable`, and to set it back to `false` on `disable`; the `enable` code itself does not write it (#214). Nothing migrates it from old state. Test (C3-5): a ledger with enabled legacy Usage Routing, current consent and a qualification record, plus default routing settings, still leaves API routes `pending` with `api_usage_disabled`, and resolves no credential.

### 4.4 Settle outstanding legacy work

Before C3-7 merges, `status.ledger.legacy_outstanding` (added by C3-0a) must be empty. It lists:

- every S1 run call that is still open: `reserved`, `dispatching` or `unresolved`, including `over_bound`;
- `legacy_block` organizations.

It also lists `ready` handoffs for information. They hold no exposure and do not block.

Today `Ledger.complete` accepts a `dispatching` or `unresolved` call (`usage_budget.py` 835, the state check), but only the legacy send path calls it. No operator entry reaches it, and `Ledger.reconcile` only reports. So the one-shot op `settle_legacy_call{run_id, phase, evidence, event_id}` (C3-0a) covers every open state, under the [operator-op rules](../plans/2026-11-08-smart-routing.md#task-s36-lineage-settlement-and-retention):

| Call state | Accepted evidence | Effect |
|---|---|---|
| `reserved` | `{kind: never_dispatched}` | The call becomes `rejected-before-send` at cost 0, as `Ledger.close_run` already does for a reserved call (`usage_budget.py` 864), and the release **closes its run**. For a `count` call nothing was charged; for a `generation` call the completed `count` keeps its charge. A run never sends again after a release. This is sound because S1 `dispatch` writes `dispatching` inside its transaction (827), and `openai_api` calls that `admit` before transmitting, so `reserved` proves nothing was sent. |
| `dispatching`, `unresolved` | `{kind: receipts, receipts, cost_usd_micros}` with a cost of at least 1 micro-USD | Charged as reported through the same code as `Ledger.complete`. A cost above the maximum becomes over-bound and is resolved with `resolve_block`. |
| `dispatching`, `unresolved` | `{kind: charge_reservation}` | Charges the maximum and releases nothing. |
| `over_bound` | none | Refused (`block_not_pending`); use `resolve_block`. |

Nothing is released without spend except a provably unsent `reserved` call. A zero cost is `evidence_invalid`. As merged (`runtime/ledger_versions.py`, #215), every settlement closes its run, not only the `reserved` release, except an over-bound result, which `resolve_block` closes: a crashed legacy run never sends again and would otherwise keep its concurrency place. Runs converted from a routing hold (`convert_hold`) are not legacy work: `settle_legacy_call` refuses them (`not_legacy_call`) and `legacy_outstanding` leaves them out, so they never block C3-7. Because every open state has a path, C3-7 can never be blocked forever. Over-bound calls and legacy blocks keep `resolve_block`. C3-7 marks the remaining `ready` handoffs `retired` and keeps the records; `claimed` handoffs belong to native delegation and stay as history.

### 4.5 Preserve history and ownership

Settled charges, cycle caps, S1 runs, handoff records, event rows and root aggregates stay under the existing retention rules (spec §11). Retirement deletes code, never ledger records. Unresolved ownership blocks the affected cutover step until it is settled, as spec §10.4 requires. Unresolved ownership means open holds, uncertain attempts and open delegation assignments.

### 4.6 Ledger version

PR #192 adds `window_debits` to the ledger record without changing the state version, so code from before #192 would ignore a debit. C3-0a raises `STATE_VERSION` to 5 so that older code refuses the ledger (`ledger_identity_changed`), as v4 did for settled charges. The 4→5 step changes no run, hold or handoff.

`runtime/usage_budget.py` is at its 1,616-line ceiling, so C3-0a first moves the version migration (`STATE_VERSION` and the 1→4 steps) into a new `runtime/ledger_versions.py` and adds the 4→5 step there. As merged (#215), `ledger_versions.STATE_VERSION = 5` is the only definition, `usage_budget.py` shrank to 1,553 lines, and v5 code also refuses version 6, 0, a string, a bool or a missing version with `ledger_identity_changed`. C3-7 later removes `Ledger.begin` and its S1-only helpers.

## 5. Rollback, failure modes and security review

### 5.1 Rollback

- **Explicit OFF.** `scripts/routing.py set smart_routing=false`, with no model call.
  - Unsent admissions of the old revision are refused.
  - Dispatched work finishes.
  - Later launches use `worker_choices` through the same allocator.

  This is the only kill switch, in both phases.
- **Previous strategy table.** A refresh that writes a new table version keeps the verified table it replaces, byte for byte, as `strategy-table.previous.json` (one version), and `scripts/routing_table.py rollback` swaps it into place; a second rollback returns to the newer table (#211). The command and its refusals are in the [strategy table refresh](../../../clients/guidance-publication.md#strategy-table-refresh) procedure. A missing or unreadable table already falls back to `worker_choices` (`table_unavailable`).
- **Phase W only.** A one-shot removal from `managed_projects` returns a project to its legacy selector for new launches. Open holds keep their rules and are settled, never dropped.
- **No return to legacy selectors after C3-7.** A revert PR can restore the code, but C3-7 removes no record and the ledger stays at v5.

### 5.2 Failure modes

| Failure | Behaviour |
|---|---|
| Routing command missing, busy or failing | The launcher refuses, passing through `ledger_busy`, `ledger_unavailable` or `internal_error`. Nothing is sent. Interactive sessions are unaffected. |
| Strategy table missing or malformed | `table_unavailable`; `worker_choices` is used. This never blocks (RF8). |
| Collector missing or failing, or a manifest refused by its [shape rules](#route-manifest-shape) | `observations_unavailable`; nothing is admitted. |
| Workflow reader hits a startup deadline or a busy runtime state | The startup reason stays distinct in the runtime's own diagnostics (`observation_deadline`, "Runtime state is busy", #219). `prepare` answers `workflow_unavailable` and `dispatch` `gate_error`; nothing is admitted or sent, and a retry may succeed. The activation precondition fails closed (`scope_unavailable`, `state_unavailable`). |
| Observations stale or missing | The affected routes are infeasible with a reason. The assignment is `pending` and is not sent elsewhere. |
| Workflow job changed after `prepare` | `dispatch` refuses with the S3.5 reason. The hold stays `admitted`, and the launcher records `not_dispatched`. |
| Host catalog lacks the admitted strategy | `catalog_mismatch`, then `not_dispatched`. |
| Provider refuses the route for the account | The attempt settles normally and gets a `not_entitled` mark. No automatic retry. |
| Plugin restarts during an attempt | The launcher attaches again with the same token ([handshake](../../../clients/opencode.md#handshake)). |
| Lost acknowledgment | `record uncertain`, then the operator op from [3.5](#35-operator-recovery). |
| Coordinator never spawns a dispatched native attempt | Delegation `reconcile` settles it (`record complete`, subscription release). A dollar route cannot reach this state on an advisory host. |
| Settings change mid-flight | S3.1 rules: unsent admissions are refused, and dispatched work continues. |
| Ledger above `SOFT_RECORD` | `prepare` commits nothing, and window debits never clear (fail closed). |
| Crashed legacy S1 call | Listed in `legacy_outstanding` and settled with `settle_legacy_call`. |

### 5.3 Security review points

Each slice's reviewer checks the points its files touch.

1. **Observation trust.** For managed projects, no request field, coordinator text or worker output reaches `observations`, a wider set of `constraints`, `workflow` or the manifest. Only the collector produces observations.
2. **Token handling.** The authority token appears only in the `dispatch` response, launcher memory, `routing.attach` and in-process `check`. Never in logs, errors, files, `status` or child process environments.
3. **Project pin.** Every production `Service` has `project`, and a `cwd` outside the pinned checkout is refused.
4. **One-shot changes.** No launcher code path calls an operator op or `set managed_projects`, and `--serve` refuses all of them.
5. **Credential isolation.** Launchers start host processes with an allowlisted environment, so no credential-like name reaches them ([C3-3](#63-slices)), and never run project commands in managed sessions.
6. **Funding boundaries (RF1, RF6, spec §10.4 step 4).**
   - Smart Routing ON never enables API Usage, OpenRouter or observability.
   - A direct grant never funds a gateway.
   - `purchased_usage` is never subscription headroom.
   - Advisory hosts never get dollar routes.
   - Old consent is never spending authority.
7. **Legacy refusal placement.** Every API Usage entry point, including the Codex host's `freeze` and `execute` behind the bridge, refuses a managed project as its first action, before any settings read, credential-binding check, configuration or organization write, or ledger access ([4.3](#43-stop-legacy-admissions)). `Controller._run` repeats the refusal before `Ledger.begin` as a backstop.
8. **History preservation.** No migration or retirement deletes runs, charges, caps, events or open holds.
9. **Review independence (F13).** Reviewer constraints come from the review policy, not from the coordinator's request.

## 6. Slice plan

Each slice is one PR. A slice may edit only the files it owns; every other file is read-only for it, except as the [shared-file protocol](#61-shared-file-protocol) allows. [Appendix A](#appendix-a--inventory-at-the-pinned-base) gives every grep hit an owner.

`scripts/validate.py` sets line ceilings, and four touched files are at theirs: `runtime/delegation.py` (3,369), `runtime/evidence.py` (8,505), `runtime/routing.py` (1,506) and `runtime/usage_budget.py` (1,616). New code therefore goes into new modules, and edits to those four files are line-neutral or shrink them.

### 6.1 Shared-file protocol

The protocol is defined once here and applies to every slice.

- **One owner per file.** Each file has exactly one owning slice at a time, as listed in [6.3](#63-slices). Where two slices must edit one file, they run in sequence, never concurrently: the later slice depends on the earlier one, and the file's entry names both owners in order. The sequenced files are:
  - `scripts/routing.py`: C3-0b, then C3-0a, then C3-2 (the three-line activation precondition, assigned by the coordinator because no open slice owned the file, #217), then C3-7;
  - `runtime/opencode_launcher.py`, `tests/test_opencode_launcher.py`: C3-3, then C3-1b (identity and window-unit alignment after #218 merged, #220);
  - `runtime/delegation_routing.py`: C3-2, then C3-1b (only its self-check sandbox declares the window unit, #220);
  - `clients/opencode.md`: C3-0a (S3.6c section), then C3-3 (launcher section);
  - `tests/openrouter_fixtures.py`: C3-4a, then C3-4b;
  - every other file C3-7 edits: its phase W owner first, then C3-7.
- **`tests/test_smart_routing_features.py`** (PR #197, Lane D). A slice deletes only the `@NEEDS_C3('<slice>')` decorator lines that name it, and changes nothing else in the file. Two exceptions edit separate, marked sections, so C3-0b and C3-1 can work on the file in parallel:
  - C3-0b deletes only its marker line, `@NEEDS_C3('C3-0b')` on `test_a_workflow_bound_prepare_requires_the_project_pin`.
  - C3-1 owns the body of `managed_project_fixture`, between `# --- managed_project_fixture: body owned by C3-1 ---` and `# --- end managed_project_fixture ---`. It also rewrites the one unmarked test that asserts the sentinel, `NeedsC3MarkerTests.test_the_managed_project_fixture_is_an_assertion_until_c3_1`, to check the real fixture instead: an unknown id prefix raises `FixtureError`, and `fixture_route` still refuses other billing classes.

  **Unexpected-pass rule.** The slice that makes a contract pass removes that contract's marker in the same PR. Otherwise the module's `_PASSED` check fails the run. A contract that still fails after its marker is removed means the slice is not done. Structural changes to the file go through Lane D.
- **Shared records** that several slices append to, and that this list gives no other rule, are append-only. Each slice appends its own section and never edits another's, so each section keeps its owner (see [ownership and parallel work](../../../policies/03-agent-delegation.md#ownership-and-parallel-work)). On a conflict, take `origin/main`'s version, re-append your own section, and commit.
- **Only C3-0b and C3-0a (and later C3-7) edit `scripts/routing.py`, and only C3-0a edits `runtime/routing.py`.** C3-1, C3-3, C3-4a, C3-4b, C3-5 and C3-6 never edit either file. When one of them needs a service change, it is added to C3-0b's or C3-0a's scope or to a follow-up by the same owner, never edited in parallel. The one exception is C3-2's activation precondition (above), made after C3-0b and C3-0a had merged.

### 6.2 DAG

```text
C3-0b  routing service: pin, managed projects, gate, observations, factory   (no #192 dependency)
C3-1   launcher core + collector + manifest                                   (parallel with C3-0b)
C3-0b               → C3-0a  ledger v5, legacy settlement, debits (2nd owner of scripts/routing.py; #192 is on main)
C3-0b + C3-1        → C3-2   native delegation (shared core) → C3-6 phase-W instructions
C3-0b + C3-1        → C3-4a  external choice + OpenRouter proposals
C3-0b + C3-1        → C3-5   API Usage (refusals only in phase W)
C3-0a + C3-1        → C3-3   OpenCode launcher
C3-1 + C3-3         → C3-1b  shim harness naming, gateway credential, endpoint uniqueness, window unit
C3-1b + C3-3 + C3-4a → C3-4b OpenRouter workers
[C3-0a..C3-6 merged → activation gate passes → canary project added → S3.7 → C.1 → user cutover]
                    → C3-7   retirement (shared core)
```

Parallel sets (no two slices in a set own the same file):
1. C3-0b and C3-1.
2. After both: C3-0a, C3-2, C3-4a and C3-5.
3. C3-3 after C3-0a and C3-1; C3-6 after C3-2.
4. C3-4b after C3-3 and C3-4a.

Lane B's table is a soft dependency: without it, `prepare` falls back to `worker_choices` or the explicit choice.

### 6.3 Slices

**C3-0b — Routing service pre-C.** No #192 dependency. Phase W. **Merged (#212), package only.**
- **Owns:**
  - Code: `scripts/routing.py` (first owner); `runtime/routing_settings.py` (`managed_projects`, the `managed_project(path, settings_path=None)` predicate of [section 1](#1-terms), the `LEGACY_ROUTE_RETIRED` code constant).
  - New tests: `tests/test_routing_managed_projects.py`.
  - Existing tests it changes (P1, [Appendix B.0](#b0-prototype-runs-validated-by-running)): `tests/test_routing_gates.py` and `tests/fixture_routing_accounting.py`; also `tests/test_routing_settings.py` for `managed_projects`.
  - In `tests/test_smart_routing_features.py`: its own marker line only.
- **Does:**
  - **`project_required`:** `Service._workflow_binding` refuses a workflow-bound `prepare` without `project`, before any workflow read.
  - **Pin exactly the tests that send an unpinned workflow-bound `prepare`.** This list is P1-verified: 664 tests pass with it.
    - `tests/test_routing_gates.py`:
      - `GateBase.setUp` (118) pins `project='/work/repo'`;
      - the re-created service in `ConcurrencyTests` (508) pins the same;
      - `CanonicalWorkflowTests.setUp` (533) pins `str(self.scope.repository_root)`;
      - `test_an_oversized_serialized_binding_is_refused_at_prepare`: one changed line (905) moves its wide `cwd` under the pinned root (`'/work/repo/' + 'é' * 500`), so it still proves `binding_too_large` instead of hitting `project_mismatch`. The value is reused unchanged at 909.
      - Unchanged: 462 and 497 (`dispatch` only), 893 (`status` only) and the already pinned services.
    - `tests/fixture_routing_accounting.py`: `admit(..., root=…)` (86–87) and `admit_sub(..., root=…)` (95–98) send their workflow-bound `prepare` through a new `call_bound`. It uses a short-lived `command.Service(self.settings, self.ledger, workflow=self.reader, project='/checkout', test_inputs=self.service.test_inputs)` (`/checkout` is the `cwd` of `where()`). `self.service` (75) stays unpinned, because `settle` without `workflow` is `project_required` on a pinned service (`scripts/routing.py` 448). This one change covers `test_routing_accounting.py` (199–243), `test_routing_accounting_roots.py`, `test_routing_accounting_blocks.py` (288) and `test_routing_subscription_reconcile.py` (117, 177); those four files are unchanged.
    - `tests/test_smart_routing_features.py`: delete `@NEEDS_C3('C3-0b')` (495).
  - **The observation refusal and collector call** ([3.3](#33-observation-collector)), echoing the collector's `seen` unchanged as an opaque mapping in every managed-project `prepare` response ([return shape](#collector-return-shape); C3-1 owns the shape).
  - **`managed_projects`** as a one-shot setting with the activation gate ([4.2](#42-activation-gate)). The setting holds at most 64 distinct, absolute, normalized paths of at most 1,024 characters (`invalid_managed_projects` otherwise). One-shot is the `Service` capability `one_shot`, which only `main()` sets (review B1 of #212). It is a constructor argument, so in-process code could pass it; only review prevents that (§5.3 point 4).
  - **Collector call.** The service calls `collect(str(project), assignment, settings_path=<its settings path>, ledger_path=<its ledger path>)` ([3.3](#33-observation-collector)). A pinned service whose settings file is unreadable treats its project as managed, so even an unmanaged project's `prepare` that carries observations gets `observations_not_accepted` (`scripts/routing.py:Service._managed`). An unmanaged `prepare` without observations is `invalid_request`.
  - **The shared factory** `scripts.routing.managed_service(checkout, scope=None)` (G3; [3.3](#33-observation-collector)). It is the only way production code builds an in-process service for a managed launch. `delegation_routing` (C3-2), `openrouter_proposals` (C3-4a), `openrouter_workers` (C3-4b), `opencode_launcher` (C3-3), `usage_routing` (C3-5) and the C.2 fixture all use it. It reads `default_settings()`/`default_ledger()` at call time, so the fixture's patches take effect.
- **Tests:**
  - an unpinned workflow `prepare` is `project_required`, and `settle` without `workflow` on an unpinned service still works;
  - a managed-project `prepare` with `observations` is `observations_not_accepted`, and without a collector `observations_unavailable`;
  - with a stub collector returning `seen = {'openrouter_revision': 7}`, the managed-project `prepare` response (admitted and pending) carries `seen` unchanged;
  - `set managed_projects` is refused under `--serve`, and refused with `managed_path_incomplete` while any self-check is missing or failing; removal is always allowed;
  - `managed_service` pins the repository root and uses the default paths.
- **Markers:** `test_a_workflow_bound_prepare_requires_the_project_pin`.
- **Shared core:** no. **Size:** M (about 350 lines with tests).

**C3-0a — Ledger pre-C.** Depends on C3-0b (`scripts/routing.py` sequencing); #192 is already on `main`. Phase W. **Merged (#215), package only.**
- **Owns:**
  - New: `runtime/ledger_versions.py`, `tests/test_routing_legacy_settlement.py`.
  - Code: `runtime/usage_budget.py` (move-out only, net shrink); `runtime/routing.py` (line-neutral); `runtime/subscription_reconcile.py`; `scripts/routing.py` (second owner: new ops, `status` fields, the `--serve` debit flag).
  - Docs: the S3.6c section of `clients/opencode.md` (first owner).
  - Existing tests:
    - `tests/test_routing_subscription_reconcile.py` and `tests/test_routing_funding_capacity.py`;
    - `tests/test_routing_dispatch.py` (222, 806 `serving=True`, 880), for the `--serve` debit change;
    - `tests/test_routing_reconcile.py` (772) and `tests/test_routing_accounting_blocks.py` (332, 336), which assert or patch `STATE_VERSION`;
    - `tests/test_usage_budget.py` (458, 498, 506, 648, 653), which assert the migrated `version`. C3-7 owns this file next, when it removes `Ledger.begin`.
- **Does:**
  - state v5 and its downgrade guard ([4.6](#46-ledger-version));
  - window-debit clearing never under `--serve` (the #192 review). The line-neutral `runtime/routing.py` change passes a `clear_debits` flag from the service into `admit` (default `False`) instead of always calling `subscription_reconcile.clear_debits` (137). As merged after review S1 of #215, `scripts/routing.py:Service._prepare` clears debits only in the one-shot command, or from a snapshot the collector built for a managed project in an in-process service that is not serving. Caller-supplied observations never clear a debit in-process;
  - **every operator op one-shot only** (review S1 of #215): all seven `OPERATOR_OPS` (the five older ones, `drop_debit` and `settle_legacy_call`) need C3-0b's `one_shot` capability, so `--serve`, `managed_service` and every other in-process service answer `operator_op_not_served` before reading anything ([3.5](#35-operator-recovery));
  - the one-shot `drop_debit{key, marker, event_id}` (`runtime/subscription_reconcile.py`). `marker` is `dd_` plus 32 hex characters of the SHA-256 of `[ledger identity, key, units, at]`, listed by `status` in `ledger.reconcile.drop_debit{debits: [{key, units, at, marker}], more}`. Refusals: `invalid_request`, `debit_not_pending`, `marker_stale`, `event_conflict`, `operator_op_not_served`;
  - the one-shot `settle_legacy_call` ([4.4](#44-settle-outstanding-legacy-work)), in `runtime/ledger_versions.py`;
  - `status.ledger.legacy_outstanding{empty, calls{count, items, more}, legacy_blocks{count, ids, more}, ready_handoffs}`; `empty` ignores ready handoffs.
- **Tests:**
  - v4 code refuses a v5 ledger;
  - a `--serve` `prepare` with a qualifying window leaves the debit;
  - `drop_debit` with a stale marker is refused;
  - `settle_legacy_call` for each state and evidence kind: zero cost is `evidence_invalid`, `over_bound` is refused, 15 racing processes apply one effect, and `--serve` refuses it;
  - the capacity worst case is recomputed.
- **Markers:** none.
- **Shared core:** no. **Size:** M (about 400 lines with tests).

**C3-1 — Launcher core, collector and route manifest.** No merge dependency. Phase W. **Merged (#213), package only; follow-ups in C3-1b below.** It reads C3-0b's `routing_settings.managed_project` when present, always with the service's `settings_path`, and treats its absence as "not managed", which fails closed; its tests patch that predicate.
- **Owns:**
  - New code: `runtime/routing_launch.py` and `runtime/routing_observations.py`:
    - preflight, request builders and its refusal codes;
    - the entitlement record, recovery hints and `readiness()`;
    - `route_identity`, `assignment_scope` and `unplanned_job` (the minimal canonical job for explicit, unplanned delegation such as a proposal or worker: role, scope reference, objective digest, conservative profile floors);
    - `managed_self_check`;
    - `collect` and the [source seam](#33-observation-collector).
  - New data: `runtime/routing_routes.json`, created from the capability matrix. Its first content waits on user decision 5; until then it lists no qualified route, so the activation gate fails closed.
  - New tests: `tests/test_routing_launch.py`, `tests/test_routing_observations.py`.
  - In `tests/test_smart_routing_features.py`: the `managed_project_fixture` body and its sentinel test ([6.1](#61-shared-file-protocol)).
- **Does:** [3.1](#31-what-a-launcher-must-do) steps 1–6, the collector and the entitlement rule, as host-neutral functions. Hosts plug in catalog, dispatch-transport and accounting callbacks. As merged:
  - `routing_launch.launch(service, *, host, assignment, job, catalog, transport, ...)` has no `dollar` flag: when the admitted billing class is a dollar class, it runs the dollar preflight on a fresh `status` before `dispatch`, and any refusal records `not_dispatched`;
  - a transport failure records `uncertain` and returns the operator op's name; there is no fallback and no second dispatch;
  - `managed_self_check` runs only in its own child of `sys.executable` (8 s, its own process group); the body refuses with `self_check_not_in_child` anywhere else.
- **Tests:**
  - every preflight item is refused;
  - a planned job without `workflow` is refused;
  - the collector reads no secret (credential resolver patched to fail) and makes no network call;
  - an unknown manifest route is excluded, and a `not_entitled` route stays excluded until cleared;
  - `readiness()` reports enabled and ready separately, with no inference;
  - **(round 5)** the collector derives `integration_enabled` from the real `openrouter_settings.status` and `funding_verified` from the ledger (organization enabled, balance at most 900 s old), never from the source. With a real enabled OpenRouter settings store and a real ledger, both are true. With the setting disabled, the route missing, the organization disabled or the balance stale, the gateway route is excluded with its reason;
  - **(round 5)** the collector never takes the `admission` lock. With `openrouter_settings.admission` held for the checkout in another handle, `collect` returns without the gateway route, with reason `openrouter_operation_busy` and `seen.openrouter_revision: null`, and without retrying. A spy shows it never calls `admission`;
  - `collect` returns the [return shape](#collector-return-shape): with a readable OpenRouter store, `seen.openrouter_revision` equals `status()['revision']`, and `seen` contains no other content;
  - the four managed-fixture contracts, run with plain `python3 -I -B -m unittest` in a subprocess with `HOME` set to a fresh empty directory and no `test_sharded` wrapper, leave `HOME` empty ([6.4](#64-c2-marker-map-and-pr-197-amendments) A4 rule 3).
- **Markers:** none. Replacing the sentinel body is what lets C3-2, C3-4a and C3-4b turn their markers into passes. C3-1 must keep `test_enablement_is_reported_separately_from_readiness` and the other C.2a and C.2e contracts passing.
- **Shared core:** no. **Size:** L (about 650 lines with tests).

**C3-1b — Launcher-core follow-ups.** Depends on C3-1 and C3-3; blocks C3-4b. Phase W. **Merged (#220), package only.** Later slices' reviews found four gaps in C3-1, fixed in C3-1's files plus the files the coordinator assigned to it:
- **Shim harness naming** for the `opencode` and `openrouter_worker` hosts ([3.3](#33-observation-collector), `route_identity`). Before, both used harness `opencode`, so no admission could pass `routing.attach`. C3-3's launcher now takes its identities and `choice` from `routing_launch` instead of its own helpers.
- **Gateway credential** derived from the cached `openrouter_settings.status` report ([3.3](#33-observation-collector), credentials row).
- **Endpoint uniqueness** and the **window unit** ([route manifest shape](#route-manifest-shape)). C3-3's launcher always stores `unit`, and C3-2's self-check sandbox declares `window_unit`.
- **Owns:** C3-1's files, plus `runtime/opencode_launcher.py`, `tests/test_opencode_launcher.py` and the two sandbox lines in `runtime/delegation_routing.py` (§6.1 sequence). A node test compares `route_identity` with the shim's `harnessFor` and fails, not skips, when node is missing.

**C3-2 — Native delegation and review wiring.** Depends on C3-0b and C3-1. Phase W. **Merged (#217), package only.**
- **Owns:**
  - New: `runtime/delegation_routing.py` (with `managed_self_check`), `tests/test_delegation_routing.py`.
  - Line-neutral hooks: `runtime/delegation.py` (`_prepare_native`, `_discovery`, `prepare_assignment`, `prepare_continuation`, `consume_continuation`, `reconcile_assignment`, `_routing_job_view`) and `runtime/evidence.py` (`prepare_review`). As merged, `consume_continuation` runs routing `dispatch` at the continuation's last pre-send step, and `_routing_job_view` keeps a bound assignment linkable (an S3.5 bug left a bound review always `ownership_conflict`).
  - The activation precondition in `scripts/routing.py` ([6.1](#61-shared-file-protocol), [4.2](#42-activation-gate)).
  - `runtime/workstreams.py` (`consume_dispatch` calls `dispatch`).
  - `tests/fixture_delegation.py`, only if it changes.
  - **Not** `scripts/workflow.py`: the route travels inside delegation's reply dict, which `workflow.py` already prints unchanged.
- **Does:** for a managed project:
  - **Every selection mode goes through `prepare` (G1).** `explicit` (as in `tests/fixture_delegation.py` 96–97 and the native C.2 contracts) and `inherit` both become `assignment.choice = routing_launch.choice('codex_native', 'subscription', model=…, effort=…)`, taken from `selection` or `selection.inheritance`. That is the four `ROUTE_IDENTITY` fields plus `funding`, as `_valid_assignment` requires. The user's choice stays a hard constraint: `prepare` admits that route or answers **`explicit_choice_unavailable`** (`runtime/routing.py` 596), and never substitutes. The new mode `routed` has no model, and lets routing choose.
  - **Order (G4).** `prepare` runs **after** delegation's reservation transaction (`_reservation_write`, 1799) commits, through `scripts.routing.managed_service(repo, scope=scope)` in-process, with `workflow{client, cwd, session_id, task_id, job_id, attempt_id}` and `job = delegation.routing_job(scope, locator)` (3270).
  - **Outcomes (G4).**
    - **admitted:** for `explicit` and `inherit`, `_discovery` (1461, called at 2106) has already validated the model and effort against the catalog, **before** the reservation. As merged, C3-2 still checks the admitted strategy against the observed native catalog and the entitlement rule for every mode, because for `routed` the admitted model is known only now. On a mismatch (`catalog_mismatch`, or `dollar_route_needs_enforced_host`), `runtime/delegation_routing.py:release`, in this order:
      1. records routing `not_dispatched` (`event_id` `<reason>:<attempt_id>`), which closes the admitted hold, before anything else (fail closed);
      2. settles the committed assignment through the **existing** `reconcile_assignment`, with a trusted `launch_state: not-started` proof written owner-only to a temporary file. `reconcile_assignment` already accepted a never-launched assignment with such a proof, so no new reconcile path was needed. The settled outcome is `cancelled`, because delegation's record validator accepts only `completed`, `failed` or `cancelled` (#217);
      3. replies `launch_admitted: false` with `routing{state: 'blocked', reason}` and the release.

      Release is automatic only on the call that committed the assignment. A replay that meets a mismatch records `not_dispatched` and leaves the assignment `prepared` for the coordinator's reconcile, because it cannot prove no earlier launch happened. Outside Workstreams, routing `dispatch` then runs as the last pre-spawn step; the token is dropped unread (advisory host). The reply is `_reply(record, admitted=True)` (1378), so `launch_admitted` (1386) is true and the reply carries `route{model, effort, attempt_id}`.
    - **pending or blocked:** the committed assignment stays `prepared` and is not launched. The reply has `launch_admitted: false` and `routing{state, reason}`. A retry is the same `delegation prepare` request (same `dispatch_id`). Its replay re-runs routing `prepare`, which is idempotent by `attempt_id`, and grants the one launch permit only on the call that sees `admitted`. Release is explicit, through delegation's existing reconcile path, which records `not_dispatched` for any admitted hold.
    - **`workflow_changed`:** `D6Workflow.view` (`runtime/routing_gates.py` 225) raises `WorkflowChanged`, which the service answers as `blocked/workflow_changed` (`scripts/routing.py` 360–361). C3-2 retries once at once; if it repeats, the outcome is the pending/blocked case above with reason `workflow_changed`.
  - **Single record.** The routing admission is the only record of the requested model. `binding.requested_model` stays null in every mode of a managed project. The read-only accessor is `delegation_routing.admitted_route(scope, attempt_id)`; wiring `observe` to compare observed settings with it is a follow-up (#217), because today's observer compares nothing with `requested_model`.
  - **Continuations and reviews.** Continuations close the child's finished turn's hold and get a fresh admission (`<attempt>.c<n>`) on the child's own admitted route. Reviewers get the independence constraint: `excluded_models` are the models admitted to the task's writers, read from delegation records and the routing ledger, never from the request. Codex is an advisory host.
  - **The managed boundary (coordinator decisions, review S1 of #217).** The review reproduced a double launch: a permit granted while the project was unmanaged, then a replay after it became managed. As merged:
    - whether the project is managed is read **once per call** (`runtime/delegation_routing.py`, the `_MANAGED` `ContextVar`), and every hook of that call sees the same answer;
    - a replay of an assignment committed before the project became managed (its `binding.requested_model` is set and it has no routing admission) answers `legacy_route_retired` and gets no permit;
    - a project that becomes managed during an unmanaged call releases the new assignment (`managed_during_prepare`) with no permit;
    - the [activation precondition](#42-activation-gate) refuses to add a project with an open, unrouted native assignment, and re-checks after the write (#222);
    - `settled()` closes the routing holds named in the record, whatever the current managed state, so removing a project after `prepare` still closes its hold.
  - **Settlement.** `reconcile_assignment` closes a settled managed assignment's routing holds: `complete` if dispatched, `not_dispatched` if only admitted. An unreadable routing ledger never undoes a committed reconciliation; it is reported as `routing{state: 'unavailable'}`.
- **Tests:**
  - one task graph;
  - an unchanged continuation reuses its profile and child but gets a new admission;
  - resource and funding gates bind the same job revision;
  - a dependency reopened after `prepare` sends nothing;
  - an advisory host never gets a dollar route;
  - an unmanaged project keeps today's `selection` behaviour;
  - pending, then admitted on a replay, grants exactly one permit;
  - `explicit` and `inherit` never get a substituted route;
  - **(round 5)** a `routed` catalog mismatch after the reservation commit releases the never-launched assignment through `reconcile_assignment`. The reply is `launch_admitted: false` with `catalog_mismatch`, the routing hold is closed (`not_dispatched`), and the assignment's claims and resource units are free for the next `prepare`.
  - **(#217)** the managed-boundary rules above, including the reproduced double launch, which is now refused with no routing call.
- **Markers:** `test_native_worker_admission_calls_routing_prepare_once`, `test_native_worker_admission_is_bound_to_its_workflow_job`.
- **Shared core:** **yes** (`runtime/delegation.py`, `runtime/evidence.py`), so the full suite runs. **Size:** L.

**C3-3 — OpenCode production launcher.** Depends on C3-0a and C3-1 (and so on C3-0b). Phase W. **Merged (#218), package only;** it has not run on OpenCode.
- **Owns:**
  - New: `runtime/opencode_launcher.py` (with `managed_self_check`), `scripts/opencode_launch.py` (JSON CLI), `tests/test_opencode_launcher.py` (a fake server, and the real `routing.py` on temporary stores).
  - The qualification harness: `tests/qualification/canary.py`, `tests/qualification/pilot_env.py`, `tests/qualification/server.py` (372), `tests/qualification/doubles.py` if it changes, and their tests `tests/test_qualification_canary.py` (239, 546) and `tests/test_qualification_harness.py`. In canary mode on a managed project the harness sends no observations ([3.3](#33-observation-collector)). This merges before the S3.7 canary.
  - The launcher section of `clients/opencode.md` (second owner, after C3-0a).
- **Does:**
  - the full [handshake](../../../clients/opencode.md#handshake) and [wiring](../../../clients/opencode.md#usage-accounting-and-resync), through `managed_service`;
  - refuses with `profile_not_enforced`, `host_unhealthy` or `api_funding_unqualified` when they apply;
  - never calls `session.command`;
  - starts its own `opencode serve` with an **allowlisted environment** (coordinator decision, review S1 of #218), never the caller's environment minus a denylist: only `HOME`, `USER`, `LANG`, `TMPDIR` and `XDG_DATA_HOME` are copied when set, `PATH` is fixed, and the launcher adds `OPENCODE_DISABLE_AUTOUPDATE`, `OPENCODE_DISABLE_MODELS_FETCH`, `OPENCODE_DISABLE_PROJECT_CONFIG` and a random in-memory `OPENCODE_SERVER_PASSWORD` (`runtime/opencode_launcher.py:host_environment`, `start_server`);
  - passes an **inline config** (`OPENCODE_CONFIG_CONTENT`, `opencode_launcher.server_config`): the routing shim as the only plugin, pinned to the managed service's stores with both store directories in `protected_paths`, `mcp: {}`, `formatter: false`, `lsp: false`, and the worker profile's rules as `permissions` (which deny `external_directory`);
  - **handles interrupts** so no hold is left plainly open: the command turns SIGTERM and SIGHUP into an exception, and the first signal switches SIGTERM, SIGHUP and Ctrl-C off so nothing interrupts the cleanup. After a confirmed `dispatch` it records `uncertain`; before that it records `not_dispatched`, and if the ledger answers `hold_state_forbidden` the dispatch committed, so it records `uncertain`. It always detaches, stops its server and answers `interrupted` (exit 1);
  - writes rate-limit observations with their [unit](#route-manifest-shape) and `not_entitled` marks ([3.4](#34-listed-is-not-entitled)).

  The step order, the worker profile and the server are defined once in [clients/opencode.md](../../../clients/opencode.md#managed-launcher). Its pre-canary requirement and the live probes this slice could not run are listed on the plan's [pre-canary requirements](../plans/2026-11-08-smart-routing.md#pre-canary-requirements).
- **Tests:**
  - each refusal happens before any session is created;
  - the token never appears in output or files (scan);
  - a lost acknowledgment records `uncertain`;
  - `descendants` is called before `detach`;
  - `shim_descendants: None` makes an API cost unknown;
  - a static check that no `session.command` call exists.
- **Markers:** none. It keeps `test_routing_success_grants_no_delivery_authority` passing.
- **Shared core:** no. **Size:** L. Live observation is separate and waits on the user.

**C3-4a — External choice and OpenRouter proposals.** Depends on C3-0b and C3-1. Phase W. **Merged (#216), package only.**
- **Owns:**
  - Code: `runtime/external_delegation.py` (adds `constraints(...)`; `select` stays until C3-7); `scripts/openrouter_proposals.py` (with `--managed-self-check`).
  - Docs: `clients/external-delegation.md` (the X0 text, with a managed-project rule); `skills/openrouter-proposals/references/procedure.md` (130) and `skills/openrouter-proposals/SKILL.md` (15).
  - Tests: `tests/test_external_delegation.py`, `tests/test_openrouter_proposals.py`, `tests/openrouter_fixtures.py` (first owner).
- **Does:** for a managed project:
  - `begin` obtains one routing admission on the `gateway` route through `managed_service`, with `job = unplanned_job(...)`, role `researcher` and no `workflow`. Every local store refusal and the permission, destination, source and packet checks run first; each `begin` gets a fresh routing `attempt_id`;
  - `dispatch` reruns the dollar preflight (`preflight(dollar=True)`) immediately before the routing `dispatch`, checks store capacity before it, and runs an in-process `check` under the store lock before the packet is returned (`scripts/openrouter_proposals.py:_authorize`, #216). A managed project never dispatches an attempt begun outside routing (`legacy_route_retired`);
  - `record` closes the hold: `not_dispatched` if never dispatched, otherwise `complete` under the money rule below;
  - `external_delegation.check` and `native_review` stay, and `external_delegation.constraints(...)` turns the user's permitted models and efforts into routing constraints (X0);
  - `triage` stays advisory and chooses nothing.
- **Money rule (coordinator decision, review S1 of #216).** The helper cannot verify a provider receipt, so a self-reported cost never lowers the charge. `record` with `usage` is sent as one `source: estimate` request whose cost is `max(reported total, hold reservation)`, so **the hold is charged its full reservation**, the same floor as `settle_legacy_call` and `resolve_cost_unknown`. A reported cost above the reservation becomes routing's unconfirmed overrun (the organization stays blocked until the operator's `resolve_overrun`). Missing usage, or an unknown cost, stays `cost_unknown`. The procedure tells callers to send `source: "estimate"`, never `provider_receipt`. Re-reading the receipt from OpenRouter to lower the charge needs the live API and is a post-release follow-up.
- **Crash safety (#216).** Both steps write ahead to the proposal store:
  - `begin` writes its routing attempt id (`routing_pending`) **before** `prepare`; after the 120 s `LEASE`, the next `begin` closes that hold with `not_dispatched`. A `begin` whose own entry recovery already closed refuses with `routing_reservation_lost`;
  - `dispatch` writes `routing_state: dispatching` on the row **before** the routing `dispatch`. If the packet is not returned, the row gets the outcome `routed_unsent` and only its own hold is settled: released while admitted, otherwise `complete` with no usage (`cost_unknown`), because the ledger never accepts `not_dispatched` after a dispatch. A row left `dispatching` by a dead process is settled under the store lock by the next `begin`, `dispatch` or `record`.
- **Tests:**
  - OpenRouter is a candidate only with its own activation and funding;
  - a direct API grant never funds it;
  - proposals gain no file or tool permission;
  - an explicit choice narrows the candidates and never widens them;
  - **(round 5)** routing `prepare` runs before `settings.admission` (a call-order spy), and a settings change between them fails closed with `openrouter_preparation_stale` and closes the hold with `not_dispatched`.
- **Order (round 5).** Routing `prepare` comes **before** `settings.admission` (`scripts/openrouter_proposals.py:324`), and admission gets `revision=seen.openrouter_revision` ([3.3](#openrouter-lock-and-revision)).
- **Markers:** `test_proposal_begin_obtains_one_routing_admission`.
- **Shared core:** no. **Size:** M.

**C3-4b — OpenRouter workers on the managed launcher.** Depends on C3-3, C3-4a and C3-1b (the shim harness naming). Phase W. **Merged (#225), package only.**
- **Owns:** `scripts/openrouter_workers.py` (with `--managed-self-check`), `skills/openrouter-workers/references/procedure.md`, `tests/test_openrouter_workers.py`, `tests/openrouter_fixtures.py` (second owner). As merged, the coordinator approved two splits by responsibility: the run-state code (write-ahead, recovery, read-only host, service wrapper) moved to `runtime/openrouter_worker_routing.py`, and the managed tests to `tests/test_openrouter_workers_managed.py`.
- **Does:** for a managed project:
  - `prepare` obtains one routing admission on the `gateway` route through `managed_service` before writing the run;
  - workers launch through `opencode_launcher` with the read-only profile from `configuration`;
  - the V1 `opencode run --pure` launch refuses with `legacy_route_retired`.
- **Tests:**
  - the V1 path is refused for a managed project;
  - the read-only profile is kept;
  - a V1 qualification record does not authorize a V2 editing profile;
  - **(round 5)** `prepare` runs before `settings.admission`, and a stale revision fails closed with `openrouter_preparation_stale`.
- **Order (round 5).** As in C3-4a: routing `prepare` before `settings.admission` (`scripts/openrouter_workers.py:126`), with `revision=seen.openrouter_revision`.
- **As merged (#225).** Every local refusal runs before `prepare`, and a write-ahead attempt entry is written before it. Without a verified receipt the hold is charged its full reservation (C3-4a's money rule), and a missing cost stays `cost_unknown`. The dollar preflight runs at `prepare` and again at dispatch. Every exit before dispatch closes the hold and returns no packet. The next managed `prepare` or `execute` recovers unlocked write-ahead entries and started runs whose hold is still open. The V1 path's first statement refuses a managed project.
- **Known gaps (#225).** All fail closed today, because the launcher refuses API-funded OpenCode routes (`api_funding_unqualified`) until [user decision 4](#71-for-the-user). The two package gaps are fixed (#229, #239), package only; the live checks they leave are on the plan's [pre-canary list](../plans/2026-11-08-smart-routing.md#pre-canary-requirements):
  - fixed (#229): the OpenCode adapter's accounting rejected OpenRouter `vendor/model` ids, so such a send ended uncertain. The Python adapter, `runtime/opencode_accounting.py` and the TypeScript shim now share one `MODEL_ID` rule: one `NAME`, or `vendor/model` with exactly one `/` and both parts in the `NAME` charset, at most 128 characters; provider and variant stay single names;
  - fixed (#229, #239): the launcher's server config had no per-launch endpoint pin, output cap or step count. `launch_options` validates them and `start_server(options=…)` writes them into that server's inline config in OpenCode's V2 keys; a host without exactly those options is refused before `prepare`, and a dispatch for another admitted model is refused (`launch_options_mismatch`) and closes the hold. Since #239 the step count is set on every V2 built-in agent, and a launch with options reads `GET /api/agent` once before `prepare` and refuses with `agent_steps_uncapped` unless the one shared rule `opencode_launcher.uncapped_agents(listing, config_agents, steps)` finds every listed agent capped; the gateway qualification harness uses the same rule. A launch without options reads no listing. The keys, the listing rule and the cost (one loopback GET per launch with options) are defined in [clients/opencode.md](../../../clients/opencode.md#per-launch-options);
  - open (live): the reply's text format (`content[].type == 'text'`);
  - open (live): OpenCode's precedence between server-level and session-level permissions.

  #229 also releases a worker run that was prepared but never executed once its admission is older than `routing.ADMISSION_TTL` (under the run's execution lock), skips live write-ahead entries in recovery, extends the worker token scan to the run folders, and splits the managed path out of `scripts/openrouter_workers.py` into `runtime/openrouter_worker_managed.py` (996 → 741 lines). `API_FUNDING_QUALIFIED` stays `False`.
- **Markers:** `test_worker_prepare_obtains_one_routing_admission` (removed in #225).
- **Shared core:** no. **Size:** M.

**C3-5 — API Usage refuses managed projects.** Depends on C3-0b and C3-1. Phase W. **Merged (#214), package only.** The managed send path moved out of this slice ([4.3](#43-stop-legacy-admissions), coordinator decision).
- **Owns:**
  - Code: `runtime/usage_routing.py` (with `managed_self_check`), `scripts/usage_routing.py`, `runtime/usage_codex_host.py`, `runtime/usage_canary.py`, `runtime/usage_canary_host.py`, `scripts/usage_codex_host.js`, `scripts/usage_key_setup_host.py` (setup only; no change expected).
  - Docs: all of `skills/manage-usage-routing/`, including `references/delegation.md`, `first-canary.md` and `codex-host.md`.
  - Tests: `tests/test_usage_routing.py`, `tests/test_usage_live.py`, `tests/test_usage_codex_host.py`, `tests/test_usage_codex_bridge.py`.
- **Does:** for a managed project:
  - the `legacy_route_retired` refusal is the first action of every spending entry point ([4.3](#43-stop-legacy-admissions); [Appendix A.2](#a2-api-usage-entry-points)), with `Controller._run`, `_evaluate` and `request_handoff` as backstops;
  - the Manage API Usage procedure sets `api_usage_enabled` through the one-shot routing command alongside `enable` ([4.3](#43-stop-legacy-admissions));
  - `usage_routing.managed_self_check` (the gate's `api_usage` capability) runs in a child process and checks all 8 entries and 3 backstops against tripwires, and that an unmanaged checkout still passes.
- **Tests:**
  - a managed project never reaches `Ledger.begin` through any spending entry: `usage_codex_host.freeze`, `usage_codex_host.execute`, `usage_canary_host.execute`, `usage_canary.run`, `usage_routing._run_live`, `evaluate`, `handoff` and `claim_handoff`. In each test `Ledger.begin`, `usage_settings.controller`, `Controller.configure` and the credential-binding reader are patched to fail;
  - after `configure` and `enable` in a managed project, every spending entry above still refuses and `Ledger.begin` is never reached;
  - the CLI `run` still answers `live_execution_unavailable` (A2a);
  - the spec §10.4 step 4 test ([4.3](#43-stop-legacy-admissions));
  - the existing `live_admission_required`, `credential_unavailable` and OFF-never-resolves-a-credential tests still pass;
  - **(#214)** `refuse_managed` fails closed: when `routing_settings.managed_project` raises `ImportError`, `TypeError`, `ValueError` or `OSError`, every entry refuses.
- **Markers:** none. It keeps `test_api_usage_stays_off_until_explicitly_enabled` passing.
- **Shared core:** no. **Size:** M.

**C3-6 — Phase W instructions.** Depends on C3-2. It merges in phase W and is part of the activation gate, so a canary coordinator never gets instructions that contradict the code. **Merged (#221), package only** (instruction tests and review walkthroughs; no live coordinator run).
- **Owns:**
  - `policies/03-agent-delegation.md`: a `Managed projects` section. In a managed project, ask a launcher and never choose a model; guidance refresh feeds the strategy table; explicit choices are hard constraints.
  - `clients/delegation.md` (lines 3, 17, 55, 57), `clients/evidence-review-prepare.md` (lines 6, 8) and `clients/delegation-requests.md` (119–122: "select model and supported effort from the actual native catalog", the explicit and inherit modes; 159: the request example's `selection`). These are the B5 and I11/I12 hits ([Appendix A](#appendix-a--inventory-at-the-pinned-base)); C3-2's code needs none of them earlier.
  - All of `skills/review-my-project/`.
  - The `skills/workstreams/` references, where they describe worker selection.

  Entry points keep the 150-word limit.
- **Does:**
  - adds the managed-project rule once, in policy 03; the other files link to it;
  - in `skills/review-my-project/references/procedure.md`, adds **one operative sentence** (G5), a step and not a second definition of the rule. Exact text, P2-checked against the merged `NEGATION` regex (`tests/test_smart_routing_features.py:174`), which rejects not, no, never, without, instead of, avoid…, skip…, unless, except…, only if, outside, may, might, can, could and optional…:

    > In a managed project, prepare each delegated reviewer for the selected scope through `scripts/routing.py`, as [policy 03](../../../policies/03-agent-delegation.md#managed-projects) requires.

    C3-6 creates the `Managed projects` section that the link targets;
  - leaves the unmanaged-project text unchanged until C3-7;
  - covers C1 (Claude Code native subagents) through policy 03, as two rules decided by the coordinator (#221): an **activation rule**, under which a project whose coordinator delegates through a host with no managed launcher is not added to `managed_projects` until [user decision 2](#71-for-the-user) and keeps the unmanaged instructions; and a **hold** for such a coordinator in a project that is already managed, which spawns no worker or reviewer itself, holds the dependent delegation, including required independent review (never replaced by self-review), and reports it. So policy 03's "required independent review remains available" and policy 06 stay true;
  - gives each non-admitted native reply exactly one action, matching `runtime/delegation_routing.py` ([policy 03](../../../policies/03-agent-delegation.md#managed-projects)). The code tags a replayed launch-check or `dispatch` refusal and `legacy_route_retired` with `routing.reconcile_required: true` (#222, #224), and policy 03 names that field.
- **Tests:** `tests.test_prepared_instructions` and the affected instruction tests; walkthroughs for Workstreams, API Usage, OpenRouter, Council and project review, reported separately.
- **Markers:** `test_project_review_routes_delegated_reviewers_through_the_routing_command`.
- **Shared core:** no, but instruction changes reach many tests, so run the affected modules. **Size:** M.

**C3-7 — Retirement.** Depends on all of the above, the C.1 result and the user's cutover decision. Phase R. It is the second (or third) owner of every file it edits.
- **Owns (in sequence):**
  - The managed-project branches in the C3-2 to C3-5 files.
  - `runtime/external_delegation.py:select`/`automatic_allowed` and their tests.
  - `Ledger.begin` and the S1-only helpers in `runtime/usage_budget.py`, and `tests/test_usage_budget.py`.
  - The A1–A4 entry points, and the spending actions in `scripts/usage_routing.py`'s `choices` (24).
  - The V1 workers launch.
  - `managed_projects`, the activation gate, the self-checks and caller-supplied `observations` outside `--test-inputs`, in `runtime/routing_settings.py` and `scripts/routing.py`.
  - The phase W conditional text in the C3-4a, C3-5 and C3-6 instruction files.
  - The obsolete tests, plus a new `tests/test_legacy_selectors_retired.py`.
- **Does:**
  - every checkout uses the managed path, with the G1 mode mapping;
  - the remaining `ready` handoffs become `retired`;
  - `legacy_outstanding` must be empty before merge.
- **Tests:**
  - legacy symbols cannot be imported;
  - the removed CLI actions exit 2;
  - no `NEEDS_C3` marker remains;
  - full suite and `validate.py` pass.
- **Markers:** `test_external_delegation_keeps_no_caller_owned_selector`, `test_usage_routing_cli_no_longer_selects_routes`.
- **Shared core:** **yes**, so the full suite runs. **Size:** M (mostly deletion).

### 6.4 C.2 marker map and PR #197 amendments

PR #197 is **merged** (`main` `bd5cde6`). Every amendment this design asked for is applied there:
- **`NEEDS_C3(slice_id)` and `SLICES`,** including `C3-0a` and `C3-0b`. Only an `AssertionError` (or a skip) counts as an expected failure. A crash goes to `_NON_ASSERTION` and a pass to `_PASSED`, and `tearDownModule` then fails the module ([6.1](#61-shared-file-protocol) unexpected-pass rule). A broken precondition raises `FixtureError`, which is deliberately not an assertion.
- **Pinned siblings.** `PinnedGates` (`project='/work/repo'`) holds the four sibling contracts. `C2bProjectPin` (484–491) builds its **own** unpinned `Service`, so C3-0b's `GateBase.setUp` pin does not affect it.
- **The fixture skeleton.** `managed_project_fixture(test, checkout, routes=())` (98), with the C3-1 owner comments and an `AssertionError` sentinel.
- **Route entries.** `fixture_route(route, billing_class)` with ids `native:<model>#<effort>` and `openrouter:<model>@<provider>`.
- **The merged review check.** `routes_reviewers_through_routing` with the `NEGATION` regex (174–183).

No further #197 change is needed. All marker lines below are on `main`.

**Route-id mapping (G2; defined once, here).** C3-1 maps ids inside the fixture body; test bodies and `fixture_route` do not change.

| Id prefix and billing class | Host(s) for `route_identity` | Provider | Harness | Account kind and account | Funding set up by the fixture |
|---|---|---|---|---|---|
| `native:<model>#<effort>` (split on the last `#`), `included_subscription` | `codex_native` | `openai` | `codex-native` | subscription, `fixture-codex-subscription` | one fresh, fixed `5h` window |
| `openrouter:<model>@<provider>` (split on the last `@`), `dollar` or `gateway` | `openrouter_proposal` **and** `openrouter_worker`, each for every effort the hosts accept (`none`, `low`, `medium`, `high`, `max`): the id carries neither host nor effort, so the test manifest lists all of them | `openrouter`; the id's `<provider>` is kept as the pinned endpoint | `openrouter-proposal`, `opencode provider openrouter` (the shim's name, [3.3](#33-observation-collector)) | gateway, `fixture-openrouter` | ledger organization, balance, credential (rule 4) |
| any other combination (`native:` with `dollar` or `gateway`, `openrouter:` with `included_subscription`, an unknown prefix) | — | — | — | — | raises `FixtureError` |

`dollar` (as #197 writes it) or `gateway` with an `openrouter:` id is the `gateway` billing class. The entries carry everything the manifest, C3-2's catalog check ([3.1](#31-what-a-launcher-must-do) step 6) and the `gateway` candidate checks need.

**A4. The body C3-1 writes must:**
1. **Check slices.** Raise `AssertionError` naming the missing slice if `runtime.routing_settings.managed_project` or `scripts.routing.managed_service` (C3-0b), or `runtime.routing_observations.default_source` or `runtime.routing_launch.route_identity` (C3-1), is absent. Never let an `AttributeError` escape. A malformed entry or an unmapped id raises `FixtureError`.
2. **Patch the project and source,** with cleanups registered on `test`:
   - `routing_settings.managed_project` answers true only for `checkout` (by realpath);
   - `routing_observations.default_source` returns a fixture source with the module's `CHOICE` route plus every mapped entry, each `status: qualified`, with the pool and account from the mapping table.
3. **Never touch real user state.** Before anything else, patch every default path the fixture or the code under test can reach to directories inside `test`'s temporary directory, whether or not a private `HOME` exists:
   - `scripts.routing.default_settings` and `default_ledger` (582, 586);
   - `runtime.usage_settings.directory` (16);
   - `runtime.openrouter_settings.BASIC_STORAGE` (15);
   - the collector's observation-cache and entitlement paths, which C3-1 derives from the settings path it is given, never from `HOME`.

   C3-1's HOME test proves this.
4. **Fund each billing class** on those temporary stores. Use the routing command's `set` ops where one exists and the `Ledger` API otherwise:
   - every case: `authority_required=true`, and `worker_choices` (`implementer` and `reviewer`: the subscription routes);
   - `included_subscription`: the pool has one fresh, explicitly fixed window (`id` `5h`, `limit` 1000, `used` 100, `length` 18000, `reset_at` = now + 3600, `rolling: False`, observed at `now`);
   - `gateway`:
     - one ledger organization (`set organization{id: 'fixture-openrouter', enabled: true, policy}` with a policy like `tests/fixture_routing_funding.policy()`), then `Ledger.observe('fixture-openrouter', 10_000_000, now, issued=10_000_000)`, i.e. $10 of credit headroom;
     - the pool carries `billing_class: gateway` and one window (`id` `credit`, `limit` 10^12, `used` 0, `organization`, `organization_revision`, fresh, `rolling: False`);
     - `credential(route, scope)` returns `{state: 'ready', scope: scope, ref: 'fixture-openrouter'}`;
     - OpenRouter activation (`integration_enabled`) comes from the real `openrouter_settings` state that the test's own `configure(...)` enabled;
     - `api_usage_enabled` stays false: a gateway route needs no API Usage (RF1).
5. **Stay offline.** Make no network, model, host or secret-store call.
6. **Yield** `{routes, settings, ledger}`.

Before C3-1 merges, the sentinel raises `AssertionError`. After it merges, the four managed contracts fail by assertion only (no routing admission yet) until C3-2, C3-4a and C3-4b each make theirs true and remove only their own marker.

| Test (marker line on `main`) | Slice | What makes it true |
|---|---|---|
| `test_a_workflow_bound_prepare_requires_the_project_pin` (495) | C3-0b | The routing service refuses an unpinned workflow-bound `prepare` with `project_required`. |
| `test_native_worker_admission_calls_routing_prepare_once` (527) | C3-2 | Managed-project native `prepare` calls `managed_service(...)._prepare` in-process once, so `runtime.routing.admit` runs once. |
| `test_native_worker_admission_is_bound_to_its_workflow_job` (535) | C3-2 | That call carries `workflow` for the planned job (`job_id` `edit`) and the delegation `attempt_id`. |
| `test_proposal_begin_obtains_one_routing_admission` (704) | C3-4a | Managed-project proposal `begin` obtains one admission on the `gateway` route. |
| `test_worker_prepare_obtains_one_routing_admission` (740) | C3-4b | Managed-project `openrouter_workers.prepare` obtains one admission before writing the run. |
| `test_project_review_routes_delegated_reviewers_through_the_routing_command` (805) | C3-6 | The review-my-project skill keeps one operative sentence naming `scripts/routing.py`, the delegated reviewer(s) and the selected scope, linking to policy 03. |
| `test_external_delegation_keeps_no_caller_owned_selector` (622) | C3-7 | `runtime.external_delegation.select` is deleted. |
| `test_usage_routing_cli_no_longer_selects_routes` (640) | C3-7 | `scripts/usage_routing.py evaluate` is a usage error (exit 2). |

[Appendix B](#appendix-b--contract-walk) walks each one step by step.

**Status at `4bc2641`.** Six markers are removed, each by its own slice: C3-0b (#212), both C3-2 contracts (#217), C3-4a (#216), C3-6 (#221) and C3-4b (#225). Two remain: the two C3-7 deletions.

## 7. Open decisions

### 7.1 For the user

1. **Cutover.** Whether and when phase R starts after C.1 passes, and whether more projects join `managed_projects` before cutover.
2. **Default ON scope (C.4).** Whether default ON at cutover covers advisory hosts (Codex and Claude Code native) for automatic subscription selection, or whether those hosts use only `worker_choices` until a pre-transmission hook exists. Recommendation: allow automatic subscription selection there, labelled `advisory`, because their funding is window-only. Since C3-6 (#221) this decision also decides **Claude Code projects** in phase W: a project whose coordinator delegates through a host with no managed launcher, today Claude Code native subagents, is not activated until this decision ([4.2](#42-activation-gate)).
3. **OpenRouter workers gap.** Accept that OpenRouter workers in managed projects are unavailable until C3-4b merges and the `gateway` route on OpenCode is qualified live. C3-4b is merged (#225), and its self-check passes; managed workers still answer `api_funding_unqualified` until decision 4.
4. **API-funded OpenCode qualification.** Run the S3.6 positive and negative descendants runs with real API costs; this lifts `api_funding_unqualified`.
5. **Route manifest.** Accept the route manifest's classifications, taken from the capability matrix, as the collector's source while observed billing for several routes still waits on you. Until then the manifest lists no qualified route, and no project can be activated. **Decided 2026-11-10:** "Use matrix, subscription only." The manifest PR (#227) lists subscription-window routes only, none qualified, and stays open until each route's demand is measured live (plan [pre-canary requirements](../plans/2026-11-08-smart-routing.md#pre-canary-requirements)).
6. **First window telemetry.** **Decided 2026-11-10 and built (#228):** the recommended `seed_window`, with the merged rules in [3.3](#seed-window). The recommendation as written: The collector learns a subscription window only from an attempt that has already settled, so the first managed launch on a route has no stored window (`window_unobserved`), and `prepare` stays `pending`. Something has to seed it; this is your decision. Recommendation: a one-shot operator op `seed_window{pool, window, used, limit, reset_at, length, event_id}`, with these limits:
   - subscription-window pools only; a dollar pool or an organization window is `invalid_request`;
   - refused (`fresh_observation_exists`) while a fresh host observation of that window exists;
   - bounds: `used`, `limit` and `length` are non-negative integers, `used <= limit`, `limit > 0` and `length > 0`; `reset_at` is an integer in the future and no later than `now + length`. Anything else is `invalid_request`;
   - one-shot only (`--serve` answers `operator_op_not_served`), idempotent by `event_id`;
   - recorded with `source: operator`, which `status` shows for that window while the seed is in use;
   - fresh for the usual 900 s, and replaced by the first real host observation, never the other way round.

   The alternative is a single admission per route at concurrency 1 that ignores utilization. I do not recommend it, because it bypasses the allocator's window check.

### 7.2 Decided here (agent-decidable; change by PR)

- Two phases, with a one-shot `managed_projects` list behind an activation gate, instead of a big-bang switch.
- New modules instead of raised line ceilings.
- Advisory hosts never get dollar routes.
- The routing admission is the only record of a routed worker's requested model.
- For managed projects, the service builds observations; request observations are refused.
- `settle_legacy_call` covers every open S1 state, and `drop_debit` is a new one-shot op. Nothing is released without spend except a provably unsent `reserved` call.
- `scripts/usage_routing.py` keeps its setup and status actions.
- Only the user clears entitlement marks.
- C3-2 does not edit `scripts/workflow.py`.
- The shared-file protocol ([6.1](#61-shared-file-protocol)) and the marker map and #197 amendments ([6.4](#64-c2-marker-map-and-pr-197-amendments)).

## 8. Revision history

- **1.0** (`bd01a22`): first draft.
- **1.8:** the pre-live slices at `91644e8`; each change is stated in the section that defines the rule:
  - **Lightweight principle (new section):** the user's binding rule of 10 November 2026, defined once in [Design principle: lightweight](#design-principle-lightweight).
  - **`seed_window` (§3.3, §3.5, §7.1, #228):** decision 6 decided and built; `limit` equals 1000, `length` equals the host's length for a window id named by a qualified subscription route's `demand`, a seed replaces only a stale host reading, a host reading always replaces a seed, the op creates the ledger, lazy imports in `scripts/routing.py`; eight operator ops.
  - **Codex window reader (§3.3, #231):** three methods, on demand only, one reader per pool with a bounded wait, a 300 s failure backoff, graceful stop, `--serve` never reads, the operator one-shot; the collector's one process and network exception.
  - **F4 (§6.3 C3-4b, #229):** `vendor/model` ids through one `MODEL_ID`; per-launch endpoint pin, output cap and step count; the worker fixes; two live gaps left.
  - **F5 (§6.3 C3-4b, #239):** steps on every V2 built-in agent and the shared `uncapped_agents` rule.
  - **Decision 5 (§7.1):** decided ("subscription only"); the manifest PR #227 stays open until demand is measured live.
- **1.7:** phase W's code is complete at `4bc2641`; each change is stated in the section that defines the rule:
  - **Host rule (§3.6, §4.2 step 3, #224):** code-enforced by `MANAGED_HOSTS` and `delegation_routing.enrolled_hosts`, before the other checks, with the refusals `advisory_host_unmanaged`, `not_enrolled` and `enrolment_unavailable`; only configured or active entries count; mixed projects may join; an OpenCode-coordinated project must be codex-enrolled.
  - **Re-check rollback (§4.2 step 4, #224):** a failed rollback write is still the refusal, with `rolled_back: false` and the failure chained; every refusal carries `invalidated_attempts`.
  - **C3-4b (§6.3, §7.1, #225):** merged, package only, with its splits, merged behaviour and known gaps; the activation gate now fails only on the route manifest.
  - **C3-6 (§6.3):** `legacy_route_retired` also carries `routing.reconcile_required` (#224), and policy 03 names the field.
  - **Markers (§6.4):** six removed, two remaining (C3-7).
- **1.6:** brought in line with the merged phase W slices and #222 at `6fe269c`; each change is stated in the section that defines the rule:
  - **Header and slices (§6.3):** each merged slice is marked with its PR and "package only"; C3-1b (#220) is added; C3-4b and C3-7 are not built.
  - **Collector (§3.3, #212, #213, #220):** `collect(project, assignment, *, settings_path, ledger_path, source=None)` with required store paths and never the defaults; the manifest is an object `{version: 1, window_unit, routes}`; `WINDOW_UNIT = 'utilization_thousandths'` with `unit` on stored windows; endpoint uniqueness for qualified routes; `route_identity` names the OpenCode hosts' harness exactly as the shim's `harnessFor`, which fixes v1.5.1's `opencode` harness for `openrouter_worker`; the gateway credential comes from the cached status report; missing telemetry is `window_unobserved`; `seen` is also echoed on a workflow-binding `blocked`.
  - **C3-0b (§1, §4.2, #212):** `managed_project(path, settings_path=None)` by file identity, with unreadable settings counting as managed; the gate's process-group kill.
  - **C3-0a (§3.5, §4.4, §4.6, #215):** all seven operator ops one-shot only, refused by every in-process service; debit clearing only one-shot or from a collector-built managed snapshot; every legacy settlement closes its run; converted runs are not legacy work.
  - **C3-2 (§4.2, §6.1, §6.3, #217):** managed read once per call; `legacy_route_retired` for pre-managed replays; `managed_during_prepare`; the activation precondition `open_unrouted_assignments`; the existing `reconcile_assignment` releases a mismatch as `cancelled`; `observe` wiring is a follow-up.
  - **C3-4a (§6.3, #216):** without a verified provider receipt the full reservation is charged (a receipt re-read is a post-release follow-up); write-ahead attempt and dispatch state; the dollar preflight at dispatch.
  - **C3-5 (§2, §4.3, §6.3, #214):** the managed API Usage send path moves to a post-release follow-up, so managed projects have no API Usage route in phase W; the procedure, not `enable`, sets `api_usage_enabled`.
  - **C3-3 (§5.3, §6.3, #218):** the allowlisted environment, the inline config, interrupt handling; its live probes are pre-canary requirements.
  - **C3-6 (§4.2, §6.3, §7.1, #221):** the Claude Code activation rule (instruction-only) and the hold in an already-managed project; decision 2 also decides Claude Code projects.
  - **Review follow-ups (§4.2, §6.3 C3-6, #222):** the post-write activation re-check with rollback; the real scope resolver; the replayed-refusal tag `routing.reconcile_required`.
  - **Rollback (§5.1, #211):** `scripts/routing_table.py rollback` swaps the previous table into place.
  - **Startup errors (§3.3, §5.2, #219):** deadline and busy reasons are distinct, and still fail closed.
  - **Markers (§6.4):** five removed, three remaining.
- **1.5.1:** pre-merge text fixes on `af6b0a7`: `status` takes a brief shared lock and can fail with `openrouter_operation_busy`, so the collector leaves the gateway route out with that reason, without retrying (C3-1 test rewritten); one collector return shape `{observations, seen}`, owned by C3-1 and echoed opaquely by C3-0b, with a test in each; `local_configuration == 'ready'`.
- **1.5:** round-5 should-fixes on `624d169` (nothing blocking; P1 reproduced from the spec text alone, and I11/I12 matched):
  - **OpenRouter lock.** A throwaway test showed `admission` holds a non-waiting lock in which even `status` fails. So the collector reads only `status` and returns `seen.openrouter_revision`; C3-4a and C3-4b run `prepare` before `settings.admission` and pass that revision, so a change in between fails closed with `openrouter_preparation_stale`. `funding_verified` stays a ledger check. B4 and B5 are re-ordered.
  - **Lines:** the unpinned `settle` refusal is at `scripts/routing.py` 448; the oversized-binding test is one changed line (905), reused at 909.
  - **`call_bound`** names all `Service` arguments.
  - **Appendix A** says slice workers must use its exact commands (`git grep` gives fewer hits).
  - **Unproven items are now tests in their slices' lists** (round-5 verdict): the collector's real derivation and the lock rule (C3-1); the never-launched reconcile after a catalog mismatch (C3-2); prepare before the lock and the stale revision (C3-4a, C3-4b). `worker_prepare` and C3-7's deletions are already enforced by their markers.
- **1.4:** the round-4 review of `71d0108`. Root-cause change: **validate by running**. Throwaway prototypes ran in a scratch worktree of `origin/main`, and Appendix B.0 records the commands and results.
  - **P1** (C3-0b `project_required` plus pins): 664 tests pass. The exact changed tests are now listed: `test_routing_gates.py` (118, 508, 533, 904–906) and `fixture_routing_accounting.py` (`call_bound`), plus the C3-0b marker. The earlier claims about 893, `test_routing_accounting_roots.py:274` and the other accounting files were wrong; those files are unchanged.
  - **P2** (fixture path): both native contracts and `proposal_begin` pass with the prototype fixture and wiring, and `HOME` stays empty under plain `unittest`. This found and fixed three gaps:
    - the workflow resolver for the fixture checkout: `managed_service(checkout, scope=…)`;
    - the `choice` shape: `funding` added;
    - the gateway `demand` source.
  - **P3** (self-check runner): `sys.executable` plus the package root works. `PATH` `python3` is Apple's 3.9.6 and fails.
  - **B1:** #197 is merged with C2bProjectPin's own unpinned service; §6.4 marks every amendment applied.
  - **B3:** `explicit_choice_unavailable` replaces the wrong `worker_choice_unavailable`.
  - **B5:** `clients/delegation-requests.md` goes to C3-6. New greps I11 and I12 cover selection instructions in `clients/`, `skills/` and `policies/`, and found no other unowned one.
  - **Should-fix:**
    - Appendix A is re-pinned to `bd5cde6` (137 hits), and the #192 rebase rule is dropped. `FIELDS` and `Service` were confirmed on `main`; `FIELDS['prepare']` gets optional `observations` (C3-0b);
    - the mapping table accepts `gateway`;
    - the C3-2 catalog-mismatch release.
  - **Nits:**
    - one line-number base;
    - `seed_window`: refused while a fresh host observation exists, with bounds on `used`, `limit`, `length` and `reset_at`.
  - The G5 sentence was checked against the merged `NEGATION` regex.
- **1.3:** the round-3 review of `7463fb5` and the root-cause diagnosis:
  - **Restructured.**
    - Appendix A: an inventory, with exact grep commands and their 109 hits at one pinned base (`94da5fb` plus #192 at `8d64c9f`), each changed hit with one owner, plus the API Usage entry-point table.
    - Appendix B: a step-by-step walk of the 8 markers in #197 at `60c191f`.
    - C3-0 split into C3-0b (routing service, no #192 dependency) and C3-0a (ledger, after #192).
  - **Round-3 items.**
    - B1: billing-classed route entries; the fixture funds `gateway` routes; `proposal_begin` and `worker_prepare` are walked end to end.
    - C3-0b owns the accounting fixture and tests, pinning only the workflow-bound admits; `settle` without `workflow` keeps working.
    - `usage_codex_host.freeze` and the bridge's actions are in the entry list.
    - C3-2 and C3-4a depend on C3-0b and C3-1.
    - The fixture patches every default path, with a test that `HOME` stays empty under plain `unittest`.
    - `seed_window` limits.
    - Nits: the self-check sandbox, a child-process timeout, an exact environment, the §6.1 parallel exception, the named harness tests.
  - **Diagnosis gaps.**
    - G1: every native selection mode goes through `prepare`, and an explicit choice is a hard constraint.
    - G2: one route-id mapping, from #197's `fixture_route` ids.
    - G3: the shared `managed_service` factory.
    - G4: delegation outcomes when routing is pending, blocked or `workflow_changed`.
    - G5: one operative sentence in the review skill.
    - G6: test-file owners.
    - G7: §6.4 rebased on #197 `60c191f`, with the unexpected-pass rule.
    - G8: `usage_key_setup_host.py` as setup only.
  - **API Usage setup actions** stay available in managed projects, by design.
- **1.2:** fixes from the round-2 review of `b760638`:
  - N1: PR #197's pin test moves to its own unpinned class, and its four siblings use a service pinned to `/work/repo`;
  - N2: a collector source seam, defined once, and the `managed_project_fixture` contract (body owned by C3-1, an `AssertionError` sentinel until then), with `NEEDS_C3` counting only assertions;
  - A2a corrected: the CLI `run` is already disabled;
  - API Usage refusals moved to the first action of every host entry point, with `Controller._run` as a backstop;
  - C3-0 owns the four existing tests its changes break;
  - C3-3 updates the qualification harness before the canary;
  - releasing a `reserved` call closes its run;
  - the activation gate runs behavioural self-checks;
  - line numbers re-verified at `a824819`;
  - new open decision 6 (first window telemetry), found while writing the fixture contract.
- **1.1:** fixes from the independent review of `bd01a22` (3 blocking findings, should-fixes and nits):
  - the eight real PR #197 markers mapped one to one to slices, with the `NEEDS_C3('<slice>')` convention and a managed-project test helper;
  - the canary and Codex host paths added to the inventory, with the legacy refusal moved into `Controller._run`;
  - one owner per file, plus the shared-file protocol;
  - C3-0 owns the `runtime/routing.py` debit-clearing change and the service-level `project_required`;
  - `managed_projects` made one-shot, behind an activation gate;
  - service-built observations;
  - `settle_legacy_call` extended to `reserved` and `dispatching` calls, and the "no settlement" statement corrected;
  - a phase W instruction slice (C3-6);
  - spec §10.4 step 4 restated, with its test;
  - X0, X1 and Claude Code native subagents reclassified;
  - the `reconcile` action added to retirement;
  - C3-1 creates the route manifest.

## Appendix A — Inventory at the pinned base

**Pinned base:** `origin/main` at `bd5cde6`, which contains #192 (S3.6c), #195, #196 and #197. This is the only line-number base in this document. The earlier base (`94da5fb` plus #192's diff) is retired: #192 has merged, so C3-0a builds on `main` with no rebase rule. If `main` moves before a slice starts, that slice re-runs the commands below and updates the cited lines in its own PR.

### A.1 Commands and owners

Run from the pinned base, with these helpers. Slice workers must use these exact commands: `git grep` (which skips untracked files and treats patterns differently) gives fewer hits, and the counts below would not match.

```bash
g() { grep -rnE --include='*.py' --include='*.js' --include='*.ts' --exclude-dir=fixtures --exclude-dir=node_modules "$@"; }
m() { grep -rnEi --include='*.md' "$@"; }
```

| # | Command | Hits | Hit (file:line) → owner |
|---|---|---|---|
| I1 | `g 'Service\(' runtime scripts tests \| grep -v 'class Service'` | 29 | `scripts/routing.py:635` (CLI `main`) → C3-0b. `tests/test_routing_gates.py:118, 508` (pinned to `/work/repo`) and `533` (pinned to the scope's root) → C3-0b, changed. `462, 497` (`dispatch` only), `668, 682, 863, 882` (already pinned) and `893` (`status` only) → C3-0b, unchanged. `tests/fixture_routing_accounting.py:75` → C3-0b, changed (adds `call_bound`). `tests/test_routing_accounting_roots.py:274` (`record` and `settle` only), `tests/test_routing_command.py:98, 525, 633, 700, 744` (no workflow-bound `prepare`) and `tests/test_routing_table.py:79` (#196; no workflow) → unchanged, no owner. `tests/test_routing_dispatch.py:222, 806, 880` and `tests/test_routing_subscription_reconcile.py:52` (workflow use is `settle`, allowed unpinned) → C3-0a. `tests/test_routing_accounting_blocks.py:234` (already pinned) → C3-0a. `tests/qualification/server.py:372` → C3-3. `tests/test_smart_routing_features.py:346, 382 (PinnedGates), 491 (C2bProjectPin's own unpinned service), 666` → no change (Lane D's file; slices only delete their marker lines, [6.1](#61-shared-file-protocol)). |
| I2 | `g 'STATE_VERSION' runtime scripts tests` | 11 | `runtime/usage_budget.py:28, 152, 242` (move to `ledger_versions.py`) → C3-0a. `tests/test_routing_reconcile.py:772`, `tests/test_routing_accounting_blocks.py:332, 336`, `tests/test_usage_budget.py:458, 498, 506, 648, 653` → C3-0a. |
| I3 | `g 'usage_settings\.(controller\|scope)\(\|[^_.]controller\((cwd\|plan\|checkout\|key\|root)' runtime scripts` | 17 | `runtime/usage_settings.py:112` (definition, unchanged) → C3-5. `runtime/usage_canary_host.py:166, 185` → C3-5 (refuse first). `runtime/usage_routing.py:39` (`status`), `55` (`enable`), `690` (`configure`), `697` (`disable`) and `713` (`reconcile` report) → C3-5 (allowed). `69, 71` (`_run_live`), `704` (`evaluate`), `720` (`handoff`) and `727` (`claim_handoff`) → C3-5 (refuse first). `runtime/usage_codex_host.py:121` (`freeze`) and `223` (`execute`) → C3-5 (refuse first); `186` (`_publish_generation`, reached only after `execute`) → C3-5 (covered). `scripts/usage_key_setup_host.py:96` → C3-5 (setup only, K1). |
| I4 | `g 'default_settings\|default_ledger' runtime scripts tests` | 13 | `scripts/routing.py:582, 586, 610, 611, 635` → C3-0b (`managed_service` reads 582 and 586 at call time). `scripts/routing_table.py:234` (#196) → unchanged, no owner; it follows `scripts/routing.py`'s defaults, which the C.2 fixture patches. `tests/test_routing_command.py:554` and `tests/test_smart_routing_features.py:340` → unchanged. `tests/test_qualification_canary.py:239, 546`, `tests/qualification/server.py:372, 373` and `tests/qualification/canary.py:107` → C3-3. |
| I5 | `grep -nE "\['freeze', 'execute'\]\|action == 'freeze'\|host\.(freeze\|execute)" scripts/usage_codex_host.js` | 5 | `scripts/usage_codex_host.js:5, 61, 62, 72, 74` → C3-5. No change: the refusals sit in the Python functions they call. |
| I6 | `grep -nE '^def ' runtime/usage_routing.py; grep -nE 'choices=' scripts/usage_routing.py` | 13 | `runtime/usage_routing.py:19 status, 47 enable, 76 quote, 106 profile_digest, 688 configure, 694 disable, 710 reconcile` → C3-5 (allowed). `60 run` → C3-7 (already disabled). `64 _run_live, 701 evaluate, 717 handoff, 724 claim_handoff` → C3-5 (refuse first). `scripts/usage_routing.py:24` (`choices`) → C3-7. |
| I7 | `g 'usage_routing\.(status\|enable\|configure\|disable\|run\|_run_live\|quote\|evaluate\|reconcile\|handoff\|claim_handoff)\b\|getattr\(usage_routing' runtime scripts` | 7 | `runtime/usage_canary.py:60, 75`, `runtime/usage_codex_host.py:107, 252` and `scripts/usage_routing.py:29, 40, 42` → C3-5. |
| I8 | `g '\.begin\(' runtime scripts \| grep -v 'def begin'` | 1 | `runtime/usage_routing.py:643` → C3-5 (backstop refusal), then C3-7 (removal). |
| I9 | `ls tests/fixture_*.py tests/openrouter_fixtures.py tests/qualification/server.py tests/qualification/doubles.py` | 15 | `fixture_delegation.py` → C3-2 (only if it changes; the P2 prototype needed no change). `fixture_routing_accounting.py` → C3-0b. `openrouter_fixtures.py` → C3-4a, then C3-4b. `qualification/server.py` and `qualification/doubles.py` → C3-3. `fixture_routing_funding.py` is read as a policy example and not changed. Not touched by C.3, so no owner: `fixture_canonical_birth.py`, `fixture_changes.py`, `fixture_evidence.py`, `fixture_native_behavior.py`, `fixture_native_reviews.py`, `fixture_opencode_accounting.py`, `fixture_package.py`, `fixture_staged_successor.py`, `fixture_worker_visibility.py`. |
| I10 | `g "'explicit'\|'inherit'" runtime/delegation.py tests/fixture_delegation.py` | 5 | `runtime/delegation.py:1486, 1487, 2113, 2114` → C3-2 (G1 mapping). `tests/fixture_delegation.py:97` → C3-2 (unchanged). |
| I11 | `m "(select\|choose\|pick)[a-z]* [a-z ]{0,30}(model\|effort\|worker settings\|reasoning)\|native catalog\|\binherit(ance)?\b.*(model\|effort)\|explicit model\|requested_model" clients skills policies` | 9 | Selection instructions. `policies/03-agent-delegation.md:13, 21` → C3-6. `clients/delegation.md:17, 57` → C3-6. `clients/evidence-review-prepare.md:8` → C3-6. `clients/delegation-requests.md:119, 120, 122` → C3-6 (B5: "Select model and supported effort from the actual native catalog", explicit and inherit modes). `skills/openrouter-workers/references/procedure.md:45` → C3-4b. |
| I12 | `m "worker selection\|native selection\|makes the selection\|selection source\|benchmarks guide selection\|\"selection\": \{\|selection/control" clients skills policies` | 12 | `clients/delegation.md:3, 55` → C3-6. `clients/delegation-requests.md:159` (the request example's `selection`) → C3-6. `skills/openrouter-workers/references/procedure.md:20, 32, 44` → C3-4b. `skills/manage-usage-routing/references/delegation.md:3, 53, 88` → C3-5. `skills/openrouter-proposals/references/procedure.md:130` and `skills/openrouter-proposals/SKILL.md:15` → C3-4a. `policies/03-agent-delegation.md:119` (escalation to a more capable worker: in a managed project, a new `prepare` with a higher profile floor) → C3-6. |

**Totals.** 137 hits.
- Every hit that C.3 changes has exactly one owner, or a named sequence of owners.
- Unchanged hits are marked "no owner". They are nine untouched fixtures, `scripts/routing_table.py:234` and the read-only test services.
- No changed hit is unowned.
- I11 and I12 are the selection-instruction greps that would have found B5 (`clients/delegation-requests.md`). Run across `clients/`, `skills/` and `policies/`, they found no other selection instruction. `clients/external-delegation.md`'s selection text (lines 11, 24) is matched by neither pattern but is already owned by C3-4a.

### A.2 API Usage entry points

Every path that can reach `Ledger.begin` (`runtime/usage_budget.py:689`, called at `runtime/usage_routing.py:643`), with C3-5's refusal position. Each refusal reads only `managed_projects`.

| Entry point (file:line) | Reached from | First settings, credential, config or ledger access | Refusal position (C3-5) |
|---|---|---|---|
| `usage_codex_host.freeze` (`runtime/usage_codex_host.py:116`) | bridge `freeze` (`scripts/usage_codex_host.js:5, 61–62`) | `usage_settings.controller(cwd)` at 121 | first statement, before 121 |
| `usage_codex_host.execute` (207) | bridge `execute` (`scripts/usage_codex_host.js:72, 74`) | `usage_settings.controller` at 223, then `_plan_settings` (credential binding) | after the `type(plan)` check (no read), before `package_digest` and 223 |
| `usage_canary_host.execute` (`runtime/usage_canary_host.py:161`) | the `first-canary.md` procedure | `usage_settings.scope` at 166; `controller(create=True)` and `configure` at 185–192 | first statement, before `verify_approval` and 166 |
| `usage_canary.run` (`runtime/usage_canary.py:70`) | `usage_canary_host` (144) | none itself; it calls `_run_live` at 75 | first statement |
| `usage_routing._run_live` (`runtime/usage_routing.py:64`) | `usage_canary.run` (75), `usage_codex_host.execute` (252) | `usage_settings.controller` at 69 | after the evidence type check, before 69 |
| `usage_routing.evaluate` (701), `handoff` (717), `claim_handoff` (724) | `scripts/usage_routing.py:42` | `controller(cwd)` at 704, 720, 727 | first statement |
| `Controller._run` (574), `_evaluate` (408), `request_handoff` (449) | all of the above | the controller is already open | backstop, before `Ledger.begin` (643) and before any decision |
| `usage_routing.run` (60) | `scripts/usage_routing.py:42` | none: it raises `live_execution_unavailable` | already disabled (A2a) |
| `usage_routing.status` (19), `enable` (47), `configure` (688), `disable` (694), `reconcile` (710); `scripts/usage_key_setup_host.py:96` | CLI, key setup | `controller` / `scope` | **not refused, by design** ([4.3](#43-stop-legacy-admissions)): setup and report only, no spending; tested by C3-5 |

## Appendix B — Contract walk

The 8 markers in `tests/test_smart_routing_features.py` on `main` at `bd5cde6` (#197 merged), from precondition to pass. "base" means that commit; a slice name means the step is new code in that slice.

### B.0 Prototype runs (validated by running)

Throwaway patches in a scratch worktree of `origin/main` (`../vibe-workflow.worktrees/scratch-e`, deleted afterwards). They prove the risky steps execute; they are not the slice implementations. Run on this Mac with Python 3.15.2 (`/opt/homebrew/opt/python@3.15/bin/python3.15`).

**P1 — C3-0b `project_required` and the test pins.**
- Run 1 (at `2ce00f1`). Patch: `require(self.project is not None, 'project_required')` in `Service._workflow_binding`, right after `valid_locator` (`scripts/routing.py:354`). Command: `python3 -B scripts/test_sharded.py 'test_routing_*' test_usage_budget`. Result: 73 failures in `test_routing_gates`, `test_routing_accounting`, `test_routing_accounting_roots`, `test_routing_accounting_blocks` and `test_routing_subscription_reconcile`.
- Run 2 (at `2ce00f1`), adding the pins listed in [6.3 C3-0b](#63-slices). Same command. Result: 626 tests OK.
- Run 3 (at `bd5cde6`), with the same patch plus `test_smart_routing_features`. Result: 663 pass, and the module fails only through `tearDownModule`: "remove the NEEDS_C3(C3-0b) marker". That is the unexpected-pass rule working.
- Run 4: run 3 with that marker line deleted. Command: `python3 -B scripts/test_sharded.py 'test_routing_*' test_usage_budget test_smart_routing_features`. Result: **664 tests, OK** (exit 0).
- The patch touches 3 files: `scripts/routing.py` +1; `tests/fixture_routing_accounting.py` +11/−1; `tests/test_routing_gates.py` +5/−4.

**P2 — the fixture path for the native contracts and `proposal_begin`** (at `2ce00f1` plus #197's merged test file; the runtime code is identical to `bd5cde6`).
- The prototype implements:
  - `routing_settings.managed_project`;
  - `routing_observations` (`default_source`, `collect`);
  - `routing_launch` (`route_identity`, `choice`, `assignment_scope`, `unplanned_job`);
  - `scripts.routing.managed_service(checkout, scope=None)`;
  - `prepare` with optional `observations`;
  - the native hook after the reservation commit, and the proposal `begin` hook;
  - the fixture body, per [6.4](#64-c2-marker-map-and-pr-197-amendments).
- Run 1: the native contract hit `FixtureError` (`launch_admitted` false). Routing answered `blocked/workflow_unavailable`, because the default `routing_gates._resolve_scope` refuses the AdmissionFixture checkout (`RuntimeScopeError … qualification_unobserved`). `proposal_begin` already passed.
- Fix: `managed_service(checkout, scope=...)`. The caller that already holds the canonical scope (delegation) passes it, as the gate tests inject `resolve=`; the project pin is still checked against `scope.repository_root` ([3.3](#33-observation-collector)).
- Run 2. Command: `python3 -B -m unittest tests.test_smart_routing_features.C2bNativeDelegationThroughRouting tests.test_smart_routing_features.C2cProposalThroughRouting`. Result: **3 unexpected successes** (both native contracts and `proposal_begin`), with `tearDownModule` asking to remove their markers. They pass.
- The same command with `HOME` set to an empty directory, plain `unittest` (no `test_sharded`): 3 unexpected successes, and **0 files** under `HOME` afterwards.
- The G5 sentence ([6.3 C3-6](#63-slices)) was appended to `skills/review-my-project/references/procedure.md`. `routes_reviewers_through_routing` returns it under the merged `NEGATION` regex, and `test_project_review_routes_delegated_reviewers_through_the_routing_command` is an unexpected success.
- Not prototyped: `worker_prepare` (C3-4b). Its fixture route id carries no effort, so the fixture lists the OpenRouter routes for every effort the hosts accept ([6.4](#64-c2-marker-map-and-pr-197-amendments)). The proposal case, which uses the same mapping with effort `none`, passed.

**P3 — the activation self-check runner on this Mac.**
- A runner started with `sys.executable` executes three checks, each as `subprocess.run([sys.executable, '-I', '-B', '-c', code, PACKAGE], env={PATH:/usr/bin:/bin, HOME, TMPDIR, LANG}, cwd=<workdir>, timeout=10, start_new_session=True)`. `code` first does `sys.path.insert(0, sys.argv[1])`, where `PACKAGE` is `runtime.state.PACKAGE`, the plugin package root, never the project checkout.
- The three checks: `from runtime import routing_settings, routing_gates, usage_routing`, then loading `scripts/openrouter_proposals.py` and `scripts/openrouter_workers.py` by path. All three returned **exit 0, `ok`**; the workdir's `HOME` stayed empty.
- Control: `python3` resolved from `PATH=/usr/bin:/bin` is Apple's Python 3.9.6, and `from runtime import routing, usage_routing` fails there with `TypeError: unsupported operand type(s) for |`. So the gate must use `sys.executable`, as [4.2](#42-activation-gate) now says.

### B.1 The walk

**B1. `test_a_workflow_bound_prepare_requires_the_project_pin` (C3-0b; marker at line 495).**
1. Precondition: `C2bProjectPin.setUp` (484–491) builds its **own** unpinned `Service`, independent of `GateBase.setUp`, which C3-0b pins.
2. The test sends `prepare` with `workflow=gate_tests.locator()`.
3. `Service._prepare` (`scripts/routing.py:309`) → `_workflow_binding` (346). C3-0b adds `project_required` after `valid_locator` (354), before the early `cwd` check and before any workflow read.
4. The answer is `blocked/project_required`. Proven by P1 run 3.

**B2. `test_native_worker_admission_calls_routing_prepare_once` (C3-2; 527).**
1. Precondition: `AdmissionFixture.plan()` and `prepare_request()` (`tests/fixture_delegation.py:86, 89`). The request's `selection` is `explicit` (96–97).
2. `managed_project_fixture` (C3-1) with `fixture_route('native:new-native-model#high', 'included_subscription')`: default paths redirected; `managed_project(repo)` true; the manifest has `route_identity('codex_native', …)` with a fresh `5h` window; `authority_required` set.
3. `delegation.prepare_assignment` → `_prepare_native` (`runtime/delegation.py:2050, 2055`): `_discovery` (call at 2106) validates `selection` against the catalog, then the reservation commits in `_reservation_write` (1799).
4. C3-2 hook: managed project → `assignment.choice = route_identity('codex_native', model, effort) + funding: 'subscription'` (G1); `workflow{…, job_id: 'edit', attempt_id}`; `job = delegation.routing_job(...)` (3270).
5. `scripts.routing.managed_service(repo, scope=scope)._prepare` → `_workflow_binding` → `D6Workflow.view` (`runtime/routing_gates.py:225`) with the injected scope → the collector with the fixture source → `runtime.routing.admit` (745), **once** → `_decide`'s explicit-choice branch → `admitted`.
6. `_reply(record, admitted=True)` (1378): `launch_admitted` (1386) is true, so the precondition holds. `admit.count == 1`. Proven by P2 run 2.

**B3. `test_native_worker_admission_is_bound_to_its_workflow_job` (C3-2; 535).** B2 steps 1–5. `admit` receives `workflow=` the binding from `routing_gates.job_binding` (125), and `locator.job_id == 'edit'`. Proven by P2 run 2.

**B4. `test_proposal_begin_obtains_one_routing_admission` (C3-4a; 704).**
1. Precondition: `enroll` and `configure(routes=('proposals',))`; the request has model `model/v1`, provider `p`, effort `None`; route `fixture_route('openrouter:model/v1@p', 'dollar')`.
2. The fixture maps the route to both OpenRouter hosts and every effort, class `gateway`. It also sets up:
   - organization `fixture-openrouter`, enabled with $10 observed;
   - a `credit` window naming it, with pool `billing_class: gateway`;
   - a ready credential for `assignment_scope(repo)`;
   - route `demand: {'credit': 1_000_000}`.
3. `openrouter_proposals.run('begin')` (`scripts/openrouter_proposals.py:321`). C3-4a first calls `managed_service(checkout)._prepare` **before** `settings.admission` (324), with role `researcher`, `choice = route_identity('openrouter_proposal', model, provider, effort=None → 'none') + funding: 'api'` and `job = unplanned_job(...)`.
4. The collector calls `openrouter_settings.status(checkout)`, which takes only a brief shared lock and never the `admission` lock → `integration_enabled`, `seen.openrouter_revision`.
5. `routing.admit`, **once**. In `_candidate` (`runtime/routing.py:438`): `gateway` ∈ `FUNDED` (332); `_credential_reasons` (411) passes; `_funding` (354) finds the organization; result `admitted`, with `seen` echoed.
6. `settings.admission(cwd, 'proposals', store=store, revision=seen.openrouter_revision)` and `external_delegation.check` pass as today.
7. `_run('begin')` returns `attempt_token`.

P2 run 2 proved steps 3, 5 and 7, with the hook placed inside the lock and the fixture supplying `integration_enabled` directly. The before-the-lock order and the `status` read come from the round-5 lock test; C3-4a's obligation test runs them together.

**B5. `test_worker_prepare_obtains_one_routing_admission` (C3-4b; 740).**
1. Precondition: `enroll` and `configure(routes=('workers',), …)`; brief model `vendor/model`, provider `provider`, effort `low`; route `fixture_route('openrouter:vendor/model@provider', 'dollar')`.
2. The fixture is set up as in B4 step 2. The `openrouter_worker` route with effort `low` exists, because the fixture lists every effort.
3. `openrouter_workers.prepare` (`scripts/openrouter_workers.py:101`) validates the brief. Then C3-4b calls `managed_service(source_root)._prepare` **before** `settings.admission` (126), with `choice = route_identity('openrouter_worker', …, effort='low') + funding: 'api'` → collector (`status`, never the `admission` lock) → `admit`, **once** → `admitted` (as in B4 step 5).
4. `settings.admission(source, 'workers', root=root, revision=seen.openrouter_revision)`.
5. `prepare` writes `brief.json`.

Not prototyped (see B.0). The steps are those B4 proved, with another host name.

**B6. `test_project_review_routes_delegated_reviewers_through_the_routing_command` (C3-6; 805).** C3-6 adds the G5 sentence to `skills/review-my-project/references/procedure.md`, and `routes_reviewers_through_routing` (`tests/test_smart_routing_features.py:178`) returns it. Proven by P2.

**B7. `test_external_delegation_keeps_no_caller_owned_selector` (C3-7; 622).** C3-4a has moved X0 to `constraints`. C3-7 deletes `select` (`runtime/external_delegation.py:95`) and `automatic_allowed` (90) with their tests, so `hasattr(external_delegation, 'select')` is false.

**B8. `test_usage_routing_cli_no_longer_selects_routes` (C3-7; 640).** C3-7 removes `evaluate`, `run`, `handoff`, `claim_handoff` and `reconcile` from `choices` (`scripts/usage_routing.py:24`). `argparse` exits 2 with `invalid choice`.

**Steps no slice provides:** none.

**Not proven by running:**
- B5 (`worker_prepare`), B7 and B8 (deletions) are enforced by their own markers: each slice must make its contract pass and remove the marker ([6.1](#61-shared-file-protocol)).
- The collector's real derivation of `integration_enabled` and `funding_verified`, and never taking the OpenRouter lock: C3-1's test list.
- `reconcile_assignment` for a never-launched assignment: C3-2's test list.
- The prepare-before-lock order and the stale-revision refusal: C3-4a's and C3-4b's test lists.
