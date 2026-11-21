# Council seat rule

Status: draft for review, 21 November 2026. Amends
[the vibe council design](2026-11-21-vibe-council-design.md).

## Intent

The council shipped with a fixed default roster in its procedure: Astra
(`openai/gpt-6-astra`), Opus (`claude-agent/opus`) and Fable
(`claude-agent/claude-fable-5-2`). The user's rule for council seats says the list
is never hard-set in a file. The fixed list already broke once: the live check
found its first Astra id missing from the plan (`docs/development.md`, "Vibe
council: what is known"). It also seats two Anthropic models.

This change replaces the fixed roster with seats picked by the rule, at council
time. The picking reuses Smart Router's selection code and score cache, so a fix
in Smart Router also fixes the council.

## The seat rule (decided by the user)

- **Frontier first.** A frontier model is one whose score clears Smart Router's
  level-3 bar: `RATIO[2]` (0.90) times the best usable score.
- **Capability first, then labs.** Seats fill in this order:
  1. frontier models, one per lab (creator), strongest first;
  2. more frontier models, even from a lab already seated, strongest first;
  3. the highest scores below the frontier.

  Different labs are a recommendation, not a blocker.
- **Chosen at council time** from Smart Router's score cache and OpenCode's live
  model list, after the same account and quota checks.
- **One model, one seat.** A model reachable through two plans (Haiku through
  Claude and through Go) takes at most one seat.
- **Named seats win.** Seats the user names are checked as today and never
  substituted.

## Goals

- `vibe_council_roster` picks the seats when the user names none.
- One shared selection core in `runtime/selector.py`, used by Smart Router and
  by the council pick.
- No fixed model ids anywhere in the council skill, README or tests.

## Non-goals

- Mixing named and picked seats in one roster. The user names every seat or
  none; to change one picked seat, the user names the full roster.
- Constraints on the pick (allowed or excluded models, efforts, harnesses).
- Changing Smart Router's per-task ranking, its answers, or its notices.
- Recording the ChatGPT limit pause outside Smart Router projects (the merged
  design accepts that gap; a limited seat fails at dispatch as a member failure).

## Anti-goals

- A second copy of any selection step (model reduction, routes per plan, account
  and quota checks, score cache, first download, refresh rules, name mapping,
  family borrowing, final account recheck).
- A picked seat the account and quota checks did not pass.
- A silent swap of a seat the user named.

## Approaches

1. **A pick mode in `vibe_council_roster` (chosen).** One council tool. The pick
   answers in the same row shape as the check, so the procedure's stop, show and
   dispatch rules stay as they are.
2. **A second tool, `vibe_council_seats`.** Rejected: one more tool to register,
   describe and test for the same rows.
3. **The coordinator picks from `smart_router_select`.** Rejected: that tool is
   gated on the Smart Router preference, returns one pick plus two alternatives
   ordered cheapest first, and knows nothing about labs.

## Design

### Shared core

Split `_select` in `runtime/selector.py`, without changing any Smart Router
answer, into:

- `_candidates(models, level, constraints, tokens, *, state_dir, source, fetch,
  fixed)`: everything up to the scored candidate list. That covers routes per
  plan and de-duplication by route id, account acquisition and the clock re-read,
  eligibility (`billing_unverified`, `quota_exhausted`, the ChatGPT pause), the
  native catalog, the score cache with the first download, name mapping, family
  borrowing, and the `unscored`, `context_limit` and `constraint` exclusions. It
  returns either an early no-selection reason (`no_models`, `no_eligible_route`,
  `scores_unavailable`) with its exclusions, or the candidates, the exclusions,
  the unscored count, the matched key per route, and the refresh data (`had`,
  `wanted`, the cache).
- `_confirm(ordered, source, fixed, limit)`: the final account recheck. It drops
  `account_changed` routes into the exclusions and stops at `limit`.
- `_refresh_flag(answer, had, cache, wanted, now)`: adds the internal
  `refresh: {wanted}` key when `scores.due` says so.

`_select` becomes `_candidates`, `routing.order`, `_confirm(..., 3)`, the reason
and the answer. `tests/test_selector.py` passes unchanged and is the proof.

The creator of a matched score comes from the shared lookup: `scores.lookup`
also returns the matched entry's `creator`. Smart Router's route output stays as
it is; only the council reads the creator.

On the plugin side, the background refresh in `smart_router_select` becomes one
helper, `refreshLater(refresh)`, which both tools call. The model reduction
(`models()`) and the Python child (`runRouter`) are already shared.

### Council pick, Python

`council.pick(request, *, state_dir, source=None, fetch=None, now=None)` in
`runtime/council.py`, reached by `scripts/select.py --council-pick`.

- Request: `{"v": 1, "count": N, "models": [...]}`. `count` is 1 to 5; `models` is
  plugin-supplied, as for Smart Router.
- Calls `_candidates(models, 3, {}, 0, ...)`. A council is always level 3, so each
  candidate's effort is `routing.effort_for(3, variants)`: `high`, clamped to the
  model's variants, or null when it lists none.
- **Identity**: the creator plus the matched key with date segments (8 or more
  digits) removed. The creator is the plan's hint (`scores.CREATOR`: Anthropic
  for Claude, OpenAI for ChatGPT) or, for Go, the matched entry's creator. A
  family-borrowed score never merges two models: `gpt-6-3-sol` keeps its own
  identity while it borrows `gpt-6-2-sol`'s score. A tier of a model is the same
  model (builder's call, narrowed by the final review): a key that extends
  another candidate's key from the same creator with words that have no digits,
  and has the same score (`gpt-6-astra-fast` at Astra's score), takes that key's
  identity. A sibling with its own score (`gpt-6-mini`, `gpt-6-pro`) and a step
  in digits (`gpt-6` and `gpt-6-2`) stay apart. Within one identity, the
  strongest candidate is kept, then the one Smart Router's own order key ranks
  first (price, own score before borrowed, newest release, route id).
- Orders with `routing.seats(candidates)`, then `_confirm(ordered, ..., count)`.
- Answer: `{state: "picked", reason, seats: [...], excluded, attribution,
  admission: false, reservation: false, spend_permit: false}`, plus the internal
  `refresh` key when due. Each seat has `provider`, `id`, `variant` (the effort),
  `score`, `score_source`, `creator` and `paid_overflow`. `reason` is null when
  `count` seats were found, `fewer_seats` when at least one was, and otherwise
  the shared no-selection reasons (`no_models`, `no_eligible_route`,
  `scores_unavailable`, `unscored`, `no_recommendation`).
- Any error answers `{"state": "failed"}`, as the roster check does.

### `routing.seats(candidates)`

```text
ranked   = one candidate per identity, strongest first; ties use Smart
           Router's order key
frontier = ranked items with score >= RATIO[2] * best score
pass 1   = frontier items, at most one per creator; an unknown creator never
           counts as a new lab
rest     = ranked items not in pass 1, strongest first (the remaining frontier
           comes before everything below it)
order    = pass 1 + rest
```

### Council pick, plugin

`vibe_council_roster` accepts either the existing check request
`{v: 1, seats: [...]}` or a pick request `{v: 1}` / `{v: 1, count: N}`, with
`count` from 1 to 5 and 3 by default. Sending both `seats` and `count` is
`invalid_request`. The input schema drops `seats` from `required` and adds
`count`.

In pick mode the plugin:

1. Takes the live list from `models()`. An empty list answers
   `models_unavailable`.
2. Sends the list without display names to `runRouter(..., "council_pick")`, and
   starts `refreshLater` with any `refresh` key.
3. Accepts only an answer that lines up with the list: state `picked`, at most
   `count` seats, each `provider/id` in the list, `variant` null or one of its
   variants, a known `paid_overflow`, a finite `score`. Anything else answers
   `roster_unavailable`.
4. Builds the usual rows (`seat`, `model`, `name`, `variant`, `status: "ok"`,
   `paid_overflow`, `candidates: []`), each with `score` and `creator` added.
   The seat name is the last alphabetic word of the display name ("Opus 5.5"
   gives "Opus"), or of the id when there is none, with " 2" or " 3" added when
   two seats would share a name.
5. Answers `ready: true` when it found `count` seats. With fewer, `ready` is false
   and `reason` is `fewer_seats`, or the Python no-selection reason when there
   are none. Every answer keeps the false authority fields and adds
   `attribution`.

The pick is never gated on the Smart Router preference, like the check. Its
first call in a project with no score cache may download the public scores once,
as Smart Router's first call does, with the same 5-second limit.

### Procedure

The "Default roster" table goes. In its place:

- **Seats the user names**: check them with `{"v":1,"seats":[…]}`, exactly as
  today.
- **No named seats**: call `{"v":1}`, or `{"v":1,"count":N}` when the user asks for
  a size. The answer is already checked.
- In both cases the existing rules hold: stop and ask when `ready` is false, show
  the roster in the message that starts the run, and dispatch only the exact
  `model` and `variant` from a `ready` answer. A picked roster also shows each
  seat's score and lab, and credits "Scores: Artificial Analysis via
  OpenRouter".
- With `fewer_seats`, show the seats found and ask whether to run with them or
  name the rest. To run with them, call `{"v":1,"count":<found>}` (builder's
  call). The pick is deterministic on the same facts, so it returns the same
  seats as a `ready` answer unless an account changed in between, and the "only
  from `ready`" rule stays whole.

### Documentation

- README "Ask the council" (through `impeccable`): drop the fixed members; one
  line on the rule; the first council in a project may download the same public
  scores Smart Router uses.
- `PRODUCT.md`: the same one-line change where it names the roster.
- The merged council design: a one-line pointer to this amendment where it
  describes the default roster.
- `docs/development.md`: the live record for the pick, after it is observed.

## Testing

- Behavior preservation: `tests/test_selector.py` and the plugin's Smart Router
  tests pass unchanged after the split, before any pick code is added.
- `routing.seats` with fixtures: two Anthropic frontier models and one OpenAI
  frontier model give one Anthropic, one OpenAI, then the second Anthropic.
  Below-frontier models come only after the frontier runs out. An unknown
  creator does not take a pass-1 place.
- Identity: Haiku through Claude and Go yields one seat; a dated alias and its
  base yield one seat; `gpt-6-astra-fast` and `gpt-6-astra` yield one seat;
  `gpt-6` and `gpt-6-2` stay two; `gpt-6-3-sol` borrowing from `gpt-6-2-sol`
  keeps its own seat.
- Live check, only with the user's authorization (it runs the seats' models): a
  `/vibe-council` with no named seats in a throwaway project, as in the merged
  live check. It records the picked seats and the first dispatch with
  `provider/id#variant`, which no live check has seen yet. If `#variant`
  dispatch fails live, picks fall back to `variant: null`.
- `council.pick` with fake facts and cache: an exhausted plan is skipped; an
  account change at the recheck drops the seat; `fewer_seats`; no scores and no
  network answers `scores_unavailable`; the first download runs once; a due
  refresh returns `refresh`; bad requests answer `failed`.
- Plugin: `{v:1}` picks three; `count` is honoured; `seats` plus `count` is
  invalid; names and their de-duplication; an answer that does not line up is
  `roster_unavailable`; `refresh` never reaches the output and starts one
  background refresh; works with Smart Router off and not onboarded.
- Skill test: the procedure names the pick call and no fixed model id.

## Left to the builder

- Exact helper names and the tuple `_candidates` returns, within the split above.
- Wording of the procedure, the tool description and the README line, within
  the rules above.
