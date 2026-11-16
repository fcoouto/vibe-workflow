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

**Approved continuation:** implement the
[account-continuity revision](../specs/2026-11-15-smart-router-account-continuity-design.md)
under Tasks 3A–3B below before accepting Task 3. The user approved the independently
passing revision and requested autonomous ownership through the remaining
implementation. Continue dependency-ready work without repeated checkpoint
approval. Use bounded owner/reviewer checkpoints and Opus for material blockers;
preserve escalation history and gates. No installation, account access, upstream
request, live qualification, activation or release grant follows.

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

### Task 3A: Publish generation-bound native metadata without provenance laundering

This and Task 3B complete Task 3 under the approved account-continuity revision.
Keep one integration owner through both outcomes. Serialize source edits and
review each outcome before releasing the next; do not recreate provider streams.

**Accepted synthetic implementation:** `98faa260..1e5d2c6`. The committed
32-module gate passed 1,039 tests with zero skips. Independent state/publication
review and scoped corrupt-state/partial-response re-review passed. Generation-row
growth retains the existing 1 MiB fail-closed limit; cross-file durability,
physical power loss and live clients remain unproved. Task 3B is still required.

**Files:** modify `runtime/claude_usage.py`, `runtime/codex_account.py`;
`runtime/adapters/native_catalog.py` only if its existing exact-schema consumer
needs adjustment. Tests: `tests/test_claude_usage.py`, `test_codex_account.py`,
`test_native_routing.py`; create `tests/test_native_account_continuity.py` for
the focused state-transition matrix rather than exceeding file ceilings.

**Interfaces:** retain `refresh`, `billing_fact`, `binding`, `usable_windows`.
The existing producer state gains `identity={key,generation}` and each complete
account carries `generation`, exactly 32 lowercase hex characters generated by
`secrets.token_hex(16)`. A valid pending state is `{pending: True, generation: g}`;
it yields no billing fact or native binding. Private transition helpers remain
producer implementation details, not a new framework.

The existing native binding's `connection` becomes `account:<key>:<generation>`.
The fact's `producer` carries its existing client/key prefix plus `:<generation>`;
its exact seven-field fact shape is unchanged. Claude
`confirmation(settings_path)` returns `[key, generation, usage_at]`.
No catalog keys, provider account settings or new store are added.

Before a clear/pending write, validate the OLD state read inside the existing
state lock: matching identity plus a complete/pending account carrying the same
generation. Unproved legacy/None/corrupt state invalidates identity first;
it cannot be converted into trusted pending state with the retained old value.
Then successful acquisition either preserves proven same-key continuity or
creates a fresh generation. Explicit account refusal invalidates it.

Publish new identity, matching catalog, complete account and confirmed quota
in the approved order. A fresh generation cannot expose an eligible fact or
binding until its required catalog succeeds. Transient failure preserves only
previously proved continuity. No state deletion or timestamp workaround.

Claude included rows must also be generation-bound. Reuse the existing row
`account` binding as an opaque digest of key+generation, and its existing bound
id mechanism; legacy rows remain stored but cannot be claimed as the new
generation. `usable_windows` checks the full token, not a timestamp/key prefix.
This avoids a new row field/store and closes same-second ABA snapshot reuse.
Codex `usable_windows` remains `[]`.

- [x] **1. Add focused failing tests** for same-second A/B/ABA, same-account
  refresh/restart, explicit refusal, transient pending state, legacy None and
  old-writer B-success/clear/new-A, lost/corrupt/re-created store, and failure
  after each publication step. Assert generation equality only for proven
  continuity; ABA/new provenance differs; incomplete states yield no fact/binding.
  Include ABA with old Claude rows at the same timestamp: no included reuse.
- [x] **2. Run only the new tests first**, observing intended failures.
  Use real disposable producer stores and existing synthetic native transports;
  no actual native executable/account is reachable.
- [x] **3. Implement the existing producer transitions/readers** under their
  current locks, retained parser/backoff/currentness rules and catalog schema.
  Align directly affected old synthetic fixtures; do not change parser outputs
  or grant identity to missing sources merely to make fixtures green.
- [x] **4. Verify** with
  `python3 -B -m unittest tests.test_native_account_continuity tests.test_claude_usage tests.test_codex_account tests.test_native_routing`.
  Parent validates, commits and runs the corresponding canonical sharded modules
  plus `test_subscription_acquisition`, `test_subscription_billing` and
  `test_routing_observations` on committed source.
- [x] **5. Obtain scoped independent state/publication review**, accepting only
  actual ABA/provenance/crash-boundary evidence. Keep live/physical-power-loss
  limits explicit. No Task 3 acceptance or activation yet.

### Task 3B: Bind every subscription permit and continuation to its admitted identity

**Accepted synthetic implementation:** `cf566bb..645b876`. The committed
43-module gate passed 1,262 tests with zero skips. Independent consumer/security
review and two scoped fix reviews cleared the raised findings. New unverified
subscription candidates reserve nothing; legacy unsent records still refuse at
dispatch. Continuation root-loss tests model the ledger-read result, not actual
pruning. Some delegation tests followed implementation; mutation checks establish
regression discrimination, not historical test-first order. Real clients,
physical crash/durability, full-suite/platform and all-five readiness remain
unproved; rollback remains unsupported by default.

