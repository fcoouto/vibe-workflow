# Smart Router levels anchored on the frontier labs

Date: 23 November 2026. Status: agreed in conversation; awaiting the user's review
of this document.

## Intent

Send each delegated task to the cheapest model that is good enough for it, and
make "good enough" stable and the same for every user. A strong model writes and
reviews plans; implementing a detailed plan goes to a small model.

Why now: `docs/advisor-evaluation.md` found that on hard tasks implementing a
detailed written plan, Haiku 5.5 alone matched Opus 5.5 alone (67.0% and 65.2% of
hidden tests, 6 runs per condition and task) at about 5% of the cost. Today those
tasks are classed level 2 (`files: 3+`) and go to level-2 models.
Separately, today's floors are shares of the best model on the user's own plans,
so they move when a frontier model ships or when a plan's quota runs out, and they
mean different things for different users. A level-1 request limited to the Go
plan picks GLM-5.3 Flash (27.8) today, because the bar falls to 55% of Haiku's
36.5.

## Goals

- Level bars computed from the public score catalog, identical for every user and
  independent of which plans they hold.
- Bars that do not move when one lab ships a new flagship.
- Implementation of an exact written plan stays at level 1 even when it touches
  three or more files.
- Go models whose scored catalog entry differs only by a date suffix get their
  scores.

## Non-goals

- The main session's model. Smart Router keeps advising delegated work only
  (the user chose this, option A, 22 November).
- Usage balancing across plans (`2026-11-20-subscription-usage-balancing-design.md`
  stays deferred).
- The council's seat rule (`runtime/council.py`, `routing.seats`), which keeps its
  own 90% frontier rule.
