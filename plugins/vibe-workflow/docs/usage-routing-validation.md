# Usage Routing validation

## Safe setup reuse and API management · 5 November 2026

The source candidate adds read-only reusable-setup discovery and optional
`enable.reuse_scope` selection. Explicit activation creates fresh checkout consent
without copying source permission or qualification. Validated identical funding
policies retain their shared revision; actual policy changes still invalidate
stale consent. Existing accounting, reservations, funding choices and ledger
identity remain intact. The public skill name is **Manage API Usage**; the
`manage-usage-routing` invocation and reference paths remain compatible.

181 affected offline tests passed using disposable ledgers, synthetic data and
fake connections. Focused settings/budget tests passed again with 49 tests after
the final boundary assertions. RED tests first reproduced cross-checkout consent
loss, the missing reuse path, binding-write failure after consent commit and
misleading status for a missing pinned ledger. GREEN tests cover equivalence,
ambiguity/selection, stale/expired/smoke/test-owned setups, permissions, revision
and disable behavior, write rollback and retained charges/reservations.
Four existing test failures were outdated revision assumptions: the policy-change
fixture now changes the policy, and three live tests use the retained revision.

Package validation and Skill Creator quick validation passed; the entrypoint is
145 words. The bounded instruction reader delivered management alone for
activation/status/ambiguity and the shared permission contract when applicable.
An implementer walkthrough checked those requests, preserving new-connection,
delegation and canary procedures as conditional loads. This is offline package
and contract evidence. Independent review remains the coordinator's next step;
actual client activation and live execution were not exercised. No real account,
credential, billing, enrollment or installation state changed.

The first independent review identified stale setup selection, incomplete
replacement-ledger recovery, mode-only ambiguity and hidden profile/connection
differences. One combined repair adds a required first-reuse `reuse_pin`, checked
against the selected source and current policy within the consent transaction;
validates retained canonical/source ledger pins before new bindings; compares
effective target modes; and exposes full safe profile/conversion plus connection
comparison metadata. Omitted or mismatched pins cannot enable. A separate source
revision/freshness pin does not create ambiguity between equivalent settings.
190 affected offline tests passed after the repair, including changed setup/source
state, unrelated-checkout stability, reduced-accounting replacement recovery,
missing/late-lost source pins, explicit-mode equivalence and visible safe
differences. Package and Skill Creator checks passed again. Scoped independent
re-review remains pending; no live client or account operation was performed.

