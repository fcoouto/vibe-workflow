# Council Seat Rule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `vibe_council_roster` picks council seats by the user's seat rule when none are named, on Smart Router's shared selection core.

**Architecture:** Split `runtime/selector.py` `_select` into a shared candidate step and a final account recheck, with no change to any Smart Router answer. A council pick (`runtime/council.py` `pick`, `routing.seats`) reuses both, at level 3. The plugin adds a pick mode to the existing roster tool and shares the background refresh with `smart_router_select`.

**Tech Stack:** Python 3.11 standard library, Node ES modules, OpenCode V2 plugin API.

**Spec:** `docs/superpowers/specs/2026-11-21-council-seat-rule-design.md` (amends `docs/superpowers/specs/2026-11-21-vibe-council-design.md`).

## Global Constraints

- Advisory only: every answer keeps `admission`, `reservation`, `spend_permit` false; nothing launches.
- Standard library and Node built-ins only; modules under 1000 lines.
- `tests/test_selector.py` and the Smart Router plugin tests pass unchanged after Task 1.
- Frontier bar: `routing.RATIO[2]` (0.90) times the best usable score. Council level is 3.
- `count` is an integer 1 to 5, default 3. `seats` and `count` together are `invalid_request`.
- No fixed model id in the council skill, README or skill test.
- README edits go through the `impeccable` skill.
- Tests never reach the network (`VCW_TEST_HOME`, fake `fetch`).

## Review Focus

- A model listed through two plans, or a `-fast` tier, must not take two seats (Task 2 tests).
- The pick must not seat a route whose account changed between eligibility and the recheck (Task 3 test).
- An empty score cache with a failing download must answer a reason, not crash or hang (Task 3 test).
- A malformed or oversized pick answer from Python must never reach the agent as seats (Task 4 test).
- A pick in a project with Smart Router off or not onboarded must still work (Task 4 test).

---

### Task 1: Shared selection core

**Files:**
- Modify: `runtime/selector.py` (`_select`, about lines 88-148)
- Modify: `runtime/scores.py` (`_result`, `lookup`)
- Test: `tests/test_scores.py`, `tests/test_selector.py` (unchanged)

**Interfaces:**
- Produces: `_candidates(models, level, constraints, tokens, *, state_dir, source, fetch, fixed) -> dict` with keys `reason` (`None`, `no_models`, `no_eligible_route`, `scores_unavailable`), `excluded`, `candidates`, `kept` (count), `unscored`, `keys` (`{route: key}`), `creators` (`{route: creator or None}`), `had`, `cache`, `wanted` (set), `now`, `source`.
- Produces: `_confirm(ordered, pool, limit) -> list` (appends `account_changed` to `pool['excluded']`).
- Produces: `_refresh_flag(answer, pool) -> answer`.
- Produces: `scores.lookup(...)` results carry `creator`.

- [ ] **Step 1: Failing test** in `tests/test_scores.py`: `lookup(cache, 'gpt-6-2-sol', None)['creator'] == 'openai'`, and a family-borrowed result (`gpt-6-3-sol` from `gpt-6-2-sol`) has `creator == 'openai'`. Add `creator='openai'` to the exact dict in `test_unique_key_without_creator_resolves`.
- [ ] **Step 2:** Run `python3 -B -m unittest tests.test_scores`. Expected: FAIL (`KeyError: 'creator'`).
- [ ] **Step 3:** `_result` adds `creator=entry['creator']`. Split `_select` into the three helpers above; `_select` keeps its answers byte for byte. The creator per route is `scores.CREATOR.get(path)` or the lookup's `creator`. Candidate dicts stay as today (no `key` or `creator` fields), so Smart Router routes do not change.
- [ ] **Step 4:** Run `python3 -B -m unittest discover -s tests`. Expected: OK, with `tests/test_selector.py` unedited.
- [ ] **Step 5:** Commit `refactor: share the selection core for council picks`.

### Task 2: Seat order

**Files:**
- Modify: `runtime/routing.py`
- Test: `tests/test_routing.py`

**Interfaces:**
- Consumes: candidate dicts as built in Task 1 plus `key` and `creator`.
- Produces: `routing.seats(candidates) -> list`, the full seat order (one per identity).

