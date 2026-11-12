# D9b: Task-status observability preparation

28 October 2026 · Bounded implementation slice under [D9](onboard-my-project-plan.md#d9-qualification-usage-and-pilot). Read when implementing or reviewing this preparation path. This is local supplied-data preparation, not a collector, export payload or native measurement qualification.

## Current assessment — 12 November 2026

**Plan proposal, awaiting user review.** Assessed clean source `7356f68f0a776ab901a7c57fab7b7d9514dc8b40` in the isolated OpenCode worktree, then created `docs/d9-observability-completion-plan` for this document update. Scope: assess existing implementation and propose completion tasks in this file. No implementation, workers, credentials, accounts, installed-client operations or runtime settings changes were authorized or performed by this assessment. Historical observations below retain their original source bindings; source inspection does not establish installed behavior.

### Implemented versus qualified

| Area | Current implementation and evidence | Remaining boundary |
| --- | --- | --- |
| Pure preparation | `runtime/observability.py:prepare_task_status` implements default-off, bounded allowlisted projection, current/retired-state separation, canonical identity, historical markers, fixed errors, independent output and explicit unknown measurements. All 14 `tests/test_observability.py` cases passed. | Supplied data is not authenticated or freshly acquired. Legacy/Claude and historical variants include synthetic supplied shapes, not native qualification. Keep this helper pure. |
| Optional task-status caller | `scripts/workflow.py:control_main` registers `--prepare-observability` only on `task status`, obtains the ordinary report once and isolates optional preparation errors. All eight caller tests passed, including CLI/state preservation. | This local flag grants neither Project telemetry consent nor export permission. No automatic collector is wired here. |
| Connection settings and recovery | `runtime/observability_settings.py` and `scripts/observability.py` implement checkout configuration, desired opt-in, revision/ownership guards and synthetic probing. All 41 settings tests passed. Status/disable use `runtime/local_ownership.py` and remain usable with stale qualification or missing/replaced/corrupt runtime state. | New external effects still require qualified canonical Codex authority. Canonical Project-wide consent, cross-Project destination discovery and collection eligibility are not implemented. |
| Transport and local activation | `runtime/langfuse.py` verifies the exact project, posts one fixed content-free span and checks bounded readback. All 19 protocol and eight activation tests passed, including local HTTPS/CLI/hidden-terminal execution with synthetic keys and a fake native store. | These prove software behavior, not a live destination, installed skill, real OS entry or native task. Historical one-record Cloud proof does not qualify current source. Actual quota/unit consumption remains unmeasured. |
| Credentials | `runtime/observability_credentials.py`, `requirements-observability.txt` and management references implement explicit environment/OS selection, pinned `keyring==25.7.0`, hidden entry, no fallback and guarded store/forget. Thirteen credential tests passed. | `PinnedWindowsCredentialsTests` was skipped as a class: `Optional pinned keyring is not installed`, confirmed by a separate verbose class invocation. Real OS access remains unobserved. A Windows backend does not establish Windows runtime support. |
| Management entry point | `skills/manage-observability/SKILL.md` and references define separate opt-in management, destination sharing, credential impact and metrics-only privacy/retention. | File presence is not installed discovery/execution. Sharing rules require guided confirmation; no global mapping enforcement exists. |
| Accounting reuse | `runtime/opencode_accounting.py:usage_from_messages` already handles bounded attempt/descendant usage, uncertainty and estimate labeling. The [routing capability matrix](smart-routing-capability-matrix.md#live-re-qualification-9-november-2026) records narrow OpenCode observations. | This owner is not connected to D9 and does not cover a complete D9 coordinator/worker/reviewer task. Routing permits, quota windows and dispatch budgets are not task-consumption receipts. No D9 collection/reconciliation/export path was found. |
| D9b acceptance / D11 | Native source qualification, source-side task/scope filtering, idempotent reconciliation and metrics-only task exports remain pending. [D11](d11-workflow-diagnostics-plan.md) remains a separate final deliverable. | Preparation and connection success satisfy none of these gates. D7b proof still gates D9b acceptance, not ordinary development or independent gate delivery. |

**Observed check:** `python3 -B scripts/test_sharded.py --workers 2 'test_observability*' test_langfuse` reported six modules, 103 tests, one class-level skip, no failures/errors, `OK`, in 161.1 seconds on Python 3.15.2/macOS. The modules ran 103 test methods; the skipped class's methods were not exercised. Logs: `/private/var/folders/tz/c2kgm3r911ndfsbh8zm53lvc0000gn/T/test-units-sr63o88l`. Tests used disposable homes and fixtures; no optional dependency was installed. This focused run replaces neither historical incomplete full regression nor final integrated-suite proof. Package validation and document checks are reported with delivery, separately from runtime assessment.

### Completion approach and limits

Extend existing owners in small reviewed slices. Keep preparation, consent, source acquisition, reconciliation and export separate. Reuse canonical membership, D5/D6 outcomes/identities, accounting and retention; add only missing D9 glue. Turning the pure preparer into a collector violates its contract. A new analytics service, ledger or fallback report would duplicate existing owners and is excluded.

Tasks below are **proposals**, not approved implementation or passing evidence. Source-dependent signatures, exact adapter edits and the integration hook must be frozen in O1 before dependent dispatch. Do not invent production APIs from fixtures. Missing sources block their own integration/qualification, not independent preparation work.

### Selected direction — hosted Langfuse with portability improvements

**User decision · 12 November 2026:** keep Langfuse and improve portability and setup. Do not require users to run a local server. This selects the proposal's direction; it does not start implementation, authorize account access or qualify additional clients/platforms.

- Use Langfuse Cloud for the default guided path. Preserve an explicitly selected existing remote Langfuse destination; do not migrate accounts or replace saved destinations automatically. No local backend, Docker/database stack, local OpenTelemetry collector, daemon or proxy is a user prerequisite. Disposable local HTTPS test fixtures remain maintainer checks only.
- Reuse the existing OTLP/HTTP trace transport boundary. Keep source acquisition and pure reconciliation separate from Langfuse-specific authentication, field mapping and readback. Add only the missing tested seams, not a second transport, mandatory SDK, generic provider registry or alternate-provider implementation. Numeric-only trace attributes satisfy a privacy contract; they are not a promise of OTLP Metrics or interchangeable dashboards.
- Define supported client/OS/credential combinations and ordinary setup steps in O1. One initially qualified surface is acceptable, with other combinations explicitly unsupported or unqualified. Windows credential storage alone does not qualify Windows runtime support. Changing the backend cannot replace missing native source receipts.
- Make O2/O6 a guided, Project-scoped setup with private credential entry, reuse across authorized repositories/worktrees, actionable failures and safe disable/recovery. Users should not construct internal JSON requests or repeat maintainer source/native qualification campaigns. Cloud account/project access and keys remain prerequisites; setup must disclose them rather than imply zero setup.
- Resolve the review's correction-convergence and conflicting-worktree migration cases in O1/O2/O5 before accepting those implementations. Retain D7b and final native reconciliation gates; neither is a new ordinary-user setup requirement.

## Proposed completion tasks

Execute later through [Workstreams](../skills/workstreams/SKILL.md), using ordinary native child delivery and its delegation policy. At implementation startup, recheck the saved choice with `python3 -I -B scripts/workflow.py features status --cwd .`; this proposal changes no preference. Advanced resource/recovery controls and managed task state are not prerequisites unless separately selected. Preserve coordinator model, reasoning, authentication, subscriptions and user customizations.

### O0 — Close current-source verification gaps

**Owner:** validation implementer; coordinator accepts evidence. **Depends on:** approved execution scope, not live integration access. **Files:** existing six observability/protocol test modules, `tests/test_persisted_binding.py` and affected canonical/runtime callers. Change production code only for a reproduced in-scope regression.

- [x] Run the pinned Windows backend class in an explicitly approved disposable interpreter containing the existing optional requirements. Record software results separately from real Windows/OS qualification; do not install globally or silently change Python. **Observed:** included in the zero-skip O0 run below; native Windows calls remained replaced by test doubles.
- [x] Freeze the candidate; run `python3 -B scripts/test_sharded.py --workers 2 'test_observability*' test_langfuse test_persisted_binding test_runtime`, `python3 -B scripts/validate.py` and `git diff --check`. Reuse assessment evidence only while its source, fixtures and conditions remain applicable. **Observed:** 162 tests passed on frozen `e573251`; see the preparation record for interpreter and limits.
- [ ] At O7, run the previously required broad regression on the final integrated candidate with `python3 -B scripts/test_sharded.py`. Inspect estimates first, agree a numeric time/resource bound and keep the checkout unchanged. Preserve historical failed/interrupted evidence and report incomplete runs rather than restarting indefinitely.

**Exit:** candidate-bound counts, skips, warnings and failures are recorded. Optional backend and final full-suite gaps close independently; neither becomes native proof.

### O1 — Freeze interfaces and qualification scope

**Owner:** coordinator with source/evidence owners. **Depends on:** user approval of this proposal. **Writable scope:** this plan and existing owner contracts only where an interface decision requires an update.

- [x] Recheck D5 acceptance, D6 task/worker/attempt identity, canonical membership and [D7b enforcement proof](d7-repository-gate-plan.md#3-qualify-enforcement-then-propose-live-rules). Link accepted evidence or mark it missing. D7b gates acceptance, not independent D9 software preparation. **Observed:** historical D5/D6 evidence is narrower than current D9 coverage; D7b remains unqualified, as recorded below.
- [ ] Select one initial native client/surface/version and authoritative usage source. Record provenance, request/attempt identity, observation times, cumulative/incremental semantics, parent/child inclusion, cache/reasoning inclusion, model/settings coverage and charge basis. Inspect existing adapters and `opencode_accounting.usage_from_messages` before adding parsing; record unsupported sources.
- [x] Publish a client × OS × credential-source support matrix for the selected slice, separating implemented, qualified and unavailable paths. Include Python/dependency requirements, source coverage and ordinary user steps. Verify the guided path needs no local service or maintainer qualification campaign; do not infer cross-client support from OTLP compatibility. **Prepared:** matrix and guided-path requirements below; installed execution remains unqualified.
- [ ] Freeze producer/consumer interfaces for Project consent/selected repositories, owner-resolved task eligibility, immutable numerical receipts, expected participant/attempt set, acceptance outcome/cutoff, export-safe correlation and disable/forwarding ownership. Name exact files, signatures, fields and tests before releasing dependent workers. Disabled file-only projects must not acquire a runtime requirement.
- [ ] Freeze stable export identity and correction ordering using the selected backend's supported update/readback semantics. Define replay behavior across process restarts and late corrections without a second ledger. Require corrected totals to converge rather than append another counted total or accept an older replacement.
- [x] Refresh official version-matched native/Langfuse documentation for content-free ingestion, readback and unit/cost semantics. Obtain separate authorization for later installation, credentials, live records, native runs or service expense, with numeric run/time limits. Do not choose an SDK or auto-instrumentation by default. **Observed:** offline installed-version schema and public documentation refreshed; no live operations are authorized by this preparation. Future campaigns still require their own scoped authorization and numeric limits.

**Exit:** reviewed interface/file-ownership table in this plan, supported scope and explicit source gaps. Stop dependent work if authenticity or attribution would require prompt inspection, rerouting or another registry.

### O2 — Add Project consent and destination selection

**Owner:** settings/membership implementer; independent authorization reviewer. **Depends on:** O1 consent/membership interfaces.

**Files:** extend `runtime/observability_settings.py`, `runtime/local_ownership.py`, `scripts/observability.py` and existing tests where necessary. Coordinate canonical-owner edits in `runtime/startup.py`/onboarding; update management references alongside supported behavior. Preserve version-1/environment and version-2/explicit-source records; checkout consent must not silently become Project collection consent.

- [ ] Write failing tests for explicit Project enablement, selected/excluded repositories, same-Project worktree reuse, two distinct Projects sharing only after matching confirmation, unknown mappings and declined sharing. Do not inventory secret stores or scan for Projects.
- [ ] Add a migration table for existing version-1/version-2 worktrees with conflicting destinations, credential sources and opt-in states. Require explicit conflict resolution; declined promotion and rollback preserve prior choices and off-states. Never choose an arbitrary checkout's destination, copy secrets or promote checkout consent silently.
- [ ] Implement through existing canonical/private settings owners with expected revisions, atomic guards and source-side eligibility. Preserve default-off and credential-free/network-free status/disable during qualification or database failure. Recovery must not repin consent.
- [ ] Prove disabled/never-configured Projects perform no optional acquisition/export/reporting; scope changes and concurrent disable prevent subsequent sends. Preserve disable-first destination/credential changes and shared-credential-impact authorization.

**Exit:** focused settings/activation/canonical-owner tests and independent authorization review pass. Status distinguishes connection configuration, desired consent, qualified sources and actual collection. No live account operation is needed for this software slice.

### O3 — Acquire qualified native observations on demand

**Owner:** native source implementer; independent source reviewer. **Depends on:** O1 source contract; real collection also requires O2 consent and separate live authorization.

**Files:** reuse `runtime/adapters/` and existing `runtime/opencode_accounting.py`/launcher evidence where applicable. O1 names the selected source implementation and test module. Add no reader to `runtime/observability.py`.

- [ ] Write failing source tests for version-pinned receipts, missing coordinator/children, pagination/truncation, failed/interrupted/retried/resumed attempts, stale/cached snapshots, served-model/settings substitution and conflicting provenance. Missing fields stay unknown; host estimates are not billed cost.
- [ ] Acquire only existing authorized numerical receipts and lifecycle identities at the agreed task boundary. Cover coordinator, workers, reviews, verification and integration through acceptance. Preserve observation times. Do not refresh task status for analytics, inspect conversation contents, poll continuously or make model calls for telemetry.
- [ ] Independently compare a separately authorized bounded native run with the original source. Record client/version, workflow revision, source IDs and omissions. Reuse existing observations only for their actual scope; parser fixtures and quota readings alone do not qualify consumption.

**Exit:** selected-source acquisition and attribution are qualified, or the path remains unavailable. Missing sources never trigger another account, routing change, local-model plugin or substitute collector.

### O4 — Reconcile usage and retain necessary proof

**Owner:** accounting implementer with lifecycle owner; independent numerical reviewer. **Depends on:** O1 receipt/participant interfaces; final qualification consumes O3 observations.

**Files:** reuse `runtime/opencode_accounting.py` where semantics match. If D9-specific logic remains necessary, proposed new `runtime/observability_accounting.py` and `tests/test_observability_accounting.py` own pure reconciliation. Use existing `runtime/evidence.py` retention, not another ledger.

- [ ] Write failing table-driven tests for replay, duplicate delivery, cumulative updates/increments, inclusive parents/disjoint children, cache/reasoning subsets, corrections, shared workers, retries/resumes, late arrivals and expired/missing support. Assert exact known subtotals, explicit gaps and no double counting.
- [ ] Reconcile stable task/worker/attempt/source/run/request identities against expected participants and declared cutoff. Prefer qualified inclusive totals; otherwise sum only complete proven-disjoint components. Preserve corrections/provenance. Never turn partial/sampled input into exact totals or unknown values into zero.
- [ ] Retain compact authoritative numerical receipts through the existing lifecycle owner: seven-day detailed/four-month compact limits, shared 100 MiB Project cap and protected execution/merge/recovery dependencies. Test correction age, expiry and protection there. Hashes alone cannot support reconciliation; do not mirror transcripts or retain report snapshots.

**Exit:** numerical/lifecycle checks pass and independent reconciliation matches qualified receipts. Subscription use, billed charges and backend/model-price estimates remain separate. Missing retention support is an owner dependency, not permission for duplicate cleanup.

### O5 — Integrate safe metrics-only task export

**Owner:** integration implementer; independent privacy/security reviewer. **Depends on:** reviewed O2/O4 and O3 for real-source export; synthetic contract development may proceed after O1.

**Files:** proposed `runtime/observability_export.py` and `tests/test_observability_export.py` separate payload/dispatch from preparation. Reuse guarded settings/transport; O1 assigns the existing completion/caller seam. Extend `runtime/langfuse.py` only for a reviewed non-probe operation; keep `synthetic_probe` fixed.

- [ ] Write failing tests for opaque export IDs, strict field/type/size allowlists, arbitrary local labels, prohibited nested content, unknown measurements and unsafe metadata. Cover disabled/excluded/Q&A scope, changed consent during source lookup, replay and lost acknowledgement before adding transport.
- [ ] Consume already-produced authoritative outcomes and eligible receipts once; never call `task_status` again for analytics. Resolve task/Project/Repository eligibility before acquisition/ingestion, excluding standalone Q&A without content classification. Preserve the independent preparation flag and its output.
- [ ] Export minimal duration/outcome/attempt/usage metadata through supported idempotent ingestion. Preserve charge basis and non-overlapping usage buckets. Do not add included cache/reasoning usage twice or present inferred model prices as billed charges. Subscription charges stay unknown even if the backend infers a price; qualify that distinction on readback.
- [ ] Exercise authentication/quota/outage, delayed acknowledgements, partial rejection, concurrent disable and missing coverage with local HTTPS fixtures. Unknown send outcomes must not cause duplicate ingestion. Preserve ordinary output, local evidence and readiness during optional failures. Add no fallback report or background queue.
- [ ] Prove correction convergence on the selected export identity: export a total of 100, correct it to 120, replay the old observation and repeat delivery after a process restart. Readback must retain the current total of 120, count the logical observation once and preserve source correction provenance. Rejected/stale updates remain explicit; do not fabricate backend ordering guarantees.

**Exit:** captured payloads and independent privacy/security review establish the allowlist and no prohibited content export; focused integration tests pass. Start without sampling. No auto-instrumentation, per-turn wrappers, pricing engine or plugin remote-deletion logic is added.

### O6 — Qualify installed management and live connection

**Owner:** qualification operator; independent observer. **Depends on:** O0's focused checks for the selected controls and separately authorized environment, not O0's final O7 broad run or the optional Windows class for a non-Windows path. Requalify affected paths after O2/O5 changes. Connection-only proof need not wait for full D9b acceptance.

**Scope:** existing clean-package/installed-client procedure and private evidence; publish sanitized results here. Use the user's foreground hidden-entry path, selected destination and explicit record/time budget. Never put secrets in worker prompts/logs.

- [ ] Observe installed skill discovery/selective loading. Prove onboarding only proposes management and routine disabled work loads no provider procedure or optional collection.
- [ ] Observe one guided management request from a fresh supported installation through destination selection, private credential entry, consent and the authorized bounded probe. The user supplies account/project choices and secrets privately, not internal JSON/revisions. Observe reuse in a second authorized worktree, actionable missing-source/credential errors and safe disable/recovery without a local server. Reuse matching authorization; do not turn each internal step into another approval request or repeat maintainer qualification for users.
- [ ] Where OS storage is selected, observe actual pinned-backend store/load/forget for the approved connection with shared-consumer authorization. Preserve environment mode. Do not erase a real shared entry for a test or infer Windows runtime support from macOS/fakes.
- [ ] With synthetic-write authorization, observe configure, enable, one fixed probe/readback, status and disable on the target. Verify no extra POST, secret exposure or status/disable network/store access. Separate acknowledged, verified, pending and unknown-write results.
- [ ] Verify failures without exhausting paid quota or breaking another user's credentials. Reuse local fault cases; missing live authentication/outage/OS-denial observations stay not run. Disclose destination history/access and service limits. Measure actual units through the supported backend view if accessible; otherwise retain the gap.

**Exit:** snapshot/client/destination-bound installed/live evidence, consent disabled after the bounded run unless separately requested, and explicit platform/failure/unit gaps. Synthetic success still does not qualify native collection.

### O7 — Accept D9b on a native-only task

**Owner:** native-task coordinator; independent source reconciler and final reviewer. **Depends on:** O1–O5 accepted, applicable O6 installed/live proof, prior D7b evidence and separately authorized bounded native execution.

- [ ] Run one real workflow task with native coordinator, worker and reviewer, preserving native model/authentication choices. Include failed/retried/resumed work plus review, verification and integration overhead through verified acceptance. Keep required local proof independent of Langfuse.
- [ ] Reconcile actual receipts, expected participants, cutoff and late corrections with Langfuse. Exercise replay, cumulative/incremental updates, overlapping totals, missing children/data, delays, disabled/excluded scope and outage behavior. Label supplementary fault-injected fixture cases separately; unavailable native proof leaves its criterion open.
- [ ] Independently verify two-Project isolation and source-side filters for every claimed path, metrics-only readback, disable/forwarding and truthful unknown costs/coverage. Dashboard filters and synthetic probes cannot establish these properties.
- [ ] Freeze the integrated candidate; complete O0 regression/static checks and independent behavior, numerical and privacy/security review. Record source/version bindings, counts, warnings and skipped/unrun cases here. Accept D9b only for demonstrated scope with complete accounting and accepted prerequisites.

**Exit:** canonical D9b native-only acceptance passes with authoritative reconciliation and no unsupported completeness/cost claims. Benefits remain unmeasured without comparable baseline/candidate evidence. Hand observations and limitations to D9c/D11; do not provision dashboards here.

### Conditional and downstream work

| Item | Proposed action and acceptance boundary |
| --- | --- |
| OpenRouter Broadcast/mixed sources | Only if claimed or separately selected: qualify actual connector IDs/correlation, metrics-only payload, pre-ingestion scope, sampling and independent disable. Add focused O3–O5 correlation/replay tests, then a separate O7 mixed-source case. Missing support leaves the path inactive; never match by timing or reroute native work. |
| Other native clients, local models and Windows | Extend O1/O3/O6 qualification per selected surface/version after runtime-owner support. No universal-client claim or Windows-runtime implementation is hidden in the first slice. Unsupported sources remain coverage gaps. |
| D7b | Existing owner supplies disposable enforcement rejection and legitimate acceptance. Missing proof blocks O7 acceptance, not ordinary work or independent preparation. This proposal does not reopen gate implementation. |
| D9c / D11 | Pilot/dashboard owners retain their plans/dependencies. D11 stays last, uses supported target-version views/filters and preserves user edits. No custom UI, offline output, ledger or dashboard provisioning belongs here. |

### Workstreams delivery and review order

1. Coordinator owns this plan, interfaces, shared caller integration, Git and acceptance. Historical Genesis/chat ownership below is provenance, not an active worker or automatic file release.
2. After O1, O2 settings and O3 source work may overlap with disjoint files/private fixtures. O4 pure reconciliation may overlap once its receipt contract is fixed. Serialize canonical/evidence owners, `scripts/workflow.py`, shared fixtures and management instructions.
3. O5 integrates reviewed contracts. O6 connection-only proof may proceed on a frozen supported package with separate authorization; rerun only invalidated evidence. O7 follows integrated and D7b proof, not worker self-reports.
4. Each implementation task writes its failing test, observes intended failure, makes the smallest change, reruns focused checks and self-reviews. Review each independently testable slice before dependent dispatch, with named independent specialist coverage. Shared candidate checks run without concurrent edits.
5. Freeze writable files, interfaces, result, evidence, numeric budgets and stop conditions in each future assignment. Recheck current policies and worker-selection guidance before dispatch. Mentioning Workstreams does not enable advanced controls or start workers.

**Proposal completion:** the user selected the hosted direction and subsequently authorized O0/O1 preparation only. O2–O5 implementation and live qualification remain unstarted. Checkboxes require observed accepted evidence; document checks, fixtures and native qualification remain separate claims.

**Proposal review and checks:** independent bounded review of `267643a` found no confirmed blocker to O0/O1 preparation and identified correction convergence and conflicting-worktree migration as improvements. Proposal revision `e573251` added those acceptance cases and the user's hosted/no-local-server direction, support matrix and guided-setup checks. It changed no executable code, permissions, credentials, settings or collection behavior. The preparation record below supplies later O0 results and O1 findings; prior proposal review did not establish native-source or backend correction guarantees. Independent implementation/source/security reviews remain future task gates. Full regression and installed/live qualification remain unrun.

## O0/O1 preparation record — 12 November 2026

**Authorized scope:** the user requested O0/O1 preparation and explicitly kept O2–O5 gated on O1 interface/source decisions. O0's current-source checks are complete; its final integrated broad run remains assigned to O7. O1 is **partially prepared, blocked on B1/B2 below**. No O2–O5 production code, native usage capture, account/credential access or live export was performed. Hosted Langfuse and no user-run local service remain selected. This record supersedes earlier pending-preparation wording, not the historical source bindings.

### O0 observed evidence

- Frozen candidate: `e573251cb68adcb6a795db1a9a9eaa4a17c0f4df`, based on `7356f68f0a776ab901a7c57fab7b7d9514dc8b40`. The isolated task worktree remained clean and unchanged during tests. OpenCode coordinator/model settings were preserved. Workstreams status reported effective enabled from project settings, revision 1; no advanced controls were activated.
- Disposable interpreter: Python 3.15.2/macOS, at `<evidence-root>/venv/bin/python`. Existing `requirements-observability.txt` installed with `pip --isolated install --disable-pip-version-check --only-binary=:all: -r requirements-observability.txt`, within a 180-second bound. Versions: `keyring==25.7.0`, `jaraco.classes==3.4.0`, `jaraco.context==6.1.2`, `jaraco.functools==4.6.0`, `more-itertools==11.2.1`, `pip==26.2.2`. Global Python and OS stores were not changed.
- Actual run: `<evidence-root>/venv/bin/python -B scripts/test_sharded.py --workers 2 --log-dir <evidence-root>/focused 'test_observability*' test_langfuse test_persisted_binding test_runtime`. Eight modules, **162 tests passed, zero skipped**, 197.0 seconds, within the 600-second bound; the initial estimate was 229.3 seconds. The credential module ran 21 cases, including the pinned Windows source regression with native calls replaced. No warning/failure/error markers appeared in unit logs. This closes the earlier optional-dependency skip, not real Windows qualification.
- `python3 -B scripts/validate.py` passed without warnings and `git diff --check` passed on that frozen candidate. These test results remain bound to that snapshot; subsequent changes are documentation only and receive separate final static checks. The full integrated suite, installed discovery, real OS access, native consumption and live Langfuse remain unrun.
- Private evidence root: `/private/var/folders/tz/c2kgm3r911ndfsbh8zm53lvc0000gn/T/opencode/d9-o0-o1.7IdnBM`. It contains disposable dependency/schema artifacts, unit logs, documentation snapshots and the preparation ledger, not runtime measurements or another observability store.

### Existing owner and prerequisite decisions

| Boundary | Existing producer / inspected evidence | O1 decision and remaining limit |
| --- | --- | --- |
| Canonical identity and membership | `runtime.startup.acquire_project_entry(event, package, *, qualifier=None, deadline=None, budget=None, managed=True)` and `_entry_scope`; `AcceptedScope` carries Project/Repository/checkout IDs, authority revision, accepted scope and qualification provenance. `runtime.local_ownership.settings_scope(cwd)` supports recovery without healthy runtime qualification. | Reuse these owners and their rechecks, not a caller-supplied identity dictionary, ancestry scan or new registry. Project consent must bind explicit selected canonical repository IDs and remain distinct from checkout connection consent. Preserve local status/disable even when runtime/source qualification breaks. |
| Task acceptance | `runtime.evidence.verify_completion(scope, request)` returns the already-produced `_verification_response`: `verified`, `implementation_verified`, `delivery_complete`, `admission_mode`, `candidate_digest`, `blockers` and task identity. `merge_authority` remains false. | Future export integration belongs after the ordinary `purpose="completion"` operation returns successfully and after its final local writer/reservation settles, before its caller prints/returns. Consume that result once; do not refresh `task_status`, rerun verification or treat `DONE`/qualification completion as ordinary verified acceptance. All verification paths must reach this same post-owner seam. No transport belongs inside an evidence transaction. |
| Participants and attempts | `runtime.delegation.delegation_status(scope, client, session_id, *, compact=False)` projects existing assignment IDs, original owners, role, state and reconciliation; the D5/D6 immutable plan/assignment artifacts own reviewer/worker roles and attempts. | Bind expected participants to those owners plus the coordinator and existing check/integration operation identities. Never infer role from model names, titles or usage counters. This preparation did not invoke those controls against private task state. The complete D9 participant-receipt producer remains B1. |
| Historical D5/D6 qualification | [D5 validation](d5-validation.md) records earlier native lifecycle observations and a narrower 4 November 0.165.0 review-readiness result. [D6 plan](d6-native-delegation-plan.md) records the bounded 5 November Mac pilot and cooperative limits. [Clean package validation](dev-package-validation.md) covers 0.167.0 startup, not usage. The D6 plan's referenced successor pilot packet is absent from this checkout. | Reuse historical evidence only for its actual source/surface. Do not promote it to current ordinary D5 acceptance or complete native usage proof. Current source/role/coverage must still be independently qualified; a missing pilot packet is not passing evidence. |
| D7b | [D7 validation](d7-validation.md) records fixture proof, not disposable production enforcement; `runtime/repository_gate.py:_acquire_qualified_readiness_source` still raises `Repository readiness source has not been independently qualified`. | D7b remains unqualified. It gates O7/D9b acceptance, not these offline checks or basic development. This task changes no repository protection or publisher. |

### Initial surface and native source investigation

Select **Codex CLI 0.167.0 on macOS** as the initial investigation target because current connection controls already require canonical Codex authority. This is not a qualified D9 source or a new platform-support claim. `codex --version` returned `codex-cli 0.167.0`. Offline `codex app-server generate-json-schema --out <evidence-root>/codex-schema` ran with a disposable `CODEX_HOME`, exit 0. A warning reported that the nonexistent disposable home prevented PATH alias creation; no global aliases were changed. No app-server listener, model turn, account query or native history read ran.

Installed-version `ThreadTokenUsageUpdatedNotification.json` requires `threadId`, `turnId`, `tokenUsage`; `tokenUsage` has `total`, `last` and optional `modelContextWindow`. Counter fields are `inputTokens`, `cachedInputTokens`, `cacheWriteInputTokens`, `outputTokens`, `reasoningOutputTokens`, `totalTokens`. `cacheWriteInputTokens` has a schema default but is not required; absence must remain unknown, not a fabricated zero. The current official source describes `total` as cumulative thread usage and `last` as the latest model response, **not an entire multi-request turn**. Do not sum successive totals or add `last` to `total`; do not infer cache/reasoning bucket inclusion or cross-fork/compaction continuity without qualification.

**B1 — native receipt acquisition is unresolved.** The generated 0.167.0 `ThreadReadResponse` has no `tokenUsage` field, so a read-only `thread/read` cannot recover the notification's counters by assumption. Its `Thread.model` description explicitly says configured model is not per-turn execution telemetry. The existing Codex cooperative activity reader skips `token_count`/`token_usage_record`; it does not issue D9 numerical receipts. Notifications alone establish no durable replay, task-start baseline, request/attempt identity, receipt observation time, auxiliary work coverage or complete descendants. Choose a supported existing host-owned numerical receipt/replay source before freezing acquisition. Do not add a listener/daemon, scan conversations, trigger native inference or invent a caller-authenticated source to fill this gap.

`runtime.opencode_accounting.usage_from_messages(...)` remains a reuse reference, not the selected Codex producer. It handles an OpenCode attempt and known descendants; above 32 requests its grouped result omits individual receipts. Never treat that reduced routing projection, quota windows or dispatch budgets as complete cross-client D9 provenance.

### Client / OS / credential-source matrix

All runtime controls require Python 3.11+ and accepted enrollment/ownership for the selected operation. Environment credentials need no keyring dependency. Explicit OS storage needs the existing pinned optional requirements in the chosen interpreter; no global installation or plaintext/default fallback is allowed.

| Client / OS | Connection/credential implementation | D9 qualification and ordinary-user boundary |
| --- | --- | --- |
| Codex CLI 0.167.0 / macOS | Existing qualified-canonical-runtime controls; environment or explicit macOS Keychain adapter. | Selected investigation surface. Software tests pass; source collection, installed management and actual Keychain access remain unqualified. No local service is required. |
| Codex CLI / Linux | POSIX control path and explicit environment injection; no Linux OS-store adapter is supplied. | Not qualified for D9 here. Preserve existing supported environment behavior; do not advertise a Linux keyring path or inherit macOS source proof. |
| Codex / Windows | Windows credential backend has pinned-source fake-native tests. | Runtime uses POSIX facilities and is not qualified/ported for Windows. Credential tests do not enable this path. |
| Claude Code / supported host OS | Ordinary workflow/startup support is separate; observability configure/enable currently hard-code Codex acquisition. | Not selected for D9; a native telemetry facility is not permission or proof of working plugin integration. No automatic client switching. |
| OpenCode / supported host OS | Existing routing/accounting modules, but no canonical OpenCode observability management path. | Not selected for D9. Keep native settings/models intact; no rerouting for visibility or inherited Codex qualification. |

The required guided experience remains one explicit management request: identify the Project/destination, disclose coverage and service limits, reuse matching authorization, enter secrets privately through the chosen source, configure/enable and run the separately authorized bounded probe. Reuse accepted Project settings in a second authorized worktree; expose actionable missing-source errors and credential-free disable/recovery. Internal revisions/JSON and maintainer source qualification are not user chores. This is an O2/O6 acceptance contract, **not implemented guided behavior**.

### Prepared interface and file-ownership table — not released for implementation

These are proposed internal Python contracts using existing plain dictionaries, not new public CLI actions or trusted caller-JSON formats. The coordinator retains this plan and shared caller integration. Do not dispatch edits while B1/B2 remain unresolved; the native producer and export effect signatures cannot be called frozen merely because their consumers are written down.

| Future owner / exact files | Proposed contract and minimum input/output | Release condition |
| --- | --- | --- |
| O2 settings owner: `runtime/observability_settings.py`, `runtime/local_ownership.py`, `scripts/observability.py`, existing settings/activation tests and management references | Preserve `status`, `configure`, `enable`, `disable`, `probe`, `store_credentials`, `forget_credentials`. A separate Project collection decision must carry `expected_revision`, canonical `project_id`, explicit selected `repository_ids`, exact destination/project, credential source and desired collection choice; reports distinguish checkout connection from Project selection, qualified sources and current collection. No checkout record silently becomes Project consent. | Freeze the Project settings record's canonical private location, schema, recovery/disable ownership and conflict table through the existing owner; no new registry or automatic migration. Until then O2 stays gated. |
| O3 source owner: proposed `runtime/adapters/codex_observability.py` and `tests/test_codex_observability.py`, only after the existing host-source seam is identified | Candidate `acquire_task_observations(scope, *, session_id, task_id, expected_revision, cutoff_ns, deadline) -> dict`. It must return source/version/provenance, owner-issued receipt IDs, expected participants/attempts, typed numerical observations, declared cutoff and explicit gaps. Each observation preserves original source identity/order, receipt-received time versus source time, counter basis/inclusion, model/settings provenance and charge basis. No conversation content or account-wide usage substitute. | B1 must resolve the actual host producer and replay/coverage mechanism. This module/signature is provisional and does not authorize creating a collector. |
| O4 accounting/lifecycle owners: proposed `runtime/observability_accounting.py`, `tests/test_observability_accounting.py`; existing `runtime/evidence.py` retains compact receipts | Candidate `reconcile_task(receipts, *, expected_attempts, cutoff_ns) -> dict`: known numerical subtotals, counted identities/source versions, complete/incomplete coverage and gaps, verified outcome reference and original correction provenance. Missing values remain `None`; cumulative snapshots replace supported earlier snapshots, not additive increments; subscription charges remain unknown. | B1 must define source grain, inclusion and ordering. Retention remains existing owner behavior, not another ledger. |
| O5 payload owner: proposed `runtime/observability_export.py`, `tests/test_observability_export.py`; existing `runtime/langfuse.py` transport and tests | Candidate `prepare_task_export(reconciled, *, export_identity) -> dict | None` creates only a strict metrics-only projection. Correlation uses random opaque IDs retained once with existing private source/evidence ownership, stable across restart and bound to destination; do not export raw labels/paths or treat hashing user labels as anonymization. | The exact OTLP mapping and delivery/correction contract remain B2. No transport effect function or automatic retry is released. |
| Coordinator integration: `runtime/evidence.py` final completion return and the corresponding `scripts/workflow.py` evidence-verify branch; relevant evidence/caller tests | One optional post-owner boundary consumes the ordinary completed verification result and its bound participant receipts without another status/verification operation. Disabled paths acquire no optional source/credentials and call no destination. Optional failure preserves ordinary output/exit behavior and never swallows cancellation or `SystemExit`. | O1 contracts reviewed and later O2–O5 authorized; source coverage must include all claimed completion paths. Never couple ingestion to task-status preparation or local DB transactions. |

### B2 — Langfuse v4 correction and replay semantics block export design

Current official [v4 ingestion guidance](https://langfuse.com/integrations/native/opentelemetry/migration-to-v4.md) says to export complete **immutable** observations once. Its [update contract](https://langfuse.com/faq/all/tracing-data-updates.md) explicitly says re-ingesting the same ID is not reliably deduplicated or even eventually consistent; it can inflate metrics. Stable trace/span IDs therefore do **not** by themselves satisfy O5's replay, restart or 100-to-120 correction requirement. The earlier O5 acceptance case remains a required outcome, not a supported same-ID overwrite algorithm.

Freeze these safe decisions now: reconcile complete observations locally through the existing evidence owner before the first authorized POST; after an unknown acknowledgement, do not resend automatically; preserve an uncertain delivery outcome without claiming exact remote totals. Do not re-ingest accepted spans to correct them. These rules prevent an unsafe send strategy, but do not close the full correction/replay requirement.

Langfuse documents mutable **scores** as one possible enrichment/correction mechanism, with identity bound to ID, name and timestamp calendar date. That does not establish that native usage/cost totals or dashboards follow a corrected score. No score exporter, duplicate revision stream, deletion scheme, fallback report or second ledger is selected here. The next O1 design step must demonstrate supported correction/duplicate handling and presentation semantics within the approved hosted-Langfuse scope; if those cannot satisfy the contract, report the precise unsupported claim for a scoped user decision. Do not silently weaken accounting or replace the provider.

### Documentation/source bindings and next gate

Checked 12 November 2026 through Context7, official public pages and the installed CLI's offline generator. Public snapshots reside under the evidence root; they are authoring evidence, not routine runtime prerequisites. Refresh affected conclusions after relevant source/API changes.

| Evidence | SHA-256 |
| --- | --- |
| [Codex app-server documentation](https://developers.openai.com/codex/app-server.md), fetched Markdown | `b9e75507755ba0be2ebc615ca1f4399a54a386125f3935f615405ea93efe77f2` |
| Installed 0.167.0 `v2/ThreadTokenUsageUpdatedNotification.json` | `aba4f6c7e4a19b2b842c08ee793b57000c07dafd57b922ad0d8e7c76609108c2` |
| Installed 0.167.0 `v2/ThreadReadResponse.json` | `4b1a0bb5d6896a5ef1c03045e35ca262b21471e07d4e322d874995b6fd31c094` |
| Langfuse v4 migration Markdown | `0a6385faa3ffd896bf450f424cc04c2ad64c0cbbd4faaf4238166be3c0980e3d` |
| Langfuse tracing-data-updates Markdown | `dfd8cd8bec09cf0b0ab7a0c1f76232ed361e57a834eee785b4d2cbb9619c4ad6` |

**Next gate:** coordinator/source-owner review must resolve B1 native receipt production and B2 hosted correction/replay semantics, then finalize the provisional interfaces and Project private-setting location/recovery contract. O2–O5 remain **gated, unstarted and not authorized by this preparation request**. D7b and O7 remain separate final acceptance dependencies. No remaining preparation checkbox is marked complete merely from a schema, fixture or proposed signature.

## Selective port to current main

Current-main source composition · 2 November 2026 · reviewed historical source `83869bef209ce630facd4aa9046efc4adc8396e5`. The initial selective port used `0da866fa`; the compatibility slice preserves that source while adapting to current canonical authority and task states. Earlier validation records below retain their original source bindings.

The preparer reuses `runtime.task_statuses.STATES`. Ordinary task/status, title synchronization and delivery stay under their existing owners. Optional preparation neither performs an extra status refresh nor enables collection/export.

Configuration, enablement, probes and credential operations reuse `startup.acquire_project_entry` and `_entry_scope`, require a qualified canonical Codex runtime and recheck its actual authority at the existing guarded boundaries. Consent binds the accepted scope and physical authority, plus the qualification provenance omitted from the accepted-identity projection. No legacy bootstrap or alternate activation route is supplied. Unqualified project acquisition blocks these operations before credential/account access. Later status/disable recovery uses local physical/settings ownership without healthy runtime qualification; it does not renew consent or authorize new external effects.

Helper, caller, settings and activation tests use current canonical birth/acquisition fixtures. Synthetic trusted-owner observations, transport and credentials remain software fixtures. The compatibility cycle covers 62 distinct cases through staged passing runs after two diagnosed corrections; it is not a complete rerun on the final generation. Additional local protocol/terminal cases and final main composition are recorded separately. Existing one-record Cloud evidence remains bounded to its original source and record.

This composition changes no collector, canonical scope owner or required local evidence rule. Native lifecycle/usage acquisition, project-wide consent, cross-Project isolation, live failures, actual unit consumption and D11 remain unqualified. Source/helper tests do not qualify installed or live behavior.

## Agreed caller and source

Genesis and this observability chat agreed on `prepare_task_status(report, *, enabled=False)` in `runtime/observability.py`. The input is the existing Python result of `runtime.state.task_status(scope, client, session_id)`, already surfaced by the task/status branch of `scripts/workflow.py:control_main`. Its source shape is `{ok, task, generation, blockers, limitations}`, optionally with `historical` from `runtime.evidence._closed_status`.

Genesis owns future D9 caller integration at that report boundary, after the ordinary requested owner operation has already produced its report and after real opt-in, permission and lifetime integration. No caller hook, configuration or CLI change is included here. `task_status` may revalidate and mutate readiness: the preparer must neither import/call it nor initiate another status operation for analytics. A dashboard read must not become a status refresh.

The pure helper cannot authenticate supplied data or grant export permission. Passing `enabled=True` requests local preparation only; it is not proof of the real integration's opt-in. With the default or explicit `enabled=False`, return `None` before accessing or validating `report`. Reject non-boolean enable values.

### Optional local caller — 29 October 2026

The user separately authorized the following local caller slice, and Genesis released its exact task/status branch, new caller tests and this subsection. This supersedes the earlier no-CLI-change boundary only for this flag. Genesis retains Project configuration, source/permission/lifetime integration, collectors, exports, dashboards and primary integration.

```sh
python3 -I -B /path/to/package/scripts/workflow.py task status --cwd /checkout --client codex --session SESSION --prepare-observability
```

The flag is available only on `task status` and defaults off. Without it, the command preserves its ordinary response and exit behavior and does not import or call the preparer. With it, the caller first obtains the ordinary status report exactly once, then passes that same report to `prepare_task_status(..., enabled=True)`. The ordinary status operation can revalidate readiness; preparation adds no refresh or further state action. This per-invocation local choice does not enable Project telemetry or establish export permission.

Only flagged output adds `observability_preparation`. Success is `{status:"prepared", data:<helper result>}`. Optional import or preparation failure is `{status:"unavailable"}` with no raw exception, input or installation details. Existing owner fields and the ordinary exit result remain intact. Core status errors retain their original error behavior; cancellation and `SystemExit` are not swallowed. Source and helper inputs are not modified, and the flag performs no new configuration, persistence, collection or export.

Implementation and validation stay in `scripts/workflow.py` and the new `tests/test_observability_caller.py`; the helper and core state/evidence source remain unchanged. The bounded plan is: observe a real CLI test reject the missing flag; add the narrow parser/output branch; prove default-off/no helper import, one source call, successful preparation, isolated optional failures, preserved core errors/cancellation and no state-file changes; then run caller/helper/runtime checks, package validation and independent phase review. The earlier incomplete full-regression result below remains explicit; local caller tests do not qualify native execution, usage, export/deletion or D11.

## Supported projection

Enabled input must be a successful task-status report. Validate the supported structured fields below without traversing excluded content. Return independent dictionaries so later output edits cannot mutate the source report.

| Source | Local preparation output | Meaning and exclusions |
| --- | --- | --- |
| `report.generation` | `generation` | Supplied owner generation, not a measurement or fresh observation time. |
| `task`, or `None` | `task`, or `None` with `task_unavailable` gap | Copy only client, session ID, task ID, source state, revision and pins. No `scope_ref`, mode, reasons, conditions, review/check content or arbitrary metadata. Source state `DONE` cannot establish current readiness; retired `READY`/`BLOCKED` require a historical marker. |
| `task.accepted_identity` for canonical pin schema 3 | `task.accepted_identity` with only `project_id`, `repository_id`, `checkout_id` | Validate selected identity fields and client agreement. Do not copy roots, paths, bindings, membership authorization or configuration. Legacy scope yields `None` and `canonical_identity_unavailable`; never derive canonical IDs from a legacy project label or path. |
| Optional `historical` | `historical`, or `None` | Preserve only its existing historical/outcome/current/implementation_verified/delivery_complete/merge_authority/status/compact structure. Require matching task outcome, historical true and all current/authority flags false. Preserve `retained` versus `unavailable`; unavailable adds `historical_support_unavailable`. A compact reference is local `{name,digest}` only; never resolve or copy its body. |
| No supported consumption/settings/coverage source | `usage`, `charges`, `native_model`, `native_settings`, `worker_coverage`, all `None` | No estimates, zero defaults, worker inference, totals, comparisons or OpenRouter/native-source merger. |

Every result includes `preparation_only: true`, `source: "runtime.state.task_status"`, and fixed gaps `source_authenticity_unverified`, `source_freshness_unverified`, `export_eligibility_unverified`, `current_readiness_unverified`. This output is not a Langfuse API schema or a new authoritative observation. Output identifiers and compact references remain private local data until an independently qualified export contract permits a safe representation.

`blockers` and `limitations` are bounded source containers only: neither their text nor their elements enter the projection. Unknown or malformed supported fields raise a fixed, bounded `ValueError` without echoing input. No prompts, completions, source code, review text, credentials, paths or arbitrary metadata are copied from excluded fields, even when they contain nested or non-serializable objects. The helper does not claim universal redaction of user-chosen identifier strings.

## Bounds and source compatibility

Use plain built-in dictionaries, lists, strings, integers and booleans for inspected input. This avoids invoking custom container or scalar methods during preparation. Reuse source text bounds: task ID 128 characters, session ID and legacy pin strings 1024, canonical identity IDs 128, and printable nonempty text. Canonical pin digests remain 64 lowercase hex characters; schemas 1/2 retain their existing three-key pins, schema 3 its four-key pins. Clients and task states follow this existing source's native-client/state enums, not a model allowlist.

Generation is nonnegative and revision positive, within SQLite's signed 64-bit counter range. The supported report has at most six fields; pins at most four; the bounded task/accepted-identity projections inspect parents with at most 32/16 fields; historical has its exact eight-field shape. Blockers/limitations have at most 64 entries, matching the source condition bound and covering its fixed diagnostic list without reading any elements. These are preparation input limits, not a new persistence or retention policy. Unsupported future source shapes require explicit compatibility review.

## Ownership, execution and verification

Historical preparation ownership: this chat owned additive `runtime/observability.py`, `tests/test_observability.py` and this contract. The referenced 28 October implementation plan is absent from the assessed checkout; use the completion tasks above rather than a missing document. Genesis retained core evidence/state, delegation, changes/onboarding/startup, adapters, `scripts/workflow.py`, caller integration and shared primary planning documents. Future assignments must re-establish ownership. The helper performs no filesystem/native-history reads, persistence, environment changes, network/API calls, exports, configuration, dashboard provisioning or readiness mutation. It imports no core state/evidence module and adds no dependency.

Implement test-first against genuine disposable canonical and empty task-status API responses, plus explicitly supplied legacy/Claude shapes where native authority is unavailable. Check valid projection, canonical client separation, source-state/authority distinctions, disabled sentinel input, historical marker shape, missing/malformed fields, bounds, excluded content, independent output and input nonmutation. An audited subprocess must demonstrate no new filesystem, network/process or core-state action. Historical marker fixtures are explicitly synthetic; they do not qualify closure or retention.

Independent review must cover the exact source caller and real response compatibility, error privacy and the absence of authority escalation or hidden collection. Run focused tests, package validation and the existing test suite on the final source snapshot; report existing failures/warnings rather than masking them. The completion tasks above own future bounded test/verification steps.

## Remaining qualification

Reuse canonical D9 enable/disable, privacy and [shared retention](onboard-my-project-plan.md#evidence-retention) through their existing owners, with D9's [metrics-only remote retention exception](onboard-my-project-plan.md#metrics-only-remote-retention) for eligible exports. Required local execution evidence remains independent of optional observability. This helper does not prove opt-in, authenticity, freshness, current completion, export eligibility or applicable destination-policy compliance; its local identifiers are not an export-safe payload.

Future collection must follow D9's [selective workflow policy](onboard-my-project-plan.md#selective-workflow-collection), including owner-resolved task/scope eligibility, minimal observations, complete qualified accounting and source-side filtering. The local preparation flag and supplied report do not establish that eligibility, implement sampling or filter any exporter. Do not add conversation classification, source refresh or collection authority to this pure helper.

The separate [Manage Observability Integration skill](../skills/manage-observability/SKILL.md) is the provider-neutral entry point for explicitly requested integration management. Its [connection-control contract](../skills/manage-observability/references/procedure.md#local-control-contract) adds dedicated `configure`, `status`, `enable`, `disable` and fixed synthetic `probe` commands for existing accepted Codex POSIX checkout authorities. The new private operational settings owner is separate from the pure preparer and SQLite task/evidence owners. Desired checkout connection consent does not grant canonical Project-wide collection, qualify native sources or turn this projection into an export payload. Routine tasks and unrelated general Q&A do not load it; onboarding may only propose it separately after successful completion.

The synthetic transport re-verifies the current selected-source project key pair before every live operation and sends no actual task data, usage or charges. Optional native credential storage uses explicit secure backends through pinned keyring; existing version-1 records keep environment behavior, while explicit version-2 configuration records the source only. Hidden local `store-credentials` verifies the pair before one native write and keeps consent off; explicit `forget-credentials` removes only the selected saved connection. Both follow the provider's [shared credential impact rule](../skills/manage-observability/references/langfuse.md#shared-credential-impact) before entry or mutation. Owner checks surround delayed input/lookup, native mutations and the sole POST. Status and disable require no keys, store query or network. Protected configuration stores no secrets, measurements or probe history; required local evidence and retention remain unchanged.

The provider's [setup mapping contract](../skills/manage-observability/references/langfuse.md#select-a-destination) recommends a dedicated Langfuse project per logical Vibe Project and allows confirmed sharing. Known active use by another distinct Vibe Project requires explicit sharing confirmation before linking; same-Project repository/worktree reuse is normal. Unknown mappings remain unknown and require clarification. Current checkout controls do not supply canonical cross-Project discovery or a global enforcement guarantee; reuse the existing owner rather than add a registry or inspect secret-store inventory.

Local HTTPS, fake native-store and actual hidden-terminal/CLI proof establish this bounded path only. They do not establish real OS-entry access, live-account write/readback, installed-client skill execution, Windows runtime support, source qualification, D9b or D11. Preserve these observations separately.

D9b acceptance still requires the native-only coordinator/worker/reviewer task, authoritative source reconciliation and preceding D7b proof. Exports require destination permissions, a qualified metrics-only payload and disclosure of destination retention/access behavior; eligible metrics do not require remote deletion qualification or plugin deletion logic. Local evidence retention remains unchanged. D11 remains last and requires supported target-version dashboards and observed scope/filter behavior. Preparation and unit fixtures pass none of those gates. No integration account, service connection, dashboard or collector is activated by this slice.

## Validation recorded on 28 October 2026

The helper and tests were implemented in the isolated `codex/observability-preparation` worktree, based on `354bb897d8e6ec8cb07cc6497629665ef448c55b`. Genesis agreed the source/caller boundary and arranged independent source/security review with no blocking findings. The focused 13 tests passed on Python 3.11 and Python 3.15; package validation and diff checks passed. The canonical positive test failed for the missing helper before implementation, and the custom-scalar regression failed before its fix.

Broad regression is **incomplete**. One final-snapshot discovery attempt found 951 cases; 455 passed, none reported failure/error/skip, one was interrupted and 495 had not started. The agreed overall limit was 30 minutes; the controlled interrupt was observed at 30m7s (exit 130). The last case was `test_evidence.LinkedVerificationTests.test_linked_preparation_post_release_observation_substitution_keeps_raw_source`. The retained stack is in existing linked-evidence capture/Git observation, not this helper. The run was not restarted or relabeled as passing. SQLite ResourceWarnings also appeared in the pre-implementation baseline. Genesis retains broad lifecycle regression/performance qualification.

Raw discovery output, per-case counts, the interrupted stack, staged patch and the final source-hash/validation report are retained under `/Users/fcouto/.codex/worktrees/observability-preparation/handoff/`. These are authoring verification artifacts, not runtime observations or a measurement ledger. Preserve needed artifacts before archiving the worktree. No current client, native usage, export/deletion, caller-hook or dashboard qualification is claimed, and the integration remains inactive.
