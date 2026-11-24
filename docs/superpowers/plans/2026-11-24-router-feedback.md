# Smart Router Feedback Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Coordinators rate finished subagents on optional 0-5 cards; cards adjust each model's effective score per kind of work and size group; an estimated agent-hours fact raises effort on long tasks.

**Architecture:** A new `runtime/feedback.py` owns the card store and the adjustment formula. `runtime/selector.py` applies adjustments in `_select` only (the council is untouched) and returns each pick's catalog identity. `scripts/select.py` gains a `--feedback` operation. `index.js` issues `pick_id`s, remembers them per session, reads the rater's model, and exposes `smart_router_feedback`.

**Tech Stack:** Python 3.11+ standard library (`unittest`), JavaScript ES modules (`node --test`).

**Spec:** `docs/superpowers/specs/2026-11-24-router-feedback-design.md`. Read it before any task.

## Global Constraints

- Standard library only; `runtime/` flat (single-level `runtime.X` imports); each Python module within 1000 lines.
- `v: 2` stays compatible: every new request field is optional; new answer fields are additions.
- `ROUTER_NOTE`, `MODEL_NOTE` and the skill-load notes stay byte-for-byte unchanged.
- No model names in any description or note text.
- The whole `smart_router_select` description stays under 2048 characters.
- Advisory only: `admission`, `reservation`, `spend_permit` stay `false` on every answer.
- Feedback never breaks a pick: any feedback read error means adjustment 0.
- Comments are never echoed in any answer.
- Constants: `LONG_HOURS = 4`; `HALF_LIFE = 30 * 86400`; cap = `0.10 * anchor`; shrinkage `n / (n + 5)`; `MIN_SESSIONS = 3`; same-lab and unknown rater weight `0.5`; family weight `0.5`; comment at most 280 characters.
- Commands: `python3 -B -m unittest discover -s tests -t .`, `node --test "tests/**/*.test.js"`, `python3 -B scripts/validate.py`.

## Review Focus

- A damaged, wrong-version or oversized `router-feedback.json` must leave selection unchanged (adjustment 0), never `selector_unavailable`. Test in Task 3.
- Two cards written at the same moment must both land or one be refused, never a corrupt file. Test in Task 2 (sequential writes under the lock; the lock path is checked).
- `estimate_agentic_effort_hours` that is negative, above 100, a string or a boolean is `invalid_request`; `0` and `4.0` are valid. Test in Task 1.
- A rater whose model is not on the user's plans is counted at half weight, not refused. Test in Task 4.
- A `pick_id` lost to a plugin restart returns `ignored`, never an error. Test in Task 5.

---

### Task 1: Estimated agent hours and effort

**Files:**
- Modify: `runtime/routing.py` (`task_level`, `effort_for`; add `LONG_HOURS`, `size_group`)
- Modify: `runtime/selector.py` (`_candidates`: pass the size to `effort_for`)
- Test: `tests/runtime/test_routing.py`, `tests/runtime/test_selector.py`

