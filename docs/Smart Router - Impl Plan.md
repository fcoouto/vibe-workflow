# Smart Routing (Vibe Router) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace vibe-workflow's competing worker selectors with built-in Smart Routing, reusing Workstreams profiles and reserving eligible funding for parallel work.

**Architecture:** Workstreams owns the canonical task graph. A shared profile service, evidence-backed ranker and deterministic allocator prepare each ready assignment; host adapters enforce its admitted route. One financial ledger connects to existing Python workflow evidence through narrow interfaces.

**Tech Stack:** TypeScript/OpenCode V2 plugin, SQLite, existing Python workflow services, and Vitest for the proposed router package's offline tests. P0 pins compatible host and SQLite bindings before implementation.

**Spec:** [Smart Routing integration design](../../vibe-workflow/docs/superpowers/specs/2026-11-08-usage-routing-opencode-subscriptions-design.md), including its feature-impact Appendix A.

- **Version:** 1.6 draft
- **Date:** 2026-11-08
- **Status:** Design aligned with the agreed component matrix; runtime implementation remains gated by P0.
- **Supersedes:** v1.5 with feature-impact coverage, phase-owned implementation checkpoints and whole-document review corrections.
- **Target:** Vibe Router inside vibe-workflow, with OpenCode V2 as the first enforced worker-routing host.

**Canonical design:** [Smart Routing — Vibe Router integration design](../../vibe-workflow/docs/superpowers/specs/2026-11-08-usage-routing-opencode-subscriptions-design.md).

The canonical design owns product behavior and merge decisions. This file records the ordered implementation handoff. Implementation belongs in vibe-workflow; its runtime must not depend on this standalone planning checkout.

## 1. Agreed component and responsibility matrix

| Component | Responsibility |
|---|---|
| **Jev — task profiler** | Enrich task family, complexity, risk, context and tool requirements only when the shared profiling service cannot reuse or derive a valid profile from Workstreams metadata. |
| **Dynamically selected router model** | Rank suitable model/effort strategies using supplied vendor guidance and benchmark evidence. |
| **Deterministic allocator** | Check budgets, choose the funding route, reserve capacity and authorize execution. |

Jev describes; the router ranks; the allocator validates and authorizes. Hard capability, funding, review and permission constraints are enforced in code. Model output never authorizes a launch.

## Global Constraints

- **Fixed coordinator.** Keep the user's coordinator model, effort and authentication. The coordinator owns task understanding, delegation, supervision, verification and synthesis. It submits bounded worker briefs instead of guessing model names.
- **Workstreams integration.** Workstreams owns decomposition, job readiness, dependencies, ownership and progress. Extend its canonical jobs with profile/ranking references; do not create a router-owned task graph. Explicit delegation uses the same preparation interface.
- **Lightweight adoption.** Preserve ordinary Workstreams' project-preference path without requiring advanced managed-runtime enrollment or a second activation. Bind selection artifacts to existing job/host-attempt identities with the minimum necessary assignment metadata.
- **Managed worker routing.** Every implementation/research worker, nested sub-agent, reviewer, repair and escalation launch uses the same managed admission path. Simple conversational work can remain with the coordinator.
- **Built-in and ON by default.** Vibe Router replaces existing automatic routing in vibe-workflow. An absent Smart Routing setting means ON; explicit OFF persists. ON automatically selects workers; OFF uses configured choices through the new allocator, not old selectors. Disable invalidates unsent automatic decisions, preserves in-flight work and needs no model response. Workstreams and API Usage retain independent controls.
- **Enablement versus readiness.** Missing required evidence, models or host support reports enabled-but-blocked/unsupported for the affected assignment. Default-on does not configure paid services, enable API Usage, broaden permissions or bypass P0.
- **Dynamic evidence.** Select models and supported efforts from current vendor guidance and comparable benchmark data. No hard-coded commercial model IDs, effort ladders or manual model tiers. New versions qualify through evidence rather than automatic “newest wins” promotion.
- **Router fallback.** Qualified free router model first; otherwise the lightest qualified model/effort in an available subscription with sufficient headroom. If neither option is available, return `router_unavailable` instead of using paid API-credit routing for the router itself.
- **Multiple funding pools.** Subscription headroom and subscription-linked API credits are separate routes. API Usage supplies grant/key handling; the deterministic allocator owns their common admission and selection.
- **API Usage eligibility.** Consider API routes only after explicit scoped enablement and secure API-key connection. Smart Routing, stored keys, subscription login and expiring credits do not enable API Usage implicitly.
- **Parallel admission.** Replace the one-task-at-a-time rule with independent buffered reservations. Tasks execute concurrently when combined funding, provider, ownership and host-resource constraints permit it.
- **Funding preference.** Healthy subscription capacity is preferred; API preference rises with subscription utilization and near-expiry grants. Default soft thresholds are 65%/85% projected utilization and a 72-hour grant-expiry horizon, all configurable.
- **Credit settings.** Default 98% of the issued subscription API allowance; custom 0–100%. `100%` uses the whole subscription grant. Explicit `no-limit` allows all verified existing API credits, including purchased credits, but never automatic purchases or postpaid spending.
- **Breaking merge.** This design takes precedence over overlapping workflow behavior. No backward-compatible routing aliases, schemas, selectors or dual ledger writers are required. Retire old paths after settling their outstanding ownership and costs.
- **Accounting and evidence.** Preserve whole-task cost to independently accepted delivery, including coordinator and routing overhead where observed, failures and reviewer rescue. Learned policy weights remain deferred; dynamic evidence-based selection and bounded calibration ship in v1.

