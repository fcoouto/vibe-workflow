# Claude Quota Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop recommending Claude routes covered by independently confirmed exhausted subscription meters, even when sibling meters are absent.

**Architecture:** Classify independently usable meters from the existing native exchange, and keep bound exhaustion evidence separate from the full usage snapshot. Use that evidence in the existing eligibility gate. A verified OpenCode failure can trigger one background, read-only probe; the failure alone never excludes a route.

**Tech Stack:** Python 3.11+ standard library, JavaScript ES modules, OpenCode V2, `unittest`, Node's built-in test runner.

**Spec:** [Claude quota evidence design](../specs/2026-11-26-claude-quota-evidence-design.md).

## Global Constraints

- No pre-limit ranking, task level, effort, reviewer independence, paid overflow, or other subscription changes. No exact-route fallback.
- No polling, model prompts, launches, retries, reservations, spend authority, direct subscription HTTP calls, added dependencies, or additional usage reads on the normal pick path.
- Use the existing private state directory, safe storage, native reader, account continuity and catalog. Do not persist credentials, identity labels, raw errors, prompts, or transcripts.
- The actual failure class was observed on OpenCode 2.3.2 and the usage shape on Claude Code 2.1.323. Test with invented data and isolated homes. Do not run a live quota check, publish, or install.
- Do not change injected notes. Follow the repository's existing version-test exception if it triggers.

## Review Focus

1. A full weekly cap with `five_hour: null` must exclude Claude without treating the separate 71% scoped meter as exhausted (Task 1).
2. Raw 99.4% and normalized 100% on the same reset are not positive quota evidence, even though their percentages are within the rounding tolerance (Task 1).
3. A cached or late positive read must not re-create a pause after a provably newer recovery (Task 2).
4. A slow probe or concurrent login switch must not stall picks or attach a pause to the new login (Task 3).
5. The failed assistant's model, not the parent or current session model, controls failure attribution; another location or a duplicate event must not start a probe (Task 4).

---

### Task 1: Parse quota evidence independently of snapshot completeness

**Files:** Create `runtime/claude_quota.py` and `tests/runtime/test_claude_quota.py`; modify `scripts/manifest.py` and `tests/tooling/test_package.py`. `package.json` already includes `runtime`.

**Interfaces:**
- Produces `claude_quota.evidence(use, models, *, started, received) -> dict | None`. `started` and `received` are epoch seconds. `models` uses the native catalog's `{alias, resolved, efforts}` records. Result: `{earliest: int, latest: int, meters: dict[str, dict], scopes_complete: bool}`. Each meter is `{exhausted: bool, reset_at: int}`. IDs are `5h`, `7d`, `family:<token>`, or `model:<normalized-resolved-id>`.
- Produces `claude_quota.covers(meter_id, model, models) -> bool`, where `model` is the route's native model ID and `models` is the currently bound native catalog. This function does not decide account binding.

