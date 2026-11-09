# Usage Routing for Codex

Usage Routing uses approved existing OpenAI API credits for bounded proposal
work while preserving the Codex coordinator, existing provider choices and
independent native review. See [observed validation](../../usage-routing-validation.md)
for the scope of tested behavior.

## Modes and activation

| Mode | Selection |
| --- | --- |
| Off | Default. New live requests are blocked. |
| Manual | The first eligible task can route when the user explicitly selects the API for that task and authorizes its data. |
| Smart Balance | The first eligible task can route under standing consent within the approved task and data scope. No new approval is needed for each task or batch within that scope. |

Save a project-bound OpenAI credential in macOS Keychain. Explicit activation
records consent for the selected organization, project, key, mode, allowed task
and data scope, funding policy, any selected spending caps and expiry. Saving configuration leaves routing
Off. A saved legacy enablement needs explicit activation to record live consent.
Other Codex work can continue throughout setup and activation.

Use the [management procedure](../../../skills/manage-usage-routing/references/procedure.md)
for configuration and status. The public CLI can show saved consent and
optionally preview selection. The [trusted Codex host](../../../skills/manage-usage-routing/references/codex-host.md)
owns live sends. Reports, tests, benchmarks and bounded canaries are optional.
A missing grant, price, source permission or review prerequisite pauses the
affected request while leaving saved consent visible.

## Eligible proposals

- Preserve explicit provider and model choices, approved local preferences
  and native authentication. Use deterministic tools when sufficient.
- Select bounded, independently checkable investigations, test proposals or
  patch proposals. Exclude secrets, production actions, migrations, broad
  architecture changes, security-critical implementation, or work that
  needs tools or context outside the authorized packet.
- Bind the exact input packet, profile, reasoning, limits and source snapshot
  to one original task. Smart Balance standing scope covers self-contained
  packets without repository source. Broader transmission requires exact
  source consent and unchanged snapshots. Manual uses the original native
  user message selecting the task and authorizing its data or exact approval
  when needed. No ordinary follow-up repeats authority already established.
- Keep a distinct, ready native reviewer and record independent acceptance
  of the exact candidate or the task's evidenced abandonment.
- Verify current consent, grant, price, budget and time before each send.
  Keep each original task bound through integration and review. A blocked API-only
  task waits for resolution. Otherwise, use the authorized native path when
  available.

Evaluate at a task boundary. No idle polling, routing model or exploratory
inference is needed. Expiry can break a tie between equally important ready
tasks within scope.

## Keychain connection

Use the [connection procedure](../../../skills/manage-usage-routing/references/keychain-setup.md)
for the official project-key picker, destination approval and encrypted transfer
to Keychain. Store only opaque reference and binding metadata in settings. The
trusted transport retrieves the bound item on demand. Missing or locked
credentials pause dispatch. Keep plaintext credentials out of files, shell
arguments, logs, SQLite, and model context. Setup and ordinary status perform
no inference or billing actions.

## Funding evidence

Use the selected organization's verified Credit Grants row for the approved
pool, its remaining balance and expiry. Bind the row to the project and
organization using supported Codex Platform surfaces. The connector's
`preview_api_credit_promo` benefit expiry can corroborate the matching row for
the linked monthly benefit. Its period amount and new-claim eligibility do not
establish available credit. Prefer the exact matching UTC expiry. When only an
accepted billing date is available, use the conservative local cutoff at the
start of that date in the billing timezone or the earliest plausible start if
the timezone is unknown.

The aggregate dashboard balance can corroborate a conservative available
amount for the same organization. It does not identify a grant or establish
settlement. A display name alone cannot establish billing identity. Observe
the narrow billing region through supported controls and keep the observation
timestamp separate from a displayed refresh label. Cached text cannot extend
validity. Keep current evidence within the 15-minute admission bound and
refresh when needed for an eligible task. Stale, missing or contradictory
observations pause transmission. Renewal requires fresh funding evidence.

