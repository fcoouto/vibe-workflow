# Council result: how Smart Router should decide which models may take which tasks

- Date: 2026-11-23
- Brief SHA-256: 63b5a22ecf4a4637c1b60e50df580817161317b89d4f4afc6d1b58084d1765d6

## Members

| Seat | Model ID | Name | Variant | Paid overflow | Lab | Score |
| --- | --- | --- | --- | --- | --- | --- |
| Astra | openai/gpt-6.2-astra | GPT-6.2 Astra | xhigh | unknown | — | — |
| Fable | claude-agent/fable | Fable 5.2 | xhigh | blocked | — | — |

## Votes

| Item | Decision | Astra | Fable |
| --- | --- | --- | --- |
| I1 | unanimous: DROP | DROP | DROP |
| I2 | unanimous: CHANGE, differing on how | CHANGE: fixed 25 points | CHANGE: 0.40 × top-5 catalog median, capped at the level-2 floor |
| I3 | unanimous: DROP | DROP | DROP |
| I4 | unanimous: DROP | DROP | DROP |
| I5 | unanimous: CHANGE | CHANGE: exact spec ignores `3+`; cross-system still raises | CHANGE: exact spec cancels only `3+`; cross-system still raises |
| I6 | unanimous in substance: KEEP | CHANGE: keep three levels and the max rule, with I5 | KEEP |
| I7 | unanimous: CHANGE, differing on how | CHANGE: level 1 random draw among plans, level 2 cheapest, level 3 strongest | CHANGE: each plan's cheapest qualifier, ranked by score |
| I8 | split | CHANGE: share level-1 picks now | KEEP: fallback now, sharing later |

## Tally

Two members, no failures. I2 and I7 were unanimous on changing but differed on
how; both were put to the user as questions rather than resolved by the
coordinator. I8 was a 1–1 split. Astra noted it had read the memory vault's index
file before seeing the brief's exclusion, and opened no vault topic files. The
coordinator did not vote.

## Compounding insight

Level 1 needs its own anchor from the public catalog, not the best model on the
user's plans; loosening the level-1 bar and the ordering rule must be decided
together, or the weakest admitted model wins on price.

## Keep

- Three levels and `level = max(base, facts)` (I6).
- An exact spec stops `files: 3+` from raising the level; `cross-system` still
  raises it (I5).

## Defer

- Usage balancing across plans (I8).

## Questions

1. Level-1 bar: fixed value, or moving with the catalog?
2. Ordering across plans: cheapest overall, per plan ranked by score, or a random
   draw?

## User decision

23 November 2026. The user replaced the proposed bars with their own rule:
level 3 is the top three labs' flagships in the public catalog; levels 2 and 1 are
80% and 55% of the weakest of those three (chosen after comparing 70/50, 75/50 and
80/55). The ordering stays cheapest list price first, unchanged. Higher-level
models remain eligible for lower-level tasks. Including Luna in level 1 is no
longer required. Design: `docs/superpowers/specs/2026-11-23-router-levels-design.md`.
