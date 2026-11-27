# Council result: how much should a coordinator be able to steer Smart Router?

- Date: 2026-11-24
- Brief SHA-256: 08d7b6126f459412fb0b1ca360c8fa78f37c9f1b7429d7505a6befcebab2bb12

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
| C1 | unanimous: CHANGE; majority: advertise only `excluded_models` | CHANGE: advertise `excluded_models` only | CHANGE: advertise none, keep for compatibility | CHANGE: advertise `excluded_models` and `allowed_efforts` |
| C2 | unanimous: KEEP (stop advertising `allowed_harnesses`) | KEEP | KEEP | KEEP |
| C3 | majority: CHANGE, hide `allowed_models` only (minority: hide both) | CHANGE | KEEP | CHANGE |
| C4 | unanimous: adopt `reviewing`; independence only among models that clear the level bar; normal pick with a notice when none | KEEP | CHANGE (lab from the catalog, notice) | KEEP |
| C5 | majority: DEFER (minority: accept at half weight) | DEFER | DEFER | CHANGE |
| C6 | unanimous: KEEP | KEEP | KEEP | KEEP |
| C7 | unanimous: CHANGE, split on how; simplest variant: reword the existing below-level notice (coordinator's call) | CHANGE: below-level only | CHANGE: when filters removed the last qualifying route | CHANGE: whenever the pick changed |
| C8 | unanimous: KEEP | KEEP | KEEP | KEEP |

## Tally

Three members, no failures. C7: all changed it but differed; the coordinator
picked the simplest variant and flags it as its own call. The coordinator did not
vote.

Further points raised: `allowed_efforts` can only remove models and never raise
effort (Fable, verified in tests); a forced below-level pick should not be
remembered, so its card cannot lower the model at its real level (Fable); a
constraint that leaves one model makes it "offered" and rateable (Fable); the
tool schema has `additionalProperties: false`, so an unlisted field is refused by
the tool, not hidden (Fable, Astra).

## Compounding insight

Being advisory, Smart Router cannot stop a coordinator from steering; it can only
keep steering visible. Narrowing ("not this model", "this is X's work") stays
inside the router, where the level bar, cards and future plan balancing still
apply; pinning one model or one plan belongs outside it.

## Keep

C2, C4, C6, C8.

## Defer

C5: cards for models outside the offered picks.

## Questions

What happens to the fields no longer advertised: removed from the tool (runtime
only), or accepted but unlisted?

## User decision

24 November 2026. A repository search found no use of `allowed_models` or
`allowed_efforts` outside tests, and one internal use of `allowed_harnesses`
(the injection check's re-pick on one plan, which can pass only that plan's
models instead). The user decided: delete `allowed_models`, `allowed_efforts`
and `allowed_harnesses` entirely, runtime included; keep `excluded_models`,
advertised, accepting a `model_ref` or catalog key and reporting entries that
matched nothing (C8); add `reviewing` (C4); reword the below-level notice (C7) and
do not remember below-level picks. Cards stay the only lasting channel (C6);
cards for overrides are deferred (C5).