Confirm current pricing, including the maximum cost of counting and the
highest applicable input rate and maximum total output including reasoning.
When any charge cannot be bounded, pause before the corresponding call.

Warn that the provider may consume other credits or trigger auto-reload
despite local controls. Use only the selected pool, reserve costs and stop
conservatively. Never initiate purchases, modify billing, deliberately spend
from another pool or switch to a paid fallback. Provider-side funding
isolation and disabling auto-reload are not activation conditions.

## Monthly ceiling choices

Ask: **“How much may Usage Routing spend from your OpenAI API credits each month?”** Display the selected billing organization and cycle dates. This is one shared workflow allowance across participating enrolled projects, checkouts and Codex sessions, covering Manual and Smart Balance together. It is not an account-wide cap on unrelated API clients. Outside consumption still reduces available provider credits. Reuse the accepted ceiling when enabling.

Store the ceiling policy, cycle basis and monotonic policy revision under the shared organization ledger's authority. Checkout settings hold separate scope/transmission consent and a reference to that authority; they cannot define competing organization caps. An explicit ceiling change from any checkout changes the displayed shared organization policy, preserves accounting, and invalidates stale prepared admissions everywhere. Require an expected revision for changes and atomically check the current organization policy with reservations at dispatch. A checkout cannot select No ceiling to bypass another checkout's existing organization cap.

| Choice | User-facing explanation | Admission semantics |
| --- | --- | --- |
| **No ceiling** | Use available authorized API credits without an additional monthly workflow cap. The workflow never purchases credits; provider-side charges remain possible. | No finite monthly policy cap; verified available credits, uncertainty buffers, request bounds and any configured cumulative or per-task caps still apply. Never represent infinity as a large money value. |
| **Monthly subscription benefit only** | Use only verified API credits granted for the current monthly subscription period. Exclude separately purchased credits. | Funding restriction plus a cycle allowance bounded by confirmed benefit grants. A prorated first grant gives a prorated allowance. The full-period amount from preview is not spendable credit. |
| **Specify it: $___ per month** | Limit Usage Routing to this amount each month, using authorized existing credits. | Positive USD amount stored exactly in integer units; the lower of remaining monthly allowance and safe credit headroom wins. Reject invalid amounts; use Off to stop spending. |

The second choice deliberately means actual benefit credits only, not a numerical ceiling equal to a plan's advertised allowance. Use the matching grant row's remaining balance and accepted expiry for local budgeting; a separate grant identifier is not required. Aggregate balance alone remains insufficient. Local reservations do not select the provider's funding source. Present that limitation as a warning, not a requirement to disable auto-reload or prove grant isolation. Missing/stale grant coverage stops API admission and requests native handoff. If no monthly benefit or adequate evidence is available, show the reason, pause grant-backed API requests and offer native handoff; the feature may remain enabled, but never substitute purchased API credits. Annual benefits are outside this particular choice until separately designed. The other choices remain generic to explicitly approved existing API credits. They do not permit a grant-only assignment to spill intentionally into purchased credits.

Choosing No ceiling or a custom amount authorizes existing credits of the approved funding types in the selected organization, including verified prepaid credits when covered by setup consent. A recurring policy can cover subsequently confirmed grants of those same types without another prompt; it never initiates purchases. Unknown increases, changes of organization or funding type, and automatic recharge evidence require reconciliation before new allocation. No ceiling is not authorization for an unlimited bill.

### Optional cumulative and per-task caps

A positive `pilot_limit` bounds cumulative routing exposure. A positive
`task_limit` bounds exposure for one task. Set either independently to JSON
`null` to omit that cap. The monthly ceiling and approved funding pool are
separate choices. Removing an extra cap does not change the funding pool,
grant, cycle or accounting. A benefit-only policy still requires verified
coverage by the selected benefit.

Each request has a full cost quote and reservation, even when both extra caps
are omitted. For an ordinary request with no cumulative cap and no explicit
bound, the [Codex host](../../../skills/manage-usage-routing/references/codex-host.md)
derives the exact exposure limit from retained organization exposure plus the
full quote. Explicit request and test caps still apply.

