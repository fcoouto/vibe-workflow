# Smart Routing — Vibe Router integration design

> **9 November 2026:** activation is now the only approval for API Usage and OpenRouter, and Manual mode is retired. Where this document requires a separate approval, permission, approval line, endpoint opt-in or Manual selection, the current [Agent Delegation](../../../policies/03-agent-delegation.md#select-capability-and-dispatch) and [shared external delegation](../../../clients/external-delegation.md#select-before-dispatch) contracts supersede it.

- **Version:** 1.5 design draft
- **Date:** 2026-11-08
- **Status:** Agreed product direction recorded and amended for incremental delivery (Appendix B); execution and enforcement remain gated by P0 qualification.
- **Product:** Vibe Router is vibe-workflow's built-in, default-enabled routing component, replacing existing selection logic. Workstreams owns orchestration; API Usage supplies opt-in API-credit funding. OpenCode V2 is the first target for enforced worker routing.

## 1. Decision and scope

Use one routing pipeline for delegated implementation, research, review, repair and escalation work. The coordinator keeps the user's selected model and effort. It decides what to delegate, prepares bounded assignments, supervises execution, verifies results and synthesizes the response. The router chooses the workers' execution strategies.

This design takes precedence over conflicting routing provisions in the earlier Vibe Router draft and existing vibe-workflow features. In particular:

- Ship Vibe Router inside vibe-workflow with Smart Routing enabled by default. Replace the automatic selection logic of native delegation, API Usage and OpenRouter with one routing service; transport and permission checks retain their separate responsibilities.
- Keep Workstreams as the owner of decomposition, dependencies, readiness and child supervision. Reuse its canonical job records and valid profiles; Jev enriches missing or ambiguous task fields only when needed.
- Keep the coordinator's model, effort and authentication under user control; Smart Routing never switches them.
- Route every managed worker spawn, including nested workers and independent reviewers, through the same admission path.
- Select model versions and reasoning settings from current vendor recommendations and comparable benchmark evidence. Portable workflow rules contain no hard-coded commercial model IDs, effort ladders or hand-maintained model tiers.
- Rank worker strategies deterministically from a strategy table compiled at maintenance refresh (section 5.2). A model ranker is optional and is promoted only when evaluation shows it beats the deterministic ranker (section 5.3); its unavailability never blocks delegation.
- Balance subscription headroom and subscription-linked API credits. Default API allowance usage is 98%, configurable from 0–100% or `no-limit` as defined in section 6.
- Consider API Usage only after the user enables it for the scope and provides a securely stored, correctly bound API key. Smart Routing and API Usage enablement are independent.
- Treat every provider the same way. Claude and OpenAI each offer subscription routes and API Usage routes, chosen by the same allocator under the same rules; no provider-specific funding rule exists (section 6.1).
- Replace the one-task-at-a-time API restriction with buffered per-task reservations. Independent tasks execute concurrently whenever their combined claims fit available funding and resource limits.
- Prefer subscription execution while headroom is healthy; increase API preference as subscription windows fill or eligible API grants approach expiry, using the lightweight equation in section 7.2.
- Let users disable Smart Routing at any time. Automatic selection, funding settings and execution accounting are distinct controls.
- Make a clean, breaking replacement of overlapping routing interfaces and policies. No backward-compatible commands, schemas, aliases, dual selectors or dual ledger writers are required.
- Preserve whole-task cost accounting, independent acceptance, bounded evidence and uncertain-cost recovery from the Vibe Router plan. Learned routing weights remain a later phase; dynamic evidence-based model selection is part of v1.

The standalone vibe-router checkout may remain as a planning archive. The completed feature must not depend on that checkout, its absolute path or its historical documents.

## 2. Component and responsibility matrix

| Component | Responsibility |
|---|---|
| **Jev — optional task profiler** | Enrich unresolved semantic profile fields only when the user has configured it and the coordinator-supplied profile and metadata leave a gap (section 5.1). |
| **Deterministic ranker** | Look up qualified model/effort strategies for a profile in the compiled strategy table (section 5.4). An optional model ranker may supply the ordering only after promotion (section 5.3). |
| **Deterministic allocator** | Check budgets, choose the funding route, reserve capacity and authorize execution. |

**Authority boundary:** profiles describe; rankers order; the allocator validates and authorizes. No model can invent capabilities, grant itself funding, relax review requirements or authorize a launch.

Supporting responsibilities:

| Component | Responsibility |
|---|---|
| Coordinator using Workstreams | Task scope, decomposition into coherent outcomes, dependencies, bounded briefs with structured `TaskProfile` fields, ready-job scheduling, supervision, integration and final response; fixed user-selected model/effort. |
| Evidence/catalog service | Account-specific model availability, supported settings, guidance, benchmark snapshots, prices and provenance, compiled into a validated strategy table at maintenance refresh. |
| Funding adapters | Subscription observations and API grant/balance observations, including billing class, identity, units, expiry and freshness. |
| Host adapter | Policy-free: enforce the admitted worker route before transmission, launch through the host, expose receipts and lifecycle events. |
| Workflow evidence/accounting | Attribute the task tree once, retain failures and uncertain charges, and record independently verified acceptance. |

Ranking is a small selection operation, not the coordinator or the worker. A table lookup or a lightweight model ranker may recommend a much more capable worker.

## 3. Smart Routing and delegation behavior

Smart Routing is built in and defaults to **ON** when no explicit setting exists. Users can set OFF at any time; their explicit choice survives restarts and updates. There is no separate Vibe Router installation or activation step. This setting chooses the routing mechanism; it does not create tasks, enable API Usage, configure paid services or broaden existing permissions.

Keep three controls independent: Workstreams governs optional automatic decomposition/delegation under its project preference; Smart Routing governs selection for work that is delegated; API Usage remains OFF until explicitly enabled with a secure key binding. Workstreams OFF still permits explicit delegation and required review through the same routing service. Smart Routing OFF uses configured worker choices through the new allocator, not the retired selector implementations.

OpenRouter, Council, observability, naming and recovery also retain their feature-specific activation rules. Default-on routing does not invoke optional features. Startup, status, naming/rendering, deterministic checks and management commands use local metadata and do not invoke Jev or a model ranker merely to report readiness. Appendix A defines each integration boundary.

Report desired enablement separately from readiness. An unsupported host or missing evidence produces an enabled-but-blocked/unsupported reason for the affected assignment. An unavailable Jev or model ranker never blocks: the assignment uses conservative profile floors and the deterministic ranker, with a visible reason. Do not silently set the feature OFF or bypass it through old routing code. A valid existing profile needs no Jev connection or call merely because Smart Routing is enabled. Default-on applies to qualified, authorized routes; P0 remains the release gate for claimed enforcement.

| Setting | Coordinator | Delegated workers |
|---|---|---|
| Smart Routing ON (default) | User-selected model and effort | Automatic strategy and funding selection within enabled routes and policy. |
| Smart Routing OFF | User-selected model and effort | Explicit or configured worker choices; deterministic admission, funding limits and accounting still apply. |

Configured worker choices live in routing settings as an ordered list per role:

```json
{"worker_choices": {
  "implementer": [{"provider": "…", "model": "…", "effort": "…", "harness": "…", "funding": "subscription"}],
  "reviewer": [], "researcher": [], "repair": []
}}
```

The allocator admits the first feasible entry for the assignment's role; an empty or wholly infeasible list leaves the assignment pending with `worker_choice_unavailable`. Entries are user data validated against the live catalog, not portable rules. An explicit per-assignment user choice overrides the list in both modes.

The coordinator supplies the objective, relevant context, required tools, risk facts, review policy, acceptance criteria, parent task and finite work budget. It asks for an appropriate worker rather than guessing model names. Simple conversational work can stay with the coordinator when delegation would add unnecessary overhead. Implementation ownership and independent review follow the workflow's task policies.

Nested spawns use the same managed interface. Worker-originated model preferences are suggestions, not authority to bypass routing. An explicit user worker selection is a constraint within its authorized scope; if it cannot be admitted, explain the conflict instead of silently substituting a route.

Disabling Smart Routing:

1. Persist the new setting and revision through a deterministic command/control that needs no model response.
2. Invalidate automatic decisions prepared under the old revision. Late profiler/router results cannot dispatch work.
3. Release only claims for which non-dispatch is established. Running and uncertain attempts retain their claims and accounting.
4. Let already-started workers finish within their assigned budgets. Subsequent spawns use the OFF behavior. Explicit interruption remains a separate action.

Re-enabling does not revive stale decisions. Rollout `canary`/`enforced` states describe qualification, not extra user selection modes. There is no shadow or simulation mode: a route is active or it is not, and unready routes are reported as blocked or unsupported rather than computed without enforcement.

Coordinator consumption is included in root-task accounting where observable and in shared-account headroom reconciliation. The coordinator does not need a worker route lease, and the router does not rewrite its session settings.

## 4. Common execution flow

```text
User → fixed coordinator + Workstreams → canonical jobs and dependencies
                              ↓
                 ready assignment's bounded brief
                              ↓
  ensure_profile: coordinator fields/metadata → floors → optional Jev
                              ↓
             capability/evidence qualification
                              ↓
  deterministic ranker orders strategies (model ranker if promoted)
                              ↓
       allocator chooses a feasible funding route
                              ↓
        atomic reservation + dispatch authorization
                              ↓
      host launches a route-bound worker or reviewer
                              ↓
       observed usage + independent workflow checks
                              ↓
                root-task settlement
```

Stage A obtains a valid profile, then performs capability/quality qualification and evidence-backed strategy ranking. A valid profile does not require a new Jev invocation. Hard exclusions belong to code, not Jev. Stage B is deterministic funding allocation, admission and reservation. Every stage change that needs another worker or a larger execution envelope returns through admission.

OFF mode normalizes known requirements and validates the explicitly configured strategy through the same allocator, without automatic profiler/router inference.

### 4.1 Workstreams integration and one profile owner

Extend the existing canonical job with an assignment-semantic digest, `profile_ref`, `ranking_ref` and admission references. Keep root/job IDs, role, dependencies, resource claims, acceptance checks, attempts and ownership in workflow's existing records. The router owns selection artifacts and financial claims referencing those IDs, not another task graph or completion state machine.

Ordinary Workstreams is currently a project preference that does not require advanced managed-runtime enrollment. Preserve that lightweight entry path: bind routing artifacts to the existing job/host-attempt identity, using the minimum workflow assignment record needed for admission. Built-in routing must not require a second Workstreams activation or enrollment into unrelated resource/recovery features.

Both Workstreams and ordinary explicit delegation call one shared preparation path:

```text
prepare_assignment(existing_job)
  → ensure_profile(job semantics, existing metadata/profile)
  → obtain_ranking(profile, current applicable evidence)
  → fresh dependency/ownership/resource and funding admission
  → bound launch
```

`ensure_profile` first validates and normalizes known facts or a reusable profile. A complete, applicable profile takes a zero-inference fast path. Free-text complexity/risk labels are inputs, not proof of a complete profile. The coordinator supplies the structured fields when it writes the job (section 5.1); unresolved fields take conservative floors, or optional Jev enrichment where configured, preserving field provenance and deterministic requirement floors. Bounded batch enrichment of ready jobs is allowed where the decision API supports keyed per-job results. Never require a root profiling call followed by another classification of every child by default.

Inherit applicable permissions, deadlines and requirement floors. Estimate each child's own role, scope and complexity; a large parent does not make every leaf large. Reviewer profiles differ from implementation profiles when the role or requirements differ. Unchanged continuations reuse the same profile and existing child where valid; a materially changed assignment gets a new semantic revision without rewriting the authority of already-running work.

| Artifact | Reuse/invalidation rule |
|---|---|
| Task profile | Key by objective, role, relevant scope/context, tools, review/risk requirements and profile-contract version. Revalidate on material semantic changes; normalize or enrich only affected fields. |
| Strategy ranking | Key by profile, permitted route scope and applicable catalog/guidance/benchmark snapshot. Evidence/model changes can require ranking without reprofiling the task. |
| Admission | Recheck for every new launch, continuation authorization or envelope increase. Cached profiles/rankings confer no spending or ownership authority. |

Balances, progress timestamps, retry counters, unrelated plan revisions and mere dependency completion do not invalidate the profile. A dependency's changed interface or context requirements can. Do not key the profile cache on the whole mutable task record. Coalesce concurrent requests for the same valid profile/ranking; bind results to the correct job and revision before use.

### 4.2 Medium–large task execution

For a feature with a shared interface, backend and UI work, Workstreams first establishes the interface, then schedules independent backend/UI jobs when their claims and dependencies permit parallel work. Integration checks follow accepted implementation results; independent review and any repairs use their proper roles. Readiness and ownership are rechecked at dispatch, even when profile/ranking reuse succeeds.

Prepare ready jobs just in time. Planning estimates for the backlog are not spendable reservations; claim capacity for admitted work and its required review provision, transferring parent/child provisions without double charging. Unready jobs do not hold speculative execution capacity or trigger repeated model calls. A six-job plan may need no additional profiling calls if its profiles are complete, but every actual launch still needs fresh admission. Workstreams remains the source of readiness, acceptance and progress throughout.

## 5. Profiling and dynamic model selection

### 5.1 Task profiles and optional Jev enrichment

The coordinator is the primary profiler. The Workstreams job schema requires structured `TaskProfile` fields, which the coordinator fills while writing the bounded brief; code validates them behind the shared `ensure_profile` service used by Workstreams and ordinary delegation. Missing or invalid fields take conservative values from deterministic floors (higher complexity/risk bands, stricter review). Jev is an optional enrichment for unresolved semantic fields, available only when the user has configured an OpenCode Console key for it; default-on routing never requires or configures Jev. Its deployed model/version is resolved from the current decision-model catalog and recorded as data; no fixed model ID or reasoning string is compiled into workflow rules. Complete, valid supplied profiles bypass inference.

Input consists of the bounded task brief and relevant ongoing-task metadata. A short follow-up such as “implement it” inherits the actual assignment's scope and risk. Output is a typed `TaskProfile`: task family, complexity, risk, context requirements, tools/modalities, review requirements, uncertainty and per-field provenance. Known context sizes and required tools come from host facts where available, not solely from model estimates.

Version the question set and validate the answer schema. Deterministic task facts and user requirements establish floors that the profiler cannot lower. Low-confidence answers, missing fields or timeouts use conservative floor values; `profile_unavailable` is reserved for assignments whose floors cannot be established, such as an unknown role or scope. Probabilities require calibration before being used as confidence guarantees.

For new or materially changed assignments, first reuse or derive the profile from coordinator fields and existing metadata; call Jev, where configured, only when relevant fields remain missing or ambiguous. Reuse a profile for a retry of the same immutable brief and apply section 4.1 invalidation rules. Store derived features and keyed digests, not prompt bodies or repository content.

### 5.2 Evidence service

Maintain bounded, source-attributed snapshots of:

- Account-specific model versions, endpoints, capabilities, context limits and supported effort controls.
- Vendor guidance, including applicability, publication/check times and content digest.
- Relevant benchmark results with task family, version, harness, endpoint, effort configuration, sample strength and uncertainty.
- Pricing, latency, reliability and data-handling eligibility for the actual endpoint.

Discover availability separately from general model capability. Preserve unknown fields. Do not assume equivalent behavior across harnesses, provider endpoints or aliases. Do not mix incompatible benchmark scales into an invented universal score or treat popularity as quality.

Refresh outside the spawn critical section during maintenance, on relevant catalog/capability changes or explicit refresh. A failed refresh keeps the last applicable verified snapshot, with its original age and a bounded retry policy. Inapplicable or insufficient evidence cannot qualify a new model. New versions become candidates automatically and are promoted only when their evidence qualifies.

This replaces manually curated commercial-model tiers. Task/risk thresholds remain explicit policy; all model/effort fitness decisions must cite supplied vendor guidance and applicable benchmark evidence. Verified local evaluation runs can contribute benchmark evidence; a ranker's remembered opinions about a model cannot.

**Compiled strategy table.** During maintenance refresh, compile guidance, catalog availability and benchmark records into a versioned strategy table keyed by `(role, task_family, complexity_band, risk_band)`. Each row is an ordered list of qualified model/effort/harness strategies with evidence references, source digests and check times. Structured sources compile mechanically. Prose guidance is extracted during the existing maintenance session by the user's coordinator, then validated by code against the live catalog and supported efforts; rows that fail validation are dropped, not guessed. A failed refresh keeps the last verified table with its original age.

**Control-plane data handling.** Any model used for profiling or ranking must have zero-retention, no-training terms for the actual endpoint, unless the user explicitly opts in for that endpoint. Endpoints whose terms forbid confidential data are never eligible. Free status and terms are rechecked at each refresh because free endpoints are time-limited.

### 5.3 Optional model ranker

Model ranking is not required for v1 dispatch. It is promoted only when a pre-registered replay and quality evaluation shows it beats the deterministic ranker (section 13). If a promoted ranker is unavailable, times out or returns invalid output, the assignment uses the deterministic ranking with reason `router_unavailable` or `ranking_invalid` instead of waiting.

Once promoted, select the ranking model itself deterministically from the cached evidence so selecting a router does not recursively invoke another router:

1. Among currently available free endpoints, select the lowest-overhead model/effort that passes the routing-role quality requirements.
2. If none qualifies or is available, select the lightest qualified model/effort from available subscriptions with sufficient headroom.
3. If neither exists, report `router_unavailable` and use the deterministic ranking. Every candidate must also satisfy the control-plane data rules in section 5.2. A cached recommendation is reusable only for its exact still-valid profile/evidence binding; it is not permission to invent a new decision or use a paid fallback.

“Lightest” means the least resource-intensive capability level that satisfies the routing benchmark, not the weakest model regardless of fitness. GLM Flash is an example of a free candidate to evaluate, not a configured default; endpoints whose terms forbid confidential data, such as the current Nemotron free endpoints, are ineligible. Provider effort names and their ordering come from current metadata. Free status is endpoint-specific and must still be valid when dispatched.

Routing benchmarks measure executor/effort suitability, high-risk handling, unsupported-option avoidance, structured-output validity, latency and availability. Coding leaderboard scores alone do not qualify a router. Model names and versions belong in evidence/decision records, not selection branches in code.

### 5.4 Strategy ranking

The deterministic ranker looks up the profile's row in the compiled strategy table. A profile with no matching row uses the next stricter band (floors only rise); if none exists, it uses the strongest evidence-qualified strategy with reason `ranking_conservative`. Ranking makes no model or network call. A promoted model ranker instead receives a compact profile and bounded candidate/evidence records and returns ordered strategy IDs, referenced evidence IDs and short reason codes. Each strategy binds a worker model/effort/harness plus the required reviewer and allowed repair/escalation paths. Validate all returned identifiers and constraints against the supplied snapshot.

Rankers order task suitability; they do not read credentials, change funding policy or do live budget arithmetic. The allocator can choose among qualified strategies according to current funding. A cheap strategy below the task's quality floor is never eligible.

Cache the ranking by profile digest, evidence/catalog version, review policy and permitted route scope. Do not put changing balances into the cached suitability decision. Current funding and authorization are rechecked for every spawn.

Profiler/router calls are bounded, tool-free control operations with their own recorded costs and request limits. They do not create recursive worker spawns. Jev inference uses its explicitly configured funding; a vendor's subscription-linked API grant does not automatically pay an unrelated gateway's profiler bill.

## 6. Funding pools and API Usage

### 6.1 Separate execution routes and shared pools

Represent execution as `provider + account/organization + credential binding + endpoint/harness + model + effort + funding pool`.

| Funding source | Resource and restriction |
|---|---|
| Subscription headroom | Included usage in every applicable native quota window. Purchased overage/extra usage is not included headroom. |
| Subscription-linked API grant | Verified, issued API credit allowance in the linked organization; actual grant expiry and execution eligibility apply. |
| Other existing API credits | Eligible only under an explicitly selected `no-limit` credit policy; never future invoicing or automatic credit purchases. |
| Separately authorized OpenRouter funding | Its own enabled integration, endpoint restrictions and qualified spending controls. Direct-vendor API Usage percentages/keys do not authorize or fund it. |
| Free endpoint | A currently free, qualified route subject to availability and data restrictions; preferred for the router role. |

API Usage becomes the grant/key discovery, credit settings and funding-observation capability of this pipeline. A provider API grant cannot be spent through OpenRouter or OpenCode Console merely because they expose the same model; each has its own billing route. Existing OpenRouter routes retain their separately configured funding and endpoint restrictions under the common allocator.

Keys identify access to an organization/project, not an exclusive grant. Every key in the linked organization shares one grant, including a key stored in OpenCode Console or OpenRouter. Grants exist only for Claude Max, Ultra and Team and ChatGPT Pro and Business; when the grant runs out, usage continues on purchased credit or auto-reload if the organization has them (and is billed as usual if invoiced), otherwise API requests stop, so the router enforces the allowance cap itself. Annual ChatGPT Business grants are issued yearly and expire at the annual cycle end. Deduplicate pools across all keys, projects, sessions and worktrees that share the actual allowance. Match subscription telemetry to the intended account before using it for automatic allocation.

**Billing classification.** Before telemetry or allocation, classify each route (`provider + account + credential + harness/endpoint`) as `included_subscription`, `purchased_usage`, `api_grant`, `api_prepaid`, `gateway` or `ineligible`, from current vendor terms and observed billing (P0.0). Rate-limit telemetry never reclassifies a route, and `purchased_usage` never counts as subscription headroom or enters automatic routing.

**Same model for every provider.** Claude and OpenAI are both available through two kinds of route, under identical rules:

| Route kind | Credential | Funding pool |
|---|---|---|
| Subscription | The provider's subscription login, through a harness the vendor bills as included usage (for example Codex or Sign in with ChatGPT for OpenAI, and Claude Code or OpenCode's Claude Agent provider for Anthropic) | Subscription headroom across that account's windows |
| API | API Usage's scoped enablement and secure key binding | The subscription-linked API allowance (`api_grant`), plus other verified credits only under `no-limit` |

