# Shared external delegation

Load when considering a useful bounded coding assignment for an active OpenAI
API or OpenRouter route. Management loads the permission section only when the
user requests configuration. Routine tasks never load upstream research or setup.

## Select before dispatch

Preserve coordinator model, effort, provider, authentication and subscriptions.
Apply binding restrictions and current revocations first, then the latest explicit
user route/model/effort/fallback choice within its actual authorization. A disabled
route stays disabled: a request to use it for one task does not enable it. Say it
is off and ask whether to enable it; enabling also authorizes later autonomous use.
Provider selection does not grant source permission.

Otherwise assess the approved local profile against its exact assessed language,
complexity, context, budgets, capacity and verification. Once that preference
applies, follow its existing bounded wait/native-fallback path. Congestion,
failure, suspension or missing verification cannot spill the task externally.

For remaining useful candidates, consider enabled, suitable external modes.
Activation is the only approval: an active route may be selected automatically
and may receive the checkout's task-relevant data, unless a saved permission
turned automatic selection off or narrowed its scope. An account connection
alone grants nothing. Use saved preference and comparable verified outcomes
including native preparation, supervision, repair and review. If eligible routes
remain unresolved, continue natively; preserve explicit API-only limits.
Outside managed projects, `runtime.external_delegation.select` expresses this
offline precedence; callers supply observed eligibility, not guessed capability,
and take each route's `automatic` from `automatic_allowed`. Consideration never
performs inference, paid discovery, activation or account changes.

Start with isolated fixes/tests and strong independent checks. Broad architecture,
security-sensitive decisions and weakly verifiable work stay native. Load only the
selected executor's procedure. Proposal qualification does not qualify tool use.

## Managed projects

In a project on Smart Routing's `managed_projects` list, routing `prepare` makes
the external choice and no caller-owned selector runs. The executor's launcher
sends the coordinator's explicit route, model and effort as the assignment's
`choice`: routing admits exactly that route or answers `pending`, and never
substitutes another. The user's scope stays a set of constraints:
`runtime.external_delegation.constraints` turns the effective permission into
routing constraints and refuses local preference or a saved `automatic: false`
before routing is asked. A `pending` or `blocked` answer returns the task to
native work; do not fall back to `select` or try another route. Endpoint pinning,
source, permission, packet and independent-review checks are unchanged, and
activation and funding stay each route's own. OpenRouter proposals do this inside
`begin` ([procedure](../skills/openrouter-proposals/references/procedure.md#managed-projects)).

## Saved permission (optional)

Activation needs no reusable permission. `effective_permission(saved, activation,
...)` derives each assignment's permission from the route's own saved
activation, which its status reports as `activation`: the activation reference,
its funding decision and any class or route limits. A configuration change,
disable or re-enable changes the reference, so earlier preparations stop
matching. Accepted funding, ceiling/no-ceiling, expiry and revocation choices
still bind.

A saved permission is an explicit restriction. The existing route's `configure`
accepts optional `external_permission`; omission preserves an existing one and
explicit `null` removes it, returning the route to its activation. While saved,
it binds exactly: its automatic opt-out, classes, sources, exclusions,
destinations and expiry. It neither activates a route nor substitutes for its
funding, pricing, qualification or exact native observations.

The object has these fields:

| Field | Meaning |
| --- | --- |
| `version` | `1` |
| `approval_ref` | Recoverable user authority for this restriction |
| `classes` | Approved task class names |
| `sources`, `exclusions` | Checkout-relative files or directory prefixes; `.` means the checkout; exclusions win |
| `destinations` | Exact objects with `service` (`openai-api` or `openrouter`), `mode` (`proposal` or `worker`), `model`, `provider`, `effort` (or null) |
| `automatic` | Explicit automatic-selection decision |
| `funding_ref` | Existing accepted funding/spending decision, including an explicit no-ceiling choice |
| `valid_until` | Approved Unix expiry, or null for no additional permission expiry; route validity still applies |

Checkout identity and revision come from existing settings. OpenAI's destination
provider is `openai`; OpenRouter uses the exact downstream slug. Permission never
transfers between services/endpoints or proposal and tool modes. Never ask for
approval before live use on an active route; do not invent a dollar cap or fixed
expiry.

## Construct and recheck the packet

Native preparation selects minimum necessary context and inspects it for secrets
and exclusions. `runtime.external_delegation.prepare(checkout, permission, parts)`,
given the assignment's effective permission, returns `text`, `snapshots` and
`binding`. Source parts use
`{"kind":"source","path":"src/example.py","start":1,"end":20}`; line bounds
are optional, inclusive and one-based. The helper reads permitted canonical files,
rejects links/escapes and builds the transmitted snippets from those exact bytes.

Free-text parts use `{"kind":"text","text":"...","sources":[]}` only for
truthfully source-free prose. For derived summaries, diagnostics, objectives or
tool results, list their actual source paths in `sources`. Preparation records
their digests and inherits every source restriction. Unknown provenance must stay
native. Native authors remain responsible
for truthful provenance and content inspection: hashes do not prove permission,
absence of secrets or arbitrary prose origins. There is no automatic taint scanner.

Add `automatic` and `local_preferred` booleans to `binding` and pass it as the
task or proposal's `external` field. OpenRouter proposals and workers always
require it; an API Usage task may omit it only for a self-contained packet with no
snapshots and no saved restriction. `local_preferred` stays true
through local wait/failure recovery unless a later explicit choice supersedes it.
OpenRouter also supplies `class_name`, observed native `coordinator` and distinct
`reviewer`, and `integration_available:true`; these are native attestations, not
external self-reports. OpenAI uses its existing trusted reviewer receipts.

The chosen adapter checks permission digest, mode/destination, source and packet
at dispatch. Ordinary in-scope edits need fresh preparation, not renewed
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
connector calls. To stop automatic consideration, save a permission with
`automatic: false` or disable the route; history, charges and outstanding
reconciliation remain.
