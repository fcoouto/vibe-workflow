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

## Proposed completion tasks

Execute later through [Workstreams](../skills/workstreams/SKILL.md), using ordinary native child delivery and its delegation policy. At implementation startup, recheck the saved choice with `python3 -I -B scripts/workflow.py features status --cwd .`; this proposal changes no preference. Advanced resource/recovery controls and managed task state are not prerequisites unless separately selected. Preserve coordinator model, reasoning, authentication, subscriptions and user customizations.

### O0 — Close current-source verification gaps

**Owner:** validation implementer; coordinator accepts evidence. **Depends on:** approved execution scope, not live integration access. **Files:** existing six observability/protocol test modules, `tests/test_persisted_binding.py` and affected canonical/runtime callers. Change production code only for a reproduced in-scope regression.

- [ ] Run the pinned Windows backend class in an explicitly approved disposable interpreter containing the existing optional requirements. Record software results separately from real Windows/OS qualification; do not install globally or silently change Python.
- [ ] Freeze the candidate; run `python3 -B scripts/test_sharded.py --workers 2 'test_observability*' test_langfuse test_persisted_binding test_runtime`, `python3 -B scripts/validate.py` and `git diff --check`. Reuse assessment evidence only while its source, fixtures and conditions remain applicable.
- [ ] At O7, run the previously required broad regression on the final integrated candidate with `python3 -B scripts/test_sharded.py`. Inspect estimates first, agree a numeric time/resource bound and keep the checkout unchanged. Preserve historical failed/interrupted evidence and report incomplete runs rather than restarting indefinitely.

**Exit:** candidate-bound counts, skips, warnings and failures are recorded. Optional backend and final full-suite gaps close independently; neither becomes native proof.

### O1 — Freeze interfaces and qualification scope

**Owner:** coordinator with source/evidence owners. **Depends on:** user approval of this proposal. **Writable scope:** this plan and existing owner contracts only where an interface decision requires an update.

- [ ] Recheck D5 acceptance, D6 task/worker/attempt identity, canonical membership and [D7b enforcement proof](d7-repository-gate-plan.md#3-qualify-enforcement-then-propose-live-rules). Link accepted evidence or mark it missing. D7b gates acceptance, not independent D9 software preparation.
- [ ] Select one initial native client/surface/version and authoritative usage source. Record provenance, request/attempt identity, observation times, cumulative/incremental semantics, parent/child inclusion, cache/reasoning inclusion, model/settings coverage and charge basis. Inspect existing adapters and `opencode_accounting.usage_from_messages` before adding parsing; record unsupported sources.
- [ ] Freeze producer/consumer interfaces for Project consent/selected repositories, owner-resolved task eligibility, immutable numerical receipts, expected participant/attempt set, acceptance outcome/cutoff, export-safe correlation and disable/forwarding ownership. Name exact files, signatures, fields and tests before releasing dependent workers. Disabled file-only projects must not acquire a runtime requirement.
- [ ] Refresh official version-matched native/Langfuse documentation for content-free ingestion, readback and unit/cost semantics. Obtain separate authorization for later installation, credentials, live records, native runs or service expense, with numeric run/time limits. Do not choose an SDK or auto-instrumentation by default.

**Exit:** reviewed interface/file-ownership table in this plan, supported scope and explicit source gaps. Stop dependent work if authenticity or attribution would require prompt inspection, rerouting or another registry.

### O2 — Add Project consent and destination selection

**Owner:** settings/membership implementer; independent authorization reviewer. **Depends on:** O1 consent/membership interfaces.

**Files:** extend `runtime/observability_settings.py`, `runtime/local_ownership.py`, `scripts/observability.py` and existing tests where necessary. Coordinate canonical-owner edits in `runtime/startup.py`/onboarding; update management references alongside supported behavior. Preserve version-1/environment and version-2/explicit-source records; checkout consent must not silently become Project collection consent.

- [ ] Write failing tests for explicit Project enablement, selected/excluded repositories, same-Project worktree reuse, two distinct Projects sharing only after matching confirmation, unknown mappings and declined sharing. Do not inventory secret stores or scan for Projects.
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

**Exit:** captured payloads and independent privacy/security review establish the allowlist and no prohibited content export; focused integration tests pass. Start without sampling. No auto-instrumentation, per-turn wrappers, pricing engine or plugin remote-deletion logic is added.

### O6 — Qualify installed management and live connection

**Owner:** qualification operator; independent observer. **Depends on:** O0's focused checks for the selected controls and separately authorized environment, not O0's final O7 broad run or the optional Windows class for a non-Windows path. Requalify affected paths after O2/O5 changes. Connection-only proof need not wait for full D9b acceptance.

**Scope:** existing clean-package/installed-client procedure and private evidence; publish sanitized results here. Use the user's foreground hidden-entry path, selected destination and explicit record/time budget. Never put secrets in worker prompts/logs.

- [ ] Observe installed skill discovery/selective loading. Prove onboarding only proposes management and routine disabled work loads no provider procedure or optional collection.
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

**Proposal completion:** user reviews assessment and task order. Implementation/live qualification remain unstarted. Future checkboxes require observed accepted evidence; document checks, fixtures and native qualification remain separate claims.

**Proposal review and checks:** focused implementer review covered requirement coverage, dependencies, existing owner reuse and authorization/privacy boundaries. This change modifies no executable code, permissions, credentials, settings or collection behavior; independent implementation/source/security reviews remain future task gates. Package validation passed without warnings and whitespace checks passed. Full regression, installed/live qualification and the optional pinned-backend class were not completed by this planning task.

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