- [ ] **Write failing parser tests** in `test_claude_quota.py` using the native `get_usage` envelope. At `now = 1893570000`, use `started=now + 0.25`, `received=now + 0.75`, and weekly resets at `now + 3600` with differing fractions of that same second. Pin:

  ```python
  # account_wide_without_five_hour: confirmed raw weekly_all at 100, normalized
  # seven_day at 100, five_hour=None, weekly_scoped/model_scoped at 71.
  self.assertEqual(evidence["meters"]["7d"], {"exhausted": True, "reset_at": now + 3600})
  self.assertFalse(evidence["meters"]["family:example"]["exhausted"])
  self.assertNotIn("5h", evidence["meters"])
  self.assertEqual(evidence["earliest"], now - 60)
  self.assertEqual(evidence["latest"], now + 1)
  # conflicting_percentage_is_unknown: same reset, raw 99.4 and normalized 100.
  self.assertNotIn("7d", conflicting["meters"])
  self.assertTrue(conflicting["meters"]["family:example"]["exhausted"])
  # missing_limits_does_not_confirm_reply and header_only_does_not_confirm_reply.
  self.assertIsNone(unconfirmed)
  # scoped_only_and_exact: exact "Claude Example 5.2" covers two aliases of
  # that resolved version but neither "example" at a different version nor another family.
  self.assertTrue(covers("model:example-5-2", "alias-of-5-2", models))
  self.assertFalse(covers("model:example-5-2", "example", models))
  # absent_normalized_scopes: confirmed raw limits with no scoped entries.
  self.assertTrue(empty["scopes_complete"])
  # unknown_or_malformed_raw_entry: valid weekly survives, inventory cannot clear old scopes.
  self.assertTrue(partial["meters"]["7d"]["exhausted"])
  self.assertFalse(partial["scopes_complete"])
  # idle_meter_is_unknown: five_hour={"utilization": 0, "resets_at": None}.
  self.assertNotIn("5h", idle["meters"])
  # unresolved_scope_is_not_empty: otherwise valid scoped entries with generic,
  # unmapped, or ambiguous labels prevent a complete inventory.
  self.assertFalse(unresolved["scopes_complete"])
  # scoped_meter_present_but_unusable: idle, expired, or conflicting duplicate.
  self.assertNotIn("family:example", unusable["meters"])
  self.assertFalse(unusable["scopes_complete"])
  ```

  Assert `None` or out-of-range percentages, booleans, NaN, infinite values, reset ≤ receipt, and reset beyond the meter's window plus `storage.CLOCK_MARGIN` do not yield a meter. Test `weekly_all` alone with `seven_day: None`, matching percentage with different resets, `...00.999Z` versus `...01.001Z`, raw-only and normalized-only scoped entries, and `weekly_all` with an explicit model scope. Assert non-finite, boolean, or reversed observation times return `None`; a non-list `model_scoped`, non-null legacy scoped meter, or unknown top-level meter-like field makes inventory incomplete. Test a direct route ID and two aliases resolving to `claude-example-5-2`, another version, an ambiguous alias, and `resolved: None`.
- [ ] **Run RED:** `python3 -B -m unittest tests.runtime.test_claude_quota`. Expected: missing module/interface before implementation.
- [ ] **Implement both interfaces.** Require `rate_limits_available is True` and a raw `limits` list. Recognize normalized account windows, explicitly unscoped `weekly_all` as `7d`, and `weekly_scoped` with a model scope (raw or via `model_scoped`). Do not guess additional raw `kind` values. Attribute an older unscoped raw repeat only if its whole-second reset matches exactly one normalized meter, regardless of percentage. A legacy row matching both or neither is unknown. Compare identified duplicates by whole-second reset, within one percentage point, and exact agreement on `>= 100` before rounding. A malformed or conflicting recognized duplicate makes only that meter unknown. Require finite, nonnegative times with `started <= received`; use floor of `started` minus the existing 60-second cache ceiling (clamped to zero) and ceiling of `received`. Emit a meter only with a valid, bounded reset strictly after actual receipt. Idle `0%` with no reset is unknown, not recovery.

  Normalize scope labels, catalog aliases, resolved IDs, and candidate identifiers the same way: casefolded ASCII alphanumeric tokens joined by `-`, ignoring the generic leading `claude`. A versioned label must match one exact normalized resolved ID. A bare family label must name a current bare native alias that resolves to the same leading family token. `covers` resolves the candidate by exact normalized alias or direct resolved ID in the catalog. Multiple aliases of one resolved ID are valid; conflicting resolutions, ambiguity and `resolved: None` cannot prove coverage. Never drop version tokens to match more routes.

  `scopes_complete` is false if **any present scoped entry** fails to yield a valid meter, including idle, expired, malformed, conflicting, generic, unmapped, ambiguous or over-cap entries. A malformed `model_scoped` field, non-null legacy scoped field (`seven_day_oauth_apps`, `seven_day_opus`, `seven_day_sonnet`), unrecognized raw row, or unknown top-level meter-like field also makes inventory incomplete. Reuse the existing meter-like shape check. These conditions do not invalidate independent known meters. Bound recognized scope IDs to eight.
- [ ] **Register and verify the file.** Add `claude_quota` to `scripts/manifest.py` and the package test's expected module list. Run `python3 -B -m unittest tests.runtime.test_claude_quota tests.tooling.test_package` and `python3 -B scripts/validate.py`. Expected: all tests and validation pass.
- [ ] **Commit** parser, tests and export registration as `feat: classify Claude quota meters independently`.

