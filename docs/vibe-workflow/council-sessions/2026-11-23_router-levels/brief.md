# Council brief: how Smart Router should decide which models may take which tasks

## Role
You are one of 2 council members (Astra, Fable). Answer independently. You cannot
see the other member's answer.

## The user's standing instructions
From the current conversation, verbatim:

- "And I like your idea of tuning Smart Router in order to make sure proper tasks go to stronger models."
- On scope, choosing option A ("Delegated work only: the coordinator writes or reviews plans on a strong model and hands implementation to level 1"): "Let's start with A."
- "Now, regarding the level-1 bar being 55% of the highest scoring model, shall we take some time for thinking on a better and dynamic approach here? I'd like to see the latest Luna on level 1 models as well..."
- On whether Luna should be a fallback or share the load with Haiku: "I believe both. Luna and Haiku are both the smaller models from the two reference companies: OpenAI and Anthropic. They are both designed to deal with bounded tasks with not much complexity, hence them both on the same level/category."
- On anchoring the bar to Haiku and Luna: "But that's my case... There could be other users with xAI and DeepSeek subscriptions, which doesn't even include Claude and OpenAI models in the equation."
- "I'm still not convinced we reached the best idea. [...] Let's hear from them what is the more effective way of doing that. I'm ok with re-estructuring the 3-level format we currently have."

Standing design rule from the user's memory (2026-11-10): "I want the usage readings to be as lightweight as possible. Actually, this applies for the whole Smart Router solution." In practice: reuse data already produced, no extra model calls, no polling or daemons, keep the hot path cheap, and say so when a design adds cost.

## Bias
Weigh optimistic bias as well as pessimistic bias. Flag an item that flatters the
result as readily as one that is too cautious.

## Context
Smart Router is an OpenCode plugin that only advises: when a coordinator agent
delegates a task to a subagent, it calls `smart_router_select` with a work kind and
three task facts, and gets back one recommended provider/model/effort plus two
alternatives. It never launches anything. The user's main session model is always
the user's own choice.

How it works today (read `runtime/routing.py`, `runtime/selector.py`,
`index.js` lines 31-72, and the design
`docs/superpowers/specs/2026-11-20-smart-router-auto-selection-design.md`, section
"Task description" and "Ranking rule"):

- `level = max(base[work_kind], files, spec, risk)`. Base: planning 3; review and
  investigation 2; implementation, testing and writing 1. Facts, levels 1/2/3:
  `files` 1-2 / 3+ / cross-system; `spec` exact / prose / open; `risk`
  reversible / shared / critical.
- Candidates are the models runnable on the user's subscription plans (here:
  Claude via Claude Code, ChatGPT via OpenAI, OpenCode Go). Each gets a public
  score (Artificial Analysis Intelligence Index via OpenRouter, refreshed about
  weekly, cached locally) and a list price `(3*input + output)/4` per million
  tokens, used as a proxy for subscription cost.
- `floor = best_runnable_score * RATIO[level]`, `RATIO = (0.55, 0.82, 0.90)`.
  Candidates at or above the floor, plus "near" ones (score >= 0.97*floor and at
  most half the cheapest qualifying price), are sorted cheapest first; ties break
  on higher score. Effort is low/medium/high for levels 1/2/3.
- A deferred design, `docs/superpowers/specs/2026-11-20-subscription-usage-balancing-design.md`,
  would reorder picks by each plan's usage pace. Not implemented.
- The council tool (`runtime/council.py`) reuses the level-3 floor for seat picks.

Evidence motivating the change: `docs/advisor-evaluation.md`. On two hard tasks
that implement an existing detailed written plan, 6 runs per condition per task:
Opus 5.5 alone 65.2% of hidden tests passed (90% interval 59.5-70.5) at $2.54 per
run; Haiku 5.5 alone 67.0% (61.0-73.5) at $0.12. GPT-6 Luna was tested only in
smaller rounds (2 runs per condition): on synthetic tasks 90.6% of hidden tests;
on the plan tasks it matched Opus on one task when it ran (it refused to start in
4 of 6 runs on the other task because of a repository instruction, not ability).
These tasks would be reported today as implementation, spec exact, files 3+,
so level 2, which routes to Sonnet- or Terra-class models.

## Settled rules (not up for a vote)
- Smart Router stays advisory; it never launches, reserves or grants spend.
- Scope is delegated work only. The main session's model is untouched.
- The selection must work for any mix of plans, including users with no Claude or
  OpenAI plan at all (for example xAI plus DeepSeek). No lab may be hard-wired as
  the reference.
- Haiku-class and Luna-class models should both be eligible for the bounded,
  low-complexity tier (as fallback for each other and to share load).
- Lightweight: data already cached (OpenRouter catalog, native model lists, usage
  readings already taken on demand) only; no new model calls or polling.
- The three-level format may be restructured. The caller's interface may change
  (it is versioned: `v: 2`).

## Measured facts
All scores from the cached OpenRouter catalog on 2026-11-23 (the same file the
router reads), excluding batch and free variants. Price is the router's blended
list price, USD per million tokens.

