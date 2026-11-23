# Smart Router Levels and Stakes-Sized Reviews Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Anchor Smart Router's three levels on the top three labs in the public catalog, keep exact-plan implementation at level 1, and size design, plan and diff reviews to the stakes through rubric definitions and skill-load notes.

**Architecture:** `runtime/scores.py` computes the anchor from the cached catalog; `runtime/routing.py` turns it into three bars and applies the new task-level exceptions; `runtime/selector.py` passes the anchor and falls back to the strongest candidate, marked `below_level`, when nothing clears. `index.js` carries the rubric definitions and a below-level notice; `src/skill-advice.js` carries the two review notes.

**Tech Stack:** Python 3.11+ standard library (`unittest`), JavaScript ES modules (`node --test`).

**Spec:** `docs/superpowers/specs/2026-11-23-router-levels-design.md` and `docs/superpowers/specs/2026-11-23-stakes-sized-reviews-design.md`. Read both before any task.

## Global Constraints

- Standard library only; each Python module within 1000 lines; `runtime/` stays flat (single-level `runtime.X` imports).
- Input contract stays `v: 2` with the same fields and enums.
- `ROUTER_NOTE` and `MODEL_NOTE` in `index.js` stay byte-for-byte unchanged.
- No model names in any rule, description or note text; levels and facts only.
- The whole `smart_router_select` description stays under 2048 characters.
- Bars: level 3 = A, level 2 = 0.80 A, level 1 = 0.55 A; A = third-highest per-lab best own score.
- Order unchanged: cheapest blended price first, then higher score, own score before family, newer release, route id; near clause unchanged (score at least 0.97 of the bar and price at most half the cheapest qualifying price).
- Selection never launches, admits, reserves or grants spend; `admission`, `reservation`, `spend_permit` stay `false`.
- Commands: `python3 -B -m unittest discover -s tests -t .`, `node --test "tests/**/*.test.js"`, `python3 -B scripts/validate.py`.

## Review Focus

- A scored alias entry (`~anthropic/claude-fable-latest`) or an `openrouter/*` entry must not count as a separate lab; otherwise one lab fills two of the three anchor slots. Test in Task 2.
- A cache with fewer than three scored labs (small or partial catalogs) must still yield an anchor: the lowest of the labs present. Test in Task 2.
- Caller constraints (`allowed_harnesses`, `allowed_models`) that leave only below-bar candidates must return the strongest of those, marked `below_level`, never `no_selection`. Test in Task 3.
- A below-level answer whose final account recheck drops every route must keep today's empty-result reason, never `selected` with a null route. Test in Task 3.
- A second below-level answer at the same level in one session must not repeat the notice, even after a normal pick in between; a different level must. Test in Task 5.
- With nothing qualifying, the near clause must not admit a cheap candidate just below the bar as an unmarked pick. Test in Task 2.

---

### Task 1: Exact-spec exceptions in the task level

**Files:**
- Modify: `runtime/routing.py` (`task_level`)
- Test: `tests/runtime/test_routing.py`

**Interfaces:**
- Produces: `routing.task_level(work_kind: str, task: dict) -> int`, same signature and validation.

- [ ] **Step 1: Write the failing test** `test_exact_spec_exceptions` and update `test_level_is_max_of_base_and_facts`:

```python
def test_exact_spec_exceptions(self):
    level = routing.task_level
    self.assertEqual(level('implementation', dict(FACTS, files='3+')), 1)
    self.assertEqual(level('implementation', dict(FACTS, risk='critical')), 1)
    self.assertEqual(level('implementation', dict(FACTS, risk='shared', files='3+')), 1)
    self.assertEqual(level('testing', dict(FACTS, risk='critical')), 1)
    self.assertEqual(level('implementation', dict(FACTS, files='cross-system')), 3)
    self.assertEqual(level('implementation', dict(FACTS, spec='prose', risk='critical')), 3)
    self.assertEqual(level('implementation', dict(FACTS, spec='prose', files='3+')), 2)
    self.assertEqual(level('review', dict(FACTS, risk='critical')), 3)
    self.assertEqual(level('review', dict(FACTS, files='3+')), 2)
    self.assertEqual(level('writing', dict(FACTS, risk='critical')), 3)
```

In `test_level_is_max_of_base_and_facts`, the two lines for `implementation` with `files='3+'` and `risk='critical'` now expect 1.

