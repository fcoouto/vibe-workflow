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
- Constants: `LONG_HOURS = 4`; `HALF_LIFE = 30 * 86400`; cap = `0.10 * anchor`; shrinkage `n / (n + 5)`; `MIN_SESSIONS = 3`; same-lab and unknown rater weight `0.5`; family weight `0.5`; comment at most 280 characters; `MAX_KEYS = 400`, `MAX_VOTES = 5000`, 10 sessions and 3 comments kept per key.
- Commands: `python3 -B -m unittest discover -s tests -t .`, `node --test "tests/**/*.test.js"`, `python3 -B scripts/validate.py`.

## Review Focus

- A damaged, wrong-version or oversized `router-feedback.json` must leave selection unchanged (adjustment 0), never `selector_unavailable`. Test in Task 3.
- Two cards written at the same moment must both land, never a corrupt file. Test in Task 2 (two threads calling `record` at once; the store is valid and holds both).
- `estimate_agentic_effort_hours` that is negative, above 100, a string or a boolean is `invalid_request`; `0` and `4.0` are valid. Test in Task 1.
- A rater whose model is not on the user's plans is counted at half weight, not refused; but a rater whose session model is the picked model itself is refused even then. Tests in Tasks 4 and 5.
- The clock moving backwards or a far-future timestamp in the store never raises and never inflates weights. Test in Task 2.
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

In `test_selector.py`, one test with model variants `["low", "medium", "high", "xhigh"]` (the default fixture has only `["low", "high"]`, so set them explicitly): at level 2 a request without the estimate returns `route["effort"] == "medium"`, and with `estimate_agentic_effort_hours=6` returns `"high"`.

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
  - `FILE = 'router-feedback.json'`, `LOCK = '.feedback.lock'`, `HALF_LIFE`, `MIN_SESSIONS`, `MAX_KEYS = 400`, `MAX_VOTES = 5000`, `MAX_SESSIONS = 10`, `MAX_COMMENTS = 3`. `runtime/feedback.py` imports only `storage`, `scores` and the standard library (never `selector`).
  - `load(state_dir) -> dict | None`: the store, or `None` on any read or shape problem (never raises).
  - `record(card: dict, *, state_dir, now: int) -> dict`: returns `dict(state='recorded')` or `dict(state='refused', reason=R)` with R in `self_rating`, `already_rated`, `invalid` (a bad card), `store_unsafe` (the file is a symlink, has wrong permissions or owner). A damaged or wrong-version store is replaced by a fresh one, as the scores cache is. When the key or vote bound is reached, the oldest key (by `at`) or vote (by time) is dropped first. Never raises. `card` fields: `rated` `{key, creator, family}`; `rater` `{key, creator}` or `None`; `work_kind`; `size_group` (`short`|`long`); `rating` int 0-5; `session` (hex, 16-64 chars); `comment` str or `None`.
  - `adjustment(store, *, key, creator, family, work_kind, size_group, anchor, now) -> dict(sessions=int, adjustment=float)`.
- Store shape (`version: 1`): `keys` maps `"creator/key|work_kind|size_group"` to `{w, wr, at, family, sessions: [hex, ...] (last 10), comments: [str, ...] (last 3), last_rater: "creator/key" or null}`; `votes` maps `"session|creator/key|work_kind"` to the time recorded.
- Decay: factor `0.5 ** (max(0, now - at) / HALF_LIFE)`; a stored `at` later than `now` is treated as `now` (no growth, no overflow).

