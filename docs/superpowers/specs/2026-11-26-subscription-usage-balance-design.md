# Subscription Usage Balance

**Status:** Known-pressure protection, starting thresholds, and [implementation plan](../plans/2026-11-26-subscription-usage-balance.md) approved, 26 November 2026. Implementation is authorized in the task worktree; activation is not.

**Basis:** [Council result](../../vibe-council/sessions/95f3259db853_subscription-usage-balance/result.md) and the Claude and Go exhaustion changes merged in [PR #406](https://github.com/fcoouto/vibe-workflow/pull/406). This draft supersedes the [earlier pace-band proposal](2026-11-20-subscription-usage-balancing-design.md) as the design under review, not as shipped behavior.

## Goal and boundaries

Use more of the included allowance that would otherwise expire. Avoid promoting a route when validated evidence shows little room; v1 does not demote the baseline winner solely for being ahead of pace. Compare each plan with its own usage windows. A percentage point is not the same amount of work across plans. The router need not estimate the usage of a task to compare those percentages.

Balance changes recommendation order only. Apply the existing billing and exhaustion gates, task quality rules, and qualified independent-reviewer preference first. A model served by Go uses Go allowance; its lab still determines review independence. Keep current effort, feedback, strongest fallback, council seating, paid-overflow reporting, and exhaustion behavior. Free and pay-per-use routes stay out. No slots, reservations, synthetic usage debits, automatic retries, or model calls to measure usage.

## Approaches

| Approach | Tradeoff |
| --- | --- |
| Bounded expiry priority with a longer-window guard | Rewards unused quota about to expire and stays comparable across window lengths. Requires a validated duration for a window to grant a boost. |
| Projected pace bands plus an expiry bonus | Preserves the original protection against running out early, but needs another rule for crossing bands. Hard band-first ordering picks the wrong plan in the reference example below. |

The draft takes the council's expiry-first direction and the user's known-pressure choice. Pace is a guard on short-window promotion, not a ranking band. No provider-specific weights are proposed.

## Usage evidence

Keep ranking hints separate from exhaustion records. A usable hint includes a validated usage fraction, window kind, reset, observation time, current connection binding, and verified duration when available. Keep the observation-time bounds the native reader can prove. Reject ambiguous, invalid, stale, expired, or out-of-binding hints. Do not reconstruct a live meter from a full snapshot that was unavailable, an old exhaustion flag, or the text of an error.

| Subscription | Source for ranking | Supported inputs |
| --- | --- | --- |
| Claude | Retain independently validated percentages from existing `get_usage` exchanges, including the already-bounded failure probe when it returns usable evidence. | [Anthropic documents](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work) a discrete 5-hour reset after first use and a discrete weekly reset. A missing meter or a missing reset before first use is not a fresh zero. Model-scoped meters never supply a boost. Preserve the native cache-age allowance. |
| OpenCode Go | Retain independently validated percentages from the existing selected-key Go usage read. | Expect rolling 5-hour, weekly, and monthly meters. [OpenCode's usage limits](https://opencode.ai/docs/go/#usage-limits) say the 5-hour window starts at first use and lasts five hours; the weekly limit resets Monday at 00:00 UTC, and the month resets with the paid period. The [usage response docs](https://opencode.ai/docs/go/#usage) identify the 5-hour meter as `rolling`. These are discrete resets, despite the field name. The month can protect shorter windows without a verified duration. |
| ChatGPT | Reuse the bounded Codex `account/rateLimits/read` exchange and its supported plan windows. | Require the existing OpenCode browser OAuth check (`chatgpt-token-sharing`), not an API key. Assume Codex and OpenCode use the same account, as the user requested; do not ask for confirmation or match percentages. Use Codex values only as ranking hints. They never start or clear a pause or prove available OpenCode allowance. |

Only award boosts from confirmed account-wide windows with discrete resets. A validated meter without discrete-reset evidence may still protect against low headroom. Treat a window with unknown meaning or scope as unknown. For Codex, use verified plan-wide meters and reported durations; do not infer either from the words “primary” or “secondary.” Limit v1 Codex boosts to verified weekly and 5-hour shapes. A 5-hour boost requires a usable weekly sibling; a missing sibling is not evidence of a single-window plan. Do not infer another required meter from an empty field. If evidence identifies an applicable longer window, apply the longer-window guard. A plan-wide Codex meter cannot see a tighter OpenCode-specific cap. OpenCode's recognized limit error stays authoritative.

### Freshness and acquisition

- Proposed maximum ranking age: **15 minutes**, or until the window resets, whichever comes first. Use the earliest plausible observation time for age and priority. Expire each hint at reset; do not infer zero usage before a fresh response.
- Reuse existing Claude and Go account acquisition on normal picks. The billing cache expires after 15 minutes; Go reads on a qualified account refresh and Claude reads when the existing native acquisition requires it, with a separate bounded limit probe. These paths can produce overlapping fresh readings, but actual overlap frequency is unmeasured. Keep baseline behavior when overlap is rare. Do not add a Claude or Go probe, or make a normal pick wait for newer ranking data.
- Add a bounded asynchronous Codex read only when a proved browser-based ChatGPT connection can compete with another subscription on the current pick and a cached reading is due. Use the existing native read and timeout. With no fresh data, the current pick uses baseline order for that route.
- Let the plugin start the bounded Codex read after the selector replies, following the existing background refresh pattern. Keep the minimal hint and refresh status in the private observation store. Use a dedicated, nonblocking process lock per store, held by the refresh child, so instances sharing that store do not duplicate a read. Recheck binding and due status under the lock. Record the start before reading; an interrupted read uses the failure cooldown, and the OS releases the lock when the process exits. A successful sample becomes due at the freshness limit or after a previously known window resets. Proposed minimum start spacing: **60 seconds**. After a failed read, wait **15 minutes** before another on-demand attempt. A partially missing window does not itself trigger a read on every pick. No scheduled timers, polls, or immediate retry.
- Bind a read at start and check that binding before accepting its result and using it to rank. Invalidate ranking hints on a changed OpenCode connection or generation. Do not persist credentials, login labels, raw provider responses, or task contents.

## Expiry equation

For a validated window with known length, at the observation time:

```text
remaining = 1 - used_fraction
time_fraction_left = min((reset_at - observed_at) / window_length, 1)
pace_ratio = remaining / time_fraction_left
expiry_score = remaining / max(time_fraction_left, 0.01)
```

All times in the ratio use the same units. Before clamping, require positive time left consistent with the window length, allowing only the reader's existing clock margin. A freshly reset and unused window scores 1 regardless of its duration. An expiry score above 1 can earn a boost; 1 or below gives no boost. The proposed **1% of window length** floor caps the score at 100 for every window length. A one-hour floor would cap a 5-hour score at 5 but a weekly score at 168. Score from the sample time, not the pick time: an aging percentage must not become more persuasive as its reset approaches.

### Known-pressure protection

Evaluate every potential boost, including a weekly boost, against the meters that constrain that route:

1. A **fresh applicable meter** with fewer than the proposed **10 percentage points** remaining blocks promotion, regardless of window length. Include an already validated Claude scoped meter only for a route that it applies to. Never let a scoped meter grant a boost. This threshold does not predict task fit or prove exhaustion.
2. A **fresh meter with a known duration and discrete reset** also blocks the boost if both its duration is strictly longer and its reset is later, and its unfloored `pace_ratio` is below 1. A matched Claude scoped weekly meter can therefore pace-veto a 5-hour boost, but can veto an account weekly boost only through the headroom rule. A meter with unknown duration or unverified reset behavior can protect headroom without a guessed pace.
3. A **missing or unusable expected longer meter** blocks that window's boost. Determine which meters are longer by the verified duration or the known meter kind, not by which fields happened to return. A known longer meter can still supply a boost if a shorter sibling is missing. This does not prove the shorter meter has room.

Compare overlapping windows within the same subscription; do not sum them. A route with at least one fresh, valid, duration-based expiry score is known. Use the maximum of 1 and the unsuppressed scores; the result is 1 if all scores are at or below 1 or their boosts are blocked. With no usable expiry score, the route is unknown, even if a protection-only meter was read. A guard never excludes a route or changes its paid-overflow policy.

## Ranking guardrails

Start from the current eligible, scored order. Within the same fully qualifying group, quota may move a candidate if it is close enough to the baseline winner. For reviewer picks, make that comparison **inside the qualifying independent group** when one exists; quota never moves a same-lab route ahead of it. Keep the current no-independent-reviewer fallback. Leave the strongest below-level fallback and near-floor exception unchanged; if the baseline winner is a near-floor pick, leave the order unchanged for v1.

Proposed calibration bounds:

- Candidate price proxy is no more than **1.5×** the baseline winner's price.
- Candidate feedback-adjusted score is at least **97%** of the baseline winner's score.

Use these as a promotion boundary, not as weights in a new weighted sum. Reorder only when the baseline winner has a usable score and a comparable candidate from another subscription beats it by at least a proposed **0.25** expiry-score points. Then reorder only the comparable, known candidates' slots, highest expiry score first and ties in baseline order. Unknown routes and all nonparticipants keep their baseline positions. Otherwise return the baseline order. The minimum gain keeps a score of 1.01 from buying the full permitted price or quality tradeoff against 1.00. A changed account binding cannot keep a boost or bypass the existing eligibility recheck.

The price is the current public price proxy, not an amount charged to the user for an included request. These bounds allow real changes beyond exact ties while protecting meaningful score and price differences. The weights and thresholds are review candidates, not calibrated production values or user-facing settings.

## Known-answer cases

The examples are synthetic. Keep identical billing, provider access, task requirements, and price/score guardrails unless those are specifically under test.

| Case | Expected behavior |
| --- | --- |
| ChatGPT weekly at 90% used, 2 hours left; Claude weekly at 40%, 72 hours left | For two 168-hour windows, scores are 8.4 and 1.4. Prefer ChatGPT if both are in the comparison. |
| Fresh ChatGPT weekly reset, 0% used, 168 hours left, with a current Claude reading still scoring 1.4 | ChatGPT scores 1.0. Prefer Claude. If ChatGPT has reset but has not been reread, preserve its unknown slot instead of imputing zero. |
| Fresh unused 5-hour window against that closing weekly window | The 5-hour window scores 1.0, not 20 raw percentage points per hour. It does not win solely because it is shorter. |
| Go at 60% rolling with 1 hour left, 20% weekly with 4 days left, 50% monthly with 10 days left and unknown duration | Go's documented discrete 5-hour reset lets its rolling score of 2.0 pass the known-pressure guard and beat Claude's 1.4. No month length is assumed. |
| Same Go case, but 98% monthly with 10 days left, or no usable monthly reading | Block both the rolling and weekly boosts. Go scores 1.0 and remains eligible. Against Claude's 1.4, Claude wins if both are comparable. |
| Go rolling is soon to reset, weekly 98% used with 4 days left | Weekly pace is below 1, so no rolling boost even if that rolling score is high. A genuinely healthy weekly sibling does not by itself veto. |
| Go weekly has a large expiry score, but its fresh rolling meter is at 98%; or a matching Claude scoped meter is at 98% | Do not grant the longer-window boost. Keep existing exhaustion and eligibility decisions. |
| Baseline winner scores 1.00; another comparable subscription scores 1.01 | Keep the baseline order. |
| A candidate has much better expiry priority but exceeds either proposed promotion bound, is below the existing quality floor, or is a same-lab reviewer when a qualified independent one exists | Do not promote it over the baseline winner. |
| Unknown candidate between two known candidates; delayed data for a replaced account; aging sample; simultaneous callers using one stale cache | Preserve the unknown slot; discard the late sample; never raise urgency by aging a sample; no duplicated read or extra allowance commitment. |

## Limits and review steps

One observation is not a throughput forecast. Concurrent callers can favor the same plan, and work can cross a reset. Usage outside OpenCode can change the next reading. Missing meters cannot establish a remaining task budget. If Codex and OpenCode are actually signed in to different accounts, the accepted same-account assumption can misorder picks, but cannot pause or authorize them. Do not add identity probing, prediction, completion tracking, configurable weights, or per-task debits to v1.

The approved thresholds are starting values, not live calibration. The implementation plan pins input, account-switch, cache, ordering, and stale-data coverage tests. Use fixture-based coverage first; no live reads to tune the design. No rollout toggle or shadow mode is proposed. Installation, activation, and any live evidence check outside the existing development exception require separate authorization.