- [ ] **Step 2: Run** `python3 -B -m unittest tests.runtime.test_routing -v`. Expected: FAIL on the new assertions.

- [ ] **Step 3: Implement in `task_level`.** When `spec` is `exact`: `files: 3+` counts as level 1 for every work kind; for `implementation` and `testing`, `risk` counts as level 1. `cross-system` and every other fact keep their index + 1. Validation is unchanged.

- [ ] **Step 4: Run** the same command. Expected: PASS.

- [ ] **Step 5: Commit** `feat: keep exact-plan implementation at level 1 unless cross-system`.

### Task 2: Catalog anchor and level bars

**Files:**
- Modify: `runtime/scores.py` (add `anchor`), `runtime/routing.py` (`BAR`, `FRONTIER`, `bars`, `order`, `strongest`, `seats`), `runtime/selector.py:170` (pass the anchor)
- Test: `tests/runtime/test_scores.py`, `tests/runtime/test_routing.py`

**Interfaces:**
- Consumes: the cache dict from `scores.load` / `parse_openrouter` (`models` entries with `creator`, `score`).
- Produces:
  - `scores.anchor(cache: dict) -> float | None`: per lab, the best own score; lab = `creator` with a leading `~` removed; entries whose creator is `openrouter` skipped; returns the third-highest lab best, or the lowest lab best when fewer than three labs are scored, or `None` when nothing is scored.
  - `routing.BAR = (0.55, 0.80, 1.0)`, `routing.FRONTIER = 0.90` (council seats only); `RATIO` is removed.
  - `routing.bars(anchor: float) -> tuple[float, float, float]`.
  - `routing.order(candidates: list, level: int, anchor: float) -> list`: floor = `bars(anchor)[level - 1]`; `ok`, `near` and sort as today, except that when `ok` is empty the result is `[]` (the near clause only widens a non-empty `ok`).
  - `routing.strongest(candidates: list) -> list`: sorted by the existing `_strongest_first`.
  - `routing.seats` uses `FRONTIER` where it used `RATIO[2]`; behaviour unchanged.

- [ ] **Step 1: Write the failing tests.**

In `tests/runtime/test_scores.py`:

```python
def test_anchor_is_the_third_lab_best(self):
    cache = parse(model('a/x-1', 70), model('a/x-2', 65), model('b/y', 62.1), model('c/z', 61.4),
                  model('d/w', 50), model('~a/x-latest', 99), model('openrouter/auto', 99))
    self.assertEqual(scores.anchor(cache), 61.4)
    newer = parse(model('a/x-1', 80), model('b/y', 62.1), model('c/z', 61.4), model('d/w', 50))
    self.assertEqual(scores.anchor(newer), 61.4)
    entrant = parse(model('a/x-1', 70), model('b/y', 62.1), model('c/z', 61.4), model('e/v', 63))
    self.assertEqual(scores.anchor(entrant), 62.1)

def test_anchor_with_fewer_labs(self):
    self.assertEqual(scores.anchor(parse(model('a/x', 70), model('b/y', 62))), 62)
    self.assertIsNone(scores.anchor(parse(model('a/x', None))))
```

Note: the `~a` entry scores 99; stripped to lab `a` it raises `a`'s best, never adds a fourth lab, so the anchor stays 61.4 (write the assertion exactly as above).

In `tests/runtime/test_routing.py`, set `ANCHOR = 61.4`, change `pick(names, level)` to call `routing.order(candidates(names), level, ANCHOR)`, and replace the two golden tests:

```python
def test_golden_two_plans(self):
    names = list(PLANS)
    self.assertEqual([pick(names, level) for level in (1, 2, 3)], ['haiku', 'terra', 'fable-5.2'])

def test_golden_claude_only(self):
    self.assertEqual([pick(CLAUDE, level) for level in (1, 2, 3)], ['haiku', 'sonnet', 'fable-5.2'])

def test_bars_follow_the_anchor(self):
    self.assertEqual(routing.bars(61.4), (61.4 * 0.55, 61.4 * 0.80, 61.4))

def test_nothing_clears_returns_empty_and_strongest_orders_by_score(self):
    low = candidates(['luna', 'haiku'])
    self.assertEqual(routing.order(low, 2, ANCHOR), [])
    self.assertEqual([c['route'] for c in routing.strongest(low)], ['haiku', 'luna'])

def test_near_clause_never_stands_alone(self):
    free = dict(route='free', score=0.98 * routing.bars(100)[0], price=0, released=0)
    self.assertEqual(routing.order([free], 1, 100), [])
```

