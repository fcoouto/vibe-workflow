# Smart Router Automatic Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hand-written strategy table with a zero-setup selector that discovers runnable models from OpenCode, scores them with public Artificial Analysis data from OpenRouter, and recommends the cheapest model that clears the task's level.

**Architecture:** `index.js` reads `ctx.model.list()` and passes a reduced model list to the Python child. Python builds routes for the three plan paths, runs the existing account and quota checks, maps each surviving model to cached OpenRouter scores and prices, ranks them with one fixed rule, and returns a pick plus two alternatives. A bounded, on-demand refresh keeps the score cache current.

**Tech Stack:** Python 3.11 standard library, Node ES modules with built-ins only, OpenCode V2 plugin API.

**Spec:** `docs/superpowers/specs/2026-11-20-smart-router-auto-selection-design.md` (approved). Also read the "Paid overflow and quota" and "ChatGPT limit signal" sections before Tasks 4, 8 and 9.

## Global Constraints

- Standard library and Node built-ins only; no new dependency; each Python module under 1000 lines (`scripts/validate.py` enforces).
- Every result keeps `admission: false`, `reservation: false`, `spend_permit: false`; nothing launches or reserves.
- The selector never runs inference. Tests never touch the network or a real account; fake every fetcher and reader.
- State directory `0700`, files `0600`, through `runtime/storage.py` helpers (locks, atomic replace).
- The only new egress: HTTPS `GET https://openrouter.ai/api/v1/models`, no credentials, no cross-host redirect.
- Attribution string, exact: `Scores: Artificial Analysis via OpenRouter`.
- Lightweight rule (user, memory vault `smart-router-lightweight`): on demand only, no timers or daemons, no polling.
- `README.md` changes go through the `impeccable` skill (AGENTS.md).
- Commands: `python3 -B -m unittest discover -s tests`, `node --test tests/*.test.js`, `python3 -B scripts/validate.py`, `python3 -B scripts/package.py --output <new path>`, `python3 -B scripts/validate.py --root <that path>`.
- Single-module test runs: `python3 -B -m unittest tests.test_<name> -v`; `node --test tests/<name>.test.js`.

## Review Focus

1. **Agents still sending `v: 1` after the upgrade.** They must get `invalid_request` with a notice that says what to send (`v: 2` and the three task facts), not a silent fallback. Test in Task 6.
2. **OpenRouter variant and duplicate ids** (`...:free`, `...:thinking`, the same model under two creators). A variant must never borrow the base model's score by accident, and duplicates resolve by the path's creator or become `unscored`. Test in Task 2.
3. **Malformed or hostile OpenRouter payloads** (not JSON, wrong types, `NaN` or negative prices, oversized body, half the models missing). The last good cache survives. Test in Task 3.
4. **An empty or failing `ctx.model.list()`.** The answer is `no_selection: no_models` and no child process is started. Test in Task 6.
5. **Models without effort variants** (`variants: []`). They are still eligible, with `effort: null`. Test in Task 1.

---

### Task 1: Ranking rule and task level

**Files:**
- Modify: `runtime/routing.py` (add the new functions beside `ensure_profile`, `row_key`, `resolve_row` and `rank`; Task 5 deletes the old ones, so every commit stays green)
- Test: `tests/test_routing.py` (new)

**Interfaces:**
- Produces:
  - `FACTS = {'files': ('1-2', '3+', 'cross-system'), 'spec': ('exact', 'prose', 'open'), 'risk': ('reversible', 'shared', 'critical')}`
  - `BASE = dict(planning=3, review=2, investigation=2, implementation=1, testing=1, writing=1)`
  - `RATIO = (0.55, 0.82, 0.90)`, `NEAR_SCORE = 0.97`, `NEAR_PRICE = 0.5`
  - `EFFORTS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')`
  - `CONSTRAINT_FIELDS = ('allowed_models', 'excluded_models', 'allowed_efforts', 'allowed_harnesses')`
  - `task_level(work_kind: str, task: dict) -> int` (1 to 3; `ValueError('invalid_request')` on unknown kind, missing or unknown fact, or bad `context_tokens`)
  - `check_constraints(constraints: dict) -> dict` (validated copy; `ValueError('invalid_request')`)
  - `price(input_per_m: float, output_per_m: float) -> float`
  - `effort_for(level: int, variants: list[str]) -> str | None`
  - `order(candidates: list[dict], level: int) -> list[dict]`; each candidate has `route` (str), `score` (float), `price` (float), `released` (int, 0 when unknown)

