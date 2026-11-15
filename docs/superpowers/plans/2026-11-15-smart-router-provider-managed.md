# Smart Router Provider-managed Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prefer trustworthy included allowance, otherwise route eligible enabled
accounts through their existing provider-managed settings without inventing quota.

**Architecture:** Extend the existing fallback branch, account producers and ledger.
Keep one selector and one attempt lifecycle. Add only one small fallback helper
module because `runtime/routing.py` is already at its fixed line ceiling.

**Tech Stack:** Python 3.11+ standard library, existing SQLite/JSON stores, `unittest`.

**Spec:** [Approved provider-managed design](../specs/2026-11-15-smart-router-provider-managed-design.md),
with the [original redesign and amendments](../specs/2026-11-10-smart-routing-redesign-design.md).

Status: Opus passed the corrected plan after the focused review recheck.
The user authorized implementation after a passing review, without another
confirmation prompt. No live operations follow from that authority.
Base: `ce1170192a9661ff1dca9504a085c8203bc9189d`. This plan replaces only the
affected source/admission/qualification tasks in the
[delivery map](2026-11-15-smart-router-delivery-streams.md), not its remaining release obligations.

## Global Constraints

- Preserve all five subscription paths, practical connection-bound identity,
  explicit model/effort/funding choices, existing settings and actual resource limits.
- Unknown quota/demand/cost is not zero, headroom or permission to spend direct API money.
- API Usage stays required next release; `API_FUNDING_QUALIFIED=False` and production
  OpenRouter stays deferred. Preserve the direct API whole-task 20% money buffer.
- No upstream contact, new source investigation campaign, credential access,
  live reads/model sends, installation, activation, canary or account-setting change.
- No extra model calls to measure/rank, polling, daemon, new store, scheduler,
  account framework or artificial worker-count cap.
- Reuse supported aliases and the current shortlist (at most 10). No production
  model names. Preserve current file ceilings; do not enlarge them.
- TDD with disposable stores/transports. For each task: observe the focused failing
  test, make the minimal change, validate, commit, then run the selected sharded
  tests on that commit. Keep reviewed task boundaries; no concurrent shared writers.
- Keep the previously selected provider-owner/integration-owner execution method.
  Claude implementation workers use the user's Sonnet/Opus choice; coordinator
  settings remain unchanged. This plan authorizes no implementation dispatch.

## Review Focus

1. A new quota reading arrives while fallback still consumes: included admission
   stays fenced until all exposure resolves and covered quota arrives (Task 1).
2. An included permit was prepared before fallback started or completed: pre-send
   recheck refuses it without a replacement permit (Task 1).
3. Unknown demand prevents account acquisition/route generation before fallback
   can be considered: account/launch-only qualification must reach admission (Task 2).
4. A failed quota read hides valid native metadata or repeatedly spawns a reader:
   preserve independent metadata and bounded acquisition reuse (Task 3).
5. OpenCode shares a provider/pool label but not the login: never borrow native
   identity/allowance; keep unsupported paths blocked and visible (Task 4).

## File map and implementation choices

| Task | Owns | Responsibility |
| --- | --- | --- |
| 1 | New `runtime/subscription_fallback.py`, `tests/test_subscription_fallback.py`; existing dispatch/record/reconciliation seams below | Exposure fence and safe terminal coverage. No new unknown-quota admissions yet. |
| 2 | `runtime/routing_paths.py`, `routing_observations.py`, `routing.py`, `routing_funding.py`; `scripts/routing.py`; matching tests | Account/launch qualification independent of quota, included-first classification and replayable records. |
| 3 | `runtime/claude_usage.py`, `codex_account.py`, `adapters/native_catalog.py`, `native_routing.py`; `LocalSource` billing/acquisition seams | Reuse native metadata; Claude first, then Codex. |
| 4 | Existing `runtime/opencode_launcher.py`, `adapters/opencode.py`, `opencode_subscription.py`; matching tests | Only evidenced selected-connection bindings; no speculative provider modules. |
| 5 | Existing readiness/replay consumers, qualification docs and client contracts; matching tests | Five-path integration, honest readiness and updated live gates. |

Decisions to avoid extra machinery:

- Reuse `routing.FALLBACK` (`provider_managed_fallback`) and the existing candidate
  `fallback` flag as the admission-basis marker. Keep `reserved={}` and empty
  constraints for fallback. Use existing `reason_codes` for `quota_unknown`,
  `quota_insufficient` or `quota_fenced`; add no `quota_state` field. Preserve
  other evidence references and existing replay detail.
- Represent unmeasured subscription qualification demand as explicit JSON `null`,
  not `{}` or guessed numeric demand, in the existing qualification file. Existing
  measured records remain readable; gateways/direct API cannot use null demand.
  Older packages may refuse null records; never rewrite them for a downgrade.
