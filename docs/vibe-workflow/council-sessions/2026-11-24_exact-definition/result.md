# Council result: should a mistake that could pass the stated checks make a task "prose"?

- Date: 2026-11-24
- Brief SHA-256: 7a5911b02552c6107182844549be8ac30312733ae380ef45267dd2381446430a

## Members

| Seat | Model ID | Name | Variant | Paid overflow | Lab | Score |
| --- | --- | --- | --- | --- | --- | --- |
| Fable | claude-agent/fable | Fable 5.2 | high | unknown | anthropic | 64.6 |
| Astra | openai/gpt-6.2-astra | GPT-6.2 Astra | high | unknown | openai | 62.1 |
| Opus | claude-agent/opus | Opus 5.5 | high | unknown | anthropic | 59.1 |

Scores: Artificial Analysis via OpenRouter

## Votes

| Item | Decision | Fable | Astra | Opus |
| --- | --- | --- | --- | --- |
| E1 | unanimous: DROP | DROP | DROP | DROP |
| E2 | split (1-1-1): the user decides | DEFER | CHANGE: "If no stated check tests a required behavior, it is prose." | DROP |
| E3 | majority: CHANGE, no text change now; evidence from level-3 diff-review findings (minority: DROP) | CHANGE | DROP | CHANGE |
| E4 | majority: DROP (minority: DEFER) | DROP | DROP | DEFER |
| E5 | majority: DROP (minority: DEFER) | DROP | DEFER | DROP |

## Tally

Three members, no failures. E3: Fable and Opus both change it to "no text change
now", differing on where the evidence comes from (Opus: cards filed after the diff
review; Fable: diff-review findings, since cards cannot see check-invisible
defects); the record keeps the shared part. Astra disclosed reading the memory
vault index before noticing the brief's exclusion; no topic files were opened.
Fable flagged that its stance agrees with its own earlier vote on the exact-plan
exception. The coordinator did not vote.

## Compounding insight

A weak check is a defect of the plan, not of the builder: the level-1 and level-3
builders failed the same third of hidden tests. Strengthen the plan's checks
(known-answer or reference-value tests) instead of paying for a stronger builder;
with "Unsure: say prose", any broader spec rule would cancel the exact-plan
exception.

## Keep

The current `exact` definition and "Unsure whether it is exact: say prose".

## Defer

E2 (a narrower spec sentence), until level-3 diff reviews of level-1 builds report
defects that passed the stated checks.

## Questions

1. E2: adopt Astra's reworded sentence or leave it out?
2. Add a request for known-answer tests to the plan-review note?

## User decision

24 November 2026. Leave E2 out for now. Add to the plan-review step of the
`writing-plans` skill-load note a request for known-answer or reference-value
tests on statistics, numerics or calibration code. No change to the `exact`
definition.
