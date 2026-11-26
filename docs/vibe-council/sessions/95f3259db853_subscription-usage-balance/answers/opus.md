R1: CHANGE: rank by `rate = remaining_points / max(hours_to_reset_from_observation, 1)` and treat a reading as expired once its reset passes. Pace bands are dropped. — A scores 5.00 and B 0.83, so A wins; a fresh post-reset A scores 0.60, so B wins. The 1-hour floor bounds the near-zero blow-up. The rate needs only the remaining share and the reset time, not the window length. Bands need the length and then calibrating a bonus to cross them.

R2: CHANGE: a window's rate counts only if every longer window on that plan is known and not hot (`used / elapsed < 1`). Otherwise the plan's rate is its longest window's rate. — In case C, weekly pace is 0.98/0.43 = 2.3, which is hot, so C gets 0.021. With a healthy weekly window, the short window counts. If a longer window's length is unknown (Go monthly), it cannot unlock a shorter window's rate. This leans strict and will blunt Go rolling picks. The 1.0 cutoff is the old draft's "runs out before reset" line, not a new threshold.

R3: CHANGE: rate may reorder only qualifying candidates priced up to 2× the current head's price. That is the inverse of the existing 0.5 near-price cap. Everything else keeps today's key. Apply it to `ordered` before the other-lab reviewer filter (`runtime/selector.py:328`). Leave `strongest()` and the near-floor exception untouched. — Quota really moves picks, but only within a bounded price spread. Price is a ranking proxy, so moving within the band costs nothing extra. If A and B differ by more than 2×, the reference decision does not flip. That is the honest bound, not a defect.

R4: CHANGE: reorder only candidates with a known rate, and only among their own slots. A plan with an unknown rate keeps its current position. Serve picks from a per-account cache filled by the existing acquisition paths. The Codex read fills it on demand under the current subscription-OAuth check, one probe in flight, at most once per account per 60 s, and never blocks a pick. An API-key connection counts as unknown. — Keeping slots gives a deterministic order with no invented headroom or pressure. Ranking freshness stays separate from the 60-s pause evidence. A missing Claude window counts as unknown, not healthy.

R5: CHANGE: keep the deferrals and add one essential test: an aging reading must never raise urgency. — With `remaining / (reset − now)`, a stale sample gets more urgent as reset nears, which flatters the feature. Measure hours from observation time, or expire the hint faster than the 1-hour floor. Also test concurrent callers who see one cached reading: same order, no extra reads.

**Compounding**
- R2 decides which windows feed R1. Without the gate, R1's rate lets rolling windows drain weekly allowances.
- R3's price band limits how far any rate can move a pick. R1 therefore needs only an ordering, not calibrated magnitudes, so tiny-window dominance stays bounded.
- R4's keep-slot rule plus R5's no-aging guard ensure stale or missing data never wins. Strict choices in R2 and R4 stack, so check that Claude and Go still move some known-answer picks.
- Reviewer independence holds because R3 reorders before the lab filter. The reorder must not filter.

**Top 3**
1. R2: the gate prevents the worst failure, a weekly plan drained early.
2. R3: the price band decides whether the feature matters at all or displaces better picks.
3. R5: the aging-reading test closes the main optimistic-bias hole.
