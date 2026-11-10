# Smart Routing C.3 — caller wiring and selector retirement design

- **Version:** 1.0 draft
- **Date:** 2026-11-10
- **Status:** Design only. No code, model call, OpenCode server or live run was used to write it. Open user decisions are in [section 7](#7-open-decisions).
- **Plan task:** [C.3 Replace callers and retire selectors](../plans/2026-11-08-smart-routing.md#task-c3-replace-callers-and-retire-selectors). The plan owns task status; this document owns how C.3 is built.
- **Parent design:** [Smart Routing spec v1.5](2026-11-08-usage-routing-opencode-subscriptions-design.md) §3, §4, §8, §10.4 and Appendix A.

This document says which code paths choose workers today, what each must call instead, the contract every launcher follows, how the old selectors are retired, and how C.3 splits into pull requests that separate workers can build at the same time. It links to existing contracts rather than restating them: the [routing command](../plans/2026-11-08-smart-routing.md#task-s31-single-routing-command) (S3.1/S3.4), [fresh gates](../plans/2026-11-08-smart-routing.md#task-s35-fresh-gates-at-dispatch-rf3) (S3.5), [settlement](../plans/2026-11-08-smart-routing.md#task-s36-lineage-settlement-and-retention) (S3.6), the [OpenCode handshake, plugin RPC and accounting](../../../clients/opencode.md#handshake) and PR #192's `reconcile_subscription` op (S3.6c, `clients/opencode.md#uncertain-subscription-holds-s36c` once merged).

## 1. Terms

Each term is defined here once and used with this meaning below.

| Term | Meaning |
|---|---|
| **Selector** | Code that decides which model, effort, provider or funding route a delegated worker uses. |
| **Legacy selector** | A selector other than `scripts/routing.py prepare`: the five listed in [section 2](#2-inventory-of-selection-paths). |
| **Legacy writer** | Code that opens new financial exposure in the ledger without a routing admission: today only S1 `Ledger.begin`, called by `usage_routing.Controller._run`. |
| **Launcher** | Trusted code that runs one delegated attempt end to end through the routing command. A coordinator model, a worker or a skill text is never a launcher; they ask a launcher. |
| **Managed launch** | One attempt run by a launcher under the [launcher contract](#3-launcher-contract). |
| **Enforced host** | A host where every model request of the attempt passes `check` before it is sent: OpenCode with the shim and an `enforced` probe, and in-process HTTP launches (API Usage, OpenRouter proposals) where the launcher is the only sender. |
| **Advisory host** | A host where routing chooses and accounts but cannot block a send before it happens: Codex native subagents today (no pre-transmission hook). |
| **Dollar route** | A route whose billing class spends dollars: direct API (`api_grant`, `api_prepaid`) or `gateway` (OpenRouter). |
| **Observation collector** | Trusted code that builds the `observations` snapshot for `prepare` from local records only ([section 3.3](#33-observation-collector)). |
| **Managed project** | A checkout on the routing settings' `managed_projects` list. Between wiring and cutover only managed projects use the new path ([section 4.1](#41-two-phases)). |

## 2. Inventory of selection paths

Every path that chooses or funds a delegated worker on `main` at `4f16c36`. "Target" is the call sequence after C.3; the steps are defined in [section 3.2](#32-call-sequence). Line numbers are for that commit.

| ID | Path (file:function) | What it decides today | Target sequence | Slice |
|---|---|---|---|---|
| N1 | `runtime/delegation.py:_prepare_native` (2055) via `_discovery` (1461), reached from `scripts/workflow.py` `delegation prepare` (279) | Validates a coordinator-supplied `selection{model, effort, mode: explicit/inherit, profile_ids}` against the observed Codex catalog and cached guidance; the coordinator chooses the model from guidance. Reserves claims and resources; returns spawn settings. | Delegation reserve (unchanged) → `prepare` with `workflow` and the delegation `attempt_id` → catalog check of the admitted strategy → `dispatch` at the last pre-spawn step → spawn with explicit model and effort → `observe` → `record complete` from `reconcile`. Advisory host. | C3-2 |
| N2 | `runtime/evidence.py:prepare_review` (5691) → `_prepare_native(role='review')` | Same as N1 for an independent reviewer; independence is checked by evidence, not by route. | As N1 with role `review` (routing role `reviewer`) and an independence constraint: the reviewer's route differs from the implementer's admitted route when the review policy requires it. | C3-2 |
| N3 | `runtime/delegation.py:prepare_continuation` / `consume_continuation` (3079, 3036) | Reuses a child for unchanged work; the continuation inherits the child's settings. | Reuses the child and its profile; gets a **fresh** `prepare` and `dispatch` (spec F11). Changed scope or role gets a new profile. | C3-2 |
| N4 | `runtime/workstreams.py:consume_dispatch` (201), `validate_admission` (183) | Workstreams launch gates for a planned job; no model choice. | Unchanged gates; becomes the "last pre-spawn step" where `dispatch` runs for Workstreams jobs. | C3-2 |
| N5 | `runtime/delegation.py:reserve_coordinator` (1947) | Reserves coordinator resources; no model choice. | Never routed (`coordinator_not_routed`). Its usage may join a root through `record observed`. | none |
| X1 | `runtime/external_delegation.py:select` (95), `automatic_allowed` (90) | Picks native, local preference or an external route from explicit choice, saved preference or the only eligible route. **Legacy selector.** | Retired. Explicit choice becomes `assignment.choice`; local preference, enabled scopes and `automatic: false` become `constraints`. | C3-4a, removal C3-7 |
| X2 | `runtime/external_delegation.py:check`, `prepare`, `effective_permission`, `native_review` | Source scope, permission, destination and packet provenance; review independence. Not a selector. | Kept unchanged as constraints and pre-send checks inside the launcher. | none |
| A1 | `runtime/usage_routing.py:Controller.evaluate`/`_evaluate` (441, 408) | Whether an API Usage proposal is `eligible`, `native` or `paused`. **Legacy selector.** | Retired. API Usage candidates come from the observation collector; `prepare` decides. | C3-5, removal C3-7 |
| A2 | `runtime/usage_routing.py:Controller._run` (574), `_run_live` (560); `runtime/usage_codex_host.py:execute` (207); `runtime/usage_canary.py:run` (70) | Opens S1 runs with `Ledger.begin` and sends through `openai_api`. **Legacy writer.** | `prepare` → `dispatch` → `Ledger.convert_hold` → `openai_api` send with a `check` before each request → `record complete` with the measured cost. | C3-5, removal C3-7 |
| A3 | `runtime/usage_routing.py:Controller.request_handoff`/`claim_handoff` (449, 495); `scripts/usage_routing.py` `evaluate`, `run`, `handoff`, `claim_handoff` | Queues paused API work for native dispatch in a ledger `handoffs` table. | Retired: a paused assignment stays `pending` in `prepare` with its reason, and the coordinator re-prepares it. Existing handoff records stay as history ([section 4.3](#43-settle-outstanding-legacy-work)). | C3-5, removal C3-7 |
| O1 | `scripts/openrouter_proposals.py:triage` (154), `run` `begin`/`dispatch` (321) | Advisory candidate list from cached OpenRouter metadata; the caller picks a model; proposal store records attempts. | Discovery metadata feeds the collector and the evidence compiler; `triage` stays an advisory report with no choice. `begin`/`dispatch` become a managed launch on the `gateway` route, keeping `external_delegation.check`, endpoint pinning and `native_review`. | C3-4a |
| O2 | `scripts/openrouter_workers.py:prepare` (101), `_execute` (330) | Caller-chosen OpenRouter model; launches `opencode run --pure` (OpenCode V1, `plugin: []`, so no shim). | The V1 launch stops admitting. Workers run as OpenCode managed launches (C3-3) keeping the read-only permission profile from `configuration` (179). | C3-4b, removal C3-7 |
| R1 | `scripts/routing.py` `prepare`/`check`/`dispatch`/`record`/`settle` | The target decision and admission path. | Called only by launchers. | C3-0 (pre-C ops) |
| Q1 | `tests/qualification/opencode_live.py:Run` | Test-only launcher for qualification and the pilot. | Stays test-only. Production code never imports `tests/`; C3-3 writes its own launcher and the harness may later reuse it. | none |

Feature checkout rows: Council (F08) is not in this repository, so C.3 has no Council caller; its rule (explicit invocation only, members and independence as hard constraints) is carried by the launcher contract and checked by C.2d. Review my Project (F09) delegates through N1/N2.

## 3. Launcher contract

### 3.1 What a launcher must do

1. **Pin the project.** Start `scripts/routing.py` with `--project <checkout root>` (realpath) for every op, and refuse without it (`project_pin_required`). In-process use constructs `Service(project=…)` the same way. `status` must show `project_pinned: true`.
2. **Preflight from `status`** before the first `prepare` of a session, and again when `status` changes revision:
   - `authority_required: true`, else `authority_not_required`;
   - `unstamped_open_holds: 0`, else `unstamped_holds_open`;
   - no `blocked_organizations`, `uncertain_dollar` or `cost_unknown` entries and `reconcile_hint: null` when the assignment could use a dollar route, else `ledger_reconcile_pending` (subscription-only assignments may proceed);
   - in phase W only: the checkout is a managed project, else the caller keeps its current path (`project_not_managed`, not an error).
3. **Bind planned work.** Every Workstreams or plan job is prepared with `workflow{client, cwd, session_id, task_id, job_id, attempt_id}` after delegation's own reservation, using that reservation's `attempt_id` as the routing `attempt_id`. A planned job without `workflow` is refused (`workflow_required`). Only explicit, unplanned delegation may omit it; it is then counted in `unbound_open_holds`.
4. **Profile from canonical data.** `job` is `delegation.routing_job(scope, locator)` plus the coordinator's optional `profile`. The launcher never adds fields the canonical job lacks.
5. **Build observations with the [collector](#33-observation-collector)**, never from request input.
6. **Validate the admitted strategy against the host's actual catalog** (N1's existing catalog check, the OpenCode provider listing, the OpenRouter endpoint) and against the [entitlement rule](#34-listed-is-not-entitled). A mismatch sends nothing, records `not_dispatched` and refuses with `catalog_mismatch` or `route_not_entitled`.
7. **Dispatch immediately before creating anything on the host**, keep the token in memory only, and pass it only to the shim's `routing.attach` (OpenCode) or to the in-process `check` before each send. Never log, store or return it.
8. **Account and settle** with the host's re-read state (OpenCode: the [wiring order](../../../clients/opencode.md#usage-accounting-and-resync), including `routing.descendants` before `detach`), sending a stable `event_id` per lifecycle event and `usage` with `cost_usd_micros` on `complete` for every dollar route.
9. **Lost acknowledgment:** if the host launch cannot be confirmed, `record uncertain` and stop. Recovery is the operator's ([section 3.5](#35-operator-recovery)).

### 3.2 Call sequence

```text
preflight(status) ─► [delegation reserve → attempt_id]
   ─► prepare{assignment, job, observations, constraints?, workflow}
        pending → report reason, hold nothing, stop
        blocked → report reason, stop
   ─► host catalog + entitlement check ── fail → record not_dispatched, stop
   ─► (OpenCode) routing.health ok
   ─► dispatch{attempt_id, request} → authority, session_id, message_id
   ─► (OpenCode) routing.attach → create session + prompt with derived ids
      (in-process HTTP) convert_hold for dollar routes → check → send, per request
      (Codex native) return route to the coordinator, which spawns with explicit model/effort
   ─► idle → re-read → (OpenCode) routing.descendants → opencode_accounting
   ─► record complete{usage} → (OpenCode) routing.detach → settle (read-only report)
```

`record observed` joins coordinator or auxiliary usage to an open root when observable.

### 3.3 Observation collector

`prepare` trusts the `observations` it is given (billing class, health flags, windows, credentials). Today the qualification harness builds them by hand. C.3 adds one collector, `runtime/routing_observations.py`, that reads only local trusted records and never a secret, a network or a model:

| Fact | Source |
|---|---|
| Candidate routes and billing class | A committed route manifest (`tests/fixtures/smart-routing/capabilities.json` from plan Task 0.3, moved to a package path the runtime may read) built from the [capability matrix](../../smart-routing-capability-matrix.md). A route absent from it, or `unverified`/`unsupported`, is not a candidate. |
| Subscription windows | Rate-limit observations the launcher stored after settlement (`opencode_accounting.rate_limit_observation`), in a private file next to the routing settings, with their observation time. Missing telemetry gives `fresh: false`, so the route is reported enabled-but-blocked (P0.2 open for ChatGPT). |
| Dollar organizations | The ledger's organizations and balances (existing `observe` paths). |
| Credentials | Binding readiness metadata from `usage_credentials` and `openrouter_settings` (state, scope, reference); the secret is never resolved. |
| OpenRouter | `openrouter_settings.admission` activation, scope and funding verification, mapped to the `gateway` class with `integration_enabled`. |
| Entitlement | The entitlement record ([section 3.4](#34-listed-is-not-entitled)). |

### 3.4 Listed is not entitled

Plan follow-up #182. A route is a candidate only when the manifest lists it as qualified for that harness and account kind. A catalog listing alone never makes it one. When a provider refuses an admitted route as unavailable for the account (OpenCode surfaces OpenAI's `model_not_found` as an HTTP 400), the launcher records the attempt's outcome normally and writes a `not_entitled` mark for that (route, account) to the entitlement record. The collector then excludes it until the user clears the mark with a local command. The launcher never retries other routes automatically after such a refusal.

### 3.5 Operator recovery

The operator ops are one-shot only and never reachable from `--serve` or `record` ([S3.6b-2 rules](../plans/2026-11-08-smart-routing.md#task-s36-lineage-settlement-and-retention)). The launcher prints which one applies; it never runs one itself.

| Situation | Op |
|---|---|
| Uncertain dollar hold | `reconcile_uncertain` |
| Uncertain subscription hold | `reconcile_subscription` (PR #192) |
| `cost_unknown` hold | `resolve_cost_unknown` |
| Unconfirmed overrun | `resolve_overrun` |
| Over-bound S1 call, legacy block | `resolve_block` |
| Wrongly kept window debit | `drop_debit` (new, C3-0) |
| Outstanding legacy S1 call | `settle_legacy_call` (new, C3-0) |

### 3.6 Refusal reasons

New fixed codes the launcher returns, in addition to the routing command's own reasons, which it passes through unchanged: `project_pin_required`, `authority_not_required`, `unstamped_holds_open`, `ledger_reconcile_pending`, `project_not_managed` (phase W only), `workflow_required`, `catalog_mismatch`, `route_not_entitled`, `host_unhealthy` (`routing.health` not `ok`, including any RPC error), `profile_not_enforced` (OpenCode probe `advisory`), `dollar_route_needs_enforced_host`, `api_funding_unqualified`, `attach_failed`, `launch_unconfirmed` (after `record uncertain`). Every refusal before `dispatch` sends nothing and leaves no open hold beyond what `prepare` made, which the launcher closes with `record not_dispatched`.

### 3.7 What a launcher never does

- Run a project command (TUI or `session.command`) inside a managed session. A command's `` !`cmd` `` runs outside the `shell` permission and keeps credential-like variables (observed 9 November 2026), so this rule is the protection.
- Send on a dollar route from an advisory host (`dollar_route_needs_enforced_host`). Advisory hosts get subscription routes and `worker_choices` only.
- Send on an API-funded OpenCode route before the S3.6 [required positive and negative runs](../../../clients/opencode.md#plugin-rpc) have been observed with real costs (`api_funding_unqualified`). It always passes `shim_descendants`, or `None` when the call failed.
- Read a key, print a token, store a prompt or brief in the ledger, or export anything (observability keeps its own opt-in).
- Change the coordinator's model, effort or session settings, or route the coordinator.
- Grant merge, push, cleanup, title adoption or acceptance. A routed `complete` is not acceptance; acceptance comes only from `delegation.routing_acceptances`.
- Call an operator op, or retry a lost acknowledgment with a second `dispatch`.
- Fall back to a legacy selector when `prepare` is pending or blocked.

## 4. Rollout and legacy retirement

### 4.1 Two phases

A14 rules out shadow mode, and the canary needs real wiring (plan S3.7). C.3 therefore lands in two phases:

- **Phase W (wiring).** Slices C3-0 to C3-5 add the managed path to every caller. A caller uses it only for a managed project; every other checkout keeps its current selector, as plan S2.7 and S3.7 already allow. `managed_projects` starts empty; the canary allowlist is written there. Routing is fully active for a managed project: there is no decision-only mode.
- **Phase R (retirement).** After C.1 passes and the user decides on cutover, C3-6 and C3-7 remove the managed-project branch, every legacy selector and the legacy writer, and every checkout uses the managed path.

During phase W two code paths open exposure in one ledger: routing admissions and, for unmanaged projects, S1 `begin`. Both run in the one `Ledger` class and its transactions, so caps and headroom stay shared. The legacy writer is removed in C3-7; no compatibility alias remains after it.

### 4.2 Stop legacy admissions

Per project in phase W: each legacy entry point (A1, A2, A3, X1, O2) refuses a managed project with `legacy_route_retired` before reading settings. Globally in phase R: the entry points are deleted. `scripts/usage_routing.py` keeps only `status`, `configure`, `enable`, `disable` and `quote` (setup and observation, which the API Usage skill still needs); `evaluate`, `run`, `handoff` and `claim_handoff` go. There is no alias under the old names.

### 4.3 Settle outstanding legacy work

Before C3-7 merges, `status` must show `legacy_outstanding` empty (C3-0 adds it): open S1 runs and calls (`reserved`, `dispatching`, `unresolved`, `over_bound`) and `legacy_block` organizations. It also lists `ready` handoffs for information; they hold no exposure and do not block.

- Over-bound calls and legacy blocks: existing `resolve_block`.
- Other unresolved S1 calls have no settlement today (`Ledger.reconcile` only reports them), so C3-0 adds the one-shot `settle_legacy_call{run_id, phase, evidence, event_id}`: `receipts` with a cost of at least 1 micro-USD charges as reported, `charge_reservation` charges the maximum; nothing is released without spend, as for `reconcile_uncertain`.
- `handoffs` in `ready` were never claimed and hold no exposure. Unmanaged projects still use them in phase W, so C3-7 (not a migration) marks the remaining ones `retired` and keeps the records. `claimed` ones belong to native delegation and stay as history.

### 4.4 Preserve history and ownership

Settled charges, cycle caps, S1 runs, handoff records, event rows and root aggregates stay under the existing retention rules (spec §11). Retirement deletes code, never ledger records. Unresolved ownership (open holds, uncertain attempts, delegation assignments) blocks the affected cutover step until it is settled, as spec §10.4 requires.

### 4.5 Ledger version

PR #192 adds `window_debits` to the ledger record without a state version change, so code from before #192 would ignore a debit. C3-0 raises `STATE_VERSION` to 5 so older code refuses the ledger (`ledger_identity_changed`), as v4 did for settled charges. `runtime/usage_budget.py` is at its 1,616-line ceiling, so C3-0 first moves the version migration (`STATE_VERSION` and the 1→4 steps) into a new `runtime/ledger_versions.py` and adds the 4→5 step there. C3-5 later removes `Ledger.begin` and its S1-only helpers, which frees more room.

## 5. Rollback, failure modes and security review

### 5.1 Rollback

- **Explicit OFF.** `scripts/routing.py set smart_routing=false` (no model call). Unsent admissions of the old revision are refused; dispatched work finishes; later launches use `worker_choices` through the same allocator. This is the only kill switch, in both phases.
- **Previous strategy table.** Lane B's `scripts/routing_table.py refresh` must keep the previous verified table; rollback selects it with the same command. A missing or unreadable table already falls back to `worker_choices` (`table_unavailable`).
- **Phase W only:** removing a project from `managed_projects` returns it to its legacy selector for new launches. Open holds keep their rules; they are settled, never dropped.
- **No rollback to legacy selectors after C3-7.** Reverting C3-7's code is a normal revert PR; it does not restore removed ledger behaviour, because C3-7 removes no record and v5 is kept.

### 5.2 Failure modes

| Failure | Behaviour |
|---|---|
| Routing command missing, busy or failing | Launcher refuses (`ledger_busy`, `ledger_unavailable`, `internal_error` passed through); nothing sent. Interactive sessions are unaffected. |
| Strategy table missing or malformed | `table_unavailable`; `worker_choices` used. Never blocks (RF8). |
| Observations stale or missing | Affected routes infeasible with a reason; the assignment is `pending`, not sent elsewhere. |
| Workflow job changed after `prepare` | `dispatch` refuses with the S3.5 reason; hold stays `admitted`; launcher records `not_dispatched`. |
| Host catalog lacks the admitted strategy | `catalog_mismatch`; `not_dispatched`. |
| Provider refuses route for the account | Attempt settles normally; `not_entitled` mark; no automatic retry. |
| Plugin restart during an attempt | Launcher re-attaches with the same token ([handshake](../../../clients/opencode.md#handshake)). |
| Lost acknowledgment | `record uncertain`; operator op per [section 3.5](#35-operator-recovery). |
| Coordinator never spawns a dispatched Codex native attempt | Delegation `reconcile` settles it (`record complete`, subscription release). A dollar route cannot reach this state on an advisory host. |
| Settings change mid-flight | S3.1 rules: unsent admissions refused, dispatched work continues. |
| Ledger above `SOFT_RECORD` | `prepare` commits nothing; window debits never clear (fail closed). |

### 5.3 Security review points

Each slice's reviewer checks the points its files touch:

1. **Observation trust.** No request field, coordinator text or worker output reaches `observations`, `constraints` widening, `workflow` or the manifest. Only the collector writes observations.
2. **Token handling.** The authority token appears only in the `dispatch` response, launcher memory, `routing.attach` and in-process `check`; never in logs, errors, files, `status` or child process environments.
3. **Project pin.** Every production `Service` has `project`; a `cwd` outside the pinned checkout is refused.
4. **Operator ops.** No launcher code path calls an operator op; `--serve` still refuses them.
5. **Credential isolation.** Launchers strip credential-like environment names from host processes they start, and never run project commands in managed sessions.
6. **Funding boundaries (RF1, RF6).** Smart Routing ON never enables API Usage, OpenRouter or observability; a direct grant never funds a gateway; `purchased_usage` is never subscription headroom; advisory hosts never get dollar routes.
7. **Legacy refusal ordering.** `legacy_route_retired` is checked before any legacy code reads settings or credentials.
8. **History preservation.** No migration or retirement deletes runs, charges, caps, events or open holds.
9. **Review independence (F13).** Reviewer constraints come from the review policy, not from the coordinator's request.

## 6. Slice plan

Each slice is one PR. "Owned files" may be edited only by that slice; anything else is read-only for it. Line ceilings from `scripts/validate.py` apply: `runtime/delegation.py` (3,369), `runtime/evidence.py` (8,505), `runtime/routing.py` (1,506) and `runtime/usage_budget.py` (1,616) are at their ceilings, so slices put new code in new modules and keep edits to those files line-neutral or shrinking.

### 6.1 DAG

```text
#192 (S3.6c) ──► C3-0 ledger pre-C ───────────────┐
                                                   ├──► C3-5 API Usage wiring ──┐
Lane B table ··► C3-1 launcher core ──┬────────────┘                            │
                                      ├──► C3-2 native delegation (shared core) ─┤
                                      ├──► C3-3 OpenCode launcher ──► C3-4b ─────┤
                                      └──► C3-4a external + proposals ───────────┤
                                                                                 ▼
                  [phase W done → canary S3.7 → C.1 → user cutover decision]
                                                                                 ▼
                                     C3-6 instructions ──► C3-7 retirement (shared core)
```

C3-0 and C3-1 run in parallel. After both merge, C3-2, C3-3, C3-4a and C3-5 run in parallel (disjoint files); C3-4b follows C3-3. C3-6 can be written during phase W but merges only in phase R, just before C3-7. Lane B's table is a soft dependency: without it `prepare` falls back to `worker_choices`.

### 6.2 Slices

**C3-0 — Ledger pre-C requirements.** Depends on #192 merged.
- Owns: new `runtime/ledger_versions.py`; `runtime/usage_budget.py` (move-out only, net shrink); `runtime/subscription_reconcile.py`; `scripts/routing.py` (op table, `status` fields); new `tests/test_routing_legacy_settlement.py`; `tests/test_routing_subscription_reconcile.py`; `tests/test_routing_funding_capacity.py`.
- Does: state v5 and its downgrade guard ([4.5](#45-ledger-version)); window-debit clearing only in one-shot `prepare`, never under `--serve` (#192 review); one-shot `drop_debit{key, marker, event_id}` with a `status` marker like `resolve_block legacy`; one-shot `settle_legacy_call`; `status.ledger.legacy_outstanding`. The 4→5 step changes no run, hold or handoff.
- Tests: v4 code reading a v5 ledger refuses it; `--serve` `prepare` with a qualifying window leaves the debit; `drop_debit` stale marker refused; `settle_legacy_call` receipts and reservation-floor paths, zero cost `evidence_invalid`, 15 racing processes one effect, `--serve` refusal; capacity worst case recomputed.
- C.2 contracts: none directly; prerequisite for C3-5 and C3-7.
- Shared core: no. Size: M (about 350 lines with tests).

**C3-1 — Launcher core and observation collector.** No code dependency.
- Owns: new `runtime/routing_launch.py` (preflight, request builders, refusal codes, `managed_project`, entitlement record, recovery hints, a `readiness()` report); new `runtime/routing_observations.py`; `runtime/routing_settings.py` (`managed_projects` list, validated realpaths, default empty); route manifest moved to a runtime-readable path with its loader; new `tests/test_routing_launch.py`, `tests/test_routing_observations.py`.
- Does: [3.1](#31-what-a-launcher-must-do) steps 1–6, the collector and the entitlement rule as host-neutral functions. Hosts plug in catalog, dispatch-transport and accounting callbacks.
- Tests: missing `--project` refused; each preflight item refused; planned job without `workflow` refused; collector ignores request-supplied facts; collector reads no secret (credential resolver patched to fail) and makes no network call; unknown manifest route excluded; `not_entitled` excludes until cleared; `readiness()` reports enabled and ready separately with zero inference.
- C.2 contracts: C.2a readiness-separate-from-enablement and zero-inference/zero-secret status; C.2c "Smart Routing ON enables nothing else" at the collector level; C.2e "no export".
- Shared core: no. Size: L (about 600 lines with tests).

**C3-2 — Native delegation and review wiring.** Depends on C3-1.
- Owns: new `runtime/delegation_routing.py`; line-neutral hooks in `runtime/delegation.py` (`_prepare_native`, `_discovery`, `prepare_continuation`, `reconcile_assignment`) and `runtime/evidence.py` (`prepare_review`); `runtime/workstreams.py` (`consume_dispatch` calls dispatch); `scripts/workflow.py` only if the reply shape needs it; new `tests/test_delegation_routing.py`.
- Does: for a managed project, `selection` gains mode `routed`: the coordinator supplies no model; the reply carries the admitted `route{model, effort, attempt_id}`; the existing catalog and guidance validators check the admitted strategy instead of the coordinator's; an explicit user choice becomes `assignment.choice`. The routing admission is the only record of the requested model; delegation's `binding.requested_model` stays null in this mode and `observe` compares the observed settings with the admission through a read-only accessor. Continuations get fresh admission. Reviewers get the independence constraint. Codex is an advisory host.
- Tests: one task graph (no second job store); unchanged continuation reuses profile and child with a new admission; resource and funding gates bind the same job revision; reopened dependency after `prepare` sends nothing; advisory host never gets a dollar route; unmanaged project keeps today's `selection` behaviour.
- C.2 contracts: C.2b (all); C.2d project review stays scoped and read-only; C.2e review independence kept.
- Shared core: **yes** (`runtime/delegation.py`, `runtime/evidence.py`, possibly `scripts/workflow.py`): full suite. Size: L.

**C3-3 — OpenCode production launcher.** Depends on C3-1.
- Owns: new `runtime/opencode_launcher.py` (local server HTTP client: health, dispatch, attach, session create and prompt with derived ids, event tracking, idle wait, re-read, descendants, accounting, record, detach), new `scripts/opencode_launch.py` (JSON CLI); the launcher section of `clients/opencode.md`; new `tests/test_opencode_launcher.py` with a fake server and the real `routing.py` on temporary stores.
- Does: the full [handshake](../../../clients/opencode.md#handshake) and [wiring](../../../clients/opencode.md#usage-accounting-and-resync); refuses `profile_not_enforced`, `host_unhealthy`, `api_funding_unqualified`; never calls `session.command`; strips credential-like environment names from any process it starts; writes rate-limit observations for the collector; writes `not_entitled` marks.
- Tests: refusal before any session is created for each refusal; token never in output or files (scan); lost acknowledgment records `uncertain`; descendants called before detach; `shim_descendants: None` makes an API cost unknown; no `session.command` call exists (static check).
- C.2 contracts: C.2e "routing success grants no merge, push or cleanup authority" for OpenCode; part of C.2c (V2 editing needs an enforced managed launch).
- Shared core: no (OpenCode adapter files are read-only for it). Size: L. Live observation is separate and waits on the user.

**C3-4a — External choice and OpenRouter proposals.** Depends on C3-1.
- Owns: `runtime/external_delegation.py` (new `constraints(explicit, local_preferred, permission)`; `select` kept until C3-7 for unmanaged projects); `scripts/openrouter_proposals.py`; `clients/external-delegation.md`; `skills/openrouter-proposals/references/procedure.md`; `tests/test_external_delegation.py`, `tests/test_openrouter_proposals.py`.
- Does: for a managed project, `begin`/`dispatch` run a managed launch on the `gateway` route with in-process `check` before the send; `external_delegation.check` and `native_review` stay; `triage` stays advisory and chooses nothing.
- Tests: OpenRouter is a candidate only with its own activation and funding; a direct API grant never funds it; proposals gain no file or tool permission; explicit choice narrows, never widens.
- C.2 contracts: C.2c "OpenRouter keeps its own funding" and "proposals gain no new permissions".
- Shared core: no. Size: M.

**C3-4b — OpenRouter workers on the managed launcher.** Depends on C3-3.
- Owns: `scripts/openrouter_workers.py`; `skills/openrouter-workers/references/procedure.md`; `tests/test_openrouter_workers.py`.
- Does: for a managed project, workers launch through `opencode_launcher` with the read-only profile from `configuration`; the V1 `opencode run --pure` path refuses with `legacy_route_retired`.
- Tests: V1 path refused for a managed project; the read-only profile is kept; a V1 qualification record does not authorize a V2 editing profile.
- C.2 contracts: C.2c "the V1 worker qualification does not authorize V2 editing".
- Shared core: no. Size: M.

**C3-5 — API Usage on the managed path.** Depends on C3-0 and C3-1.
- Owns: `runtime/usage_routing.py`; `scripts/usage_routing.py`; `runtime/usage_codex_host.py`; `runtime/usage_canary.py`; `skills/manage-usage-routing/` references; `tests/test_usage_routing.py`, `tests/test_usage_live.py`, `tests/test_usage_codex_host.py` (as present).
- Does: for a managed project, API Usage proposals run `prepare` → `dispatch` → `convert_hold` → `openai_api` with `check` before the count and generation requests → `record complete`; `evaluate`, `run`, `handoff` and `claim_handoff` refuse a managed project with `legacy_route_retired` before reading settings.
- Tests: API Usage OFF excludes direct routes; a managed project never reaches `Ledger.begin`; existing `live_admission_required`, `credential_unavailable` and OFF-never-resolves-a-credential tests still pass on the new path.
- C.2 contracts: C.2c "API Usage stays opt-in".
- Shared core: no. Size: M.

**C3-6 — Instructions and source coverage.** Written in phase W, merged in phase R.
- Owns: `policies/03-agent-delegation.md` (worker selection: routing chooses, guidance refresh feeds the strategy table, explicit choices are constraints); `clients/external-delegation.md` selection text (after C3-4a); `skills/manage-usage-routing/references/delegation.md`; affected skill entry points (150-word limit); `docs/policy-sources.md`; `clients/instruction-map.json` (regenerated).
- Does: removes instructions that tell a coordinator to pick models; adds the "ask a launcher" rule once.
- Tests: `tests.test_prepared_instructions` and the affected instruction tests; walkthroughs for Workstreams, API Usage, OpenRouter, Council and project review, reported separately.
- C.2 contracts: C.2a "explicit OFF survives re-onboarding" if it rests on onboarding text; C.2d Council only on explicit invocation.
- Shared core: no, but instruction changes reach many tests: run the affected modules. Size: M.

**C3-7 — Retirement.** Depends on everything above and the user's cutover decision.
- Owns: removal in every file of C3-2 to C3-5 (the managed-project branches), `runtime/external_delegation.py:select`/`automatic_allowed`, `Ledger.begin` and S1-only helpers in `runtime/usage_budget.py`, the V1 workers launch, `managed_projects` in `runtime/routing_settings.py`, obsolete tests; new `tests/test_legacy_selectors_retired.py`.
- Does: every checkout uses the managed path; remaining `ready` handoffs become `retired` ([4.3](#43-settle-outstanding-legacy-work)); `selection` modes `explicit`/`inherit` in delegation are refused under ON (only `choice` remains); `legacy_outstanding` must be empty before merge.
- Tests: legacy symbols are not importable; legacy CLI actions are usage errors; no `NEEDS_C3` marker remains; full suite and `validate.py`.
- C.2 contracts: the remaining markers; all of C.2 passes.
- Shared core: **yes**: full suite. Size: M (mostly deletion).

### 6.3 Lane D marker convention

So each slice can flip exactly its own contracts in its own PR, Lane D's `NEEDS_C3` markers should name the slice that makes them true (for example `NEEDS_C3('C3-2')`), using the C.2 mapping in each slice above. A slice removes only its own markers; C3-7 asserts none remain. Where a contract needs two slices, the marker names the later one.

## 7. Open decisions

### 7.1 For the user

1. **Cutover.** Whether and when phase R starts after C.1 passes, and whether phase W's `managed_projects` grows beyond the canary project before cutover.
2. **Default ON scope (C.4).** Whether default ON at cutover covers advisory hosts (Codex native) for automatic subscription selection, or whether advisory hosts use only `worker_choices` until a pre-transmission hook exists. Recommendation: allow automatic subscription selection on advisory hosts, labelled `advisory`, because funding there is window-only.
3. **OpenRouter workers gap.** Accept that OpenRouter workers in managed projects are unavailable until C3-4b merges and the `gateway` route on OpenCode is qualified live.
4. **API-funded OpenCode qualification.** Running the S3.6 positive and negative descendants runs with real API costs, which lifts `api_funding_unqualified`.
5. **Route manifest.** Accepting the Task 0.3 route manifest's classifications (from the capability matrix) as the collector's source, given that observed billing for several routes still waits on the user.

### 7.2 Decided here (agent-decidable; change by PR)

- Two phases with a `managed_projects` list instead of a big-bang switch.
- New modules instead of raising line ceilings.
- Advisory hosts never get dollar routes.
- The routing admission is the only record of a routed worker's requested model.
- `settle_legacy_call` and `drop_debit` as new one-shot operator ops; no release without spend.
- `scripts/usage_routing.py` keeps setup and status actions.
- Entitlement marks cleared only by the user.
- The marker convention in [6.3](#63-lane-d-marker-convention).
