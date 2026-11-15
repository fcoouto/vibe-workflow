# Smart Router: trustworthy quota optimization with provider-managed fallback

Status: design proposed for approval. On 15 November 2026 the user selected
approach 3 as the basis for this revision, not as approval of this written design,
implementation, activation or new live operations.

Base: `main` at `ce1170192a9661ff1dca9504a085c8203bc9189d`.
This is a focused amendment to the
[approved redesign](2026-11-10-smart-routing-redesign-design.md), especially its
account-based amendment and sections 5.4 and 5.6. Unchanged requirements remain
in force. The [delivery plan](../plans/2026-11-15-smart-router-delivery-streams.md)
must be revised after this design is approved; its old quota prerequisites are
not silently marked passed.

## 1. Intent and decision

Route useful work to capable models, ordered by value, using enabled accounts
with their existing settings. Use trustworthy included-quota evidence to improve
selection, but do not require complete knowledge of provider quota internals to
permit an otherwise eligible provider-managed launch.

The user rejected contacting OpenAI or sending anything upstream. The existing
request draft remains unsent. This work uses existing local evidence; no new
upstream investigation is a prerequisite.

### Approaches

| Approach | Benefit | Tradeoff and decision |
| --- | --- | --- |
| 1. Strict quota admission everywhere | Local capacity estimates precede every launch | Existing surfaces cannot establish all required quota semantics; rejected as a universal launch prerequisite. |
| 2. Provider-managed routing only | Fewest quota dependencies | Discards trustworthy observations and useful shared-pool accounting; not selected. |
| 3. Trustworthy quota optimization plus provider-managed fallback | Uses evidence where it works without blocking all other eligible launches | Cannot guarantee included-only billing or predict provider acceptance; selected by the user. |

### Scope and non-goals

All five paths remain required: native Claude, native Codex, OpenCode Claude,
OpenCode ChatGPT and OpenCode Go. Native Claude remains the first integration
priority, without blocking independent supported paths.

Reuse the existing router, ledger, acquisition path, native bridge and launchers.
Do not build another selector, account-control framework, quota store or scheduler.
Do not change account authentication, provider-managed overflow, credits, spending
limits, permissions, coordinator settings or explicit model choices.

Direct API Usage remains required next release, with its existing funding and
reconciliation controls. Production OpenRouter remains deferred. L1–L3 stay
post-canary; L4 remains conditional and off. No measurement-only model calls,
polling, daemon, automatic credit purchase or production activation is added.

## 2. Existing behavior and the narrow change

The existing account amendment already permits enabled accounts' ordinary
provider-managed overflow. The implementation covers only part of that contract:

- `runtime/subscription_billing.py` checks a current path/account/pool fact;
  an unknown `paid_crossover` value is allowed.
- `runtime/routing_observations.py:collect` enables fallback only for native
  Claude, then excludes routes without the demanded windows.
- `runtime/routing.py:_candidate` rejects missing or stale windows before
  considering fallback; fallback currently handles insufficient fresh headroom.
- `runtime/routing_funding.py:prefer_included` already prefers included candidates.
- `runtime/codex_account.py` has metadata-only account/catalog support;
  `usable_windows=[]` correctly refuses unsupported quota.

Extend the existing fallback distinction to missing or unusable quota for each
supported account source. Do not weaken quota parsing to make that possible.
No production path becomes qualified merely because a synthetic case works.

## 3. Two decisions, not two routers

### 3.1 Account and launch eligibility: always required

Before either included or provider-managed admission, establish:

1. The account and path are enabled within the user's scope.
2. A code-selected source reports the practical identity of the account actually
   used by that host/connection: email, organization when present and sign-in type,
   persisted as a nonsecret derived key. An organization or pool label alone is
   insufficient. OpenCode never inherits native identity by assumption.
3. The host's supported catalog supplies the requested alias and effort controls.
   Capability, task profile, host restrictions and explicit choices still apply.
   A catalog listing is not proof that the provider will accept the request.
4. Existing path qualification, account/catalog currentness, one-use dispatch,
   workflow authority, actual resource admission and pre-send binding checks pass.
   No known provider refusal applies to that route.

