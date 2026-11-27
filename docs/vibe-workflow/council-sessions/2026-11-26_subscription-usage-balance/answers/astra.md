R1: CHANGE: Rank windows by `max(0, remaining_points / max(hours_left, H) − 100 / duration_hours)` — subtracting each window’s uniform rate prevents a freshly reset five-hour meter from winning merely because it is short. With a proposed one-hour floor `H`, A scores 4.40 versus B’s 0.24; freshly reread A scores zero. The floor bounds urgency at reset. Pace bands plus a bonus need another rule to decide when expiry overrides a band.

R2: CHANGE: Use the largest applicable window score, but suppress promotion when a longer meter has seriously depleted headroom — test “remaining fraction below half the remaining time fraction” as a candidate guard, not an approved cutoff. C’s weekly 2% versus 57% time remaining triggers it; 60% versus 57% does not. Suppress the bonus, not eligibility. Never sum overlapping allowances. Match scopes to routes and charge Go-served models to Go. Without a verified duration, skip that meter’s pace calculation; retain confirmed pauses.

R3: CHANGE: Bound promotion against the baseline winner’s price **and** feedback-adjusted score, not its rank position — extra model aliases should not crowd out another plan. Test a shared maximum price premium and score loss against comparable and materially different pairs before choosing either bound. Public price is a proxy, not an included-use charge. Preserve baseline ties, near-floor placement, below-level fallback, and the qualified independent-reviewer pool and fallback.

R4: CHANGE: Keep unknown routes in their baseline positions; reorder known contenders only in their existing positions — this gives a deterministic order without assigning unknown capacity. Retain validated per-meter percentages during existing Claude and Go reads. Allow one bounded, on-demand Codex read per refresh interval, including after failure, only for competing `oauth`/`chatgpt-token-sharing` routes; never API keys. Accept the same-account assumption without prompting. Discard hints at the earliest of freshness expiry, reset, or binding change. Never let a hint clear OpenCode’s pause.

R5: KEEP — Test the reference pick, fresh versus unread reset, pressured versus healthy weekly sibling, and simultaneous callers sharing one cached reading. Essential extra case: switch accounts during a refresh; its late result must not influence the new login. Defer synthetic debits, consumption learning, task-size predictions, and provider weights.

Compounding  
Apply billing, exhaustion, quality, and reviewer rules before comparing quota; leave effort and council seats unchanged.  
Test both directions: expiry must overturn a modest baseline preference, but not a material price or quality gap.  
Concurrent picks may all recommend one plan. Ranking neither reserves its remaining allowance nor guarantees work finishes before reset.

Top 3: R1, R2, R4.