The allocator considers both kinds for each provider and chooses between them with section 7.2's preference. API Usage OFF removes only the API routes; a provider's subscription routes stay available. Billing class belongs to the route (credential plus harness), not to the provider, and P0.0 discovers it the same way for both providers. A route the vendor bills as extra or purchased usage is `purchased_usage` for either provider and is excluded from automatic routing.

**Reference Claude subscription route.** OpenCode running Claude through its Claude Agent provider on the user's Claude plan is the user's working setup (observed 2026-11-08; no API key, cost 0). That provider launches the installed Claude binary through the Claude Agent SDK, so the harness is the Agent SDK, not interactive Claude Code. It is a subscription route under this model, pending P0.0 confirmation, and is routed like Codex only while its standing holds. Its standing is disputed: Anthropic's support text says plan-signed Agent SDK usage draws from subscription limits, while its Agent SDK overview says third-party developers may not offer claude.ai login or rate limits unless previously approved, and an April 2026 announcement said subscriptions no longer cover third-party tools. Treat the route as tolerated, not guaranteed, keep its concurrency caps conservative, and see the [capability matrix](../../smart-routing-capability-matrix.md).

### 6.2 Credit controls

| User setting | Meaning |
|---|---|
| `0%` | Disable API-credit consumption for this configured credit scope. |
| `1–100%` | Maximum fraction of the issued subscription API allowance for its billing cycle. Default **98%**. |
| `no-limit` | Use all verified available API credits in the configured scope, including purchased/prepaid credits if present. No automatic purchases or postpaid spending. |

