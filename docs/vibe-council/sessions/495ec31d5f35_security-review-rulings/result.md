# Council result: rulings made while building the proportional security review

- Date: 2026-11-27
- Brief SHA-256: 495ec31d5f35c281dc3c7da007e31386eb2f1f71c89d6fb114c0ccd6456008c6

## Members

| Seat | Model ID | Name | Variant | Paid overflow | Lab | Score |
| --- | --- | --- | --- | --- | --- | --- |
| Fable | claude-agent/fable | Fable 5.2 | high | unknown | anthropic | 64.8 |
| Astra | openai/gpt-6.2-astra | GPT-6.2 Astra | high | unknown | openai | 62.2 |
| Opus | claude-agent/opus | Opus 5.5 | high | unknown | anthropic | 59.1 |

Scores: Artificial Analysis via OpenRouter

Independence: Fable implemented the fix waves and Astra ran the final review; Opus is the
coordinator's model. The coordinator wrote the rulings and did not vote.

## Votes

| Item | Decision | Fable | Astra | Opus |
| --- | --- | --- | --- | --- |
| R1 | unanimous: KEEP | KEEP | KEEP | KEEP |
| R2 | unanimous: KEEP | KEEP | KEEP | KEEP |
| R3 | majority: KEEP as a process lesson (minority: CHANGE, same lesson stated as a rule) | KEEP | CHANGE | KEEP |
| R4 | unanimous: KEEP | KEEP | KEEP | KEEP |
| R5 | majority: CHANGE, future task briefs state the `AGENTS.md` `--injection` exception explicitly (coordinator's call) | KEEP | CHANGE: reserve the run for Task 7 | CHANGE: state the exception in briefs |
| R6 | unanimous: KEEP | KEEP | KEEP | KEEP |
| R7 | unanimous: KEEP | KEEP | KEEP | KEEP |
| R8 | unanimous: KEEP | KEEP | KEEP | KEEP |
| R9 | majority: KEEP (minority: CHANGE, bound full-review raw reads; skip credential stores) | KEEP | CHANGE | KEEP |
| R10 | unanimous: KEEP | KEEP | KEEP | KEEP |
| R11 | unanimous: KEEP | KEEP | KEEP | KEEP |
| R12 | unanimous: KEEP | KEEP | KEEP | KEEP |
| R13 | unanimous: KEEP | KEEP | KEEP | KEEP |
| R14 | unanimous: KEEP | KEEP | KEEP | KEEP |
| R15 | unanimous: KEEP | KEEP | KEEP | KEEP |
| R16 | majority: KEEP (minority: CHANGE, do not claim the copy adds no reader) | KEEP | CHANGE | KEEP |
| R17 | split: ask the user | CHANGE: allow group-writable, refuse other-owner or world-writable | KEEP | DEFER |
| R18 | unanimous: KEEP | KEEP | KEEP | KEEP |
| R19 | unanimous: CHANGE, if resuming fails, dispatch a fresh reviewer for the remaining scope with the known alert; if that fails, record the review incomplete (coordinator's call) | CHANGE | CHANGE | CHANGE |
| R20 | majority: KEEP (minority: CHANGE, verify each instruction file's resolved path) | KEEP | CHANGE | KEEP |
| R21 | unanimous: KEEP | KEEP | KEEP | KEEP |
| R22 | unanimous: KEEP | KEEP | KEEP | KEEP |

## Tally

- 16 unanimous KEEP, 4 majority KEEP (R3, R9, R16, R20), 1 majority CHANGE (R5), 1 unanimous
  CHANGE (R19), 1 split (R17).
- R5: two CHANGE votes differ; the simplest variant (state the exception in future briefs, a
  process change with no code effect) is the coordinator's own call.
- R19: all three ask for a failure path; the variant combines Fable's and Opus's fallback with
  Astra's "never report an incomplete review as clean". The coordinator's own call.
- R17: Fable CHANGE, Astra KEEP, Opus DEFER. The user decides.
- The coordinator did not vote.

## Compounding insight

The post-fix path (storage through the script, stop-and-resume, partner-origin checks) is
tested in code but not observed live, and its safe fallbacks all lean toward "chat",
"unavailable" or `[BLOCKED]`. Two members ask for one live probe of the agent-driven path
before `[READY]`. Opus adds that the passing secret probe rated a committed key medium, which
flatters the check: a committed likely secret should rate high until rotated. Fable adds that
the tier-2 reviewer brief does not carry the secret rules, and that a skipped partner should be
named under limits.

## Keep

R1, R2, R3 (as a lesson), R4, R6-R16, R18, R20-R22.

## Defer

None by majority; Opus's R17 DEFER is part of the split.

## Questions

1. R17: allow group-writable parents (Linux umask `002`), keep refusing them, or defer?
2. Apply R19's fallback and the compounding fixes, and run one live probe of the agent-driven
   path before `[READY]`?

## User decision

2026-11-27:
- R17: allow group-writable parents; refuse only another owner or world-writable folders.
- Apply R19's failure path, the R17 change, a "committed likely secret rates high until
  rotated" rule, the secret rules in the tier-2 reviewer brief, and naming a skipped partner
  under limits; one scoped re-review; then one live probe of the agent-driven path (report
  through the script, reviewer stop and resume) before `[READY]`.
- R5: future task briefs state the `AGENTS.md` `--injection` exception (process only).

Outcome: fix wave 4 applied the decisions (`9720843..9088a40`); two follow-up storage fixes
(`00e919a`, `a9ffb15`) closed regressions from allowing group-writable parents; the final
re-review is clean. Live probes after the changes: storage through the script, secret handling,
severity, the router-picked reviewer and the rotation request passed; reviewer stop-and-resume
stayed unobserved. The user accepted the rotation request in the final reply.
