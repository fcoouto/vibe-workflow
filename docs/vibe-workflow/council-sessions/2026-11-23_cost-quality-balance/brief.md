# Council brief: the most efficient cost-versus-quality balance for Smart Router levels and stakes-sized reviews

## Role
You are one of 3 council members (Fable, Astra, Opus). Answer independently. You
cannot see the other members' answers.

## The user's standing instructions
Verbatim, from the current conversation:

- "I'd love to have a @vibe-council set up for the final decisions on what would be the more efficient way to have the best out of cost vs quality. The perfect balance between these two, giving a bit of priority for quality over cost, but not to much... Let's say 60/40"
- "Our goal here is still the same: efficiency. Doing more with less."
- "I don't want that rule to be so simple... It should be more for the model to decide depending on the task profile... For example, if it's dealing with trading, real money project, I would want a strong model designing/planning and reviewing what other lesser models implemented."
- "Leave it for the agents to judge... We may help the agents on HOW-TO judge with a complementary skill, if current instructions are not enough..."
- "In order to increase quality of deliverables, I want Design and Implementation Plans to be reviewed by a Level 3 model. In accordance to what our partner plugins already establish."
- "I'm ok with re-estructuring the 3-level format we currently have."

Standing design rule (user, 2026-11-10): "I want the usage readings to be as lightweight as possible. Actually, this applies for the whole Smart Router solution." No extra model calls, polling or state on the hot path.

**Weighting for every vote: quality 60, cost 40.** Prefer the option that buys more
quality per unit of cost; accept extra cost only where it buys a clear quality gain.

## Bias
Weigh optimistic bias as well as pessimistic bias. Flag an item that flatters the
result as readily as one that is too cautious. Note: Fable drafted the texts in
items R1-R6; the coordinator drafted the rest. The coordinator does not vote.

## Context
Smart Router is an advisory OpenCode plugin: when a coordinator agent delegates to
a subagent, it calls `smart_router_select` with a work kind and three facts (files,
spec, risk) and gets one recommended model. It never launches anything. Partner
plugin "superpowers" sets the workflow: brainstorming writes a design (a "builder
check" subagent reads it before the user does), writing-plans writes an
implementation plan (author self-review only), then subagent-driven development or
inline execution implements it, with per-task reviewers scaled "to the diff's size,
complexity, and risk" and a final whole-branch review "on the most capable available
model".

Read, in `/Users/fcouto/.local/share/opencode/worktree/13464e/router-levels-council`:
- `docs/superpowers/specs/2026-11-23-router-levels-design.md` (levels).
- `docs/superpowers/specs/2026-11-23-stakes-sized-reviews-design.md` (texts R1-R3).
- `docs/advisor-evaluation.md` (the evidence).
- `runtime/routing.py` (`task_level`, `order`), `index.js` lines 31-75,
  `src/skill-advice.js`.
- Superpowers skills, read-only:
  `/Users/fcouto/.cache/opencode/npm/git-superpowers-0958a5557860/1794274885747/node_modules/superpowers/skills/`
  `brainstorming/SKILL.md`, `writing-plans/SKILL.md`,
  `subagent-driven-development/SKILL.md` (Model Selection), `executing-plans/SKILL.md`.

## Settled rules (not up for a vote)
- Level 3 = score at least A, where A is the third-highest of the labs' best
  catalog scores (61.4 today: Fable 5.2 64.6, Astra 62.1, Gemini 4 Neon 61.4).
  Level 2 = at least 0.80 A (49.1). Level 1 = at least 0.55 A (33.8). Same for every
  user. Higher-level models are eligible for lower-level tasks.
- Order: cheapest blended list price first, unchanged, including the near clause.
- `level = max(base[work_kind], files, spec, risk)`; bases: planning 3, review and
  investigation 2, implementation, testing and writing 1.
- Agents judge stakes per task; no per-project stakes setting.
- Approach: definitions in the tool description plus skill-load notes, then a live
  check; a separate judging skill only if the live check shows misjudgment.
- The design check (builder check) is replaced by one review that both asks
  questions and critiques. Plans gain one independent review.
- Smart Router is advisory only; the main session's model is the user's.
You may comment on how these compound, not on whether to keep them.

## Measured facts
- Evidence (`docs/advisor-evaluation.md`), hard tasks implementing an existing
  detailed plan in the user's real-money trading-research repository, 6 runs per
  condition per task, share of hidden tests passed: Opus 5.5 alone 65.2% (90%
  interval 59.5-70.5) at $2.54 per run; Haiku 5.5 alone 67.0% (61.0-73.5) at $0.12;
  Haiku with an Opus advisor 65.9% at $0.45. Single runs ranged 45-91% on one task.
  No task without a written plan was tested.
- Blended list prices, USD per million tokens ((3 input + output)/4): Fable 5.2 16,
  Astra 16, Opus 5.5 8, Sol 6.2 4, Sonnet 5.5 4, Terra 6.1 2, Haiku 5.5 0.20.
- Today's picks under the settled levels, all three of the user's plans usable:
  level 1 Haiku; level 2 Terra 6.1; level 3 Fable 5.2.
- An advisor call with compact context cost about $0.15 (Opus) in the evaluation;
  the cost of one level-3 document review subagent here is not measured.
- Superpowers: "Turn count beats token price ... Use a mid-tier model as the floor
  for reviewers"; the cheapest models "routinely take 2-3x the turns on multi-step
  work".
- `risk: critical` today lifts every work kind to level 3, including the
  implementer of an exact plan.

## Items
- R1 — Definitions in the tool description (text in the stakes design, 727
  characters): rate the task, not the project, by what a mistake would cost; critical
  = could lose real money, expose credentials, break production or users' data, or
  not undone by a revert. Direction: neutral. If wrong: systematic mis-rating.
- R2 — Tie-break "Unsure whether it is critical: say critical". Direction: stricter.
  If wrong: inflated level-3 spend, or (if dropped) under-rated money code.
- R3 — Review facts: a review takes the risk and files of the reviewed work; spec is
  exact for a plan or a diff built from one, otherwise prose. Direction: looser. If
  wrong: design reviews of critical work too weak, or all design reviews on flagships.
- R4 — Design check runs for every project-sized design and for small changes whose
  risk is critical; other small changes skip it. Reviewer adds up to five
  costliest flaws, no rewrites; document path only, one pass. Direction: neutral.
  If wrong: missed design flaws, or review spend on trivia.
- R5 — Plan review runs unless the plan's risk is reversible (so every shared plan
  gets a level-2 review), one reviewer, once, paths only, ranked findings, "nothing
  important" valid; author fixes without a second review. Direction: stricter. If
  wrong: most plans pay a review that finds nothing.