Thus `100%` is subscription-grant-only; `no-limit` explicitly broadens the eligible credit pool. These controls do not change subscription headroom limits or grant additional source/endpoint permissions.

For one allowance pool/cycle, define:

- `G`: verified issued allowance, including actual prorated additions in that cycle.
- `p`: configured fraction between 0 and 1.
- `B`: reconciled remaining allowance after known consumption, including settled debits not yet reflected by provider observations, but before outstanding reservations.
- `R`: sum of remaining buffered task reservations against that pool, including retained uncertain exposure (section 7.4).
- `U`: additional uncertainty allowance for stale/lagging observations or unobserved concurrent consumption.

```text
protected_balance = (1 - p) * G
H_api = max(0, B - protected_balance - R - U)
```

Use integer monetary micro-units and conservative rounding. Apply the percentage to the issued allowance, not repeatedly to the remaining balance. External organization usage also reduces `B`; the protected fraction cannot be recreated by switching API keys or processes.

Example: a $100 grant, 98% policy, $30 remaining, $5 outstanding and zero additional uncertainty gives $23 available for new work. At 100%, the protected fraction is zero, but request bounds, reservations and uncertainty checks still apply.

In `no-limit` mode the protected percentage is zero and `B` spans the verified eligible existing credit lots. Keep each lot's expiry and the provider's actual debit order. Never count the same monthly grant again inside a total-credit observation.

A billing-date change does not mint funds or erase outstanding exposure. Admit new credits only when issuance is observed. Expired lots stop funding new requests. No request may rely on an anticipated renewal; execution crossing an expiry needs admission against then-valid funding.

### 6.3 Grant coverage and strict funding

Discover amount, origin, expiry, organization/project identity and eligible transport from supported evidence. Plan eligibility or a working inference key alone does not establish a grant. Unknown grant provenance, balance, prices or applicable billing scope leaves that API route unavailable for strict automatic admission.

Both vendors can continue spending purchased credits after subscription-linked credits are depleted, and some accounts support automatic purchases or invoicing. A 2% reserve and local ledger cannot alone prevent provider-side spill from concurrent external consumers. Strict grant-only eligibility therefore requires an enforceable funding boundary or isolated funding configuration qualified in P0. If it cannot be established, report the unsupported guarantee and keep that route out of strict automatic admission.

Subscription and API routes must have independently bound credentials. Do not switch a shared active credential to dispatch concurrent jobs. If the host cannot supply an isolated/request-bound route, that combination is unsupported until the adapter establishes it. Endpoint hostnames alone do not establish who pays.

### 6.4 API Usage enablement and secure keys

**Organizations are the user's choice.** The user defines the organizations and API keys they want to use, and is never required to define one. A built-in **Personal** workspace is available to enable for API funding on the user's own account; named organizations (for example a team workspace) may be added, each with its own opaque secure-key binding. An organization is enabled explicitly. Routing, onboarding and updates never create, enable or fill in an organization or key. With none enabled there are simply no API-funded routes and subscription routes work unchanged. Organization identity is independent of its key: an enabled organization whose key is not yet ready is unavailable with a reason, not an error. Disabling an organization removes its routes and expiry pressure but keeps its in-flight claims and holds.

API Usage starts excluded from routing until the user has explicitly enabled it for the provider/organization and workflow scope and connected an API key through secure setup. Connection and activation can be one user action; merely discovering a stored key or a subscription login does not enable API Usage.

```text
api_eligible = api_usage_enabled
               AND credential_binding_ready
               AND task_and_endpoint_permitted
               AND funding_and_capacity_verified
```

Filter ineligible direct API Usage routes before building the ranking shortlist or computing expiry pressure. If none remains, no direct API Usage candidate or grant-expiry signal participates. Separately enabled OpenRouter candidates retain their own authorization/funding gates; disabling one integration does not enable or disable the other. If no API-side candidate of any permitted kind remains, `API_share = 0` when subscription execution is feasible; otherwise the task stays pending. Neither Smart Routing ON, an expiring grant, nor `no-limit` can enable API Usage implicitly. Smart Routing OFF can use an explicit API worker choice only while API Usage itself remains enabled and eligible.

Use the OS credential store (macOS Keychain on the observed host) or a qualified host secret store. Persist only opaque references and provider/organization/project binding metadata. The trusted transport resolves the key privately; prompts, model context, command arguments, source files, logs and the routing ledger never contain it. A normal API key does not by itself prove grant access or its remaining balance.