Recheck the available account/connection binding before send. A detected change,
expired/missing identity evidence, unreadable required source or mismatch refuses
the dispatch and invalidates dependent observations. Never substitute another
account silently to make the binding pass.

This preserves practical client-reported identity, not an atomic backend identity
guarantee across independent processes. A final read of cached authentication is
not proof that external sign-in changes were observed. Each producer/consumer
integration must establish its supported connection binding and report this
visibility limit; this design grants no new credential access or identity bypass.

### 3.2 Included-quota feasibility: optional optimization

Use the included branch only when the source establishes complete applicable
constraints, conservative original observation time, units/reset semantics,
model applicability and a supported demand estimate. Include held reservations,
debits and existing margins in the current atomic shared-pool accounting.

Uncertain backend age, unsupported constraints, missing model membership or
unknown demand prevents an included-quota claim. Keep the reading or demand
unknown; do not fabricate zero usage, infinite capacity, reset periods or seeds.

Provider permission and quota completeness are different facts. Missing quota
permission detail is not proof of permission, but does not by itself constitute
a provider refusal of an otherwise authorized normal account launch. A supported
explicit refusal always applies; fallback never overrides it.

## 4. Selection and accounting contract

After static eligibility and the existing shortlist cut:

| Evidence and result | Admission basis |
| --- | --- |
| Trustworthy applicable quota and demand; reservation fits | Included allowance with the existing reservation. |
| Trustworthy quota; reservation does not fit | Provider-managed fallback, without an included-quota reservation. |
| Missing, stale, incomplete or unsupported quota/demand | Provider-managed fallback, with quota remaining unknown. |
| Missing account identity, binding, capability, authority or qualified launch path | No launch; report the specific prerequisite. |
| Applicable provider refusal or prior uncertain send of this assignment | No new launch; preserve refusal or recovery handling. |

For automatic ranked routing, first evaluate all eligible included candidates in
the existing shortlist. Preserve the current window-pressure allocator, including
one shared pool's allowance across aliases and existing value ordering.

Only if none can reserve included allowance, select the first eligible
provider-managed candidate in the shortlist's value order. Do not compare unknown
pressure with known pressure or invent a fallback pressure score. Reuse stable
route ordering for ties. No ranking model runs.

Explicit model, effort, provider, host and funding constraints filter candidates
before either branch. A different candidate's included headroom never overrides
an explicit choice. If that choice cannot launch, report its unavailability.

Provider-managed fallback is an admission basis for the same subscription-account
route, not a newly activated direct API route. Retain existing route identity and
billing-class compatibility; record the basis and quota disposition explicitly.
`paid_crossover` remains reported or unknown, never an enablement requirement.

### 4.1 Holds, concurrency and completion

- Both branches use the existing attempt, workflow binding, hold lifecycle and
  one-use permit. An empty included reservation is not an untracked or free launch.
- A provider-managed launch claims no included capacity and reserves no direct
  API money. Its actual provider-managed charge may be unknown, not zero.
- On a possible send, prevent subsequent admissions from reusing older included
  readings for that account and every proven shared allowance. Fence prepared
  included admissions with the existing pre-send currentness checks as well.
  Preserve reservations/exposure already in flight; do not release them to create
  apparent room. Adapt existing consumed-boundary machinery, not a new store.
- Keep included optimization fenced for those pools while any provider-managed
  attempt can still consume an unknown amount: running, possibly sent or
  unresolved/uncertain. A newer reading during that exposure does not clear the
  fence. Derive outstanding exposure from the existing attempt/hold lifecycle,
  not a second registry. Eligible provider-managed concurrency may continue.
- Restore included optimization only after every such attempt has a supported
  terminal/reconciled disposition that rules out further consumption, and a
  trustworthy complete reading covers usage through the last possible
  consumption of all those attempts. A provably-unsent attempt adds no usage.
  Receipt time, elapsed time or closing an empty reservation is not coverage.
  Record terminal consumption boundaries before releasing exposure; an idempotent
  send-time mark must not suppress the later terminal boundary update. This is
  the coordinator's conservative design choice to preserve unknown-use safety.
- Completion settles once. Reuse trustworthy incidental telemetry, but do not
  infer complete window consumption or a zero charge from absent telemetry.
