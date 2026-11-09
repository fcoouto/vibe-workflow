# D9b: Task-status observability preparation

28 October 2026 · Bounded implementation slice under [D9](onboard-my-project-plan.md#d9-qualification-usage-and-pilot). Read when implementing or reviewing this preparation path. This is local supplied-data preparation, not a collector, export payload or native measurement qualification.

## Selective port to current main

Current-main source composition · 2 November 2026 · reviewed historical source `83869bef209ce630facd4aa9046efc4adc8396e5`. The initial selective port used `0da866fa`; the compatibility slice preserves that source while adapting to current canonical authority and task states. Earlier validation records below retain their original source bindings.

The preparer reuses `runtime.task_statuses.STATES`. Ordinary task/status, title synchronization and delivery stay under their existing owners. Optional preparation neither performs an extra status refresh nor enables collection/export.

Connection controls reuse `startup.acquire_project_entry` and `_entry_scope`, require a qualified canonical Codex runtime and recheck its actual authority at the existing guarded boundaries. Consent binds the accepted scope and physical authority, plus the qualification provenance omitted from the accepted-identity projection. No legacy bootstrap or alternate activation route is supplied. Unqualified project acquisition blocks these controls before credential/account access.

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
| `task`, or `None` | `task`, or `None` with `task_unavailable` gap | Copy only client, session ID, task ID, source state, revision and pins. No `scope_ref`, mode, reasons, conditions, review/check content or arbitrary metadata. Source state `ready` cannot establish current readiness. |
| `task.accepted_identity` for canonical pin schema 3 | `task.accepted_identity` with only `project_id`, `repository_id`, `checkout_id` | Validate selected identity fields and client agreement. Do not copy roots, paths, bindings, membership authorization or configuration. Legacy scope yields `None` and `canonical_identity_unavailable`; never derive canonical IDs from a legacy project label or path. |
| Optional `historical` | `historical`, or `None` | Preserve only its existing historical/outcome/current/implementation_verified/delivery_complete/merge_authority/status/compact structure. Require matching task outcome, historical true and all current/authority flags false. Preserve `retained` versus `unavailable`; unavailable adds `historical_support_unavailable`. A compact reference is local `{name,digest}` only; never resolve or copy its body. |
| No supported consumption/settings/coverage source | `usage`, `charges`, `native_model`, `native_settings`, `worker_coverage`, all `None` | No estimates, zero defaults, worker inference, totals, comparisons or OpenRouter/native-source merger. |

Every result includes `preparation_only: true`, `source: "runtime.state.task_status"`, and fixed gaps `source_authenticity_unverified`, `source_freshness_unverified`, `export_eligibility_unverified`, `current_readiness_unverified`. This output is not a Langfuse API schema or a new authoritative observation. Output identifiers and compact references remain private local data until an independently qualified export contract permits a safe representation.

`blockers` and `limitations` are bounded source containers only: neither their text nor their elements enter the projection. Unknown or malformed supported fields raise a fixed, bounded `ValueError` without echoing input. No prompts, completions, source code, review text, credentials, paths or arbitrary metadata are copied from excluded fields, even when they contain nested or non-serializable objects. The helper does not claim universal redaction of user-chosen identifier strings.

## Bounds and source compatibility

Use plain built-in dictionaries, lists, strings, integers and booleans for inspected input. This avoids invoking custom container or scalar methods during preparation. Reuse source text bounds: task ID 128 characters, session ID and legacy pin strings 1024, canonical identity IDs 128, and printable nonempty text. Canonical pin digests remain 64 lowercase hex characters; schemas 1/2 retain their existing three-key pins, schema 3 its four-key pins. Clients and task states follow this existing source's native-client/state enums, not a model allowlist.

Generation is nonnegative and revision positive, within SQLite's signed 64-bit counter range. The supported report has at most six fields; pins at most four; the bounded task/accepted-identity projections inspect parents with at most 32/16 fields; historical has its exact eight-field shape. Blockers/limitations have at most 64 entries, matching the source condition bound and covering its fixed diagnostic list without reading any elements. These are preparation input limits, not a new persistence or retention policy. Unsupported future source shapes require explicit compatibility review.

## Ownership, execution and verification

This chat owns additive `runtime/observability.py`, `tests/test_observability.py`, this contract and the small [implementation plan](superpowers/plans/2026-10-28-observability-preparation.md). Genesis retains core evidence/state, delegation, changes/onboarding/startup, adapters, `scripts/workflow.py`, caller integration and shared primary planning documents. The helper performs no filesystem/native-history reads, persistence, environment changes, network/API calls, exports, configuration, dashboard provisioning or readiness mutation. It imports no core state/evidence module and adds no dependency.

Implement test-first against genuine disposable legacy, canonical and empty task-status API responses. Check valid projection, canonical client separation, source-state/authority distinctions, disabled sentinel input, historical marker shape, missing/malformed fields, bounds, excluded content, independent output and input nonmutation. An audited subprocess must demonstrate no new filesystem, network/process or core-state action. Historical marker fixtures are explicitly synthetic; they do not qualify closure or retention.

Independent review must cover the exact source caller and real response compatibility, error privacy and the absence of authority escalation or hidden collection. Run focused tests, package validation and the existing test suite on the final source snapshot; report existing failures/warnings rather than masking them. The implementation plan owns the bounded test/verification steps.

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