### Cycle and reservation rules

- When a verified monthly benefit exists, align all three choices to its billing cycle. Otherwise, use UTC calendar months for a custom ceiling and display those boundaries in the user's timezone. Persist the chosen cycle basis and absolute boundaries; never infer a new grant from the calendar. No ceiling still retains cycle history and outstanding reservations.
- Count all recorded Usage Routing cost and reservations in the applicable cycle, including Manual requests, corrections/escalations and billable helpers. Activation mid-cycle grants at most the selected ceiling for the remaining period without prorating a custom amount; prior recorded workflow spend in that cycle is deducted. Unrelated pre-enrollment spending is excluded from the workflow counter but reflected in available credits. Unknown prior managed accounting blocks a counter reset.
- At normal renewal, open a new monthly policy bucket without erasing the old bucket. Each billable attempt's workflow charge belongs permanently to its admission cycle, including settlement after renewal. Replace that cycle's reservation with its actual cost when known; never migrate it into a new workflow allowance. Carry unresolved worst-case exposure in organization credit headroom across renewal, independently of which workflow bucket owns the charge. Benefit-only mode waits for confirmed grant delivery. For other modes, renewal does not add provider credits; they may use only still-valid authorized balances. Unused monthly policy allowance does not accumulate.
- A cap or mode change never clears consumed cost, reservations or cycle history. Lowering below consumed-plus-reserved work pauses new admissions; already-dispatched work remains accounted for. Raising the cap or selecting No ceiling requires an explicit user setting change. No prompt is needed for normal operations within an accepted recurring policy.
- A changed billing anchor, plan or currency cannot create a second allowance during overlapping time. Block transition until the old and new periods and unresolved charges are reconciled. Do not recompute boundaries from today's date or apply an assumed fixed day-of-month.

For a finite ceiling, monthly headroom is `max(0, cycle_ceiling - managed_cycle_cost - cycle_reservations)`. Admit against verified pool headroom and the remaining headroom under each applicable limit. Grant restrictions apply independently. Each attempt is counted once within each limit; all limits are checked atomically. Keep a conservative reservation while funding attribution is uncertain, with no duplicate dispatch. For example, a request admitted in cycle A and funded from a cycle-B grant remains workflow cost in A, while reducing B's available grant balance. Release its credit reservation only when trusted reconciliation establishes the actual charge and inclusion in the observed provider balance; calendar rollover alone never releases it. Do not also deduct that old request from B's workflow ceiling.

## Reservation and recovery

Use one owner-only SQLite database at the canonical `~/.local/state/vibe-coding-workflow/usage/organizations.sqlite3`, outside repositories. Store organization-scoped policy, accounting, observation references and request state in this database; do not create a separate registry and per-organization ledger hierarchy. Checkout consent references its database identity and organization. Coordinate the single active request slot across participating sessions in the same database. Unknown in-flight work blocks that slot until reconciled.

Concurrent registration and admission are atomic. A missing or replaced configured database, identity mismatch or requested reset blocks new admissions until trusted recovery reconciles outstanding exposure. Never reconstruct an empty allowance from a missing file. This coordinates participating processes; it cannot control other machines or protect against deliberate deletion of all evidence.

Use exact integer USD micro-units and conservative rounding. A local
reservation cannot isolate funds at the provider or constrain outside consumers.
### Conservative accounting without settlement watermarks

Establish a private accounting baseline from verified pool evidence, carrying any prior unresolved exposure. Let B be the lowest accepted authorized-pool balance observed since that baseline; D be all retained managed costs since the baseline plus outstanding worst-case reservations, counted once per attempt; F is the fixed protected floor of 20% of the first accepted balance, rounded up in USD micro-units. Admit against `max(0, B - D - F)`, further bounded by applicable spending limits. An increase in displayed balance cannot increase B automatically. Continue requiring fresh evidence; retaining an old low balance does not extend evidence validity.