- An uncertain send retains the open attempt and its known or unknown exposure
  even when the included reservation is empty. Do not retry the same assignment,
  issue another permit, switch routes or clear exposure because time elapsed.
- Keep operator reconciliation and provably-unsent cancellation semantics.
  Native advisory hosts cannot prove an unsent launch after granting a permit.
- Preserve genuine shared pools. Unknown cross-host identity/sharing cannot mint
  separate included allowances or borrow another host's readings. Independent
  account-bound provider-managed launches do not require a sharing assertion.
- Preserve actual provider throttling and host capacity. Add no fixed worker,
  per-account, session or process-count cap.

### 4.2 Refusals and safe continuation

An actual provider refusal ends that attempt's send path. Do not repeat the same
route through fallback to evade the refusal. Apply the existing route/account
entitlement or availability handling.

Continue to another eligible candidate only when the host establishes the
previous attempt did not execute and the assignment's remaining authority permits
continuation. Preserve explicit choices and bounded backoff. A timeout or lost
acknowledgment is uncertain, not a safe continuation signal. No probing loop or
automatic retry campaign is added.

### 4.3 Direct API and gateway boundaries

Unknown subscription usage does not activate API Usage or OpenRouter, prove a
subscription full, open a pressure-based gateway condition, or authorize a credit
purchase. Existing API-off and subscription-only ranked-selection boundaries stay.

Future separately authorized direct API work keeps whole-task monetary estimates
with the 20% reservation buffer, protected funds, shared funding authority,
reliable balance reflection, once-only settlement and uncertain exposure.
Provider-managed overflow is enforced by existing provider account settings;
the workflow does not promise a new local monetary ceiling for it.

## 5. Per-path disposition using existing evidence

This table describes source evidence, not current login facts or live readiness.

| Path | Required account/catalog work | Quota disposition |
| --- | --- | --- |
| Native Claude | Reuse its existing account/catalog producer and bridge; qualify the consumer and launch separately. | Unsupported scoped limits/model membership stay unknown. They block included optimization, not independently eligible provider-managed routing. |
| Native Codex | Reuse delivered metadata parsing; wire its own account/catalog producer with supported launch binding. Cached-auth limitations remain explicit. | Keep `usable_windows=[]` until quota is evidenced. Never start or consume the legacy shared-pool reader as a substitute. |
| OpenCode Claude | Resolve identity from the actual selected connection; native login/environment equivalence is not proof. | Do not borrow native-Claude readings. Unsupported usage need not block a separately supported provider-managed path. |
| OpenCode ChatGPT | Resolve its own selected account; do not infer native-Codex identity or sharing. | No native quota reuse without genuine sharing evidence. |
| OpenCode Go | Bind its actual account/workspace and catalog to the launched connection. | Unproved observation age, reset/monthly semantics or demand normalization stay unknown; no invented meters. |

Missing identity or launch binding still blocks the affected path. Keep all five
in release scope; neither this design nor a native-only success completes coverage.

## 6. Lightweight execution and honest reporting

Reuse recent account/catalog data and incidental observations within existing
validity rules. Acquire only stale-and-needed evidence through existing bounded,
locked/backed-off operations outside pure preview/check/served reads.

Do not call an unsupported quota reader merely to enter provider-managed fallback.
Do not repeatedly seek the same missing contract. A failed optional quota read
leaves quota unknown; a failed required identity read still prevents launch.
Keep metadata usable independently of quota failure when its own exchange and
binding passed. No measurement-only model call is justified by this change.

The added cost is local candidate classification and recording an admission basis.
Any necessary metadata process reuses the existing bounded exchange. The lighter
provider-only alternative was considered but would discard useful quota evidence.
No new process, dependency, recurring job or performance claim is required.

Use the existing result/status surfaces to distinguish:

- Included allowance reserved; local estimate, not a provider acceptance guarantee.
- Provider-managed: included allowance unknown or insufficient; existing account
  settings apply and charges are possible.
- Blocked: exact account, launch, authority, provider-refusal or recovery reason.

Record the selected basis, original evidence references and deterministic ordering
in the existing admission/replay data. Strict replay must reproduce both branches
without live reads. Preserve historical records and their old replay behavior;
an old record without a basis marker is not silently reinterpreted as fallback.
Learning remains post-canary and cannot score missing quota or dollars as zero.

