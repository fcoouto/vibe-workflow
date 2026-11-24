# Smart Router Steering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the three `allowed_*` constraints, make `excluded_models` accept every model format and report unmatched entries, add `reviewing` for reviewer independence, and stop remembering below-level picks.

**Architecture:** Python owns matching and ordering (`runtime/routing.py`, `runtime/selector.py`); the plugin (`index.js`) owns the schema, the descriptions, the notices and pick memory. The dev tool `scripts/live_check.py` stops using `allowed_harnesses`.

**Tech Stack:** Python 3.11+ standard library (`unittest`), JavaScript ES modules (`node --test`).

**Spec:** `docs/superpowers/specs/2026-11-24-router-steering-design.md`. Read it before any task.

## Global Constraints

- Standard library only; `runtime/` flat; modules within 1000 lines.
- `v: 2` stays; `reviewing` is optional; a request carrying `allowed_models`, `allowed_efforts` or `allowed_harnesses` is `invalid_request`.
- `ROUTER_NOTE`, `MODEL_NOTE` and the skill notes unchanged; no model names in any text; the `smart_router_select` description under 2048 characters.
- Advisory only: `admission`, `reservation`, `spend_permit` stay false.
- Independence never lowers quality: only candidates that already qualify (or the below-level fallback list) are reordered.
- Commands: `python3 -B -m unittest discover -s tests -t .`, `node --test "tests/**/*.test.js"`, `python3 -B scripts/validate.py`.

## Review Focus

- `excluded_models` naming the pick's own `model_ref` with a `#variant` removes that route. Test in Task 1.
- `excluded_models` entries matching nothing never block and are listed once each in `unmatched`. Test in Task 1.
- `reviewing` naming a model that is not on any plan still resolves its lab from the catalog when possible. Test in Task 2.
- A pool where only the reviewed lab qualifies returns the normal pick and `reviewer_independent: false`. Test in Task 2 (`test_reviewing_never_lowers_the_level`).
- A below-level answer with `reviewing` is never reordered. Test in Task 2.
- A below-level pick returns a `pick_id` but its card is `ignored`. Test in Task 3.

---

### Task 1: Delete the allowed_* fields; excluded_models matching and unmatched

**Files:**
- Modify: `runtime/routing.py` (`CONSTRAINT_FIELDS`, `check_constraints`), `runtime/selector.py` (`_constrained`, `_candidates`, `_select`), `scripts/live_check.py` (`pick_for_path`)
- Test: `tests/runtime/test_routing.py`, `tests/runtime/test_selector.py`, `tests/tooling/test_live_check.py`

**Interfaces:**
- Produces: `routing.CONSTRAINT_FIELDS == ('excluded_models',)`. `selector.excluded(item, key, entries) -> bool`: true when an entry equals `item['model']`, `f"{item['provider']}/{item['model']}"` after removing any `#...` suffix from the entry, `key` (catalog key) or `item['route']`. Every answer (selected, `no_selection` including the early `no_models`, `no_eligible_route` and `scores_unavailable` returns) carries `unmatched: [entry, ...]` (request order, no duplicates) only when some `excluded_models` entries match no route built from `models` by `routing_paths.routes`; for that check compute the catalog key of every route with `_key` and the stored native catalog, before eligibility, quota and scoring.

- [ ] **Step 1: Write failing tests.**
  - `test_routing.py`: `check_constraints(dict(excluded_models=['m']))` passes; `dict(allowed_models=['m'])`, `dict(allowed_efforts=['low'])`, `dict(allowed_harnesses=['x'])` raise `ValueError`. Rewrite the existing assertions at lines 125-127 to use `excluded_models` for the shape checks (a string instead of a list, 257 entries, unknown key), so they still fail for the intended reason.
  - `test_selector.py`: rewrite, do not drop, the tests around line 171 (constraints forcing a below-level pick: use `excluded_models` to remove the qualifying model) and line 297 (`excluded_models` naming every route → the same `no_selection` reason as before). Add: excluding a route by its bare model name, by `provider/model#high`, by its route name, and by its catalog key, using `opencode-go` model `glm-5.3-flash` whose key `glm-5-3-flash` differs from its name; `excluded_models=["nope", "<a real model name>", "nope"]` returns `unmatched == ["nope"]`; a quota-exhausted route named by catalog key is not in `unmatched`; `unmatched` is present on a `no_models` answer; no `unmatched` key when all match; a request with `allowed_models` returns `blocked/invalid_request`.
  - `test_live_check.py` (around lines 485-490): give the fixture models from two providers; `pick_for_path` sends no `constraints` key and only the given provider's models.
- [ ] **Step 2: Run** the three test modules. Expected: FAIL.
- [ ] **Step 3: Implement.** `_constrained(item, key, constraints)` uses `excluded`; thread the catalog key from `keys`. `pick_for_path` filters `models` to `m["provider"] == provider`.
- [ ] **Step 4: Run** the full Python suite and validate.py. Expected: PASS.
- [ ] **Step 5: Commit** `feat: narrow-only constraints with flexible excluded_models`.

### Task 2: reviewing and reviewer independence

**Files:**
- Modify: `runtime/selector.py` (`select` request validation, `_select`; add `review_lab`)
- Test: `tests/runtime/test_selector.py`