## Review Focus

- **RF1 — Independent controls:** Smart Routing ON must not activate API Usage, OpenRouter, Council or observability; API keys/grants cannot fund a different gateway. Owner: P3/P6.
- **RF2 — Cached context:** identical-looking jobs in different scopes/roles or after changed requirements must not reuse incompatible profiles/rankings. Owner: P2.
- **RF3 — Ready-to-launch races:** a reopened dependency, changed writer or disabled route after preparation must block an unsent launch even on a cache hit. Owner: P4.
- **RF4 — Accounting edges:** free pricing, unknown completion, expiry and parent/child transfers must not divide by zero, double-charge or release uncertain capacity. Owner: P3/P5.
- **RF5 — Ordinary UI/management:** startup, naming, status, diagnostics and deterministic checks must perform zero routing inference; optional features retain their triggers. Owner: P6.

Appendix B assigns concrete file/interface boundaries and verification checkpoints to each phase. Those paths are proposed implementation files in vibe-workflow, not files already delivered by this document update. Commit checkpoints apply only when the execution workflow authorizes commits.

## 3. Architecture and merge ownership

Build a TypeScript router package inside vibe-workflow and load its host integration in the OpenCode service. Keep existing Python workflow task/evidence services behind narrow versioned JSON/event interfaces. One logical account-wide routing ledger owns financial admission across sessions and worktrees; SQLite transactions and exclusive dispatch authority, not merely one process, prevent races.

Proposed placement:

```text
vibe-workflow/
  packages/vibe-router/             # contracts, profiling, evidence, ranking, allocator, ledger
  integrations/opencode/           # V2 host plugin, managed spawn, guards, RPC
  runtime/adapters/opencode.py     # workflow client bridge where required
  clients/opencode.md              # qualification and client procedures
  policies/03-agent-delegation.md   # delegates selection to the common pipeline
```

Reuse grant/credential provenance from API Usage, catalog/benchmark discovery from OpenRouter, and canonical task/review/acceptance records from workflow. Replace their overlapping selectors and budget policies. In particular, replace API Usage's 20% balance floor and single in-flight slot with the agreed allowance policy and concurrent reservations. Host CPU/memory resource admission remains a separate constraint.

The canonical design's section 10.2 records the implementation evaluation: keep Workstreams dependency/ownership validation, catalog/effort checks, evidence caches and secure credentials; replace fragmented selection and both API Usage slot paths. New profiler/ranker inference and the host bridge add latency and integration risk, so require metadata/profile reuse, cached rankings and measured improvement. These proposed gains are not established runtime results.

The inspected `main` baseline is `d73252a77d1e784f8a7d6f637bc661467485178d`; the authoring checkout is on an older, actively edited branch. Reconcile the actual implementation baseline and OpenCode adaptation before touching shared runtime files. Existing OpenCode V1 worker execution does not qualify V2 host enforcement.

## 4. P0 — Complete feasibility qualification

Earlier probes reported OpenCode 2.2.6, Codex 0.167.0 and Claude Code 2.1.311. Preserve the distinction between documented interfaces, narrow observed probes and full workflow qualification.

| Gate | Required proof |
|---|---|
| P0.1 Source/runtime | Reconciled workflow baseline, available SQLite plugin binding, one shared ledger authority and versioned workflow/host interfaces. |
| P0.2 Subscription telemetry | Codex `account/rateLimits/read` is bound to the same account as the selected OpenCode route; Claude `native.rateLimit` utilization scale, completeness and freshness are understood. |
| P0.3 API grants | Both vendors' grant origin, scope, balance, expiry, actual debit order, current pricing and eligible execution transport can be established. An inference key or subscription plan name alone is insufficient. |
| P0.4 Credential/funding binding | Scoped API Usage enablement and secure key resolution gate API candidates and sends. Concurrent subscription/API routes are isolated without switching a shared active credential. Strict allowance-only execution cannot spill into unauthorized purchased or invoiced funding. |
| P0.5 Model/effort | Requested versus observed model/variant and role-specific control inference behave as documented on every admitted route. |
| P0.6 Mandatory worker routing | Built-in and nested worker spawns, native harness delegation, model switching and alternate worker launch paths cannot bypass admission in the supported profile. Blocking happens before transmission. |
| P0.7 Dispatch recovery | Duplicate session/message IDs, racing callers and interrupted launch acknowledgments cannot produce duplicate dispatch. Caller-supplied IDs alone are not proof of deduplication. |
| P0.8 Lineage/accounting | Coordinator, control calls, worker/review/repair stages, auxiliary requests and retries can be attributed or explicitly marked unknown. Event gaps can be resynchronized. |
| P0.9 Role fitness/performance | Jev profiling, free router candidates and the qualified subscription fallback meet pre-registered task-specific benchmark and latency requirements. |
| P0.10 Workstreams binding | Existing jobs, profiles, readiness, resource claims and acceptance references feed one shared preparation/admission path on the target host; semantic reuse and revision checks prevent redundant inference or stale dispatch. |