**Interfaces:**
- Produces: `routing.LONG_HOURS = 4`; `routing.size_group(task: dict) -> str` returning `'long'` when `task.get('estimate_agentic_effort_hours', 0) >= 4`, else `'short'`; `routing.effort_for(level: int, variants: list, long: bool = False) -> str | None` (long targets one step above the level's base in `EFFORTS`: low→medium, medium→high, high→xhigh, then clamps as today). `task_level` accepts the optional key `estimate_agentic_effort_hours` (int or float, not bool, finite, 0 to 100 inclusive) and ignores it for the level.

- [ ] **Step 1: Write failing tests** in `test_routing.py`:

```python
def test_estimated_hours_is_validated_and_does_not_change_the_level(self):
    for hours in (0, 4.0, 100):
        self.assertEqual(routing.task_level('implementation', dict(FACTS, estimate_agentic_effort_hours=hours)), 1)
    for bad in (-1, 100.5, '4', True, float('nan')):
        with self.assertRaises(ValueError):
            routing.task_level('implementation', dict(FACTS, estimate_agentic_effort_hours=bad))

def test_size_group_and_long_effort(self):
    self.assertEqual(routing.size_group(FACTS), 'short')
    self.assertEqual(routing.size_group(dict(FACTS, estimate_agentic_effort_hours=3.9)), 'short')
    self.assertEqual(routing.size_group(dict(FACTS, estimate_agentic_effort_hours=4)), 'long')
    v = ['low', 'medium', 'high', 'xhigh', 'max']
    self.assertEqual([routing.effort_for(l, v, long=True) for l in (1, 2, 3)], ['medium', 'high', 'xhigh'])
    self.assertEqual(routing.effort_for(3, ['low', 'high'], long=True), 'high')
```

In `test_selector.py`, one test: a request with `estimate_agentic_effort_hours=6` at level 2 returns `route["effort"] == "high"` (fixture variants include `"high"`; add `"medium"` if the fixture lacks it).

- [ ] **Step 2: Run** `python3 -B -m unittest tests.runtime.test_routing tests.runtime.test_selector -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces; in `_candidates`, call `routing.effort_for(level, item['variants'], long=routing.size_group(request_task) == 'long')` (thread the task through `_candidates` from `_select`; the council passes no task, so `long=False`).
- [ ] **Step 4: Run** the full Python suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat: estimated agent hours raise effort on long tasks`.

### Task 2: Feedback store and adjustment

**Files:**
- Create: `runtime/feedback.py`
- Modify: `scripts/manifest.py` (`RUNTIME` gains `"feedback"`)
- Test: `tests/runtime/test_feedback.py`

**Interfaces:**
- Produces, in `runtime/feedback.py`:
  - `FILE = 'router-feedback.json'`, `LOCK = '.feedback.lock'`, `HALF_LIFE`, `MIN_SESSIONS`, `MAX_KEYS = 2000`, `MAX_VOTES = 5000`, `MAX_COMMENTS = 10`.
  - `load(state_dir) -> dict | None`: the store, or `None` on any read or shape problem (never raises).
  - `record(card: dict, *, state_dir, now: int) -> dict`: returns `dict(state='recorded')`, `dict(state='refused', reason=R)` with R in `self_rating`, `already_rated`, `invalid`, `store_full`. `card` fields: `rated` `{key, creator, family}`; `rater` `{key, creator}` or `None`; `work_kind`; `size_group` (`short`|`long`); `rating` int 0-5; `session` (hex, 16-64 chars); `comment` str or `None`.
  - `adjustment(store, *, key, creator, family, work_kind, size_group, anchor, now) -> dict(sessions=int, adjustment=float)`.
- Store shape (`version: 1`): `keys` maps `"creator/key|work_kind|size_group"` to `{w, wr, at, family, sessions: [hex, ...] (last 20), comments: [str, ...] (last 10)}`; `votes` maps `"session|creator/key|work_kind"` to the time recorded.

- [ ] **Step 1: Write failing tests** in `tests/runtime/test_feedback.py` (temp state dir, fixed `now`):
  - `test_three_sessions_needed`: two cards from two sessions → `adjustment == 0`, `sessions == 2`; a third session → nonzero.
  - `test_caps_both_ways`: 50 sessions rating 0 → adjustment `== -0.10 * anchor` within 1e-9; 50 rating 5 → `+0.10 * anchor`.
  - `test_shrinkage_and_formula`: 6 full-weight cards averaging 1.5 at anchor 61.4 → adjustment `== 6.14 * ((1.5 - 3) / 3) * 6 / 11` within 1e-6.
  - `test_half_life`: the same cards read 30 days later give `n` halved (check the adjustment equals the formula with n halved).
  - `test_rater_weights`: same-creator rater and `rater=None` each add weight 0.5; a different-lab rater adds 1.
  - `test_self_rating_refused`: rater key and creator equal the rated → `refused/self_rating`, store unchanged.
  - `test_one_vote_per_session_model_kind`: a second card same session, model, kind (any size group) → `refused/already_rated`; a different kind → recorded.
  - `test_family_half_weight`: cards on `claude-x-5-1` count at half weight for `claude-x-5-2` (same creator and family, older version via `scores.version`), never for an older key from a newer one, never across creators.
  - `test_invalid_cards`: rating 6, rating 2.5, comment of 281 characters, unknown size group → `refused/invalid`.
  - `test_damaged_store_loads_as_none`: garbage bytes, wrong version, a symlink → `load` returns `None`; `record` on a damaged store refuses `invalid`, never raises.
  - `test_bounds`: beyond `MAX_KEYS` new keys or `MAX_VOTES` votes, the oldest by `at` / time is dropped first; comments keep the last 10.
- [ ] **Step 2: Run** `python3 -B -m unittest tests.runtime.test_feedback -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** with `storage._locked(path, lock=LOCK)`, `storage.read_json`, `storage._write`. Decay totals lazily: on read and before adding, multiply `w` and `wr` by `0.5 ** ((now - at) / HALF_LIFE)` and set `at = now`. Adjustment, with `own` and `fam` the decayed totals (fam = sum over family keys with same creator, same `work_kind` and `size_group`, `scores.version(other) < scores.version(key)`):

```python
n = own_w + 0.5 * fam_w
if n <= 0 or len(sessions) < MIN_SESSIONS: return 0
mean = (own_wr + 0.5 * fam_wr) / n
d = (mean - 3) / 3 if mean < 3 else (mean - 3) / 2
cap = 0.10 * anchor
return max(-cap, min(cap, cap * d * n / (n + 5)))
```

  `sessions` = distinct session hashes across own and the counted family keys.
- [ ] **Step 4: Run** the full Python suite and `python3 -B scripts/validate.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: feedback card store and bounded adjustment`.

### Task 3: Selector applies adjustments

**Files:**
- Modify: `runtime/selector.py` (`_select`)
- Test: `tests/runtime/test_selector.py`

**Interfaces:**
- Consumes: `feedback.load`, `feedback.adjustment` (Task 2); `routing.size_group` (Task 1).
- Produces: in `_select` only, each candidate's `score` becomes catalog score + adjustment before `routing.order`, `strongest` and the qualifier; each candidate keeps `catalog_score`, gains `feedback: {sessions, adjustment}` and `identity: {key, creator, family}` (key from `pool['keys']`, creator from `pool['creators']`, family `scores.family_key(key)`). `council.pick` and `_candidates` are unchanged.

- [ ] **Step 1: Write failing tests:**
  - `test_feedback_moves_a_model_below_its_bar`: seed a store (via `feedback.record`, 6 sessions rating 1, implementation, short) for the cheapest level-1 candidate whose catalog score sits within 3 points above the level-1 bar → it is no longer the pick; `route` shows `feedback.adjustment < 0`, `catalog_score` unchanged.
  - `test_feedback_is_per_kind`: the same store leaves a `review` request's pick unchanged.
  - `test_damaged_feedback_file_changes_nothing`: write garbage to `router-feedback.json` → same pick as with no file, `state == "selected"`.
  - `test_pick_reports_identity_and_feedback`: every chosen route has `identity.key`, `identity.creator`, `feedback.sessions` (0 with no store).
- [ ] **Step 2: Run** `python3 -B -m unittest tests.runtime.test_selector -v`. Expected: FAIL.
- [ ] **Step 3: Implement**; load the store once per `_select`; any exception from feedback code means adjustment 0 for that candidate.
- [ ] **Step 4: Run** the full Python suite, including `tests.runtime.test_council`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: feedback adjusts effective scores at selection`.

### Task 4: The feedback operation and rater identity

**Files:**
- Modify: `runtime/selector.py` (add `identity`), `scripts/select.py` (`--feedback`), `runtime/feedback.py` (add `submit`)
- Test: `tests/runtime/test_feedback.py`, `tests/tooling/test_package.py` only if it enumerates operations

**Interfaces:**
- Produces:
  - `selector.identity(entry: dict, *, state_dir) -> dict | None`: `entry` is one plugin model entry (`provider`, `id`, `model`, `family`, `variants`, `context`, `released`); routes it with `routing_paths.routes([entry])`, keys it with `_key` and the stored native catalog, takes creator from `scores.CREATOR` or `scores.lookup(...)['creator']`; `None` when anything is missing.
  - `feedback.submit(request: dict, *, state_dir, now) -> dict`: request `{v: 1, rated: {key, creator, family}, rater_entry: entry | None, work_kind, size_group, rating, session, comment}`; resolves `rater` via `selector.identity` (failure → `None`), then calls `record`. Invalid shape → `refused/invalid`.
  - `scripts/select.py --feedback`: reads the request from stdin like other operations; on any exception prints `{"state":"failed"}`.

- [ ] **Step 1: Write failing tests:** `test_submit_resolves_rater_and_refuses_self` (rater entry equal to the rated model → `self_rating`); `test_unknown_rater_counts_half` (an entry for a provider not on any path → recorded with weight 0.5); `test_submit_invalid_shape` (missing `rated`, `v` 2 → `refused/invalid`); a CLI test running `scripts/select.py --feedback --state-dir <tmp>` with a valid request prints `{"state":"recorded"}`, following the existing CLI tests' pattern.
- [ ] **Step 2: Run** the new tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full Python suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat: feedback operation with rater identity`.

### Task 5: Plugin: pick_id, the feedback tool and the text

**Files:**
- Modify: `index.js`
- Test: `tests/plugin/plugin.test.js`

**Interfaces:**
- Consumes: answer fields `identity`, `feedback` on each chosen route (Task 3); `--feedback` operation (Task 4).
- Produces:
  - Selected answers gain `pick_id` (16 hex characters from `crypto.randomBytes(8)`) and `feedback_hint`: exactly `"After the subagent reports, you may rate its work with smart_router_feedback and this pick_id."`
  - Per session in memory: `pick_id` → `{work_kind, size_group, refs: Map(model_ref → identity)}`, at most 64 picks per session, sessions bounded by `MAX_NOTICE_SESSIONS`, cleared when the plugin closes or Smart Router is turned off.
  - Tool `smart_router_feedback`, input `{pick_id: string, model_ref: string, rating: integer 0-5, comment?: string maxLength 280}`, additionalProperties false. Answers: `{state: "recorded"}`, `{state: "refused", reason}`, `{state: "ignored"}` (unknown `pick_id`, other session, or Smart Router off), `{state: "failed"}`. Never echoes `comment`. `model_ref` not among the pick's refs → `refused`, reason `not_offered`. Rater entry: `ctx.session.get({sessionID}).model` `{providerID, id}` matched against the plugin's model list (`provider === providerID && id === id`); not found → `rater_entry: null`. Session hash: SHA-256 of `sessionID`, first 32 hex characters. `runRouter` gets operation `feedback` (args `--feedback`, timeout 10000, output state must be one of `recorded`, `refused`, `failed`).
  - Feedback tool description (no model names): "Optional. Rate a subagent's finished work for a Smart Router pick: 0 = failed, 3 = met the stated checks, 5 = met them with no rework needed. Skip it when the brief was at fault or the provider failed. One card per session, model and kind of work. Ratings adjust later picks on this machine; Smart Router sends nothing."
  - `smart_router_select` description: replace the `spec: exact` clause with "exact = this request is one task of a written plan that names its files, its expected behavior and the checks that tell success from failure, and leaves the builder nothing to decide; several plan tasks in one request, or any open choice, is prose. Unsure whether it is exact: say prose."; replace "Rate the task, not the project, by what a mistake in it would cost, not by its size." with "One request per delegated task. Rate risk by what a mistake would cost, not by the task's size; give the size in estimate_agentic_effort_hours, the hours the delegated agent will work, not a person's."
  - Input schema: `task.properties.estimate_agentic_effort_hours` `{type: "number", minimum: 0, maximum: 100}`; `constraints` `{type: "object", additionalProperties: false, properties: {allowed_models, excluded_models, allowed_efforts, allowed_harnesses: {type: "array", items: {type: "string"}}}}`.

- [ ] **Step 1: Write failing tests:**
  - a selected answer has a 16-hex `pick_id` and the exact `feedback_hint`; a `no_selection` answer has neither;
  - feedback with an unknown `pick_id` → `ignored`; with a `model_ref` not offered → `refused/not_offered`; with a valid pick → the stub selector receives `--feedback` and a request carrying `rated.key`, `work_kind`, `size_group`, a 32-hex `session`, and `rater_entry` matching the session's model; the tool's answer never contains the comment text;
  - descriptions: select under 2048 characters, contains "one task of a written plan" and "estimate_agentic_effort_hours", no match for `/haiku|opus|sonnet|fable|astra|terra|luna|gemini/i`; feedback description contains "0 = failed" and "Smart Router sends nothing", same model-name check;
  - schema lists the four constraint fields and the hours field with bounds;
  - `ROUTER_NOTE` and `MODEL_NOTE` unchanged (existing tests stay green).
- [ ] **Step 2: Run** `node --test tests/plugin/plugin.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `node --test "tests/**/*.test.js"`, the Python suite and `python3 -B scripts/validate.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: pick ids and the smart_router_feedback tool`.

### Task 6: Docs, package and live check

**Files:**
- Modify: `docs/development.md` (a short "Feedback cards" paragraph: store path, formula pointer to the spec, what is and is not observed live)

- [ ] **Step 1:** Write the paragraph (no model names); run all three commands plus `python3 -B scripts/package.py --output <new path>` and `python3 -B scripts/validate.py --root <new path>`. Expected: all pass. Commit `docs: record Smart Router feedback cards`.
- [ ] **Step 2 (authorized with this plan's approval):** one `scripts/live_check.py --prompt` run with Smart Router on: the agent selects a model, dispatches one small subagent, then files a card and tries a second one for the same model and kind. Expected from the tool calls: first card `recorded`, second `refused` with `already_rated`, and the card stored with the rater's model. Record the outcome in `docs/development.md` and commit.