Use cached trusted readiness metadata for selection, and recheck enablement/revision and the exact credential binding before every API Usage send or reservation increase. Missing, locked, revoked or unresolvable credentials exclude the affected route. Disabling API Usage blocks new sends on those routes and invalidates unsent authority; already-transmitted requests retain their accounting until settled. This gate needs no model call or repeated authentication probe on the spawn path.

## 7. Deterministic allocation

### 7.1 Feasibility before preference

Reject a candidate that lacks required capabilities, compatible benchmark evidence, review capacity, valid funding, fresh enough observations or an enforceable credential binding. For every consumed pool/window require the bounded reservation to fit current headroom, including outstanding and uncertain claims.

Use expected full-delivery consumption for ranking and bounded execution envelopes for admission. A mean cost estimate is not a money cap. Review on a different account has its own claim; a required review without capacity remains pending. Repair and escalation requests consume the original task's remaining allowances and require admission before expanding an envelope.

### 7.2 Subscription versus API preference

For automatic worker selection, choose the funding class first, then a route within that class. Respect explicit user constraints and section 7.1 feasibility checks before computing preference. A promoted model ranker's own free-to-subscription selection remains the separate rule in section 5.3.

The API side can include separately authorized OpenRouter work, but only direct eligible subscription-linked grants contribute `E`. An explicit endpoint/provider constraint narrows candidates before scoring; it never acquires funding from a different service. An expiry signal cannot be used to select a gateway that will not consume the corresponding grant. The router-role free-first policy does not automatically make free endpoints preferred execution workers.

For each suitable subscription route, take the **maximum utilization projected at reset** across its applicable windows, including five-hour and weekly limits. For window `w`, with all terms as fractions of the window:

```text
pace_w      = used_w / elapsed_w        (only when elapsed_w >= min_elapsed_fraction * length_w)
projected_w = used_w + reserved_w + reserve_new_w + pace_w * time_to_reset_w
```

`used_w` is reconciled consumption, `reserved_w` the outstanding buffered reservations and `reserve_new_w` the new assignment's buffered reservation, without double-counting reflected usage. Before `min_elapsed_fraction` (default 0.10), and for rolling windows without qualified reset semantics, omit the pace term and record `pace_unavailable`. Let `u` be the lowest such maximum among suitable, feasible subscription routes: one hot account does not force API usage while another suitable subscription is healthy.

Let `t` be time until the earliest verified expiry of a spendable subscription-linked API grant that can fund this assignment. If there is no eligible expiring benefit grant, set `E = 0`; purchased credits alone do not create benefit-expiry pressure. Use the grant's actual expiry, not an assumed monthly date. Let `r` be that grant's remaining eligible amount and `expected_spend` its settled API spend over the last `lookback_hours` (default 72), scaled to `t`; `stranded = max(0, r - expected_spend)`.

```text
clamp(x) = min(1, max(0, x))

S = clamp((u - L) / (H - L))
E = clamp(1 - t / T) * clamp(stranded / r)

API_share = 1 - (1 - S) * (1 - E)
Subscription_share = 1 - API_share
```

Configurable starting values: `L = 0.65`, `H = 0.85`, `T = 72 hours`, with `0 <= L < H <= 1` and `T > 0`. Utilization is a fraction; `t` and `T` use the same time unit. These are preference thresholds, not provider limits. Below 65% with distant grant expiry, prefer subscription; from 65–85%, increase API share; at 85% or above, prefer API when feasible. Expiry pressure can favor API even with healthy subscription capacity. With no recent spend, `stranded / r = 1` and `E` reduces to the time term; when expected spend already consumes the grant before expiry, `E = 0`. Tiny balances still face the feasibility check, so they cannot attract tasks they cannot fund.

Hard overrides: no feasible API route sets API share to zero; no feasible subscription route sets it to one; neither feasible leaves the assignment pending. Expired, insufficient or disabled grants are excluded before scoring. Unknown/stale utilization is not zero utilization: apply conservative admission/refresh rules instead of inventing a score. The configured credit ceiling (98% by default) always applies; 100% API share is a workload preference, not permission to spend beyond it.

Use deterministic deficit scheduling between feasible, positive-share classes, derived from admission records rather than separate counters. Each admission records its bucket `(role, complexity_band)`, the class shares in force and the chosen class. For an incoming assignment in bucket `b`, compute for each class `c`: `deficit_c = Σ(recorded share_c in b since epoch) + current share_c − count(admitted to c in b since epoch)`, and choose the largest. Ties favor API. The epoch starts at the latest policy revision or consumed-window reset, and each assignment counts as one unit. Duplicate or rejected spawns create no admission record, so they consume no allocation. Apply the existing within-class allocator below to select the provider/account strategy. Expiry-biased API selection should prefer the eligible grant supplying the expiry signal, subject to actual provider debit order.

Recompute for new spawns from cached observations. The preference adds only arithmetic over cached observations and admission records—no extra model call, network lookup, persisted counter or demand forecast beyond the recent-spend rate above. Existing workers keep their admitted routes.

### 7.3 Within-class route selection

For each feasible strategy `s`, let `c[s,p,w]` be expected resource use per comparable accepted delivery in pool `p`, window `w`, including attributable review and expected repair. Let `H[p,w]` be reconciled usable headroom after policy caps, reservations and uncertainty. Omit constraints the strategy does not consume.

```text
fundable_work[s] = min over consumed pools/windows (H[p,w] / c[s,p,w])
binding[s] = the constraint attaining that minimum
pressure[s] = fundable_work[s] / max(time_to_reset_or_expiry(binding[s]), minimum_horizon)
```

Ties for the binding constraint use earliest verified expiry/reset, then a stable pool/window ID. Every overlapping window remains a conjunctive constraint; do not add five-hour and weekly percentages. Rolling/refill windows require provider-specific availability semantics, not an invented hard-reset timestamp.

Within the selected funding class, use the best-ranked currently feasible strategy per execution funding pool when computing pressure. Multiple keys, model aliases or model variants sharing a pool must not multiply its weight. Other consumed pools, including reviewers funded by another class, remain shared feasibility constraints and are reserved atomically.

Only positive resource-demand constraints enter the `H / c` calculation. A zero-priced route still has capability, request/concurrency and authorization checks. If no finite comparable pressure exists, choose by applicable evidence ranking and stable route ID; never divide by zero or treat free pricing as infinite capacity.

Admit the feasible strategy with the highest `pressure`, computed from headroom after all reservations. Stable ties use earliest binding expiry/reset, then pool ID. Because each admission reduces its pool's headroom, parallel admissions spread across pools without persisted deficit state. Record the pressure snapshot with the admission so decisions replay exactly.

This within-class rule equalizes remaining comparable capacity across pools with equal horizons, rather than allocating proportionally, and prefers usable capacity nearing reset/expiry. It does not override the funding-class shares in section 7.2 or any hard constraint. No synthetic work is created merely to consume expiring credits.

If reset semantics or calibrated consumption are unavailable, do not fabricate weights or compare raw percentages across providers. Use the evidence-ranked strategy for which conservative admission can actually be established, with `capacity_uncalibrated`; otherwise return a pending-capacity reason. Subscription calibration must account for coarse observations, concurrent/outside usage and uncertainty; a raw before/after account delta is not automatically attributable to one task.

### 7.4 Buffered reservations for parallel tasks

Remove API Usage's global `slot` / `request_in_flight` admission rule and the slot-owned dispatch, completion and recovery paths. Each task/attempt has its own claim; only short ledger transactions serialize, not worker execution. Provider rate limits, host resources, ownership conflicts and explicit user concurrency limits still apply. Admission also requires open attempts to stay within a global cap and a per-provider-account cap. Defaults: the onboarding-recorded concurrency limit if present; otherwise 4 total and 2 per provider account. Caps are user settings, raised only after canary evidence.

For each task `i` and funding pool/window `p`, reserve:

```text
reserve[i,p] = max(round_up(estimated_remaining_cost[i,p] * (1 + margin)),
                   next_requests_bound[i,p])
admit i only if reserve[i,p] <= current_headroom[p] for every consumed pool/window
```

Use a configurable positive task margin, initially 20%. The estimate includes the task's planned execution and assigned review/repair provision in each pool's own units. `next_requests_bound` covers the maximum cost of requests about to be authorized. The task margin is releasable padding, distinct from the monthly protected credit fraction and pool-level telemetry uncertainty; do not count the same uncertainty twice.

Headroom in section 6.2 already subtracts all other outstanding holds. Read it and reserve the entire vector atomically before dispatch; duplicate attempts never add another hold. Splitting a parent's reserved review budget into a child claim transfers ownership rather than charging it twice. Update projected subscription utilization and API affordability from these same claims.

Example with $30 available before new claims, 20% padding and request bounds no larger than the padded estimates: tasks estimated at $5, $10 and $8 reserve $6, $12 and $9.60. All three can run concurrently, leaving $2.40 for another task. A fourth task estimated at $3 needs $3.60 and waits. If the first task finishes at an actual $4, releasing its unused $2 raises available room to $4.40 and permits the fourth task.

As observed spending is charged, reduce the task's remaining reservation and debit the balance together, without creating new free capacity. Release unused padding only when safe completion/non-execution is established; timeouts retain uncertain exposure. Before a task needs more than its remaining envelope, atomically admit a top-up or pause it at the next request boundary. One unresolved task holds its own exposure; it does not automatically serialize unrelated tasks that still fit.

## 8. Admission, enforcement and recovery