Validated response usage may replace that attempt's worst-case reservation with its conservatively priced local cost in D. This settles local workflow accounting only; the cost stays deducted even if it might already be included in B. Unknown usage retains the maximum. A proven unsent attempt contributes zero. No balance refresh, elapsed time or calendar reset retires retained deductions. This can pause earlier than actual credit exhaustion; accept that conservative stop.

If headroom becomes insufficient, expiry arrives, or evidence conflicts, pause with `manual_recovery_required`. Recovery may record provider evidence for specific charges/inclusion or a fully reconciled new baseline only after its source is qualified. It cannot clear deductions by user assertion, reset the database, replay a request or fabricate a cutoff. If adequate evidence is unavailable, remain blocked; other providers remain available by explicit selection. A newly delivered grant alone does not clear old exposure.

Before each generation, freeze the full text request and model/reasoning/price profile. Obtain a qualified input-token count for the same request shape. Reserve all input at the highest applicable input rate, including any separately billed cache-write rate, plus maximum total generated tokens, including reasoning, at the applicable output rate, plus any applicable charges. Do not assume cache discounts, free helper endpoints, fixed context-tier pricing, or unverified token estimates. Verify counting-endpoint costs and semantics before dispatch and include any known maximum charge. Unknown cost bounds block generation.

The counting call itself needs admission before transmission: either qualified evidence establishes no charge, or a separate known worst-case preflight charge is reserved against the same applicable caps and grant limits. Claim the shared active-request slot before counting, including a zero-charge count, and retain it through the sequential generation. If its cost cannot be bounded without an unadmitted network call, pause before counting. Freeze the transmitted source scope before either call. Counting never grants permission to generate.

Admit only when the maximum request cost fits verified credit headroom and every applicable cap. Atomically reserve that cost and claim the single active request slot before sending. Use a unique attempt ID, immutable request digest, configuration revision and funding/pricing evidence references. One shared organization ledger prevents two checkouts allocating the same headroom; do not hold database locks across network calls.

Attempt states: reserved, dispatching, completed, rejected-before-send, unresolved, reconciled. Record dispatching before network transmission. A crash or ambiguous timeout retains the full reservation. Replaying an attempt ID returns its record, never another inference. Release only with trusted evidence of no dispatch, or settle with observed usage and qualified reconciliation. Known provider completion can free the active slot while unsettled credit exposure remains deducted; an unknown in-flight request keeps the slot blocked. Time elapsed, process exit or a response's claimed success never proves zero billing.

### One run, two separately accounted calls

One immutable `run_id` owns the active API slot and references the original task ID. It has two distinct attempt IDs, `(run_id, count)` and `(run_id, generation)`, each with its own request digest, maximum cost, admission-cycle identity, dispatch state and usage. Even a qualified free count has a zero-money admission record. Never reuse one attempt ID for both calls.

| Event | Required transition |
| --- | --- |
| Count succeeds | Durably record its result and local cost; retain its credit deduction and the run's slot; separately admit generation against the remaining limits |
| Count is uncertain | Retain its maximum reservation and the slot; do not send generation |
| Generation is denied before sending | Record `rejected-before-send`; release any proven-unsent generation reservation and the run's slot; preserve count cost and any native work already incurred |
| Crash between completed count and generation dispatch | Recovery may close the run only when the durable dispatch protocol proves generation was not sent; preserve count accounting, release the slot, and do not automatically continue |
| Generation completes | Settle its local accounting independently; release the API slot, retaining credit deductions and the task's independent-review requirement |
| Either dispatch is ambiguous | Keep that attempt's maximum and the slot; require trusted recovery |
| Any run or attempt ID is replayed | Return its stored state only; never transmit again |

A correction is a new explicitly selected run within the original task scope and applicable spending limits, retaining the original task's identity and remaining repair allowance. Each call records `dispatching` before transmission; recovery cannot infer no dispatch from process death.