- [ ] **Step 1: Write the failing tests** in `tests/test_routing.py`:
  - `test_level_is_max_of_base_and_facts`: `task_level('implementation', dict(files='1-2', spec='exact', risk='reversible')) == 1`; `task_level('review', <same>) == 2`; `task_level('planning', <same>) == 3`; `task_level('implementation', dict(files='3+', spec='exact', risk='reversible')) == 2`; `risk='critical'` gives 3.
  - `test_missing_or_unknown_facts_are_rejected`: missing `risk`, `files='many'`, an extra key, `context_tokens=True` and `context_tokens=-1` each raise `ValueError`.
  - `test_golden_two_plans` with the spec's scores and prices (Fable 5.2 64.6 $8/$40, GPT-6.2 Astra 62.1 $8/$40, Opus 5.5 59.1 $4/$20, GPT-6.2 Sol 57.3 $2/$10, Sonnet 5.5 54.5 $2/$10, GPT-6.1 Terra 51.3 $1/$5, Fable 5.1 50.0 $10/$50, Haiku 5.5 36.5 $0.10/$0.50, GPT-6 Luna 27.9 $0.10/$0.50): `order(...)[0]['route']` is Haiku at level 1, Sol at level 2 and Sol at level 3.
  - `test_golden_claude_only` (the Claude rows only): Haiku, Sonnet 5.5 and Opus 5.5 for levels 1, 2 and 3.
  - `test_near_clause_needs_both_conditions`: a candidate at `0.96 * floor` and a quarter of the price is not added; one at `0.98 * floor` and 60% of the cheapest qualifying price is not added.
  - `test_ties_break_by_score_then_release_then_route`: two candidates with equal price; the higher score is first; at equal score the newer `released` is first.
  - `test_effort_clamps_to_variants`: `effort_for(2, ['low','medium','high']) == 'medium'`; `effort_for(3, ['low','medium']) == 'medium'`; `effort_for(1, ['high','max']) == 'high'`; `effort_for(2, []) is None`.
  - `test_constraints_are_bounded_lists`: an unknown key, a non-list and a 257-item list raise; `allowed_strategy_ids` is now unknown.

- [ ] **Step 2: Run and confirm failure.** `python3 -B -m unittest tests.test_routing -v`. Expected: import errors for the new names.

- [ ] **Step 3: Implement the interfaces in `runtime/routing.py`.** `order` follows the spec's rule exactly:

```text
best  = max score; floor = best * RATIO[level-1]
ok    = score >= floor
near  = score >= NEAR_SCORE * floor and price <= NEAR_PRICE * min(price of ok)
sort ok + near (deduplicated) by (price, -score, -released, route)
```

  `effort_for` targets `('low', 'medium', 'high')[level-1]`; if it is absent, take the nearest variant above it in `EFFORTS`, else the highest one below it.

- [ ] **Step 4: Run and confirm pass.** Same command, then `python3 -B -m unittest discover -s tests` and `node --test tests/*.test.js`. Expected: all pass (old code is untouched).

- [ ] **Step 5: Commit.** `git add runtime/routing.py tests/test_routing.py && git commit -m "feat: rank models by task level, score and price"`

### Task 2: Score mapping and OpenRouter parsing

**Files:**
- Create: `runtime/scores.py` (pure part)
- Test: `tests/test_scores.py` (new)

