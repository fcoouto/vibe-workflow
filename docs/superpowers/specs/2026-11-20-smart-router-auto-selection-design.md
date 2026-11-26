# Smart Router automatic selection

Status: approved by the user, 20 November 2026. The ChatGPT limit signal is
gated on the spike described in its section.

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
- Balancing usage across subscriptions. Recorded separately as a deferred
  design: `2026-11-20-subscription-usage-balancing-design.md`.
- Pay-per-use API providers. Only the three subscription paths stay.
- User-editable weights, floors or overrides.
- Changing who launches work: the router stays advisory.

## Anti-goals

- Recommending a plan whose quota reading shows it at 100% (exhausted), where
  further use would draw on paid extra usage or be refused.
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
   `config/routes.json`. There is no path configuration. Paths without a
   signed-in account drop out through the existing `billing_unverified` check.
4. The existing billing and quota checks run first and drop routes that cannot
   be used now. They run before ranking so the floor comes from the best model
   the user can actually use. The overflow check is removed; see
   *Paid overflow and quota*.
5. Each remaining model gets a score and a price from the cached OpenRouter data
   (see *Mapping* and *Refresh*), then the context and caller constraints apply.
6. The ranking rule orders the candidates. The first is the pick; the next two
   are returned as `alternatives`. Dropped routes stay visible in `excluded`
   with their existing reasons.

### Task description

Superseded by `2026-11-23-router-levels-design.md`; the level rule below no longer applies.

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

Superseded by `2026-11-23-router-levels-design.md`; the ratios and order below no longer apply.

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
starting values, pinned by a golden test on today's scores. With the Claude and
ChatGPT plans both usable, that test expects: level 1 → Haiku 5.5; level 2 →
GPT-6.2 Sol (ties Sonnet 5.5 on price, scores higher); level 3 → GPT-6.2 Sol
through the cost-benefit clause (57.3 against a floor of 58.1, at half of Opus
5.5's price). With only the Claude plan usable: level 1 → Haiku 5.5, level 2 →
Sonnet 5.5, level 3 → Opus 5.5. The builder may tune the ratios to meet these
expectations; the expectations are the contract.

### Paid overflow and quota

Decided by the user: the paid-overflow setting never excludes a plan. A plan
with extra usage enabled (or unknown) still has its included quota, and it should
be used. Paid overflow does not affect quota eligibility or grant spending
authority.

- The quota check excludes a route (`quota_exhausted`) only on positive, fresh
  exhaustion evidence or an active ChatGPT pause. Claude's confirmed 5-hour
  and 7-day windows apply to the subscription path; a recognized model-scoped
  weekly limit applies only to matching models on that path. Claude and Go
  meter-based exclusions may lapse before reset if the reading ages out.
  The [balancing design](2026-11-20-subscription-usage-balancing-design.md#what-ships-today)
  records the reader and freshness limits.
- Work already running is not affected. The router only advises on new work, so
  the last task can finish its few remaining calls past 100%, as the user has
  observed providers allow.
- The overflow exclusion (`overflow_not_blocked`, `routing_observations.py:47`
  and `selector.py:85`) is removed. The overflow state is still read and is
  returned on each route as `paid_overflow` (`blocked`, `enabled` or `unknown`),
  for information only.
- ChatGPT uses its own stop signal; see *ChatGPT limit signal*.

### ChatGPT limit signal

Decided by the user: the 429 is the only stop signal for ChatGPT, and the Codex
login is assumed to be the same ChatGPT account as OpenCode's.

Why: OpenCode's ChatGPT connection exposes no quota data and no account
identity. Verified on 20 November 2026 with two real requests and the model
list. The response headers have no usage fields, the stream has only
moderation metadata and token counts, the stored credential holds only
encrypted tokens and a client ID, and the identity endpoint rejects the token
(HTTP 401). The 429 is documented by OpenAI for partner apps, is exact for
OpenCode's own connection, and also fires when the user has capped OpenCode's
share of the plan (for example at 50%).

- **Stop.** When a ChatGPT request through OpenCode fails with
  `subscription_sharing_usage_limit_exceeded` (HTTP 429), the plugin marks the
  ChatGPT plan as limited, and its routes are excluded as `quota_exhausted`.
  The error can arrive as an HTTP 429 before the stream opens, or as
  `response.failed` after streaming has begun. Detect the first through the
  `http.response` hook for the `openai` provider (clone the body before
  reading it). The plugin also listens for OpenCode's `provider.chatgpt-limit`
  error on `session.step.failed`. The event path is covered by synthetic tests,
  not a live limit failure.
- **Resume.** After a 429, the selector reads Codex once with
  `account/rateLimits/read` (zero tokens, one short process, the read the
  previous version shipped as `runtime/codex_rate_limits.py`). The mark lasts
  until the reset of the Codex window with the highest `usedPercent`. If Codex
  is missing, signed out or the read fails, the mark lasts one hour. After
  either, the next ChatGPT request decides: another 429 sets a new mark.
- **No other Codex reads.** Codex is not read on normal requests, and its
  percentages never stop routing on their own.
- **The mark is shared and persistent.** It is stored in the state directory,
  so every project and session sees it, and a restart does not forget it.
- **Assumption, stated.** If Codex is signed in to a different account, the
  only effect is a wrong resume time; the next request's 429 or success
  corrects it.

#### Spike before building the ChatGPT limit signal

Requested by the user: before this part is planned in detail or built, run one
bounded spike to look for something better than the 429 plus Codex resume. It
runs when implementation reaches this part, not before.

- **Questions.** Is there any source of ChatGPT plan usage, or of account
  identity, for OpenCode's ChatGPT connection, so the router could stop before a
  429 or drop the same-account assumption?
- **Leads to check.** OpenAI's Sign in with ChatGPT documentation for usage or
  identity endpoints added since 20 November 2026. The WebSocket transport (the
  account model list reports `prefer_websockets`, and `http.response` never sees
  WebSocket traffic) and whether its events carry rate limits. What OpenCode
  emits, in its session errors and events, when it handles
  `subscription_sharing_usage_limit_exceeded`. Whether Codex app-server,
  started with OpenCode's access token as documented (`env_key`), answers
  `account/rateLimits/read` for that token's account. Open-source OpenCode
  plugins or other partner tools that already show ChatGPT plan usage.
- **Bounds.** At most half a day of agent work and at most 10 tiny ChatGPT
  requests. Read-only: never refresh or rewrite OpenCode's tokens, never call
  ChatGPT's internal `backend-api` (OpenAI's partner docs say not to), and
  install nothing without the user's approval.
- **Exit.** A short written finding in this repository. Adopt a source only if
  it is documented, or observed consistently in at least two separate checks;
  then update this design and ask the user before planning it. Otherwise build
  the 429 plus Codex resume exactly as written above.

Research behind the decision (20 November 2026):

- **ChatGPT.** OpenCode signs in through ChatGPT's partner plan sharing (the
  reader checks `chatgpt-token-sharing`). For a partner app on a personal plan,
  using credits after the plan limit needs an explicit opt-in that is off by
  default, plus the app's limit set to 100%; automatic reload is a separate
  setting (help.openai.com articles 20001542 and 12642688). No API exposes these
  switches, so the state stays `unknown`.