**Interfaces:**
- Consumes: `_entry_identity`, `_model_identity`, the canonical-lab helper used by feedback (strip a leading `~`).
- Produces: `select` accepts an optional `reviewing` (string, 1-256 printable characters with exactly one `/` before any `#` and non-empty provider and model parts, else `invalid_request`). `selector.review_lab(reviewing, models, *, state_dir) -> str | None`: strip `#variant`; if `provider/model` matches a request model entry (`provider` and `id`), use `_entry_identity`, else `_model_identity`; return the canonical creator or `None`. In `_select`, when `reviewing` is given and the answer is not below-level: if the ordered qualifying list (after `routing.order`) holds candidates whose canonical creator differs from the review lab, keep only those (relative order unchanged) before the confirm step; `reviewer_independent` is true when that filter applied and a route was chosen, else false. The below-level `strongest` fallback is never filtered or reordered (`reviewer_independent: false`). The answer carries `reviewer_independent` only when `reviewing` is given.

- [ ] **Step 1: Write failing tests.** Fixture: a catalog with four labs whose best scores are 70 (lab a), 68 (lab b), 66 (lab c), 64 (lab d), so the anchor (third-highest lab best) is 66 and the level-3 bar is 66; candidates are `opencode-go` routes (so the lab comes from the catalog, not `scores.CREATOR`): `a-small` (lab a, score 67, cheap) and `b-small` (lab b, score 67, pricier; or score 50 where stated, below the near band 0.97 × 66 = 64.02). Requests use `work_kind="review"` and `risk="critical"` (level 3).
  - `test_reviewing_prefers_another_lab`: `reviewing="opencode-go/a-small"` (resolved through the request's model entry) → pick `b-small`, no alternative from lab a, `reviewer_independent is True`; without `reviewing`, `a-small` is the pick.
  - `test_reviewing_never_lowers_the_level`: `b-small` scores 50 → pick `a-small`, `reviewer_independent is False`.
  - `test_reviewing_below_level_not_reordered`: only candidates below the bar (`a-small` 60, `b-small` 50) → `below_level` true, pick is the strongest (`a-small`), `reviewer_independent is False`.
  - `test_reviewing_unknown_lab`: `reviewing="x/unknown"` → normal pick, `reviewer_independent is False`.
  - `test_reviewing_off_plan_model_resolves`: `reviewing="openai/a-other"`, a lab-a catalog model not in `models` (resolved by `_model_identity`) → `b-small` is picked.
  - `test_reviewing_variant_and_alias`: `reviewing="opencode-go/a-small#high"` resolves to lab a; a catalog creator spelled `~a` is the same lab as `a`.
  - `test_no_reviewing_no_field`: answers without `reviewing` have no `reviewer_independent` key; `reviewing=7` and `reviewing="no-slash"` → `invalid_request`.
- [ ] **Step 2: Run** `python3 -B -m unittest tests.runtime.test_selector -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full Python suite and validate.py. Expected: PASS.
- [ ] **Step 5: Commit** `feat: reviewing prefers a qualifying reviewer from another lab`.

### Task 3: Plugin schema, text, notices and pick memory

**Files:**
- Modify: `index.js`
- Test: `tests/plugin/plugin.test.js`

**Interfaces:**
- Consumes: answer fields `unmatched`, `reviewer_independent`, `below_level` (Tasks 1-2).
- Produces:
  - Input schema: `constraints` = `{type: "object", additionalProperties: false, properties: {excluded_models: {type: "array", items: {type: "string"}, description: "Models to leave out for this request only: a pick's model_ref, a catalog key or a model name."}}}`; top-level `reviewing` = `{type: "string", description: "For a review: the model_ref of the model whose work is reviewed; the router prefers a reviewer from another lab."}`. The three `allowed_*` properties are gone.
  - The below-level notice becomes exactly: `` `vibe-workflow: Smart Router could not confirm a level ${level} match for this request and recommended the strongest available; excluded models may be the cause. Tell your human partner.` `` (same cause key and dedupe).
  - New notice when `reviewer_independent === false` on a selected answer, cause `same_lab_review`, once per session and kept for the session like `below_level_*` causes (update `forgetFailures` so a normal pick does not clear it), exactly: `"vibe-workflow: No reviewer from a different lab than the reviewed work reaches this task's level; Smart Router recommended the usual pick."` If both notices apply, the below-level one wins.
  - Picks with `below_level: true` keep their `pick_id` and hint but are not remembered; a card for them returns `ignored`.

- [ ] **Step 1: Write failing tests:** schema lists only `excluded_models` under `constraints` and has `reviewing`; the `reviewing` value reaches the stub selector's request unchanged; the reworded below-level notice text; the `same_lab_review` notice appears once per session, also after review → ordinary pick → review, and not when `reviewer_independent` is true or absent; with both `below_level` and `reviewer_independent: false`, only the below-level notice; a below-level pick's card returns `ignored` (the stub answer must otherwise be rateable: `size_group` and a ref with `catalog_id`, unlike the existing `below()` helper); description still under 2048 and no model names (existing checks). Update the existing schema assertion around line 1180.
- [ ] **Step 2: Run** `node --test tests/plugin/plugin.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full Node and Python suites and validate.py. Expected: PASS.
- [ ] **Step 5: Commit** `feat: reviewing input, narrowed constraints and clearer notices`.

### Task 4: Docs and checks

**Files:**
- Modify: `docs/development.md` (a short "Steering" paragraph: what `excluded_models` and `reviewing` do, that the `allowed_*` fields were removed and why, pointing to the design and the council record; and update line 45, which still says the injection check's re-pick uses `allowed_harnesses`)

- [ ] **Step 1:** Write the paragraph (no model names). Run validate.py, both suites, `scripts/package.py --output <new path>` and `validate.py --root <new path>`. Commit `docs: record narrowing-only steering and reviewing`.

## After the merge (controller)

Release 0.11.0 (minor: three request fields removed). The release notes say that a
caller still sending `allowed_models`, `allowed_efforts` or `allowed_harnesses`
now gets `invalid_request`, and that those fields were first advertised in 0.10.0.