- [ ] **Step 1: Write failing tests** in `tests/runtime/test_feedback.py` (temp state dir, fixed `now`):
  - `test_three_sessions_needed`: two cards from two sessions → `adjustment == 0`, `sessions == 2`; a third session → nonzero.
  - `test_bounded_both_ways`: 200 sessions rating 0 → adjustment between `-0.10 * anchor` and `-0.97 * 0.10 * anchor`; 200 rating 5 → between `+0.97 * 0.10 * anchor` and `+0.10 * anchor` (shrinkage `n/(n+5)` approaches the cap; the clamp is a guard). Patch `MAX_VOTES` if needed.
  - `test_shrinkage_and_formula`: 6 full-weight cards averaging 1.5 at anchor 61.4 → adjustment `== 6.14 * ((1.5 - 3) / 3) * 6 / 11` within 1e-6.
  - `test_half_life`: the same cards read 30 days later give `n` halved (check the adjustment equals the formula with n halved).
  - `test_rater_weights`: same-creator rater and `rater=None` each add weight 0.5; a different-lab rater adds 1.
  - `test_self_rating_refused`: rater key and creator equal the rated → `refused/self_rating`, store unchanged.
  - `test_one_vote_per_session_model_kind`: a second card same session, model, kind (any size group) → `refused/already_rated`; a different kind → recorded.
  - `test_family_half_weight`: cards on `claude-x-5-1` count at half weight for `claude-x-5-2` (same creator and family, older version via `scores.version`), never for an older key from a newer one, never across creators.
  - `test_invalid_cards`: rating 6, rating 2.5, comment of 281 characters, unknown size group → `refused/invalid`.
  - `test_damaged_store`: files created with mode `0o600`: garbage bytes, a wrong version, a file over 1 MiB → `load` returns `None`, and `record` replaces it with a fresh store and records. A symlink or a `0o644` file → `load` returns `None` and `record` refuses `store_unsafe`. Nothing raises.
  - `test_bounds`: with `MAX_KEYS` and `MAX_VOTES` patched to 3, a fourth key or vote drops the oldest; comments keep the last 3; sessions the last 10.
  - `test_concurrent_records`: two threads each record a card at once (different sessions); afterwards `load` returns a valid store holding both.
  - `test_clock_safety`: a store whose `at` is 10**12 seconds in the future, and a `now` earlier than `at`, give finite weights equal to the undecayed ones; `record` does not raise.
  - `test_last_rater`: a recorded card stores `last_rater` as `creator/key` of the rater, or null for an unknown rater.
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
- Produces: in `_select` only, each candidate's `score` becomes catalog score + adjustment before `routing.order`, `strongest` and the qualifier; each candidate keeps `catalog_score`, gains `feedback: {sessions, adjustment}` and `catalog_id: {key, creator, family}` (key from `pool['keys']`, creator from `pool['creators']`, family `scores.family_key(key)`). Selected answers also carry `size_group` (from `routing.size_group`), so the plugin never repeats the 4-hour threshold. `council.pick` and `_candidates` are unchanged.

- [ ] **Step 1: Write failing tests:**
  - `test_feedback_moves_a_model_below_its_bar`: a custom catalog body with three labs (anchor 61.4, level-1 bar 33.77): a cheap candidate A scoring 34.8 and a pricier candidate B scoring 40. Seed 20 sessions rating 0 for A (implementation, short) via `feedback.record`: A's adjustment is about −4.9, so it falls below both the bar and the near clause's 97% (32.76); the pick becomes B, and A no longer appears among the alternatives. Without the store, A is the pick.
  - `test_feedback_is_per_kind`: the same store leaves a `review` request's pick unchanged.
  - `test_damaged_feedback_file_changes_nothing`: write garbage to `router-feedback.json` → same pick as with no file, `state == "selected"`.
  - `test_pick_reports_catalog_id_and_feedback`: every chosen route has `catalog_id.key`, `catalog_id.creator`, `feedback.sessions` (0 with no store); the answer has `size_group`.
