# Council result: Smart Router as a required engine, and model picking in the injection check

- Date: 2026-11-22
- Brief SHA-256: 51429ec7c8245ae4abc064facb9b2784f4586b82d97e0614c2b066b2bfc30248

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
| D1 required, no opt-out | unanimous: DROP | DROP | DROP | DROP |
| D2 engine required for internals, router note opt-in | unanimous: KEEP | KEEP | KEEP | KEEP |
| D3 per-path picks in `--injection` | majority: CHANGE (minority: KEEP), keep the recorded models and ask the selector only when a recorded model is no longer listed (coordinator's call) | CHANGE: sticky picks, re-pick only when unlisted | KEEP | CHANGE: needs a token-free model list first; until then keep the recorded list and stop with "model unavailable" |
| D4 "could not select" is its own outcome | unanimous: KEEP | KEEP | KEEP | KEEP |
| D5 widen the pre-authorization | majority: CHANGE (minority: DEFER), bounded wording, the user's decision (coordinator's call on the variant) | CHANGE: one level-1 pick per path, three providers, four prompts; refuse other providers and hand-edited records | DEFER: ask the user once | CHANGE: level 1, low effort, subscription route with paid overflow blocked, one prompt per path; print picks first |

## Tally

- D1, D2, D4 unanimous.
- D3: Fable and Opus agree the check cannot reach the selector's model list today and must not re-pick on every run; they differ on whether picking waits for a model-list read (Opus) or happens only when a recorded model disappears (Fable). The coordinator picked Fable's sticky variant as the simpler one, because it needs a full model read only on the day a model is retired. Measured after the votes: `opencode models --standalone` lists provider/model IDs (67 on the three paths) with no model call, which answers "is the recorded model still listed?"; the selector's full metadata (family, variants, context, release) from outside the plugin is still unverified.
- D5: Fable and Opus give bounded wordings; Astra defers to the user. The coordinator picked Fable's count-based wording (one pick per path, three providers, four prompts) as the simplest; Opus adds effort and overflow conditions.
- No member failed. The coordinator did not vote.

## Compounding insight

D2 already delivers the "required engine" idea (the council and onboarding readiness use
the engine with Smart Router off), so D1's cost (a router note on every delegation in every
project, against the lightweight rule, and a broken product promise) buys nothing extra.
Picking the check's models with the selector couples the update check to a second moving
part; sticky picks keep that coupling to the day a recorded model is retired.

## Keep

- D2 (as today), D4.

## Defer

- None.

## Questions

1. Keep Smart Router's opt-out (D1 dropped), and treat the engine as required internally (D2)?
2. D3 + D5: keep the recorded models, check them with `opencode models` (no model call), and re-pick with the selector only when one is gone, with your pre-authorization widened to "one level-1 pick per subscription path, three providers, four prompts"?

## User decision

2026-11-22:
1. D1 dropped and D2 kept: Smart Router keeps its opt-out; the engine is required for plugin internals only.
2. D3 as the majority changed it: keep the recorded models and re-pick only when one is no longer listed
   (`opencode models`, no model call). D4 kept. D5 as the council recommended: the pre-authorization widens to
   one level-1 Smart Router pick per subscription path (three providers, four prompts), used only when a recorded
   model is gone. The coordinator's alternative (ask the user on retirement instead of widening) was declined.