- Fence conservatively by the existing pool key. A shared label is still not
  account/sharing proof; over-fencing may suppress an optimization, never permit
  borrowed quota. Fallback concurrency remains eligible.
- Keep old replay behavior. Existing empty-constraint fallback selection already
  follows stored order through `routing_funding.choose`; do not add a replay
  version or a second allocator unless an actual regression demonstrates the need.

### Task 1: Make fallback exposure safe across dispatch and completion

**Files:** create the helper/test named above; modify `runtime/routing.py`
(`_decide`, `dispatch`), `runtime/routing_launch.py:_covered_or_marked`,
`runtime/subscription_reconcile.py:reconcile` and `scripts/routing.py`
dispatch/reconcile wiring. Cover the existing marks in
`runtime/delegation_routing.py:_close` and `runtime/native_routing.py:complete`;
retain their code when already correct. Test in `tests/test_routing_dispatch.py`,
`test_routing_launch.py`, `test_routing_subscription_reconcile.py`,
`test_delegation_routing_consumed.py` and `test_native_routing.py`.
Only forwarding/replacement edits in ceiling-bound modules; move responsibility
into the focused helper instead of growing those files.

**Interfaces:** helper `active_pools(data) -> set[str]` derives dispatched/uncertain
fallback exposure from `Ledger.open_holds` and its admission markers.
`dispatch_problem(data, record, settings_path) -> str | None` checks included
admissions against that exposure and the current consumption boundary. Refuse if
the pool is active or `consumed_at(settings_path, pool) >= record['at']`: every
original reading predates or equals admission time, so this is conservative.
Keep `mark_consumed(settings_path, pool, at, attempt)` unchanged and call it at
terminal completion only, before ledger release. Extend only
`reconcile(ledger, event, now, *, settings_path=None)` to pass the actual store
from its service owner; a fallback cannot close without that required context.
No `before_close` callback, send-time marker or new timestamp field is needed.

- [ ] **1. Write failing lifecycle tests.** In the new test module, extend existing
  `Stores`/real-service fixtures. Pin these assertions:

  ```python
  # test_read_during_running_or_uncertain_fallback_does_not_restore_included
  self.assertIn(pool, active_pools(ledger.snapshot()))
  self.assertIsNotNone(dispatch_problem(ledger.snapshot(), included_record, settings))
  # test_concurrent_fallbacks_need_terminal_coverage_for_every_attempt
  self.assertEqual(fallback_admission["reserved"], {})
  self.assertFalse(included_allowed_while_either_attempt_is_open)
  self.assertFalse(included_allowed_from_read_before_last_terminal_consumption)
  ```

  The last three names describe values captured from real service responses and
  fixture stores, not new production APIs. Also pin prepared-before-send and
  prepared-before-completion refusal, failed terminal write, repeated events,
  empty-reservation reconciliation/abandonment, and exposure surviving pruning.
- [ ] **2. Run the new module with unittest; observe intended failures.**
  `python3 -B -m unittest tests.test_subscription_fallback`.
- [ ] **3. Implement the helper and existing terminal seams.** Scan exposure inside the same
  ledger transaction used for admission/dispatch. Include fallback holds with
  empty windows. Write terminal boundaries before closing any supported complete/
  reconciled/abandoned fallback; provably-unsent cancellation adds no usage.
  `_covered_or_marked` must mark a `routing.FALLBACK` admission even when its empty
  claimed set is covered. Native/managed completion already mark before release;
  add the missing mark for both reconcile/abandon branches after validating the
  event and before closing. Keep existing once-per-attempt idempotence and `max()`
  ordering across concurrent completions. Read-only checks start no process.
  Failed marks retain the attempt/exposure. Add no send-time store write.
- [ ] **4. Verify focused tests pass; validate and commit.** Then run
  `python3 -B scripts/test_sharded.py test_subscription_fallback test_routing_dispatch test_routing_launch test_routing_subscription_reconcile test_delegation_routing_consumed test_native_routing`.
  Obtain independent lifecycle/accounting review before Task 2 uses this seam.

### Task 2: Admit eligible accounts without demanding usable quota

**Files:** Task 2 files in the map; existing `tests/test_routing_paths.py`,
`test_routing_observations.py`, `test_subscription_acquisition.py`,
`test_routing_ordered_fallback.py`, `test_routing_production_flows.py`,
`test_qualification_replay_strict.py`; replay consumer only if a failing test
proves it needs adjustment.
Name the null-demand callers explicitly: `routing._valid_route`, `_incomplete`,
`_candidate`; `routing_funding._eligible`; `routing_observations.acquire_billing`,
`shortlist`, `collect`; and path generation/validation. `routing.py` is at its
1415-line ceiling, `usage_budget.py` at 1467 and `scripts/routing.py` at 997/1000.
Keep edits within those limits; use the focused helper, not a ceiling increase
or an unrelated refactor. No generic ledger edit is required by this plan.

