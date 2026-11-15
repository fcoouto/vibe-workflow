# Smart Router Accelerated Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans for accepted implementation tasks. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver automatic Smart Router selection and launch across all required subscription hosts through parallel provider outcomes, without relaxing qualification or release gates.

**Architecture:** Keep the delivered router, ledger and native bridge. Provider owners deliver account-bound sources and focused tests independently; one integration owner wires shared consumers. The orchestrator owns priorities, interfaces, acceptance, Git, production operations and release evidence.

**Tech Stack:** Python 3.11+ standard library, Git, existing SQLite/JSON stores and `unittest` through `scripts/test_sharded.py`.

**Spec:** [Approved redesign and dated amendments](../specs/2026-11-10-smart-routing-redesign-design.md), [existing implementation plan](2026-11-10-smart-routing-redesign.md), [qualification runbook](../../smart-routing-qualification.md) and [registered validation](../../smart-routing-validation.md).

**Status, 15 November 2026:** Proposed execution split for user confirmation.
Planning does not start implementation streams or authorize live observations.
Base: `e41c3f59c07d198108a1a91760d1548e6a6a84f5` (PR #309).
Workstreams is enabled; no project is currently managed and API Usage is disabled.

## Global Constraints

- Preserve the approved product scope. This is a delivery map, not a replacement design.
- F1–F7, PR #308 acquisition ordering and PR #309 native Claude source support are delivered. Do not rebuild them.
- Route enabled accounts with their existing settings, including ordinary provider-managed overflow. Never change authentication, permissions, credits, spending limits or account configuration.
- Derive nonsecret identity from reported email, organization when present and sign-in type. An organization alone is insufficient. No UUID or exhaustive coordinator-settings attestation.
- Account facts belong to the exact path. A shared pool name does not establish equal accounts. Account changes invalidate reused account, usage and catalog data.
- Preserve freshness, conservative original observation time, complete applicable constraints, genuine shared allowance and uncertain-send exposure. Unknown is not zero or spare capacity.
- Prefer usable included allowance; represent provider-managed fallback separately, without invented included capacity. Respect provider refusal.
- Read only when stale and needed; reuse incidental observations, locking and backoff. No polling, daemons, measurement-only model calls or per-assignment ranking calls.
- Preserve explicit model choices, supported host-specific aliases/efforts, native-only provider boundaries, one-use dispatch and idempotent settlement.
- Keep existing storage, funding and API-money protections. No new account-control framework, scheduler, registry or quota store.
- No hard-coded production model names. Respect current `scripts/validate.py` line ceilings, not older numeric ceilings in historical plans.
- F8/advisory replay is retired. Strict replay remains required. API Usage is required next release; production OpenRouter remains separately deferred.
- L1–L3 implementation stays post-canary; L4 stays conditional and off by default.
- No new account access, private-export access, installation, activation, model-send budget or Results publication follows from this plan.

## Review Focus

1. Same provider, different logins: OpenCode must not borrow a native account's readings. Streams OC/CC and I own negative sharing tests.
2. Login changes between selection and send: reject stale account/catalog binding without issuing a second permit. Streams CC and I own dispatch tests.
3. Partial, scoped, cached or malformed limits: keep unsupported constraints unknown; never refresh timestamps or invent headroom. Streams NC/OC/GO own source tests.
4. Included quota exhausted: preserve account-managed fallback without an included reservation or direct-API activation. Stream I owns cross-provider accounting tests.
5. Timeout after possible send: keep exposure, reconcile once, never blindly relaunch. Stream I owns integration tests; Q owns the live drill packet.

## Baseline and delivery milestones

Recorded PR #309 proof: macOS full suite passed 4,904 tests, with four existing
skips and two expected failures; Python 3.11 gate passed 206 tests, zero skips.
These are prior source results, not new checks or real-client evidence.
Strict replay privately passed all 21 historical worker-choice admissions;
there were no ranked admissions. Separate Results publication remains pending.
No current full Linux pass exists.

| Milestone | Observable result | Gate |
| --- | --- | --- |
| M1: native Claude qualification | Existing account reader and supported aggregate branch observed on installed client; separately approved native launch/demand evidence | NC + bounded authority + I regression proof; not production activation |
| M2: complete source integration | All five paths have real source bindings and synthetic source-to-settlement proof | OC + CC + GO integrated by I, independently reviewed |
| M3: ranked canary accepted | Required measured OpenCode paths, table, seeds, activation and registered replay/canary criteria pass | Q and orchestrator; explicit live/production authority |
| M4: cutover and release coverage | Both native hosts and all three OpenCode families have evidence; user cutover/default-ON decisions resolved | Q + I retirement work + V platform evidence + exact release-candidate full suite |
| M5: learning | L1 records scores; L2 refreshes data; L3 promotes only after registered evaluation | Post-canary gates below; API Usage remains next-release scope |

M1 is the shortest dependency path, not a reduced release. M2 and M3 may overlap:
the scheduled two-provider OpenCode canary need not wait for Go/native release
coverage, but it cannot establish that coverage. No reliable delivery date exists
until missing source evidence is resolved.

## Streams and exclusive ownership

Owners are responsibilities, not a fixed number of simultaneous workers.
Start dependency-ready children within real client slots, provider quotas,
host capacity, task budgets and file ownership. No artificial worker cap.
Use one native child per cohesive outcome, with no nested children.

| ID / owner role | Outcome | Exclusive writable files |
| --- | --- | --- |
| NC / `software-engineer` | Existing native Claude coverage and smallest qualification packet | `runtime/claude_usage.py`, `tests/test_claude_usage.py` |
| OC / `software-engineer` | OpenCode Claude connection-bound source | Proposed `runtime/opencode_claude_account.py`, `tests/test_opencode_claude_account.py`, only if the inspected host source warrants them |
| CC / `software-engineer` | Native Codex account/catalog plus account-safe OpenCode ChatGPT sharing | `runtime/codex_rate_limits.py`, `tests/test_routing_codex_rate_limits.py`; proposed `runtime/codex_account.py`, `tests/test_codex_account.py`; a separate OpenCode projection only after its source is found and files reserved |
| GO / `software-engineer` | Go workspace/account, meter semantics and model-demand source | `runtime/opencode_subscription.py`, `tests/test_opencode_subscription.py`, `tests/test_opencode_subscription_wiring.py` |
| I / designated integration child, orchestrator accepts | Real shared consumer wiring and combined regressions | Shared runtime/test reservation below |
| Q / `qa-automation-engineer` | Source-only qualification/replay/canary preparation; later authorized campaign support | `tests/qualification/{live_modes,launcher_probes,drill,demand,safety,replay_export,replay_strict,canary_cli,canary_ranked,canary_ranked_state}.py` and matching `tests/test_qualification_*.py` only as individually reserved before dispatch |
| V / `test-engineer` | Recovery-test reliability and current Linux proof | `tests/test_parent_recovery.py`; isolated test environments/logs. No runtime repair ownership without reassignment |

The orchestrator alone owns tracked plans/specs, capability matrix, validation
results, client guidance, release docs, shared settings, private tracker, Git and
publication. Provider/Q children propose documentation updates in their reports.
Each brief names exact files, not the whole table's possible scope.

**I's reserved shared runtime files:** `runtime/routing_observations.py`,
`runtime/adapters/native_catalog.py`, `runtime/native_routing.py`,
`runtime/opencode_launcher.py`, `runtime/routing_paths.py`,
`runtime/routing_routes.json`, `runtime/subscription_billing.py`,
`runtime/routing_launch.py`, `runtime/subscription_reconcile.py`,
`runtime/routing.py`, `runtime/routing_funding.py`, `runtime/routing_settings.py`.
Reserve other shared accounting/caller files explicitly if necessary.
Ownership is not a request to edit every file.

**I's shared tests:** `tests/test_routing_production_flows.py`,
`tests/test_native_routing.py`, `tests/test_subscription_acquisition.py`,
`tests/test_subscription_billing.py`, `tests/test_routing_observations.py`,
`tests/test_routing_managed_projects.py`, `tests/test_opencode_launcher.py`,
`tests/test_routing_launch.py`, `tests/test_delegation_routing_consumed.py`.
No provider child edits these concurrently, even in another worktree.

## Interfaces before dependent edits

Reuse these contracts rather than adding a generic provider framework:

- `LocalSource.billing(route, now)` returns `None` or the exact
  `{producer, path, account, pool, observed_at, valid_until, paid_crossover}` fact
  validated by `runtime/subscription_billing.py`. Overflow states remain
  `provider_blocked`, `provider_managed`, `unknown`; none excludes an enabled account.
- `LocalSource.acquire_billing(routes, now)` acquires missing/stale facts outside
  pure reads. Its return value grants no authority. Account reads may also store
  incidental bound windows.
- Retain Claude/Codex `refresh(settings_path, *, need=..., ...)` and existing
  fixed errors, bounded transport, locking/backoff and conservative time.
  Go retains `refresh(service, read, request, *, now)`.
- Native snapshots remain `{binding, models: [{alias, resolved, efforts}]}` via
  `store_snapshot(settings_path, client, snapshot)` and
  `catalog(settings_path, client)`. Binding fields remain
  `{client_version, connection, controls}`; no second native bridge.
- Preserve Go `window_semantics()` and `LocalSource.model_demand(route)`.
  Their current `None` results cannot become positive without actual source evidence.

Before positive provider implementation, its owner hands I a short source note:
exact host/path/account/pool, acquisition method/schema, original observation
time, complete applicable windows, storage/invalidation, catalog/effort mapping,
and justified sharing/fallback behavior. I accepts concrete signatures and row
selection before dependent edits. An unavailable source produces a blocker and
bounded evidence request, not a fabricated parser or an unsupported success.

## Execution tasks

### Task 1: Freeze ownership and source contracts (orchestrator + provider owners)

- [ ] Confirm plan with the user; preserve the selected parallel/orchestrator method.
- [ ] Reconcile active writers and prepare task-owned worktrees from current `main`.
  Keep primary checkout on `main`; preserve partial native prototypes and old F8 work.
- [ ] Dispatch source-local investigations/tests for NC, OC, CC and GO; Q prepares
  evidence packets and V diagnoses the known clock fixture independently.
  Use bounded briefs, native completion events, explicit file claims and checkpoints.
- [ ] Accept each provider's source note before positive wiring. Inspect documented
  or existing protocol evidence source-only; request only indispensable live metadata.
  Stop dependent positive implementation when identity or semantics remain unknown.

### Task 2: NC — qualify what already exists, extend only evidenced limit coverage

**Consumes:** existing `claude_usage.refresh`, account-bound windows and native snapshot.
**Produces:** supported-limit coverage and finite installed-client qualification packet.

- [ ] Inventory unsupported raw/scoped/unknown applicable limits; do not rebuild
  PR #309's aggregate account reader.
- [ ] For any evidenced new shape, add a failing test with exact window/time
  assertions. Cached/future/partial/unknown shapes must never become new capacity.
- [ ] Implement only that evidenced mapping; preserve zero-inference native exchange,
  account-change invalidation, incidental reuse and bounded cleanup.
- [ ] Validate and commit; run `python3 -B scripts/test_sharded.py test_claude_usage`.
  Expected: all selected tests pass, unsupported shapes still refuse.
- [ ] Hand I the unchanged/new contract and Q the smallest missing observation packet.
  Installed reader and native launch remain unobserved until separately authorized.

### Task 3: OC — bind OpenCode Claude to its actual connection

**Consumes:** inspected OpenCode connection metadata and complete usage provenance.
**Produces:** path-specific fact/window acquisition; not native-Claude login inference.

- [ ] Locate the host's nonsecret identity source; record unsupported access instead
  of reading credential files or assuming native/OpenCode login equality.
- [ ] In the proposed local test module, pin `test_native_identity_is_not_opencode_proof`,
  `test_changed_connection_invalidates_usage` and
  `test_partial_applicable_windows_stay_unknown`; observe intended failure.
- [ ] Add the minimal source projection using the accepted source note. Bind settled
  telemetry to the actual connection; never relabel native-bound rows.
- [ ] Validate, commit and run `python3 -B scripts/test_sharded.py test_opencode_claude_account`.
  Expected: positive projection only from evidenced host metadata; negative cases refuse.
- [ ] Hand I a tested provider-local change and Q its remaining real-host checks.

### Task 4: CC — native Codex plus safe ChatGPT sharing

**Consumes:** existing bounded quota reader, inspected native account/catalog protocol,
and separately evidenced OpenCode ChatGPT connection identity.
**Produces:** native account/catalog bootstrap and justified cross-host allowance binding.

- [ ] Keep one provider owner for native/OpenCode identity sharing. Quota response
  alone is not an account source; an OpenCode model listing is not entitlement.
- [ ] Write failing local tests for missing/changed account, catalog/effort mapping,
  all applicable reported windows, same verified account sharing and different/
  unknown OpenCode account refusal. Observe the intended failures.
- [ ] Implement bounded account/catalog acquisition beside the existing reader;
  preserve its consumed-boundary freshness and process cleanup.
- [ ] Validate, commit and run `python3 -B scripts/test_sharded.py test_codex_account test_routing_codex_rate_limits`.
  Expected: exact bound native metadata; no duplicated shared allowance.
- [ ] Hand I native bootstrap and OpenCode projection separately. One unsupported
  host must not prevent the independently supported host's source delivery.

### Task 5: GO — complete meter meaning before positive routing

**Consumes:** existing Go parser/transport and evidenced workspace/catalog,
original observation/cache age, meter lengths/semantics and model-dependent demand.
**Produces:** account-bound Go observations usable by real consumers.

- [ ] Resolve each missing fact explicitly. A fresh-looking usage response alone
  does not establish weekly/monthly semantics, monthly length or per-model demand.
- [ ] Write failing tests for missing semantics/time/demand, changed workspace,
  all three simultaneous meters and aliases sharing one allowance.
- [ ] Implement only positively evidenced source bindings in the existing module.
  Never guess month length or enable a fixed-window stale-full shortcut.
- [ ] Validate, commit and run `python3 -B scripts/test_sharded.py test_opencode_subscription test_opencode_subscription_wiring`.
  Expected: complete verified cases work; each missing fact stays blocked.
- [ ] Hand I the source note/change; ask Q for only unresolved bounded evidence.
  Unsupported-source disposition keeps GO open for release, not silently deferred.

### Task 6: I — integrate one accepted provider at a time

**Consumes:** accepted provider-local changes and the interfaces above.
**Produces:** real `LocalSource` → admission → exact launch → settlement/refusal proof.

- [ ] Integrate ready outcomes without waiting for every provider. Review provider
  diffs before releasing dependencies; serialize shared-file edits and Git operations.
- [ ] Add failing shared tests for the five Review Focus cases, acquisition before
  eligibility/activation, no acquisition in pure/served paths, and all five host paths.
- [ ] Wire the existing account/window/catalog consumers. Extend the current
  native-Claude-only fallback projection only for paths with real account producers.
  Preserve host filtering and genuine account-safe shared-pool accounting.
- [ ] Exercise cold native bootstrap, supported aliases/efforts, refusal/entitlement
  continuation, one-use permits and once-only completion through actual shared code
  with synthetic transport/private stores, not request-supplied positive observations.
- [ ] Validate and commit; run `python3 -B scripts/test_sharded.py test_claude_usage test_routing_codex_rate_limits test_opencode_subscription test_opencode_subscription_wiring test_native_routing test_subscription_acquisition test_subscription_billing test_routing_observations test_routing_managed_projects test_opencode_launcher test_routing_production_flows test_routing_launch test_delegation_routing_consumed test_routing_replay`.
  Add provider-local modules and affected transitive callers to each actual selection.
- [ ] Obtain independent behavior/accounting/security review and the proportionate
  final integrated gate. Cross-cutting source changes require one final committed
  full suite; do not run competing full suites in provider worktrees.

### Task 7: Q — prepare evidence now; operate only within later bounded authority

**Consumes:** current runbook/validation, recovered inherited gates below and provider notes.
**Produces:** one criterion-to-evidence matrix, finite approval packet and executable
qualification fixes only where existing harness consumers lack required source binding.

- [ ] Map every required path to synthetic source, real reader, catalog/controls,
  launch, account-bound demand, settlement/refusal and platform evidence.
  Keep native Claude as first qualification candidate.
- [ ] Check existing `LiveEnv.codex_window` and demand samples against the new account
  contract; add failing synthetic tests before any necessary account-provenance wiring.
  No extra measurement launch. Go demand stays model-dependent.
- [ ] Prepare isolation/lost-ack, table, replay, campaign pin/abort and release packets.
  Reuse the private strict-pass summary; do not reopen private exports or publish Results.
- [ ] Validate, commit harness changes if needed, then run
  `python3 -B scripts/test_sharded.py 'test_qualification_*'`.
  Pure documentation preparation needs static checks, not a manufactured code change.
- [ ] Orchestrator requests one finite reader/host session: exact versions/accounts,
  projected nonsecret fields, zero-inference operations, read/process/time limits,
  private destination and cleanup. Initial one-per-client metadata checks are consumed.
  Historical two reads around one independently intended task per path do not
  authorize a new measurement task or repeated retries.
- [ ] Separately bound any missing model launch/probe exposure and purposes.
  Isolation 8 and drill 4 are ceilings, not open caps. Keep canary cap zero in this
  first packet; gateway remains skipped. Preserve account settings and uncertainty.
- [ ] After authority and prerequisites hold, orchestrator serializes production
  order: compiled table → measured path qualification → seeds for every demanded
  window → user activation → separately approved ranked canary.
  Guidance row publication uses the existing maintenance procedure, one model read
  per guide page; no L2 importer is required before canary.
- [ ] Canary approval separately names one project, paths, pinned input digest,
  history disposition, exposure, quiet window and start. Preserve lifetime ledger,
  whole-run 100 thousandths per pool/window, earliest reset and minimum 60 admissions.
  A maximum exposure ceiling of 83 is not approval for 83 calls.
- [ ] Retain snapshot exports before pruning; replay pilot and all canary records at
  C.1, verify latency and registered cutover criteria, then ask for user cutover.
  Remove the canary project from `managed_projects` afterward as approved.
  Separate sanitized Results publication still needs its own authority.

### Task 8: V — remove verification noise, then establish current platform proof

- [ ] Reproduce the clock-boundary fixture in
  `test_human_reactivation_keeps_deadline_and_consumed_send_allowance`:
  `now` is sampled before `register()`. Pin time across registration/observations
  or derive observation time from actual registration; do not weaken token/deadline assertions.
- [ ] Add/observe a deterministic boundary reproduction, fix only the fixture,
  validate, commit and run `python3 -B scripts/test_sharded.py test_parent_recovery`.
  Expected: deadline and consumed-send allowance remain unchanged across reactivation.
- [ ] Reuse PR #305's focused Linux repairs as history, not current full proof.
  Run current committed candidate on an already authorized isolated Linux environment;
  record Python/Git/Node versions, exact tree, skips, failures and cleanup.
- [ ] Coordinate one heavy Linux gate with I/orchestrator. No new downloads,
  installs or network authority is assumed. Missing offline Node coverage remains
  missing; do not count skips as a full platform pass.
  New runtime failures return to their owner through bounded diagnosis.

## Recovered pre-canary, cutover and default-ON obligations

The inherited 8 November plan was removed by `11a8927c`; its last text is
[pinned in Git history](https://github.com/fcoouto/vibe-workflow/blob/02758ebf1ea13dbff75e1d4ebe77b1b5be7a42b6/docs/superpowers/plans/2026-11-08-smart-routing.md).
Read it with `git show 02758ebf1ea13dbff75e1d4ebe77b1b5be7a42b6:docs/superpowers/plans/2026-11-08-smart-routing.md`.
Do not restore its obsolete criteria or treat historical checkboxes as current proof.

| Inherited requirement | Current disposition / owner |
| --- | --- |
| Pre-canary 1–2: decisions, qualified paths/generated routes, first readings | Q tracks unresolved user cutover/default-ON choices; I preserves activation/seed gates. Seeds are not routine automatic account/usage producers. |
| Item 3: launcher isolation | Q retains flags/password, hostile/global config, plugin/MCP/formatter/LSP, auth/outside-read risk, permission precedence/shape, no-variant model, fixed PATH and reply text-shape checks. Accepted risks need explicit user disposition. |
| Items 4–5: drill, handshake, dispatch, accounting, shim | Q requires live lost-ack drill; `authority_required: true`, `unstamped_open_holds: 0`, project pin and workflow/attempt binding; reconcile unresolved holds/overruns/legacy blocks without clearing exposure. Preserve required S3.3d and health/deny evidence; launchers never invoke project commands in managed sessions. |
| Item 6: enrolment | Q verifies configured/active Codex enrolment for the scheduled OpenCode canary; any added managed native host must meet its own current source/catalog gate. |
| Item 7: replay/export | Strict pilot pass exists privately; Results delivery unresolved. Q retains strict C.1 replay and 12-hour/end-run snapshot export before 24-hour detail pruning. Advisory replay/F8 is retired by Edit 14. |
| Item 8: gateway | Deferred; not a subscription-canary prerequisite. No gateway funding or API qualification is opened. |
| Items 9–10: Codex reader and demand | CC/Q retain bounded protocol/timing/auth-file-metadata checks, actual account provenance and all applicable reported windows. Do not retain the old weekly-only expectation when more constraints apply. Demand uses existing bounded launches, not guessed policy values. |
| Items 11–12: production canary/foundation | Q retains explicit production-ledger approval, quiet window, post-run removal, new guidance rows/table, Edit 7 and real-home `model_names: passed`. F1–F7 delivered; F8 no longer required. |
| C.1 then C3-7 | After accepted canary + strict pilot/canary replay and user cutover, I owns a separate retirement task: no second selector/writer; `legacy_outstanding` empty; preserve history/unresolved ownership. Expand exact files from the C.3 design before dispatch. |
| C.2 contracts and C.4 default ON | I/Q reconcile surviving feature-contract gaps, including readiness surfaces, continuation reuse and Council boundaries, against actual code. Retain the two C3-7 expected failures until retirement makes them pass. User decides default-ON/advisory-host scope; explicit OFF and table rollback remain available. No automatic activation follows source merge. |

All inherited constraints apply with later amendments: no artificial delegation
counts, updated storage/money controls, account-managed overflow and F8 retirement.
This table assigns the old gates; Q must retain a concrete receipt or unresolved
condition for each before declaring canary/cutover readiness.

## Dependency graph and orchestration

```text
delivered F1–F7 / PR308 / PR309
  NC coverage + approved real reader/native launch ───── M1
  OC source ─────────────────────────────────┐
  CC native + OpenCode identity source ──────┼─ I integrates each ─ M2
  GO account/time/semantics/demand source ────┘
  Q source-only packets/table inputs ───────────┐
  V deterministic fixture + Linux proof ────────┼─ evidence joins relevant gates
  measured required OpenCode paths + table ─────┘
       seeds → user activation → separately approved ranked canary → M3
       strict C.1 + user cutover → I retirement / default-ON decision
       all five paths + platform/release proof → M4
  accepted canary → L1 and L2 → sufficient L1 evidence → L3 → conditional L4
```

- **First wave:** provider-local source investigation/tests, Q preparation and V
  fixture diagnosis. I freezes interfaces and accepts ready integrations. No live probes.
- **Next waves:** integrate/review each supported provider without waiting for GO;
  bundle missing metadata requests to reduce user interruptions. Unsupported sources
  block their positive wiring and release claims, not unrelated work.
- **Serialize:** shared files, commits/integration, full gates, guidance refresh/rollback,
  installation/global configuration, production stores/caps and all same-login
  sampling/canary quiet windows. Worktrees do not isolate these shared resources.
- **Delivery:** orchestrator creates/reuses native task worktrees, commits and runs
  focused checks on committed candidates, obtains independent review, publishes draft
  `[BLOCKED]` PRs, promotes `[READY]` and merges under standing repository authority.
  Preserve the existing approved implementation/reviewer model choice; resolve actual
  supported dispatch controls before assigning workers. Do not silently substitute
  or change coordinator settings. Source proof is separate from installed execution.
- **Progress:** track accepted outcomes and blockers, not elapsed time or number of
  workers. Reuse the same child for fixes; no nested dispatch or polling. If a worker
  becomes uncertain, reserve its resources until stopped ownership is confirmed.

## Post-canary learning and next-release handoff

Prepare field maps, fixture sketches and source inventories now, not implementation.
Expand each existing L outline only after its gate; do not move learning ahead of canary.

| Slice | Unlock / outcome |
| --- | --- |
| L1 | After accepted canary, add `runtime/routing_learning.py`, idempotent canonical-outcome rows and scores recorded, not applied. Capture missing before/after usage from existing reads, rework/severity/time and exposed served version. Land within 14 days or export records first. Preserve 60-day retention and design thresholds 5/20, half-weight floors and borrowing at 10. |
| L2 | Post-canary; independent of L1 completion once shared files are scheduled. Add `runtime/due_jobs.py`, bounded entry-point-triggered due jobs, conditional catalog download, guide digests, benchmark importer and family/successor logic. Check credential-free availability/terms at L2 start; 2% tolerance; no effort means default effort only. |
| L3 | Post-canary plus sufficient L1 evidence. Register before reading held-out data: chronological earlier/later halves; at least three work kinds, two candidates per kind and five later-half results per candidate; learned choice at least as good in 80% of counted kinds. Only then enable one-shot promotion/rollback. |
| L4 | Conditional evidence that the optional ranker beats the table with learned scores; off by default. Not part of this acceleration wave. |
| API Usage | Required next release: discovery/bindings, managed execution/check/settlement, ranked admission/replay and existing subscription/API equation. Preserve 20% whole-task money buffer, protected funds and uncertainty. Does not wait for OpenRouter. |

## Plan acceptance and stop condition

- [ ] Self-review scope coverage, signatures, ownership, five Review Focus cases,
  source/live distinction, recovered gates and task proportion.
- [ ] Obtain one focused independent plan review; fix substantiated gaps without
  restarting already delivered work.
- [ ] Validate documentation/package, commit and publish this planning change.
  No runtime suite or client execution is implied by documentation checks.
- [ ] Present the split for user confirmation before implementation-stream dispatch.

Planning is complete when the reviewed map is published and the user can confirm
the split. Implementation, real-client qualification, activation and release remain
separate unfinished outcomes; no worker is claimed running without a live assignment.