The runner does not retry inference automatically. A known rate-limit rejection pauses; credit/spend-limit errors are terminal for the route until conditions change. No automatic model escalation is included. A reasoned correction or profile escalation requires explicit selection within the approved task scope, applicable spending limits and attempt bounds, preserving all outstanding reservations. No automatic fallback to OpenRouter, unapproved API credits or new paid funding is permitted. Conservative grant/budget/expiry stops may hand remaining work to the authorized native route; retain independent review and do not replay the API attempt.

### Native handoff on conservative API stops

A grant/budget/expiry pause returns `handoff_recommended`. The coordinator requests one bounded handoff containing the original task, remaining-work instructions, source snapshot references and pending API accounting. Persist only task identifiers, digest, state and native task identity in the ledger, not source text. Keep the same handoff ID across refreshes; unclaimed requests recheck the latest API-only/provider choices, reviewer readiness. A missing reviewer waits rather than selecting a paid alternative.

Claim the handoff once with a native dispatch attempt marker **before** calling the existing native agent tools. `new_claim:true` authorizes that one dispatch attempt under the original task scope; replay returns `new_claim:false` and must observe/reconcile the existing native attempt, never spawn again. The claim is tracking metadata, not proof of actual agent execution, review or completion. Preserve existing worker selection, permissions and provenance.

Tool-free API proposals cannot mutate the repository. Native continuation can therefore pick up remaining work while a previous API call's charge remains unresolved, using fresh source checks and ignoring late output unless independently revalidated. Do not replay external actions or release any outstanding API maximum/slot merely because native work starts. A later explicit API-only/provider choice needs resolution before native dispatch. Independent native review remains required.

## Model and execution scope

Preserve the coordinator exactly. Select API profiles using current official guidance and observed account availability; persist exact model, supported reasoning effort, endpoint, processing tier, pricing reference and verified price evidence. Compare profiles on the same task, then vary reasoning; do not equate maximum effort with required quality or low token price with low accepted-task cost.

The executor uses Python 3.11+ standard-library HTTPS and JSON, consistent with existing runtime transport. Send only to the fixed direct OpenAI endpoint with explicit project/organization binding. No redirects, custom base URL, environment proxy, SDK retry layer, hosted tools, remote URLs, uploaded files, conversation chaining, background mode or additional model calls. Use text inputs, no tools, disabled storage where supported, bounded response bytes and an absolute deadline. Do not reuse Langfuse-specific credentials or change global authentication.

The transport obtains only this route's bound Keychain credential through a small native Security framework adapter. Setup may use the already-required OpenAI Developers Node.js runtime for standard cryptography in a bounded memory-only helper; it adds no daemon, SDK or extra model. Qualify that helper's in-memory delivery and Keychain permissions before actual provisioning. The optional simulation transport uses synthetic authentication.

The response is untrusted text or a patch proposal. It has no filesystem/connector/tool authority. The native coordinator validates original snapshot hashes, inspects the patch, applies only within the authorized task, runs accepted tests, and obtains a fresh independent reviewer against the exact candidate. Reviewers receive requirements, relevant source, diff and test evidence; author rationale cannot substitute for their examination. The reviewer cannot be the proposal's author/session. Existing security review triggers and completion gates remain unchanged.

Direct API review suggestions may be recorded as advisory, but cannot impersonate native reviewer receipts. No modification to D5/D6 acceptance semantics is needed for API proposals. A future external authoritative reviewer requires its own trusted provenance adapter and qualification.

## Optional evaluation

