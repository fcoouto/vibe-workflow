# Subscription Usage Balance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give expiring included quota priority among comparable Smart Router recommendations.

**Architecture:** Keep ranking evidence separate from exhaustion evidence. Provider readers normalize existing observations; a pure scorer evaluates windows and reorders only the slots of comparable qualifying candidates. The plugin schedules the bounded Codex refresh after selection, and the selector uses only cached Codex hints.

**Tech Stack:** Python 3.11+ standard library, JavaScript ES modules, OpenCode V2, `unittest`, Node 22+ native test runner.

**Spec:** [`docs/superpowers/specs/2026-11-26-subscription-usage-balance-design.md`](../specs/2026-11-26-subscription-usage-balance-design.md).

## Global Constraints

- Ranking only: preserve billing, confirmed exhaustion, paid-overflow handling, task quality, near-floor exception, strongest fallback, reviewer lab independence, effort, feedback, and council seating.
- Only included subscriptions. Free and pay-per-use routes stay out. Group by subscription path, not model lab.
- Use the approved **15 minutes** maximum ranking age, **60 seconds** minimum Codex start spacing, and **15 minutes** failure cooldown. An old meter expires at reset even within its freshness limit.
- Use a **1% of window length** denominator floor and **10 percentage points** remaining for promotion. Compare against **1.5×** the baseline price, **97%** of its adjusted score, and a gain of at least **0.25** expiry-score points. Bounds are inclusive.
- No new Claude or Go reads; no scheduled timers, polling, reservations, debits, automatic retries, admission, spend permits, or inference to measure usage. No task-fit predictions, provider weights, or rollout settings.
- Require proved OpenCode browser OAuth `chatgpt-token-sharing` for Codex hints. Accept the same-account assumption without new identity checks or confirmation. Codex percentages cannot create or clear a pause.
- Use isolated homes and synthetic transports. No installation, activation, publication, or live collection. Follow the existing `AGENTS.md` exception only if the OpenCode version check fails or injected notes change; this plan does not change injected notes.

## Review Focus

1. An unknown baseline winner stays first; an unknown middle slot stays fixed even with three subscriptions and several model aliases.
2. A fresh reset or exact threshold boundary is not rounded into an exclusion or a spurious boost.
3. One malformed or ambiguous meter does not erase a valid sibling or supersede a confirmed pause.
4. A delayed Codex completion after an observed login or auth-method change is discarded and cannot restore the old hint.
5. An interrupted refresh or burst of picks does not start duplicate reads or hold up selection.

---

### Task 1: Score windows and reorder comparable slots

**Files:** Create `runtime/usage_balance.py` and `tests/runtime/test_usage_balance.py`; register the module in `scripts/manifest.py`. `package.json` already covers `runtime/`. No route configuration changes.

**Interfaces:**
- A `Meter` is a JSON-compatible mapping with exactly `used_percent` (finite number, `0..100`), `earliest`, `latest`, and `reset_at` (epoch seconds), `duration` (positive seconds or `None`), and `discrete` (`bool`). Meter kind is the outer key. Normalize account keys to `5h`, `7d`, `monthly`; keep Claude's verified `family:`/`model:` keys for matching protection. `monthly` for Go has `duration=None`. Use `storage.CLOCK_MARGIN` for duration plausibility.
- `fresh_meter(value: object, *, now: int) -> bool`; false for malformed, stale, future, or expired readings. Require `earliest <= latest <= now + 1`, `earliest <= now < reset_at`, `reset_at >= latest`, and `now - earliest < 900`. Check horizon against `latest + duration + CLOCK_MARGIN` when duration is known. `latest` may round receipt up by one second for Claude. Treat time since `earliest` conservatively and clamp the normalized time fraction to 1.
- `expiry_score(family: str, meters: dict[str, Meter], *, now: int) -> float | None`. Caller supplies only applicable scoped meters. Eligible boost IDs are `5h` and `7d`, both with verified discrete duration. Expected longer siblings: Claude and ChatGPT `5h` requires `7d`; Go `5h` requires `7d` and `monthly`, and Go `7d` requires `monthly`. Additional validated Codex durations use `duration:<seconds>` keys; they protect headroom only.
- `comparable(candidate: dict, baseline: dict) -> bool` uses existing price and adjusted `score`. `reorder(ordered: list[dict], *, subscriptions: dict[str, str], expiry: dict[str, float | None]) -> list[dict]` takes baseline order and route-keyed maps for only fully qualifying included candidates. `subscriptions` holds exact route `path` values, not provider or quota-parser families. Do not mutate inputs. Missing or invalid path values are nonparticipants.