Rewrite `test_near_clause_needs_both_conditions` with `floor = routing.bars(100)[0]` and pass `100` as the anchor to `order`. In every other `order` call in the file (tie-break and borrowed-score tests), pass the highest candidate score in that test as the anchor, so every candidate clears level 1 and the test still checks only ordering.

- [ ] **Step 2: Run** `python3 -B -m unittest tests.runtime.test_scores tests.runtime.test_routing -v`. Expected: FAIL (`anchor`, `bars`, `strongest` missing; golden picks differ).

- [ ] **Step 3: Implement** the interfaces above. In `runtime/selector.py:170`, call `routing.order(pool['candidates'], level, scores.anchor(pool['cache']))`, so the suite stays green before Task 3.

- [ ] **Step 4: Run** `python3 -B -m unittest discover -s tests -t .`. Expected: PASS, council and selector tests unchanged.

- [ ] **Step 5: Commit** `feat: anchor level bars on the top three labs in the catalog`.

### Task 3: Selector uses the anchor and falls back below the level

**Files:**
- Modify: `runtime/selector.py` (`result`, `_select`)
- Test: `tests/runtime/test_selector.py`

**Interfaces:**
- Consumes: `scores.anchor`, `routing.order(candidates, level, anchor)`, `routing.strongest` (Task 2).
- Produces: every answer from `selector.select` carries `below_level: bool` (default `False` in `result`); `True` only when candidates existed, none cleared the bar (or `scores.anchor` returned `None`), and the strongest were returned instead.

- [ ] **Step 1: Write the failing tests.** Add a body with three strong labs:

```python
BODY3 = body(("anthropic/claude-fable-5-2", 10, 8, 40, 64.6), ("openai/gpt-6-2-astra", 20, 8, 40, 62.1),
             ("google/gemini-4-neon", 30, 4, 20, 61.4), ("anthropic/claude-haiku-5-5", 40, 0.1, 0.5, 36.5),
             ("z-ai/glm-5-3-flash", 50, 0.03, 0.11, 27.8))
```

Tests (use `Fetch(BODY3)`, `work_kind="implementation"`, `TASK`):
- `test_cheap_model_below_the_bar_is_not_picked`: models `[model("opencode-go", "glm-5.3-flash"), model("opencode-go", "claude-haiku-5-5")]` → `route["model"] == "claude-haiku-5-5"`, `below_level is False`, and `glm-5.3-flash` not among the alternatives.
- `test_nothing_at_the_level_returns_the_strongest_marked`: models `[model("opencode-go", "glm-5.3-flash")]` → `state == "selected"`, `route["model"] == "glm-5.3-flash"`, `below_level is True`, `level == 1`.
- `test_constraints_leaving_only_low_candidates_fall_back`: models glm and haiku, `constraints=dict(allowed_models=["glm-5.3-flash"])` → selected glm, `below_level is True`.
- `test_below_level_recheck_failure_keeps_the_empty_reason`: one-model case with `self.source.change = True` → `state == "no_selection"`, `route is None`, `reason == "no_eligible_route"`, `below_level is False`.
- In `test_selects_cheapest_qualifying_and_two_alternatives`, assert `result["below_level"] is False`.

- [ ] **Step 2: Run** `python3 -B -m unittest tests.runtime.test_selector -v`. Expected: FAIL.

- [ ] **Step 3: Implement.** In `_select`: `anchor = scores.anchor(pool['cache'])`; `ordered = routing.order(pool['candidates'], level, anchor)` when `anchor` is not `None`, else `[]`; if `ordered` is empty and candidates exist, `ordered = routing.strongest(pool['candidates'])` and `below = True`; confirm as today; `below_level=below and bool(chosen)` in the answer. Add `below_level=False` to `result`'s defaults.

- [ ] **Step 4: Run** `python3 -B -m unittest discover -s tests -t .`. Expected: PASS (council included).

- [ ] **Step 5: Commit** `feat: recommend the strongest model, marked below_level, when nothing reaches the level`.

### Task 4: Date-suffix score matching

**Files:**
- Modify: `runtime/scores.py` (`lookup`)
- Test: `tests/runtime/test_scores.py`