- **One ledger authority.** All managed funding paths use one account-wide routing ledger. SQLite transactions enforce exclusive dispatch ownership across concurrent callers and plugin instances; being inside one service does not itself remove races. Keep inference/network waits outside write transactions.
- **Atomic authorization.** Recheck policy revision, API Usage enablement/key binding where applicable, route identity and current claims while committing an attempt ID, buffered reservations and a single-use dispatch authorization. Repeated callers receive the decision, not another authority to launch. Distinct tasks can hold concurrent reservations.
- **Dispatch identity.** Bind task, parent task, stage, attempt, session/message IDs, model/effort, credential, funding pool and resource limits. Test host deduplication before relying on caller-supplied IDs. Without proof of non-launch or verified host deduplication, a lost acknowledgment stays `unknown` and is not retried blindly.
- **Common spawn path.** Workstreams and ordinary delegation use the same assignment preparation and managed worker-spawn interface, including nested review/repair paths. Recheck canonical dependencies, ownership and host-resource authority alongside the funding grant. A skill explains when to delegate; the host mechanism enforces the resulting route.
- **Pre-dispatch guard.** Managed worker requests must match their admitted route and remaining envelope. Cover auxiliary title/compaction calls, provider retries and native harness subprocesses. Check capacity locally before each provider request; extending the envelope needs fresh admission, not necessarily another model-ranking call.
- **Enforcement boundary.** Qualify native sub-agent tools and alternate launch paths, including shell/CLI, direct APIs and model-switch tools exposed to workers. Profiles that can use unmediated credentials or launches cannot claim mandatory routing. A hook that only detects a request after transmission is not fail-closed enforcement. If prevention cannot be proved, enforced mode remains blocked for that path; interruption/quarantine is recovery, not an equivalent guarantee. OpenCode 2.2.6 specifics (the hooks that can block before transmission, fail-open plugin loading, advisory-only worker profiles that keep arbitrary shell access) are recorded in the [capability matrix](../../smart-routing-capability-matrix.md#opencode-enforcement-p01-p05p08).
- **Coordinator boundary.** The user's root conversation retains its selected model/effort. Its delegated launches are mediated; observing its costs does not authorize model switching.
- **Reconciliation.** Anchor observations to pool/window epochs and source watermarks where available. Retain settled-but-unreflected debits separately from active reservations; remove them only with evidence they are reflected. A newer timestamp alone, or a reset timestamp moving forward, is not proof of reflection or worker termination.
- **Bounded uncertainty.** Cap unresolved admissions per pool and stop new affected admissions at the cap. Preserve minimum ownership/financial safety state until reconciliation; expire diagnostic detail separately. Lease expiry never proves non-execution or releases an uncertain charge.

## 9. Lightweight execution path

| Operation | Frequency |
|---|---|
| Guidance, benchmark and catalog refresh | Maintenance, relevant change or explicit refresh; outside per-spawn locks. |
| Strategy-table compilation | Maintenance refresh or relevant catalog/guidance change; outside per-spawn locks. |
| Profile preparation | Validate coordinator-supplied fields and job metadata; conservative floors for gaps. |
| Jev enrichment (optional) | Only where configured, for unresolved fields on a new or materially changed assignment; bounded ready-wave batching where supported. |
| Strategy ranking | Table lookup per assignment. A promoted model ranker runs only on a profile/evidence cache miss, coalescing identical requests. |
| Subscription/API preference | Every automatic worker spawn; cached utilization/expiry arithmetic over observations and admission records only. |
| API Usage eligibility | Before API Usage candidate selection and each send; scoped enablement and trusted credential binding, without an inference or authentication probe. |
| Funding checks, setting revision and reservation | Every managed spawn, including when Smart Routing is OFF. |
| Envelope check | Every managed provider request, using local admitted state. |
| Observation refresh/reconciliation | Execution events and stale-data refresh as needed, with one refresh owner per pool. |

Admission is a bounded local lookup, arithmetic over a small candidate set and one transaction. The default path needs no profiling or ranking inference; only a promoted model ranker or configured Jev adds a remote call. Measure each operation and cache hit separately; do not claim all spawns are millisecond operations while remote inference is required.

Cache suitability, never dispatch authority. Finite time/input/output limits apply to control operations. An unavailable model ranker falls back to the deterministic ranker, never to a paid API-credit router. No model invocation is needed to disable routing, inspect its state or enforce existing limits.

## 10. Merge and ownership plan

### 10.1 Source baseline

The inspected feature baseline is vibe-workflow `main` at `d73252a77d1e784f8a7d6f637bc661467485178d`. The authoring checkout is on `codex/project-repository-adjustments` and lacks some files already present on `main`. Existing local changes and the OpenCode adaptation must be reconciled by the implementation owner before changing shared runtime files. File names below describe inspected components, not a claim that all are present in this checkout.

### 10.2 Implementation evaluation and recommended action

The assessment below is grounded in inspected `main` source/contracts, not a new end-to-end validation. Current strengths are implemented behavior or explicit contracts; proposed efficiency and quality gains remain hypotheses until replay and live qualification pass. Workstreams decomposition is coordinator/instruction-led, with runtime validation of the resulting plan; there is no demonstrated standalone Jev profiler already performing it.

| Area and existing evidence | Better to retain or gain | Worse, missing or at risk | Recommended action |
|---|---|---|---|
| Workstreams/delegation: `clients/workstreams.md`, `clients/delegation-requests.md`; `runtime/delegation.py:_delegation_plan`, `validate_dependencies` | Stable jobs, dependency-cycle checks, bounded briefs, claims, child reuse and acceptance references already exist. Ordinary Workstreams preference needs no advanced runtime enrollment. | A second decomposition/queue or mandatory advanced enrollment would add unnecessary overhead. Managed plan validation currently admits specific Codex routes, not every host. | **Keep orchestration.** Add shared preparation to existing job readiness/dispatch, preserve the lightweight ordinary path and qualify new host bindings explicitly. |
| Task description: `selection_constraints` and worker briefs; `selection.profile_ids` | Existing complexity/risk facts, tools, scope and review/check metadata can feed a profile without another model call. | Fields are not a complete normalized reusable task profile. `profile_ids` reference guidance recommendations, not task classifications. | **Extend records.** Add semantic digests/profile references and `ensure_profile`; preserve guidance IDs separately. The coordinator fills profile fields; floors cover gaps; Jev optionally enriches them. |
| Native selection: `runtime/delegation.py` selected-settings/catalog and guidance validation | Current code already checks actual model availability, supported efforts, guidance applicability and coordinator preservation. | Selection is supplied by callers and is separate from API/OpenRouter choice; duplicating those checks in a new hard-coded tier table would regress it. | **Keep validators, replace choice.** Supply settings through the central evidence-backed ranker and retain observed-versus-requested checks. |
| Shared external choice: `runtime/external_delegation.py:select` | Explicit choices, applicable local preferences, enabled scopes and source restrictions have defined precedence. | Multiple suitable routes can fall back to native without reset/credit optimization; it is another decision owner. | **Replace selector.** Convert binding user preferences/permissions into candidate constraints, then use the common ranking/allocator. Keep packet provenance checks. |
| OpenRouter: `scripts/openrouter_proposals.py:triage` and discovery procedure | Bounded model/endpoint/benchmark caches, refresh-owner tokens, supported-effort validation and failed-refresh handling are already specified/implemented. | Popularity is not fitness; proposal success does not prove worker tool competence. Re-fetching metadata per spawn would add latency. | **Reuse evidence service patterns.** Share normalized snapshots with the evidence compiler; remove separate model-choice instructions while retaining transport/endpoint qualification. |
| API Usage: `runtime/usage_budget.py` and `runtime/usage_routing.py` | Transactions, maximum-request reservations, policy revisions and retained uncertain charges provide a useful base. | Both modules contain `slot`/`request_in_flight` paths and 20% balance-floor logic. This serializes independent work and differs from the new 98% allowance policy. | **Replace financial policy and slot lifecycle.** One ledger, per-task padded claims, atomic top-ups and recovery over all pending attempts. Rewrite tests expecting an affordable second task to fail. |
| Credentials: `runtime/usage_credentials.py`, `usage_setup.py`, `openai_api.py` and host bridges | Opaque references, scoped bindings, Keychain/private-pipe secret handling and explicit API activation are implemented. | Existing coverage is OpenAI/macOS-specific; default-enabling API spend or extracting keys into model context would be a regression. | **Reuse secure handling, extend adapters.** Smart Routing defaults ON; API Usage stays separately opt-in with qualified keys, grants and routes. |
| Host resources and evidence: `runtime/resource_admission.py`, `resource_accounting.py`, `evidence.py`, review/delivery records | CPU/memory/resource ownership and independent acceptance are existing workflow responsibilities. | They do not measure provider quota automatically. A second completion ledger risks duplicate counts or false acceptance. | **Keep ownership.** Require both resource and funding admission for the same job revision; attach observed costs and outcome references rather than recreating task status. |
| OpenCode pilot: `scripts/openrouter_workers.py` and its procedure | Existing bounded launches, progress and recovery observations can inform the new adapter. | The inspected V1 worker under a native supervisor does not establish V2 host enforcement, credential isolation or nested-spawn mediation. | **Qualify and replace the host path.** Reuse proven transport facts, build the V2 binding, and report unsupported paths rather than silently retaining old selectors. |
| New profiler/ranker and host integration | Shared decisions, semantic reuse and one allocator can reduce duplicate selection work and enable cross-pool balancing. | Per-assignment inference, availability dependencies and a second-language ledger would be slower and riskier than existing caller-supplied selection. Savings are unmeasured. | **Keep the core in Python and inference off the hot path.** Coordinator-supplied profiles, a compiled strategy table, one Python ledger writer, a policy-free host shim, and measured gains before promoting model ranking. |

**Recommendation:** retain Workstreams, task/evidence ownership, secure credentials and useful discovery/validation code; replace the competing selection engines and serial API budget policy. Vibe Router is the default decision service inside these existing paths, not a new orchestration product. No backward-compatible selector is needed when Smart Routing is OFF: manual/configured choices use the new common admission layer.

Existing proposal-only restrictions are not a permanent ceiling on this design. Implementation/review workers become eligible when the actual route, tools, ownership and independent checks qualify. Preserve real funding and permission boundaries, not legacy restrictions merely for compatibility.

### 10.3 Packaging

Keep the Vibe Router core in Python inside vibe-workflow, consistent with the workflow's runtime contract (Python standard library and SQLite, client adapters under `runtime/adapters/`). Every client, including Codex and Claude Code coordinators, reaches it through one local versioned JSON command. The financial ledger remains `runtime/usage_budget.py`'s SQLite store, extended rather than reimplemented. Proposed placement:

```text
runtime/usage_budget.py              # the one ledger (extended)
runtime/routing_settings.py          # Smart Routing, caps, worker_choices, thresholds
runtime/routing.py                   # contracts, ensure_profile, prepare_assignment
runtime/routing_evidence.py          # evidence compiler and strategy table
runtime/routing_allocator.py         # pure allocation functions
scripts/routing.py                   # the single JSON command for all clients
runtime/adapters/opencode.py         # probe and event normalization
runtime/adapters/opencode_plugin.ts  # policy-free pre-transmission shim
clients/opencode.md                  # host qualification and loading contract
policies/03-agent-delegation.md
```

One logical ledger authority owns financial decisions across worktrees. The OpenCode shim forwards spawn, model-switch and provider-request hooks to the command and blocks unless it returns an admission; it contains no selection or funding policy and fails closed when the command is unavailable. If P0.1 shows per-request process spawning is too slow, the shim may keep one child process alive for the OpenCode session's lifetime; no separate daemon or UI is introduced.

### 10.4 Breaking cutover

1. Select the reconciled source baseline and map every current admission/selection writer.
2. Replay recorded arrivals against isolated ledgers as ordinary tests, then canary enforced routing on one explicitly chosen project within recorded bounds: a project allowlist, a spend cap per funding pool, a maximum duration, the concurrency caps, an abort condition and the Smart Routing OFF kill switch. Proceed only when the cutover criteria, recorded before the canary starts, pass (section 13); otherwise the current selectors remain.
3. Stop legacy new admissions, finish or authoritatively reconcile outstanding work, and retain historical costs/minimal unresolved ownership evidence.
4. Install the built-in routing service and new schema with Smart Routing ON by default, honoring an explicit new Smart Routing OFF choice and the existing Workstreams preference. Configure readiness separately. API Usage remains excluded until explicit enablement/key setup; do not translate legacy Manual/Smart Balance state into new API spending authority implicitly.
5. Remove superseded runtime selectors, aliases, schemas, wrappers and obsolete tests when the new path qualifies. No compatibility layer or simultaneous old/new writer is retained.

Unresolved old executions block affected cutover until their ownership can be established. No-backward-compatibility does not mean deleting charges or making uncertain capacity available again. The user can later delete the standalone planning checkout without affecting the feature.

## 11. Accounting, retention and later learning

The primary metric is total attributable resource use per independently accepted delivery over a defined mature cohort. Sum each root's coordinator, profiling, routing, implementation, review, repair, checks, escalation and auxiliary work once, including failed/abandoned attempts. One acceptance costing 100 units plus one rejection costing 90 gives 190 units per accepted delivery. Unresolved tasks retain cost-to-date separately; zero acceptances has no finite ratio.

Keep quota units per pool/window, tokens where observed, credit consumption, actual incremental monetary spend, latency and review/repair counts separate. A provider's token-price estimate is not proof of out-of-pocket spend on a subscription. Unattributed external consumption affects capacity but is not fabricated as a task's measured cost. Separate mandatory review from reviewer rescue/rewrite work so weak executors do not receive misleading quality credit.

Retention defaults: quota snapshots 24 hours; decision/event diagnostic metadata 14 days; task features 30 days; calibration/evaluation aggregates a rolling 60 days. Authority state for active grants, cycle caps, live/uncertain claims and needed idempotency records survives until safe reconciliation, with explicit caps. Cleanup never resets a still-active monthly cap. Do not store prompts, source, full transcripts or credentials in the routing store.

Filter expired evidence on every read and clean up at service start and while active. This is deletion while running/on next start, not a promise to delete files while the machine is powered off. Expired evidence must never influence a later decision.

Dynamic vendor/benchmark-based selection and bounded resource calibration ship in v1. Automatic learned routing weights remain deferred until sufficient independently verified evidence exists, with uncertainty, chronological replay, promotion gates and rollback that preserves claims.

## 12. Evidence and remaining P0 gates

Earlier local probes reported OpenCode 2.2.6, Codex 0.167.0 and Claude Code 2.1.311. These are versioned observations, not minimum-version promises or proof that the full router exists.

| Area | Evidence available | Remaining qualification |
|---|---|---|
| Billing/terms (P0.0) | The user's OpenCode session runs Claude through its Claude Agent provider (the Claude Agent SDK) on the Claude subscription (observed 2026-11-08). Anthropic announced in April 2026 that subscriptions would no longer cover third-party tools, later paused those changes (support page update of 15 June 2026), and its Agent SDK overview still says third-party products may not offer claude.ai login or rate limits unless previously approved. | Classify every route (section 6.1) from current terms and observed billing. For each provider, confirm which harness/credential combinations bill as included subscription usage (for example Codex, Claude Code, OpenCode's Claude Agent provider and Sign in with ChatGPT) and that API Usage key routes bill the API allowance. |
| Model/effort and lineage | Installed schema exposes session model/variant, parent IDs and metadata. | Verify requested versus observed settings, lifecycle attribution and native harness boundaries. |
| OpenAI subscription quota | Read-only Codex app-server `account/rateLimits/read` returned usage percentages, windows and reset times. | Establish account identity equivalence with the OpenCode route; simultaneous exhaustion or correlated deltas alone are not identity proof. Qualify freshness/units and successful OpenCode telemetry. |
| Claude subscription quota | One native probe emitted `rate_limit_event`; one OpenCode assistant message exposed `native.rateLimit`. | Applies to every Claude route that P0.0 classifies `included_subscription`, as for OpenAI. Establish account identity equivalence with the selected route, then confirm utilization scale, completeness, freshness and coverage across versions/models; do not assume every response has it. |
| API grants | Official vendor documentation establishes benefits, eligibility, expiry and funding spill behavior. | Explicit scoped API Usage enablement, secure key resolution, account-bound grant observations, debit ordering, current prices and strict funding boundaries for both providers. |
| Optional profiler/model ranker | OpenCode documents the Jev decision interface and text-generation models; free candidates exist but are time-limited, and some retain or train on inputs. | Not a v1 dispatch gate. Before promotion: data-handling eligibility, role-specific benchmarks, cold-path latency, subscription fallback and control-operation accounting. |
| Dispatch/enforcement | OpenCode documents tool transforms, session hooks and caller-supplied IDs. | Prove blocking before transmission, credential isolation, duplicate suppression, native nested-spawn coverage, auxiliary calls and retry handling. |
| Persistence/recovery | SQLite transaction patterns exist in workflow. | Qualify the plugin binding, shared ledger ownership, event resynchronization, interruptions and unresolved-claim limits. |
| Workstreams integration | Existing job IDs, dependency/claim checks, briefs and guidance bindings are implemented in the inspected native path. | Shared profile normalization/reuse, semantic invalidation, ready-wave scheduling and fresh multi-gate admission on the actual target host. |