- [ ] **Write tests.** Use fixed time and a fixture `meter(used, hours, duration_hours, *, at=NOW, age_offset=0, discrete=True)` with `latest=at`, `earliest=at-age_offset`, and `reset_at=at+hours*3600`, all integer seconds. Pin these assertions in `UsageBalanceTest`; use `assertAlmostEqual` for ratios.

```python
assert score("chatgpt", {"7d": meter(90, 2, 168)}) == 8.4          # test_reference_and_fresh_reset
assert score("claude", {"7d": meter(40, 72, 168)}) == 1.4
assert score("chatgpt", {"7d": meter(0, 168, 168)}) == 1
assert score("claude", {"5h": meter(0, 5, 5), "7d": meter(0, 168, 168)}) == 1
assert score("go", go_example(month_used=50)) == 2                 # test_month_without_duration
assert score("go", go_example(month_used=98)) == 1
assert score("go", go_example(month_used=None)) == 1                # None omits monthly
assert score("go", go_example(weekly_used=98)) == 1
assert score("go", go_example(weekly_used=80)) == 1                 # above headroom floor; fails weekly pace
assert score("go", go_example(rolling_used=98, weekly_used=20)) == 1
assert score("claude", {"7d": meter(25, 24, 168), scope: meter(98, 24, 168)}) == 1
assert score("claude", {"7d": weekly, "5h": short, scope: meter(85, 144, 168)}) == 1.4
assert score("claude", {"7d": weekly, "5h": short, scope: meter(20, 96, 168)}) == 3.75
assert score("claude", {scope: meter(25, 24, 168)}) is None          # no account-window score
assert score("chatgpt", {"5h": meter(50, 1, 5)}) == 1               # test_missing_longer_is_not_missing_plan
assert score("go", {"monthly": meter(50, 240, None)}) is None
assert score("claude", {"7d": meter(25, 24, 168, discrete=False)}) is None
assert score("claude", {"5h": short, "7d": meter(85, 144, 168, discrete=False)}) == 3.75
assert score("claude", {"5h": short, "7d": meter(50, 84, 168)}) == 3.75  # longer pace exactly 1
assert score("claude", {"7d": meter(90.01, 2, 168)}) == 1               # 90 already tested above
assert score("claude", {"5h": timing, "7d": weekly}) == 25
assert score("claude", {"7d": aged}, now=aged["earliest"] + 899) > 1
assert score("claude", {"7d": aged}, now=aged["earliest"] + 900) is None
assert fresh_meter(closing, now=closing["reset_at"] - 1)
assert not fresh_meter(closing, now=closing["reset_at"])
assert score("claude", {"5h": closing, "7d": weekly}, now=closing["reset_at"] - 1) == 90
assert score("claude", {"5h": closing, "7d": weekly}, now=closing["reset_at"]) == 1.4
assert half_minute_score_5h == half_minute_score_7d == 100          # same 0% used; 5h case has healthy weekly
assert reorder_names(["a", "unknown", "b", "c", "c-alias"]) == ["c", "unknown", "c-alias", "b", "a"]
assert reorder_names(["unknown", "a", "b"]) == ["unknown", "a", "b"]  # unknown first
assert rank_two(b_expiry=1.01) == ["a", "b"]                         # a has 1.00
assert rank_two(b_expiry=1.25, price_ratio=1.5, score_ratio=.97) == ["b", "a"]
assert rank_two(b_expiry=2, price_ratio=1.501) == ["a", "b"]
assert rank_two(b_expiry=2, score_ratio=.969) == ["a", "b"]
assert rank_two_same_subscription(b_expiry=2) == ["a", "b"]
assert reorder_names(["a", "outside_bounds", "b"]) == ["b", "outside_bounds", "a"]
assert reorder_names(["a", "small_gain", "b"]) == ["b", "small_gain", "a"]
```

  `go_example` uses the spec's 60%/1h, 20%/96h, 50%/240h. Use `weekly=meter(40,72,168)`, `short=meter(25,1,5)`, `timing=meter(50,5/60,5,age_offset=60)`, `aged=meter(40,72,168,age_offset=60)`, and `closing=meter(10,0.5/60,5)`. For the slot tests use `a=1`, `b=1.4`, `c=c-alias=2`, `small_gain=1.2`, and `outside_bounds=3` at 1.501× the baseline price. Except for `outside_bounds`, prices and adjusted scores are equal; only `c` and `c-alias` share a subscription. The 0.25 gain opens the group; it is not a pairwise sorting comparator. Also assert boolean, non-finite and out-of-range percentages yield no score, and an expired weekly cannot make a short score positive.