- R6 — Fallback when Smart Router is off: reviewer on a mid tier, top tier only if
  critical. Direction: neutral. If wrong: unrouted reviewers inherit the session model.
- R7 — Implementing (or testing) an exact plan in critical code: `risk` no longer
  raises the level, so it runs at level 1 (Haiku), on condition that a level-3
  review of what was implemented follows. Options: keep level 3, cap at level 2, or
  level 1. Direction: looser. If wrong: money-code bugs that the review misses.
- R8 — For critical work, the level-3 implementation review is the final
  whole-branch review; per-task reviews follow their normal level (2). Direction:
  looser. If wrong: defects caught late, larger fix loops.
- R9 — An exact spec stops `files: 3+` from raising an implementation's level;
  `cross-system` still raises it to 3. Direction: looser. If wrong: large plans fail
  on small models.
- R10 — `cross-system` forces level 3 regardless of risk (for example a docs change
  across two repositories). Direction: stricter. If wrong: flagship spend on trivia.
- R11 — When nothing on the user's plans reaches a level: recommend the strongest
  available, mark `below_level`, notice once per session per level. Direction:
  neutral. If wrong: silent downgrades or notice fatigue.
- R12 — Score matching fixed for date-suffixed catalog names only; tier suffixes
  such as `-contributor` stay unscored. Direction: stricter. If wrong: good cheap
  models left out, or small models scored as large ones.
- R13 — Live check before any skill: three prompt runs (a trading change, expect
  critical; a docs typo, expect reversible and no plan review; a config or helper
  feeding a money path, expect critical). Direction: neutral. If wrong: shipping
  definitions that misjudge, or paying for a test that proves little.

## Output format (strict)
Answer with at most 700 words, in this order:

1. One line per item, exactly:
   `<ID>: KEEP | CHANGE: <simplest change> | DROP | DEFER — <short reason>`
   Pick one verdict per line. Give a change only after CHANGE. For R7, a KEEP means
   level 1; otherwise CHANGE to "level 2" or "level 3".
2. `Compounding`: 2 to 4 lines on how the items interact under the 60/40 weighting.
3. `Top 3`: the three items that matter most.

## Constraints
- Read-only. No edits, no Git writes, no subagents.
- Forbidden paths: `~/.ssh`, credentials, any `auth.json`, `.env` files,
  `~/.local/state/vibe-workflow`, `~/ai-memory-vault`, and
  `docs/vibe-council/sessions/` (except earlier sessions' `result.md` files).
- Use the network only if you need it.
- Use about 15 tool calls.
- Answer in your final message.