**Interfaces:**
- Produces: `scores.lookup(cache, key, creator)` unchanged in signature. When the exact entry is missing or unscored, it first looks for a unique scored entry (same creator when `creator` is given) whose key is `key` plus one `-` and four digits that read as a month and day (`MMDD`, month 01-12, day 01-31). Found: score from it, `score_source='openrouter'`, price from the exact entry when that entry exists, otherwise from the dated one. Not found or ambiguous: the existing family fallback, unchanged.

- [ ] **Step 1: Write the failing test** `test_date_suffixed_catalog_entry_scores_the_plain_name`:

```python
cache = parse(model('deepseek/deepseek-v4.1-flash', None, prompt='0.00000005', completion='0.0000002'),
              model('deepseek/deepseek-v4.1-flash-1017', 32.6, prompt='0.00000014', completion='0.00000063'),
              model('qwen/qwen3.8-max-0902', 33.7), model('meta/muse-spark-1.3', 36.6),
              model('x/twin-0902', 40), model('x/twin-1017', 41))
found = scores.lookup(cache, 'deepseek-v4-1-flash', None)
self.assertEqual((found['score'], found['score_source'], found['price']),
                 (32.6, 'openrouter', scores_price(0.05, 0.2)))
self.assertEqual(scores.lookup(cache, 'qwen3-8-max', None)['score'], 33.7)
self.assertIsNone(scores.lookup(cache, 'muse-spark-1-3-contributor', None))
self.assertIsNone(scores.lookup(cache, 'twin', None))
ctx = parse(model('x/long-8192', 50))
self.assertIsNone(scores.lookup(ctx, 'long', None))
```

Today's effect: DeepSeek V4.1 Flash (32.6) and Qwen3.8 Max (33.7, blended price about 3) stay below the level-1 bar and outside the near clause, so no pick changes.

- [ ] **Step 2: Run** `python3 -B -m unittest tests.runtime.test_scores -v`. Expected: FAIL.
- [ ] **Step 3: Implement** in `lookup`, before the family fallback.
- [ ] **Step 4: Run** `python3 -B -m unittest discover -s tests -t .`. Expected: PASS.
- [ ] **Step 5: Commit** `fix: score models whose catalog entry carries a date suffix`.

### Task 5: Rubric definitions and the below-level notice

**Files:**
- Modify: `index.js` (`description`, the notice branch in `smart_router_select.execute`)
- Test: `tests/plugin/plugin.test.js`

**Interfaces:**
- Consumes: `below_level` and `level` on the selector answer (Task 3).
- Produces: `description` = today's text + `" "` + the definitions paragraph copied verbatim from the stakes design's "Texts" section (the block after "Tool description, appended after the rubric"). Notice for `state === "selected" && below_level === true`: cause `` `below_level_${level}` ``, message exactly `` `vibe-workflow: No model on your plans reaches this task's level ${level}; Smart Router recommended the strongest available. Tell your human partner.` ``, deduplicated per session per cause like failure notices. A selected answer without `below_level` clears only the session's failure causes; `below_level_*` causes stay seen for the whole session. A below-level answer clears nothing.

- [ ] **Step 1: Write the failing tests.**
  - In `the tool describes its input and rubric`: assert `tool.description.length < 2048`, `/critical = a mistake could lose real money/`, `/cross-system = changes in more than one repository or deployed service that must stay compatible with each other/`, `/To review a design or plan, use work_kind planning/`, and no match for `/haiku|opus|sonnet|fable|astra|terra|luna|gemini/i`.
  - New `below-level answers get one notice per session and level`: with the `answer.json` fixture used by `notices deduplicate per session…`, write `{state: "selected", reason: null, below_level: true, level: 3, route: {provider: "invented", model: "invented", effort: "high"}, admission: false, reservation: false, spend_permit: false}`; first call → notice equals the message above with `level 3`; second call same session → `null`; a normal selected answer (no `below_level`) in between, then level 3 again → still `null`; switch to `level: 2` → notice; a different session → notice. The existing `notices deduplicate…` test must still pass unchanged.
- [ ] **Step 2: Run** `node --test tests/plugin/plugin.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** in `index.js`; keep `ROUTER_NOTE` and `MODEL_NOTE` untouched.
- [ ] **Step 4: Run** `node --test "tests/**/*.test.js"`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: define the task facts and announce below-level picks`.

