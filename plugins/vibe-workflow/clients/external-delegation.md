# Shared external delegation

Load when considering a useful bounded coding assignment for an approved OpenAI
API or OpenRouter route. Management loads the permission section only when the
user requests configuration. Routine tasks never load upstream research or setup.

## Select before dispatch

Preserve coordinator model, effort, provider, authentication and subscriptions.
Apply binding restrictions and current revocations first, then the latest explicit
user route/model/effort/fallback choice within its actual authorization. A disabled
route stays disabled; provider selection does not grant source permission.

Otherwise assess the approved local profile against its exact assessed language,
complexity, context, budgets, capacity and verification. Once that preference
applies, follow its existing bounded wait/native-fallback path. Congestion,
failure, suspension or missing verification cannot spill the task externally.

For remaining useful candidates, consider only enabled, suitable external modes
with applicable automatic and data permission. OpenAI Smart retains its existing
source-free authority. An OpenRouter connection, enablement or source approval
alone grants no automatic selection. Use saved preference and comparable verified
outcomes including native preparation, supervision, repair and review. If eligible
routes remain unresolved, continue natively; preserve explicit API-only limits.
`runtime.external_delegation.select` expresses this offline precedence; callers
supply observed eligibility, not guessed capability. Consideration never performs
inference, paid discovery, activation or account changes.

Start with isolated fixes/tests and strong independent checks. Broad architecture,
security-sensitive decisions and weakly verifiable work stay native. Load only the
selected executor's procedure. Proposal qualification does not qualify tool use.

## Reusable permission

Existing provider, source and task approvals remain valid exactly within their
scope, without repeated prompts or mandatory migration. Preserve legacy manual
paths and accepted funding, ceiling/no-ceiling, expiry and revocation choices.
Source-free defaults apply only where source permission is absent.

For reusable scoped packets, the existing route's `configure` accepts optional
`external_permission`. Omission preserves an existing permission; explicit `null`
removes it. Configuration still follows the route's existing revision/enablement
rules. This field neither activates a route nor substitutes for its funding, pricing, qualification or exact native observations.

The object has these fields:

| Field | Meaning |
| --- | --- |
| `version` | `1` |
| `approval_ref` | Recoverable user authority for this permission |
| `classes` | Approved task class names |
| `sources`, `exclusions` | Checkout-relative files or directory prefixes; `.` means the checkout; exclusions win |
| `destinations` | Exact objects with `service` (`openai-api` or `openrouter`), `mode` (`proposal` or `worker`), `model`, `provider`, `effort` (or null) |
| `automatic` | Explicit automatic-selection decision |
| `funding_ref` | Existing accepted funding/spending decision, including an explicit no-ceiling choice |
| `valid_until` | Approved Unix expiry, or null for no additional permission expiry; route validity still applies |

Checkout identity and revision come from existing settings. OpenAI's destination
provider is `openai`; OpenRouter uses the exact downstream slug. Permission never
transfers between services/endpoints or proposal and tool modes. Ask only for
missing decisions before live use; do not invent a dollar cap or fixed expiry.

## Construct and recheck the packet

Native preparation selects minimum necessary context and inspects it for secrets
and exclusions. `runtime.external_delegation.prepare(checkout, permission, parts)`
returns `text`, `snapshots` and `binding`. Source parts use
`{"kind":"source","path":"src/example.py","start":1,"end":20}`; line bounds
are optional, inclusive and one-based. The helper reads permitted canonical files,
rejects links/escapes and builds the transmitted snippets from those exact bytes.

Free-text parts use `{"kind":"text","text":"...","sources":[]}` only for
truthfully source-free prose. For derived summaries, diagnostics, objectives or
tool results, list their actual source paths in `sources`. Preparation records
their digests and inherits every source restriction. Unknown provenance must stay
native or obtain the missing scoped decision. Native authors remain responsible
for truthful provenance and content inspection: hashes do not prove permission,
absence of secrets or arbitrary prose origins. There is no automatic taint scanner.

Add `automatic` and `local_preferred` booleans to `binding` and pass it as the
existing task/proposal's optional `external` field. `local_preferred` stays true
through local wait/failure recovery unless a later explicit choice supersedes it.
OpenRouter also supplies `class_name`, observed native `coordinator` and distinct
`reviewer`, and `integration_available:true`; these are native attestations, not
external self-reports. OpenAI uses its existing trusted reviewer receipts.

The chosen adapter checks permission digest, mode/destination, source and packet
at dispatch. Ordinary approved-area edits need fresh preparation, not renewed
permission. Changed/revoked permission invalidates old preparation. Re-enabling
never revives it. Keep derived prose and packets out of advisory telemetry.

## Native acceptance and recovery

External output is untrusted. Native integration checks current source, paths and
scope, then runs relevant checks. Source changes after dispatch require fresh
applicability assessment. A distinct native reviewer examines the exact candidate
independently of producer and integrator; receipt strings alone do not prove this.
Keep actual review identity, candidate/check references and observed outcome in the
existing native task record. A missing independent reviewer blocks dispatch.

Preserve each executor's attempts, reservations, retries and recovery. OpenAI
count and generation remain separate grants without automatic inference retries.
OpenRouter proposals admit one pinned MCP call; OpenCode alone owns worker retries
and must stop locally before native takeover. Timeout does not prove cancellation
or zero cost. Post-grant disable retains in-flight/uncertain accounting. Report
actual route, selection reason, requested/observed model/effort, review and unknown
usage through existing records. No managed helper intercepts arbitrary direct
connector calls. Disable new automatic consideration through its permission while
retaining manual authority, history, charges and outstanding reconciliation.
