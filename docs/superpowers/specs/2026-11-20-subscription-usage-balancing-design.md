# Subscription usage balancing

Status: deferred, 20 November 2026. Current behavior checked against the
implementation and its tests on 25 November 2026. The proposal is not approved
for implementation; it still needs its own review and plan.

For the current proposal, see the [26 November draft](2026-11-26-subscription-usage-balance-design.md).
The audit and projected-pace proposal below are historical.

The separate [Claude quota evidence design](2026-11-26-claude-quota-evidence-design.md)
addresses exhausted routes first. It does not change the balancing proposal.

## Intent

Smart Router should spread work across the subscriptions a user pays for, so
no plan runs out early while another plan's quota resets unused. Today quota
can exclude an exhausted route when the router has qualifying evidence.
Usage below the limit never changes the order of recommendations.

## What ships today

- **Claude:** the native Claude Code reader uses a bounded, zero-inference
  `initialize` / `get_usage` exchange. It can store account-wide 5-hour and
  7-day meters and model-scoped weekly limits, tied to the current login.
  A confirmed account-wide meter at 100% can exclude the whole subscription
  path; a recognized model-scoped limit at 100% excludes only matching models on
  that path. Both need a future reset and evidence no more than 60 seconds old.
  Account-wide meter timestamps are already backdated 60 seconds for the native
  client's cache, so a new snapshot can be too old by the time selection checks
  it. Routine selection refreshes account metadata, not on-demand quota for a
  current login. Incomplete or unsupported account-wide readings do not prove
  spare capacity. The plugin does not turn a Claude step failure into a pause.
  See [`claude_usage.py`](../../../runtime/claude_usage.py),
  [`opencode_account.py`](../../../runtime/opencode_account.py) and
  [`test_readers.py`](../../../tests/runtime/test_readers.py).
- **OpenCode Go:** account acquisition reads the matching workspace's rolling,
  weekly and monthly meters. A meter marked limited or at 100% excludes the
  subscription path only with a future reset and a reading no more than
  60 seconds old. The stored percentages and reset times do not include window
  durations.
- **ChatGPT:** a specific OpenCode usage-limit error sets a shared pause. A
  one-off Codex read supplies a reset time, assuming both clients use the same
  account. If the read returns no usable reset, the pause lasts one hour.
  Normal picks do not read Codex, and no ChatGPT usage percentage enters
  ranking. See the dated [quota spike](../../chatgpt-quota-spike.md).

Subscription and model lab are separate: a model served by Go uses Go's quota,
not its maker's subscription. No live failure was replayed for this audit.

## What is balanced (the user's framing)

- **Subscription usage, not amount of work.** The quantity being balanced is
  each plan's own usage percentage, measured against its own windows. Ten
  percent of a ChatGPT Pro plan and ten percent of a Go plan are different
  amounts of work, and that does not matter: the goal is that every plan is
  used at a sustainable pace relative to its own reset. So percentages from
  different providers can be compared for this purpose, and no per-model
  "work" calibration is needed.
- **Many tasks per plan at once.** The router never answers "unavailable
  because a task is already using this subscription". There are no router
  slots, reservations or admission gates; balancing only changes the order of
  recommendations. Provider limits still apply.

## Prior art