Produce sanitized feasibility notes, a capability matrix and reproducible fixtures in vibe-workflow. Earlier scratch probe output is not a fixture until prompts, credentials and account identifiers are removed. Live event streams require resynchronization after a gap; do not assume an event subscription replays missed records.

P0 exit is evidence for each enabled route or an explicit unsupported result. Advisory operation is not a substitute for mandatory-routing proof. In particular, an after-the-fact interrupt does not close the dispatch or funding gate.

## 13. Validation and acceptance cases

Before implementation, set role-specific benchmark thresholds, quality non-inferiority margin, efficiency criteria, latency targets, evidence freshness and required sample strength. Derive statistical evidence requirements from those criteria rather than treating a small fixed task count as proof.

Required cases:

1. Profile service, rankers and allocator have distinct typed outputs; no model can launch, change funding or weaken review.
2. Without a promoted model ranker, or when it is unavailable, the deterministic ranker orders strategies with zero inference. A promoted ranker's free-then-subscription selection never falls back to an API-credit router, and its unavailability never blocks an admissible assignment.
3. Catalog/effort additions need no code change; missing evidence, unsupported efforts and alias/version changes cannot bypass qualification.
4. Coordinator settings remain fixed with routing ON, OFF, under fallback and during nested delegation. Every managed child/reviewer/repair uses the admission path.
5. Disable during profiling, ranking and pre-dispatch invalidates pending authority; disable after dispatch preserves in-flight ownership and spend. OFF uses configured worker choices without extra routing-model calls.
6. A $100 grant at 98% with $30 remaining and $5 reserved admits at most $23 before additional uncertainty. Test 0%, 100%, `no-limit`, purchased-credit exclusion, external usage, duplicate keys, prorated grants, annual expiry and missing renewal.
7. Within one selected funding class, with equal costs/horizons and comparable capacities, 20 versus 75 remaining comparable tasks send the first 55 admissions to the 75-pool (20 left in each pool); the pools alternate once their remaining capacity is equal. With capacities 1,000 versus 100, the same percentages mean 200 versus 75 comparable tasks, not the reverse.
8. Within a class, twenty comparable tasks expiring in one hour outrank 75 expiring in 24 hours at the initial decision and for 17 admissions. An exhausted short window vetoes the preference.
9. With default preference settings and distant API expiry, projected subscription utilization of 40%, 75%, 80% and 90% gives API shares of 0%, 50%, 75% and 100%. With 24 hours until an eligible grant expires, 40% and 75% utilization give approximately 67% and 83%. Test the maximum-window rule, a healthier alternative subscription, unavailable classes, expired grants, the separate 98% cap, positive-share-only scheduling and deficits derived from admission records. Expected spend that consumes the grant before expiry gives `E = 0`. A weekly window early in its cycle with a high pace projects above its current utilization; pace is omitted before the minimum elapsed fraction.
10. Unequal costs, shared review pools, rolling/refill windows, duplicate model aliases and uncalibrated capacity do not create fictional independent capacity.
11. Race 15 callers for one task and interrupt around the host call: at most one dispatch authority/worker, with visible uncertainty if launch cannot be established.
12. Delayed/out-of-order observations across resets never resurrect consumed capacity or double-debit it. Lease expiry and an elapsed billing cycle do not erase unresolved exposure.
13. API spend bounds cover reasoning, output, applicable tool charges and retries. Unknown prices or provider spill cannot be labeled strict grant-only funding.
14. Each task tree settles once; duplicate receipts, failed attempts, reviewer rescue and idle-period cleanup preserve correct costs and active caps.
15. Breaking cutover leaves one selector and one financial ledger authority, with old outstanding work safely settled rather than silently dropped.
16. Distinct tasks run concurrently when their padded claims fit. Reproduce the $30 / three-task example, race distinct tasks against the last available room, and test partial spend, safe release, top-ups, parent/child transfers and multiple unresolved attempts. No global busy slot rejects an affordable independent task.
17. Smart Routing ON with API Usage OFF, a stored-but-unactivated key, a wrong-scope binding, or a missing/locked/revoked key excludes direct API Usage candidates and their expiry pressure. Explicit enablement with a qualified secure binding admits them. Independently enabled OpenRouter routes remain governed by their own permission/funding. Disable/credential changes between selection and send block unsent work while preserving transmitted costs; secret values never enter routing output or storage.
18. A complete coordinator-supplied profile causes zero Jev calls; gaps take conservative floors, with bounded Jev enrichment only where configured. Balance/progress/retry changes reuse the profile; changed scope/role/tools invalidate affected fields. A catalog/benchmark refresh can rerank without reprofiling. Batched results remain bound to individual jobs.
19. A medium–large dependency plan uses existing job IDs: independent ready jobs run concurrently, dependent jobs wait, unchanged continuations reuse children/profiles, and a stale dependency or ownership revision blocks dispatch despite a cache hit. Financial reservations are fresh and parent/child provisions are not duplicated.
20. An absent Smart Routing setting resolves to ON; explicit OFF persists. Workstreams OFF does not become ON, API Usage stays opt-in, and missing readiness reports enabled-but-blocked rather than invoking an old selector. Both automatic and explicit-worker paths use the new admission layer.
21. Independently toggle direct API Usage, OpenRouter, Council and observability. Only enabled/permitted routes or exports occur; a direct grant never funds a gateway request. Zero-priced candidates produce finite decisions and still obey request limits.
22. Startup/status/naming/diagnostics trigger no profiler/router calls. Explicit Council invocation retains its member/group/independence constraints and anonymous critique protocol; ordinary routing never invokes Council. Review My Project remains scoped and read-only unless separately authorized.
23. Claude and OpenAI expose the same route kinds: each provider's eligible subscription and API Usage routes are both candidates, chosen by the same allocator. With API Usage OFF, each provider's subscription routes remain available. Every route carries a billing class, and a `purchased_usage` route never counts as subscription headroom for either provider.
24. At default caps, the third open attempt on one provider account waits while an attempt on another account is admitted; closing an attempt frees a place.
25. Smart Routing OFF admits the first feasible `worker_choices` entry for the role, or leaves the assignment pending with `worker_choice_unavailable`.
26. Profiling or ranking endpoints that retain, train on or forbid confidential data are never used without the user's per-endpoint opt-in. Strategy-table rows naming unavailable models or unsupported efforts are dropped at compilation.

