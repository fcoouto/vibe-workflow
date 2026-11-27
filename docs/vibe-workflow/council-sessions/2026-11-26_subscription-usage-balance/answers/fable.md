R1: CHANGE: rank on dimensionless surplus `S = (1 − used) / (time_left / duration)`, with `time_left` floored and S capped — raw points/hour is not comparable across window lengths. An on-pace 5-hour window scores 20 points/hour and beats A's 5.00; the reference only passes because both windows are 168 h. S gives A 8.4, B 1.4, fresh post-reset A 1.0. S > 1 is the same condition as old projection < 1, so pace and expiry become one quantity with no band/bonus calibration.

R2: CHANGE: use a gate, not raw min/max or a penalty: plan S is its soonest-closing known-duration window, counted only when every longer window on that plan reads fresh with S ≥ 1; otherwise the plan is neutral — C (short 16, weekly 0.035) becomes neutral, and a healthy weekly suppresses nothing. A missing longer meter means neutral. An unknown-duration meter never grants and cannot pass the gate, so Go stays neutral until its monthly period length is evidenced; that cost is real.

R3: CHANGE: change one sort key to `price / clamp(S, 1, cap)`; keep floor and near-floor membership on raw price, and give near-floor additions no discount — neutral or unknown is 1, so today's order is reproduced exactly. A route never passes one more than cap× cheaper. The cap is the single number to calibrate, and cap = 1 turns the feature off. Promotion only; demoting over-pace plans is deferred. The reviewer filter (`runtime/selector.py:328`) runs after ordering and is unchanged.

R4: CHANGE: no pick waits on a read: a stale or absent reading is neutral now and starts one single-flight refresh for later picks; a sample dies at min(hint TTL, earliest reset in the sample) — the Codex read has a 5 s timeout (`runtime/codex_reset.py:13`). Claude's 60 s backdated exhaustion freshness would make every ranking reading stale, so the hint TTL must be separate. Skip the Codex read unless the OAuth check passes and at least two plans have qualifying routes.

R5: CHANGE: add one known-answer test before choosing a design: a fresh 5-hour window against A's closing weekly — it is the only case that separates raw rate from normalized surplus. Every example in the brief uses equal durations, which flatters raw division. Keep all deferrals.

Compounding:
- R1's neutral point (S = 1) is what makes R4 unknowns and R3 baseline reproduction work with no special cases; raw rates have no neutral value.
- R2 needs durations, so the R4 cache must store duration per window, not only percent and reset. The existing Codex reader keeps only `usedPercent` and `resetsAt`.
- The R2 gate and R3 cap limit burst harm but do not prevent it: concurrent picks share one cached S and herd onto the same plan until the next reading. The R4 TTL sets that overshoot window.
- Optimistic bias: a capped S with 15 minutes left cannot actually be consumed. Work started then mostly lands after the reset and charges the longer window, so the R2 gate carries the protection.

Top 3: R1, R3, R2

The S figures are hand arithmetic from the brief's synthetic cases; nothing was run.