Use a preview to inspect a routing decision without sending. If an operator
requests an evaluation, set its task, data, time and cost bounds before any
send. Use the [bounded canary](../../../skills/manage-usage-routing/references/first-canary.md)
or the [exact approval test path](../../../skills/manage-usage-routing/references/codex-host.md#optional-exact-approval)
when the requested test calls for it. An evaluation can report attribution,
quality and uncertainty without gating ordinary activation.

### Paired comparison template

Copy this table for each matched native baseline and candidate. Record the task
class, client, actual execution route, model and reasoning, input snapshot,
evaluation order, identical checks, and blind independent review rubric.
Give the reviewer an anonymous diff and check results with route labels and
route-identifying paths removed. Record quality, blocking findings, repairs,
handoff, and elapsed time for each arm.

| Measure | Native baseline | Candidate | Difference, source, and uncertainty |
| --- | --- | --- | --- |
| Tokens by phase | | | Split cached input, uncached input, and output. Record reasoning within output when available. |
| API cost (USD) | | | Separate provider-reported charges, estimates, and retained reservations. Record grant settlement if verified. |
| Native weekly quota (percentage points) | | | Record whole-percent start and end readings, timestamps, and reset identity. Flag unrelated activity, rounding, and unsettled readings. |

Include coordinator preparation, dispatch, supervision, verification, and
integration, as well as worker execution and independent review. Count failed
attempts and repairs in each arm. Record shared evaluation overhead separately
from expected ordinary use and count it once in pilot totals. Keep full financial
records private. Mark missing or unattributable measures inconclusive, including
account-wide quota changes that cannot be assigned to an arm.

## OpenCode follow-up gate

OpenCode 1.19.5 has a direct OpenAI Responses adapter, but that alone does not qualify credit-aware execution. Before adding it, demonstrate per-request interception/reservation for inference, retries, title/compaction/helper models and tool charges; explicit API-key authentication; exact endpoint/project attribution; supported reasoning/output parameters; permissions and stop handling; and billing reconciliation. If any outgoing billable call bypasses admission, that route cannot offer strict credit-aware operation. Preserve the existing OpenRouter runner and its qualification. Never use a model-ID-only substitution.

## Sources (authoring only)

- [OpenAI API authentication](https://developers.openai.com/api/reference/overview#authentication) and [OpenAI Developers plugin](https://developers.openai.com/learn/developers-codex-plugin), checked 3 November 2026: direct API credentials and connector-assisted project-key setup. Installed 1.3.13 source confirms an env-file writer and no exposed Keychain destination; see the redacted report for bounded evidence and the unproven dialog cause.
- [Apple Keychain items](https://developer.apple.com/documentation/security/keychain-items), checked 3 November 2026: encrypted storage, item attributes and authorized retrieval. This establishes platform capability, not qualification of the installed adapter.
- [OpenAI credit benefit](https://developers.openai.com/api/docs/guides/chatgpt-api-credits): eligible route, organization attribution, paid-credit continuation and exhaustion.
- [Per-run spending controller](https://developers.openai.com/cookbook/articles/per_run_spending_controller_responses_api): maximum-cost reservations and continued server work after client timeout; a local controller is not a final-bill guarantee.
- [Prepaid billing](https://help.openai.com/en/articles/8264644-setting-up-and-managing-prepaid-api-billing): automatic recharge and delayed cutoff/negative balances.
- [OpenAI spend limits](https://developers.openai.com/api/docs/guides/spend-limits): enforced limits versus alerts and enforcement delay.
- [Organization costs](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs): consumption reporting, not a credit-grant ledger. Current documentation and live connector/dashboard inspection informed the 1 November balance evaluation; no Costs API credential or call was used.
- [Model selection](https://developers.openai.com/api/docs/guides/model-selection) and [reasoning](https://developers.openai.com/api/docs/guides/reasoning): task comparisons and total generated-token accounting.
- [Input token counts](https://developers.openai.com/api/reference/resources/responses/subresources/input_tokens/methods/count): documented request-counting endpoint; actual account capability/cost still requires qualification.
- [OpenCode 1.19.5 provider source](https://raw.githubusercontent.com/anomalyco/opencode/v1.19.5/packages/opencode/src/provider/provider.ts), [provider authentication](https://opencode.ai/docs/providers/), and [configuration](https://opencode.ai/docs/config/): direct Responses support, auth choice and auxiliary models.
- [OpenAI Python retry behavior](https://github.com/openai/openai-python#retries): a future SDK substitution must disable implicit retries; the direct runner adds no SDK.
