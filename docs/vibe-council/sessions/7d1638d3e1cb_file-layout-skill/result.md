# Council result: a file-and-folder layout skill for vibe-workflow

- Date: 2026-11-21
- Brief SHA-256: 7d1638d3e1cb96f9b29fcb07a22b0ef4d5c12954d04fee35de24ecd3cf592fd2

## Members

| Seat | Model ID | Name | Variant | Paid overflow | Lab | Score |
| --- | --- | --- | --- | --- | --- | --- |
| Fable | claude-agent/fable | Fable 5.2 | high | blocked | anthropic | 64.6 |
| Astra | openai/gpt-6.2-astra | GPT-6.2 Astra | high | unknown | openai | 62.1 |
| Opus | claude-agent/opus | Opus 5.5 | high | blocked | anthropic | 59.1 |

Scores: Artificial Analysis via OpenRouter

## Votes

| Item | Decision | Fable | Astra | Opus |
| --- | --- | --- | --- | --- |
| I1 ship as skill | majority: KEEP (minority CHANGE: point to partners, compatible) | KEEP | KEEP | CHANGE |
| I2 two jobs | majority: CHANGE, placement is default; restructure hands moves to `safe-refactor` and adds a layout checklist (imports, manifests, package lists, fixtures) | CHANGE | CHANGE | KEEP |
| I3 domain-first | unanimous: CHANGE, framework and local precedent first; domain-first only as tiebreak; tests follow existing convention | CHANGE | CHANGE | CHANGE |
| I4 layout examples | unanimous: KEEP, at most four trees, include convention-owned folders | KEEP | KEEP | KEEP |
| I5 record layout | majority: CHANGE, record placement rules (about 10 lines, no tree) in the existing agent instructions file, with approval, updated with any move | KEEP | CHANGE | CHANGE |
| I6 split heuristics | unanimous: CHANGE, hints that trigger a proposal, never an action; convention-required one-file folders allowed | CHANGE | CHANGE | CHANGE |
| I7 no hook yet | majority: KEEP (minority Opus: add `writing-plans` note now) | KEEP | KEEP | CHANGE |
| I8 Composio drops | unanimous: KEEP, suspected dead files reported only | KEEP | KEEP | KEEP |
| I9 runtime vs scripts | majority: CHANGE, keep them separate; do not move dev tools | CHANGE | CHANGE | DEFER |
| I10 this repo's restructure | majority: CHANGE, separate PR after the skill, flat `src/`, `index.js` stays at root, runtime and tests in later phases (coordinator's call; minority Astra: pilot on the next new file only) | CHANGE | CHANGE | CHANGE |
| I11 Python nesting | unanimous: DEFER, real subpackages if grouping is chosen later | DEFER | DEFER | DEFER |
| I12 name | unanimous: KEEP `vibe-project-layout`; description names triggers | KEEP | KEEP | KEEP |

## Tally

- I1: Opus's change (point to `writing-plans` and `safe-refactor`) matches I2's majority, so it is folded in.
- I2: Opus voted KEEP but also asked to hand move mechanics to `safe-refactor`; all three agree on that.
- I5: Fable's KEEP also asked for rules, not a file listing; all three agree on that.
- I10: all three CHANGE, differing on how. Fable and Opus both chose a flat `src/` in a separate PR; the phasing and `index.js` at root come from Fable (`validate.py` pins `exports` to `./index.js`). Merging them is the coordinator's own call. Astra's minority: pilot with the next real file, no repo-wide move.
- The coordinator did not vote.

## Compounding insight

Fable and Opus both found that I3, I6 and I10 conflict as written: strict domain-first plus "no one-file folders" rules out `src/<domain>/` for seven one-module domains. Precedent first, then examples, then domain-first as a tiebreak, with numbers as hints, resolves it. I5 and I7 cover each other: the recorded layout loads every session, but only after the skill has run once.

## Keep

I1, I4, I8, I12; I7 (majority).

## Defer

I11 (runtime subpackages), until a restructure plan earns the `validate.py`/`manifest.py` change.

## Questions

1. I10: this repo's restructure, phased flat `src/` move in a separate PR, or Astra's pilot-only?
2. I7: add the `writing-plans` note now (Opus), or skill description plus recorded layout only (majority)?

## User decision

2026-11-22:

- `writing-plans` is not related enough to this skill. The skill does not reference it.
  I1 keeps the `safe-refactor` part of Opus's change only.
- The skill keeps `safe-refactor` for behavior-preserving moves (I2).
- Question 2 (I7): the user likes Opus's minority view, a plugin note when `writing-plans`
  loads. The exact note text awaits the user's review.
- Question 1 (I10): follow Fable and Astra; phased. Separate PR after the skill: six root
  modules to a flat `src/`, `index.js` stays at root; `runtime/` and `tests/` in later
  PRs. I9: `runtime/` and `scripts/` stay separate, dev tools stay. I11: deferred.
