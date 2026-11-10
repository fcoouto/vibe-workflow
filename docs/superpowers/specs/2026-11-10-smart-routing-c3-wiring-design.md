# Smart Routing C.3 — caller wiring and selector retirement design

- **Version:** 1.2 draft (round-2 review fixes; see [revision history](#8-revision-history))
- **Date:** 2026-11-10
- **Status:** Design only. No code, model call, OpenCode server or live run was used to write it. Open user decisions are in [section 7](#7-open-decisions).
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

## 1. Terms

Each term is defined here once and used with this meaning below.

| Term | Meaning |
|---|---|
| **Selector** | Code or an instruction that decides which model, effort, provider or funding route a delegated worker uses. |
| **Legacy selector** | A selector other than `scripts/routing.py prepare`. They are listed in [section 2](#2-inventory-of-selection-paths). |
| **Legacy writer** | Code that opens new financial exposure in the ledger without a routing admission. Today that is only S1 `Ledger.begin`, called from `usage_routing.Controller._run`. |
| **Launcher** | Trusted code that runs one delegated attempt end to end through the routing command. A coordinator model, a worker or a skill text is never a launcher; they ask a launcher. |
| **Managed launch** | One attempt run by a launcher under the [launcher contract](#3-launcher-contract). |
| **Enforced host** | A host where every model request of the attempt passes `check` before it is sent. Two qualify: OpenCode with the shim and an `enforced` probe, and in-process HTTP launches (API Usage, OpenRouter proposals) where the launcher is the only sender. |
| **Advisory host** | A host where routing chooses and accounts but cannot block a send before it happens. Today: Codex native subagents and Claude Code native subagents. Neither has a qualified pre-transmission hook. |
| **Dollar route** | A route whose billing class spends dollars: direct API (`api_grant`, `api_prepaid`) or `gateway` (OpenRouter). |
| **Observation collector** | Trusted code that builds the `observations` snapshot for `prepare` from local records only ([section 3.3](#33-observation-collector)). |
| **Managed project** | A checkout on the routing settings' `managed_projects` list. Between wiring and cutover, only managed projects use the new path ([section 4.1](#41-two-phases)). |
| **Activation gate** | The check that decides whether a project may be added to `managed_projects` ([section 4.2](#42-activation-gate)). |

## 2. Inventory of selection paths

The table covers every path that chooses or funds a delegated worker on `main` at `4f16c36`. "Target" is the call sequence after C.3; [section 3.2](#32-call-sequence) defines the steps. Line numbers are for that commit.

| ID | Path (file:function) | What it decides today | Target sequence | Slice |
|---|---|---|---|---|
| N1 | `runtime/delegation.py:_prepare_native` (2055) via `_discovery` (1461), reached from `scripts/workflow.py` `delegation prepare` (279) | Validates a coordinator-supplied `selection{model, effort, mode: explicit/inherit, profile_ids}` against the observed Codex catalog and cached guidance. The coordinator chooses the model from guidance ([clients/delegation.md](../../../clients/delegation.md) "Select suitable worker settings"). It also reserves claims and resources and returns spawn settings. | Delegation reserve (unchanged) → in-process `prepare` with `workflow` and the delegation `attempt_id` → catalog check of the admitted strategy → `dispatch` at the last pre-spawn step → spawn with explicit model and effort → `observe` → `record complete` from `reconcile`. Advisory host. | C3-2 |
| N2 | `runtime/evidence.py:prepare_review` (5691) → `_prepare_native(role='review')` | Same as N1 for an independent reviewer. Independence is checked by evidence, not by route. | As N1, with role `review` (routing role `reviewer`) and an independence constraint from the review policy. | C3-2 |
| N3 | `runtime/delegation.py:prepare_continuation` / `consume_continuation` (3079, 3036) | Reuses a child for unchanged work. The continuation inherits the child's settings. | Reuses the child and its profile, but gets a **fresh** `prepare` and `dispatch` (spec F11). A change of scope or role gets a new profile. | C3-2 |
| N4 | `runtime/workstreams.py:consume_dispatch` (201), `validate_admission` (183) | Workstreams launch gates for a planned job. No model choice. | Gates unchanged. Becomes the last pre-spawn step, where `dispatch` runs for Workstreams jobs. | C3-2 |
| N5 | `runtime/delegation.py:reserve_coordinator` (1947) | Reserves coordinator resources. No model choice. | Never routed (`coordinator_not_routed`). Its usage may join a root through `record observed`. | none |
| C1 | Claude Code native subagents: instruction-only, from [policy 03 worker selection](../../../policies/03-agent-delegation.md#select-capability-and-dispatch) ("Select each worker's model and reasoning…") | The coordinator model picks the subagent model from guidance. No runtime path exists on `main`; D8 (#156–#175) is still open. | Advisory host. In a managed project the coordinator asks a launcher instead of choosing a model (C3-6 text). A runtime path follows D8 and is outside C.3. | C3-6 |
| X0 | Coordinator following [clients/external-delegation.md](../../../clients/external-delegation.md) (lines 22–30) | The actual external choice. The coordinator applies explicit choice, saved preference and "the only eligible route", and otherwise continues natively. | In a managed project the coordinator asks a launcher; explicit choices become `assignment.choice`, and enabled scopes, local preference and `automatic: false` become `constraints`. | C3-4a |
| X1 | `runtime/external_delegation.py:select` (95), `automatic_allowed` (90) | Encodes X0's precedence offline. **No production caller**: only tests call it. | Deleted with no alias. | C3-7 |
| X2 | `runtime/external_delegation.py:check`, `prepare`, `effective_permission`, `native_review` | Source scope, permission, destination and packet provenance, plus review independence. Not a selector. | Kept unchanged, as constraints and pre-send checks inside the launcher. | none |
| A1 | `runtime/usage_routing.py:Controller.evaluate`/`_evaluate` (441, 408), `scripts/usage_routing.py` `evaluate` | Whether an API Usage proposal is `eligible`, `native` or `paused`. **Legacy selector.** | Retired. API Usage candidates come from the collector, and `prepare` decides. | C3-5; removal C3-7 |
| A2 | `Controller._run` (574) → `Ledger.begin` (643), reached through every entry below | Opens S1 runs and sends through `openai_api`. **Legacy writer.** | `prepare` → `dispatch` → `Ledger.convert_hold` → `openai_api` send with a `check` before each request → `record complete` with the measured cost. | C3-5; removal C3-7 |
| A2a | `scripts/usage_routing.py` `run` → `usage_routing.run` (60) | Already disabled: it only raises `live_execution_unavailable` (60–61) and never reaches `Controller._run`. | Deleted in C3-7. | removal C3-7 |
| A2b | `runtime/usage_codex_host.py:execute` (207) → `usage_routing._run_live` (64, call at 252) → `Controller._run_live` (560) → `_run`; reached from `scripts/usage_codex_host.js` (the Codex host bridge) | Live API proposal on the Codex host. | As A2. | C3-5 |
| A2c | `runtime/usage_canary_host.py:execute` (161) → `usage_canary.run` (70, call at 144) → `usage_routing._run_live` → `_run` | First-canary host, from `skills/manage-usage-routing/references/first-canary.md`. | As A2. | C3-5 |
| A3 | `Controller.request_handoff`/`claim_handoff` (449, 495); `scripts/usage_routing.py` `handoff`, `claim_handoff` | Queues paused API work for native dispatch in the ledger's `handoffs` table. | Retired. A paused assignment stays `pending` in `prepare` with its reason, and the coordinator prepares it again. Existing records stay as history ([4.4](#44-settle-outstanding-legacy-work)). | C3-5; removal C3-7 |
| A4 | `scripts/usage_routing.py` `reconcile` → `Ledger.reconcile` (1603) | Reports unresolved S1 calls for a scope; settles nothing. | Replaced by `status.ledger.legacy_outstanding` and `settle_legacy_call` (C3-0). | removal C3-7 |
| O1 | `scripts/openrouter_proposals.py:triage` (154), `run` `begin`/`dispatch` (321) | An advisory candidate list from cached OpenRouter metadata; the caller picks the model. The proposal store records attempts. | Discovery metadata feeds the collector and the evidence compiler. `triage` stays an advisory report that chooses nothing. In a managed project, `begin`/`dispatch` become a managed launch on the `gateway` route and keep `external_delegation.check`, endpoint pinning and `native_review`. | C3-4a |
| O2 | `scripts/openrouter_workers.py:prepare` (101), `_execute` (330) | A caller-chosen OpenRouter model, launched with `opencode run --pure` (OpenCode V1, `plugin: []`, so no shim). | In a managed project, workers run as OpenCode managed launches (C3-3) and keep the read-only permission profile from `configuration` (179). The V1 launch stops admitting. | C3-4b; removal C3-7 |
| R1 | `scripts/routing.py` `prepare`/`check`/`dispatch`/`record`/`settle` | The target decision and admission path. | Called only by launchers. | C3-0 (service changes) |
| Q1 | `tests/qualification/opencode_live.py:Run` | Test-only launcher for qualification and the pilot. | Stays test-only. Production code never imports `tests/`. | none |

Council (F08) is not in this repository, so C.3 has no Council caller. Its rule (explicit invocation only; members and independence are hard constraints) is part of the launcher contract and checked by C.2d. Review my Project (F09) delegates through N1/N2, and its skill text changes in C3-6.

## 3. Launcher contract

### 3.1 What a launcher must do

1. **Pin the project.** Run `scripts/routing.py` with `--project <checkout root>` (realpath) for every op; in-process use builds `Service(project=…)`. The refusal code is `project_required`, defined once by the routing service (C3-0). The service refuses a workflow-bound `prepare` without a pin. As defence in depth, the launcher (C3-1) refuses any launch without a pin, using the same code before it calls the service. `status` must show `project_pinned: true`.
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
      (in-process HTTP) convert_hold for dollar routes → check → send, per request
      (advisory native) return the route to the coordinator, which spawns with explicit model/effort
   ─► idle → re-read → (OpenCode) routing.descendants → opencode_accounting
   ─► record complete{usage} → (OpenCode) routing.detach → settle (read-only report)
```

`record observed` joins coordinator or auxiliary usage to an open root when it can be observed. Native delegation (C3-2) calls the routing service in-process (`scripts.routing.Service`), so `runtime.routing.admit` runs in the caller's process; the C.2 counting contracts depend on this.

### 3.3 Observation collector

`prepare` trusts the `observations` it is given: billing class, health flags, windows and credentials. Today the caller supplies them; the qualification harness builds them by hand. C.3 changes this in two parts.

**Collector (C3-1).** `runtime/routing_observations.py:collect(project, assignment)` reads only local trusted records. It never reads a secret, makes a network call or calls a model.

| Fact | Source |
|---|---|
| Candidate routes and billing class | The route manifest that C3-1 creates (`runtime/routing_routes.json`; plan Task 0.3 never created it). It is built from the [capability matrix](../../smart-routing-capability-matrix.md) and its content is subject to [user decision 5](#71-for-the-user). A route that is absent, `unverified` or `unsupported` is not a candidate. |
| Subscription windows | Rate-limit observations the launcher stores after settlement (`opencode_accounting.rate_limit_observation`), in a private file next to the routing settings, with their observation time. Missing telemetry gives `fresh: false`, and the route is reported enabled-but-blocked (P0.2 is still open for ChatGPT). |
| Dollar organizations | The ledger's organizations and balances (existing `observe` paths). |
| Credentials | Binding readiness metadata from `usage_credentials` and `openrouter_settings` (state, scope, reference). The secret is never resolved. |
| OpenRouter | `openrouter_settings.admission` activation, scope and funding verification, mapped to the `gateway` class with `integration_enabled`. |
| Entitlement | The entitlement record ([3.4](#34-listed-is-not-entitled)). |

**Service enforcement (C3-0).** When the service is pinned to a managed project, `prepare` refuses a request carrying `observations` (`observations_not_accepted`) and calls the collector itself. If the collector module is missing or raises, `prepare` is blocked with `observations_unavailable`, so C3-0 fails closed until C3-1 lands. Request observations stay accepted only for unmanaged projects in phase W (the pilot and qualification harness, on their own ledgers) and under `--test-inputs`. C3-7 removes the unmanaged case.

**Source seam (C3-1; test-only use).** This seam is defined once, here. `collect(project, assignment, *, source=None)` reads its route manifest and window observations from a source object. With `source=None` it uses `routing_observations.default_source()`, which returns the local source (manifest file, stored rate-limit observations, entitlement record). A source has two methods:
- `manifest()`, which returns the route list;
- `windows(pool, now)`, which returns that pool's window observations, each with its observation time.

Only code can replace `default_source`: a test patches the module attribute. No request field, setting, table, environment variable or command-line flag can select a source, the same rule as the S3.5 workflow reader. The C.2 test helper uses this seam ([6.4](#64-c2-marker-map-and-pr-197-amendments)).

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
- The collector excludes the route until the user clears the mark with a local command.
- The launcher never retries other routes automatically after such a refusal.

### 3.5 Operator recovery

Operator ops are one-shot only and can never be reached from `--serve` or `record` ([S3.6b-2 rules](../plans/2026-11-08-smart-routing.md#task-s36-lineage-settlement-and-retention)). The launcher prints which one applies and never runs one itself.

| Situation | Op |
|---|---|
| Uncertain dollar hold | `reconcile_uncertain` |
| Uncertain subscription hold | `reconcile_subscription` (PR #192) |
| `cost_unknown` hold | `resolve_cost_unknown` |
| Unconfirmed overrun | `resolve_overrun` |
| Over-bound S1 call, legacy block | `resolve_block` |
| Window debit kept wrongly | `drop_debit` (new, C3-0) |
| Outstanding legacy S1 call in any open state | `settle_legacy_call` (new, C3-0; [4.4](#44-settle-outstanding-legacy-work)) |

`set managed_projects` is also one-shot only ([4.2](#42-activation-gate)), but it is a settings change, not a recovery op.

### 3.6 Refusal reasons

These are new fixed codes, each defined once by the slice named. Reasons from the routing command pass through unchanged.

| Code | Defined by | Meaning |
|---|---|---|
| `project_required` | C3-0 (service); C3-1 reuses it | No `--project` pin. |
| `observations_not_accepted`, `observations_unavailable` | C3-0 | See [3.3](#33-observation-collector). |
| `managed_path_incomplete` | C3-0 | Activation gate not met ([4.2](#42-activation-gate)). |
| `legacy_route_retired` | C3-0 (code constant), used by C3-4b and C3-5 | A legacy path was reached for a managed project. |
| `authority_not_required`, `unstamped_holds_open`, `ledger_reconcile_pending`, `project_not_managed`, `workflow_required`, `catalog_mismatch`, `route_not_entitled`, `dollar_route_needs_enforced_host` | C3-1 | Launcher preflight and checks. |
| `host_unhealthy`, `profile_not_enforced`, `api_funding_unqualified`, `attach_failed`, `launch_unconfirmed` | C3-3 | OpenCode launch. `host_unhealthy` covers any `routing.health` answer other than `ok`, including an RPC error. `launch_unconfirmed` follows `record uncertain`. |

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

- **Phase W (wiring).** Slices C3-0 to C3-6 add the managed path to every caller and update the instructions. A caller uses the path only for a managed project; every other checkout keeps its current selector, as plan S2.7 and S3.7 already allow. `managed_projects` starts empty. For a managed project, routing is fully active; there is no decision-only mode.
- **Phase R (retirement).** After C.1 passes and the user decides on cutover, C3-7 removes:
  - the managed-project branch;
  - every legacy selector and the legacy writer;
  - the phase W conditional text in the instructions.

  After that, every checkout uses the managed path.

During phase W, two code paths open exposure in one ledger: routing admissions, and S1 `begin` for unmanaged projects. Both run in the one `Ledger` class and its transactions, so caps and headroom stay shared. C3-7 removes the legacy writer, and no compatibility alias remains.

### 4.2 Activation gate

A project may be added to `managed_projects` only through the one-shot `scripts/routing.py set` with `managed_projects`. `--serve` refuses it with `operator_op_not_served`, as it refuses operator ops. The service also checks the activation gate, defined once in C3-0 as a fixed list of required capabilities. A capability counts only when its behavioural self-check passes; the code merely being present is not enough.

**Self-check contract.** Each owning phase W slice adds `managed_self_check(workdir) -> list[str]` to a file it owns. It returns an empty list when the check passes, or fixed failure codes otherwise. The check:
- runs the slice's managed path end to end against stores created in `workdir`: a temporary routing settings file, a ledger, a workflow double and a test source through the [source seam](#33-observation-collector);
- makes no network, model, host or secret-store call, and never touches the production defaults or the checkout.

For each capability, the gate:
- creates a fresh owner-only temporary directory;
- runs the check with a 10 s budget, each in-process for runtime modules, or as `python3 -I -B <script> --managed-self-check <workdir>` with a minimal environment (no credential-like names) for scripts;
- deletes the directory.

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

If any check is missing or fails, the change is refused with `managed_path_incomplete` and the failing names, and nothing is written. Removing a project is always allowed, and it is also one-shot only. C3-7 deletes the gate, the self-checks and `managed_projects` together.

### 4.3 Stop legacy admissions

- **Phase W, per project.** The refusal sits at the narrowest point every path crosses, so no entry point can bypass it:
  - **API Usage entry points.** Each refuses a managed project with `legacy_route_retired` as its first action. That comes before it opens `usage_settings.controller`, reads API Usage, OpenRouter or credential settings, validates a credential binding, writes configuration or an organization, or touches the ledger. Its only read is the routing settings' `managed_projects`. The entry points are:
    - `usage_codex_host.execute` (A2b), before `usage_settings.controller` (223) and before the credential-binding checks it reaches through `_plan_settings`;
    - `usage_canary_host.execute` (A2c), before `usage_settings.scope` and before `controller(create=True)` and `controller.configure` (185–192), so no S1 configuration or organization is written;
    - `usage_canary.run` and `usage_routing._run_live` (both on the A2c path);
    - the module functions `usage_routing.evaluate`, `handoff` and `claim_handoff` (A1, A3), before `controller(...)`.
  - **Backstop.** `Controller._run` refuses again before `Ledger.begin`, and `Controller._evaluate` and `request_handoff` refuse the same way, in case a future caller skips an entry point.
  - A2a is already disabled ([inventory](#2-inventory-of-selection-paths)).
  - OpenRouter workers: the V1 launch in `openrouter_workers._execute` refuses a managed project.
  - X0 and C1 are instructions; C3-6 and C3-4a change them for managed projects.
- **Phase R, global.** The entry points are deleted. `scripts/usage_routing.py` keeps only `status`, `configure`, `enable`, `disable` and `quote` (setup and observation, which the API Usage skill still needs). `evaluate`, `run`, `handoff`, `claim_handoff` and `reconcile` go, and become usage errors (exit 2). There is no alias under the old names.

**Old consent is never spending authority (spec §10.4 step 4).** Legacy Usage Routing consent, Manual or Smart Balance state, a saved `external_permission`, an organization's configuration and an S1 qualification record are never translated into API spending. The only authority for a direct API route is `api_usage_enabled` in the routing settings, set by an explicit `scripts/routing.py set`. In a managed project, C3-5 makes Manage API Usage's `enable` action write that setting through the routing command; nothing migrates it from old state. Test (C3-5): a ledger with enabled legacy Usage Routing, current consent and a qualification record, plus default routing settings, still leaves API routes `pending` with `api_usage_disabled`, and resolves no credential.

### 4.4 Settle outstanding legacy work

Before C3-7 merges, `status.ledger.legacy_outstanding` (added by C3-0) must be empty. It lists:

- every S1 run call that is still open: `reserved`, `dispatching` or `unresolved`, including `over_bound`;
- `legacy_block` organizations.

It also lists `ready` handoffs for information. They hold no exposure and do not block.

Today `Ledger.complete` accepts a `dispatching` or `unresolved` call (`usage_budget.py` 835, the state check at `a824819`), but only the legacy send path calls it. No operator entry reaches it, and `Ledger.reconcile` only reports. So the one-shot op `settle_legacy_call{run_id, phase, evidence, event_id}` (C3-0) covers every open state, under the [operator-op rules](../plans/2026-11-08-smart-routing.md#task-s36-lineage-settlement-and-retention):

| Call state | Accepted evidence | Effect |
|---|---|---|
| `reserved` | `{kind: never_dispatched}` | The call becomes `rejected-before-send` at cost 0, as `Ledger.close_run` already does for a reserved call (`usage_budget.py` 864), and the release **closes its run**. For a `count` call nothing was charged; for a `generation` call the completed `count` keeps its charge. A run never sends again after a release. This is sound because S1 `dispatch` writes `dispatching` inside its transaction (827), and `openai_api` calls that `admit` before transmitting, so `reserved` proves nothing was sent. |
| `dispatching`, `unresolved` | `{kind: receipts, receipts, cost_usd_micros}` with a cost of at least 1 micro-USD | Charged as reported through the same code as `Ledger.complete`. A cost above the maximum becomes over-bound and is resolved with `resolve_block`. |
| `dispatching`, `unresolved` | `{kind: charge_reservation}` | Charges the maximum and releases nothing. |
| `over_bound` | none | Refused (`block_not_pending`); use `resolve_block`. |

Nothing is released without spend except a provably unsent `reserved` call. A zero cost is `evidence_invalid`. Because every open state has a path, C3-7 can never be blocked forever. Over-bound calls and legacy blocks keep `resolve_block`. C3-7 marks the remaining `ready` handoffs `retired` and keeps the records; `claimed` handoffs belong to native delegation and stay as history.

### 4.5 Preserve history and ownership

Settled charges, cycle caps, S1 runs, handoff records, event rows and root aggregates stay under the existing retention rules (spec §11). Retirement deletes code, never ledger records. Unresolved ownership blocks the affected cutover step until it is settled, as spec §10.4 requires. Unresolved ownership means open holds, uncertain attempts and open delegation assignments.

### 4.6 Ledger version

PR #192 adds `window_debits` to the ledger record without changing the state version, so code from before #192 would ignore a debit. C3-0 raises `STATE_VERSION` to 5 so that older code refuses the ledger (`ledger_identity_changed`), as v4 did for settled charges. The 4→5 step changes no run, hold or handoff.

`runtime/usage_budget.py` is at its 1,616-line ceiling, so C3-0 first moves the version migration (`STATE_VERSION` and the 1→4 steps) into a new `runtime/ledger_versions.py` and adds the 4→5 step there. C3-7 later removes `Ledger.begin` and its S1-only helpers.

## 5. Rollback, failure modes and security review

### 5.1 Rollback

- **Explicit OFF.** `scripts/routing.py set smart_routing=false`, with no model call.
  - Unsent admissions of the old revision are refused.
  - Dispatched work finishes.
  - Later launches use `worker_choices` through the same allocator.

  This is the only kill switch, in both phases.
- **Previous strategy table.** Lane B's `scripts/routing_table.py refresh` must keep the previous verified table, and rollback selects it with the same command. A missing or unreadable table already falls back to `worker_choices` (`table_unavailable`).
- **Phase W only.** A one-shot removal from `managed_projects` returns a project to its legacy selector for new launches. Open holds keep their rules and are settled, never dropped.
- **No return to legacy selectors after C3-7.** A revert PR can restore the code, but C3-7 removes no record and the ledger stays at v5.

### 5.2 Failure modes

| Failure | Behaviour |
|---|---|
| Routing command missing, busy or failing | The launcher refuses, passing through `ledger_busy`, `ledger_unavailable` or `internal_error`. Nothing is sent. Interactive sessions are unaffected. |
| Strategy table missing or malformed | `table_unavailable`; `worker_choices` is used. This never blocks (RF8). |
| Collector missing or failing | `observations_unavailable`; nothing is admitted. |
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
5. **Credential isolation.** Launchers strip credential-like environment names from host processes they start, and never run project commands in managed sessions.
6. **Funding boundaries (RF1, RF6, spec §10.4 step 4).**
   - Smart Routing ON never enables API Usage, OpenRouter or observability.
   - A direct grant never funds a gateway.
   - `purchased_usage` is never subscription headroom.
   - Advisory hosts never get dollar routes.
   - Old consent is never spending authority.
7. **Legacy refusal placement.** Every API Usage entry point refuses a managed project as its first action, before any settings read, credential-binding check, configuration or organization write, or ledger access ([4.3](#43-stop-legacy-admissions)). `Controller._run` repeats the refusal before `Ledger.begin` as a backstop.
8. **History preservation.** No migration or retirement deletes runs, charges, caps, events or open holds.
9. **Review independence (F13).** Reviewer constraints come from the review policy, not from the coordinator's request.

## 6. Slice plan

Each slice is one PR. A slice may edit only the files it owns; every other file is read-only for it, except as the [shared-file protocol](#61-shared-file-protocol) allows. `scripts/validate.py` sets line ceilings, and four touched files are at theirs: `runtime/delegation.py` (3,369), `runtime/evidence.py` (8,505), `runtime/routing.py` (1,506) and `runtime/usage_budget.py` (1,616). New code therefore goes into new modules, and edits to those four files are line-neutral or shrink them.

### 6.1 Shared-file protocol

The protocol is defined once here and applies to every slice.

- **One owner per file.** Each file has exactly one owning slice at a time, as listed in [6.3](#63-slices). Where two slices must edit one file, they are sequenced, never concurrent. The later slice depends on the earlier one, and the file's entry names both owners in order. The sequenced files are:
  - `clients/opencode.md`: C3-0 (S3.6c section), then C3-3 (launcher section);
  - every file C3-7 edits: their phase W owner first, then C3-7.
- **`tests/test_smart_routing_features.py`** (PR #197, Lane D). A slice deletes only the `@NEEDS_C3('<slice>')` decorator lines that name it, and changes nothing else in the file. There is one exception: C3-1 owns the body of the `managed_project_fixture` helper, between the comment lines `# --- managed_project_fixture: body owned by C3-1 ---` and `# --- end managed_project_fixture ---`, and edits nothing else there ([6.4](#64-c2-marker-map-and-pr-197-amendments)). A contract that still fails after its marker is removed means the slice is not done. Structural changes to the file go through Lane D.
- **`clients/instruction-map.json`** is generated. Never hand-merge it. On a conflict, `git merge origin/main`, then run `python3 -B scripts/read_instructions.py --prepare` and commit the result.
- **`docs/policy-sources.md`** is append-only. Each slice appends its own section and never edits another's. On a conflict, take `origin/main`'s version, re-append your own section, and commit.
- **Slices C3-1, C3-2, C3-4a, C3-4b, C3-5 and C3-6 never edit `scripts/routing.py` or `runtime/routing.py`.** Only C3-0 does (and later C3-7). When a later slice needs a service change, it is added to C3-0's scope or to a follow-up owned by C3-0's author, never edited in parallel.

### 6.2 DAG

```text
#192 (S3.6c) → C3-0  ledger + routing service
Lane B table ··→ C3-1  launcher core + collector + manifest      (parallel with C3-0)

C3-1          → C3-2  native delegation (shared core) → C3-6 phase-W instructions
C3-1          → C3-4a external choice + OpenRouter proposals
C3-0 + C3-1   → C3-3  OpenCode launcher → C3-4b OpenRouter workers
C3-0 + C3-1   → C3-5  API Usage

[C3-0..C3-6 merged → activation gate passes → canary project added → S3.7 → C.1 → user cutover]
              → C3-7  retirement (shared core)
```

- C3-0 and C3-1 run in parallel.
- After C3-1 merges, C3-2 and C3-4a can start.
- After both C3-0 and C3-1 merge, C3-3 and C3-5 can start.
- C3-4b follows C3-3, and C3-6 follows C3-2.
- No two parallel slices own the same file.
- Lane B's table is a soft dependency: without it, `prepare` falls back to `worker_choices`.

### 6.3 Slices

**C3-0 — Ledger and routing service pre-C.** Depends on #192 merged. Phase W.
- **Owns:**
  - New: `runtime/ledger_versions.py`, `tests/test_routing_legacy_settlement.py`, `tests/test_routing_managed_projects.py`.
  - Code: `runtime/usage_budget.py` (move-out only, net shrink); `runtime/routing.py` (line-neutral); `runtime/subscription_reconcile.py`; `runtime/routing_settings.py` (`managed_projects`, `managed_project(path)` predicate); `scripts/routing.py`.
  - Docs: the S3.6c section of `clients/opencode.md` (first owner).
  - Existing tests: `tests/test_routing_subscription_reconcile.py`, `tests/test_routing_funding_capacity.py`, `tests/test_routing_command.py`, `tests/test_routing_gates.py`, `tests/test_routing_settings.py`. It also owns these, which its changes break:
    - `tests/test_routing_reconcile.py` (771) and `tests/test_routing_accounting_blocks.py` (332), which assert `STATE_VERSION == 4`;
    - `tests/test_usage_budget.py` (458, 498, 506), which assert the migrated `version` through `budget.STATE_VERSION`, now in `ledger_versions.py`;
    - `tests/test_routing_accounting_roots.py` (274), which builds an unpinned workflow `Service`.

    C3-7 later owns `tests/test_usage_budget.py` in sequence, when it removes `Ledger.begin`.
- **Does:**
  - state v5 and its downgrade guard ([4.6](#46-ledger-version));
  - window-debit clearing only in one-shot `prepare`, never under `--serve` (the #192 review): the line-neutral `runtime/routing.py` change passes a `clear_debits` flag from the service into `admit` instead of always calling `clear_debits`;
  - the one-shot `drop_debit{key, marker, event_id}`, with a `status` marker like `resolve_block legacy`;
  - the one-shot `settle_legacy_call` ([4.4](#44-settle-outstanding-legacy-work));
  - `status.ledger.legacy_outstanding`;
  - `project_required` for an unpinned workflow-bound `prepare` (tests that relied on an unpinned workflow `prepare` are updated to pin, with `project` equal to the locator's `cwd`);
  - each line numbered above is the one at `a824819`.
  - the observation seam ([3.3](#33-observation-collector));
  - `managed_projects` as a one-shot setting with the activation gate ([4.2](#42-activation-gate));
  - the `legacy_route_retired` code constant.
- **Tests:**
  - v4 code refuses a v5 ledger;
  - a `--serve` `prepare` with a qualifying window leaves the debit;
  - `drop_debit` with a stale marker is refused;
  - `settle_legacy_call` for each state and evidence kind, zero cost `evidence_invalid`, `over_bound` refused, 15 racing processes apply one effect, `--serve` refuses it;
  - an unpinned workflow `prepare` is `project_required`;
  - a managed-project `prepare` with `observations` is `observations_not_accepted`, and without a collector `observations_unavailable`;
  - `set managed_projects` is refused under `--serve` and refused with `managed_path_incomplete` while any capability's self-check is missing or failing (the self-checks run as in [4.2](#42-activation-gate)); removal is always allowed;
  - the capacity worst case is recomputed.
- **Markers:** `test_a_workflow_bound_prepare_requires_the_project_pin`.
- **Shared core:** no. **Size:** L (about 600 lines with tests).

**C3-1 — Launcher core, collector and route manifest.** No merge dependency. Phase W. It reads C3-0's `routing_settings.managed_project` when present and treats its absence as "not managed", which fails closed. Its tests patch that predicate.
- **Owns:**
  - New code: `runtime/routing_launch.py` (preflight, request builders, its refusal codes, entitlement record, recovery hints, `readiness()`); `runtime/routing_observations.py` (`collect`).
  - New data: `runtime/routing_routes.json`, created from the capability matrix. Its first content waits on user decision 5; until then it lists no qualified route, so the activation gate fails closed.
  - New tests: `tests/test_routing_launch.py`, `tests/test_routing_observations.py`.
  - The [source seam](#33-observation-collector) and `routing_launch.managed_self_check`.
  - The body of `managed_project_fixture` in `tests/test_smart_routing_features.py`, between its owner comments only ([6.4](#64-c2-marker-map-and-pr-197-amendments)).
- **Never edits** `scripts/routing.py` or `runtime/routing.py`.
- **Does:** [3.1](#31-what-a-launcher-must-do) steps 1–6, the collector and the entitlement rule, as host-neutral functions. Hosts plug in catalog, dispatch-transport and accounting callbacks. Steps 1–6 cover: pin check with `project_required`, preflight, `workflow_required`, profile from canonical data, catalog check.
- **Tests:**
  - every preflight item is refused;
  - a planned job without `workflow` is refused;
  - the collector reads no secret (credential resolver patched to fail) and makes no network call;
  - an unknown manifest route is excluded, and a `not_entitled` route stays excluded until cleared;
  - `readiness()` reports enabled and ready separately, with no inference.
- **Markers:** none, but replacing the helper's sentinel body is what lets C3-2, C3-4a and C3-4b turn their markers into passes. The behaviour checked by `test_enablement_is_reported_separately_from_readiness` and the other C.2a and C.2e contracts already passes on `main`, and C3-1 must keep it passing.
- **Shared core:** no. **Size:** L (about 600 lines with tests).

**C3-2 — Native delegation and review wiring.** Depends on C3-1. Phase W.
- **Owns:**
  - New: `runtime/delegation_routing.py` (with `managed_self_check`), `tests/test_delegation_routing.py`.
  - Line-neutral hooks: `runtime/delegation.py` (`_prepare_native`, `_discovery`, `prepare_continuation`, `reconcile_assignment`) and `runtime/evidence.py` (`prepare_review`).
  - `runtime/workstreams.py` (`consume_dispatch` calls `dispatch`).
  - **Not** `scripts/workflow.py`: the route travels inside delegation's reply dict, which `workflow.py` already prints unchanged.
- **Does:** for a managed project:
  - `selection` gains the mode `routed`, in which the coordinator supplies no model;
  - delegation calls the routing service in-process with `workflow` and its `attempt_id`, and the reply carries `route{model, effort, attempt_id}`;
  - the existing catalog and guidance validators check the admitted strategy;
  - an explicit user choice becomes `assignment.choice`;
  - the routing admission is the only record of the requested model: `binding.requested_model` stays null in this mode, and `observe` compares observed settings with the admission through a read-only accessor;
  - continuations get a fresh admission, and reviewers get the independence constraint;
  - Codex is an advisory host.
- **Tests:**
  - one task graph;
  - an unchanged continuation reuses its profile and child but gets a new admission;
  - resource and funding gates bind the same job revision;
  - a dependency reopened after `prepare` sends nothing;
  - an advisory host never gets a dollar route;
  - an unmanaged project keeps today's `selection` behaviour.
- **Markers:** `test_native_worker_admission_calls_routing_prepare_once`, `test_native_worker_admission_is_bound_to_its_workflow_job`.
- **Shared core:** **yes** (`runtime/delegation.py`, `runtime/evidence.py`), so the full suite runs. **Size:** L.

**C3-3 — OpenCode production launcher.** Depends on C3-0 and C3-1. Phase W.
- **Owns:**
  - New: `runtime/opencode_launcher.py` (with `managed_self_check`), `scripts/opencode_launch.py` (JSON CLI), `tests/test_opencode_launcher.py` (a fake server, and the real `routing.py` on temporary stores).
  - The qualification harness: `tests/qualification/canary.py`, `tests/qualification/pilot_env.py` and their tests. In canary mode on a managed project it sends no observations ([3.3](#33-observation-collector)). This merges before the S3.7 canary.
  - The launcher section of `clients/opencode.md` (second owner, after C3-0).
- **Does:**
  - the full [handshake](../../../clients/opencode.md#handshake) and [wiring](../../../clients/opencode.md#usage-accounting-and-resync);
  - refuses with `profile_not_enforced`, `host_unhealthy` or `api_funding_unqualified` when they apply;
  - never calls `session.command`;
  - strips credential-like environment names from any process it starts;
  - denies external-directory edits and adds the routing state directory to `protected_paths`;
  - writes rate-limit observations and `not_entitled` marks.
- **Tests:**
  - each refusal happens before any session is created;
  - the token never appears in output or files (scan);
  - a lost acknowledgment records `uncertain`;
  - `descendants` is called before `detach`;
  - `shim_descendants: None` makes an API cost unknown;
  - a static check that no `session.command` call exists.
- **Markers:** none. It keeps `test_routing_success_grants_no_delivery_authority` passing.
- **Shared core:** no. **Size:** L. Live observation is separate and waits on the user.

**C3-4a — External choice and OpenRouter proposals.** Depends on C3-1. Phase W.
- **Owns:**
  - Code: `runtime/external_delegation.py` (adds `constraints(...)`; `select` stays until C3-7); `scripts/openrouter_proposals.py` (with `--managed-self-check`).
  - Docs: `clients/external-delegation.md` (the X0 text, with a managed-project rule); `skills/openrouter-proposals/references/procedure.md`.
  - Tests: `tests/test_external_delegation.py`, `tests/test_openrouter_proposals.py`.
- **Does:** for a managed project, `begin`/`dispatch` run a managed launch on the `gateway` route, with an in-process `check` before the send. `external_delegation.check` and `native_review` stay. `triage` stays advisory and chooses nothing.
- **Tests:**
  - OpenRouter is a candidate only with its own activation and funding;
  - a direct API grant never funds it;
  - proposals gain no file or tool permission;
  - an explicit choice narrows the candidates and never widens them.
- **Markers:** `test_proposal_begin_obtains_one_routing_admission`.
- **Shared core:** no. **Size:** M.

**C3-4b — OpenRouter workers on the managed launcher.** Depends on C3-3. Phase W.
- **Owns:** `scripts/openrouter_workers.py` (with `--managed-self-check`), `skills/openrouter-workers/references/procedure.md`, `tests/test_openrouter_workers.py`.
- **Does:** for a managed project, `prepare` obtains one routing admission and workers launch through `opencode_launcher` with the read-only profile from `configuration`. The V1 `opencode run --pure` launch refuses with `legacy_route_retired`.
- **Tests:**
  - the V1 path is refused for a managed project;
  - the read-only profile is kept;
  - a V1 qualification record does not authorize a V2 editing profile.
- **Markers:** `test_worker_prepare_obtains_one_routing_admission`.
- **Shared core:** no. **Size:** M.

**C3-5 — API Usage on the managed path.** Depends on C3-0 and C3-1. Phase W.
- **Owns:**
  - Code: `runtime/usage_routing.py` (with `managed_self_check`), `scripts/usage_routing.py`, `runtime/usage_codex_host.py`, `runtime/usage_canary.py`, `runtime/usage_canary_host.py`, `scripts/usage_codex_host.js`.
  - Docs: all of `skills/manage-usage-routing/`, including `references/delegation.md`, `first-canary.md` and `codex-host.md`.
  - Tests: `tests/test_usage_routing.py`, `tests/test_usage_live.py`, `tests/test_usage_codex_host.py`, `tests/test_usage_codex_bridge.py`.
- **Does:** for a managed project:
  - API Usage proposals run `prepare` → `dispatch` → `convert_hold` → `openai_api`, with a `check` before the count request and before the generation request, then `record complete`;
  - the `legacy_route_retired` refusal is the first action of every entry point listed in [4.3](#43-stop-legacy-admissions), with `Controller._run`, `_evaluate` and `request_handoff` as backstops;
  - `enable` writes `api_usage_enabled` through the routing command.
- **Tests:**
  - a managed project never reaches `Ledger.begin` through any entry: `usage_routing._run_live`, `usage_codex_host.execute`, `usage_canary.run`, `usage_canary_host.execute`, and `usage_routing.evaluate`/`handoff`/`claim_handoff`. In each test `Ledger.begin` is patched to fail. `usage_settings.controller`, `Controller.configure` and the credential-binding reader are patched the same way, which proves the refusal comes before any settings read or write;
  - the CLI `run` still answers `live_execution_unavailable` (A2a);
  - the spec §10.4 step 4 test ([4.3](#43-stop-legacy-admissions));
  - the existing `live_admission_required`, `credential_unavailable` and OFF-never-resolves-a-credential tests still pass on the new path.
- **Markers:** none. It keeps `test_api_usage_stays_off_until_explicitly_enabled` passing.
- **Shared core:** no. **Size:** M.

**C3-6 — Phase W instructions.** Depends on C3-2. It merges in phase W and is part of the activation gate, so a canary coordinator never gets instructions that contradict the code.
- **Owns:** `policies/03-agent-delegation.md` (a `Managed projects` section: in a managed project, ask a launcher and never choose a model; guidance refresh feeds the strategy table; explicit choices are constraints); `clients/delegation.md` (worker settings, lines 17 and 57); `clients/evidence-review-prepare.md` (line 6); all of `skills/review-my-project/`; `skills/workstreams/` references where they describe worker selection. Entry points keep the 150-word limit.
- **Does:**
  - adds the managed-project rule once, in policy 03, and links to it from the other files;
  - leaves the unmanaged-project text unchanged until C3-7;
  - covers C1 (Claude Code native subagents) through policy 03.
- **Tests:** `tests.test_prepared_instructions` and the affected instruction tests; walkthroughs for Workstreams, API Usage, OpenRouter, Council and project review, reported separately.
- **Markers:** `test_project_review_routes_delegated_reviewers_through_the_routing_command`: the review-my-project skill text names `scripts/routing.py` for delegated reviewers in managed projects.
- **Shared core:** no, but instruction changes reach many tests, so run the affected modules. **Size:** M.

**C3-7 — Retirement.** Depends on all of the above, the C.1 result and the user's cutover decision. Phase R. It is the second owner of every file it edits.
- **Owns (in sequence):**
  - The managed-project branches in the C3-2 to C3-5 files.
  - `runtime/external_delegation.py:select`/`automatic_allowed` and their tests.
  - `Ledger.begin` and the S1-only helpers in `runtime/usage_budget.py`.
  - The A1–A4 entry points.
  - The V1 workers launch.
  - `managed_projects`, the activation gate and caller-supplied `observations` outside `--test-inputs`, in `runtime/routing_settings.py` and `scripts/routing.py`.
  - The phase W conditional text in the C3-4a, C3-5 and C3-6 instruction files.
  - The obsolete tests, plus a new `tests/test_legacy_selectors_retired.py`.
- **Does:**
  - every checkout uses the managed path;
  - the remaining `ready` handoffs become `retired`;
  - the delegation `selection` modes `explicit`/`inherit` are refused under ON; only `choice` remains;
  - `legacy_outstanding` must be empty before merge.
- **Tests:**
  - legacy symbols cannot be imported;
  - the removed CLI actions exit 2;
  - no `NEEDS_C3` marker remains;
  - full suite and `validate.py` pass.
- **Markers:** `test_external_delegation_keeps_no_caller_owned_selector`, `test_usage_routing_cli_no_longer_selects_routes`.
- **Shared core:** **yes**, so the full suite runs. **Size:** M (mostly deletion).

### 6.4 C.2 marker map and PR #197 amendments

PR #197 (head `b0365cc`) has exactly eight `@needs_c3` markers. Lane D amends #197 **before it merges**, in four ways. This list is the contract Lane D writes to.

**A1. Marker helper.** Replace `needs_c3` with `NEEDS_C3(slice_id)`. It counts only an `AssertionError` as an expected failure, so a contract that crashes (an import error, a missing attribute) can never pass as "still waiting on C.3":

```python
SLICES = {'C3-0', 'C3-1', 'C3-2', 'C3-3', 'C3-4a', 'C3-4b', 'C3-5', 'C3-6', 'C3-7'}
_NON_ASSERTION = []

def NEEDS_C3(slice_id):
    assert slice_id in SLICES
    def mark(test):
        @functools.wraps(test)
        def run(self):
            try:
                test(self)
            except (AssertionError, unittest.SkipTest):
                raise
            except Exception as error:
                _NON_ASSERTION.append('%s: %s' % (test.__qualname__, type(error).__name__))
                raise
        run.needs_c3 = slice_id
        return unittest.expectedFailure(run)
    return mark

def tearDownModule():
    if _NON_ASSERTION:
        raise AssertionError('NEEDS_C3 contracts failed without an assertion: ' + ', '.join(_NON_ASSERTION))
```

`unittest` still records the crash as an expected failure, but `tearDownModule` then raises, so the module run fails.

**A2. Slice names.** Each of the eight markers names its slice from the table below.

**A3. Project pin (N1).**
- `C2bWorkstreamsDelegationResources` builds its `Service` pinned, in its own `setUp`, with `project='/work/repo'` (the `cwd` of `gate_tests.locator()`). Its four tests keep passing after C3-0 adds `project_required`:
  - `test_one_task_graph_ready_jobs_admit_dependent_and_conflicting_jobs_wait`;
  - `test_an_unchanged_continuation_reuses_its_profile_but_gets_fresh_admission`;
  - `test_changed_scope_or_brief_invalidates_the_prepared_selection`;
  - `test_resource_and_funding_gates_bind_the_same_job_revision`.
- `test_a_workflow_bound_prepare_requires_the_project_pin` moves to its own class, `C2bProjectPin(gate_tests.GateBase)`, which keeps the base's **unpinned** service.

**A4. Managed-project fixture (N2).** Lane D adds this skeleton now. C3-1 later replaces only the body between the owner comments ([6.1](#61-shared-file-protocol)):

```python
@contextmanager
def managed_project_fixture(test, checkout, routes=()):
    """Make `checkout` a managed project with a qualified test route and fresh window telemetry."""
    # --- managed_project_fixture: body owned by C3-1 ---
    raise AssertionError('managed_project_fixture: needs C3-1 (collector source seam)')
    yield {}  # unreachable until C3-1 replaces this body
    # --- end managed_project_fixture ---
```

The four behaviour contracts wrap their bodies in it:
- `with managed_project_fixture(self, <fixture checkout>, routes=(<the fixture's own explicit choice>,)):`
- the two native ones pass the delegation fixture's repository root and its `selection`;
- `proposal_begin` and `worker_prepare` pass `self.repo` and their request's model and provider.

Until C3-1 merges, the sentinel raises `AssertionError`, so the markers stay expected failures under A1.

The body C3-1 writes must:
1. Raise `AssertionError` naming the missing slice if `runtime.routing_settings.managed_project` (C3-0) or `runtime.routing_observations.default_source` (C3-1) is absent. Never let an `AttributeError` escape.
2. Patch, with cleanups registered on `test`:
   - `routing_settings.managed_project` to answer true only for `checkout` (by realpath);
   - `routing_observations.default_source` to return a fixture source whose `manifest()` lists the module's `CHOICE` route plus every entry of `routes`, each as `status: qualified` and `billing_class: included_subscription`, and whose `windows(pool, now)` returns one fresh, explicitly fixed window (`id` `5h`, `limit` 1000, `used` 100, `length` 18000, `reset_at` = now + 3600, `rolling: False`, observed at `now`).
3. Using the routing command's own `set` ops, on the default settings and ledger paths under the unit's private `HOME`, write `worker_choices` (`implementer` and `reviewer`: those routes) and `authority_required=true`, so the C3-1 preflight passes.
4. Make no network, model, host or secret-store call, and write nothing outside the unit's private `HOME` and the test's temporary directory.
5. Yield `{route, settings, ledger}` for assertions.

After C3-1 merges, those four contracts fail by assertion only (no routing admission yet), until C3-2, C3-4a and C3-4b each make theirs true and remove only their own markers.

| Test | Slice | What makes it true |
|---|---|---|
| `test_a_workflow_bound_prepare_requires_the_project_pin` | C3-0 | The routing service refuses a workflow-bound `prepare` without `--project` with `project_required`. |
| `test_native_worker_admission_calls_routing_prepare_once` | C3-2 | Managed-project native `prepare` calls the routing service in-process exactly once, so `runtime.routing.admit` runs once. |
| `test_native_worker_admission_is_bound_to_its_workflow_job` | C3-2 | That call carries `workflow` built from the planned job (`job_id` `edit`) and the delegation `attempt_id`. |
| `test_proposal_begin_obtains_one_routing_admission` | C3-4a | Managed-project proposal `begin` obtains one routing admission on the `gateway` route. |
| `test_worker_prepare_obtains_one_routing_admission` | C3-4b | Managed-project `openrouter_workers.prepare` obtains one routing admission before writing the run. |
| `test_project_review_routes_delegated_reviewers_through_the_routing_command` | C3-6 | The review-my-project skill text routes delegated reviewers through `scripts/routing.py` (by way of the managed-project rule). |
| `test_external_delegation_keeps_no_caller_owned_selector` | C3-7 | `runtime.external_delegation.select` is deleted. |
| `test_usage_routing_cli_no_longer_selects_routes` | C3-7 | `scripts/usage_routing.py evaluate` is a usage error (exit 2). |

Each slice removes only the marker lines naming it ([6.1](#61-shared-file-protocol)).

## 7. Open decisions

### 7.1 For the user

1. **Cutover.** Whether and when phase R starts after C.1 passes, and whether more projects join `managed_projects` before cutover.
2. **Default ON scope (C.4).** Whether default ON at cutover covers advisory hosts (Codex and Claude Code native) for automatic subscription selection, or whether those hosts use only `worker_choices` until a pre-transmission hook exists. Recommendation: allow automatic subscription selection there, labelled `advisory`, because their funding is window-only.
3. **OpenRouter workers gap.** Accept that OpenRouter workers in managed projects are unavailable until C3-4b merges and the `gateway` route on OpenCode is qualified live.
4. **API-funded OpenCode qualification.** Run the S3.6 positive and negative descendants runs with real API costs; this lifts `api_funding_unqualified`.
5. **Route manifest.** Accept the route manifest's classifications, taken from the capability matrix, as the collector's source while observed billing for several routes still waits on you. Until then the manifest lists no qualified route, and no project can be activated.
6. **First window telemetry.** The collector learns a subscription window only from an attempt that has already settled, so the first managed launch on a route has no fresh window, and `prepare` stays `pending`. The routing decision needs one way to seed it. Recommendation: a one-shot operator op `seed_window{pool, window, used, limit, reset_at, length, event_id}`. It is refused under `--serve`, recorded with `source: operator`, fresh for the usual 900 s and replaced by the first host observation. The alternative is a single admission per route at concurrency 1 that ignores utilization; I do not recommend it, because it bypasses the allocator's window check.

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