Known narrow observations: a read-only Codex rate-limit query worked; one native Claude run emitted `rate_limit_event`; one OpenCode Claude assistant message exposed `native.rateLimit`. OpenAI success-path quota telemetry and account equivalence remain unproven. Simultaneous exhaustion or correlated usage deltas do not prove account identity. No blanket claim that every response contains telemetry is justified.

Deliver `docs/smart-routing-feasibility.md`, `docs/smart-routing-capability-matrix.md` and sanitized probe fixtures in vibe-workflow. Remove prompts, tokens and account identifiers from earlier scratch output before reusing it. Document supported and unsupported routes explicitly.

**Exit:** each enabled route meets the relevant gate. After-the-fact interruption/quarantine is recovery, not a substitute for fail-closed enforcement. Unsupported routes stay out of enforced automatic dispatch.

## 5. P1 — Contracts, persistence and settings

Define versioned runtime schemas and canonical shared identities:

| Contract | Essential contents |
|---|---|
| `AssignmentBinding` | Existing workflow root/job/stage identity and semantic revision/digest, with profile/ranking/admission references; no duplicate task state machine. |
| `TaskProfile` | Normalized task family, complexity, risk, measured/estimated context and tools, review policy, per-field provenance, contract version, semantic digest and uncertainty; reusable without a new Jev call. |
| `CandidateEvidence` | Model version, endpoint/harness, supported efforts/capabilities, official guidance and benchmark references, source times, prices and applicability. |
| `StrategyRanking` | Profile/evidence digest, ordered valid strategy IDs, model/effort/review paths, evidence references and reason codes; no spending authority. |
| `FundingPool` | Provider/account/organization, shared pool/grant identity, units, issued/remaining amounts, window epoch, actual expiry/reset, observation watermark, freshness and uncertainty. |
| `RoutingSettings` | Smart Routing ON by default with persistent explicit OFF, separate opt-in API Usage enablement, opaque secure-key bindings, worker constraints, API percentage or `no-limit`, task reserve margin, subscription preference thresholds, grant-expiry horizon, policy revision. Read Workstreams preference from its existing authority. |
| `Admission` | Bound strategy/credential/funding vector, padded finite execution envelope, attempt/session/message identity, single-use authority and settings/evidence revisions. |
| `Claim` | Per-task remaining buffered reservation or settled-but-unreflected debit, pool/window attribution, dispatch owner, reconciliation state and expiry metadata; no global execution slot. |
| `LifecycleEvent` / `TaskSettlement` | Canonical task lineage, actual observations, independent acceptance provenance, complete cost vector and unresolved exposure. |

Keep one ledger owner and atomic multi-pool reservations. Bound stored data and enforce idempotency. Do not import legacy settings as new authority or create a second budget writer for Python callers. Credit settings, selection mode and rollout qualification are distinct.

**Exit:** schema/contract tests cover unknown model/effort names, shared pools, settings revision changes, concurrent callers and interrupted persistence.

## 6. P2 — Evidence service, Jev and router ranking

1. Discover model availability and supported effort controls for each actual account/endpoint/harness.
2. Cache versioned official guidance and comparable benchmark evidence; refresh outside the hot path on maintenance or relevant changes. Unknown evidence stays unknown.
3. Implement shared `ensure_profile` for Workstreams and ordinary delegation: reuse an applicable profile or normalize existing job facts first; invoke Jev only for missing/ambiguous semantic fields. Keep requirement floors and per-field provenance. Resolve the deployed profiler version through catalog/configuration data.
4. Implement the router-role resolver: qualified free endpoint → lightest qualified available subscription → unavailable. Resolve this from evidence without recursive model calls.
5. Give the router a compact profile and qualified candidate/evidence records. Validate its returned strategy/evidence IDs and cache suitability rankings by profile and evidence version.
6. Charge bounded profiler/router inference to its actual funding scope. A gateway's profiler bill is not automatically covered by a vendor's subscription-linked grant.

Use official recommendations for supported configurations and relevant benchmarks for model/effort fitness. Do not let model memory substitute for supplied evidence. Benchmarks for routing must measure selection quality, structured output, risk handling and latency; a coding leaderboard alone is insufficient.

Profile cache keys cover objective, role, relevant scope/context, tools, review/risk requirements and profile-contract version—not balances, retry counters or the whole mutable task record. Evidence/catalog changes invalidate ranking when needed without automatically reprofiling. Use bounded ready-wave enrichment where supported; a new assignment with complete metadata can need zero profiling calls.

**Exit:** fixtures prove metadata/profile reuse with zero Jev calls, selective semantic invalidation, role separation, supported-effort validation, dynamic catalog adoption, uncertainty and fallback ordering. No hard-coded model selection table remains.

## 7. P3 — Funding adapters and deterministic allocation

Implement subscription windows and API grant observations behind the same funding contract. Separate benefit credits, other prepaid API credits and purchased subscription overage; similar names do not make them interchangeable. Deduplicate an organization's allowance across its keys/worktrees.

