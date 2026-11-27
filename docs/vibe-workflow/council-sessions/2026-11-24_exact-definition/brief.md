# Council brief: should a mistake that could pass the stated checks make a task "prose"?

## Role
You are one of 3 council members (Fable, Astra, Opus). Answer independently. You
cannot see the other members' answers.

## The user's standing instructions
Verbatim, from the current conversation:

- "Not sure about the `exact` sentence question... I need the @vibe-council"
- Earlier, standing: "Our goal here is still the same: efficiency. Doing more with less." Quality 60 / cost 40. Agents judge the stakes per task; a separate judging skill only if instructions prove insufficient. Rules never name models. Smart Router stays lightweight.

## Bias
Weigh optimistic bias as well as pessimistic bias. Flag an item that flatters the
result as readily as one that is too cautious. The coordinator drafted the
proposal; it does not vote.

## Context
Smart Router is an advisory OpenCode plugin (repository
`/Users/fcouto/.local/share/opencode/worktree/13464e/exact-council`, release 0.11.0).
A coordinator rates each delegated task with facts; the level is
`max(base[work_kind], files, spec, risk)`, with exceptions when `spec` is `exact`:
`files: 3+` no longer raises any level, and `risk` no longer raises implementation
or testing. So implementing an exact plan task stays at level 1 (cheapest models)
even in critical code; quality comes from level-3 design and plan reviews and a
level-3 review of each critical diff. `prose` gives level 2 or more; `open` gives
level 3.

The current tool text (in `index.js`, `definitions`; whole description 1,632 of a
2,048-character limit):

> spec: exact = this request is one task of a written plan that names its files, its expected behavior and the checks that tell success from failure, and leaves the builder nothing to decide; several plan tasks in one request, or any open choice, is prose. Unsure whether it is exact: say prose.

Read: `docs/advisor-evaluation.md`, `docs/superpowers/specs/2026-11-23-router-levels-design.md`,
`docs/superpowers/specs/2026-11-23-stakes-sized-reviews-design.md`,
`docs/superpowers/specs/2026-11-24-router-feedback-design.md`, and the council
records `docs/vibe-council/sessions/00e94efeee57_cost-quality-balance/result.md`
and `08d7b6126f45_router-constraints/result.md`.

## Settled rules (not up for a vote)
- The exact-plan exception (level 1 for implementing an exact plan task, even
  critical; reviews carry the risk), decided by council 00e94efeee57.
- Feedback cards adjust models over time; `estimate_agentic_effort_hours` raises
  effort at 4 hours or more.
- "Unsure whether it is exact: say prose" stays.
You may comment on how these compound, not on whether to keep them.

## Measured facts
- Evidence for the exception (`docs/advisor-evaluation.md`): two hard tasks
  implementing an existing detailed plan in a real-money trading-research
  repository, about one hour each, graded with the commit's own tests: a level-1
  model 67.0% of hidden tests passed vs a level-3 model 65.2% (6 runs each). About
  a third of hidden tests failed in every condition.
- A coordinator on that project (14-task plan, statistics-heavy) overrode the
  level-1 builder pick on 10 of 14 tasks, moving them to a level-2 model, citing
  "subtle statistics, where a mistake wouldn't show in a quick test". Under the
  0.10.0 text it relabelled 3 of the 14 (layout choices, design choices,
  amendments) as not exact on its own.
- No feedback cards from that run exist yet.
- The proposed sentence costs about 70 characters; the description has about 416
  left.

## Items
- E1 — Add to the `exact` definition: "If a mistake could pass the stated checks, it is prose." Direction: stricter. If wrong: most real tasks (whose tests never catch everything) become prose and level 2, erasing the measured saving.
- E2 — Narrower alternative: "If the stated checks could not tell a subtly wrong result from a right one (for example statistics, numerics or calibration), it is prose." Direction: stricter, bounded. If wrong: agents misread "subtly" and the rule over- or under-fires.
- E3 — No text change now; let feedback cards show whether level-1 builders fail on such tasks, and revisit with evidence. Direction: looser. If wrong: subtle defects ship meanwhile, caught (or not) by level-3 diff reviews.
- E4 — Instead of a spec rule, raise effort one step when the checks cannot catch a subtle error (a new optional fact `weak_checks`). Direction: neutral. If wrong: a new input agents misuse; more surface.
- E5 — Put worked examples (exact vs prose) in `docs/development.md` or a skill reference, not the tool text. Direction: neutral. If wrong: agents never read them at decision time.

## Output format (strict)
Answer with at most 600 words, in this order:

1. One line per item, exactly:
   `<ID>: KEEP | CHANGE: <simplest change> | DROP | DEFER — <short reason>`
   KEEP means adopt the item as written.
2. `Proposal`: up to 150 words, your simplest stance.
3. `Compounding`: 2 to 4 lines on how the items interact with the settled rules.
4. `Top 3`.

## Constraints
- Read-only. No edits, no Git writes, no subagents.
- Forbidden paths: `~/.ssh`, credentials, any `auth.json`, `.env` files,
  `~/.local/state/vibe-workflow`, `~/ai-memory-vault`, and
  `docs/vibe-council/sessions/` other than earlier sessions' `result.md` files.
- Use the network only if you need it.
- Use about 15 tool calls.
- Answer in your final message.
