# Council result: the most efficient cost-versus-quality balance for Smart Router levels and stakes-sized reviews

- Date: 2026-11-23
- Brief SHA-256: 00e94efeee57cea6948550bd51878ec64c812689ce83251b90e391e503f130c0
- Weighting set by the user: quality 60, cost 40.

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
| R1 | unanimous: CHANGE; all three compatible fixes applied (coordinator's call) | CHANGE: narrow cross-system | CHANGE: sharpen exact | CHANGE: critical includes indirect paths |
| R2 | majority: KEEP (minority: reword as a plausible path to serious harm) | KEEP | CHANGE | KEEP |
| R3 | unanimous: CHANGE; design and plan reviews at level 3, diff reviews keep the reviewed work's facts | CHANGE | CHANGE | CHANGE |
| R4 | majority: KEEP (minority: review every design) | KEEP | CHANGE | KEEP |
| R5 | majority: KEEP the reversible skip, review at level 3 (minority: every plan) | KEEP | CHANGE | CHANGE |
| R6 | unanimous: CHANGE; top tier for design, plan and critical reviews, in both notes | CHANGE | CHANGE | CHANGE |
| R7 | majority: KEEP, level 1 (minority: level 2) | KEEP | CHANGE: level 2 | KEEP |
| R8 | majority: CHANGE; each critical task's diff review at level 3 | CHANGE | CHANGE (variant) | CHANGE |
| R9 | unanimous: KEEP | KEEP | KEEP | KEEP |
| R10 | split; the user decided | DEFER | CHANGE | KEEP |
| R11 | unanimous: KEEP | KEEP | KEEP | KEEP |
| R12 | unanimous: KEEP | KEEP | KEEP | KEEP |
| R13 | majority: CHANGE; Opus's variant (assert level-1 implementer and level-3 diff review in the trading case, no fourth run), coordinator's call as the simplest | CHANGE: fourth case | KEEP | CHANGE |

## Tally

Three members, no failures. Fable drafted the texts in R1 to R6; the brief said so.
R8: Astra's variant (level 3 earlier only when later tasks depend on a critical
decision) is the minority; Fable and Opus agree that each critical task's review
takes level 3 through the existing `max` rule. The coordinator did not vote.

## Compounding insight

Spend the flagship on judging, not on writing code: level-1 models implement exact
plans, and level-3 models review the design, the plan and every critical diff.
Reading a document costs far less than a fix loop. The router cannot enforce that
the reviews happen, so the live check must show it.

## Keep

R2, R4, R5 (skip when reversible), R7 (level 1), R9, R11, R12.

## Defer

The separate judging skill, until the live check shows misjudgment.

## Questions

R10: should cross-system work still force level 3 regardless of risk?

## User decision

23 November 2026. R10: keep it, with the narrower definition "changes in more than
one repository or deployed service that must stay compatible with each other". The
user also asked that model names stay out of rules, notes and decisions; they speak
in levels and facts. Designs updated:
`docs/superpowers/specs/2026-11-23-router-levels-design.md` and
`docs/superpowers/specs/2026-11-23-stakes-sized-reviews-design.md`.