Before direct API Usage candidate selection, require explicit API Usage enablement, a ready secure key binding for the intended provider/organization/project and workflow scope, and verified task/endpoint/funding eligibility. Persist opaque key references only; the trusted transport resolves secrets privately from Keychain or a qualified host secret store. Recheck enablement and binding before each API Usage send or reservation increase. An absent, disabled or unavailable connection removes that route and its expiry signal. Independently enabled OpenRouter candidates use their own funding/permission gate, not the direct API Usage key or grant. If no eligible API-side candidate remains, API share is zero when subscription execution is feasible.

Implement the canonical design's credit calculation:

```text
protected_balance = (1 - configured_fraction) * issued_subscription_allowance
available = max(0, reconciled_remaining - protected_balance - reservations - uncertainty)
```

Percentage applies to the issued allowance, not the remaining balance. `0%` disables API-credit consumption. `no-limit` removes the percentage protection and expands to verified existing eligible credit lots, while retaining finite request bounds and expiry checks. Never mint a new grant from a calendar rollover or clear uncertain costs on renewal.

Replace the global `slot` / `request_in_flight` gate with per-task buffered claims:

```text
reserve = max(round_up(estimated_remaining_cost * (1 + task_margin)), next_requests_bound)
admit only when reserve fits current headroom in every consumed pool/window
```

Start with a configurable 20% task margin, separate from the monthly credit protection. Headroom subtracts all other remaining reservations. Commit multi-pool claims atomically; then workers run in parallel. Charge measured spend and reduce remaining holds together, release confirmed unused padding, retain uncertain exposure, and require a top-up before exceeding the envelope. Transfer a parent's reserved review provision to its child rather than reserving it twice. Only short ledger transactions serialize, not task execution.

For automatic worker selection, first choose subscription versus API using canonical design section 7.2:

```text
S = clamp((u - 0.65) / (0.85 - 0.65), 0, 1)
E = clamp(1 - hours_until_eligible_grant_expiry / 72, 0, 1)
API_share = 1 - (1 - S) * (1 - E)
Subscription_share = 1 - API_share
```

The numbers are configurable defaults. `u` is the lowest maximum projected window utilization among suitable, feasible subscription routes, including existing and new buffered reservations. Set `E = 0` when no eligible expiring benefit grant exists. Exclude disabled API Usage routes and expired/insufficient grants before scoring; unknown utilization is not healthy utilization. An unavailable class forces selection of the other feasible class; if neither is feasible, keep the task pending. The credit ceiling remains a hard limit regardless of API share.

Use deterministic workload deficits between positive-share classes, with API winning ties, and commit counters with admission. Within the chosen class, use the existing fundable-work/reset-aware pool allocator; honor the eligible grant that supplied expiry preference. Form weights per pool, not model alias, and reserve review on its actual pool. Expected lifecycle costs rank strategies; bounded envelopes determine admission. This class decision uses cached observations and arithmetic only, with no extra inference, network call or forecast.

Use an evidence-ranked, conservatively admissible strategy when calibration is insufficient, with an explicit reason. Never compare dollars to quota percentages or assume unknown headroom is infinite. A 98% policy is not by itself a guarantee against provider funding spill; strict API eligibility must pass P0.4.

Exclude zero-demand terms from `H / c`. Zero-priced candidates still require qualified request/concurrency controls; absent finite comparable pressure uses evidence ranking and stable tie-breaking, never division by zero or infinite-capacity assumptions. A direct grant's expiry preference must select a route capable of consuming that grant, not an unrelated gateway.

**Exit:** reproduce the canonical design's 98% example, 0/100/`no-limit` cases, padded parallel-task example and preference examples (75% utilization → 50% API share with distant expiry; 40% utilization → about 67% API share with 24 hours left). Check API enablement/key exclusions, class overrides, concurrent budget races, top-ups/releases, atomic deficits, shared reviews, stale observations, prorated/annual grants and short-window vetoes.

## 8. P4 — Managed spawn and request enforcement

Connect Workstreams' existing ready-job preparation to `ensure_profile` → cached/new ranking → fresh dependency/ownership/resource and funding admission → bound launch. The same path handles explicit delegation, nested workers, reviewers and repairs. Keep the coordinator session's selected model and effort fixed.

For a medium–large task, Workstreams establishes shared interfaces, schedules independent ready jobs concurrently and releases dependent checks/review after accepted results. Inherit requirement floors, not the parent's complexity label. Preserve existing child identity for unchanged continuations. Backlog estimates do not reserve speculative execution capacity; required review provisions are transferred rather than duplicated. Recheck readiness and ownership at dispatch even on a profile/ranking cache hit.

Commit a single dispatch authority per attempt with the applicable settings revision and funding claims. Before launch, revalidate the bound route, API enablement/key binding where applicable, and host evidence. Handle lost acknowledgments conservatively; retry only with proven host deduplication or proof of non-launch. Test duplicate callers separately from successful parallel admission of distinct tasks.

Guard managed provider requests against the admitted model/effort, credential, funding scope and remaining envelope. Cover tool-loop continuation, auxiliary requests and retries. Top-ups require new admission, not an unbounded worker loop. Required review capacity cannot be silently spent on a different account.