- [ ] **Run red:** `python3 -B -m unittest tests.runtime.test_usage_balance -v`. Expect missing module or failing assertions.
- [ ] **Implement.** Apply the 10-point guard to every fresh applicable meter (`used_percent > 90` avoids subtraction error at 90). Apply the unfloored pace veto only where the other meter is strictly longer, resets later, and has a verified discrete duration. Scoped weekly meters can pace-veto account `5h` boosts, not account `7d` boosts; any matched scope can veto for low headroom. Missing expected longer meters veto that window, not the route; a fresh protection-only sibling can satisfy presence. Return `None` when there is no fresh, discrete `5h` or `7d` score, even with protection-only evidence. Return `1` for a known route with all boosts suppressed. Score from `earliest`, never from the pick clock. Require a distinct subscription's comparable candidate to clear the minimum gain before sorting participant slots; stable-sort on descending expiry. Use the project tolerance for calculated score/gain boundaries, not to admit a just-out-of-bound price or headroom.
- [ ] **Run green:** the same unittest command and `python3 -B scripts/validate.py` pass.
- [ ] **Commit** the module, tests and manifest (`feat: score expiring subscription quota`). Rerun the affected tests after commit.

### Task 2: Retain and bind Claude and Go hints without new acquisition

**Files:** Modify `runtime/claude_quota.py`, `runtime/claude_usage.py`, `runtime/opencode_account.py`, and `runtime/routing_observations.py`; extend `tests/runtime/test_claude_quota.py` and `tests/runtime/test_readers.py`.

**Interfaces:**
- Reuse the successful `claude_quota.evidence` parse; add keyword `include_usage=False` and a separate `usage: dict[str, Meter]` element when requested. Existing callers/tests that do not request it still receive the exact old evidence shape.
- `claude_usage.ranking_meters(settings_path, route: dict, now: int, *, native: str) -> dict[str, Meter]` checks the native continuity key/generation and filters scoped hints through `claude_quota.covers` using only the bound catalog. Unknown/malformed scoped evidence never becomes an account-wide meter.
- `opencode_account.ranking_meters(settings_path, route: dict, now: int, *, marker: str) -> dict[str, Meter]` validates family, pool, current billing marker, and bound hints. `LocalSource.ranking_meters(route: dict, now: int) -> dict[str, Meter]` passes the candidate's `account_marker`. Task 4 adds a ChatGPT branch to the same reader, not another selector source.

- [ ] **Write tests.** Extend the existing fake native exchanges and their fixed clocks. Assert:

```python
assert hints["7d"]["used_percent"] == 40 and "5h" not in hints       # malformed sibling
assert hints["7d"]["earliest"] == int(started) - 60                  # preserves Claude cache bound
assert (claude_hints["5h"]["duration"], claude_hints["7d"]["duration"]) == (18_000, 604_800)
assert all(claude_hints[key]["discrete"] is True for key in ("5h", "7d"))
assert matching_scope in matching_route_hints
assert matching_scope not in different_route_hints
assert go_hints["5h"]["duration"] == 18_000 and go_hints["5h"]["discrete"]
assert go_hints["monthly"]["duration"] is None
assert old_marker_hints_after_switch == {}
assert hints_after_overlapping_reply == {}
assert hints_after_provably_newer_reply["7d"]["used_percent"] == 94
assert quota_reason_after_bad_or_late_hints == "quota_exhausted"
assert calls_before_cached_pick == calls_after_cached_pick
```

  For a Go row `rate-limited` at 80%, assert a ranking `used_percent` of 100 and the same existing pause. For a newer partial valid reply assert a malformed expected sibling becomes unknown, while its valid sibling and the older confirmed pause remain usable for their separate purposes. Feed an old healthy Claude sample followed by an overlapping low-headroom sample; assert neither ranks, a delayed older sample cannot restore the hint, and a later non-overlapping sample restores evidence. Inspect only the fake store: assert each cached meter has exactly the `Meter` fields and no raw response or extra metadata.
- [ ] **Run red:** `python3 -B -m unittest tests.runtime.test_claude_quota tests.runtime.test_readers -v`. Expect new hint assertions to fail.
- [ ] **Implement.** Publish an additive `ranking` field under each provider's existing generation-checked update. For Claude, collect from regular `get_usage` and the existing limit probe; keep its current window and scope agreement rules. Use documented discrete durations for the account windows and recognized weekly scopes. For Go, capture the percentage in the same validated parse, normalizing `rate-limited` to 100 for ranking only. Stamp request start and receipt. Preserve the old exhaustion record shape and status semantics, and alias Go `rolling`/`weekly` to `5h`/`7d`.

  A newer response with a recognized usage envelope replaces the whole ranking batch even if only some or none of its meters validate. New means its earliest time is strictly after the current batch's latest. Ignore a provably older reply. For overlapping intervals, discard ranking meters and retain their combined interval as the ordering boundary; do not queue either sample. An unavailable transport does not renew anything. These rules touch no exhaustion merge. Recheck bounds on read and isolate lost, corrupt or unsafe ranking state from the baseline pick and pause. No raw responses or new credential fields.
- [ ] **Run green:** the same unittest command and `python3 -B scripts/validate.py` pass.
- [ ] **Commit** the provider changes and tests (`feat: retain bound Claude and Go usage hints`). Rerun the affected tests after commit.

### Task 3: Apply balancing inside the existing selection groups

**Files:** Modify `runtime/selector.py`; extend `tests/runtime/test_selector.py`.

**Interfaces:** Consume `LocalSource.ranking_meters`, `opencode_account.family`, `usage_balance.expiry_score`, `comparable`, and `reorder`. Use `family` only to identify supported quota shapes; use each candidate's `path` for subscription identity. Use `_balance(ordered: list[dict], pool: dict, qualifies) -> list[dict]` immediately before each applicable `_confirm`. This is in ordinary `_select` only, never `_candidates`, council or setup.

