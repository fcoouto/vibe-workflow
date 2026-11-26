# Council result: Subscription Usage Balance

- Date: 2026-11-26
- Brief SHA-256: `95f3259db853e39f70455c0b5b8fcd498dd12c8ae5cfecac5e24f69a2d2b483b`

## Members

| Seat | Model ID | Name | Variant | Paid overflow | Lab | Score |
| --- | --- | --- | --- | --- | --- | --- |
| Fable | `claude-agent/fable` | Fable 5.2 | high | unknown | Anthropic | 64.8 |
| Astra | `openai/gpt-6.2-astra` | GPT-6.2 Astra | high | unknown | OpenAI | 62.2 |
| Opus | `claude-agent/opus` | Opus 5.5 | high | unknown | Anthropic | 59.1 |

Scores: Artificial Analysis via OpenRouter.

## Votes

| Item | Decision | Fable | Astra | Opus |
| --- | --- | --- | --- | --- |
| R1 — Expiry equation | Unanimous: replace hard pace-band priority with a bounded expiry signal. | CHANGE | CHANGE | CHANGE |
| R2 — Multiple windows | Unanimous: protect longer windows from short-window boosts. Majority: require fresh, healthy longer-window evidence to boost. | CHANGE | CHANGE | CHANGE |
| R3 — Rank guardrails | Unanimous: let quota change some picks, but bound how far it moves them. | CHANGE | CHANGE | CHANGE |
| R4 — Observation trust | Unanimous: require fresh evidence, avoid blocking reads, and keep unknown neutral. Majority: preserve unknown plans' baseline positions. | CHANGE | CHANGE | CHANGE |
| R5 — First release | Majority: keep all deferrals and add targeted known-answer tests. Astra voted KEEP on the brief's release boundary and added an account-switch case. | CHANGE | KEEP | CHANGE |

## Tally

The coordinator did not vote. These are simplest-variant recommendations where multiple members voted CHANGE with different implementations. They are not user approvals.

- **R1:** Use the fraction of quota left divided by the fraction of the window left. A fresh, unused window starts at 1 regardless of its length; only values above 1 can boost priority. Compute from the observation time, floor the denominator, cap the effect on ranking, and expire the sample at reset or the freshness limit. This selects Fable's dimensionless normalization and Opus's observation-time guard. Astra also favored normalization, by subtracting nominal points per hour; Opus favored raw points per hour. A local calculation confirmed the counterexample: at reset a 5-hour window has a raw rate of 20 points/hour, versus 5 for the expiring 168-hour reference window. Normalized values are 1 and 8.4 respectively. The other reference value is 1.4, so it wins after a fresh 168-hour reset reading at 1, all else equal.
- **R2:** Give a verified longer window under pressure a veto over a shorter window's promotion, not over route eligibility. Fable and Opus also give unknown longer windows a veto; Astra gates on known longer-window pressure. Resolve that difference before designing Go's monthly handling. Do not guess a monthly duration or add overlapping window quotas.
- **R3:** Start by bounding promotion against the existing winner's price proxy and feedback-adjusted score within the same eligible group. For reviews that means the already-qualifying independent group where available. This uses Astra's guardrail rather than a position limit, a price-only ratio, or a discounted price sort key. The members did not agree on numeric bounds. Preserve current admission, near-floor handling, strongest fallback, and reviewer independence.
- **R4:** Reorder known contenders only in their existing slots, leaving unmeasured routes in their baseline positions (Astra and Opus). Fable instead gave unknown plans a neutral multiplier. Reuse the existing reads and cache fresh validated values. A stale or missing hint does not block the current pick. Accept the Codex same-account assumption only through the existing browser OAuth eligibility check; Codex percentages never create or clear an exhaustion pause. No cache interval was agreed.
- **R5:** Add mixed-duration and aging-reading checks (Fable and Opus), plus Astra's account-switch and late-reply check. Keep the synthetic reference decision, fresh versus unread reset, conflicting windows, material price or score gap, independent reviewer seats, and shared cached reads under concurrent callers. The examples validate ordering, not throughput or account capacity.

## Compounding insight

Normalize the expiry signal to each window's own duration, then limit when and how far it can move a recommendation. Otherwise a freshly reset short window wins on units alone, or a stale sample gets more persuasive as its reset approaches. Cached ranking is advisory: several simultaneous picks can still favor the same plan.

## Keep

- Measured expiry-aware priority and no extra inference to obtain readings.
- Existing eligibility, quality, reviewer lab independence, and paid fallback rules.
- Browser OAuth guard, accepted Codex account assumption, and no user confirmation prompt.

## Defer

- Reservations, slots, synthetic usage debits, and task-size consumption estimates.
- Continuous weight tuning, provider-specific weights, and tracking of quota shared with other apps.
- Free-model or paid-route balancing.

## Questions

- If a longer window is unknown, should it block short-window boosts on that plan, or should only known longer-window pressure block them? The stricter choice may keep Go in its baseline position until the monthly period is verified; it does not exclude Go.

## User decision

26 November 2026: The user chose known-pressure protection and requested a draft, not implementation. An unknown longer-window duration alone does not veto a Go rolling boost when the percentage and reset still show enough room. A nearly exhausted longer meter or a missing usable longer meter withholds the short-window boost only; it does not change eligibility.

26 November 2026, after the design review: The user approved the proposed thresholds in the [design](../../../superpowers/specs/2026-11-26-subscription-usage-balance-design.md), then approved implementation under the [plan](../../../superpowers/plans/2026-11-26-subscription-usage-balance.md). Installation and activation remain out of scope.