Manual and Smart Balance passed a
[foreground Desktop dry run](#codex-desktop-host-dry-run--4-november-2026).
The [live canary](#first-live-foreground-canary--4-november-2026) passed and
returned to OFF. The [delegation registration integration](#delegation-registration-integration--4-november-2026)
records the checks for this candidate separately from earlier live tests.

## Initial offline implementation

Smart Balance/Manual selection, local configuration, maximum-cost admission,
count/generation orchestration, native review, restart/replay protection and
recovery have **68 passing offline tests**. Seven selected existing OpenRouter and
native-review regressions pass. Package/skill validation and whitespace/link checks
pass. Independent behavior/security review passed after scoped fixes.

The protocol tests inject fake connections and synthetic authentication. They do
not contact OpenAI. The default transport rejects before connection/key access.
Local enablement saves the requested feature mode with a best-effort funding
warning; it cannot activate real requests without the actual adapter. Smart Balance returns shadow
selection plus its actual-adapter blocker; source labels and qualification flags remain
non-authoritative. No real keys, source transmission, billing/settings changes or
installation was performed. Existing providers and independent review are preserved.

## Limits of the initial offline implementation

The user explicitly accepts warned best-effort grant use; auto-reload OFF and a
provider-backed credit-only boundary are not enablement requirements. Actual
client/price/grant-source qualification remains pending. The authenticated live connection adapter remains
unimplemented. The accepted dashboard/grant expiry observations establish
readability only; they do not prove current account settings or settlement.
Live canary/evaluation, actual quality/savings, account-wide enforcement and native
client execution are not qualified. The full suite was not rerun for this slice.
Routing remains OFF. No merge readiness or release is claimed.

## Read-only credit verification · 3 November 2026

The user authorized credit verification in Codex. The connector benefit preview,
MCP Apps balance dashboard and signed-in Platform pages were read without keys,
inference or billing/settings changes. Current credit availability and the
accepted matching grant expiry were observed; a stable Platform organization ID
is now recorded privately. No project-request billing attribution was exercised.

Automatic recharge remains enabled and the organization spend limit explicitly
reports that it is not enforced. These are observed risks. Under the user's later
best-effort policy they are warnings, not feature-enable blockers. A dashboard Refresh did not advance
its displayed minute and establishes no provider settlement watermark. The existing
shadow ledger was not populated with account data or promoted to live authority.
Routing remains OFF. Private financial observations: `/var/folders/tz/c2kgm3r911ndfsbh8zm53lvc0000gn/T/vcw-credit-verification-banwgjsp/observation.json`.

## Best-effort enablement and native handoff · 3 November 2026

The user revoked the auto-reload-OFF/provider-isolation requirement. Default remains
OFF, but explicit local enablement now succeeds with a warning and no billing
change. Credit/freshness/expiry stops recommend one native handoff, preserving all
pending API accounting and required independent review. Unclaimed handoffs refresh
current choices and reviewer readiness; one-time claims precede native host tools, and replay must
observe rather than respawn. Actual native execution is not claimed by a queue entry.

74 focused offline tests pass. Independent review found and verified fixes for
stale handoff choices and one-use claim identity. Real
adapter/authentication, request billing attribution and provider funding behavior
remain unqualified. No actual user configuration was enabled by this change; no
billing settings, purchases, live API requests or actual task handoff occurred.

## Keychain implementation

The credential adapter uses a dedicated service in the default macOS
Keychain. This uses the file-based Security framework APIs so the local Python
interpreter does not need a Data Protection Keychain access-group entitlement.
The dedicated process suppresses optional Keychain UI during the operation.
The item account binds the opaque reference to the selected organization and
project. Version 2 configuration saves only that binding. Version 1 remains
available for simulations. The setup helper exchanges a public JWK and encrypted
connector result, then pipes decrypted bytes to the Keychain writer.
The trusted setup host must pass the same absolute Python interpreter used for
lookup, keeping the Keychain access identity consistent.
Lookup uses a five-second child-process deadline and returns the secret to the
transport over a private inherited pipe.

84 focused offline tests passed, including a fake Keychain backend and an
isolated synthetic setup exchange. They cover scoped lookup, sanitized failures,
storage without a network request, and OFF configuration without credential
access. Package validation passed and the Security framework symbols loaded
without accessing an item. Live dispatch is still unavailable.

## Disposable Keychain probe · 4 November 2026

Passed from the current Codex command runner on macOS 27.0.1 with Python 3.15.0
at `/opt/homebrew/opt/python@3.15/bin/python3.15`. The desktop build number
was unavailable. The tested credential adapter SHA-256 was
`5382786b4e9327ce0c6041ede3ccb50ccd57687ee2d4b7e0e338f3cf3f2cc9a5`.

The adapter wrote one synthetic noncredential value to the default Keychain and
resolved it from a separate process through the private pipe. Separate lookups
with a different organization or project were denied. Cleanup matched the exact
item and value before deletion. A final lookup returned `errSecItemNotFound`.
The probe completed without user interaction. This qualifies the observed
interpreter and session only. Locked, background, and resumed sessions remain
unverified, as do connector provisioning and live dispatch.

### Encrypted setup rehearsal

Passed in a detached local process under the same logged-in session, using
Node.js 24.22.0 and the same Python executable. The rehearsal used a byte-for-byte
copy of the setup helper, a synthetic encrypted connector response, and the
production Keychain writer and resolver. The test launcher pinned the random item
reference so cleanup could find the item even if the writer lost its receipt.

The helper returned the public JWK and opaque receipt. The decrypted disposable
value reached Keychain through the private writer pipe and matched the value
retrieved by the resolver. Cleanup verified ownership, removed the item, and
confirmed `errSecItemNotFound`. No connector call or HTTPS request was involved.
This rehearsal covers the local encrypted handoff. The Codex approval surface,
actual connector provisioning, locked sessions, and live dispatch still require
their respective checks. All 84 focused offline tests passed again.

The installed 0.1.6 package recognized the accepted Codex enrollment and lacks
the Usage Routing skill, runtime, and setup helper. The development checkout
reported `configuration_changed` for enrollment and
`live_adapter_unavailable` for routing. Qualification through the installed
skill still depends on packaging the feature and reconciling the candidate with
the accepted enrollment.

## Setup and HTTPS wiring · 4 November 2026

The Codex setup bridge preflights the checkout scope and uses the approved
selection, the qualified absolute Node and Python executables, and the encrypted
connector exchange. Its local output contains public creation arguments and a
safe OFF receipt. The Codex host owns approval and the one connector call.

The authenticated transport uses verified TLS to the fixed OpenAI host and a
one-shot ledger grant immediately before each POST. A credential failure before
generation leaves a completed count charge in place, rejects generation as
unsent, and retains the task's review requirement. Connection preparation returns at
the deadline. OS resolver work may finish later and closes any late socket
without a POST. The setup helper stops its process group before reaping the
leader, including when a writer survives an early Node exit.

102 focused offline tests and package validation passed. Independent review
verified the connection deadline and process cleanup fixes. A full discovery
run showed failure markers and was stopped before it emitted diagnostics.
The checkout still reports OFF and simulation only. Actual Codex approval,
connector provisioning, trusted live admission, and provider billing behavior
remain unverified.

## Internal Manual admission · 4 November 2026

The typed host admission binds the exact task, checkout, credential reference,
grant row, pricing, approval cap, and distinct native reviewer to one run.
Separate count and generation reservations recheck the same pin and cap.
The final transaction rechecks checkout scope, source snapshots, and deadlines.
A newer depleted or conflicting grant observation invalidates older evidence
even when the new observation prevents a request. Native review remains
required after successful transport.

122 focused offline tests passed, including duplicate run contention, scope
revocation before dispatch, exact-count cap enforcement, and grant conflicts.
Package validation passed. The public CLI reports OFF and simulation only.
The installed Codex host still needs an evidence issuer with verified
provenance and canary gating. The current checks do not establish actual
provider attribution or qualify Smart Balance for live use.

## Codex evidence qualification · 4 November 2026

Codex CLI and app-server 0.165.0 on macOS 27.0.1 arm64 installed and discovered
the checkout candidate (manifest 0.1.0) in a disposable, unauthenticated home
with network access denied. Native skill discovery listed Manage Usage Routing.
The installed script reported configured enrollment, OFF, and
`live_dispatch:false` on an enrolled fixture and created no routing state.
This was a CLI discovery and status check. The active Desktop installation and
host evidence issuance remain separate qualification work.

Read-only host checks returned a Codex account identifier, a quota window with
used percentage and reset time, and the OpenAI Developers benefit expiry in UTC.
The benefit preview did not supply the linked organization identifier or remaining
balance. Use a fresh matching Platform grant row with the approved organization
and project before forming live funding evidence. The aggregate balance and a
displayed refresh time cannot establish grant coverage or request settlement.
Quota rounding and attributable task consumption remain unqualified.

[OpenAI's token billing guidance](https://help.openai.com/en/articles/4936856-understanding-and-counting-tokens)
explicitly confirms that `POST /v1/responses/input_tokens` is free and uses no
normal inference quota. This qualifies a zero monetary bound for that exact
endpoint. Keep its separate zero-dollar ledger reservation, task approval and
scope checks. Verify the chosen generation model, tier and rates independently
against the [API pricing page](https://developers.openai.com/api/docs/pricing).

The 15 admission tests and package validation passed. This source check did not
issue live admission evidence or authorize a canary.

## Desktop admission dry run · 4 November 2026

The signed-in Desktop host returned an account-bound weekly quota window with
a reset time and allowed ordinary usage. OpenAI Developers returned the
benefit expiry in UTC. A read-only Platform browser check found the available
grant row with the matching expiry date, confirmed the selected organization
identifier, and matched the previously selected project. No account identifiers
or balances are retained in this note. These observations do not establish
provider settlement or an independent reviewer for a future task.

The local quote used an illustrative GPT-6.1 Sol standard-tier text profile
with a 10,000-input-token bound and 64 total output tokens. The conservative
generation reservation was $0.05064, including the highest published cache
write rate. The counting reservation was $0. This was a quote, with no
provider count or generation. See [model documentation](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
and [API pricing](https://developers.openai.com/api/docs/pricing).

The dry-run record carried no live approval. The public run guard returned
`live_execution_unavailable`, and the internal boundary rejected the record
as `trusted_live_evidence_required`. The checks observed zero credential
lookups and zero sends. Before and after status matched and reported OFF.
Installed 0.1.6 recognized enrollment and did not contain the routing skill
or admission module. This rehearses evidence collection and denial. An installed
Desktop evidence issuer, independent reviewer, and explicit canary authorization
remain pending. All 15 focused tests and package validation passed.

## Foreground canary rehearsal · 4 November 2026

The [foreground procedure](../skills/manage-usage-routing/references/first-canary.md)
binds one direct Codex user approval to the checkout, staged package, selected
key, grant, fixed request and independent reviewer. The coordinator reads the
native thread tools directly. The local verifier checks their bindings.
This qualifies foreground coordination only. Installed Desktop skill discovery
and unattended execution remain separate.

The disposable ledger rehearsal used fake transport connections. It sent one
count and one generation, kept independent native review, returned the scope
to OFF, and rejected a replay. A regression test reproduced activation committing
before an exception, then verified that cleanup disables the owned revision.
Astra independently confirmed the fix. All 135 focused offline tests passed.
Real dispatch and provider settlement await the approved canary.

The staged candidate matched an isolated Codex install byte for byte. Codex
discovered the routing skill, and the candidate recognized the actual configured
checkout and reported OFF without creating routing state. The active 0.1.7
installation recognizes that checkout and lacks the routing skill. The approved
canary would run through the staged candidate in the foreground.

The signed-in Chrome read showed different figures in the matching grant row,
the Credit Grants summary and the billing overview. The canary requires fresh
grant and organization balance evidence and uses the lowest figure as an
additional local cap. Settlement remains unverified. The current Codex turn
lacked live approval and was rejected.

The first direct Desktop approval was recorded with one terminal line feed.
The original verifier rejected it before credential setup or dispatch. The
replacement candidate accepts the exact token with or without that single line
feed. Astra independently checked the boundary. The same native receipt
reproduced the fix in a read-only check and was rejected for the replacement
plan. All 136 focused offline tests passed. The scope still reports OFF.

## First live foreground canary · 4 November 2026

Codex Desktop verified the direct approval against the staged candidate. The
official connector created the approved project credential and the local bridge
confirmed Keychain storage. The grant checks passed. One
[free input count](https://help.openai.com/en/articles/4936856-understanding-and-counting-tokens)
and one generation completed. GPT-6.1 Sol returned `READY`. The ledger recorded
a $0.000700 generation reservation and a $0.000110 conservative usage estimate
under the approved $0.05064 ceiling. Routing returned to OFF at revision 3.

Astra independently accepted the result. Task completion was recorded, and the API accounting was retained. The Platform Usage
view showed the selected project and key with no matching activity yet. The
estimate is local. Project billing attribution and grant settlement remain
pending and must be checked before a further evaluation case.

## Skill and mode wiring · 4 November 2026

The management skill loads setup, status, mode selection and proposal procedures
as needed. Setup stays OFF. Explicit mode selection changes checkout consent
without replacing the connection or shared budget. Offline tests exercise the
typed Smart Balance runner, including qualification changes between count and
generation and the independent review requirement. The proposal instructions surface
routing outcomes in chat and use existing optional Habblaud visibility for
actual native reviewers and handoff workers.

All 139 focused offline tests and package and skill validation passed. In a
credential-free, network-disabled Codex CLI 0.165.0 install, `plugin/read` and
`skills/list` discovered the management skill for the enrolled checkout. This
observes installed discovery in the disposable client. Ordinary Desktop live
dispatch still requires a trusted admission issuer, and Smart Balance stays in
shadow until class qualification. This slice made no live request.

## Foreground Codex host integration · 4 November 2026

The [coordinator procedure](../skills/manage-usage-routing/references/codex-host.md)
uses the installed helper to read native thread receipts. Manual binds
one explicit task to its active approval. Smart Balance may reuse direct consent
for an exact bounded batch in the same chat. Each dispatch binds the checkout,
package, current settings, task, funding, price and distinct reviewer. Missing or changed evidence stops dispatch, and the public CLI cannot send.

All 147 focused offline tests passed. Synthetic checks exercised the bridge and
shared ledger, including stale approval, replay, mismatched evidence, one task
through review and uncertain accounting. Astra independently reviewed the change.
The bridge now reports execute failures conservatively when the accounting
outcome is unverified. Package and skill validation passed. No live request was
made during this integration.

A disposable network-disabled Codex CLI 0.165.0 installation discovered the
skill and matched all 281 staged files. The staged package reported OFF for
the accepted checkout. This check establishes discovery and local status.
Ordinary live Desktop execution, provider attribution and measured Smart Balance
qualification still require separate evidence.

## Codex Desktop host dry run · 4 November 2026

The current Desktop chat supplied a genuine active-turn receipt. A native history read retrieved an earlier direct user turn
in the same chat. Desktop returned its cursor at `page.nextCursor`. The original
helper expected a top-level cursor, so Smart Balance could not recover earlier
approval. The nested response fixture reproduced the failure and passed after
the narrow fix. The corrected staged package was discovered in a disposable
offline Codex install.

The packaged bridge prepared both modes with the actual Desktop receipts and
denied genuine messages that lacked the exact plan approval. Separate disposable
ledgers used synthetic approval, pricing, funding and transport to exercise the
positive paths. Each mode recorded one simulated count and one simulated
generation. Both results required native review. Manual rejected replay. The harness denied network access and active ledger access, and
forbade real HTTPS and credential resolution.

Astra reviewed the candidate, harness and evidence. All 147 focused offline
tests and package validation passed. The actual checkout reported OFF at the
same revision before and after. The dry run qualifies Desktop receipt handling
and the rehearsed path. It establishes no live approval acceptance, credential
access, provider settlement or measured Smart Balance savings.

## Bounded Manual host test prepared · 4 November 2026

The Platform Usage view now shows one request under the prior canary's selected
project and dedicated key. The spend display rounds to cents, leaving grant
settlement unverified. This observation establishes project/key attribution for
the canary. Comparative evaluation still requires the remaining funding evidence.

The trusted Desktop host froze one source-free Manual proposal while the actual
checkout reported OFF. The plan binds the existing Keychain connection, current
settings, package, task, approval and independent reviewer. The preparation turn
was rejected as authorization. Exact approval is required before temporary
activation. The test permits one free count and at most one generation, with
a $0.00564 new-cost bound and $0.00575 combined ledger cap.

All 149 focused offline tests passed. Astra verified cleanup and the guard
against concurrent qualification changes. Package and skill validation passed.
The staged package recognized the checkout. Preparation left active routing
OFF and made no live API request.

## Bounded live Manual host test · 4 November 2026

The staged foreground host verified the exact native approval, selected grant,
pricing and reviewer readiness. It reused the existing Keychain binding.
One free count and one generation completed. GPT-6.1 Sol returned `MANUAL`, the
exact requested word. Generation reserved $0.000710 after counting and recorded
a conservative local cost of $0.000130. Combined retained accounting was
$0.000240. The host returned routing to OFF at revision 5 with the run closed
and the API slot clear.

Astra independently accepted the result. Task completion was recorded while
preserving the API ledger. The Platform project
aggregate showed two requests, but the exact key filter still showed only the
prior canary. Attribution for this request and grant settlement remain pending.
This qualifies the tested one-shot Manual path.

## Manual test attribution recheck · 4 November 2026

The transport bound the request to the selected OpenAI organization and project
and used the existing Keychain credential. Platform Usage showed two requests
for the project. Two checks with the dedicated key filter showed one request,
with the earlier canary's input count. The key's tracking ID matched the filter.
Its last-used field showed the date without a time. The Manual request ID had no
match in the project Logs view. Provider confirmation at the key level remains
pending.

The matching benefit grant remained available. Credit Grants and billing
overview showed no visible balance change, and Billing History supplied invoice
summaries. The local $0.000130 estimate is below the displayed cent precision.
OpenAI's [published funding order](https://help.openai.com/en/articles/20001626-api-credits-benefit-for-eligible-chatgpt-subscriptions)
supports expected promotional credit use. The available provider views did not
identify the funding source for this request. Smart Balance evaluation remains
paused. The read-only check made no new API request and routing stayed OFF.

## Smart Balance shadow tests with delayed attribution · 4 November 2026

All 81 focused selector, ledger, admission and host tests passed against the
staged runtime with network access and active ledger reads blocked. A separate
disposable replay used the two recorded local cost estimates as fixtures. A
fresh observation of the same balance retained all $0.000240 of local accounting.
The next $0.000740 proposal exceeded the $0.000900 fixture cap once prior costs
were included, and Smart Balance paused without changing the ledger or sending.

OpenAI [documents request counts as a usage metric](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/completions).
The reviewed documentation does not establish a minimum cost for reporting a
request. Rounded display amounts do not retire local costs or confirm grant
settlement. Routing remained OFF.

## Smart Balance paired pilot preparation · 4 November 2026

The user accepted unresolved key attribution and grant settlement for this
pilot only. The frozen foreground plan pairs four synthetic Python triage
cases with supervised Manual API proposals and a native-only baseline. A
distinct native reviewer scores both arms before the next case. Smart Balance
replays decisions in shadow using the same evidence. The plan contains no
activation, retries, or paid fallback.

The staged host froze the four proposals against the OFF configuration. The
new API maximum is $0.03024. The cumulative local ceiling is $0.03048 with
prior costs retained. The offline rehearsal proved that Manual restores OFF
and preserves the saved profile after each case. It also proved that the next
case requires the previous run to complete, independent review acceptance and recorded task completion. All 150 focused offline tests and package validation
passed. Astra independently reviewed the bound, consent and review lifecycle.
Live pilot results and measured savings are pending. No pilot API calls ran.

## Smart Balance paired pilot stopped at the quality gate · 4 November 2026

The exact approval admitted the first supervised Manual case. One free count
and one generation completed. The generation's conservative local cost was
$0.001655. The API slot cleared, routing returned to OFF, and both answers were independently reviewed. No later case ran.

Astra scored the anonymized outputs against the frozen rubric. Both found the
correct fix. The native answer tested the expiry instant alone. The API answer
tested both sides with equivalent values. The rubric required specific values
that the shared prompt omitted, and both answers failed that check. The pilot
therefore stopped at its agreed quality gate. The result does not establish a
quality difference between routes.

Smart Balance shadow replay paused with no class qualification and issued no
request. Weekly usage moved from 52% to 53% across the account during the run.
That reading does not isolate either arm, excludes earlier preparation, and
does not establish savings. Provider settlement remains unverified.

## Revised paired pilot frozen · 4 November 2026

The revised pilot starts with a new fixture. Each shared prompt states all
scored regression scenarios, and the independent rubric accepts equivalent
values and test formats. Astra reviewed all four frozen prompts and rubrics.
The host rejected both the preparation turn and the prior pilot approval.
The $0.03024 new cap retains $0.001895 of prior local accounting. Routing
remains OFF pending the new exact approval. Preparation made no API requests.

## Revised paired pilot completed · 4 November 2026

The approved pilot completed four pairs. Astra independently scored both
arms 3/3 on every frozen rubric. Four free counts and four generations used
$0.008180 in conservative local accounting against the $0.03024 new cap.
The host returned to OFF after each case. The final ledger had no open API
slot. No retry or paid fallback ran.

The weekly account-wide display stayed at 53% across arm and review readings.
That granularity does not establish zero native use, isolate preparation, or
demonstrate savings. All four Smart Balance shadow replays paused for lack of
class qualification, with no additional requests. Grant settlement remains
unverified. The result supports quality parity on these four synthetic cases.

## Repository-backed paired pilot frozen · 4 November 2026

The two frozen cases cover linked checkout scope and a subprocess deadline.
Historical baselines failed the targeted checks. The fixed revisions passed
the same targeted checks under network denial, including the worker suite on
the deadline reference. Exact excerpts, test files, source archives and the
local token reader were pinned. Astra reviewed the envelope and the separate
blind reviewer confirmed readiness. The new API cap is $0.01512. Native
counter deltas require completed turns and a verified baseline. The host
rejected the previous approval. Routing remains OFF pending exact approval.

## Repository-backed pilot stopped on the first response · 4 November 2026

The linked-checkout input count completed. The runner rejected the first
generation as `incomplete_response` and restored OFF. The parser did not
retain a provider reason or validated usage for that response. The API slot remains held, with $0.004420 retained as maximum
exposure for the unresolved generation. No native coding arm, second case,
retry or fallback ran. This pilot produced no quality or savings comparison.

## Incomplete response diagnostics and reconciliation · 4 November 2026

The selected project's Responses Logs returned no match for the exact client
request ID. The original runner retained no provider identifiers, reason or
usage. The scoped reconciliation command reported `manual_recovery_required`.
At that point, the $0.004420 reservation and API slot were held.
Routing was OFF, and no API request was sent during reconciliation.

The updated runner retains allowlisted incomplete reasons, validated request
and response IDs, and bounded token usage when the provider supplies them. It
stores this metadata with the unresolved call. The scoped `reconcile` report
exposes it for diagnosis. The outcome remains unresolved and requires provider
evidence to settle. Astra independently reviewed the change. All 156 focused
offline tests passed, and the staged candidate read the legacy pending record.

## Local retirement · 4 November 2026

A separate authorized local operation recorded the generation as
`retired_unverified`. Readback showed routing OFF and the API slot clear. The
$0.004420 remains included in the $0.014495 cumulative accounting. Provider outcome and actual charge remain
unknown. No API request, retry or fallback occurred during retirement.

## Delegation registration integration · 4 November 2026

The integration worktree started from `origin/main` at `0cc0a79`. It uses an
explicit snapshot of the existing Usage Routing runtime, skill, scripts,
fixtures and documentation from the primary checkout. The source snapshot
SHA-256 is `a94089aa50d9f9e284e5b5ced106a14786285c012765ccda72b9975a13363dd1`.

211 offline Usage Routing, transport, worker visibility and instruction
loader tests passed, plus the existing OpenRouter badge replay test and
package validation. The Manual and Smart fixtures retain their run and attempt
IDs and record the actual route. The Codex host rejects mismatched settlement
evidence before display. The bridge returns the settled proposal when the
display wait fails. Receipt tests cover the historical UUID, replay, conflicting
labels and a second client transport without Codex observers.

The instruction loader delivered the selected sources for native delegation and
review, Manual proposals, Smart local preference, API-only stops, unresolved
handoff, unavailable display and a second client without a qualified transport.
Those selections leave routing management unloaded. The routing and review
fixtures cover their corresponding decisions and evidence limits.

In the live OpenRouter Desktop check, the native activity showed
`Review totals via openrouter started working` and
`Review totals via openrouter finished`. The first Habblaud badge read
`Scout · GLM 5.3 Flash`. The OpenAI API preview showed `Scout · GPT-6.1 Sol`
with synthetic settlement evidence. Live API registration and a Claude Code
transport remain unqualified. The unresolved API charge stayed on hold during
this integration.