Evaluate in two parts. Deterministic allocator replay runs identical chronological arrivals, concurrent demand, balances and resets over isolated ledgers as ordinary tests, checking funding violations, stranded quota/credits and recovery errors. Quality evaluation re-executes a pre-registered, budgeted sample of historical tasks with fixed acceptance checks; it is required for disagreements flagged by the cutover criteria and for promoting a model ranker over the deterministic one. Measure routing overhead, acceptance and total delivery cost per policy. Cutover follows replay and a bounded canary (section 10.4). Rolling back automatic selection keeps ledger ownership and limits intact.

Run `python3 -B scripts/validate.py` for package checks and, once implementation exists, focused router/adapter tests and representative instruction-loading walkthroughs. Report document/package validation, synthetic tests and observed client/provider execution separately.

## 14. Definition of done

A user works with their chosen coordinator. Workstreams prepares dependency-aware, bounded assignments in the workflow's canonical job records. Each assignment has a validated, reusable task profile supplied by the coordinator and existing metadata, with conservative floors for gaps and optional Jev enrichment. Smart Routing is built in and enabled by default on every client: a deterministic ranker supplies an evidence-backed model/effort ranking, ranker unavailability never blocks delegation, and the allocator performs fresh funded admission before each launch. Ready independent assignments run concurrently within buffered reservations and concurrency caps; all supported nested work uses the same path. Claude and OpenAI subscription and API routes follow the same model; API Usage routes participate only when explicitly enabled with a secure key binding. Independently verified outcomes settle once into root-task accounting. Users can disable automatic selection without losing in-flight ownership or weakening funding limits. No duplicate task graph, legacy selector or standalone-checkout dependency remains.

## 15. Sources and precedence