- [ ] **Step 1: Failing tests** (`SeatsTest`), with a helper `c(route, key, creator, score, price=1, source='openrouter', released=0)`:
  - `test_one_per_lab_then_more_frontier`: Anthropic 64.6 and 59.1, OpenAI 62.1, OpenAI 57.3, Anthropic 54.5 → routes in order 64.6, 62.1, 59.1, 57.3, 54.5 with the bar at 58.14; three seats are the first three.
  - `test_below_frontier_only_after_frontier`: Anthropic 90 and 85, OpenAI 70 → 90, 85, 70 (OpenAI is below 81, so pass 1 has only Anthropic).
  - `test_unknown_creator_is_not_a_new_lab`: Anthropic 90 and 89, creator `None` 88 → 90, 89, 88.
  - `test_one_model_one_seat`: the same key on two routes keeps the lower price; `claude-haiku-5-5-20261001` and `claude-haiku-5-5` are one; `gpt-6-astra-fast` and `gpt-6-astra` are one; `gpt-6` and `gpt-6-2` are two; `gpt-6-3-sol` and `gpt-6-2-sol` are two.
  - `test_ties_use_the_shared_order_key`: equal scores order by price, then own score before `family`, then newer release, then route id.
- [ ] **Step 2:** Run `python3 -B -m unittest tests.test_routing`. Expected: FAIL (`seats` missing).
- [ ] **Step 3:** Implement `seats(candidates)`:

```text
identity = (creator, key without 8+ digit segments); a key that extends another
           candidate identity's key of the same creator by "-" plus words with no
           digits takes the shortest such identity
keep     = per identity, min by (price, -score, source == 'family', -released, route)
ranked   = keep sorted by (-score, price, source == 'family', -released, route)
frontier = ranked with score >= RATIO[2] * ranked[0].score
pass 1   = frontier, first per creator; creator None never qualifies
order    = pass 1 + [rest of ranked in ranked order]
```

- [ ] **Step 4:** Run `python3 -B -m unittest tests.test_routing`. Expected: OK.
- [ ] **Step 5:** Commit `feat: order council seats by the seat rule`.

### Task 3: Council pick in Python

**Files:**
- Modify: `runtime/council.py`, `scripts/select.py`
- Test: `tests/test_council.py`

**Interfaces:**
- Consumes: `selector._candidates`, `selector._confirm`, `selector._refresh_flag`, `routing.seats`.
- Produces: `council.pick(request, *, state_dir, source=None, fetch=None, now=None) -> dict`; CLI flag `--council-pick`. Answer: `{state: 'picked', reason, seats: [{provider, id, variant, score, score_source, creator, paid_overflow}], excluded, attribution, admission, reservation, spend_permit}` plus optional `refresh`.