### Task 6: Design and plan review notes

**Files:**
- Modify: `src/skill-advice.js` (`SKILL_NOTES`)
- Test: `tests/plugin/skill-advice.test.js`, `tests/plugin/pr-hook.test.js`

**Interfaces:**
- Produces: a `brainstorming` entry with `gate: "onboarded"` whose note is the stakes design's `brainstorming` note verbatim (its last sentence is `OVERRIDE`); the `writing-plans` note gains the stakes design's added step verbatim, inserted after "never as a silent move. " and before `OVERRIDE`, followed by one space (the step ends "must decide." and `OVERRIDE` follows after a single space).

- [ ] **Step 1: Write the failing tests.**
  - `skill-advice.test.js`: add a `DESIGN` constant with the exact brainstorming note; new test mirroring the writing-plans one: onboarded → `skillNote("superpowers:brainstorming") === DESIGN` and `skillNote("brainstorming") === DESIGN`; not onboarded or failing → `null`. Update `PLAN` with the added step. Remove `"brainstorming"` from the two "returns null" loops; use `"brainstorming-old"` instead.
  - Both notes: assert no match for `/haiku|opus|sonnet|fable|astra|terra|luna|gemini/i`.
  - `pr-hook.test.js` line with `superpowers:brainstorming` and `untouched()`: use `superpowers:systematic-debugging`; add one assertion that an onboarded `superpowers:brainstorming` load gets a second content part starting `\n\nvibe-workflow: your human partner asks that the design check match the stakes`.
- [ ] **Step 2: Run** `node --test tests/plugin/skill-advice.test.js tests/plugin/pr-hook.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** in `src/skill-advice.js`.
- [ ] **Step 4: Run** `node --test "tests/**/*.test.js"`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: size design and plan reviews to the stakes`.

### Task 7: Docs, package and injection check

**Files:**
- Modify: `docs/development.md`, `docs/superpowers/specs/2026-11-20-smart-router-auto-selection-design.md`, possibly `tests/tooling/opencode-verified.json` (written by the check)

- [ ] **Step 1:** In `docs/development.md`, add a short "Levels and reviews" paragraph pointing to both specs and naming `scores.anchor`, `routing.bars` and `below_level`. In the auto-selection design, add one line to the "Task description" section (the task-level rule) and one to the "Ranking rule" section: superseded by `2026-11-23-router-levels-design.md`.
- [ ] **Step 2: Run** `python3 -B scripts/validate.py`, `python3 -B -m unittest discover -s tests -t .`, `node --test "tests/**/*.test.js"`, `python3 -B scripts/package.py --output <new path>`, `python3 -B scripts/validate.py --root <new path>`. Expected: all pass.
- [ ] **Step 3: Commit** `docs: record the level anchor and stakes-sized reviews`.
- [ ] **Step 4: Run** `python3 -B scripts/live_check.py --injection` (pre-authorized by `AGENTS.md`: this plan changes the skill-load notes, which are injected notes, though `ROUTER_NOTE` and `MODEL_NOTE` stay unchanged). Its shell probe covers the `execute.after` path the skill notes ride on; it does not read the new texts, which Task 8 covers. Expected: every model and the shell probe pass. If it fails, stop and tell the user. If it updates `tests/tooling/opencode-verified.json`, commit that change.

### Task 8: Live prompt cases (only after the user's explicit OK)

**Files:**
- Modify: `docs/development.md` (observed results)

- [ ] **Step 1:** Ask the user to authorize three `scripts/live_check.py --prompt` runs (inference on the default model). Without the OK, stop here and report the task as not run.
- [ ] **Step 2:** With Smart Router on (`--settings` enabling `smart_router` and onboarding), run each prompt and inspect the printed tool calls, not the reply:
  1. A planned change to order sizing in a trading project: expect `risk: critical`; a plan review requested with `work_kind: "planning"`; the exact-plan implementer requested at level 1; a diff review requested with `risk: critical`.
  2. A docs typo fix: expect `risk: reversible` and no plan review.
  3. A config default feeding an order path: expect `risk: critical`.
- [ ] **Step 3:** Record what was observed (OpenCode version, date, commit, cases passed or not) in `docs/development.md`, and commit. A misjudged case is the trigger for the separate judging skill; report it, do not write the skill.