- New OpenRouter fields. Coding and Agentic indexes are present but empty for every
  model today; expiration date and cache-read price are possible later refinements.
  (Coordinator's call: deferred to keep the change small.)
- New routes or providers. `config/routes.json` still holds exactly three paths.

## Anti-goals

- A bar that a single release from one lab can move overnight.
- A cheap model well below the level-1 bar becoming a default pick (the unchanged
  near clause still admits one from 97% of the bar at half the price).
- Silent downgrades: when nothing on the user's plans reaches a task's level, the
  answer must say so.
- Any new network call, model call, polling or stored state (the user's standing
  rule: Smart Router stays lightweight).

## Constraints

- Advisory only: no launch, admission, reservation or spend permit.
- The input contract stays `v: 2` with the same fields and enums.
- Python standard library; each module within 1000 lines; `runtime/` stays flat.
- The injected `ROUTER_NOTE` and `MODEL_NOTE` texts are unchanged. If a builder
  changes them, `scripts/live_check.py --injection` must run (AGENTS.md).

## Approaches considered

1. **Shares of the best model on the user's plans (today).** Rejected: moves with
   releases and quota, and differs per user.
2. **A share of the catalog's top-5 median, or a fixed index value.** Proposed by the
   council (record `docs/vibe-workflow/council-sessions/2026-11-23_router-levels/`).
   Superseded by the user's own rule below, which keeps the catalog anchor but ties
   it to labs rather than to the top five models.
3. **Each lab's cheapest model, or Haiku and Luna as references.** Rejected: noisy
   tier detection, or two labs hard-wired; users without Claude or ChatGPT plans
   would have no reference.
4. **Frontier-lab anchor (chosen by the user).** Level 3 is the top three labs'
   flagships; levels 2 and 1 are fixed shares of the weakest of those three.

## The decided design

### Anchor and bars

From the cached OpenRouter catalog (`openrouter-models.json`, already loaded per
request), using only entries with an own Intelligence Index score:

```text
best[lab] = max(score of the lab's models)          # lab = OpenRouter creator
A         = third-highest best[lab]                  # 61.4 today: Gemini 4 Neon
bar[3]    = A                                        # 61.4
bar[2]    = 0.80 * A                                 # 49.1
bar[1]    = 0.55 * A                                 # 33.8
```

Today the three labs are Anthropic (Fable 5.2, 64.6), OpenAI (Astra 6.2, 62.1) and
Google (Gemini 4 Neon, 61.4). A new flagship from Anthropic or OpenAI does not move
A; only a change to the third lab's best, or a new lab entering the top three, does.

Level 3 is "score at least A". The user stated it as "top 3 models from different
vendors"; the bar form is the coordinator's reading, identical today, and it also
keeps a lab's previous flagship at level 3 while it still scores at least A.

The 80% and 55% were chosen by the user after comparing 70/50, 75/50 and 80/55 on
today's scores. They are policy values, not measured thresholds.

### Eligibility and order

- A task at level L may use any runnable candidate scoring at least `bar[L]`.
  Higher-level models are eligible for lower-level tasks.
- Order is unchanged (the user: "keep the cheapest rule the same"): cheapest
  blended list price first, then higher score, own score before a family-borrowed
  one, newer release, route id. The near clause stays: a candidate scoring at least
  97% of the bar and priced at most half the cheapest qualifying price is also
  eligible.
- Effort still follows the task's level (low, medium, high), not the model's.
  (Since 0.12.0: medium, medium, high. Since 0.13.0: high at every level for every kind of work but writing,
  which keeps medium, medium, high; see the feedback design's amendments.)

### Nothing reaches the level

When no runnable candidate scores at least `bar[L]`, recommend the strongest
runnable candidates (score first, then the usual order), and say so. This replaces
today's behaviour, where a pool-relative floor always lets something through.
(Coordinator's calls, settled after the builder check:)

- The answer keeps `state: "selected"`, so the agent still gets a default pick, and
  adds `below_level: true` on every such answer.
- `index.js` today builds a notice only when `state !== "selected"`. Add a branch:
  a selected answer with `below_level` gets a notice saying no model on the user's
  plans reaches this task's level and the strongest available was recommended.
  It follows the existing once-per-session-per-cause rule, with the level as part
  of the cause, so each level is announced once per session. The field on the
  answer itself is never deduplicated.
- `ROUTER_NOTE` stays unchanged, so the injection live check is not triggered. The
  notice text itself asks the agent to tell the user.

### Task level

`level = max(base[work_kind], files, spec, risk)` stays, with exceptions when `spec`
is `exact`:

- For every work kind, `files: 3+` no longer raises the level.
- For `implementation` and `testing`, `risk` no longer raises the level either, so
  implementing an exact plan stays at level 1 even in critical code. Quality comes
  from level-3 design and plan reviews and a level-3 review of each critical diff
  (`2026-11-23-stakes-sized-reviews-design.md`). Decided by the council
  (`00e94efeee57_cost-quality-balance`), majority, weighted quality 60, cost 40.
- `cross-system` still raises any task to 3. The user kept it, with the narrower
  definition: changes in more than one repository or deployed service that must stay
  compatible with each other.

The definitions of `exact`, `risk` and `cross-system` go into the tool's rubric text;
their wording is in the stakes design. `ROUTER_NOTE` is not changed.

### Score matching for suffixed names

Two runnable Go models go unscored because the catalog's scored entry carries a
date suffix:

| Runnable | Catalog | Score |
| --- | --- | --- |
| `deepseek-v4.1-flash` | `deepseek/deepseek-v4.1-flash-1017` | 32.6 |
| `qwen3.8-max` | `qwen/qwen3.8-max-0902` | 33.7 |

Match a runnable key to a catalog key that differs only by one trailing four-digit
date segment on the catalog side. Keep the existing rule: only an exact, unique match
counts; two dated entries for one key stay unscored. The catalog also lists
`deepseek/deepseek-v4.1-flash` itself, without an Intelligence Index score, so the
date match must apply when the exact entry exists but is unscored, before the family
fallback. Price follows the existing lookup rule: the runnable model's own catalog
price when its entry exists, otherwise the matched entry's. Both models fall just
below the level-1 bar today, so no pick changes.

Tier suffixes on the runnable side (`muse-spark-1.3-contributor`) are not matched.
Without a score on the runnable side there is no guard that the suffix names the
same model: "any suffix without digits" would map `hy4-flash` to `hy4`, and
`-mini` or `-pro` to their larger siblings, and with the runnable model's own
lower price the smaller model would become a default level-1 pick.
(Coordinator's calls: the fix was recommended in conversation and not objected
to; the builder check narrowed it to date suffixes.)

## What it picks today

Bars 61.4 / 49.1 / 33.8. The name fix adds no model at any level today.

| Level | Claude | ChatGPT | Go |
| --- | --- | --- | --- |
| 3 | Fable 5.2 | Astra 6.2 | none |
| 2 | Opus 5.5, Sonnet 5.5, Fable 5.1 | Sol 6.2, Astra 6, Sol 6.1, Terra 6.1 | Gemini 4 Argon |
| 1 | Opus 5, Haiku 5.5 | Sol 6 | Kimi K3.1 Pro, Qwen4 Max, Haiku 5.5, Grok 4.7, HY4 |

Picks with all three plans usable: level 1 Haiku (Claude), then Haiku (Go), then
HY4; level 2 Terra 6.1; level 3 Fable 5.2 (ties Astra on price, scores higher).
Claude only: Haiku, Sonnet, Fable 5.2. Opus 5.5 becomes level 2, so a Claude-only
user's planning goes to Fable 5.2 instead of Opus; the user accepted this.

## Tests the builder must update or add

- `tests/runtime/test_routing.py`: the golden tests change. Two plans: level 1
  Haiku, level 2 Terra, level 3 Fable 5.2. Claude only: Haiku, Sonnet, Fable 5.2.
  The anchor must take the third-best lab, not the third-best model.
- A frontier release from a top-two lab leaves the bars unchanged; a new third lab
  moves them.
- A Go-only level-1 request no longer picks GLM-5.3 Flash (27.8). The near clause
  still admits a model from 97% of the bar (32.8 at level 1) when it costs at most
  half the cheapest qualifying price; this is the unchanged cheapest rule the user
  kept, and no current model triggers it.
- Nothing at the level: `selected`, the strongest runnable model, `below_level:
  true`, and one notice per session and level.
- `spec: exact` with `files: 3+` is level 1; implementation or testing with `spec:
  exact` and `risk: critical` is level 1; review with `risk: critical` is level 3;
  anything `cross-system` is level 3.
- The two date-suffix matches above, a key with two dated entries staying unscored,
  and `muse-spark-1.3-contributor` staying unscored.
- The council's seat pick is unchanged.

## Left to the builder

- Where the anchor is computed (scores module or routing), and how `order` receives
  the bars.
- The notice's wording.
- Whether `RATIO` survives only for the council's 90% rule.
- Updating `docs/development.md` and the auto-selection design's ranking section to
  point here; `README.md` changes only through the `impeccable` skill, if at all.

## Record

- Advisor evidence: `docs/advisor-evaluation.md`.
- Council: `docs/vibe-workflow/council-sessions/2026-11-23_router-levels/result.md`.