### Task 2: Store account-bound evidence and use it in eligibility

**Files:** Modify `runtime/claude_quota.py`, `runtime/claude_usage.py`, `runtime/opencode_account.py`, `tests/runtime/test_claude_quota.py`, and `tests/runtime/test_readers.py`.

**Interfaces:**
- Extends `claude_quota` with `merge(previous, sample, *, now) -> dict` and `remaining(record, model, models, *, now) -> int | None`. A record has `{meters: dict, inventory: dict | None}`. Retained meters add `earliest` and `latest` to the meter fields. A complete inventory stores `{scopes: list[str], earliest: int, latest: int}`. Neither shape contains an account ID; the native reader binds it under lock.
- Produces private `claude_usage._quota_change(state, expected, sample, now) -> dict` for both acquisition and probe commits. Returns the change `{quota: {key, generation, record}}` for the expected native `(key, generation)` or raises if the stored continuity no longer proves it. Use an empty previous record unless its binding matches exactly; a `None` sample never adds or clears evidence.
- Produces `claude_usage.exhaustion_left(settings_path, route, now) -> int | None`. Consumers use it only after checking the current OpenCode Claude billing binding.
- Preserves `opencode_account.quota_problem(settings_path, route, now)` and `quota_retry_after`; the latter becomes remaining seconds until reset for confirmed Claude quota. Go and ChatGPT retain their existing implementations.
- Changes the private native exchange to `exchange(command, environ, timeout=TIMEOUT) -> tuple[dict, dict, float, float]`, returning `(init, use, started, received)`. Capture `received` on reading the usage response, before child cleanup; thread it into `_record` and the evidence parser.

- [ ] **Write failing merge and acquisition tests.** Use the existing fake native exchanges. With `t = 1893570000`, `reset = t + 3600`, and merge clock `now = t + 200`, give the exhausted mark interval `[t - 60, t]`, overlapping healthy interval `[t - 30, t + 30]`, and confirmed recovery interval `[t + 1, t + 61]`. Supply a delayed positive result `[t + 50, t + 111]` that arrives after recovery, then a boundary positive `[t + 61, t + 121]` and a provably newer positive `[t + 62, t + 122]`. Assert:

  ```python
  self.assertEqual(remaining(marked, "invented", models, now=reset - 1), 1)
  self.assertIsNone(remaining(marked, "invented", models, now=reset))
  self.assertEqual(merge(marked, overlapping_healthy, now=now), marked)
  self.assertIsNone(remaining(cleared_by_newer, "invented", models, now=now))
  self.assertEqual(merge(cleared_by_newer, delayed_exhausted, now=now), cleared_by_newer)
  self.assertEqual(merge(cleared_by_newer, boundary_exhausted, now=now), cleared_by_newer)
  self.assertEqual(remaining(merge(cleared_by_newer, newer_exhausted, now=now),
                             "invented", models, now=now), reset - now)
  self.assertEqual(openc.quota_problem(settings, claude_route, now), "quota_exhausted")
  self.assertIsNone(openc.quota_problem(settings, unrelated_go_route, now))
  ```

  Add these regressions:

  - A complete empty inventory `[t+101, t+161]` removes a scoped mark. Overlapping complete inventory `[t+110, t+170]` containing that scope cannot replace the absence record. Another positive at `[t+120, t+180]` cannot restore it; `[t+162, t+222]` can when evaluated after `t+222`. Verify the empty inventory does not suppress a `7d` positive and does not fence scopes it actually lists.
  - A first healthy meter `[t+140, t+200]` commits before an older 100% response `[t+40, t+100]`; assert no pause even without a prior recovery. Assert idle, expired, conflicting or unresolved scoped entries do not clear a prior unexpired mark, and meter-only recovery still works when an unrelated field is absent.
  - Catalog missing or bound to a different generation: a current account-wide mark returns `reset - now`; a scoped-only mark returns `None`. A direct resolved route is blocked when its catalog is bound. Exercise binding with the same synthetic `now` as quota evaluation, not wall clock.
  - Update the old 60-second Claude expiry assertions to check exclusion at `now + 61` and expiry at reset. Use a reset less than `FRESH_SECONDS=900` from the bound account observation for integration tests of that boundary; otherwise billing freshness, not quota expiry, decides.
  - Verify the exchange's `received` timestamp precedes cleanup: a fake clock advances during child cleanup, but the returned `received` stays at the usage response.

  Cover weekly-only and scoped-only acquisition, old-format caches, account switches, and unsafe or malformed evidence. Run matching and unrelated routes through `routing_observations.eligible` and `council.roster` with the real local source and synthetic billing; give the unrelated Go route valid billing and no exhausted meter. Verify valid partial quota data does not publish a complete usage snapshot.