The old design `2026-11-08-usage-routing-opencode-subscriptions-design.md`
(git history, sections 7.2 to 7.4) defined pace and projected utilization at
reset, subscription-versus-API shares and a within-class pressure rule
(`fundable_work / time_to_reset`), with buffered reservations for parallel
tasks. As far as found, it was never implemented. This design keeps its pace
and projection ideas, drops the API funding class (out of scope), replaces its
per-model consumption calibration with plain percentages (the user's framing),
and drops reservations (advisory router, unlimited concurrency per plan).

## Proposed inputs

Per plan `p` and each of its windows `w` that has a reset time:

- `used[w]`: fraction used, 0 to 1 (Claude 5-hour and 7-day windows; Go
  rolling, weekly and monthly meters; ChatGPT weekly and 5-hour windows if a
  trusted reading exists).
- `elapsed[w]`: fraction of the window already passed, from its length and
  reset time.

Reuse the existing readers where they provide current, account-bound data.
Using percentages to rank plans would require a separate freshness rule from
the exhaustion check. For pace projections, use only windows with a known
length and reset; do not infer Go's window lengths from meter names. A Claude
model-scoped limit is not an account-wide usage meter.

ChatGPT pressure stays unknown unless the design accepts a separate on-demand
Codex read and addresses the account and app-share limits below. Do not poll
or make model calls to measure usage.

## Proposed ranking rule

```text
projected[w] = used[w] / elapsed[w]       when elapsed[w] >= 0.10
             = used[w]                     otherwise (too early to extrapolate)
P[p]         = max over w of projected[w]  # every window must hold; the binding one decides

band(p)      = spare     if P[p] < 0.85
             = hot       if P[p] >= 1.00   # on pace to run out before reset
             = on_pace   otherwise
             = on_pace   if p has no fresh reading (never invent headroom or pressure)
```

Original ranking sketch, not the current ordering. Revisit it against today's
level and reviewer-independence rules before approval. Balancing is a
preference among eligible routes, not a reason to relax either rule:

```text
order = sorted(candidates, key=(band_rank(plan), price, -spare_rate(plan), -score, id))
band_rank: spare = 0, on_pace = 1, hot = 2
spare_rate(p) = min over w of (1 - used[w]) / max(hours_to_reset[w], 1)
```

`spare_rate` is quota left per hour until reset, so among plans in the same
band and price, the one whose unused quota is about to be lost wins.

Example from 20 November 2026, 21:26: ChatGPT Pro was 86% into a weekly
window with 57% of the week passed, so `P = 1.50` (hot). It would have run out
about 1.6 days before its reset. A level-2 task that ties GPT-6.2 Sol and
Sonnet 5.5 on price would go to Sonnet 5.5 if Claude was spare or on pace.

## Parallel requests without reservations

Several requests can reuse the same cached readings and pile onto the same
plan. Original proposal (not decided): count recommendations per plan in
the last 30 minutes and add a small assumed usage per recommendation
to `used[w]` before computing `P`. This only reorders; it never refuses, and
it expires on its own. The assumed size per recommendation is open: a
constant, or learned from how much each plan's percentage moves between
readings.

## Known limits

- **ChatGPT's OpenCode share.** A user can cap OpenCode's share of the ChatGPT
  plan (for example 50%). No API exposes that share, and plan-wide percentages
  then overstate OpenCode's room. The documented 429
  (`subscription_sharing_usage_limit_exceeded`) still stops routing. A later
  refinement could learn the share from the plan percentage at which 429s
  arrive.
- **Usage outside OpenCode** (Claude or ChatGPT used directly) moves the
  percentages too. That is correct for balancing usage, since the plan is
  shared, but it means usage cannot be attributed to one task.
- **No reading, no balancing.** Plans without a fresh reading sit in the
  neutral band.

## Non-goals

- Reservations, slots, admission or any refusal because a plan is busy.
- Balancing pay-per-use API credits.
- Learning per-model consumption.

## Open questions for the revisit

1. Verify what OpenCode exposes for Claude weekly-limit failures, including
   whether the signal distinguishes model-scoped from account-wide exhaustion
   and includes a reset. Treat this as a separate decision from balancing.
2. Decide which existing meters are usable for balancing, their freshness
   policy, and whether to add a Codex read for normal picks.
3. Whether to count recommendations at all, and the assumed usage per
   recommendation if so.
4. Whether `band` should be continuous (a multiplier on price) instead of
   three bands, once real data shows how often bands flip.
5. The thresholds 0.85 and 1.00, and the 10% minimum elapsed fraction.