Implement immediate Smart Routing disable without model inference: invalidate unsent automatic authorizations, preserve active/unknown claims, and use configured worker choices for subsequent spawns. Preserve financial checks in OFF mode. Returning to ON requires fresh decisions.

**Exit:** 15 callers racing for the same attempt produce at most one worker, while distinct ready jobs run concurrently when resources fit. Dependency-plan tests prove reuse and stale-readiness rejection. Bypass attempts are prevented before transmission on qualified routes; coordinator settings stay fixed. Default ON and explicit OFF cover pending inference, pre-dispatch, active workers and nested spawns independently of Workstreams/API Usage settings.

## 9. P5 — Accounting, reconciliation and retention

Consume workflow's canonical task/check/review evidence. Attribute coordinator, profiling, routing, worker, review, repair, escalation and auxiliary costs once per root task, retaining failures and abandoned attempts. Keep quota, credits, tokens, actual monetary spend and elapsed time separate. Preserve reviewer rescue as incremental rework rather than evidence of executor quality.

Anchor reservations and settled debits to the relevant observed pool/window epoch. A newer timestamp alone does not prove consumption is reflected. Reset/expiry does not prove a worker stopped. Bound unresolved admission counts while retaining minimum safety state until authoritative reconciliation.

Retention defaults: snapshots 24 hours, diagnostic decision/events 14 days, task features 30 days, rolling aggregates 60 days. Filter expired evidence on every read; clean up while running and on next start. Active cycle caps, grants, unresolved claims and required idempotency state outlive diagnostic retention until safe settlement. No prompts, source, transcripts or credentials belong in the router store.

**Exit:** complete review/repair trees, failures, duplicate receipts, external consumption, event gaps, restart and prolonged inactivity retain correct costs and caps. One accepted task costing 100 and one rejected task costing 90 report 190 per accepted delivery; unresolved work is separate.

## 10. P6 — Workflow integration and breaking cutover

Use the canonical design's implementation evaluation to integrate API Usage, OpenRouter and native delegation under one default-enabled selector. Keep Workstreams orchestration, host-resource admission, canonical evidence and independent acceptance ownership. Update client/policy/management entry points to use shared preparation; skills remain thin guidance, not enforcement.

Retire old routing modes, budget schemas, selector branches and legacy aliases after stopping their admissions and settling outstanding work. Install the replacement service with Smart Routing ON by default and respect explicit OFF. API Usage remains separately excluded until enabled with secure credentials; Workstreams retains its project preference. No compatibility layer or simultaneous old/new financial writers are part of this plan. Keep historical audit facts and unresolved ownership safe.

Remove the serial-execution rule from Manage API Usage adapter instructions and all `slot`-owned runtime admission/dispatch/completion logic in both `usage_budget.py` and `usage_routing.py`. Recovery enumerates every pending attempt. Replace tests expecting a second affordable task to fail with distinct-task concurrency and shared-headroom assertions. OFF mode must not revive any retired automatic selector.

The implementation no longer depends on proposal-only restrictions merely because an older adapter used them. Each actual worker/tool capability must still qualify before implementation or review work is enabled.

**Exit:** the chosen source baseline has one automatic selector, one authoritative funding ledger and a tested OpenCode client path. Existing coordinator choices and user permissions remain respected. Deleting the standalone planning checkout has no runtime effect.

## 11. P7 — Evaluation and rollout

Use historical tasks from the user's repositories with fixed acceptance checks. Compare strongest-qualified, deterministic-baseline and model-ranked worker policies under identical chronological arrivals, concurrent demand and reset/expiry events. Keep each policy's state independent during replay.

Measure accepted deliveries per constrained budget, cost per accepted delivery including failures, review/repair burden, unused expiring quota/credits, profiler/router overhead, warm/cold dispatch latency, funding violations and recovery errors. Pre-register quality margins, sample strength, efficiency and latency requirements before seeing results; a small fixed sample is not a non-inferiority guarantee.

Qualify through shadow and bounded canary before releasing enforced automatic routing. Ship the qualified replacement ON by default; preserve an explicit user OFF. If a route or required role is not ready, report the affected assignment blocked/unsupported rather than silently switching off the feature or executing an old selector. Rollback uses a previously qualified routing policy or explicit OFF/manual selection through the new allocator, preserving ledger limits and claims.

Automatic learned routing weights remain deferred to a later version with sufficient independently verified evidence, uncertainty, replay and rollback. Dynamic vendor/benchmark model selection and bounded consumption calibration are already v1 requirements.

**Required package check:** `python3 -B scripts/validate.py` in vibe-workflow. Add focused router/host tests and representative instruction-loading checks as implementation lands. Report package checks separately from observed client/provider execution.

## 12. Review traceability and completion

The [independent review](independent-review.md) applies to its recorded v1.0 artifact, not this revised plan. Carry its findings forward as acceptance requirements:

| Finding | Current handoff |
|---|---|
| Funding/account binding | P0.3–P0.4, P3–P4 |
| Exclusive dispatch and ambiguous launches | P0.7, P1, P4 |
| Stale observations and double debits | P3, P5 |
| Stage-bound reviewer funding | P3–P4 |
| Retention versus unresolved safety | P5–P6 |
| Rescue attribution and sparse quality evidence | P2, P5, P7 |
| Deterministic allocation and comparable units | P3 |
| Cohort costs including failures | P5, P7 |
| Expiry after inactivity | P5 |
| Chronological replay and quality thresholds | P7 |