- **OpenCode Go.** Extra Usage is off by default on Go and Go Plus and on by
  default on Go Teams; it draws on the Console balance
  (opencode.ai/docs/console/go). A live, zero-token read of
  `/api/experimental/usage/limits` on this machine returned only `workspace`,
  the three meters and an empty `budgets` list: no Extra Usage field. The state
  stays `unknown`.
- **Claude.** Unchanged: the native reader reports extra usage as enabled,
  disabled or unknown.

None of these settings matter for eligibility any more; they are recorded here
because they explain why `paid_overflow` is `unknown` for ChatGPT and Go.

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
   due. Models on unusable paths never trigger one.
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
is due. No model calls. Account reads stay as they are today: at most one
credential read per 15 minutes of use, shared by all three plans, plus Go's
provider and usage reads when Go is connected. A plan that is not connected
fails fast without extra calls.

**Removed models.** A model that disappears from OpenCode is out of the routing
engine on the very next request, because candidates come from the live model
list every time; it can never be recommended again. No fetch is needed for
that. Its retry-schedule entry is dropped from the cache at the next cache write.
Its OpenRouter score stays only as long as OpenRouter lists it, so it can still
lend its score to its family.

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

Added: the ChatGPT limit mark, and a Codex reset reader trimmed from the previous
version's `runtime/codex_rate_limits.py` (handshake plus
`account/rateLimits/read` only, bounded process, no auth-file reads), reusing
`runtime/processes.py`.

## Testing

- Golden ranking test on a synthetic fixture holding today's scores and
  prices, with the expectations above for both eligibility cases.
- Mapping tests: `sonnet` → `claude-sonnet-5-5` → OpenRouter
  `anthropic/claude-sonnet-5.5`; `openai/gpt-6.2-sol` direct; an ambiguous match
  is unscored; a new `gpt-6.3-sol` borrows Sol's score.
- Refresh tests with a fake clock and fake fetcher for every rule above,
  including the first-run wait, the 15-minute gap, backoff, the sanity
  rejection, and a never-scored model falling back to the weekly ceiling.
- Paid overflow and quota: routes with overflow `blocked`, `enabled` or
  `unknown` are all eligible below 100% and carry their `paid_overflow` state;
  a fresh 100% reading excludes the plan's routes as `quota_exhausted`; a stale
  or missing reading excludes nothing.
- Removed models: a model absent from the live list is never recommended, even
  with a cached score, and its retry entry is pruned.
- ChatGPT limit: a synthetic 429 with `subscription_sharing_usage_limit_exceeded`
  (both the HTTP and the `response.failed` form) marks the plan; its routes are
  excluded until the Codex reset time, or for one hour when Codex is
  unavailable; other 429s and other providers set no mark; Codex is never
  read without a mark. Codex is faked; no test touches a real account.
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