**Interfaces:**
- Produces:
  - `URL = 'https://openrouter.ai/api/v1/models'`, `ATTRIBUTION = 'Scores: Artificial Analysis via OpenRouter'`
  - `CREATOR = {'opencode-claude-subscription': 'anthropic', 'opencode-chatgpt-subscription': 'openai'}` (Go has no creator hint)
  - `normalize(model_id: str) -> str`: lowercase; keep the part after the last `/`; every run of characters outside `a-z0-9` becomes `-`; strip leading and trailing `-`.
  - `family_key(key: str) -> str`: drop `-`-separated segments that are all digits (`gpt-6-3-sol` → `gpt-sol`, `claude-haiku-4-5-20251001` → `claude-haiku`).
  - `parse_openrouter(raw: bytes, previous_scored: int = 0) -> dict`: returns `{'models': {creator/key: entry}}` with `entry = {'key', 'creator', 'family', 'score' (float or None), 'input' (USD/M), 'output' (USD/M), 'released' (int)}`, or raises `ValueError('scores_invalid')`.
  - `lookup(cache: dict, key: str, creator: str | None) -> dict | None`: returns `{'score', 'price', 'score_source': 'openrouter' | 'family', 'released'}`.

- [ ] **Step 1: Write the failing tests** in `tests/test_scores.py` with a small synthetic OpenRouter body (dicts serialized to bytes; no network):
  - `test_normalize_matches_both_spellings`: `normalize('anthropic/claude-sonnet-5.5') == normalize('claude-sonnet-5-5') == 'claude-sonnet-5-5'`; `normalize('openai/gpt-6.2-sol') == 'gpt-6-2-sol'`.
  - `test_family_key`: the spec's examples.
  - `test_parse_keeps_needed_fields_and_converts_prices`: `pricing.prompt "0.000002"` becomes `input == 2.0`; `benchmarks.artificial_analysis.intelligence_index` becomes `score`; models without a score keep `score None`.
  - `test_variant_ids_never_share_the_base_key` (Review Focus 2): `openai/gpt-6.2-sol:free` normalizes to `gpt-6-2-sol-free`, so its score is never returned for `gpt-6-2-sol`.
  - `test_duplicate_key_resolves_by_creator_or_is_unscored` (Review Focus 2): the same key under `openai` and `someone`. `lookup(cache, key, 'openai')` returns the `openai` entry; `lookup(cache, key, None)` returns `None`.
  - `test_unscored_model_borrows_newest_family_score`: `gpt-6-3-sol` is absent, while `gpt-6-2-sol` (newer `released`) and `gpt-6-1-sol` are scored. The result has `score_source == 'family'`, the newer score, and the newer member's price when the model has no price of its own.
  - `test_parse_rejects_bad_payloads` (Review Focus 3): non-JSON, `data` not a list, an id longer than 256 characters, a `NaN` or negative price, a score that is not a finite number in 0 to 100, and `previous_scored=100` with only 40 scored models each raise `ValueError('scores_invalid')`. Malformed single entries inside an otherwise valid list are skipped, not fatal.

- [ ] **Step 2: Run and confirm failure.** `python3 -B -m unittest tests.test_scores -v`

- [ ] **Step 3: Implement** the interfaces in `runtime/scores.py`. Bounds: at most 5000 models, ids at most 256 printable characters, prices finite and `>= 0`. Reject the whole payload when fewer than half of `previous_scored` models carry a score. A family lookup uses only entries with a score; choose the greatest `released`, then key.

- [ ] **Step 4: Run and confirm pass.** Same command.

- [ ] **Step 5: Commit.** `git commit -m "feat: map runnable models to OpenRouter scores"` (add both files).

### Task 3: Score cache and refresh triggers

**Files:**
- Modify: `runtime/scores.py`
- Test: `tests/test_scores.py`