**Definition of done:** a user works with their chosen coordinator. Workstreams prepares dependency-aware, bounded assignments in its canonical job records. Each assignment carries a validated reusable profile derived from existing metadata and enriched by Jev only when necessary. Built-in Smart Routing is ON by default and supplies an evidence-backed model/effort ranking plus fresh deterministic funded admission for every launch. Ready independent jobs run concurrently within buffered reservations; all supported nested work uses the same path. API Usage participates only with explicit enablement and a secure key binding. Independently verified outcomes settle once into root-task accounting. Users can disable automatic selection without losing ownership or weakening funding limits. No redundant task graph, legacy selector or standalone-checkout dependency remains.

## Appendix A — Existing-feature impact and implementation ownership

The authoritative feature-by-feature matrix is [design Appendix A](../../vibe-workflow/docs/superpowers/specs/2026-11-08-usage-routing-opencode-subscriptions-design.md#appendix-a--impact-on-every-existing-feature). It covers all ten distinct skills across inspected `main` and this checkout, plus cross-cutting runtime features. The table below assigns implementation ownership so the matrix does not become an unimplemented wish list.

| Feature IDs | Feature | Planned effect | Owner |
|---|---|---|---|
| F01–F02 | Prepare my Workspace; Onboard my Project | Built-in readiness/default-ON reporting, explicit OFF, minimal assignment binding; no automatic account or paid-feature activation. | P0/P1/P6 |
| F03, F11 | Workstreams; planning/native delegation/continuation | Shared preparation, reusable profiles and existing job/dependency ownership; replace model-choice branches. | P2/P4/P6 |
| F04 | Manage API Usage | Secure opt-in connection and allowance settings; concurrent buffered claims replace the global busy slot. | P1/P3/P4/P6 |
| F05–F07 | Manage OpenRouter; proposals; workers | Independent activation/funding, reused discovery, shared ranking/admission; actual executor capabilities remain qualified separately. | P2/P3/P4/P6 |
| F08 | LLM Council (checkout feature) | Route explicitly invoked answer/critique/chair assignments with Council constraints; preserve independence, anonymity and group choice. | P2/P4/P6 |
| F09 | Review my Project (checkout feature) | Reuse review scope and profiles; route delegated reviewers without authorizing edits/enrollment. | P2/P4/P6 |
| F10 | Manage Observability | Optional sanitized routing/usage export; local accounting works without export. | P5/P6 |
| F12 | Resource admission; capacity recovery | Both resource and financial gates bind the same job revision; no implicit restart or ownership release. | P4/P5/P6 |
| F13–F14 | Tests/security/code review/evidence; worktrees/delivery/merge assurance | Retain independent checks and actual delivery authority; add route/cost references without granting merge or cleanup rights. | P4/P5/P6 |
| F15–F17 | Naming/Habblaud; context/handoff; startup/maintenance/diagnostics | Truthful route visibility, bounded references/cache refresh and zero inference for status/rendering. | P2/P5/P6 |

## Appendix B — Writing-plans execution checkpoints

These checkpoints refine P0–P7; read each phase's requirements and the spec before its task. P0 evidence can reject a host binding without changing the agreed policy. Do not invent provider methods to make later tests pass. Tests below are future implementation work; package validation of these documents does not run them.

### Task P0: Pin the source and qualify adapter boundaries

**Files:** Create `docs/smart-routing-feasibility.md`, `docs/smart-routing-capability-matrix.md`, `tests/fixtures/smart-routing/capabilities.json`; inspect the feature paths in design Appendix A.

**Interfaces:** Produce a capability manifest with source revision, host version, route identity, observed controls, evidence references and `qualified | unsupported | unverified` per P0 gate. No secrets or prompt bodies.

- [ ] Reconcile the `main`/checkout feature inventory and record every F01–F17 owner and the actual test/runtime toolchain.
- [ ] Probe documented interfaces within authorized bounds and record requested versus observed behavior for P0.1–P0.10. A missing proof is `unverified`, not `qualified`.
- [ ] Run `python3 -B scripts/validate.py`. Expected: package validation passes; observed provider/host results are reported separately in the manifest.
- [ ] Review the manifest and stop work dependent on an unqualified boundary. Checkpoint only the sanitized artifacts.

### Task P1: Define shared records and independent settings

**Files:** Create `packages/vibe-router/package.json`, `src/contracts.ts`, `src/settings.ts`, `test/contracts.test.ts`, `test/settings.test.ts` under that package; reuse the current workflow job identity contract.

**Interfaces:** Define the contracts in P1 above. `resolve_settings(input: Partial<RoutingSettings>): RoutingSettings` defaults Smart Routing ON and API Usage OFF. `validate_assignment(input: unknown): AssignmentBinding` validates existing root/job identity and semantic/profile bindings. Add `Blocked = { state: 'blocked'; reason: string }` as the common failure result; evidence, snapshot and ledger handles used by later tasks are explicit schema-backed records, not credentials.

- [ ] Write failing settings/contract tests with these assertions:

```ts
expect(resolve_settings({}).smart_routing).toBe(true)
expect(resolve_settings({}).api_usage_enabled).toBe(false)
expect(resolve_settings({ smart_routing: false }).smart_routing).toBe(false)
```

- [ ] Configure the package test script as `vitest run`; run `npm --prefix packages/vibe-router test -- test/contracts.test.ts test/settings.test.ts`. Expected initially: missing implementation/assertion failures.
- [ ] Implement the two interfaces, bounded JSON validation and explicit policy defaults; keep secret values out of all schemas.
- [ ] Repeat the command. Expected: all contract/settings tests pass, including unsupported effort names preserved as data and malformed bindings rejected. Review and checkpoint the task.

### Task P2: Reuse profiles and rank from supplied evidence

**Files:** Create `packages/vibe-router/src/profiles.ts`, `src/evidence.ts`, `src/ranker.ts`, `test/profiles.test.ts`, `test/ranker.test.ts`; adapt `runtime/delegation.py` metadata extraction and OpenRouter discovery's read-only output.

**Interfaces:** `ensure_profile(assignment: AssignmentBinding, supplied: Partial<TaskProfile>, evidence: CandidateEvidence[]): Promise<TaskProfile | Blocked>`; `rank_strategies(profile: TaskProfile, candidates: CandidateEvidence[]): Promise<StrategyRanking | Blocked>`. Injectable profiler/ranker clients count inference in tests; `CandidateEvidence` carries snapshot identity/applicability. Returned records bind the exact semantic/profile/evidence digests.

- [ ] Write failing `profiles.test.ts` cases: complete metadata yields zero profiler calls; balance/progress changes reuse the profile; changed role or scope invalidates relevant fields; another project's incompatible scope cannot reuse it (RF2).
- [ ] Write failing `ranker.test.ts` cases: evidence refresh reranks without reprofiling; absent qualified free router selects a qualified subscription option; unsupported settings/evidence IDs produce `Blocked`; no implicit paid fallback.
- [ ] Run `npm --prefix packages/vibe-router test -- test/profiles.test.ts test/ranker.test.ts`. Expected: failing unimplemented behavior.
- [ ] Implement bounded evidence caching, semantic keys, conditional Jev enrichment and role resolution/ranking using the interfaces above.
- [ ] Repeat the command; expect PASS and exact inference-call counts. Review and checkpoint without adding a second job graph.

### Task P3: Implement funding eligibility and deterministic preference

**Files:** Create `packages/vibe-router/src/funding.ts`, `src/preference.ts`, `test/funding.test.ts`, `test/preference.test.ts`; adapt secure provider/grant observations through the P0-qualified host binding.

**Interfaces:** `eligible_funding(settings: RoutingSettings, pools: FundingPool[]): FundingPool[]`; `api_share(utilization: number, expiry_hours: number | null, low = 0.65, high = 0.85, horizon_hours = 72): number`. Apply feasible-class overrides outside the pure score; no expiry signal means `null` and `E = 0`.

- [ ] Write failing eligibility tests for RF1: API Usage OFF excludes direct API grants; independently enabled OpenRouter uses only its own wallet/permission; revoked key or wrong organization blocks that route; stored keys alone never enable funding.
- [ ] Write failing preference tests with these exact anchors and hard-limit cases:

```ts
expect(api_share(0.40, 480)).toBe(0)
expect(api_share(0.75, 480)).toBeCloseTo(0.5)
expect(api_share(0.40, 24)).toBeCloseTo(2 / 3)
expect(api_share(0.90, null)).toBe(1)
```

- [ ] Run `npm --prefix packages/vibe-router test -- test/funding.test.ts test/preference.test.ts`; expect initial failures.
- [ ] Implement the spec's 98%/0–100%/`no-limit` headroom policy, maximum-window/minimum-qualified-route pressure, stable class/pool deficits and finite zero-cost handling (RF4). Keep all prices/balances in their actual units.
- [ ] Repeat the command; expect PASS for the published examples, hard overrides and independent integration controls. Review and checkpoint.

### Task P4: Reserve and dispatch distinct ready assignments

**Files:** Create `packages/vibe-router/src/ledger.ts`, `test/ledger.test.ts`, `integrations/opencode/src/dispatch.ts`, `integrations/opencode/test/dispatch.test.ts`; modify the workflow's shared assignment preparation/continuation bridge.

**Interfaces:** `reserve(assignment: AssignmentBinding, strategy: StrategyRanking, settings: RoutingSettings, pools: FundingPool[]): Admission | Blocked` commits claims and one dispatch authority through the ledger service. `dispatch(admission: Admission): Promise<{ attempt_id: string; session_id: string; state: 'launched' | 'unknown' } | Blocked>` consumes that authority after fresh host/workflow gates. Initialize the host package's Vitest test script alongside its dispatcher.

- [ ] Write failing ledger tests: the $30 example admits $6/$12/$9.60 in parallel; a fourth $3.60 claim waits; duplicate attempt IDs do not reserve again; racing distinct tasks cannot overdraw.
- [ ] Write failing dispatcher tests for RF3: reopen a dependency, change writer ownership, disable API Usage or change the credential binding after preparation; assert zero sends. A lost acknowledgment retains an unknown claim and does not retry blindly.
- [ ] Run `npm --prefix packages/vibe-router test -- test/ledger.test.ts` and `npm --prefix integrations/opencode test -- test/dispatch.test.ts` separately; expect initial failures.
- [ ] Implement per-attempt SQLite claims, parent/child provision transfers, single-use dispatch and all P0-qualified request guards. Keep external calls outside write transactions.
- [ ] Repeat both commands; expect PASS, including 15 same-attempt callers producing at most one worker and affordable distinct jobs executing concurrently. Review and checkpoint.

### Task P5: Settle task trees and expire only disposable evidence

**Files:** Create `packages/vibe-router/src/accounting.ts`, `src/retention.ts`, `test/accounting.test.ts`, `test/retention.test.ts`; integrate workflow `evidence.py` and qualified observability/display event adapters.

**Interfaces:** `record_event(event: LifecycleEvent): void` is idempotent in the ledger; `settle_root(root_task_id: string): TaskSettlement` consumes canonical independent outcome references; `expire_evidence(now: number): { removed: number; retained_safety_records: number }` operates on the same store.

- [ ] Write failing RF4 tests: duplicate child receipts charge once; 100 accepted + 90 rejected yields 190 per acceptance; first task in the $30 example settles at $4 and releases $2 unused; ambiguous timeouts do not release exposure.
- [ ] Write failing inactivity tests: expired cached evidence cannot influence a decision; active cycle caps, unknown claims and ownership survive diagnostic retention; display/export never manufactures acceptance.
- [ ] Run `npm --prefix packages/vibe-router test -- test/accounting.test.ts test/retention.test.ts`; expect initial failures.
- [ ] Implement idempotent event attribution, authoritative settlement, reflected/unreflected debit accounting and the 24h/14d/30d/60d retention rules.
- [ ] Repeat the command; expect PASS. Review and checkpoint with observability OFF producing no export and no loss of local accounting.

### Task P6: Replace selectors at every feature boundary

**Files:** Modify the feature paths listed in design Appendix A, `policies/03-agent-delegation.md`, `docs/policy-sources.md` and `clients/instruction-map.json`; create `tests/test_smart_routing_feature_contracts.py` with host/RPC doubles. Add the shared bridge at `runtime/routing.py` and a private transport adapter at `runtime/adapters/opencode.py` after baseline reconciliation.

**Interfaces:** `prepare_assignment(job: dict, *, client: RoutingClient) -> dict` is the single Python workflow entry point. `RoutingClient.prepare(job: dict) -> dict` transports the versioned job/profile references to the router service and returns either admission metadata or a typed blocked reason; its implementation never writes a second financial ledger. Data is validated against P1's exported schema.

- [ ] Write failing feature-contract scenarios for every F01–F17 row using adapter doubles. RF5 asserts zero inference/secret access on startup/status/naming; Council requires explicit invocation and retains group/independence; review-only scope rejects edits; merge authority cannot be inferred from routing success.
- [ ] Run `python3 -B -m unittest tests.test_smart_routing_feature_contracts`; expect initial failures.
- [ ] Replace native/external/API/OpenRouter selection callers with the shared entry point; remove both API slot gates and old automatic selectors. Keep endpoint, scope, independent acceptance and explicit user controls as constraints. Update instruction loading and bounded skill entry points.
- [ ] Repeat the targeted command and run `python3 -B scripts/validate.py`; expect PASS. Run instruction-loading walkthroughs separately for Workstreams, API Usage, OpenRouter, Council and project review. Review and checkpoint after no legacy selector or second writer remains.

### Task P7: Prove the composed policy and release default ON

**Files:** Create `packages/vibe-router/test/replay.test.ts`, `docs/smart-routing-validation.md`; update the actual package/host release configuration after P0 pinning.

**Interfaces:** Replay calls the same P2–P5 interfaces with isolated ledgers and recorded arrivals, never a second production optimizer. Report policy/evidence versions, quality, overhead, failures and uncertainty separately from funding totals.

- [ ] Write the replay regression cases before changing release defaults: equivalent task mix, reset/expiry boundaries, concurrent claims, model-catalog refresh, OFF toggles and a six-job dependency plan with zero redundant Jev calls.
- [ ] Run `npm --prefix packages/vibe-router test -- test/replay.test.ts`; expected regression cases fail before the composed path is complete.
- [ ] Connect the production interfaces and execute the pre-registered comparison; qualify authorized live host cases separately. Default ON still reports unsupported/unready routes truthfully; it never activates paid features.
- [ ] Repeat the replay command and package validation; expect PASS. Publish measured results and remaining limitations in the validation record. Review and checkpoint release changes only after the required gates pass.

## Appendix C — Whole-plan self-review

Reviewed with Superpowers `writing-plans` 7.0.2 on 2026-11-08. The design and this complete handoff were checked for coverage, actionable steps, interface consistency, review-focus cases and proportion. Appendix A covers each existing skill/runtime feature; Appendix B maps it to files, interfaces and regression ownership.

Corrections from review: distinguish OpenRouter funding from direct API Usage, preserve explicit Council/project-review boundaries, prevent management/status inference, and define finite handling for zero-priced routes. The canonical design records the same corrections. This author review does not close P0's live capability/funding gates or constitute independent approval of the implementation.