- [ ] **Run RED:** `python3 -B -m unittest tests.runtime.test_claude_quota tests.runtime.test_readers`. Expected: missing merge interfaces and persistence/gate expectations fail.
- [ ] **Implement the merge.** Prune meter entries at reset. Keep positive and non-exhausted entries, even if no pause existed. A new observation can replace an existing meter only if `new.earliest > old.latest`; equality and overlap are not newer. This can defer a change within the native cache window rather than guessing which sample came first. Retain at most two account-wide and eight scoped keys. Keep existing positives first, then new positives, then most recently observed healthy entries; resolve ties by key. A complete inventory fences only **scoped keys absent from its `scopes` list**. It is live until `inventory.latest + 7*86400 + CLOCK_MARGIN`. Replace live inventory only when `sample.earliest > inventory.latest`. Only an accepted complete inventory can clear an omitted scoped mark, and only when that interval is newer than the mark. A positive result may override an applicable absence fence only if `new.earliest > inventory.latest`. Account-wide meters and scoped keys listed in the inventory are not fenced by it. No unknown or invalid row can extend or clear evidence. `remaining` returns the largest future `reset_at - now` across applicable validated marks; malformed entries cannot exclude.
- [ ] **Wire the normal acquisition and quota gate.** Compute evidence independently in `_record` after `check_quiet` and identity/catalog proof. Merge it into `claude-account.json` via `_quota_change` in the existing account publish transition even if `parse_usage` rejects a full snapshot. `_quota_change` checks key and generation, and may accept the pending account from `_record`; only the probe adds a complete/current precondition to its own transition. `exhaustion_left` checks the current account and quota binding, `opencode-claude-subscription` path and Claude pool; do not use `claude_usage.applies`, which matches a different path. Compare `native_catalog.stored(..., CLIENT)["binding"]` against the expected binding derived from that account and the supplied `now`, not `binding()`'s wall clock. Use an empty models list if missing or unbound; only scope matching needs it. The read takes no process or write lock. In the OpenCode Claude branch use `exhaustion_left` instead of old window and scoped checks. Do not use old rounded or cached rows as alternate proof. No migration: an old cache gets evidence on its next ordinary native acquisition or probe. Preserve the full-snapshot reader and billing behavior; remove obsolete quota-only code if unused.
- [ ] **Verify GREEN:** `python3 -B -m unittest tests.runtime.test_claude_quota tests.runtime.test_readers tests.runtime.test_selector tests.runtime.test_council tests.runtime.test_chatgpt_limit`. Expected: all pass; existing Go, ChatGPT, paid overflow and ranking assertions still pass.
- [ ] **Commit** as `feat: gate Claude routes on bound exhaustion evidence`.

### Task 3: Run a bounded, nonblocking probe against the proved login

**Files:** Modify `runtime/claude_usage.py`, `runtime/opencode_account.py`, `scripts/select.py`, and `tests/runtime/test_readers.py`. Reuse `tests/runtime/native_reader.py`; extend its synthetic transport only if an existing mode cannot exercise the requested behavior.

**Interfaces:**
- Produces `opencode_account.probe_claude_limit(settings_path, *, reset_at, command=None, environ=None, timeout=claude_usage.TIMEOUT) -> dict` with `state` in `{"stored", "skipped", "failed"}`. It obtains a current, proved OpenCode Claude binding and invokes the native probe in this task.
- Produces `claude_usage.probe_limit(settings_path, *, expected, reset_at, command=None, environ=None, timeout=TIMEOUT) -> dict` with the same states. `expected` is the current native `(key, generation)` tuple from the proved binding. Reuses `exchange`, `check_quiet`, `parse_identity` and `evidence`.
- Produces internal CLI `scripts/select.py --state-dir DIR --claude-limit`, reading exactly `{"reset_at": <integer epoch seconds>}` on stdin and returning only the probe state. Require an absolute, nonsymlink state directory and existing private storage checks. No new tool or public selector fields.

  State contract: `{"state":"stored"}` means usable evidence was processed under the proved binding, including a complete empty inventory; `{"state":"skipped"}` means no current binding, busy or cooling down, missing usable evidence, an expired/out-of-range integer hint, or a binding change (including a proved different login). `{"state":"failed"}` covers malformed request, malformed or unsafe private state, unsupported or unproved identity, invalid telemetry, timeout or cancellation. A wait never triggers a queued retry.

