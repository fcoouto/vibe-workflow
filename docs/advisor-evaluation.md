# Advisor evaluation

Date: 20 to 22 November 2026, on OpenCode 2.3.2. Asked by the user: would an
advisor like Claude Code's `/advisor` (a cheap executor that consults a stronger
model at decision points) raise quality and lower cost here? A throwaway plugin
prototype was built and measured in four rounds; nothing was added to this
package. Costs are API list prices (OpenRouter), estimated from token counts,
because subscription runs report no cost; advisor cost is a lower bound, since the
advisor's thinking tokens are not visible.

## Answer

No advisor for now. Across four rounds and about 190 graded runs the advisor never
gave a reliable quality gain. The larger finding is about executors: on hard tasks
that implement a detailed written plan, Haiku alone matched Opus alone at about 5%
of the cost. That points to routing, not to an advisor: a strong model writes the
plan, a level-1 model implements it.

## How it was measured

- Prototype: a no-input `advisor` tool. It read the session (`ctx.session.context`)
  plus the captured system prompt, called the advisor with
  `ctx.experimental.generate.text` and no tools, and returned the advice. Its
  when-to-call rules followed Claude Code's.
- Each run: a fresh clone, a private server (`opencode run --standalone`), the
  user's global plugins, one prompt that marked the task approved, then grading
  with tests the executor never saw.
- Rounds 1 to 3: four synthetic Python tasks, three harder ones, and two merged
  pull requests of this repository (#359, and #340 graded with an OpenCode 2.3.2
  result-shape test). Round 4: two tasks of the user's trading-research repository,
  each the next task of an existing written plan, graded with that commit's tests.
- A blind diff review by GPT-6.2 Astra in rounds 1 to 3.

## Results

| Round | Finding |
|---|---|
| 1, easy tasks | Every condition passed every hidden test. Sonnet with an Opus advisor cost as much as Opus alone. |
| 2, this repository | No gain. Reading the whole session cost about $0.37 per Opus call (median about 330,000 characters, uncached). Sonnet saw the policy but called the advisor in 1 of 8 runs; Sol called it about 4 times per run. |
| 3, level-1 executors | Only Luna gained (tests 90.6% to 96.9%), at 60 times Luna's cost; 2 runs per condition. |
| 4, hard plan tasks | With compact context (no tool catalog, last three tool results only, at most three calls) a call cost about $0.15 with Opus and $0.05 with Sonnet. A Sonnet advisor scored below no advisor. |

Round 4 confirmation, 6 runs per condition and task, share of the commit's tests
passed (bootstrap 90% interval):

| Condition | Average | Cost per run |
|---|---|---|
| Opus alone | 65.2% (59.5–70.5) | $2.54 |
| Haiku alone | 67.0% (61.0–73.5) | $0.12 |
| Haiku with an Opus advisor | 65.9% (60.2–71.6) | $0.45 |

An earlier "Haiku with an advisor matches Opus" came from two runs; single runs of
one condition ranged from 45% to 91% on the same task.

## Other findings

- Advice was accurate (it caught a stale-cache design and a wrong field order) but
  the executors mostly reached the same answers alone.
- The advisor sometimes widened scope (copy and pickle support, a Code Mode detour)
  and once told an executor to skip a brainstorming approval gate.
- Claude models refuse instructions appended to tool results as prompt injection.
- `claude-agent` (Claude Code) disables Claude's own advisor tool
  (`CLAUDE_CODE_DISABLE_ADVISOR_TOOL`).

## Limits

Two task families in round 4, both implementing an existing detailed plan. Tasks
without a written plan, where an advisor would plausibly help most, were not
tested. DeepSeek runs were cut by the OpenCode Go five-hour limit and are not
reported. The judge in rounds 1 to 3 is from the same lab as Sol and Luna.
