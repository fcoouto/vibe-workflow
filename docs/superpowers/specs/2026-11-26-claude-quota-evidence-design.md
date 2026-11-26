# Claude quota evidence

Status: written design approved on 26 November 2026.
The user chose to defer automatic exact-route fallback. This is the exhaustion
step before [subscription usage balancing](2026-11-20-subscription-usage-balancing-design.md).

## Why

OpenCode 2.3.2 recorded two Claude reviewer failures as `provider.unknown`.
Their messages named a weekly limit and the same reset, but no scope. A bounded
`get_usage` read on the same login, before reset, confirmed an unscoped
`weekly_all` meter at 100% and a `weekly_scoped` meter at 71%. Both meters
matched the error's reset to the second. Claude Code 2.1.323 returned
`five_hour: null`; the current reader rejected the entire snapshot as
`claude_window_missing`. A missing meter does not prove that a cap was retired,
nor should it hide an exhausted meter. Evidence: the local session and usage
check, not a replay or a model call.

## Goal and boundaries

Stop recommending a Claude subscription route that has confirmed exhausted
quota. Distinguish the account-wide cap from a model-scoped cap. Do not infer
which cap triggered a failure when both could have. The unit is the
subscription account or the model scope on that account, not the model's lab.

Do not change pre-limit ranking, task levels, effort, reviewer independence,
other subscriptions, or paid overflow. Do not add automatic exact-route
fallback. The router never launches or retries a model, changes a login,
reserves capacity, or authorizes spending. No polling, synthetic prompts,
direct subscription HTTP calls, or extra reads on the normal selection path.

## Approaches

- **Parser only:** accept independent positive evidence from the current
  reader. Smallest change, but routine account acquisition does not ask a
  current login for a new quota reading after a failure.
- **Parser plus one read on failure (accepted):** reuse the existing native
  usage exchange. Use its scope and reset rather than guessing from the
  error. This can work without an OpenCode release.
- **Typed OpenCode limit event:** preserve Claude's structured limit data
  before OpenCode flattens the native error. Prefer a supported structured
  signal if one becomes available and identifies scope. Do not depend on an
  upstream change or parse SDK internals for this release.

## What counts as evidence

Use the fields and shapes the native reader recognizes, not the wording of
the limit notice:

| Evidence in a confirmed response | Effect |
| --- | --- |
| Valid account-wide `5h` or `7d` meter at 100% with a future reset; includes an unscoped `weekly_all` row | Exclude that Claude subscription path. |
| Valid `weekly_scoped` meter at 100% with a future reset, scoped to a model or family resolved unambiguously through the current catalog | Exclude only routes covered by that scope on the same Claude subscription. |
| Missing, `null`, idle without a reset, malformed, expired, conflicting, or unrecognized meter | Unknown for that meter. Do not turn it into extra headroom or a limit on unrelated routes. |
| Confirmed inventory with no scoped limits in either `limits` or `model_scoped` | Clear earlier scoped evidence for that account. |
| Unconfirmed or incomplete scope inventory | Do not use absence to clear earlier scoped evidence. |

First prove the native subscriber identity, the current account binding, the
zero-inference exchange, and its provider-confirmed usage response. A
header-seeded or otherwise unconfirmed response cannot establish meter evidence
or prove an empty list. In the known response format, the raw `limits` array
is part of that proof. A missing normalized `model_scoped` list can be valid
if the raw list establishes the inventory. If either source has unrecognized
or malformed scope entries, it cannot establish that a scope disappeared.

Validate scopes, utilization and reset per meter after that check. An unknown
new meter must not poison the known positive evidence. Do not guess the
meaning of a new scope, widen an exact model scope into a family, or match on
the lab. Duplicates representing the same meter must agree within the
reader's known rounding and reset precision, and must agree on whether the
meter is exhausted. A raw 99.4% and normalized 100% conflict for this
purpose; only that meter is unknown. A shared reset is not proof that two
meters have the same scope. Compare reset hints at whole-second precision.

Keep positive exhaustion evidence separate from a complete usage snapshot.
The existing snapshot completeness and freshness rules must not become a
claim of headroom for balancing. The native client can cache usage for
60 seconds. Order observations conservatively: the earliest possible
observation is request start minus the cache ceiling, and the latest is
response receipt. Clear a pause early only when the new observation is
provably newer and proves the same meter is no longer exhausted, or
confirms that its scope is gone. A failed or overlapping read, a missing or
invalid reading for that meter, or an unconfirmed scope inventory cannot
clear it. The reset always ends the pause; unknown meters neither extend it
nor replace the previous reset.