**Interfaces:**
- Consumes: `parse_openrouter`, `storage.read_json`, `storage._locked`, `storage._write`
- Produces:
  - `CACHE = 'openrouter-models.json'`; `GAP = 900`; `CEILING = 7 * 86400`; `RETRY = (3600, 14400, 43200, 86400)`; `BACKOFF = (300, 3600)` (start, cap; doubling); `FIRST_TIMEOUT = 5.0`; `MAX_BYTES = 5 << 20`
  - `load(state_dir: Path) -> dict | None` (cache or `None`; an unsafe file raises like `storage.read_json`)
  - `due(cache: dict | None, wanted: list[str], now: int) -> bool`
  - `refresh(state_dir: Path, wanted: list[str], now: int, *, fetch=None, timeout: float = 15.0) -> str`. Returns `'stored'`, `'skipped'`, `'busy'` or `'failed'`. `fetch(timeout) -> bytes` defaults to `fetch_openrouter`.
  - `fetch_openrouter(timeout: float, environ=None) -> bytes`. Uses urllib over HTTPS only, with a redirect handler that refuses any host other than `openrouter.ai`. It reads at most `MAX_BYTES + 1` bytes, fails above `MAX_BYTES`, and sends `User-Agent: vibe-workflow-smart-router`. It raises `OSError('network_disabled')` when `VCW_TEST_HOME` is set, the same guard as `opencode_account.default_command`, so CLI and Node tests can never reach the network.
  - Cache shape: `{'version': 1, 'fetched_at', 'attempt_at', 'failures', 'models': {...}, 'unscored': {key: {'step': int, 'next_at': int}}}`

- [ ] **Step 1: Write the failing tests** with a fake clock and a fake `fetch`:
  - `test_due_rules`. With no cache, `due` is true. A fresh cache with every wanted key present is not due. A new wanted key is due once the 15-minute gap has passed and not before. The gap measures from `attempt_at`, including failed attempts. A cache whose `fetched_at` is more than 7 days old is due. A `fetched_at` in the future counts as stale and is due.
  - `test_unscored_retry_schedule_then_weekly`: a wanted key that stays unscored is due again at +1h, +4h, +12h and +24h after successive refreshes, and then not again until the 7-day ceiling.
  - `test_removed_models_are_pruned`: a refresh with `wanted=[]` drops `unscored` entries for keys no longer wanted.
  - `test_failure_keeps_last_good_copy_and_backs_off`: a fetch raising `OSError` returns `'failed'`, leaves `models` byte-identical and makes `due` false for 300 s, then 600 s after a second failure, up to 3600 s. A malformed payload behaves the same way (Review Focus 3).
  - `test_single_fetcher`: while a test holds the cache lock, `refresh` returns `'busy'` without calling `fetch`.
  - `test_cache_file_is_private`: after `'stored'`, the file mode is `0o600` and the directory mode `0o700`.
  - `test_fetch_refuses_cross_host_redirect`: a stub opener redirecting to another host makes `fetch_openrouter` raise; no second request is made.
  - `test_fetch_is_disabled_under_test_home`: `fetch_openrouter(1, environ={'VCW_TEST_HOME': '/x'})` raises `OSError` before opening any connection.

- [ ] **Step 2: Run and confirm failure.**
- [ ] **Step 3: Implement.** `refresh` takes the lock and re-checks `due`, so a late caller does nothing and returns `'skipped'`. It records `attempt_at` before fetching, parses with `previous_scored` from the old cache, then writes atomically. After a successful refresh, each wanted key still without its own score advances one `RETRY` step; past the last step it is removed from `unscored`, so only the weekly ceiling brings it back.
- [ ] **Step 4: Run and confirm pass.**
- [ ] **Step 5: Commit.** `git commit -m "feat: refresh the score cache on demand"`

### Task 4: Routes from the model list, and eligibility without the overflow rule

**Files:**
- Modify: `runtime/routing_paths.py` (add `routes` beside `generate`)
- Modify: `runtime/routing_observations.py` (add `eligible` beside `shortlist`)
- Test: `tests/test_selector.py` (new class `RoutesTest`)
- Task 5 deletes `generate`, `shortlist` and `runtime/routing_funding.py`, and removes `routing_funding` from `scripts/manifest.py`.