- [OpenCode V2 models and variants](https://opencode.ai/docs/models/)
- [OpenCode V2 server plugin hooks, tools and sessions](https://opencode.ai/docs/build/plugins/server/)
- [OpenCode V2 client event delivery](https://opencode.ai/docs/build/client/)
- [OpenCode Console models and Jev decision interface](https://opencode.ai/docs/console/models/)
- [OpenAI subscription-linked API credits](https://developers.openai.com/api/docs/guides/chatgpt-api-credits)
- [Anthropic subscription-linked API credits](https://support.claude.com/en/articles/17154008-monthly-api-credits-for-max-ultra-and-team-plans)
- [Anthropic announcement: Claude subscriptions and third-party tools (2026-04-03)](https://x.com/bcherny/status/2040206440556826908)

Sources were consulted during the 2026-11-08 design discussion. Refresh applicability during P0; none substitutes for the user's account evidence. OpenAI's documented annual Business grant behavior and Anthropic's API/Agent SDK versus interactive Claude Code distinction are reasons to discover expiry and transport eligibility instead of hard-coding monthly periods or blanket provider support.

Earlier independent-plan findings are carried forward as requirements for funding binding, exclusive dispatch, observation reconciliation, stage-bound review funding, bounded uncertainty, unbiased quality evidence, cohort accounting, retention and chronological replay. Their original review does not constitute approval or live validation of this revised design.

## Appendix A — Impact on every existing feature

Inventory scope: inspected `main` at the revision in section 10.1 plus the current authoring checkout. All ten distinct skill entry points across those baselines are covered below, along with cross-cutting runtime features. **Checkout** identifies features present here but absent from the inspected `main` skill inventory; reconcile their source before implementation. These are future integration requirements, not claims of completed implementation or new live qualification.

| ID / existing feature | Smart Routing integration and affected files | Boundary and acceptance check |
|---|---|---|
| F01 Prepare my Workspace | `skills/prepare-my-workspace/`, client setup contracts: install/verify the built-in routing package and compatible host binding through normal environment preparation; report configured state and readiness separately. | No separate router activation, account switch or credential purchase. Verify/status performs no routing inference; preserve user coordinator settings. |
| F02 Onboard my Project | `skills/onboard-my-project/`, `runtime/onboarding.py`, `runtime/startup.py`: expose default ON and explicit OFF; read the existing Workstreams project preference; bind minimal routing metadata to the project/host. | Ordinary development does not require advanced managed enrollment. API Usage, OpenRouter and observability are not enabled by onboarding. Explicit OFF survives reopening/re-onboarding. |
| F03 Workstreams | `skills/workstreams/`, `runtime/workstreams.py`, `runtime/delegation.py`, `clients/workstreams*.md`: retain decomposition, dependencies, readiness and child reuse; call shared assignment preparation for each ready job. | One task graph. A valid profile takes zero Jev calls; progress/balance changes do not reprofile. Distinct ready jobs run in parallel; dependent/conflicting jobs wait. |
| F04 Manage API Usage | `skills/manage-usage-routing/`, `runtime/usage_*.py`, trusted API host bridge: manage secure connection, explicit API enablement and allowance policy; replace independent selection and global-slot execution with common buffered claims. | Default OFF until enabled with a secure key. Preserve 98%, 0–100%, `no-limit`, expiry and spill checks. An affordable second task succeeds; stale keys/disable block unsent requests. |
| F05 Manage OpenRouter Integration | `skills/manage-openrouter/`, `runtime/openrouter_settings.py`, `scripts/openrouter.py`: manage its own enabled scope, endpoints and funding; expose eligible candidates to the shared ranker. | Smart Routing ON and direct API keys do not activate OpenRouter. Preserve user-approved local/provider preferences as constraints and independent OpenRouter spending authority. |
| F06 OpenRouter proposals | `skills/openrouter-proposals/`, `scripts/openrouter_proposals.py`: reuse discovery/history and bounded packets; use common profile/ranking/admission for an approved proposal, including tool-free calls that are not native child sessions. | No new file/tool permissions. Keep endpoint pinning, source scope, independent verification and uncertain-cost reconciliation. A proposal receipt cannot become acceptance. |
| F07 OpenRouter workers | `skills/openrouter-workers/`, `scripts/openrouter_workers.py`, OpenCode adapter: route each worker through the common service and record actual requested/observed model/effort and parent ownership. | Current read-only V1 qualification does not authorize editing or V2 enforcement. Host retries are charged; no competing outer retry or ownership takeover after an ambiguous stop. |
| F08 LLM Council (**Checkout**) | `skills/llm-council/`, `runtime/council*.py`, `clients/codex-council.md`: only after explicit invocation, submit answer/critique/chair assignments through shared selection and funding, with Council's group/provider/independence requirements as hard constraints. | Preserve independent answers, anonymous critique, fresh synthesis and approved transmission scope. Do not collapse required members onto one producer, silently replace the user's group, or invoke Council for ordinary routing. The user's coordinator stays fixed; a delegated chair has its own constrained role. |
| F09 Review my Project (**Checkout**) | `skills/review-my-project/` and its local procedure: reuse selected repositories/areas and review briefs; route delegated reviewers within that scope. | Requested scan scope remains authoritative. Routing does not enroll repositories, run scheduled scans, authorize fixes or turn a review into implementation. |
| F10 Manage Observability | `skills/manage-observability/`, `runtime/observability*.py`, `runtime/langfuse.py`: optionally export sanitized selection reasons, cache hits, control-call counts, reservation/settlement and quality metrics. | Observability stays opt-in with its own destination. Never export keys, prompts or source; distinguish estimated consumption from measured cost and actual incremental spend. Disabled export does not disable local accounting. |
| F11 Planning, native delegation and continuation | `policies/02-task-planning.md`, `policies/03-agent-delegation.md`, `runtime/delegation.py`, `clients/delegation*.md`, `runtime/external_delegation.py`: replace caller-owned automatic selectors with the shared service; retain task identity, finite attempts, actual catalog validation and explicit constraints. | Unchanged continuation reuses profile/child but obtains required fresh admission. Changed scope/role invalidates relevant selection artifacts. Permission/source checks are not removed with the old selector. |
| F12 Resource admission and capacity recovery | `runtime/resource_admission.py`, `resource_execution.py`, `parent_recovery.py`, `clients/workstreams-resources.md`, `workstreams-recovery.md`: combine resource readiness with financial admission for the same assignment; reassess funding on an authorized recovery. | Resource/recovery preferences remain separate. Provider headroom does not prove CPU/memory availability or stopped ownership. A quota reset is not authority to resume automatically or restart spent attempts. |
| F13 Tests, security/code review and evidence | `policies/04-security-review.md` through `06-code-review.md`, `runtime/evidence.py`, `review_storage.py`, `clients/evidence*.md`: use existing checks, candidate bindings and independent acceptance; route any required reviewer as its own assignment. | Deterministic tests need no Jev/router inference. Preserve reviewer independence and current source/check bindings; cheaper routing cannot lower review requirements or turn worker self-report into acceptance. |
| F14 Worktrees, delivery and merge assurance | `clients/delivery.md`, `runtime/delivery.py` on main; repository-gate contracts and existing worktree ownership: attach routing records to actual task/checkouts and retain them through delivery. | Routing grants no commit, push, merge, publication or cleanup authority. The inspected repository gate's unqualified production paths stay unqualified; synthetic funding tests cannot open them. |
| F15 Thread naming, visibility and Habblaud | `clients/workstreams-naming.md`, `workstreams-titles.md`, `worker-visibility.md`, `runtime/title_sync.py` and qualified display transport: show actual worker route/effort, waiting-for-budget reason and progress using existing ownership. | Preserve explicit title/display adoption and coordinator identity. Rendering/naming makes no routing-model call; a badge or completed animation is not evidence of acceptance. |
| F16 Context/handoff and structural evidence | `policies/07-context-and-handoff.md`, Workstreams context rules and existing graph guidance: attach profile/ranking references to the bounded handoff, reuse shared read-only structural evidence when valid. | No full transcript duplication, per-child graph rebuild or unconditional reclassification. Changed task-relevant context can invalidate a profile; unrelated repository activity does not. |
| F17 Startup, maintenance and diagnostics | `clients/startup.md`, `runtime/startup.py`, `doctor.py`, `policies/09-workflow-maintenance.md`, `clients/guidance-publication.md`: report default ON, explicit OFF and typed readiness; refresh guidance/catalogs through the existing maintenance boundary. | Startup/status stays cheap and non-inferencing. Missing capability reports blocked/unsupported, not a hidden legacy dispatch. Installing a new model version alone does not prove benchmark fitness. |

### Adoption checklist

- [ ] Reconcile `main` and Checkout-only features; name the actual implementation owner for every F01–F17 row before retiring callers.
- [ ] Route every applicable worker/proposal/control-inference path through shared policy and accounting, preserving the feature-specific constraints above.
- [ ] Update instruction loading/source-to-contract coverage and `clients/instruction-map.json` when their owners change. Keep skill entry points within 150 words.
- [ ] Exercise ON/OFF/readiness combinations and each row's boundary with adapter doubles/instruction walkthroughs before qualifying the live host.

## Appendix B — Whole-document review record

Self-reviewed on 2026-11-08 using installed Superpowers `writing-plans` 7.0.2: spec coverage, checkable steps, interface consistency, review-focus cases and proportion. This is an author self-review, not an independent review or P0 execution result.

| Finding | Resolution |
|---|---|
| Existing-feature coverage omitted Council, project review, startup, visibility and delivery interactions. | Appendix A inventories all ten skill entry points plus runtime features across the two source baselines. |
| Direct API Usage gating could accidentally exclude or authorize independently configured OpenRouter work. | Sections 6–7 now distinguish funding sources and activation, and prevent transferring a vendor grant to a gateway. |
| Free pricing could leave `H / c` undefined or imply unlimited capacity. | Section 7.3 restricts the calculation to positive demands and retains nonmonetary limits with a finite fallback. |
| Default-on could cause inference during setup/status or imply approval of optional features. | Section 3 and F01/F02/F08/F10/F15/F17 separate local controls from actual inference and feature activation. |
| Existing phase handoff lacked concrete feature regression ownership and executable checkpoints. | The accompanying implementation handoff adds a writing-plans header, review focus, file/interface boundaries and phase-owned checks. |

### v1.5 amendments (2026-11-08; A14 added later the same day)

Amendments A1–A14 from the [v1.7 implementation plan](../plans/2026-11-08-smart-routing.md), accepted by the user, with A12 applied identically to every provider:

| ID | Amendment | Sections |
|---|---|---|
| A1 | Python routing core and one JSON command for all clients; policy-free OpenCode shim. | 10.3 |
| A2 | Compiled strategy table and deterministic ranking by default; model ranking promoted only on evidence. | 1, 2, 5.2–5.4, 9 |
| A3 | Ranker unavailability falls back to deterministic ranking instead of blocking. | 3, 5.3, 9, 13 case 2 |
| A4 | Coordinator-supplied profiles; conservative floors; Jev optional and user-configured. | 2, 4.1, 5.1, 13 case 18 |
| A5 | Control-plane data-handling rules for profiling/ranking endpoints. | 5.2, 5.3, 13 case 26 |
| A6 | Subscription utilization projected at reset. | 7.2, 13 case 9 |
| A7 | Expiry pressure weighted by stranded credit. | 7.2, 13 case 9 |
| A8 | Class deficits derived from admission records. | 7.2 |
| A9 | Stateless within-class selection by highest pressure. | 7.3, 13 cases 7–8 |
| A10 | Default global and per-provider-account concurrency caps. | 7.4, 13 case 24 |
| A11 | Replay and canary before cutover; two-part evaluation. | 10.4, 13 |
| A12 | Billing classification per route; Claude and OpenAI offer the same subscription and API route kinds under one model. | 1, 6.1, 12, 13 case 23 |
| A13 | OFF-mode `worker_choices` settings shape. | 3, 13 case 25 |
| A14 | No shadow or simulation mode: a route is active or it is not; the canary is bounded. | 3, 10.4, 12, 13 |

This is an author revision, not an independent review.

P0 gaps remain explicit: billing/terms classification, target-host enforcement, credential isolation, grant provenance, telemetry fidelity and identity matching. Model-ranker fitness is an S4 promotion gate rather than a v1 dispatch gate. Implementation cannot replace these proofs with document/package validation.