**Files:** `runtime/subscription_billing.py`, `routing_observations.py`,
`routing_funding.py`, `subscription_fallback.py`, `delegation_routing.py`;
line-neutral forwarding/condition edits in `runtime/routing.py`;
`scripts/routing.py` only if signature forwarding needs it. No ledger schema
rewrite, generic writer or raised ceiling.

Tests: create `tests/test_routing_account_continuity.py` and
`tests/test_delegation_account_continuity.py`; align actual affected existing
`test_subscription_billing`, `test_subscription_fallback`, `test_routing_dispatch`,
`test_routing_launch`, `test_routing_admission`, `test_routing_funding`,
`test_delegation_routing`, `test_delegation_routing_consumed`, `test_native_routing`,
`test_routing_production_flows`, `test_routing_observations`,
`test_qualification_replay_strict` and their directly affected fixture helpers.
Reserve any additional failing caller before editing it. Keep the 995-line
observations test at its ceiling by placing new scenarios in the focused module.

**Interfaces:** add `subscription_billing.fact(source, route, now) -> dict | None`,
retaining `verified(...) -> bool` as its compatibility wrapper, and
`subscription_billing.marker(fact, route, now) -> str | None`.
Marker is `routing_gates.digest(['subscription-account', 1, fact['producer'],
fact['path'], fact['account'], fact['pool']])` after existing fact validation.
No timestamp/expiry/overflow value enters the digest.

Add `subscription_billing.dispatch_problem(record, settings_path, now) -> str | None`.
It obtains only a current local fact from the code-selected source and compares
the validated marker with the admission's `binding.credential`. Missing marker:
`account_binding_missing`; invalid/missing current fact/store: `billing_unverified`;
different marker: `account_changed`. Funded records keep existing credential checks.
Only trusted collection supplies the in-memory route `account_marker`, retained
through `ROUTE_FIELDS` and stored by `routing_funding.record` for subscriptions.
The public admission projection stays unchanged.

The shared dispatch gate invokes subscription identity validation BEFORE any
fallback quota-fence early return. Preserve `_fresh_gate` funded credential
comparison only for funded records. Forward the actual in-transaction time
through the focused helper instead of acquiring metadata inside the transaction.
Keep route/authority/workflow/accounting checks and duplicate ordering intact.

Collection must compare the retained fact and generation-bound reading
confirmation after reading, and reject a mismatch rather than silently acquiring
a different identity for the same attempt. Recheck required identity even when
Codex has no windows; staling an empty window list is not a refusal.

Delegation resolves the existing root assignment/admission and original route
before preparing a continuation. Require valid subscription markers, same root/
child marker, and the same trusted root relationship at consumption. Remove
the original-selection fallback for absent/pruned/legacy roots. A new child
admitted on a mismatch closes only as provably unsent; issued/uncertain exposure
is never released by this identity check. Preserve prior-turn close refusal.

- [x] **1. Write failing real-service tests** for native helper (including
  `bound=False`), managed immediate prepare, split Workstreams consume and
  continuation under same-second A/B/ABA, missing/expired facts and root loss.
  Assert no permit/tool call, no substituted route, proper unsent closure or
  retained uncertain exposure, and an independent new attempt can qualify.
- [x] **2. Pin legacy and compatibility negatives:** absent binding; null marker
  admitted-unsent; already-dispatched/uncertain/terminal records; marked roots
  versus missing/pruned/legacy roots; unchanged funded marker behavior.
  Historical strict replay must not consult new live identity or rewrite records.
- [x] **3. Observe focused RED, implement the shared marker and guards**, then
  align fixtures through labelled synthetic facts. Never add a missing-marker
  bypass, test-only production branch, canned successful gate or fake native
  identity. No account/ledger backfill.
- [x] **4. Verify focused continuity/legacy modules and affected callers**;
  validate, commit and run canonical sharded coverage including the earlier
  31-module gate plus new tests, binding/storage/replay and continuation callers.
  Leave checkout unchanged during tests. Diagnose failures before another edit.
- [x] **5. Obtain independent security/consumer/legacy review**, including
  actual old-revision dispatcher/new-marker source behavior and isolated
  account-free compatibility tests where safe. Use disposable copies and
  historical Git objects; never install or activate old code on real accounts.
  No downgrade is supported unless all affected old paths are proved blocked.
- [x] **6. Accept Task 3 only after source and this shared invariant pass.**
  Then continue Tasks 4/5 and original remaining delivery obligations. Preserve
  unsupported paths and pending live/platform proof; do not manufacture readiness.

### Task 4: Carry the same contract to OpenCode without inventing identity

**Negative scope accepted:** `71f3bac`, eight committed modules, 177 tests,
zero skips, independent review approved. All three real OpenCode families refuse
inferred identity; synthetic connection-change consumer proof covers Claude
through the shared gate.