**Interfaces:**
- Produces:
  - `routing_paths.routes(models: list[dict]) -> list[dict]`. Input items look like `{'provider', 'id', 'model' (str|None), 'family' (str|None), 'variants' (list[str]), 'context' (int|None), 'released' (int|None)}`. Each route is `dict(rule, route=f"{path}:{id}", model=id, model_id=model, family=family, variants=variants, context_limit=context, released=released or 0, demand=None)` for the one rule whose `provider` matches. Other providers are ignored; malformed items raise `ValueError('invalid_request')`; more than 512 models raise.
  - `routing_observations.eligible(routes: list[dict], source, now: int | None = None) -> tuple[list[dict], list[dict]]`. A kept route gains `account_marker` and `paid_overflow`. Excluded items are `{'route', 'reason'}` with reason `billing_unverified` or `quota_exhausted`.
  - `PAID = {'provider_blocked': 'blocked', 'provider_managed': 'enabled', 'unknown': 'unknown'}`

- [ ] **Step 1: Write the failing tests** (`RoutesTest`, using the existing `Facts` fake):
  - `test_routes_come_from_models_per_path`: three models (one each for `claude-agent`, `openai`, `opencode-go`) plus one `anthropic` model. The result is three routes with the right `path`, `pool` and `harness` from `config/routes.json`, and the `anthropic` model is ignored.
  - `test_overflow_never_excludes`: `paid_crossover` set to `provider_blocked`, `provider_managed` or `unknown`. All three routes are kept, with `paid_overflow` equal to `blocked`, `enabled` or `unknown`.
  - `test_quota_and_billing_still_exclude`: an exhausted path gives `quota_exhausted`; a stale fact gives `billing_unverified`.
  - `test_no_ten_route_cap`: 30 routes with valid facts are all kept.
- [ ] **Step 2: Run and confirm failure.** `python3 -B -m unittest tests.test_selector.RoutesTest -v`
- [ ] **Step 3: Implement.** Reuse `subscription_billing.fact` and `marker`. Keep the existing "only positive, fresh exhaustion refuses" behavior of `source.quota` unchanged.
- [ ] **Step 4: Run and confirm pass.** Then run `python3 -B scripts/validate.py`. Expected: passed.
- [ ] **Step 5: Commit.** `git commit -m "feat: build routes from runnable models with quota-only eligibility"`

### Task 5: Selector, readiness and CLI

**Files:**
- Modify: `runtime/selector.py` (rewrite `select`, `check_setup`; add `refresh`; delete `_configuration`)
- Modify: `scripts/select.py` (flags `--refresh`, `--allow-fetch`)
- Delete the old code kept by Tasks 1 and 4:
  - from `runtime/routing.py`: `ensure_profile`, `row_key`, `resolve_row`, `rank`, `FLOORS`, `REVIEW_POLICIES`, `COMPLEXITY_BANDS`, `RISK_BANDS`;
  - `generate`, and the table validation in `runtime/routing_paths.py`;
  - `shortlist` in `runtime/routing_observations.py`;
  - `runtime/routing_funding.py`, with `routing_funding` removed from `scripts/manifest.py`.
  
  Grep `runtime/` for each removed name first; `claude_usage` and `opencode_account` must not depend on them.
- Test: `tests/test_selector.py` (rewrite `SelectionTest`), `tests/test_package.py` (CLI cases), `tests/plugin.test.js` (the one test that runs the real CLI)