- [ ] **Write failing probe tests** with fake processes and event barriers, not sleeps. Assert a proved binding and confirmed `weekly_all` at 100% change eligibility with only `initialize` and `get_usage` controls. For a hint at `now + 3600`, give only another model's confirmed exhausted scope resetting at `now + 7200`; assert a 7200-second pause for that scope and no pause for the failed model. Have `initialize` advertise a different alias from the stored one; classify scopes from the stored bound catalog only. An absent or unbound catalog still permits a valid weekly account-wide mark.

  With a blocked fake child, assert ordinary reads retain the proved binding, the selector finishes before releasing the child, and only one exchange starts across consumers sharing state. Assert one observation can apply two exhausted scopes; cooldown skips at 59 seconds, on a backwards clock, and on a generation change of the same key, but not at 60 seconds. Test both sides of the allowed reset range including equality at the upper bound and expiration before commit. Assert the stated fixed return states; unsafe path (including relative path and symlink), unsupported identity, nonzero telemetry, missing/expired/pending binding, unconfirmed usage and interruption never publish quota. A proved different login invalidates only the still-current old binding; a concurrently rotated account is preserved. Cover malformed CLI input and no secret output.
- [ ] **Run RED:** `python3 -B -m unittest tests.runtime.test_readers`. Expected: probe interface and CLI mode absent.
- [ ] **Implement the native probe and CLI.** Reuse the four-result `exchange` directly, not `refresh`. The hint `now < reset_at <= now + 7*86400 + storage.CLOCK_MARGIN` is a trigger check before start and at commit; never compare it to returned meter resets. Use shared nonblocking `storage._locked(..., block=False, lock=".claude-limit-probe.lock")` plus private `claude-limit-probe.json` cooldown `{version: 1, key, started_at}`. Throttle by native account key across generations. If `now - started_at < 60` for that key (including a negative value), skip. Write the watermark before exchange; hold only the probe lock during the child. Do not fence account state during the read.

  After `check_quiet` and identity proof, recheck a *complete*, fresh native account and exact expected key/generation inside the short `_transition` before calling `_quota_change`. For `evidence`, use models from `native_catalog.stored(..., CLIENT)` only when bound to that same account, else `[]`; share the Task 2 read-side binding rule. Do not use or publish the probe's `initialize` catalog or billing metadata. On proof of a different subscriber, invalidate the old binding only if still complete, fresh, and matching; ordinary acquisition qualifies the replacement. An unsupported identity is not proof of a different login and cannot invalidate it. On failure or cancellation do not write quota or retry. Preserve the existing bounded CLI input and SIGTERM/SIGINT cleanup; output the fixed state only.
- [ ] **Verify GREEN:** `python3 -B -m unittest tests.runtime.test_readers tests.runtime.test_claude_quota`. Expected: all tests pass; specifically prove the normal selector does not wait for a probe with an already-current binding.
- [ ] **Commit** as `feat: probe Claude quota off the pick path`.

### Task 4: Trigger the probe from the exact failed Claude step

**Files:** Create `src/claude-limit-events.js` and `tests/plugin/claude-limit-events.test.js`; modify `index.js`, `tests/plugin/plugin.test.js`, `package.json`, `scripts/manifest.py`, `tests/tooling/test_package.py`, and `docs/development.md`.

