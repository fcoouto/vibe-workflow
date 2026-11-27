# Council result: how Smart Router delivers its "consult smart_router_select" instruction

- Date: 2026-11-21
- Brief SHA-256: a129ba8dd79c24abd228f61793c8747d386b36ef4bff4c75bb93d9812f2e523c

## Members

| Seat | Model ID | Name | Variant | Paid overflow | Lab | Score |
| --- | --- | --- | --- | --- | --- | --- |
| Fable | claude-agent/claude-fable-5-2 | Fable 5.2 | high | blocked | anthropic | 64.6 |
| Astra | openai/gpt-6.2-astra | GPT-6.2 Astra | high | unknown | openai | 62.1 |
| Opus | claude-agent/opus | Opus 5.5 | high | blocked | anthropic | 59.1 |

Scores: Artificial Analysis via OpenRouter

## Votes

| Item | Decision | Fable | Astra | Opus |
| --- | --- | --- | --- | --- |
| D1 one mechanism (entry) for all | unanimous: DROP | DROP | DROP | DROP |
| D2 push only as a fallback | unanimous: DROP | DROP | DROP | DROP |
| D3 PR #364 as is (push + entry for all) | majority: CHANGE, entry only for `claude-agent` (minority: DROP) | CHANGE | DROP | CHANGE |
| D4 choose by provider | unanimous direction, majority variant: push on every step for all providers, put the entry only for `claude-agent` (coordinator's call on the variant) | CHANGE | KEEP | CHANGE |
| D5 move the PR fallback note too | unanimous: DROP | DROP | DROP | DROP |
| D6 report the ordering upstream | unanimous: KEEP | KEEP | KEEP | KEEP |
| D7 merge #364 now, simplify later | unanimous: CHANGE, revise #364 before merging | CHANGE | CHANGE | CHANGE |

## Tally

- D3 and D4 describe one design, and all three members land on it: the push stays
  for providers that read the system prompt, and the entry is used only for
  `claude-agent`. Astra votes DROP on D3 and KEEP on D4 as written (push only for the
  others); Fable and Opus change both to "push for everyone, entry gated to
  `claude-agent`". The pushed text is lost on `claude-agent` anyway, so the two agree
  on what each model receives.
- D4 variant, the coordinator's own call: the simplest variant is Fable's. Keep the
  push unconditional and gate only the put on the provider; put when the provider is
  unknown; never remove on a model switch. Opus would remove the entry when a session
  leaves `claude-agent`; Astra asks for a test of whether that removal's "Disregard it"
  conflicts with the push. Not removing on a switch avoids that conflict.
- Fable and Opus both flag one unverified fact: that the `context` event carries the
  provider at `event.model.providerID`.
- D6: Opus notes that filing the issue is outward-facing and needs the user's approval.
- No member failed. The coordinator did not vote.

## Compounding insight

No single mechanism serves all three plans today: the system-prompt push never reaches
`claude-agent`, and the entry lands in a tool result on `opencode-go`. So the user's
"injection as fallback" idea inverts: the push is the primary, stateless channel, and
the entry is a `claude-agent`-only supplement. Gating the entry also serves the
lightweight rule, since `openai` and `opencode-go` sessions make no `put` calls. No
option reaches a `claude-agent` session's first model call; only an upstream change
(D6) can, and it would let the entry code be deleted.

## Keep

- D6: report the hook ordering upstream, after the user approves.

## Defer

- None.

## Questions

1. Apply the D4 gate (push for all, entry only for `claude-agent`, no removal on a model
   switch) to PR #364 before merging, after one live check of the provider field?
2. File the upstream OpenCode issue about the hook ordering?

## User decision

2026-11-21: (1) yes, apply the D4 gate to PR #364 before merging, after a live check
of how the hook learns the provider; (2) no, do not file the upstream issue.
