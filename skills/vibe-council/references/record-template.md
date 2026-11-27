# Record template

A run lives in `docs/vibe-workflow/council-sessions/<date>_<title>/`
(`<date>` is `YYYY-MM-DD`):

- `brief.md`: the exact bytes every member read.
- `brief-r2.md`: the round 2 prompt, when a round 2 ran.
- `answers/<seat>.md`: each member's raw answer (round 2: `<seat>-r2.md`).
- `result.md`: laid out as below.

## result.md

```markdown
# Council result: <topic>

- Date: <YYYY-MM-DD>
- Brief SHA-256: <full 64-digit hash>

## Members

Each member's seat, model ID, display name, variant and paid overflow, as the roster
tool reported them; lab and score only for a picked roster (`—` for named seats).

| Seat | Model ID | Name | Variant | Paid overflow | Lab | Score |
| --- | --- | --- | --- | --- | --- | --- |
| <seat> | <provider/id> | <display name> | <variant or none> | <blocked | enabled | unknown> | <lab or —> | <score or —> |

<For a picked roster: "Scores: Artificial Analysis via OpenRouter".>

## Votes

| Item | Decision | <Seat 1> | <Seat 2> | <Seat 3> |
| --- | --- | --- | --- | --- |
| <ID> | <unanimous | majority | split>: <result> | <KEEP | CHANGE | DROP | DEFER | no vote> | … | … |

## Tally

<Notes: minorities, the simplest-variant picks flagged as the coordinator's own call,
members that failed or did not vote. State that the coordinator did not vote.>

## Compounding insight
<The shared insight.>

## Keep
<List.>

## Defer
<List.>

## Questions
<The questions asked of the user.>

## User decision
<What the user decided, with the date.>
```

## Worked example (dry run)

Three invented members vote on four items. This shows how to tally by reading.

| Item | Astra | Opus | Fable |
| --- | --- | --- | --- |
| R1 | KEEP | KEEP | KEEP |
| R2 | KEEP | KEEP | DROP |
| R3 | KEEP | CHANGE | DEFER |
| R4 | CHANGE: cap at 10 | CHANGE: cap at 5 | no vote |

Tally:

- R1: unanimous KEEP.
- R2: majority KEEP, 2–1. Note Fable's DROP as the minority.
- R3: split 1–1–1. The user decides; offer Round 2 if the user wants it.
- R4: majority CHANGE, 2 of 2 votes, but they differ on how. Fable has no vote because
  its line is missing. Pick the simplest variant (cap at 5) and flag it as the
  coordinator's own call.

Result table:

| Item | Decision | Astra | Opus | Fable |
| --- | --- | --- | --- | --- |
| R1 | unanimous: KEEP | KEEP | KEEP | KEEP |
| R2 | majority: KEEP (minority: DROP) | KEEP | KEEP | DROP |
| R3 | split: ask the user | KEEP | CHANGE | DEFER |
| R4 | majority: CHANGE, cap at 5 (coordinator's call) | CHANGE: cap at 10 | CHANGE: cap at 5 | no vote |
