# Smart Router automatic selection

Status: draft for review, 20 November 2026.

## Intent

Smart Router should work with no setup. Today a user who answers "Use Smart
Router" during onboarding still gets `setup_needed: unconfigured`, because the
selector needs two hand-written files (`router.json`, `strategy-table.json`) that
nothing creates, and its ranking is the hand-typed order of models in those rows.
New models are never picked up: GPT-6.2 Sol is released and listed by OpenCode,
while the user's old table still names GPT-6.1 Sol.

The user wants the router to discover the models they can run, judge how hard a
task is, shortlist the models able to handle it, and recommend the least capable
one that can do the job, as superpowers advises ("use the least powerful model
that can handle each role"). When two models score about the same, the cheaper
one wins. New models are included automatically. Users never edit a file.

## Goals

- Zero configuration: the onboarding answer is the only setup.
- Automatic model discovery from what OpenCode can actually run.
- Capability from public benchmark scores, with no API key.
- A deterministic, testable rule: cheapest model that clears the task's bar,
  with a cost-benefit exception for a much cheaper, nearly as capable model.
- Lightweight: on-demand work only, no timers, no daemons, no model calls.

## Non-goals

- Learning from task outcomes (failures, review rounds). Possible later.
- Pay-per-use API providers. Only the three subscription paths stay.
- User-editable weights, floors or overrides.
- Changing who launches work: the router stays advisory.

## Anti-goals

- Any recommendation that could cause paid spending.
- Blocking the caller on the network during normal use.
- Fuzzy model-name guessing. A wrong match is worse than no score.
- Bundling or redistributing benchmark data in the package.

## Constraints

- Advisory invariants stay: results carry `admission: false`,
  `reservation: false`, `spend_permit: false`; nothing launches or reserves.
- The user's standing rule for Smart Router (memory vault,
  `smart-router-lightweight`): on-demand reads, reuse existing data, no polling,
  timers or background daemons, no extra model calls, minimal bounded process
  spawns, and say when a design adds cost.
- Python 3.11 standard library and Node built-ins only. Each Python module stays
  under 1000 lines.
- The selector never runs inference. The calling agent describes the task.
- Private state: directory `0700`, files `0600`, existing storage helpers
  (`runtime/storage.py`) with their locks and atomic replacement.
- New network egress: one public HTTPS GET to `openrouter.ai`, with no
  credentials. This is new for the selector and is disclosed at onboarding.
- Artificial Analysis requires credit wherever its scores are shown.

## Verified facts (20 November 2026)