**Interfaces:** retain `qualify(settings_path, path_id, *, demand, evidence,
account=None, now, rules=None, allow_test_only=False)` but allow `demand=None` only
for subscription account/launch qualification. Retain `LocalSource.billing`,
`acquire_billing`, `model_demand`, `collect` and `_candidate` signatures.
Nullable demand and empty window lists are valid only for eligible subscription
fallback; paid routes and included claims retain their strict shapes.

- [ ] **1. Write failing tests for launch-only qualification and both branches.**

  ```python
  # test_subscription_launch_qualification_does_not_require_quota_seed
  self.assertIs(paths.qualifications(settings)["native-path"]["demand"], None)
  # test_unknown_quota_is_fallback_not_headroom
  self.assertEqual(answer["admission"]["reserved"], {})
  self.assertIn(routing.FALLBACK, answer["admission"]["reason_codes"])
  self.assertIn("quota_unknown", stored_record["reason_codes"])
  ```

  Use the real collector with isolated source doubles; table-test absent/stale/
  partial/malformed/unsupported quota and absent/unverified model demand. Pin
  `test_known_included_wins_unless_explicit_choice_excludes_it`, unknown overflow,
  provider refusal, nullable gateway rejection and unchanged historical replay.
  Empty or malformed numeric demand must never become an included claim.
- [ ] **2. Run affected unittest modules; observe failures for the new assertions.**
- [ ] **3. Remove only quota prerequisites from account eligibility.** Update
  qualification parsing/generation, `_model_demand` prefilters and collection so
  missing demand/windows do not hide an eligible account. Keep billing/identity,
  catalog, host, authority, entitlement and path-qualification checks.
  Do not start a known unsupported quota reader just to choose fallback.
- [ ] **4. Extend the existing candidate decision.** Perform non-quota eligibility
  first. If complete supported quota/demand fits and Task 1 permits it, use the
  unchanged reservation allocator. Otherwise create the existing empty-constraint
  fallback candidate with its quota disposition. Apply explicit constraints
  before `prefer_included`; then included first, otherwise shortlist value order.
  Reuse `routing_funding.record`; export/replay actual decisions without live reads.
  Do not catch unrelated integrity, identity or provider-refusal errors as fallback.
- [ ] **5. Verify, validate, commit and run**
  `python3 -B scripts/test_sharded.py test_routing_paths test_routing_observations test_subscription_acquisition test_routing_ordered_fallback test_routing_production_flows test_qualification_replay_strict`.
  Require independent behavior/funding review. No production activation.

### Task 3: Wire the delivered native metadata, Claude first

**Files:** Task 3 files in the map, `runtime/routing_observations.py:LocalSource`;
`tests/test_claude_usage.py`, `test_codex_account.py`, `test_native_routing.py`,
`test_subscription_billing.py`, `test_subscription_acquisition.py`.

**Interfaces:** retain native `refresh`, `billing_fact`, `binding`, `usable_windows`
and `native_catalog.catalog(settings_path, client)`. Claude account/catalog success
survives quota failure; Codex's `usable_windows` remains `[]`. `LocalSource` chooses
these producers by exact native path, never by a request/env/settings source name.

- [ ] **1. Write failing tests:** `test_native_metadata_without_quota_reaches_fallback`,
  `test_codex_metadata_does_not_start_legacy_quota_reader`,
  `test_failed_quota_does_not_repeat_fresh_metadata_exchange`, and
  `test_changed_native_binding_refuses_prepared_send`.
  Reuse existing wire responses and native-tool doubles. Assert one exact permit,
  no included reservation, no second process for fresh metadata, and unknown
  served effort/cost remaining unknown. Missing identity/catalog sends nothing.
- [ ] **2. Run affected unittest modules; observe intended wiring failures.**
- [ ] **3. Connect only the existing metadata producers.** Preserve bounded
  locking/backoff and own-account provenance. Make required metadata acquisition
  reusable independently of optional quota. Native catalog/bootstrap/readiness
  consume the producer's real binding. Do not infer an atomic cross-process
  identity fence from cached Codex auth; report the supported visibility limit.
- [ ] **4. Verify, validate, commit and run**
  `python3 -B scripts/test_sharded.py test_claude_usage test_codex_account test_native_routing test_subscription_billing test_subscription_acquisition`.
  Obtain native-source/consumer review; synthetic proof is not live qualification.

### Task 4: Carry the same contract to OpenCode without inventing identity