- [ ] **Step 1: Failing tests** (`PickTest`, reusing the selector test's `body`/`Fetch`/`Facts` pattern with a warmed cache: Opus 80, Sonnet 76, Haiku 50, GPT-6.2 Sol 78):
  - `test_picks_one_per_lab_first`: models Opus, Sonnet (`claude-agent`), Sol (`openai`), count 3 → `[opus, sol, sonnet]`, `state 'picked'`, `reason None`, variant `high`, creators `anthropic`, `openai`, `anthropic`.
  - `test_fewer_seats`: count 5 with those three → three seats, `reason 'fewer_seats'`.
  - `test_exhausted_plan_skipped`: ChatGPT exhausted → no Sol seat; Sol's route in `excluded` with `quota_exhausted`.
  - `test_account_change_drops_seat`: `Facts.change = True` → `account_changed` in `excluded`.
  - `test_no_scores_answers_reason`: empty cache and `Fetch(None)` → `seats []`, `reason 'scores_unavailable'`; the fetch ran once with `scores.FIRST_TIMEOUT`.
  - `test_due_refresh_is_flagged`: a warmed cache at `NOW` asked at `NOW + scores.CEILING + 1` → `refresh` present.
  - `test_invalid_pick_requests`: `v` 2, count 0, 6, `True`, `'3'`, extra key, missing `models` → `ValueError`.
  - `test_cli_pick_failure_is_failed`: `scripts/select.py --state-dir <abs> --council-pick` with invalid stdin prints `{"state":"failed"}`.
- [ ] **Step 2:** Run `python3 -B -m unittest tests.test_council`. Expected: FAIL.
- [ ] **Step 3:** Implement `pick`: validate (`set(request) == {'v','count','models'}`, `v` is int 1, `count` int 1–5, not bool); `_candidates(models, 3, {}, 0, ...)`; attach `key`/`creator`; `routing.seats`; `_confirm(..., count)`; reasons as in the spec; `_refresh_flag`. `select.py`: `--council-pick` calls `council.pick`, and its error fallback is `{"state": "failed"}`.
- [ ] **Step 4:** Run `python3 -B -m unittest discover -s tests`. Expected: OK.
- [ ] **Step 5:** Commit `feat: pick council seats from the shared core`.

### Task 4: Pick mode in the roster tool

**Files:**
- Modify: `council.js`, `index.js`
- Test: `tests/council.test.js`, `tests/plugin.test.js`

**Interfaces:**
- Consumes: `--council-pick` answers from Task 3.
- Produces: `createRoster({ models, check, pick, providers })`, where `pick(entries, count, signal)` resolves the Python answer without `refresh`. Roster answers in pick mode: `{ ready, reason, seats: [{ seat, model, name, variant, status: 'ok', paid_overflow, candidates: [], score, creator }], attribution, admission, reservation, spend_permit }`.

- [ ] **Step 1: Failing tests** in `tests/council.test.js` (stub `pick`):
  - `{v:1}` sends count 3 and entries without `name`; three picked seats → `ready true`, seats named "Astra", "Opus", "Fable" from display names "GPT-6 Astra", "Opus 5.5", "Fable 5.2"; model `provider/id`; `score` and `creator` kept.
  - `{v:1,count:2}` sends 2; two seats whose names collide ("Sol", "Sol") → "Sol", "Sol 2".
  - `fewer_seats` → `ready false`, `reason 'fewer_seats'`, rows kept; zero seats with `scores_unavailable` → `ready false`, that reason, no rows.
  - Answers that do not line up (`state 'failed'`, a seat not in the list, a variant not in its variants, `paid_overflow 'x'`, non-finite score, more seats than count, unknown reason, a thrown `pick`) → `roster_unavailable`, no rows.
  - `{v:1,count:3,seats:[…]}`, `count` 0, 6, 2.5, `'3'` → `invalid_request`; `pick` not called.
  - Empty model list → `models_unavailable`.
- [ ] **Step 2: Failing tests** in `tests/plugin.test.js`: `{v:1}` spawns `select.py --council-pick` with `{"v":1,"count":3,"models":[…]}` and no names; a `refresh` key never reaches the output and starts one `--refresh` child; the pick works with the Smart Router preference off and with no settings file.
- [ ] **Step 3:** Run `node --test tests/council.test.js tests/plugin.test.js`. Expected: FAIL.
- [ ] **Step 4:** Implement: pick mode in `createRoster` (validation, names from the last alphabetic word of `name`, else `id`, made to match `SEAT`; de-duplicated case-insensitively with " 2", " 3"); schema `required: ["v"]` plus `count` (integer 1–5); description covers both modes. In `index.js`: operation `council_pick` (`--council-pick`, fallback `{state:"failed"}`, accepts `state: "picked"` with array `seats` and false authority fields); `refreshLater(refresh)` shared by `smart_router_select` and the pick.
- [ ] **Step 5:** Run `node --test tests/*.test.js` and `python3 -B -m unittest discover -s tests`. Expected: all pass.
- [ ] **Step 6:** Commit `feat: pick council seats when none are named`.

### Task 5: Procedure and documentation

**Files:**
- Modify: `skills/vibe-council/references/procedure.md`, `tests/council-skill.test.js`, `README.md`, `PRODUCT.md`, `docs/superpowers/specs/2026-11-21-vibe-council-design.md`

- [ ] **Step 1: Failing test:** the procedure test requires `'{"v":1}'`, `"count"`, `"fewer_seats"`, `"Artificial Analysis via OpenRouter"`, and asserts none of `gpt-6-astra`, `claude-agent/opus`, `claude-fable-5-2` appear.
- [ ] **Step 2:** Run `node --test tests/council-skill.test.js`. Expected: FAIL.
- [ ] **Step 3:** Replace "Default roster" with the seat rule and the two calls; keep the stop/show/dispatch rules; add the `fewer_seats` re-pick. README "Ask the council" through `impeccable`; `PRODUCT.md` line; a one-line pointer in the merged design's roster section.
- [ ] **Step 4:** Run all tests, `python3 -B scripts/validate.py`, and the package export check. Expected: pass.
- [ ] **Step 5:** Commit `docs: describe rule-picked council seats`.

### Task 6: Live check (authorized by the user)

- [ ] **Step 1:** Export the package; run a private OpenCode 2.2.6 server (`--standalone`, own config and state) in a throwaway one-commit repository that is not onboarded, as in the merged council live check.
- [ ] **Step 2:** Run `/vibe-council` on a tiny topic with no named seats. Record the picked seats, scores, labs and paid overflow, and whether `provider/id#variant` dispatch ran.
- [ ] **Step 3:** If `#variant` dispatch fails, change the pick to `variant: null`, with a test, and re-run.
- [ ] **Step 4:** Add the record to `docs/development.md` ("Vibe council: what is known"); remove the throwaway folders. Commit `docs: record the council seat pick live check`.