## 7. Required proof and changed gate boundaries

Implementation is not part of this design-writing task. The subsequent plan must
map these cases to real shared entry points with synthetic transports/stores:

1. Known included capacity wins over eligible unknown-quota fallback; shared
   reservations, margins and races remain correct.
2. Valid account/catalog plus missing, stale, partial, malformed or unsupported
   quota/demand admits only provider-managed fallback with no included claim.
3. Unknown overflow setting does not exclude an enabled account; explicit provider
   refusal does. Required account/catalog failures still launch nothing.
4. Missing/different/changed OpenCode identity cannot borrow native evidence.
   Detected pre-send changes invalidate reuse and issue no replacement permit.
5. Native Codex stays quota-empty and cannot reach legacy shared quota acquisition.
6. Provider-managed sends invalidate older included observations across proven
   shared aliases, including already-prepared but unsent included admissions.
   A later reading while a fallback runs or remains uncertain cannot restore
   included admission. Multiple concurrent fallback attempts require all exposure
   resolved and a supported reading covering their last possible consumption;
   replayed completion, an empty reservation or elapsed time cannot clear it.
7. Concurrent provider-managed attempts remain tracked; empty reservations,
   uncertain sends, duplicate preparation/completion and recovery remain safe.
8. Explicit choices survive global included preference; API/gateway remain off.
   Direct API funding/regression behavior remains unchanged.
9. Exact replay covers both new bases and unchanged historical admissions.
10. Each of the five paths reaches source-to-launch-to-settlement proof or remains
    explicitly incomplete. Independent behavior/accounting/security review and
    proportionate committed checks cover the integrated candidate.

Qualify account/catalog and launch/refusal/settlement independently of included
quota measurement. Only the included branch needs quota/demand qualification and
seeds; provider-managed readiness must not depend on invented quota seeds.

Before any live campaign, revise and review the
[registered validation](../../smart-routing-validation.md) and
[qualification runbook](../../smart-routing-qualification.md) for these branches.
The existing quota-denominated canary exposure ceiling cannot be claimed satisfied
by an unknown-quota launch. Preserve its old evidence and limits. Any campaign
including provider-managed launches needs separately approved, finite task/send/
time scope, existing-account charge-risk acknowledgment and explicit refusal/
uncertainty stop rules before it runs. These are campaign authority bounds, not
standing worker-count limits. No such campaign or budget is authorized here.

Retain strict replay, launcher isolation, lost-ack recovery, native advisory-host
disclosure, platform/release checks, user activation and cutover/default-ON decisions.
Historical failed quota reads remain failed quota reads, not retroactive passes.

## 8. Requirement preservation and next approval

| Agreed requirement | How this amendment preserves it |
| --- | --- |
| Capable models ordered by value | Existing profile/table/shortlist; pressure allocation for included candidates and value order for fallback. |
| Prefer subscriptions and usable included allowance | Included-first selection; otherwise enabled provider-managed accounts, with no direct API enablement. |
| Practical identity and explicit choice | Same host/connection account evidence and pre-send recheck; exact choice remains a hard filter. |
| Unknown usage, genuine shared allowance | Unsupported readings remain unknown; no fallback quota reservation or duplicated allowance. |
| Uncertain-send and financial safety | Existing attempt/permit/reconciliation lifecycle; no blind retry or direct API bypass; provider-managed charge risk is disclosed. |
| Lightweight operation | Existing bounded acquisition and incidental reuse; no measurement calls, polling, daemon or new store. |
| Five paths and later scope | All five remain required; API Usage next release, OpenRouter deferred, learning post-canary. |

The changed guarantee is explicit: routing is not an included-only billing promise
or a predictor of provider acceptance. This follows the selected approach and the
earlier account-settings amendment; it does not grant spending or access beyond
enabled accounts' existing provider-managed configuration.

Left to the builder: minimal field/module placement, compatible record versioning,
fixed reason codes, stable tie handling and focused test selection. Do not invent
another policy switch or account registry. Implementation planning follows written
design approval, with scope/gate updates before any governed implementation or run.