**Interfaces:**
- Produces `createClaudeLimitHandler(ctx, { location, signal, enabled, probe, now = Date.now }) -> (event) => void` in `src/claude-limit-events.js`. `enabled() -> Promise<boolean>` is local settings, `probe({ reset_at }) -> Promise<unknown>` is the existing plugin runner with operation `claude_limit`. Invoke the handler without awaiting inside the existing single `session.step.failed` subscription in `index.js`; preserve ChatGPT's branch and fallback envelope handling.
- Consumes Task 3's CLI. Use `ctx.session.context({ sessionID }, { signal: lookupSignal })` and match exactly one row with `type === "assistant"` and `id === data.assistantMessageID`. Require `model.providerID === "claude-agent"` and nonempty `model.id`; actual OpenCode 2.3.2 rows use `id`, not `modelID`. Accept the array or `{data: [...]}` envelope. Never use the session's current model.

- [ ] **Write failing event tests** for the handler and JS-to-CLI handoff using the fake runner. Freeze `now` before the spec's sanitized UTC reset. From the precise Claude assistant in the same location, `.000Z`, `.500Z` and `Z` in that fixture each yield `probe({reset_at: 1893578400})` in separate cases. Test invalid calendar dates and both edges of `now < reset_at <= now + 7*86400 + 60`. Assert zero probes for wrong directory/workspace (including a different symlink spelling), `"anthropic"` or `"opencode-go"` as provider, missing `model.id`, wrong or non-unique assistant ID, missing context API, disabled project, generic 429, auth error, forged substring, and abort during delayed context/settings. An omitted `workspaceID` matches only another omitted value. Use the middle dot and final period in the fixture. Give a nested agent parent with a different model; it is not consulted.

  Hold the context call open past its two-second signal deadline and assert no probe. Send a duplicate and a distinct Claude failure during both lookup and active probe; assert only one active lookup/probe and no queued launch. Check the plugin has exactly the existing single failure subscription, the fake runner receives only `--claude-limit` and the `reset_at` JSON, and unload aborts the operation. With a controllable clock and hung fake child, assert no timeout before 15,000 ms, `SIGTERM` at 15,000 ms, and no retry.
- [ ] **Run RED:** `node --test tests/plugin/claude-limit-events.test.js tests/plugin/plugin.test.js`. Expected: new watcher and CLI handoff absent; existing ChatGPT cases still pass.
- [ ] **Implement the handler and handoff.** Accept the verified `provider.unknown` weekly shape only in this release: `You've hit your weekly limit · resets `, a nonempty human-readable clause (do not parse it; no newline), then ` Resets at `, an ISO UTC timestamp and final period. Do not guess other failure text, including five-hour notices; verified five-hour meter evidence still applies. Cap the whole message at 512 characters. Allow absent or one to three fractional digits in the timestamp; pad to three for ISO round-trip validation and floor to integer Unix seconds before validating the same reset range as Task 3 and passing it to CLI.

  Require matching absolute directory strings and equal workspace IDs (absent equals absent); do not follow symlinks or treat a missing event location as local. Require the context API to be callable. Deduplicate by bounded durable event IDs and take one per-instance slot *before* async settings or context reads and release in `finally`. The context request gets `AbortSignal.any([signal, AbortSignal.timeout(2000)])`; if the host ignores it, discard a late result and do not open another slot until the original settles. Recheck settings, abort and timestamp before launch. The handler never awaits in the parent event loop or queues work; Python enforces the shared guard. Add `claude_limit` to `runRouter` with the `--claude-limit` flag, existing cancellation, and a 15-second timeout; no user-visible output. Keep one failure subscription and preserve `markLimit` and its HTTP fallback. Register the new JS path in `package.json`, `scripts/manifest.py` and the export test.
- [ ] **Update development docs** with the accepted meter shapes, failure trigger, bound pause behavior, safe handling of unknowns, and the limit of synthetic coverage. Do not edit injected notes or rewrite historical observations.
- [ ] **Verify GREEN:** `node --test "tests/**/*.test.js"`, `python3 -B -m unittest discover -s tests -t .`, and `python3 -B scripts/validate.py`. Package to a fresh path with `python3 -B scripts/package.py --output "$OUT"` and run `python3 -B scripts/validate.py --root "$OUT"`. Expected: no failures, packaged export includes both new modules, and no changes to the injected notes.
- [ ] **Commit** as `feat: observe Claude subscription-limit failures`. Re-run affected tests after the commit. Request independent behavior/security review for the complete implementation before any merge or release; this plan authorizes neither.
