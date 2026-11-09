# Manage API Usage

Manual and Smart Balance share one runner and budget. Saving settings leaves
routing Off. A Keychain connection and explicit activation make either mode
available for the first eligible task. Other Codex work can continue. The public
CLI records and shows consent. Only the trusted Codex host can send requests
after live admission.

Warn at enablement/status that the provider may consume other credits or trigger
auto-reload despite local controls. Routine decisions use a compact warning code.
Reserve the next request's full maximum against the selected grant and limits;
stop earlier when coverage or expiry is doubtful and hand remaining work to native
when permitted. Never initiate a purchase or silently switch to a paid API pool.

## Set up and select a mode

1. Read `status` in the selected enrolled Codex checkout. Show the configured
   mode and whether live consent is saved.
2. For an unconfigured checkout, inspect `reusable_setups` and `reuse_reason`.
   Reuse an applicable existing connection, profile and funding policy without
   repeating account setup or asking the user to perform an internal binding step.
   For an explicit mode, use `reuse_reason_by_mode[mode]`: otherwise identical
   Manual/Smart source choices need no user selection. If effective setups differ,
   explain the organization, API project, profile limits/rates/validity, task class,
   budget and stored connection differences and obtain only the unresolved choice.
   Different connection fingerprints mean different stored Keychain connections;
   key names are unavailable. Use an existing connection receipt/identifier if
   needed to identify the intended one. Never ask for an unexplained opaque scope,
   guess a connection, or query Keychain automatically. An API provider project identifies
   requests at OpenAI; it is distinct from the user's local repository or checkout.
   For a new connection, use [Keychain setup](keychain-setup.md). Save needed
   settings changes with `configure`. Confirm only unresolved organization/key, profile, expiry,
   cycle, funding or spending choices. Task/data permission stays local to this
   checkout; another checkout's permission or qualification is never inherited.