- `ctx.model.list()` is part of the OpenCode V2 server-plugin API and reflects
  every registered provider and model
  (<https://opencode.ai/docs/build/plugins/server>). It lists, per model, the
  provider, id, display name and effort variants. Subscription providers
  (`claude-agent`, `openai` on a ChatGPT plan) carry no price.
- OpenCode's built-in Claude Agent provider records each alias's real model,
  for example `sonnet` → `claude-sonnet-5-5`. This repository already reads the
  same mapping from the Claude CLI (`runtime/native_catalog.py`,
  `from_claude_initialize`, field `resolved`).
- OpenRouter's public `GET https://openrouter.ai/api/v1/models` answers without a
  key: 477 models, about 88 KB gzip-compressed (926 KB raw). Each model has
  `pricing.prompt` / `pricing.completion` (USD per token) and
  `benchmarks.artificial_analysis.intelligence_index`. 105 models have a score,
  including every model the user runs. There is no `ETag` or `Last-Modified`;
  `Cache-Control` is `public, max-age=120`.
- Artificial Analysis no longer publishes a Coding Index; that field is always
  null. The Intelligence Index is the score to use.
- OpenRouter ids use dots (`anthropic/claude-sonnet-5.5`); model ids elsewhere
  use dashes (`claude-sonnet-5-5`). OpenCode's own catalog links the two forms,
  so a dot-to-dash normalization is an exact match, not a guess.
- Current selection only accepts routes whose billing fact reports
  `paid_crossover: provider_blocked`. Only Claude can report that (when extra
  usage is disabled). ChatGPT and Go report `unknown` and are always excluded
  as `overflow_not_blocked` (`runtime/routing_observations.py:47`,
  `tests/test_selector.py:163-169`).

Scores today (Intelligence Index; price per million tokens, input / output):
Fable 5.2 64.6 ($8/$40), GPT-6.2 Astra 62.1 ($8/$40), Opus 5.5 59.1 ($4/$20),
GPT-6.2 Sol 57.3 ($2/$10), Sonnet 5.5 54.5 ($2/$10), GPT-6.1 Terra 51.3 ($1/$5),
Fable 5.1 50.0 ($10/$50), Haiku 5.5 36.5 ($0.10/$0.50), GPT-6 Luna 27.9
($0.10/$0.50).

## Approaches

1. **Compute on every request from live sources (chosen).** OpenCode's model list
   says what can run; OpenRouter's public list supplies scores and prices; one
   rule ranks. No strategy file exists. New models appear as soon as OpenCode
   lists them. Fewest files and no maintenance job.
2. **Generate a model inventory daily from a bundled benchmark snapshot**
   (proposed by GPT-6.2 Astra). Scores ship inside the package, refreshed by a
   maintainer job. Rejected: Artificial Analysis requires permission to
   redistribute, it adds a recurring job, and the file can go stale.
3. **Let the calling agent regenerate `strategy-table.json`** from the catalog.
   Rejected: nondeterministic, untestable, stale between runs, and it keeps the
   whole table surface.

Also rejected: asking each user for an Artificial Analysis key (a manual step,
and OpenRouter already carries the same scores without one).

The approach came from the user's description, independent proposals by GPT-6.2
Astra and Claude Fable 5.2, and the user's choices in conversation.

## Design

### Data flow per request

1. The caller invokes `smart_router_select` with the work kind and three task
   facts (below). The project preference check is unchanged.
2. `index.js` reads `ctx.model.list()` and passes a reduced list (provider, id,
   name, effort variants, context limit) to the Python child inside the request,
   under a key the tool's input schema does not accept. The caller can never
   supply or alter the model list.
3. Python keeps only models whose provider matches one of the three paths in
   `config/routes.json`. There is no path configuration. A path whose account
   reader can never prove paid overflow is blocked (today ChatGPT and Go; see
   *Open decision*) is skipped before any account read, with the fixed exclusion
   reason `overflow_unprovable`, so it costs nothing. Paths without a signed-in
   account drop out through the existing `billing_unverified` check.
   (Builder's call, from the lightweight rule: without the skip, every request
   would read ChatGPT and Go accounts that can never be picked.)
4. The existing billing, overflow and quota checks run first, unchanged, and
   drop routes that cannot be used now. They run before ranking so the floor
   comes from the best model the user can actually use.
5. Each remaining model gets a score and a price from the cached OpenRouter data
   (see *Mapping* and *Refresh*), then the context and caller constraints apply.
6. The ranking rule orders the candidates. The first is the pick; the next two
   are returned as `alternatives`. Dropped routes stay visible in `excluded`
   with their existing reasons.

### Task description

Input contract `v: 2`. Required: `work_kind` (unchanged enum) and `task`, an
object with three required facts the caller can observe:

| Fact | Level 1 | Level 2 | Level 3 |
|---|---|---|---|
| `files` | `"1-2"` | `"3+"` | `"cross-system"` |
| `spec` | `"exact"` | `"prose"` | `"open"` |
| `risk` | `"reversible"` | `"shared"` | `"critical"` |

`level = max(base[work_kind], files, spec, risk)`, where the base is 3 for
`planning`, 2 for `review` and `investigation`, and 1 for `implementation`,
`testing` and `writing`. Review's base of 2 encodes superpowers' "turn count
beats token price" floor for reviewers.

The tool description carries this rubric in a few lines. Requests with `v: 1`,
or missing facts, return `invalid_request`; there is no silent default. Optional
`constraints` keep `allowed_models`, `excluded_models`, `allowed_efforts` and
`allowed_harnesses`. `allowed_strategy_ids` and the old `profile` fields go away.
`profile.context_tokens` survives as an optional `task.context_tokens` for the
existing context-limit filter.

### Mapping a runnable model to OpenRouter data

- Take the model part of the id (after the provider). For `claude-agent`, first
  replace the alias with its real model from the Claude CLI's stored catalog
  (`native_catalog`), for example `sonnet` → `claude-sonnet-5-5`.
- Normalize: lowercase, every run of characters outside `a-z0-9` becomes `-`.
- Normalize OpenRouter ids the same way, using the part after the creator
  (`anthropic/claude-sonnet-5.5` → `claude-sonnet-5-5`).
- Accept only an exact, unique match. Ambiguous or missing means unscored.
- An unscored model borrows the score of the newest scored model in its family.
  The family key is the normalized id with numeric segments removed
  (`gpt-6-3-sol` → `gpt-sol`). The borrowed price is the model's own price when
  OpenRouter lists one, otherwise the family member's. A model with no own score
  and no family score is not recommended and is reported as `unscored`.

### Ranking rule

```text
price(m) = (3 * input + output) / 4          # USD per million tokens
best     = max(score(m) for m in candidates)
floor    = best * RATIO[level]               # RATIO = (0.55, 0.82, 0.90)
ok       = [m for m in candidates if score(m) >= floor]
near     = [m for m in candidates
            if score(m) >= 0.97 * floor and price(m) <= 0.5 * min(price(x) for x in ok)]
order    = sorted(ok + near, key=(price, -score, -release, id))
effort   = ("low", "medium", "high")[level - 1], clamped to the model's variants
```

The floor is relative to the best runnable model, so level 3 means "close to the
most capable model you have", matching superpowers' wording. The ratios are
starting values, pinned by a golden test on today's scores. With all three plans
eligible, that test expects: level 1 → Haiku 5.5; level 2 → GPT-6.2 Sol (ties
Sonnet 5.5 on price, scores higher); level 3 → GPT-6.2 Sol through the
cost-benefit clause (57.3 against a floor of 58.1, at half of Opus 5.5's price).
With only the Claude plan eligible, which is today's reality: level 1 → Haiku 5.5,
level 2 → Sonnet 5.5, level 3 → Opus 5.5. The builder may tune the ratios to meet
these expectations; the expectations are the contract.

### Refresh of the OpenRouter data

One cache file, `openrouter-models.json`, in the state directory, shared by all
projects and sessions. It stores only what the rule needs: per model the
normalized id, family key, score, prices and `created`, plus `fetched_at`,
`attempt_at`, the failure count and the per-model unscored retry schedule.

1. **On demand only.** Checks happen when the router is asked; there is no timer.
2. **Never wait, except the first time.** A request answers from the cache. When
   a refresh is due, `index.js` starts `scripts/select.py --refresh` after
   answering: one bounded child process, killed on plugin cleanup. With no cache
   at all, the request itself fetches with a 5-second limit; if that fails, the
   answer is `no_selection: scores_unavailable` and the client keeps its model.
3. **A new usable model triggers a fetch.** If a model that passed step 4 of the
   data flow (a usable path and account) has no entry in the cache, a refresh is
   due. Models on skipped or unusable paths never trigger one.
4. **Unscored usable models retry on a widening interval:** 1 hour, 4 hours,
   12 hours, then 24 hours. After that they wait for the weekly ceiling like
   everything else, so a model that is never scored cannot cause daily fetches.
   The family score covers the gap.
5. **Weekly ceiling.** A refresh is due when `fetched_at` is older than 7 days.
6. **At most one fetch per 15 minutes,** whatever the trigger.
7. **Failures** keep the last good copy and back off from 5 minutes, doubling,
   to at most 1 hour.
8. **Replace only on sane data.** HTTPS only, no cross-host redirects, a 5 MB
   size cap, strict types (printable bounded ids, finite non-negative numbers).
   Reject a response that carries fewer than half the previous scored models.
9. **One fetcher at a time** through the existing storage lock.

Cost, per the lightweight rule: one GET of about 88 KB, typically once a week
plus a few times in the first day after a new usable model appears, never more
than once per 15 minutes, and one extra short-lived process only when a refresh
is due. No model calls. Account reads stay as they are today for usable paths
and drop to none for skipped paths.

Change from the conversation: a model disappearing from OpenCode does not
trigger a fetch, because removal needs no new data. (Builder's call; the user
can reverse it.)

### State and setup

- The state directory is created automatically by the existing storage helpers.
- `router.json` and `strategy-table.json` are no longer read. Existing copies are
  left untouched.
- Enabled or not is the project's onboarding answer, as today.
- `check_setup` (used by onboarding) reports `ready` when the cache holds scores;
  otherwise `setup_needed` with `scores_unavailable`. It does not read accounts.
  The onboarding preview reads the cache only and never fetches, because it runs
  before the user has answered. Only `apply` with `enabled: true` may run the
  first bounded fetch.
- The onboarding procedure's Smart Router question adds one line to its context:
  the router downloads public model scores from openrouter.ai about weekly and
  sends no credentials. The readiness lines change accordingly.
- Projects that were already enabled never see that question again. The first
  successful fetch on a machine (no cache existed before) therefore adds the same
  line, once, as the result's `notice`. No consent state and no re-ask.
  (Builder's call; the user can ask for a re-ask instead.)

### Result

Unchanged fields stay. Added: `level`, `alternatives` (up to two routes, same
shape as `route`), `score` and `score_source` on each route (`openrouter`,
`family`), and `attribution: "Scores: Artificial Analysis via OpenRouter"`.
The fallback notice text in `index.js` gains entries for `scores_unavailable`
and `unscored`.

### What is removed

`config/router.example.json`, `config/strategy-table.example.json`, the
strategy-table reading and validation in `runtime/selector.py`, `resolve_row`
and row keys in `runtime/routing.py`, the table half of
`runtime/routing_paths.py`, `strategy_id` and `allowed_strategy_ids`, the old
`profile` contract, and the README's "Configure routes" section (edited through
the `impeccable` skill, per AGENTS.md).

Kept: `config/routes.json`, all account, billing, overflow and quota readers,
the advisory invariants, the fallback notices and the context hook.

## Open decision for the user

**Paid-overflow rule.** Today only routes that prove paid overflow is blocked
can be picked, which in practice means Claude only. Under that rule the
cross-plan cost comparison (GPT-6.2 Sol against Opus 5.5) never happens.
Default in this design: keep the rule, so recommendations stay Claude-only
until ChatGPT and Go can prove overflow is blocked, and those paths are skipped
without account reads. The alternative is to treat `unknown` as acceptable for
ChatGPT and Go, which risks recommending a route that could bill extra usage;
it would also bring their account reads back and drop `overflow_unprovable`.

## Testing

- Golden ranking test on a synthetic fixture holding today's scores and
  prices, with the expectations above for both eligibility cases.
- Mapping tests: `sonnet` → `claude-sonnet-5-5` → OpenRouter
  `anthropic/claude-sonnet-5.5`; `openai/gpt-6.2-sol` direct; an ambiguous match
  is unscored; a new `gpt-6.3-sol` borrows Sol's score.
- Refresh tests with a fake clock and fake fetcher for every rule above,
  including the first-run wait, the 15-minute gap, backoff, the sanity
  rejection, and a never-scored model falling back to the weekly ceiling.
- Skipped paths: no account reader is called for ChatGPT or Go, and their
  models appear in `excluded` as `overflow_unprovable`.
- Onboarding: preview never fetches; apply may; the one-time notice appears
  only on the first fetch on a machine.
- Contract tests: `v: 1` and missing facts are rejected; the caller cannot
  inject a model list; the advisory invariants hold.
- No test touches the network or real accounts.

## Left to the builder

- Whether `ctx.model.list()` exposes the Claude alias's real model directly; if
  it does, prefer it over the native catalog.
- Module layout (a new `runtime/scores.py` is the natural home for fetch, cache
  and mapping) and how `index.js` passes the model list.
- Final ratio values, within the golden-test expectations.