- [ ] **Write tests.** Extend the selector's fake source with route-keyed hints and billing markers. Assert `answer["route"]["route"]` selects Go over Claude for the healthy Go fixture when both qualify. Assert the baseline Claude route stays first for unknown Go, lower than 97% adjusted score, more than 1.5× price, and for the month at 98%. Assert a legitimate near-floor baseline winner and the below-level result stay unchanged. Assert unrouted free and pay-per-use entries never get a hint lookup or join the result. In `ReviewingTest`, assert the known independent route still wins even if the same-lab Go model has stronger expiry; then compare two independent routes to prove the price/score anchor comes from their own group. When no independent route confirms, assert the old fallback route and `reviewer_independent=False`. Assert binding changes exclude the changed route and do not reorder surviving routes on its stale score or comparison anchor. Assert an exploding hint read leaves selection intact and `admission`, `reservation`, and `spend_permit` false.
- [ ] **Run red:** `python3 -B -m unittest tests.runtime.test_selector tests.runtime.test_council -v`. Expect the new promotion fixture to fail.
- [ ] **Implement.** Reorder only fully qualifying slots inside the chosen group. If the baseline winner is near-floor or below-level, or a review has no qualified independent result, use that group's original baseline behavior. Read only comparable candidates' hints and do nothing when fewer than two subscriptions can compete. Keep `_confirm` as the final binding check. If it detects a changed participant after a reorder, fall back to the baseline order of the affected group with changed routes removed; preserve the current recheck and fallback, without a network read. Never expose the ranking evidence in the pick.
- [ ] **Run green:** the same unittest command and `python3 -B scripts/validate.py` pass.
- [ ] **Commit** selector and tests (`feat: balance comparable subscription picks`). Rerun the affected tests after commit.

### Task 4: Add bounded, nonblocking Codex hints and end-to-end checks

**Files:** Modify `runtime/codex_reset.py`, `runtime/opencode_account.py`, `runtime/selector.py`, `scripts/select.py`, `index.js`, and `docs/development.md`. Extend `tests/runtime/test_chatgpt_limit.py`, `tests/runtime/test_readers.py`, `tests/runtime/test_selector.py`, `tests/runtime/test_select_cli.py`, and `tests/plugin/plugin.test.js`.

**Interfaces:**
- `codex_reset.read_windows(*, timeout=TIMEOUT, environ=None, command=None) -> dict[str, Meter]` reuses `_exchange`, its 5-second budget, test-home refusal and allowed environment. `read_reset` retains its old parsing/return contract for the exhaustion path. Only read `account/rateLimits/read`; no account or credit actions.
- `opencode_account.chatgpt_usage_due(settings_path, now: int) -> bool` and `refresh_chatgpt_usage(settings_path, *, environ=None, command=None) -> str` use the existing private OpenCode account store and `_chatgpt` OAuth proof. Return one of `stored`, `busy`, `not_due`, `unbound`, `failed`; callers never see hint or binding data.
- Internal ordinary selector reply includes `usage_refresh: true` only when a qualifying, comparable ChatGPT route can compete with another subscription in the current decision group and a read is due. Strip it in `index.js`; schedule a single bounded `--usage-refresh` child with exact stdin `{}` and fixed output state. No new public tool, scores-refresh changes, or council trigger.

- [ ] **Write tests.** Use existing fake processes, fixture clocks, and a process/IPC barrier for concurrency. Assert:

```python
assert codex_hints["7d"]["used_percent"] == 90                       # duration, not named slot
assert codex_hints["5h"]["duration"] == 18_000
assert (codex_hints["7d"]["earliest"], codex_hints["7d"]["latest"]) == (started, received)
assert codex_extra["duration:1209600"]["discrete"] is False
assert weekly_with_extra_at_85_percent == 8.4 and weekly_with_extra_at_98_percent == 1
assert other_limit_bucket_not_used and conflicting_bucket_views_not_ranked
assert past_reset_or_invalid_duration_hints == {}
assert selection_without_hint["route"]["route"] == baseline
assert selection_after_hint["route"]["route"] == chatgpt
assert selection_after_fresh_reset["route"]["route"] == claude
assert selection_after_unread_reset["route"]["route"] == baseline
assert unknown_chatgpt_baseline_and_comparable_rival["usage_refresh"] is True
assert "usage_refresh" not in unknown_chatgpt_baseline_without_comparable_rival
assert reads_for_api_key_or_unproved_connection == 0
assert reads_for_same_login_aliases_only == 0
assert reads_while_first_worker_holds_lock == 1
assert not due(success_start + 59) and due(success_start + 60)      # during post-reset refresh demand
assert not due(failed_finish + 899) and due(failed_finish + 900)
assert new_binding_hints_after_old_read_releases == {}
assert active_pause_is_unchanged_after_a_healthy_hint
assert no_pause_is_created_by_a_100_percent_hint
```

  CLI: assert malformed stdin is `{"state":"failed"}` and the Codex reader is not entered. Plugin: hold the fake child open and verify two normal picks return without waiting, only one child starts, neither reply exposes `usage_refresh`, and cleanup stops the child. A council or setup call and an already cancelled pick must not start it. Assert a partial valid result does not trigger each pick. On interrupted worker exit, verify the OS lock releases but the durable cooldown suppresses another read.