**Interfaces:**
- Consumes: Tasks 1 to 4; `native_catalog.stored(anchor, 'claude-code')`; `routing_observations.LocalSource`
- Produces:
  - `select(request: dict, *, state_dir: Path, source=None, fetch=None, now=None) -> dict`. The request is `{'v': 2, 'work_kind', 'task', 'constraints'?, 'models'}`; `models` is plugin-supplied.
    - Result: `{'state': 'selected'|'no_selection'|'blocked', 'reason', 'route', 'alternatives', 'excluded', 'level', 'attribution', 'admission': False, 'reservation': False, 'spend_permit': False}`.
    - Internal keys, which the plugin strips: `refresh` (`{'wanted': [...]}` or absent) and `first_fetch` (bool).
    - Each route adds `effort`, `score`, `score_source`, `price` and `paid_overflow`.
  - Reasons: `invalid_request`, `no_models`, `scores_unavailable`, `no_recommendation`, `no_eligible_route`. New exclusion reasons: `unscored`, `context_limit`, `constraint`.
  - `check_setup(*, state_dir: Path, allow_fetch: bool = False, fetch=None) -> dict` returns `{'state': 'ready'|'setup_needed', 'reason': None|'scores_unavailable'|'selector_unavailable'}`.
  - `refresh(request: dict, *, state_dir: Path, fetch=None) -> dict` returns `{'state': <scores.refresh outcome>}` for `{'wanted': [...]}`.
  - The anchor for readers stays `state_dir / 'router.json'`; it is a directory anchor only and is never read.

- [ ] **Step 1: Rewrite `SelectionTest`** around a fake model list, the `Facts` fake and a fake `fetch` returning a synthetic OpenRouter body. Keep the authority-boundary assertions. Tests:
  - `test_selects_cheapest_qualifying_and_two_alternatives`: level 2 picks the expected model; `alternatives` has 2 entries; `attribution` is exact; the three authority flags are `False`.
  - `test_first_run_fetches_once_and_reports_first_fetch`: with no cache, `fetch` is called once and `first_fetch` is `True`. The next call makes no fetch and has `first_fetch` `False`.
  - `test_first_run_failure_is_scores_unavailable`: no cache and a failing fetch give `no_selection`, `scores_unavailable`, and a `None` route.
  - `test_refresh_due_is_reported_not_performed`: a stale cache answers from the cache, carries `refresh.wanted`, and calls `fetch` zero times.
  - `test_claude_alias_maps_through_native_catalog`: a stored native catalog maps `sonnet` to `claude-sonnet-5-5`, so the route model stays `sonnet` and its score is Sonnet 5.5's.
  - `test_unscored_without_family_is_excluded`: the model appears in `excluded` as `unscored`.
  - `test_context_and_constraints_only_narrow`: `task.context_tokens` above a route's `context_limit` excludes it as `context_limit`; `excluded_models` and `allowed_efforts` exclude as `constraint`.
  - `test_empty_model_list_has_no_effects` (Review Focus 4): `models: []` gives `no_selection` and `no_models`, with no acquisition, no fetch and no state written.
  - `test_invalid_requests_do_not_echo_or_acquire`: `v: 1`, a missing `task`, a bad fact and an unknown key carrying `PRIVATE-SENTINEL` all give `blocked` and `invalid_request`, never echo the sentinel, and make no acquisition.
  - `test_changed_identity_is_refused_on_final_recheck`: keep the existing test's intent.
  - `test_check_setup`: no cache gives `scores_unavailable` without fetching; `allow_fetch=True` with a working fake gives `ready`; an existing cache gives `ready` without fetching; no account reader is called (patch `runtime.opencode_account.refresh` to raise).
- [ ] **Step 2: Update the CLI cases** in `tests/test_package.py`. Its environment already sets `VCW_TEST_HOME`, so fetches are refused. Cases:
  - `models: []` gives `no_models` and writes no state.
  - Valid models with no cache give `scores_unavailable`.
  - `--refresh` with a pre-written fresh cache (`fetched_at` and `attempt_at` set to now) prints `{"state":"skipped"}` without echoing stdin.
  
  Remove the cases that write `router.json` and `strategy-table.json`. In `tests/plugin.test.js`, "unconfigured execution returns no authority without creating state" now expects `no_models`, because its host stub has no `ctx.model`.
