# Subscription usage balancing

Status: deferred, 20 November 2026. Recorded so it can be revisited after
automatic selection (`2026-11-20-smart-router-auto-selection-design.md`) ships.
Not approved for implementation; it still needs its own review and plan.

## Intent

Smart Router should spread work across the subscriptions a user pays for, so
no plan runs out early while another plan's quota resets unused. Today the
router only stops recommending a plan at 100%; it never prefers one plan over
another because of how much of it is used.

## What is balanced (the user's framing)

- **Subscription usage, not amount of work.** The quantity being balanced is
  each plan's own usage percentage, measured against its own windows. Ten
  percent of a ChatGPT Pro plan and ten percent of a Go plan are different
  amounts of work, and that does not matter: the goal is that every plan is
  used at a sustainable pace relative to its own reset. So percentages from
  different providers can be compared for this purpose, and no per-model
  "work" calibration is needed.
- **Many tasks per plan at once.** A plan must accept any number of
  concurrent tasks. The router never answers "unavailable because a task is
  already using this subscription". There are no slots, reservations or
  admission gates; balancing only changes the order of recommendations.

## Prior art

The old design `2026-11-08-usage-routing-opencode-subscriptions-design.md`
(git history, sections 7.2 to 7.4) defined pace and projected utilization at
reset, subscription-versus-API shares and a within-class pressure rule
(`fundable_work / time_to_reset`), with buffered reservations for parallel
tasks. As far as found, it was never implemented. This design keeps its pace
and projection ideas, drops the API funding class (out of scope), replaces its
per-model consumption calibration with plain percentages (the user's framing),
and drops reservations (advisory router, unlimited concurrency per plan).

## Inputs

Per plan `p` and each of its windows `w` that has a reset time:

- `used[w]`: fraction used, 0 to 1 (Claude 5-hour and 7-day windows; Go
  rolling, weekly and monthly meters; ChatGPT weekly and 5-hour windows if a
  trusted reading exists).
- `elapsed[w]`: fraction of the window already passed, from its length and
  reset time.

Claude and Go readings already exist in the automatic-selection design's
readers. For ChatGPT, that design reads Codex only after a 429. Balancing would
need Codex read on demand like the others (zero tokens, one short process,
under the same account assumption), which is a decision for the revisit.
Start from the finding of the ChatGPT spike in the automatic-selection plan
(Task 8, `docs/chatgpt-quota-spike.md`): it looks for exactly this source.

## The rule

```text
projected[w] = used[w] / elapsed[w]       when elapsed[w] >= 0.10
             = used[w]                     otherwise (too early to extrapolate)
P[p]         = max over w of projected[w]  # every window must hold; the binding one decides

band(p)      = spare     if P[p] < 0.85
             = hot       if P[p] >= 1.00   # on pace to run out before reset
             = on_pace   otherwise
             = on_pace   if p has no fresh reading (never invent headroom or pressure)
```

Ranking change, applied only inside the candidates that the capability rule
already accepted (the `ok` and `near` sets; floors are never lowered):

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

Readings are refreshed at most every 15 minutes, so several requests in that
time would all see the same numbers and pile onto the same plan. Proposal (to
be decided when revisited): keep a short local count of recommendations per
plan in the last 30 minutes and add a small assumed usage per recommendation
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

1. The size of the assumed usage per recent recommendation.
2. Whether `band` should be continuous (a multiplier on price) instead of
   three bands, once real data shows how often bands flip.
3. The thresholds 0.85 and 1.00, and the 10% minimum elapsed fraction.