- Strongest runnable on this user's plans: Fable 5.2 64.6 ($16), GPT-6.2 Astra
  62.1 ($16), Opus 5.5 59.1 ($8), GPT-6.2 Sol 57.3 ($4), Sonnet 5.5 54.5 ($4),
  GPT-6.1 Terra 51.3 ($2), Haiku 5.5 36.5 ($0.20), GPT-6 Luna 27.9 ($0.20).
- Today's level-1 floor: 0.55 * 64.6 = 35.5. Haiku clears it by 1.0 point; Luna is
  excluded. A frontier release scoring 67 or more would push Haiku out.
- If the Claude plan is exhausted, the best runnable becomes Astra 62.1 and every
  floor drops about 4%.
- A user with only xAI and DeepSeek: best runnable Grok 4.7 35.5, level-1 floor 19.5,
  level-3 floor 32.0.
- Catalog-wide top five (any lab): 64.6, 62.1, 61.4 (Gemini 4 Neon), 60.0
  (Gemini 4 Carbon), 59.1; median 61.4.
- Cheapest current model per lab (released in the last 6 months, labs with two or
  more such models), scores: Anthropic Haiku 36.5, OpenAI Luna 27.9, Google Gemini
  3.5 Flash-Lite 11.7, DeepSeek V4.1 Flash 32.6, Meta Muse Ember 36.0, Qwen 3.8
  Flash 28.6, Z.ai GLM-5.3 Flash 27.8 ($0.05), MiniMax M3.1 Flash 29.1, xAI Grok
  4.6 31.0 ($3), Xiaomi MiMo 2.6 Flash 26.7, Moonshot Kimi K3.1 Pro 46.5, Nvidia
  5.5, Thinking Machines Inkling 2 Mini 20.1. Median 28.6. Noisy: "cheapest" is not
  always the lab's small tier.
- Other cheap scored models near the Luna band: Ling 3.1 Flash 29.6 ($0.09), GLM-5.3
  Flash 27.8 ($0.05). Any bar admitting Luna admits these where runnable. On this
  user's Go plan most cheap models are currently unscored (name mismatch), so they
  are not candidates today.
- Live level-1 request today (implementation, 1-2 files, exact, reversible): pick
  Haiku (Claude plan), alternatives Haiku via Go and GPT-6.1 Terra.
- Ratios: Haiku/Opus 0.62; Luna/Opus 0.47; Luna/Fable 0.43; Haiku/Fable 0.57.

## Items
- I1 — Keep the level-1 floor as a share of the best model on the user's own plans
  (status quo, 0.55). Direction: neutral. If wrong: Haiku drops out on the next
  frontier release; floors move with quota; weak-plan users get loose floors.
- I2 — Level-1 floor from the public catalog, same for every user: 0.45 times the
  median of the catalog's five strongest models (27.6 today). Levels 2 and 3 stay
  relative to the user's own best. Direction: looser. If wrong: frontier releases
  still move it, and cheap untested models from any lab become the default pick.
- I3 — Level-1 floor = median of each lab's cheapest current model in the catalog
  (28.6 today), possibly with a margin. Direction: neutral. If wrong: noisy, label
  tiers misdetected, Luna falls just outside.
- I4 — Level-1 floor = the catalog scores of the newest Haiku and Luna, read from
  public data. Direction: neutral. If wrong: two labs hard-wired; breaks on renames.
- I5 — When `spec` is exact (a detailed written plan), `files` no longer raises an
  implementation task's level; `risk` still does. Direction: looser. If wrong:
  large plan implementations go to small models and fail.
- I6 — Keep three levels with `level = max(base, facts)`. Direction: neutral. If
  wrong: the coarse max rule mis-tiers work, and the format is the real problem.
- I7 — Above the floor, keep cheapest list price first, ties to the higher score.
  Direction: neutral. If wrong: the cheapest barely-qualifying model always wins,
  so a qualifying tier collapses to one model and quality sits at the floor.
- I8 — Sharing level-1 load between plans belongs to the deferred usage-balancing
  design, not to the floor rule. Direction: neutral. If wrong: "both Haiku and Luna"
  is not delivered by this change.

## Output format (strict)
Answer with at most 800 words, in this order:

1. One line per item, exactly:
   `<ID>: KEEP | CHANGE: <simplest change> | DROP | DEFER — <short reason>`
   Pick one verdict per line. Give a change only after CHANGE.
2. `Proposal`: up to 300 words. Your most effective design for deciding which
   models may take which delegated tasks, under the settled rules. You may replace
   the levels, the floors, the facts or the ordering. Say what it computes, from
   which cached data, what it would pick today for this user at each tier, what it
   would do for an xAI plus DeepSeek user, and how it behaves when a new frontier
   model ships.
3. `Compounding`: 2 to 4 lines on how the items interact.
4. `Top 3`: the three items that matter most.

## Constraints
- Read-only. No edits, no Git writes, no subagents.
- Forbidden paths: `~/.ssh`, `~/.config` credentials, any `auth.json`, `.env`
  files, `~/.local/state/vibe-workflow` (the numbers above come from it),
  `~/ai-memory-vault`, and `docs/vibe-council/sessions/` other than reading
  earlier sessions' `result.md` files.
- Use the network only if you need it.
- Use about 15 tool calls.
- Answer in your final message.