- [ ] **Step 3: Run and confirm failure.** `python3 -B -m unittest tests.test_selector tests.test_package -v`
- [ ] **Step 4: Implement.** Flow in `select`:
  1. Validate the request.
  2. `level = routing.task_level(...)`.
  3. `routes = routing_paths.routes(models)`; if empty, return `no_models`.
  4. `source.acquire_billing`, then `eligible`.
  5. Load the cache. With none, run the first fetch with `scores.FIRST_TIMEOUT`.
  6. Map each kept route with `scores.lookup`. Claude keys come from the native catalog alias, else `model_id`, else `id`.
  7. Apply context and constraints.
  8. `routing.order`.
  9. Recheck the account marker for the first three, as today; a changed one is excluded as `account_changed`.
  10. Compute `wanted` (usable keys without their own entry); set `refresh` when `scores.due(...)` is true.
  
  `scripts/select.py`: `--refresh` reads `{'wanted': [...]}` from stdin and prints `refresh(...)`; `--allow-fetch` is only valid with `--check-setup`. Keep the existing size limit, signal handling and fixed failure output.
- [ ] **Step 5: Run and confirm pass**, then the full Python suite. Expected: all pass.
- [ ] **Step 6: Commit.** `git commit -m "feat: select from live models without a strategy table"`

### Task 6: Plugin glue

**Files:**
- Modify: `index.js`, `onboarding.js`
- Test: `tests/plugin.test.js`, `tests/onboarding.test.js`

**Interfaces:**
- Consumes: the CLI from Task 5; `ctx.model.list()` returning `Model.Info` (`id`, `modelID`, `providerID`, `family?`, `variants[].id`, `limit.context`, `time.released?`, `capabilities.tools?`, `enabled`, `status`).
- Produces:
  - Tool input schema `v: 2` (const), `work_kind` (enum), `task` (`files`, `spec` and `risk` required enums; `context_tokens` optional integer `>= 0`) and `constraints`; `additionalProperties: false`.
  - Tool description: the current advisory sentence plus one rubric line per fact, naming its three values in level order (for example `files: 1-2 | 3+ | cross-system`).
  - A missing `ctx.model`, a throwing `ctx.model.list()` or an empty reduced list returns `no_selection` with `no_models` without spawning.
  - Model reduction: keep `enabled && status !== 'deprecated' && capabilities.tools !== false`; map to `{provider: providerID, id, model: modelID ?? null, family: family ?? null, variants: variants.map(v => v.id), context: limit?.context ?? null, released: time?.released ?? null}`. Cap at 512.
  - `registerOnboarding(ctx, projects, checkSetup, prGuard)`, where `checkSetup(signal, allowFetch)` is passed `false` for preview and `true` for apply with `enabled: true`.

- [ ] **Step 1: Write the failing tests:**
  - plugin: `test("passes the reduced model list and drops a caller-supplied one")`. The fixture `select.py` asserts that stdin has `models` from a stub `ctx.model.list()`, and that a `models` key in the tool input is ignored.
  - plugin: `test("strips internal fields and starts one bounded refresh")`. The fixture answers with `refresh: {wanted: ["x"]}` and `first_fetch: true`. The tool content has neither key, a second spawn with `--refresh` receives `{"wanted":["x"]}` on stdin, and cleanup stops it.
  - plugin: `test("first fetch shows the disclosure once")`. The notice includes `openrouter.ai` and `sends no credentials`. A later answer without `first_fetch` has no notice.
  - plugin: `test("empty or failing model list never spawns")` (Review Focus 4). A stub returning `[]` or throwing gives `no_selection` and `no_models`, and the fixture's spawn marker file does not exist.
  - plugin: `test("v1 callers learn what to send")` (Review Focus 1). The fixture answers `invalid_request`, and the notice matches `/v: 2/` and `/files, spec, risk/`.
  - plugin: update the "enabled failures" table. Remove `unconfigured`, `table_empty` and `overflow_not_blocked`. Add `scores_unavailable` (`/scores/i`), `no_models` (`/models/i`) and `unscored`.
  - onboarding: `preview` passes `--check-setup` without `--allow-fetch`, while `apply` with `enabled: true` passes `--allow-fetch`. Assert through a fixture `select.py` that records its argv.
  - Keep the existing context-hook assertions. The hook text must now also mention the task facts (`/files.*spec.*risk/`).