3. On the user's explicit enable request, pass the current `revision` as
   `expected_revision` and select `mode: manual` or `mode: smart`. This
   records this checkout's live consent for the approved configuration. An
   unconfigured checkout uses revision `0`. Internally carry the selected status
   candidate's `scope` as `reuse_scope` and `setup_pin` as `reuse_pin`; choosing an
   arbitrary representative of equivalent effective setups requires no new user
   approval. Reuse needs this freshness pin even when selection is automatic.
   Use the existing
   approved regular-routing budget and expiry. Use `live_enabled` to verify
   saved live consent. If status reports `routing_budget_required`, follow
   [regular-routing budget setup](#regular-routing-budget).
4. In Manual, route only tasks explicitly selected by the user. In Smart
   Balance, use the standing consent for eligible tasks within the approved
   scope and budget. Load [delegation](delegation.md) for either mode. Previews,
   comparisons and canaries are optional.
5. Use `disable` on an explicit stop request. Recheck status and report Off.
   On a stale revision, show current status before applying a changed selection.

## Read-only status, quoting and selection

From the package root:

```sh
python3 -I -B scripts/usage_routing.py status --cwd /absolute/project/path
python3 -I -B scripts/usage_routing.py quote < synthetic-quote.json
python3 -I -B scripts/usage_routing.py evaluate --cwd /absolute/project/path < task.json
```

Status creates no state or credentials. It distinguishes enrollment and saved
live consent (`live_enabled`). `live_dispatch:false` describes the public CLI
boundary. If the source package is unavailable or untrusted, resolve that
through the accepted workflow and preserve enrollment. Evaluate is an optional
read-only preview. `eligible` never authorizes a send; status `reason` says why routing is not
active (disabled, not configured, trusted host required, live adapter unavailable). A paused/unknown decision is not a
reason to start another provider after an API attempt.

For an unconfigured enrolled checkout with an existing ledger, status adds
`reusable_setups`: a list of objects with `scope` (opaque local source checkout
identifier), `organization`, `api_project`, `org_revision`, `model`, `reasoning`,
`class_name`, `requested_mode`, the existing `policy`, complete safe `profile`
(the fields below, including rates, limits and validity), `production_budget`
(regular-budget conversion state), `setup_pin`, and `connection`. Connection is
`{storage_kind: "macos_keychain", fingerprint: <SHA-256>, key_name: null}`. Its
stable nonsecret fingerprint distinguishes stored connections; no key name is
retained. These comparison details explain differences; a digest alone is not a
meaningful user choice. No credential reference or secret is exposed.
Candidates require valid Keychain binding
metadata, a current organization revision, current policy/profile windows and
a regular-routing budget. Active test ownership is excluded. A source checkout
may be Off or disabled; that local choice is never copied.

`reuse_reason` is `reuse_available` for one distinct setup (including equivalent
candidates), `reuse_selection_required` for different setups,
`reusable_setup_unavailable` when none is compatible, `already_configured` for a
configured target, or `not_configured` when no existing state is available.
`reuse_reason_by_mode` maps `manual` and `smart` to those outcomes after applying
that target mode. Source identity/revision and pins prove freshness separately;
they do not make equivalent copied settings ambiguous.
Enrollment/recovery errors retain `reason: configuration_or_scope_unavailable`;
resolve that error before attempting activation. Existing canonical pins must
all agree with the current ledger, and every saved source routing record must
have its matching pin. Conflicting, corrupt or missing source evidence requires
manual recovery, including before a new `configure`, without rewriting pins or
creating another allowance. Status does not bind or enable.

Quote input has exactly `version:1`, `packet`, `profile`, `now`. Packet fields:
`version`, `model`, `reasoning`, `text`, `input_tokens`, `max_output_tokens`.
Profile fields: `model`, `reasoning`, `tier` (`default`), `input_rate`,
`cached_input_rate`, `cache_write_rate`, `output_rate`, `max_input_tokens`,
`max_output_tokens`, `verified_at`, `valid_until`, `count_maximum`. Rates are integer
USD micro-units per million tokens; times are integer UTC epoch seconds.
The quote uses the highest input rate and total output including reasoning.
Counting has its own maximum charge. A digest binds inputs without authenticating
counts/prices or granting spending permission. Use synthetic inputs for tests.

For reusable coding-source or automatic-selection permission, use the [shared permission contract](../../../clients/external-delegation.md#reusable-permission); preserve the existing budget and funding choices.

## Explicit local configuration

`configure --cwd ...` accepts bounded stdin JSON with exactly:

- `version` (1 or 2), `expected_revision`, `expected_org_revision`;
- `binding`: `organization`, `project`, `credential_ref`. Version 1 accepts an
  environment-variable name that can never authorize a request. Version 2 also requires
  `storage_kind: macos_keychain` and a reference of the form `vcw_` followed by
  32 lowercase hexadecimal digits. Configuration and status use metadata only.
- `policy`: `cycle`, `start`, `end`, `expiry`, `stop_at`, `ceiling`, `amount`,
  `grant_amount`, `pilot_limit`, `task_limit`;
- `profile` as above; `class_name`; `mode` (`manual` or `smart`).
- Optional `external_permission` follows that shared contract; omission preserves it and null revokes it.
- Optional `production_budget: true` for an existing smoke-test configuration
  in the same organization and accounting cycle.

Express money amounts as exact integer USD micro-units. Ceiling is `none` (No ceiling),
`subscription_benefit` (Monthly subscription benefit only), or `custom` (Specify
it). Amount is positive for custom and null otherwise. Benefit-only requires
actual authorized grant coverage before a live request; a numerical setting is
not proof. Stop time precedes cycle end/expiry.

Configuration requires the supported enrolled Codex scope. It explicitly creates
owner-only feature state under `~/.local/state/vibe-workflow/usage/` and
pins the one canonical database identity in checkout binding files. It saves a
requested mode while leaving the feature OFF and acquires no account connection. A missing
or replaced pinned database requires recovery, not a new allowance. This feature
state is separate from optional managed task runtime activation.

`disable` takes `expected_revision`. `enable` takes `expected_revision` and an
optional `mode` (`manual` or `smart`), `reuse_scope` (a source `scope` string
from status) and `reuse_pin` (that candidate's `setup_pin` SHA-256 string).
Null is equivalent to omission. Omit `mode` to keep the saved choice. Disable
blocks new admissions. Explicit enable on a Keychain configuration sets
`live_consent` and returns the funding warning.
For an unconfigured target, `expected_revision: 0` creates its configuration and
records explicit consent atomically; the returned target revision is `1`.
`reuse_scope` selects an existing compatible setup, and omission automatically
selects one distinct setup. Equivalent candidates must match binding, profile,
class, effective target mode, organization revision and regular-budget conversion
state. An explicit mode replaces source modes before comparison; omitted mode
retains genuine saved-mode ambiguity. Source revisions/pins do not participate
in this equivalence comparison.
Initial reuse requires `reuse_pin` even with a unique candidate or explicit scope.
The pin binds the selected source scope, revision, enabled/disabled/live-consent
state, binding, complete profile, class, saved mode, conversion state and current
organization policy/revision. It is rechecked inside the existing transaction
before binding or target consent. Unrelated checkout changes do not change this
selected source pin. This is status-to-enable bookkeeping, never a separate
mechanical approval prompt. Existing configured-target enable inputs need no pin.
The target receives fresh consent, no external/source permission or qualification,
and retains the existing ledger identity, funding policy, charges and reservations.
No credential access, smoke test or new cap is required for reuse. Shared policies
that validate unchanged keep their revision; real changes invalidate stale consent.

Ambiguity fails with `reuse_selection_required`; read status and select
`reuse_scope` explicitly. An unavailable, stale or expired selection fails with
`reusable_setup_unavailable`; refresh or configure the affected setup.
An omitted initial pin returns `reuse_pin_required`; a mismatched pin returns
`reusable_setup_changed`. Refresh status and reconsider only the changed setup
before retrying; never activate the updated settings using an earlier selection.
Missing
state returns `not_configured`; a lost or replaced pinned ledger requires recovery.
Failed reuse leaves target consent and the ledger unchanged. A configured or
disabled target keeps its own settings and revision checks; reuse fields cannot
replace them (`already_configured`). Disable never silently reattaches a setup.
The public `run` path remains unavailable.
Live evidence belongs to the trusted host. `reconcile` takes `{}` and reports
`manual_recovery_required`, not guessed settlement or cleared deductions.

### Regular-routing budget

Manual and Smart Balance share the monthly ceiling and funding choice. Set
`pilot_limit` to a positive amount for a cumulative routing cap or JSON
`null` to omit it. Set `task_limit` the same way for a cap on one task.
These choices are independent of each other and of the monthly ceiling.
No monthly ceiling leaves any configured cumulative or per-task caps in effect.
Changing these caps preserves the selected funding policy, grant, cycle,
retained charges and outstanding reservations. With Monthly subscription
benefit only, live requests still require verified coverage from that benefit.
Quote and reserve the maximum cost of every request against approved credits,
the applicable caps and any explicit request bound before sending.

An optional smoke test uses its own budget, token limits and expiry. If its
accounting cycle is still active, obtain the user's regular-routing budget
choice and call `configure` with `production_budget: true`. Supply the approved
policy, including `null` for any omitted extra cap, current price profile, and
stop time. Keep the cycle ID and billing boundaries so costs and reservations
still count. Then use the requested mode's normal enable procedure.

## Optional preview and evaluation

The coordinator's local `Controller.observe` retains a conservative balance
after checking organization/project, USD, selected pool, expiry and source/age.
For a comparative evaluation, `record_qualification` accepts `class_name`,
`profile_digest`, `samples`, `quality_passed`, `net_native_saved`,
`uncertainty`, `unit`, `observed_at`, and `valid_until`. Report attributable
measurements in the stated unit and mark unrelated activity that prevents attribution. Neither preview records nor their inputs
authorize transmission.

A preview task contains exactly `task_id`, `run_id`, `class_name`, `packet`,
`provider` (`auto`, `openai-api`, or explicit other provider),
`local_preferred`, `api_only`, `reviewer_available`, `native`, and
`snapshots`. Native task identity contains `account`, `task`, and `windows: []`. Snapshots contain `path` and `sha256` for unchanged files within
the selected checkout. Other-provider/local choices retain priority.

## Optional local checks

Run these from a checkout of this repository; installed packages may omit `tests/`.

```sh
python3 -B -m unittest tests.test_usage_budget tests.test_usage_routing tests.test_usage_settings tests.test_smart_balance tests.test_openai_api tests.test_usage_admission tests.test_usage_live
```

These checks use disposable SQLite and fake connections. Use the
[Codex host](codex-host.md) for live admission.

## Native handoff

If evaluate returns `handoff_recommended:true`, use `handoff --cwd ...` with
`{"task": <the bounded task>, "reason": <the decision reason>}`. The result includes
one stable handoff ID, state, native brief, pending API accounting and mandatory
review flags. Refresh an unclaimed handoff when task choices or reviewer readiness change;
unclaimed handoffs respect the latest API-only/provider/reviewer choices. No source
text is persisted in the handoff ledger.

Before existing native agent tools, call `claim_handoff --cwd ...` with
`{"task_id": "...", "attempt_id": "<native dispatch attempt marker>"}`.
Only `new_claim:true` permits that dispatch attempt under existing task authority.
A replay returns `new_claim:false`; observe/reconcile the existing native attempt,
never create another child. The claim is not proof of agent execution or review.
Explicit API-only/provider choices require resolution; a missing reviewer
waits rather than switching to another paid API. The existing native workflow owns
actual child identity, permissions, completion and fresh independent review.

Keep all pending API reservations and unresolved claims while native work proceeds.
Independent tasks that fit the shared headroom and the concurrency caps (default 4 total,
2 per account) may run in parallel; one unresolved task holds only its own exposure.
Tool-free API output cannot modify the source; the native agent must reread source
and avoid applying late output without new checks/review. Handoff never asserts
that the provider request stopped, settled, was free or consumed only the grant.