**Positive sources delivered (2026-11-16, user-authorized real-account
exploration):** the earlier "no supported source" finding is superseded. On
OpenCode 2.2.6 the selected connections are readable through one bounded
`opencode api --standalone GET /api/credential` (parsed in memory; only
domain-separated digests, a per-family generation and fixed reasons persist, in a
private 0600 store). `runtime/opencode_account.py` derives identity for Claude
(native Claude login plus an identity-only native read), ChatGPT (JWT `sub` plus
credential id; unsigned-trust, not merged with native Codex) and Go (Console
account/org, Go credential id and key digest; attribution proved by the
`x-opencode-org-id` header of exactly one `opencode-go` entry in `GET
/api/provider` equal to the Console org; key-to-workspace is user-attested). Every
OpenCode fact has quota unknown, so these routes are provider-managed fallback
only, never an included claim. Commits `3ee5117c`, `7b041df4`, `c9d9e2e3`,
`95d1d4e3`, `cd8b0126`, `8ef00a06`, `6de6a74a`, `4ceb0c33`; two independent
reviews found no secret leak, permit bypass, wrong model/variant or hidden charge.
Real launcher runs found and fixed: a literal `none` variant being dropped (our
shim refused `route_mismatch`), failed host turns reported as success
(`host_turn_failed`, settlement unchanged), and a created-session model readback
check. Variant-less models are refused before sending on 2.2.6 (documented
unqualified).

**Files:** Task 4 files in the map; existing `tests/test_opencode_launcher.py`,
`test_opencode_subscription.py`, `test_opencode_subscription_wiring.py`,
`test_routing_production_flows.py`. Reserve a new projection file only after an
actual supported selected-connection source establishes its shape.

**Interfaces:** preserve `LocalSource.billing(route, now)` and its current exact
fact shape, selected-connection catalog and `routing_launch.admitted_check`.
Missing quota is optional; missing selected-account identity remains a refusal.

- [x] **1. Pin negative tests for all three OpenCode families.** Same provider,
  same pool label, native login and matching environment must not satisfy
  OpenCode identity. Changed connection refuses an unsent admission.
  Go's unknown reset/demand semantics never manufacture included quota.
- [x] **2. Use the accepted source notes once; do not repeat upstream scans.**
  The existing Claude/ChatGPT notes have no supported selected-account source;
  leave those positive bindings blocked unless new authorized local evidence
  supplies one. Apply the same check to Go's account/workspace provenance.
  Report the exact missing field/source; no speculative parser or new API proposal.
- [x] **3. (done 2026-11-16; see delivered sources above.) For an evidenced source only, write its failing positive projection/
  launch test, implement the minimal connection-bound fact, then verify fallback
  through the existing launcher.** A double proves consumer behavior, not that the
  real producer exists. Unsupported families stay explicitly incomplete.
- [x] **4. Validate, commit any actual changes and run**
  `python3 -B scripts/test_sharded.py test_opencode_launcher test_opencode_subscription test_opencode_subscription_wiring test_routing_production_flows`.
  Review each positive source before integration. Blocked OpenCode work does not
  hold native delivery, but it does block an all-five-path release claim.

### Task 5: Prove integration and separate readiness from live authority

**Implementation checkpoint:** existing readiness and historical replay behavior
needed no runtime change. New real-source regressions and current guidance
separate account/catalog readiness, included optimization and live authority.
The new tests passed initially and are regression proof, not earlier TDD.
Validation Edit 15 retains historical limits and proposes, but does not authorize
or execute, a separate finite fallback packet. Final gates on `4ceb0c33`: full
suite 5,073 tests (4 skipped) OK; Python 3.11 29 modules/740 tests OK; partial
offline Linux 173 tests plus 18 pilot-guard tests, with two errors that are only the
missing `git` binary in the cached image. A live run with the real launcher on this
user's three OpenCode accounts completed one tiny request each as provider-managed
fallback (reserved `{}`, one permit, hold released, no open holds) and refused a
no-connection control with nothing sent.

**Files:** `runtime/native_routing.py:activation_readiness`,
`runtime/routing_launch.py:readiness`, `tests/qualification/replay_strict.py`,
matching existing tests; `docs/smart-routing-qualification.md`,
`docs/smart-routing-validation.md`, `clients/delegation.md`, `clients/opencode.md`,
`clients/claude-code.md`, and `policies/03-agent-delegation.md` where they currently
present quota measurement as a universal launch prerequisite.

**Interfaces:** keep existing status, activation and replay entry points.
Report included readiness separately from account/launch readiness; status is
pure and a fallback-ready result grants neither activation nor a live-send budget.

- [x] **1. Write failing real-service regression tests** for all five path names,
  both admission bases, strict historical/new replay, API-off/gateway-off,
  uncertain duplicate sends, safe refusal continuation and account changes.
  Assert unsupported sources refuse rather than marking all five ready.
- [x] **2. Update only the affected readiness/replay consumers and guidance.**
  Preserve consumed-boundary and exposure checks in every native/managed launch.
  Missing telemetry remains unknown in returned results and reconciliation;
  no new learning/reporting subsystem. Preserve explicit OFF and rollback.
- [x] **3. Amend the registered validation/runbook before any governed run.**
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
