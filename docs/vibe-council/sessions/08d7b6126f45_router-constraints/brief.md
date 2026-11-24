# Council brief: how much should a coordinator be able to steer Smart Router?

## Role
You are one of 3 council members (Fable, Astra, Opus). Answer independently. You
cannot see the other members' answers.

## The user's standing instructions
Verbatim, from the current conversation:

- "hmm... But that can affect our Smart Router feature that balances usage throughout subscriptions, right?" (about `allowed_harnesses`)
- "hmm The same could apply to `excluded_models` field... When coordinator wants a specific model, it could exclude all the others, forcing a selection to pass. In that case, would the only way out from Coordinators is to provide a pick feedback, so next proposals would take that into consideration... Shall we escalate this situation to the @vibe-council ?"
- Earlier, standing: "Our goal here is still the same: efficiency. Doing more with less." Quality 60 / cost 40. "I don't want user's input here. It should be something between models." Smart Router must stay lightweight: no extra model calls, polling or state on the hot path. Rules and notes never name models.

## Bias
Weigh optimistic bias as well as pessimistic bias. Flag an item that flatters the
result as readily as one that is too cautious. The coordinator drafted the items;
it does not vote.

## Context
Smart Router is an advisory OpenCode plugin (repository
`/Users/fcouto/.local/share/opencode/worktree/13464e/router-constraints-council`, at
release 0.10.0). A coordinator agent calls `smart_router_select` with a work kind,
task facts and optional `constraints`; it gets one pick plus two alternatives, each
with a ready `model_ref`, and a `pick_id`. It never launches anything: the
coordinator dispatches the subagent itself and may use any model, with or without
the pick. After a subagent reports, the coordinator may file a 0-5 card with
`smart_router_feedback`; cards adjust that model's effective score per kind of work
and task size (bounded ±10% of the anchor, 3-session minimum, 30-day half-life).

Read: `index.js` (tool schema around line 100, the feedback tool around line 340),
`runtime/selector.py` (`_constrained`, `_select`), `runtime/routing.py`
(`check_constraints`), `docs/superpowers/specs/2026-11-24-router-feedback-design.md`,
`docs/superpowers/specs/2026-11-20-subscription-usage-balancing-design.md` (deferred).

## Settled rules (not up for a vote)
- Advisory only; the coordinator always decides what it dispatches.
- Feedback cards between models, no human input; the formula above.
- Level bars from the catalog anchor; cheapest qualifying model first.
- Usage balancing across plans is deferred but intended (reorder plans by usage pace).
You may comment on how these compound, not on whether to keep them.

## Measured facts
- `constraints` has four optional fields, each a list of strings:
  `allowed_models`, `excluded_models`, `allowed_efforts`, `allowed_harnesses`. They
  are hard filters (`runtime/selector.py:66-69`): a candidate is dropped when its
  route model name is in `excluded_models`, or its model, effort or harness is not
  in a given `allowed_*` list. Release 0.10.0 listed the four fields in the input
  schema for the first time; before that, `constraints` was an untyped object.
- Matching is on the route's model name only (`"fable"`, `"gpt-6.2-sol"`); a
  `model_ref`, catalog key or route name in `excluded_models` matches nothing and is
  silently ignored. `allowed_harnesses` values are internal strings such as
  `"opencode provider openai"`, visible on every pick as `route.harness`.
- A card is accepted only for the pick or one of its two listed alternatives;
  otherwise it is refused `not_offered` (`index.js:359`). A coordinator that
  overrides the pick with an unlisted model cannot file a card for it, so the
  router learns nothing from that override.
- Real use (one coordinator, Opus project, 14 tasks): it used `excluded_models` to
  force a reviewer from another lab, tried `allowed_harnesses: ["openai"]` and got a
  generic `no_eligible_route`, and overrode 10 of 14 builder picks by hand
  (dispatching models outside the pick), citing "subtle statistics" and an earlier
  model that stalled. Two coordinators independently asked for reviewer
  independence (a reviewer from a different lab than the builder or author).
- No coordinator has asked to pin one subscription plan.
- Usage balancing is not implemented; today only an exhausted plan is dropped.

## Items
- C1 — Keep the four `constraints` fields as hard filters, as in 0.10.0. Direction: neutral. If wrong: coordinators pin plans and models by habit, bypassing levels, cards and future balancing.
- C2 — Remove `allowed_harnesses` from the advertised schema (keep it accepted, undocumented). Direction: stricter. If wrong: a legitimate escape hatch becomes undiscoverable.
- C3 — Remove `allowed_models` and `excluded_models` from the advertised schema (keep them accepted, undocumented). Direction: stricter. If wrong: coordinators lose a cheap way to say "not this model" and override outside the router instead.
- C4 — Add a `reviewing: <model_ref>` fact for reviews: the router excludes that model's lab, falling back to the same lab with a notice when nothing else qualifies. Direction: neutral. If wrong: a new input agents misuse, or independence that costs quality.
- C5 — Accept a card for any model the coordinator actually dispatched, not only the pick and alternatives, so overrides teach the router. Direction: looser. If wrong: coordinators' own preferences (lab affinity) feed the scores directly.
- C6 — Cards are the only channel for a coordinator to change future picks; constraints only shape one request. Direction: neutral. If wrong: slow correction when a model fails repeatedly within a session.
- C7 — When constraints leave only models below the task's level, or remove every model of a lab, the answer carries a notice saying the constraint, not the router, made the choice. Direction: stricter. If wrong: notice noise.
- C8 — Report constraint entries that matched nothing (instead of ignoring them silently). Direction: neutral. If wrong: noise.

## Output format (strict)
Answer with at most 700 words, in this order:

1. One line per item, exactly:
   `<ID>: KEEP | CHANGE: <simplest change> | DROP | DEFER — <short reason>`
   For C1-C8, KEEP means adopt the item as written.
2. `Proposal`: up to 200 words: your simplest coherent stance on coordinator steering, consistent with balancing later and with learning from cards.
3. `Compounding`: 2 to 4 lines on how the items interact.
4. `Top 3`: the three items that matter most.

## Constraints
- Read-only. No edits, no Git writes, no subagents.
- Forbidden paths: `~/.ssh`, credentials, any `auth.json`, `.env` files,
  `~/.local/state/vibe-workflow`, `~/ai-memory-vault`, and
  `docs/vibe-council/sessions/` other than earlier sessions' `result.md` files.
- Use the network only if you need it.
- Use about 15 tool calls.
- Answer in your final message.