**Files:** Task 4 files in the map; existing `tests/test_opencode_launcher.py`,
`test_opencode_subscription.py`, `test_opencode_subscription_wiring.py`,
`test_routing_production_flows.py`. Reserve a new projection file only after an
actual supported selected-connection source establishes its shape.

**Interfaces:** preserve `LocalSource.billing(route, now)` and its current exact
fact shape, selected-connection catalog and `routing_launch.admitted_check`.
Missing quota is optional; missing selected-account identity remains a refusal.

- [ ] **1. Pin negative tests for all three OpenCode families.** Same provider,
  same pool label, native login and matching environment must not satisfy
  OpenCode identity. Changed connection refuses an unsent admission.
  Go's unknown reset/demand semantics never manufacture included quota.
- [ ] **2. Use the accepted source notes once; do not repeat upstream scans.**
  The existing Claude/ChatGPT notes have no supported selected-account source;
  leave those positive bindings blocked unless new authorized local evidence
  supplies one. Apply the same check to Go's account/workspace provenance.
  Report the exact missing field/source; no speculative parser or new API proposal.
- [ ] **3. For an evidenced source only, write its failing positive projection/
  launch test, implement the minimal connection-bound fact, then verify fallback
  through the existing launcher.** A double proves consumer behavior, not that the
  real producer exists. Unsupported families stay explicitly incomplete.
- [ ] **4. Validate, commit any actual changes and run**
  `python3 -B scripts/test_sharded.py test_opencode_launcher test_opencode_subscription test_opencode_subscription_wiring test_routing_production_flows`.
  Review each positive source before integration. Blocked OpenCode work does not
  hold native delivery, but it does block an all-five-path release claim.

### Task 5: Prove integration and separate readiness from live authority

**Files:** `runtime/native_routing.py:activation_readiness`,
`runtime/routing_launch.py:readiness`, `tests/qualification/replay_strict.py`,
matching existing tests; `docs/smart-routing-qualification.md`,
`docs/smart-routing-validation.md`, `clients/delegation.md`, `clients/opencode.md`,
`clients/claude-code.md`, and `policies/03-agent-delegation.md` where they currently
present quota measurement as a universal launch prerequisite.

**Interfaces:** keep existing status, activation and replay entry points.
Report included readiness separately from account/launch readiness; status is
pure and a fallback-ready result grants neither activation nor a live-send budget.

- [ ] **1. Write failing real-service regression tests** for all five path names,
  both admission bases, strict historical/new replay, API-off/gateway-off,
  uncertain duplicate sends, safe refusal continuation and account changes.
  Assert unsupported sources refuse rather than marking all five ready.
- [ ] **2. Update only the affected readiness/replay consumers and guidance.**
  Preserve consumed-boundary and exposure checks in every native/managed launch.
  Missing telemetry remains unknown in returned results and reconciliation;
  no new learning/reporting subsystem. Preserve explicit OFF and rollback.
- [ ] **3. Amend the registered validation/runbook before any governed run.**
  Only included optimization needs quota/demand seeds. Preserve the old
  quota-denominated canary limits/evidence; do not claim unknown-quota launches
  meet them. Describe a separate, still-unapproved finite task/send/time packet
  with provider-managed charge-risk acknowledgment and refusal/uncertainty stops.
  Keep isolation, lost-ack drill, strict replay, platform/release, activation and
  user cutover/default-ON decisions. Do not execute the packet.
- [ ] **4. Run affected tests, self-review spec coverage, validate and commit.**
  For policy/client instruction edits, use the skill-creator authoring standard
  in `docs/development.md` and check representative included, fallback, identity-
  blocked and uncertain-send tasks; package checks alone are not instruction proof.
  Then run `python3 -B scripts/test_sharded.py` once on the integrated candidate.
  Require independent behavior/accounting/security review of that exact tree.
  Record current Python 3.11 and available Linux proof separately; missing platform
  proof remains pending, never a passing skip. No competing full suites.
- [ ] **5. Publish reviewed source through the normal draft/ready PR lifecycle.**
  Stop at implementation proof and the explicit missing-source/live/platform
  matrix. Installation, qualification, activation and release need their own
  existing prerequisites and authority; this plan supplies none of them.

## Plan acceptance and stop

- [x] Self-review this plan against every requirement in the approved amendment.
- [x] Obtain the requested bounded Opus review for correctness and simplicity.
- [ ] Resolve substantiated findings, validate links/package and publish the plan.
- [ ] After Opus clears the corrected plan, proceed under the user's conditional
  implementation authority. Preserve the previously chosen execution method;
  do not ask for another confirmation or start new live operations.

Do not add new research or reconstruct delivered provider code to make every task
look executable. A missing connection identity blocks only that path. The overall
five-path product remains unfinished until all required evidence exists.