## On a Claude limit failure

1. Use OpenCode's `session.step.failed`. Attribute it to the model of the
   failed assistant step, never the parent session's current model. The
   `session.step.started` event carries that step's model and assistant
   message ID. An implementation may correlate these in a bounded map or
   use an equally precise host lookup. Do nothing if attribution is missing,
   ambiguous, or not on the `claude-agent` subscription path. The handler
   also requires Smart Router to be on for that step's location.
2. For the observed `provider.unknown` form, recognize a narrowly matched
   Claude subscription-limit notice with a valid, bounded future reset.
   A generic 429, auth error, transient capacity error, or reset timestamp
   alone is not enough. Treat human-readable text as a trigger and reset
   hint, never a scope assertion.
   Sanitized fixture, with an invented date and timezone:
   `You've hit your weekly limit · resets 10am (UTC) Resets at 2030-01-02T10:00:00.000Z.`
   Use the appended ISO UTC timestamp, not the human-readable time.
3. Run the existing bounded native exchange as a read-only probe. Do not run
   the full account refresh: it makes the account temporarily unprovable and
   can make a pick wait on its lock. The probe must not hold a lock used by
   normal account acquisition while the child is running. Use a separate
   nonblocking probe guard and existing private-state helpers for short
   updates. Across plugin instances, allow at most one probe in flight and
   no more than one start per minute for the same account. Each probe checks
   all meters returned for the account, not just those of the failed route.
   Additional failures do not queue reads. A pick made while a read is
   pending can still recommend the route.
4. The read can prove that meters on the current login are exhausted even
   if those meters do not explain the triggering failure. If only model B
   has confirmed exhaustion and model A failed, apply B's evidence only
   to B. Do not attribute the failure to a meter based on its reset alone.
   If the read confirms no exhausted scope for A, do not mark A. OpenCode
   2.3.2 identifies the failed model but does not attest which native login
   ran that step. A cached generation match is not that proof: the CLI
   login can change outside OpenCode. A coordinator can still exclude the
   exact route from its next request.

## State and selection

- Keep only bounded, validated account-wide and catalog-scoped pause records
  in the existing private Smart Router state directory. Reuse
  the existing account key and generation as the binding, storage helpers,
  and account checks. No raw error messages, credentials, account labels,
  prompts, or transcripts are stored.
- Before using a mark, prove the current subscription account binding.
  Never carry marks across account switches or a broken continuity fence.
  If a probe proves a different login, discard its meters and invalidate
  the old binding only if it is still current. Let normal account acquisition
  qualify the new login.
  An in-flight read must not overwrite newer evidence, publish against a
  changed binding, or publish a result after unload cancels the exchange.
  Turning the project feature off stops new reads and picks, but does not
  erase verified account evidence shared by other projects. Expired, unsafe,
  unreadable, and malformed entries do not exclude a route.
- Use the existing quota eligibility check and `quota_exhausted` result.
  That keeps filtering before ranking for Smart Router and council seats.
  A model supplied explicitly outside either tool is still the caller's
  choice. Nothing automatically launches an alternative.

## Acceptance checks

- Reproduce the observed shape: `five_hour: null`, confirmed `weekly_all`
  at 100%, scoped meter at 71%, same reset. Exclude every route on that
  account, regardless of model lab. Leave routes on other plans available.
- With only a recognized scoped meter exhausted, exclude its matching
  routes and no other models, including when the plan meter is missing.
  Missing or malformed sibling meters must not erase positive evidence.
- A provably newer, confirmed empty scope inventory clears old scoped
  evidence. An incomplete or potentially older cached observation does not.
  Check reset expiry, confirmed recovery, ambiguous scopes, duplicates
  straddling 100%, account switches, and interrupted reads.
- Synthetic events cover wrong provider, wrong step, ambiguous model,
  generic 429, duplicate failures across plugin instances, two distinct
  failed routes during the same probe, and timeout. One probe must apply
  each confirmed scope, but must not exclude a failed route without positive
  meter evidence covering it. A slow probe must not delay a pick.
- Selection and council tests cover the common quota gate. Unchanged
  ranking, authorization, and the existing ChatGPT limit behavior need
  regression coverage. No injected note changes. A future real failure is
  optional evidence, not a test that deliberately consumes quota.