- [ ] **Step 2: Run and confirm failure.** `node --test tests/plugin.test.js tests/onboarding.test.js`
- [ ] **Step 3: Implement.** Notice texts, each ending in `Continuing with your client's existing selection.`:
  - `scores_unavailable`: "Model scores are not available yet; Smart Router retries automatically."
  - `no_models`: "No runnable models were found on your connected plans."
  - `unscored`: "No scored model fits this task yet."
  - `invalid_request`: "Send v: 2 with task facts files, spec, risk."
  - Disclosure, shown only on `first_fetch`: "vibe-workflow: Smart Router downloads public model scores from openrouter.ai about weekly and sends no credentials. Scores: Artificial Analysis via OpenRouter."
  
  The refresh child uses the same spawn options as `runRouter`, a 15 s timeout, and the shared `active` set. A setup check with `--allow-fetch` gets a 10 s timeout.
- [ ] **Step 4: Run and confirm pass**, then `node --test tests/*.test.js`.
- [ ] **Step 5: Commit.** `git commit -m "feat: pass live models to the selector and refresh scores in the background"`

### Task 7: Package, onboarding text and docs

**Files:**
- Delete: `config/router.example.json`, `config/strategy-table.example.json`
- Modify: `scripts/manifest.py` (`FILES`), `scripts/validate.py` (drop both example checks), `tests/test_package.py` (expected export set)
- Modify: `skills/vibe-onboard-project/references/procedure.md`
  - Question 1 context gains: "It downloads public model scores from openrouter.ai about weekly and sends no credentials."
  - Step 9's Smart Router `ready` line becomes: "Smart Router: On · Scores loaded — route eligibility is checked when work is delegated."
- Modify: `README.md` through the `impeccable` skill. Replace "Configure routes" with two or three lines: no setup; scores from Artificial Analysis via OpenRouter; how to pause (opt out in onboarding).
- Modify: `docs/dependencies.md` (remove the `routing_funding.py` row; describe `routing.py` as level and ranking; add `scores.py`)

- [ ] **Step 1: Update `tests/test_package.py`** for the new export set, then run it and see it fail.
- [ ] **Step 2: Make the file changes.**
- [ ] **Step 3: Verify.**
  1. `python3 -B -m unittest discover -s tests && node --test tests/*.test.js && python3 -B scripts/validate.py`
  2. `python3 -B scripts/package.py --output /tmp/sr-export-$(date +%s)`
  3. `python3 -B scripts/validate.py --root <that path>`
  
  Expected: all pass.
- [ ] **Step 4: Commit.** `git commit -m "chore: drop the strategy-table setup from the package and docs"`

### Task 8: ChatGPT spike (gate)

Run this only when implementation reaches this point, as the user asked. It follows the spec's section "Spike before building the ChatGPT limit signal" exactly: questions, leads, bounds (half a day, at most 10 tiny ChatGPT requests, read-only, no token refresh, no `backend-api`, no installs without approval) and exit rule.

- [ ] **Step 1:** Ask the user to start the spike, and confirm the request budget again.
- [ ] **Step 2:** Work through the leads; record every request made.
- [ ] **Step 3:** Write the finding to `docs/chatgpt-quota-spike.md` and commit it: what was checked, what was found, and whether any source meets the adoption rule.
- [ ] **Step 4:** If a source is adopted, update the spec and stop for the user's approval. Otherwise continue to Task 9 as specified.

### Task 9: ChatGPT limit signal (planned after Task 8)

Deliberately not planned in detail yet, at the user's request: its plan depends on the spike. Without an adopted source, it implements the spec's "ChatGPT limit signal" section:
- the 429 mark and its two detection surfaces;
- the Codex reset read trimmed from the previous version's `runtime/codex_rate_limits.py`;
- the one-hour fallback and the persistent mark;
- the spec's ChatGPT tests.

Write its tasks in this file, in this plan's format, before starting it.