- [ ] **Step 2: Run** `python3 -B -m unittest tests.runtime.test_selector -v`. Expected: FAIL.
- [ ] **Step 3: Implement**; load the store once per `_select`; any exception from feedback code means adjustment 0 for that candidate.
- [ ] **Step 4: Run** the full Python suite, including `tests.runtime.test_council`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: feedback adjusts effective scores at selection`.

### Task 4: The feedback operation and rater identity

**Files:**
- Modify: `runtime/selector.py` (add `rater_id`, `feedback_submit`), `scripts/select.py` (`--feedback`)
- Test: `tests/runtime/test_feedback.py`, `tests/tooling/test_package.py` only if it enumerates operations

**Interfaces:**
- Produces:
  - `selector.rater_id(entry: dict, *, state_dir) -> dict | None`: `entry` is one plugin model entry (`provider`, `id`, `model`, `family`, `variants`, `context`, `released`); routes it with `routing_paths.routes([entry])`, keys it with `_key` and the stored native catalog, takes creator from `scores.CREATOR` or `scores.lookup(...)['creator']`; `None` when anything is missing.
  - `selector.feedback_submit(request: dict, *, state_dir, now: int | None = None) -> dict` (in `selector`, so `feedback` never imports `selector`): request `{v: 1, rated: {key, creator, family}, rater_entry: entry | None, work_kind, size_group, rating, session, comment}`; resolves `rater` via `rater_id` (failure → `None`), then calls `feedback.record` with `now` or the current time. Invalid shape → `refused/invalid`.
  - `scripts/select.py --feedback`: reads the request from stdin and calls `feedback_submit(request, state_dir=...)`; on any exception prints `{"state":"failed"}` (add the flag to the `failed` fallback branch).

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
- Consumes: answer fields `catalog_id`, `feedback` on each chosen route and `size_group` on the answer (Task 3); `--feedback` operation (Task 4).
- Produces:
  - Selected answers gain `pick_id` (16 hex characters from `crypto.randomBytes(8)`) and `feedback_hint`: exactly `"After the subagent reports, you may rate its work with smart_router_feedback and this pick_id."`
  - Per session in memory: `pick_id` → `{work_kind, size_group, refs: Map("provider/model" → {catalog_id, provider, model})}` (the pick and its alternatives), at most 64 picks per session, sessions bounded by `MAX_NOTICE_SESSIONS`, cleared when the plugin closes or Smart Router is turned off.
  - Tool `smart_router_feedback`, input `{pick_id: string, model_ref: string, rating: integer 0-5, comment?: string maxLength 280}`, additionalProperties false. Answers: `{state: "recorded"}`, `{state: "refused", reason}`, `{state: "ignored"}` (unknown `pick_id`, other session, or Smart Router off), `{state: "failed"}`. Never echoes `comment`. `model_ref` is matched without its `#variant` part, so a dispatch at another effort still counts; not among the pick's refs → `refused`, reason `not_offered`. Rater: `ctx.session.get({sessionID}).model` `{providerID, id}` (shape observed live on OpenCode 2.3.2); if it equals the rated ref's provider and model → `refused`, reason `self_rating`, before calling Python; otherwise matched against the plugin's model list (`provider === providerID && id === id`); `get` missing, throwing, or without `model`, or no match → `rater_entry: null`. Session hash: SHA-256 of `sessionID`, first 32 hex characters. `runRouter` gets operation `feedback` (args `--feedback`, timeout 10000, output state must be one of `recorded`, `refused`, `failed`; its `fallback()` returns `{state: "failed"}`).
  - Feedback tool description (no model names): "Optional. Rate a subagent's finished work for a Smart Router pick: 0 = failed, 3 = met the stated checks, 5 = met them with no rework needed. Skip it when the brief was at fault or the provider failed. One card per session, model and kind of work. Ratings adjust later picks on this machine; Smart Router sends nothing."
  - `smart_router_select` description: replace the `spec: exact` clause with "exact = this request is one task of a written plan that names its files, its expected behavior and the checks that tell success from failure, and leaves the builder nothing to decide; several plan tasks in one request, or any open choice, is prose. Unsure whether it is exact: say prose."; replace "Rate the task, not the project, by what a mistake in it would cost, not by its size." with "One request per delegated task. Rate risk by what a mistake would cost, not by the task's size; give the size in estimate_agentic_effort_hours, the hours the delegated agent will work, not a person's."
  - Input schema: `task.properties.estimate_agentic_effort_hours` `{type: "number", minimum: 0, maximum: 100}`; `constraints` `{type: "object", additionalProperties: false, properties: {allowed_models, excluded_models, allowed_efforts, allowed_harnesses: {type: "array", items: {type: "string"}}}}`.

- [ ] **Step 1: Write failing tests:**
  - a selected answer has a 16-hex `pick_id` and the exact `feedback_hint`; a `no_selection` answer has neither;
  - feedback with an unknown `pick_id` → `ignored`; with a `model_ref` not offered → `refused/not_offered`; with a valid pick → the stub selector receives `--feedback` and a request carrying `rated.key`, `work_kind`, `size_group`, a 32-hex `session`, and `rater_entry` matching the session's model; the tool's answer never contains the comment text;
  - descriptions: select under 2048 characters, contains "one task of a written plan" and "estimate_agentic_effort_hours", no match for `/haiku|opus|sonnet|fable|astra|terra|luna|gemini/i`; feedback description contains "0 = failed" and "Smart Router sends nothing", same model-name check;
  - schema lists the four constraint fields and the hours field with bounds;
  - `ROUTER_NOTE` and `MODEL_NOTE` unchanged. Update the tool-list assertion at `tests/plugin/plugin.test.js:58` to include `smart_router_feedback`, and give the test host's `ctx.session` a `get` stub (merge it with the existing `hook`, do not replace it).
  - rater cases: `get` missing, throwing, or returning no `model` → `rater_entry: null`; a session model equal to the picked model → `refused/self_rating` without calling Python; a dispatch `model_ref` with a different `#variant` is accepted.
- [ ] **Step 2: Run** `node --test tests/plugin/plugin.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `node --test "tests/**/*.test.js"`, the Python suite and `python3 -B scripts/validate.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: pick ids and the smart_router_feedback tool`.

### Task 6: Docs, package and live check

**Files:**
- Modify: `docs/development.md` (a short "Feedback cards" paragraph: store path, formula pointer to the spec, what is and is not observed live)

- [ ] **Step 1:** Write the paragraph (no model names); run all three commands plus `python3 -B scripts/package.py --output <new path>` and `python3 -B scripts/validate.py --root <new path>`. Expected: all pass. Commit `docs: record Smart Router feedback cards`.
- [ ] **Step 2 (authorized with this plan's approval):** one `scripts/live_check.py --prompt` run with Smart Router on: the agent selects a model, dispatches one small subagent, then files a card and tries a second one for the same model and kind. Expected from the tool calls: first card `recorded`, second `refused` with `already_rated`. The run uses the user's real state directory, so its one card lands in the real store; with the 3-session rule a single card changes no pick, and it decays. Report that in the record. Record the outcome in `docs/development.md` and commit.