- [ ] **Run red:** `python3 -B -m unittest tests.runtime.test_chatgpt_limit tests.runtime.test_readers tests.runtime.test_selector tests.runtime.test_select_cli -v` and `node --test tests/plugin/plugin.test.js`. Expect new reader and scheduling assertions to fail.
- [ ] **Implement.** Recognize the documented [`codex` rate-limit bucket](https://developers.openai.com/codex/app-server/#6-rate-limits-chatgpt), using exact `limitId`, explicit `windowDurationMins` and future `resetsAt`. Prefer the `rateLimitsByLimitId.codex` entry when present; if the single-bucket view also identifies `codex`, reject competing claims for the same meter when they disagree. Do not use other buckets, credit fields or slot names to infer scope. Verified 300-minute and 10,080-minute windows can grant boosts. Other plausible durations in that bucket use a `duration:<seconds>` key and `discrete=False`; the fixture is a 14-day meter resetting in 10 days, paired with the 8.4 weekly reference. Preserve a valid independent sibling; all missing/unknown is failure, a usable partial response succeeds. A missing weekly sibling still vetoes a 5-hour boost.

  Stamp `earliest=floor(start)` immediately before the bounded exchange and `latest=ceil(receipt)` when it returns. The [upstream handler](https://github.com/openai/codex/blob/main/codex-rs/app-server/src/request_processors/account_processor.rs) fetches backend usage per request; the interval bounds this exchange, not the age of the provider's accounting. Do not infer a cache age or renew the hint from a notification. Decide refresh demand before scoring: an unknown ChatGPT winner still needs a read if a rival subscription is comparable. Keep raw replies, account labels, and credit details out of the store.

  Use a dedicated nonblocking `storage._locked` lock for the refresh child, separate from the account store's write lock. Recheck due status and the proved key/generation inside it. Before the exchange store an interrupted/failed-by-default start record so a killed worker cannot cause retry churn; after a returned failure calculate cooldown from completion. After a success, allow one follow-up when the 900-second age or the earliest previously known reset is reached, no sooner than 60 seconds after that start. Evaluate each meter's expiry independently. Publish via the existing account transition only if the same complete, current OAuth binding is still proved; read hints only under that binding and marker. A recorded logout/rotation invalidates old hints; a host login change the existing account reader has not observed yet is not detectable by this cache. Do not add polls or read Codex auth files. Use the plugin's bounded child lifecycle and shutdown behavior, with a separate in-process in-flight guard so unchanged score refreshes do not block quota scheduling.

  Document the accepted identity/proxy limits, public Go reset evidence, and lack of live calibration in `docs/development.md`; do not claim a new guarantee about paid overflow, usage overlap, or account identity.
- [ ] **Run green:** rerun both targeted commands and `python3 -B scripts/validate.py`; all pass. Confirm the ordinary selector still reports `admission=false`, `reservation=false`, and `spend_permit=false`.
- [ ] **Commit** Codex integration, tests and developer notes (`feat: refresh ChatGPT balance hints on demand`). Rerun affected tests after commit.
- [ ] **Verify the branch.** Run `python3 -B -m unittest discover -s tests -t .`, `node --test "tests/**/*.test.js"`, and `python3 -B scripts/validate.py`. Use a fresh local output path `dist/subscription-usage-balance` for `python3 -B scripts/package.py --output dist/subscription-usage-balance`, then run `python3 -B scripts/validate.py --root dist/subscription-usage-balance`. Do not publish it. Require an independent whole-branch behavior and security review before acceptance. Report deterministic counts and any unverified live behavior separately.
