# Policy source map

## Smart Routing OpenCode adapter (S3.2) — 8 November 2026

Task S3.2 of the [Smart Routing plan](superpowers/plans/2026-11-08-smart-routing.md) adds the OpenCode client contract and its pure normalization module. No plugin, launcher, ledger write or client registration is part of this slice.

| Local contract | Requirements carried locally | Basis |
| --- | --- | --- |
| [OpenCode client](../clients/opencode.md) and `runtime/adapters/opencode.py` | Loaded only for OpenCode Smart Routing work; normalization of the four routing hooks, lineage and usage events and stored messages; prompts, text, command text and header/environment values never carried; unknown fields stay unknown; fixed-reason errors without payload content; ID patterns; route identity as alias + requested variant + catalog family with the resolved model `unknown`; `probe` classes from the capability matrix (fail-open plugin load, unenforceable shell contents, health check before managed launches, advisory-only shell-keeping profiles); the four user decisions; no observed-behavior claim beyond the committed fixtures | Spec §8; plan Task S3.2; [capability matrix](smart-routing-capability-matrix.md#opencode-enforcement-p01-p05p08) and the user's decisions of 8 November 2026; OpenCode 2.2.6 probe fixtures, including the hook and event excerpt `p0-hook-event-excerpt.json` taken from the same runs' sanitized observations |

## Review my Project (D10) — 8 November 2026

The review instructions from the [D10 plan](d10-project-review-plan.md) were ported to current `main` with their instruction-delivery tests and fixtures. The report and retention runtime candidates were not merged. Validation evidence is in the [D10 validation record](d10-validation.md).

| Local contract | Requirements carried locally | Basis |
| --- | --- | --- |
| `skills/review-my-project/SKILL.md` and `references/procedure.md` | Repositories before areas, native selection/fallback, preserved selection, unenrolled/temporary scope, bounded Full Scan, project rules, safe command inspection, canonical check reuse, complete selected security/dependency coverage, honest actionable findings, separate installation and fix authority | Approved D10 plan; Toolkit Prompt 13 and existing local policies 01/04/05/06; user-approved standalone chat-first outcome |
| `references/source-structure.md` | Explicit analysis approval, fresh shortlist, per-repository partial coverage and sample-only priorities, supported refactoring recommendations, separate implementation approval, no measured workflow-savings claim | Approved 28 October source-structure extension preserved from the saved D10 draft |
| `references/retention.md` | Existing canonical evidence owners; original ages/allowances, enrolled accounting, protected overlaps/unknowns, seven-day detail/four-month compact/100 MiB Project rules, no new store, chat-only detailed reporting until the complete existing retention path is available | Canonical membership/retention plan and inspected D5/private ownership interfaces; no runtime qualification claimed |

## Reusable native evaluation — Phase 3, 5 November 2026

The user authorized reusable evaluation packaging after Phase 2 integration.
The [Phase 3 plan](superpowers/plans/2026-11-05-workflow-templates-phase-three.md)
uses the retained Toolkit snapshot identified as revision
`21b71b68679c11184f49c23168852014bf6dde8b`. The retained snapshot has no Git
metadata; its adopted bytes are bound by the source hashes below. No unrelated
contract or active task is repinned, and routine maintenance needs no Toolkit
checkout or fetch.

| Adopted source or requirement | Local owner and adaptation |
| --- | --- |
| `templates/harness-evaluation/access.mjs`, SHA-256 `fa96fe6a759bc49db77236810a83bb4c97d3ce693124bb6505545e0193550240` | `assets/harness-evaluation/boundary/access.mjs` retains the intentional ownership/tenant defect for negative controls; never application code. |
| `scripts/validate-harness.mjs`, SHA-256 `14220a750afbcf228fc397d5d3d32f10631c0f2e0a533c0ba8fa79b553f62e1c` | Local script of the same name retains seven checks; only the expected-notes URL changes to the protected package asset. |
| `templates/harness-evaluation/local-notes.txt`, SHA-256 `79e3fc6afaf135133c764bc261b5040575c373a80dcb44b254bce07e5a4a5b0c`; complete MIT notice, SHA-256 `d687e4265813cc684a8307b579a68ac19578f3de351aafbd6419da926b52203f` | Exact notes and license are bundled. Fixture instructions are locally authored from the scoped requirements; no upstream prompt is adopted. |
| Protected inputs/checks, stale-handoff repair, read-only review and explicit observation limits (`docs/07-harness-evaluation.md`; existing maintenance contract) | `policies/09-workflow-maintenance.md` conditionally loads `clients/harness-evaluation.md`. Independent hashes/diffs complement the evaluator: its notes check does not establish instruction preservation or host isolation. |
| Reusable reapplication and lifecycle/reporting cases from accepted Phase 2 evidence | Selected assets retain twelve synthetic input files and three document overlays. Case notes preserve partial-undo and native-observation limits; lifecycle, role, title and recovery rules keep their existing owners. |

`tests/test_harness_evaluation.py` checks actual evaluator behavior and relocated
asset resolution. Package completeness uses the existing local-file validator
without adding Node to ordinary package validation. Those checks do not establish
native instruction use, pauses, provider actions, startup or first-badge rendering.
Actual execution and acceptance belong in the implementation record. This section
is authoring provenance, not a second routine loading dependency.

## Lifecycle and project artifacts — Phase 2, 5 November 2026

The user approved the single lifecycle layout and compact child-report direction
and authorized the [Phase 2 plan](superpowers/plans/2026-11-05-workflow-templates-phase-two.md).
Toolkit authoring source remains `21b71b68679c11184f49c23168852014bf6dde8b`;
the repository HEAD and retained source/catalog hashes were checked during this
execution. No unrelated source contract or active task is repinned.

| Adopted requirement | Owner and evidence scope |
| --- | --- |
| One local lifecycle record for start, meaningful checkpoints and completion; compact reports preserve material facts and uncertainty (user-approved direction; existing bounded handoff requirements) | `policies/07-context-and-handoff.md#lifecycle-record-and-reports`; planning and delegation link to it. The implementation plan records static checks and subsequent bounded native observations separately. |
| Title handling keeps its existing eligibility, ownership, readback, pause and failure semantics | Lifecycle handling references `clients/workstreams-naming.md` and its title procedure; no new title state store, runtime feature or execution blocker. Native metadata/readback does not establish visible rendering. |
| Adapt all nine project-document responsibilities to actual project facts and canonical equivalents (`templates/project-docs/manifest.json`, `docs/06-project-defaults.md`) | `skills/onboard-my-project/references/procedure.md#adapt-project-documents`; the reviewed proposal reuses that mapping. Relevant file inspection, accepted checks, startup limitations and private recovery retain their existing owners. |
| Preserve context, permissions, budgets, ownership and independent acceptance while keeping handoffs proportionate (`docs/tools/02-subagent-orchestration.md`; existing local delegation/handoff contracts) | The compact report is a view of existing task context, not authority. Direct/persisted validators and file-configuration transactions remain unchanged; existing recovery regressions supply only their recorded scope. |

The constructing/consuming code audit found no safe simplification justified by
these prose artifacts. Existing shared validation and transaction paths are
retained. The security-report layout remains deferred without a concrete gap.
The native pilot exposed broad cross-service tool-metadata retrieval; its
successful narrow recovery supports a bounded discovery recipe in the existing
title owner. The implementation record retains the oversized lookup and cost
limits; no savings or transport-enforcement claim follows from that repair.
This section is authoring provenance, not another routine loading dependency.

## Selective workflow examples — Phase 1, 5 November 2026

The user approved the [Phase 1 plan](superpowers/plans/2026-11-05-workflow-templates-phase-one.md)
under the [selective-template design](superpowers/specs/2026-11-05-workflow-template-design.md),
including a requirement that routine complexity stay flat or decrease. The
authoring review used Toolkit revision `21b71b68679c11184f49c23168852014bf6dde8b`.
This selected-source assessment does not repin unrelated contracts or active tasks.

| Source requirement | Local owner and proof |
| --- | --- |
| Adapt only useful template components; retain actual project facts and existing equivalents (`templates/README.md`, `docs/06-project-defaults.md`) | Basic and selected-managed examples in `skills/onboard-my-project/references/reviewed-configuration.md`; relevant-inspection and unenrolled-checkout regressions preserve ordinary native work and recovery. |
| Keep ordinary work proportionate and load details on demand (`docs/prompts/12-existing-project-setup.md`, authoring reference only) | `README.md` and `clients/evidence.md` route managed evidence only when selected; shared policies still own ordinary checks, security impact and review. |
| Establish initial smoke cases and requalify material instruction changes without inventing an optimization trial (`docs/07-harness-evaluation.md`) | `policies/00-index.md` routes initial setup and material instruction/memory changes to the existing maintenance owner; observed cases and their limits are recorded in the implementation plan. |
| Validate real invariants and distinguish accepted plans from executed evidence (`docs/prompts/12-existing-project-setup.md`, `docs/07-harness-evaluation.md`) | Named examples in `clients/evidence-plan.md` run through existing `runtime/evidence.py` operations in `tests/test_evidence.py`; malformed and stale requests retain their current rejection paths. |

No source prompt is copied into routine instructions. Existing validators, task
records and recovery remain authoritative. The delegation audit preserves the
early role check and the direct-entry check: removing either loses a distinct
boundary, while consolidating them adds more machinery than the duplicated
computation warrants. No new runtime layer, role-only step or spawn blocker is
introduced. Package checks and native observations are separate; this provenance
section is authoring/update material, not a routine loading dependency.
## Shared external delegation — 5 November 2026

The owner approved the [shared design](superpowers/specs/2026-11-05-shared-external-delegation-design.md)
for native Astra/high implementation, preserving local priority and native review,
then requested simple implementation without extra hardening controls. Source was
reconciled on current `origin/main` at `c4a4eb856b7c491d706148acb760bad066f4489c`;
no installed runtime was reconstructed or edited.

| Requirement | Local contract and implementation |
| --- | --- |
| Explicit choices, local wait/native fallback, useful automatic consideration | [Shared selection](../clients/external-delegation.md#select-before-dispatch), loaded by Agent Delegation; offline shared selector |
| Existing approvals, checkout/destination/mode scope, no mandatory cap/expiry | [Reusable permission](../clients/external-delegation.md#reusable-permission); optional existing Usage Routing/OpenRouter configure fields |
| Real source packet, derived prose, exclusions, dispatch recheck | [Packet contract](../clients/external-delegation.md#construct-and-recheck-the-packet); shared byte construction, OpenAI final grants, OpenRouter dispatch and worker launch |
| Accounting, duplicate/uncertain attempts, route-specific retry ownership | Existing route procedures and ledgers retained; scoped proposal dispatch uses its existing attempt token |
| Funded API admission and distinct native acceptance | [Foreground host](../skills/manage-usage-routing/references/codex-host.md) binds task consent, financial coverage and a distinct native reviewer; existing ledger retains API reservations and accounting history |
| Distinct native acceptance and truthful provenance | [Native acceptance](../clients/external-delegation.md#native-acceptance-and-recovery); trusted native preparation, existing OpenAI review receipts and scoped OpenRouter review references |

The [implementation plan](superpowers/plans/2026-11-05-shared-external-delegation.md)
records fixture checks and instruction walkthroughs separately from unobserved
client/provider execution. These additions preserve Toolkit's delegation and
verification requirements; source permission does not become execution authority.


## Cooperative D6 scope — 4 November 2026

The user approved cooperative native delegation, retained uncertain ownership
and deferred automatic takeover. The subsequent whole-plan review with Astra
separates useful delegation from stronger enforcement qualification. The
[canonical D6 scope](d6-native-delegation-plan.md#approved-cooperative-scope)
now targets reservation, once-only native dispatch, applicable checks,
independent review and cooperative handoff. Normal completion relies on actual
native activity, inspected effects and coordinator control of follow-ups, not
independent access to hidden host queues or retry internals.

The [short successor packet](superpowers/plans/2026-11-04-d6-successor-release-packet.md)
records the completed standalone Mac CLI pilot and its spent allowance. The
[held managed-hook record](superpowers/plans/2026-11-03-d6-managed-lifecycle-pilot.md)
preserves the exact canary, marker, D4 bootstrap and original finite limits only
for that selected stronger route. Enforced patch-only isolation, universal
descendant proof and other surfaces are not core D6 acceptance gates. Existing
selected protections, historical claims and original proof meanings remain.

The [native procedure](../clients/delegation.md#observe-and-reconcile-independently)
and [proof reference](../clients/delegation-proofs.md#cooperative-native-completion)
define version-3 cooperative evidence for coordinator-owned follow-ups while
retaining version-2 semantics. Offline coverage exercises a selected guarded
worker, dependency-ordered registered check, and independent reviewer of the
same candidate. Reviewer admission preserves the worker's pre-edit witness.
The check's external lifecycle evidence remains synthetic in that fixture;
actual execution needs its own independently established proof. Strict recovery,
registered checks, ordinary D5 readiness and merge evidence retain their requirements. The earlier construction and schema-transition instructions
are superseded as active work, not a mandate for another migration. This retains
Toolkit's delegation, ownership and review outcomes under the stated trust model.

## Subagent role badges

The user requested role badges for subagents and independent dispatch.
[Agent Delegation](../policies/03-agent-delegation.md#select-capability-and-dispatch)
loads the [shared contract](../clients/worker-visibility.md#declare-before-dispatch).
The coordinator chooses each subagent's role once while preparing its assignment.
Ordinary native preparation accepts the full brief and returns the native name
and message with its existing correlation marker. Managed D6 worker and D5
review CLI preparation captures the returned declaration and ledger digest
outside the runtime transaction, preserving immutable prompts and launch results.
Managed continuation preparation reuses the existing child's role and retained
request prompt after the ledger operation, before consuming its one-use intent.
Ordinary retries reuse matching saved declarations and their exact first-line
marker. OpenRouter and Usage Routing require the assignment role in their
existing brief or proposal fields for workflow-created jobs, including separate
native supervisor assignments. The shared rule covers lightweight reviewers,
testers/evaluators and nested children independently of Habblaud configuration.
Display preparation failure never blocks dispatch. Legacy role-only preparation
remains a compatibility and recovery path, not an extra routine step.
For a reused native child, the same assignment preparation preserves the
original prompt and reuses its declaration or captures a missing role once.
The dedicated Habblaud adapter owns delivery, including replay of previously
observed contexts; the receiver retains badges across restarts for matching
native children. Replay never creates presence or changes activity.

Native badges use a bound declaration or the reported native role. Habblaud
shows its standard subagent fallback when neither source supplies a role and
leaves native thread owners without a role badge. A native CLI probe found the
spawn message encrypted in `PreToolUse`; automatic hook parsing cannot capture
its role. No new hook or role inference is introduced. Direct native spawning
can still omit metadata, and first-badge rendering needs separate observation.
Display identity checks validate each native checkout independently when a
coordinator moves worktrees and a child retains its original checkout. The
declaration must match the verified parent or child checkout. Execution scope
checks keep their existing stricter boundary.

Workspace preparation and integration discovery load the existing-hook
[reconciliation procedure](../clients/worker-visibility.md#reconcile-on-discovery-and-upgrade)
during fresh installation, onboarding and authorized upgrades. The packaged
[receiver procedure](../clients/habblaud-receiver.md) applies the bundled patch
to compatible upstream source, verifies and deploys the resulting build, and
recognizes an already-patched installation. Native presentation reuses the
verified child's model display metadata and formats its actual identifier when
catalog lookup is unavailable, following Vibe Setup's
[consumer display guidance](https://github.com/fcoouto/vibe-setup/blob/main/skills/vibe-setup/native-display.md).

## Usage Routing enablement — 4 November 2026

The user authorized first-task routing after Keychain setup and explicit
activation in either mode while other Codex work continues. The
[management procedure](../skills/manage-usage-routing/references/procedure.md#set-up-and-select-a-mode)
binds consent to the selected configuration. The
[proposal procedure](../skills/manage-usage-routing/references/delegation.md)
and [trusted host](../skills/manage-usage-routing/references/codex-host.md)
carry Manual task selection, scoped Smart Balance consent, source and funding
checks, full API cost reservations, task identity and independent
verification. Measurements, previews and bounded canaries are optional.
The user directed that smoke-test limits stay isolated from the
[regular-routing budget](../skills/manage-usage-routing/references/procedure.md#regular-routing-budget).
The user approved independently optional cumulative and per-task caps for
regular routing and kept verified subscription-benefit funding for the
selected project. The [monthly policy and reservation design](superpowers/specs/2026-10-31-usage-routing-design.md#monthly-ceiling-choices)
preserves the funding choice, grant, cycle, retained exposure and per-request
quote and reservation requirements when either cap is omitted.
[Observed validation](usage-routing-validation.md) records its tested scope
separately from current admission rules.

## API usage activation — 5 November 2026

The user approved [safe setup reuse and the public name Manage API Usage](superpowers/specs/2026-11-05-api-usage-activation-design.md).
The [management procedure](../skills/manage-usage-routing/references/procedure.md#set-up-and-select-a-mode)
routes explicit enablement through read-only discovery, unique compatible reuse
or explicit selection. Its [status contract](../skills/manage-usage-routing/references/procedure.md#read-only-status-quoting-and-selection)
distinguishes provider project identity from enrolled local checkout scope.
The [enable schema](../skills/manage-usage-routing/references/procedure.md#explicit-local-configuration)
defines target revision checks, selection, fresh checkout consent and permission
isolation. Discovery supplies a required first-reuse setup pin, transactionally
binding the selected source state, setup and current organization policy/revision;
effective mode comparison collapses otherwise equivalent source modes. Complete
safe profile/conversion and connection metadata make real differences visible
without credential references or secret access. `runtime/usage_settings.py`
checks retained canonical pins and source evidence before new bindings, preserving
recovery for replacements or incomplete restores; it retains enrollment and ledger identity;
`runtime/usage_budget.py` reuses validated unchanged policy revisions without
clearing charges or reservations. Real policy changes still invalidate stale
consent. Stale, expired, unqualified-binding and smoke-only setups cannot enable
regular live routing through reuse. The public heading and UI metadata change;
the `manage-usage-routing` identifier and reference paths remain compatible.
No live activation, credential lookup, billing change, purchase or publication
is authorized by this implementation. [Validation](usage-routing-validation.md)
separates offline proof from observed client execution.

## Usage Routing delegation registration — 4 November 2026

The user approved the [implementation plan](superpowers/plans/2026-11-04-usage-routing-delegation-registration.md)
and directed that the unresolved run and reservation stay on hold.
[Delegation selection](../policies/03-agent-delegation.md#select-capability-and-dispatch)
loads the existing [Usage Routing procedure](../skills/manage-usage-routing/references/delegation.md)
for a selected and authorized proposal. The routing result supplies the
actual execution route. Assignment context and the existing ledger retain
task identity, admission and accounting.
Native handoff and independent review retain their existing evidence and
authority. The [Codex host](../skills/manage-usage-routing/references/codex-host.md)
verifies settlement and the parent before using
[shared external registration](../clients/worker-visibility.md#external-routes-in-chat-and-habblaud).
The registrar reuses existing display identities, private receipts and readback.
The [validation record](usage-routing-validation.md) separates offline tests
from live qualification and preserves the pending recovery evidence.

The user requested route suffixes on native activity boxes and removed the
standalone chat label. The [native task naming rule](../clients/worker-visibility.md#external-routes-in-chat-and-habblaud)
sets the suffix before dispatch for a real supervisor. On Codex Desktop
26.1029.61003, `review_results_via_openrouter` rendered as
`Review results via openrouter`. `api_display_investigation` identified the
native investigator. Foreground Usage Routing runs in the coordinator.

## Shared worker roles — 3 November 2026

The user approved explicit role and custom-name visibility for native and
OpenRouter workers. The [shared visibility contract](../clients/worker-visibility.md)
extends the existing optional OpenRouter/local-delegation display transport.
It preserves Toolkit delegation, specialist responsibility, ownership and
independent verification requirements. Native and external identities remain
distinct; immutable markers, original pins and authority roles are unchanged.
The extension grants no new execution route or installation authority. Source
fixtures verify label delivery behavior; actual client rendering and first-binding
compatibility remain unqualified.

## Managed-runtime birth without unrelated history — 3 November 2026

The user's selected D4 slice removes temporary-review storage as a prerequisite
for fresh managed-runtime creation. The [runtime contract](../clients/runtime.md#change-a-pinned-runtime)
and [D4 boundary](d4-onboarding-plan.md#operation-boundaries) retain explicit
authority, original pins and provenance, known peer/owner/descendant protection,
durable birth receipts and recovery. A tagged absent-history witness grants no
stopped-writer evidence or optional control readiness. Existing ready-history
requirements remain unchanged. This implements the accepted D4 ownership and
pilot-readiness requirements; it does not adopt new Toolkit components or enable
any installation, native qualification, repository enforcement or external route.

For policy authoring, setup, reviewed updates and coverage audits only. Runtime reads the local policy index and applicable contracts; it does not need this file, Toolkit access or a permanent source snapshot.

Vibe Coding Toolkit remains the workflow authority. The contracts express its applicable requirements in local wording, with the user's extensions identified below. `vibe-setup` is an implementation reference only.

## Codex activation guarantee · 2 November 2026

The user adopted [setup-time behavioral qualification plus later observable configuration](activation-guarantee-proposal.md) for capabilities that depend on managed activation. [Project Scope](../policies/01-project-scope.md#authority-and-activation), [Project Onboarding](../policies/10-project-onboarding.md#verify-and-finish), [startup](../clients/startup.md#codex-qualification) and [qualification/activation](../skills/onboard-my-project/references/qualification.md#activation-and-reporting) route its use. Invisible session-local disablement, revocation or stale loaded controls are an accepted limitation. The 3 November minimal-default decision below makes this campaign selective; it is not a prerequisite for ordinary development or basic onboarding. Actual selected-capability evidence, source identity, writer/prior-owner reconciliation and recovery remain required. Contract adoption is separate from native observation implementation and pilot evidence.

## Staged pilot qualification · 2 November 2026

The user approved [staged qualification](pilot-readiness-plan.md#completion-boundaries): explicitly disposable scopes may perform basic guarded work and bounded qualification while onboarding remains incomplete. Pending capabilities block ordinary dependent use. This pilot retains its 44 mandatory requirements; other projects use the mandatory set applicable to their catalog and scope. Full-onboarding/READY/merge prerequisites and primary-activation evidence remain in force. Expiry or interruption does not release uncertain writers or grant readiness; recovery remains separately authorized. The [pilot coordination plan](pilot-readiness-plan.md) tracks implementation and observed native qualification separately. This extension authorizes no live campaign, credentials, protection changes, installation, or new registry, service or public bypass.

## Native staged behavior evidence · 3 November 2026

The [pilot plan](pilot-readiness-plan.md) separates merged PR52 and 137 passing staged software checks from native qualification: package validation passed; client execution was not checked. Codex implementation references pinned at `360319d8dee62d87e2d59fc0c21805d79519cfed` inform observation of launch safety and allocation handling ([`safety.rs`](https://github.com/openai/codex/blob/360319d8dee62d87e2d59fc0c21805d79519cfed/codex-rs/core/src/safety.rs#L11-L14), [`safety.rs`](https://github.com/openai/codex/blob/360319d8dee62d87e2d59fc0c21805d79519cfed/codex-rs/core/src/safety.rs#L81-L131)), patch application ([`apply_patch.rs`](https://github.com/openai/codex/blob/360319d8dee62d87e2d59fc0c21805d79519cfed/codex-rs/core/src/apply_patch.rs#L55-L57)) and parallel execution ([`parallel.rs`](https://github.com/openai/codex/blob/360319d8dee62d87e2d59fc0c21805d79519cfed/codex-rs/core/src/tools/parallel.rs#L323-L338)). These source references guide what to observe; they do not prove effective local edits or OS enforcement. The observed read-only policy rejection proves only that the submitted write was rejected under the observed launch controls; hooks may rewrite proposals and effects may be remote.

The user approved the narrow native source-observation guarantee in [apply and recovery](../skills/onboard-my-project/references/apply-and-recovery.md) on 3 November 2026, accepting unknown, potentially late client-managed startup descendants for this bounded path. The coordinator must still verify the exact CLI/API process was reaped and reconcile workflow-directed and every identified conflicting or uncertain writer; a pending hook, unexpected tool/spawn, unexplained write or conflict blocks affected work. New limited records remain distinguishable, historical full-stop records retain their meaning, and legacy records are not upgraded. This is a user decision, not a Toolkit requirement. D4 command recovery, D5 checks, D6 worker/reviewer reconciliation, READY, merge and full-catalog requirements remain unchanged. Shared native-state effects still require authorization and the serialized window; this does not authorize fixture deletion or native-home rollback. Source/package checks and actual native execution remain separate; no pilot readiness or live campaign follows from this decision.

## Minimal-default onboarding · 3 November 2026

The user approved the first guidance/default-gate slice in the staged `minimal-default-workflow-decision.md`. Basic onboarding inspects relevant project instructions/configuration, assesses every component in the complete Toolkit catalog for relevance, applies authorized changes recoverably, and leaves useful guidance, working project commands, relevant checks, limitations and an undo location. Ordinary native editing, tests and review do not require runtime task state, a database, managed activation or a default patch blocker. Advanced execution, qualification, external routes and merge enforcement load and acquire only their own prerequisites when selected. Preserve actual permission checks, path and writer ownership, backups/atomic recovery, data-loss protection, security, review, unknown-evidence limits and existing protections. This source slice updates instructions and defaults only; it does not claim implementation or native client execution. Usable status, disable and recovery paths are required even when qualification is stale or broken; their implementation status is tracked separately.

Representative instruction paths for this slice: (1) basic enrollment loads the skill, policy index/scope/onboarding and ordinary procedure, then the file recovery contract only for configuration/undo; it does not load managed planning or qualification; (2) an explicitly selected managed dispatcher additionally loads `managed-plan.md`; (3) an explicitly selected adapter/activation campaign loads `qualification.md`; (4) ordinary Codex editing, tests and review use native workflow without task-state or runtime prerequisites; (5) selected merge enforcement loads Merge Readiness controls while existing repository protections continue to apply. These paths check required guidance and unwanted default gates, not runtime behavior.

The standalone basic file-authoring scope may inspect relevant inputs and exact planned effect/evidence paths without a whole-checkout snapshot. Preserve applicable instruction and target preimages, label the snapshot partial, and keep command checks native. This inspection choice changes neither the complete Toolkit component assessment nor permissions, managed authority or runtime requirements for operations that actually select them.

### Onboarding integration and local guidance correction · 3 November 2026

The user requested correction after Opus received documentation setup without startup registration, and its generated security guidance required an upstream prompt during routine review. [Client startup integration](../policies/10-project-onboarding.md#client-startup-integration) now connects basic onboarding to the selected client and shared startup contracts, preserving startup-only configuration, existing managed controls, trust and inspect-only limits. [Apply compatible changes](../policies/10-project-onboarding.md#apply-compatible-changes) routes generated guidance to complete installed local procedures; upstream links remain authoring provenance. [Completion checks](../policies/10-project-onboarding.md#verify-and-finish) separate documents, registration/trust, handler execution and native-session delivery, and exercise representative instruction paths. This corrects the minimal-default loading path without making managed activation or a maintainer campaign mandatory. The [validation record](workflow-simplification-validation.md#onboarding-regression-checks--3-november-2026) distinguishes behavioral checks from native client execution.

## Reviewed sources

These contracts were checked against Toolkit revision `790a895304e0c08b70a5b215c9d334f1c86b816c`. This records the reviewed basis, not a claim that it is still the latest revision.

On 26 October 2026, rechecked orchestration Parts B/C and the parallel execution rule against that revision, which still matched `main`. They preserve the coordinator, select workers from official client guidance, retain test-first implementation and review task changes between waves. They prescribe no cache location or refresh interval. The user's local-cache extension uses concise JSON guidance and maintenance/change-driven refresh; fixed daily expiry and hourly retries are not requirements.

| Source | Relevant scope |
| --- | --- |
| [Subagent orchestration][delegation] | Capability/effort selection, context boundaries, bounded workers, safe parallelism, review and recovery |
| [Security review, Prompt 13][security] | Review authority, reproducible targets, threat analysis, safe evidence, reporting and completion |
| [Existing-project setup, Prompt 12][setup] | Sections 1–5: scope, preservation, decisions and durable working agreements; section 10: review by risk and evidence |
| [Brainstorm to plan, Prompt 04][planning] | Conditional clarification, verifiable plans, lightweight confirmation and checked implementation |
| [Shared AGENTS template][agents] | Working agreements, authorization, execution continuity, canonical documents and honest evidence |
| [Toolkit overview][overview] and [tool catalog][catalog] | Setup paths, complete component assessment and scope-dependent applicability |
| [Harness evaluation guide][evaluation-guide] and [Prompt 14][evaluation] | Disposable behavioral cases, independent observations and repeatable verification |
| [Harness improvement, Prompt 15][improvement] and [method guide][harness-method] | Native baseline/candidate comparison, bounded changes, recovery and separately authorized external labs |
| [Workstreams separation design](superpowers/specs/2026-11-01-workstreams-separation-design.md) | Independent resource-admission and capacity-recovery preferences, shared delegation safety, lifecycle separation, record preservation, selective loading and five representative instruction paths; agreed direction, not implementation or native qualification |

## Requirement coverage

| Local contract section | Requirements carried locally | Basis |
| --- | --- | --- |
| [Workstreams project choice](../skills/workstreams/references/procedure.md), [onboarding question](../skills/onboard-my-project/references/procedure.md#project-preference-questions) and [delegation trigger](../policies/03-agent-delegation.md#select-capability-and-dispatch) | One effective instruction checkbox shared by onboarding and the skill; Enabled/Disabled, read-only status and not-configured diagnostics; immediate recoverable application without runtime activation; idempotence, preserved user guidance, exact checkout scope and unchanged advanced settings; required review, naming and uncertain ownership remain | User's 5 November 2026 clarification and prior authorization to finish enablement; [small design](superpowers/specs/2026-11-05-workstreams-project-choice.md); independent Astra design analysis |
| Optional OpenRouter [management](../skills/manage-openrouter/SKILL.md), [proposals](../skills/openrouter-proposals/SKILL.md) and [read-only workers](../skills/openrouter-workers/SKILL.md) | Native default; separate checkout consent and current canonical authority; selective discovery/triage loading; bounded read-only execution and native verification; saved consent distinct from implemented capability and execution qualification | User-authorized optional integration; preserved OpenRouter implementation and PR23 tool-profile fix, adapted to current canonical authority. Software coverage does not qualify native/external execution, editing, registered checks or automatic worker dispatch. |
| [Stuck-pattern escalation](../policies/03-agent-delegation.md#stuck-pattern-escalation) | Immediate pause and stronger independent diagnosis for stalled coordinator or worker repairs, including cycles of fresh findings; unchanged coordinator, permissions, ownership and original budgets; evidence-backed recovery | User-requested extension, 1 November 2026; [instruction validation](escalation-validation.md). Model selection remains portable; no automatic runtime detector is claimed. |
| [Agent Delegation: select capability](../policies/03-agent-delegation.md#select-capability-and-dispatch) | Preserve coordinator settings; task-based worker selection using official guidance and actual controls; local guidance with refresh provenance; observed/requested settings; native authorization; bounded briefs; test-first new behavior/fixes, refactor baselines and self-review | Orchestration Parts A/B/C; user-approved authorization and local-cache extension below |
| [Outcome ownership](../policies/03-agent-delegation.md#outcome-ownership), [Workstreams preference](../skills/workstreams/references/procedure.md#meaning-and-scope) and [handoff](../policies/07-context-and-handoff.md#handoff-and-resumption) | Small complete outcomes owned by reusable native children through checks, guidance, self-review, review fixes and verified handoff; coordinator decisions/integration; proportionate direct work; dependency-ready dispatch; current ownership, family mappings and execution records; useful worktree isolation distinct from explicitly requested sidebar chats; partial-work preservation | User-approved D7–D11 pilot orchestration review, 5 November 2026; extends Toolkit context-boundary delegation without changing those plans, model choices, permissions, budgets or managed controls |
| [New-thread scope](../clients/workstreams-naming.md#new-thread-scope), [display-family association](../clients/workstreams-naming.md#resolve-the-display-family) and [title adoption](../clients/workstreams-titles.md#adopt-a-parent-or-child) | Newly created contexts store/display P or C; actual native UUIDs identify contexts sharing the root family; concrete root/role briefs and returned-identity binding/readback; native lineage, peer chats and worktree scope remain distinct; manual overrides and immutable bindings preserved; existing titles, records, controls, readers and pins untouched | User's superseding new-threads-only, no-compatibility decision, 5 November 2026; Astra's prospective design check; no ordinal bookkeeping, migration, current-thread repair or new authority |
| [OpenCode sessions](../clients/workstreams-titles.md#opencode-sessions) | Ordinary-path OpenCode titles through `opencode api` session read/update only; no model turn; session ID only from the plugin hook context or a recorded child, never from `OPENCODE_SESSION_ID` (observed unreliable) and never discovered; strict session and title validation; bounded retries for transient failures only, then a silent deferral retried at the next update; manual-title rules, with writes over unknown titles only when empty, default or managed-format (re-adoption), or through an explicit one-time `adopt` at the naming trigger or on user request, returning the replaced title; documented read-to-write race; runtime-bound OpenCode sync, hooks, state and onboarding unchanged | User's 8 November 2026 request that failed renames never surface as errors and decisions to use an explicit, non-persisted adopt flag, also on explicit user request, and to return the replaced title; independent PR review findings; OpenCode 2.2.6 OpenAPI (`session.get`, `session.update`); fake-executable tests only, no observed client execution; the 8 November 2026 observation that OpenCode 2.2.6 does not set `OPENCODE_SESSION_ID` (any value is inherited from the server's launch environment); managed sync deferred to Smart Routing S3 |
| [Agent Delegation: ownership](../policies/03-agent-delegation.md#ownership-and-parallel-work) | Exact file/resource/dependency isolation, uncertainty, concurrency limits, coordinator tracking and serialized authorized Git operations | Orchestration parallel-wave guidance and [parallel execution rule][parallel] |
| [Handoff and supervision](../policies/03-agent-delegation.md#progress-and-result-retrieval), [payload](../policies/07-context-and-handoff.md#handoff-and-resumption) and [managed execution](../clients/workstreams.md#dispatch-acceptance-and-recovery) | Named coordinator/permitted route; meaningful checkpoints with human-authorized peer push or local-report retrieval; receipt distinct from acceptance; accepted outputs present before dependency release; same-owner authorized continuation, bounded supervision and honest stops; parent-primary updates; managed settlement/recovery preserved | User-approved correction after Astra's D10/coordinator trace review, 5 November 2026; D10 followed its 0.1.26 local-report brief; generic managed guidance consolidated without new runtime, messaging authority or wakeup guarantee |
| [Agent Delegation: integration](../policies/03-agent-delegation.md#review-integration-and-recovery) | Fixed task targets, read-only independent review before the next wave, bounded rounds, verified findings, reassessed dependencies, escalation and stopped-worker recovery | Orchestration review/recovery guidance and [parallel execution rule][parallel] |
| [D6 native implementation plan](d6-native-delegation-plan.md) | Useful cooperative flow; suitable worker selection and existing guidance; bounded briefs, once-only dispatch, shared ownership, current results, checks and independent review; retained uncertainty; two narrow runtime corrections with affected offline checks and one held surface-specific pilot; historical state and stronger selected guarantees preserved | Agent Delegation and Context/Handoff; user-approved cooperative simplification and whole-plan review with Astra on 4 November 2026; package implementation and native qualification remain distinct |
| [Native delegation](../clients/delegation.md) | Ordinary delegation distinguished from selected managed controls; compatible guidance and actual capabilities; exact claims/dependencies/budgets and once-only launch; requested/configured/observed settings; cooperative completion scoped to coordinator submissions and inspected activity; historical proof semantics preserved; strict stop proof only for its actual consumers | Agent Delegation; version-3 cooperative D6 contract and retained historical proofs; no proof reinterpretation, automatic takeover or implied native qualification |
| [Workstreams controls and routing](../skills/workstreams/SKILL.md), [advanced management controls](../skills/workstreams/references/advanced-controls.md), [execution](../clients/workstreams.md) | Separate feature preferences and revisions; defaults only for new supported scopes with no saved choices; read-only preference status; ordinary delegation and shared safety in all four settings combinations; selective loading for heavy resource phases and parent recovery; no status-side worker/schedule creation | Approved Workstreams separation design; Batch 1 shared settings/delegation handoff; exact operational command examples remain tied to the final CLI implementation |
| [Resource admission](../clients/workstreams-resources.md) | Current resource revision gates heavy coordinator, worker and reviewer phases; common task/dependency/ownership checks, host reservations, bounded rechecks, fresh disk checks, degraded telemetry and safe cleanup remain independent of recovery | Approved Workstreams separation design; Batch 2A resource implementation handoff; cooperative admission is not OS containment or native qualification |
| [Capacity recovery](../clients/workstreams-recovery.md) | Recovery revision gates registration independently of resource leases; preflight precedes preference mutation; original records and consumed delivery state remain; acknowledged preference changes reconcile registrations and the existing shared schedule; unresolved recovery alone does not block ordinary/resource work | Approved Workstreams separation design; Batch 2B recovery implementation handoff; schedule state requires existing native observation and acknowledged lifecycle recording |
| [Shared startup](../clients/startup.md) and [Codex shared task runtime](../clients/runtime.md) | Canonical Project authority schema 2 and fresh SQLite runtime schema 3; explicit accepted membership for ancestor authority; runtime-enabled legacy records do not establish current task authority; old M/E packages, readers and pins stay at original paths; runtime-free startup-only behavior and explicit historical inspection remain distinct; E lowercase schema-3 history stays with its original reader; file configuration separate from runtime birth; owned D4 birth before canonical task start; schema namespaces separate from proof/manifest protocols | Project Scope, Context/Handoff and Workflow Maintenance; accepted D4/D6 ownership, fresh canonical composition and LD-C1 legacy ruling |
| [D7 repository gate](d7-repository-gate-plan.md) | Trusted publisher/evidence/verifier, candidate/base/policy freshness, explicit successful prerequisites, actual rejection and legitimate acceptance across claimed merge paths, scoped bypass/queue evidence and separately authorized live adoption | Merge Readiness contract; user-approved real merge assurance; official GitHub documentation reviewed for the plan; production publishing/enforcement remain unqualified |
| [Repository merge assurance](../clients/repository-gate.md) | Selective merge-assurance loading; bounded fixture input and modeled consistency checks; always-exit-1 CLI, unauthenticated supplied evidence and no production merge/publish authority | Reviewed G1 fixture inspector; modeled fixture validation is separate from trustworthy evidence sources, publishing and observed repository enforcement |
| [D9a behavioral qualification](onboard-my-project-plan.md#d9-qualification-usage-and-pilot) | Seven native cases, protected evaluator, deliberately broken workflow negative control and corrected behavior, one-change baseline/candidate comparison, capability/surface/version/revision-scoped evidence | Harness guide, Prompts 14/15; Workflow Maintenance contract; user-approved qualification acceptance |
| [D9b native usage](onboard-my-project-plan.md#d9-qualification-usage-and-pilot) | Authoritative coordinator/worker/attempt observations through verified acceptance, stable identities, cumulative/incremental and inclusive/subset semantics, idempotency, explicit unknown/incomplete coverage | User-approved reconciliation acceptance; Toolkit's observed-usage discipline; optional existing measurement formats require collector qualification |
| [Project/Repository planning extension](onboard-my-project-plan.md#project-and-repository-model) | Canonical membership/identity, bounded coordinator adoption outside read-only startup, explicit shared-guide/hook/path ownership, lint owner and concrete D3/D4/startup/D5/D6/D7/D10/D11 instruction seams | User-approved design; PHIOON guide/common-Git patterns below are implementation references. Future policies 01/03/05/06/10, startup/runtime/evidence/delegation client procedures and onboarding skill procedures require selective-loading/behavioral acceptance; not implemented by this plan |
| [Linked-task and recovery planning](onboard-my-project-plan.md#cross-repository-tasks-and-delivery), [membership recovery](onboard-my-project-plan.md#membership-updates-and-recovery) | Existing repository tasks and shared acceptance, complete required companions, implementation versus delivery, one editable software repository per worker with covered shared effects, per-target merge authority and dependency-aware shared-policy rollback | User-approved extension; installed `vibe-setup` task lifecycle/update patterns are references only. D5/D6/D7 and D4 own future implementation; no group registry, transferred permission or new orchestration authority |
| [Retention planning](onboard-my-project-plan.md#evidence-retention), [D10 review](d10-project-review-plan.md), [last D11 diagnostics](d11-workflow-diagnostics-plan.md) | Temporary unenrolled review with stable private target-to-owner accounting; existing-evidence diagnostics; four-month compact/seven-day detailed/one 100 MiB Project limits with protected companion proof; retirement of settled task/assignment history beyond shared-state-authority-wide 4096 bounds, preserving replay/dependency integrity and blocking dependent admission when all remain needed | User-approved design and retention limits. Existing D2/D5/D6 lifecycle and reporting/review owners retain implementation/qualification; retired identity is not passing evidence, no authority switch evades uncertainty, and package checks do not qualify native or repository behavior |
| [Lean data authority and compatibility contract](lean-data-contract.md) | One authority per fact and actual consumer; immutable detail versus derived indexes; direct dependency edges with per-root purpose; guarded-stage reuse/freshness boundaries; recoverable publication/deletion; original readers for pinned/live/uncertain records and forward-only new records; exact approved retention limits with implementation/qualification gaps explicit | Approved lean-data design and review matrix; authoring-only contract, not a runtime loading dependency or proof of implementation/qualification |
| [Security: target and limits](../policies/04-security-review.md#establish-the-target-and-limits) | Inspection authority, diff and dirty layers, baseline scope, excluded paths, safe isolated commands, execution settings and disclosure limits | Prompt 13 scope, target and authority |
| [Security: boundaries](../policies/04-security-review.md#review-relevant-boundaries) | Tool/manual capability, threat model, relevant vulnerability classes, counterevidence, safe reproduction and versioned references | Prompt 13 capabilities and review procedure |
| [Security: evidence](../policies/04-security-review.md#evidence-and-completion) | Severity/confidence/provenance, redacted private reports, repository integrity, honest limits, risk-scaled independence and relevant rechecks | Prompt 13 reporting/completion; Prompt 12 section 10 |
| [Project Scope: authority](../policies/01-project-scope.md#authority-and-activation) | Actual target and instructions, preserved permissions/customizations, scoped decisions; ordinary work without task-state/activation prerequisites; selected capabilities blocked only on their own missing evidence | Prompt 12 sections 1–5; AGENTS working agreements/continuity; minimal-default decision |
| [Project Scope: conflicts](../policies/01-project-scope.md#when-instructions-conflict) | Material choices, preservation, prior authorization, assumptions versus decisions, and dependent blockers | Prompt 12 sections 2–5; AGENTS working agreements/continuity |
| [Task Planning: contract and procedure](../policies/02-task-planning.md#planning-and-verification) | Outcome/boundaries/constraints, observable acceptance, proportional plans, conditional confirmation, actual per-step checks, regression proof and verified resumption | Prompt 04; Prompt 12 durable agreements; AGENTS working agreements/continuity |
| [Tests and Builds: executable proof](../policies/05-tests-and-builds.md#select-and-run-proof) | Actual commands, behavior/regression proof, isolated safe checks, baseline failures and honest distinctions between kinds of evidence | Prompt 12 sections 5, 9, 11; AGENTS working agreements |
| [Tests and Builds: quality and freshness](../policies/05-tests-and-builds.md#preserve-quality-and-evidence) | Native tooling/self-check, compatible typed lint, measured runner cost, adapted size limits, legacy warning adoption, preserved gates and current-content evidence | Prompt 12 section 8; accepted evidence design |
| [Code Review: coverage and findings](../policies/06-code-review.md#choose-coverage) | Complementary risk-based scopes, exact target, read-only review, qualified independence, substantiated/deduplicated findings, dispositions and bounded rechecks | Prompt 12 section 10; AGENTS delegation/review; accepted merge-review baseline |
| [Codex task checks and review evidence](../clients/evidence.md) | Selective runtime trigger; model-free accepted check claims/replay and version-aware reconciliation; one linked D5/D6 review admission with separate plaintext/artifact digests; latest-result quality correlation distinct from lifecycle/ownership; retained historical findings; current completion evidence, atomic verifier barrier/readiness invalidation and safe retirement | Policies 04–06; accepted D5/D6 implementation; historical D5 qualification does not qualify schema-2 native lifecycle |
| [Context and Handoff: records](../policies/07-context-and-handoff.md#canonical-documents-and-selective-retrieval) | Canonical document mapping, client-specific loading, selective memory, source/verification/invalidation and privacy | Prompt 12 sections 5, 7; AGENTS documentation/memory |
| [Context and Handoff: continuation](../policies/07-context-and-handoff.md#handoff-and-resumption) | Temporary handoff, actual-state reconciliation, uncertain worker ownership, explicit pauses and authorized remaining work | Prompt 12 durable agreements; AGENTS execution continuity |
| [Merge Readiness](../policies/08-merge-readiness.md) | Current candidate/base/queue and applicable evidence; trusted verifier, repository rules/bypasses and scoped authority when managed enforcement is selected; preserve existing repository protections | User-approved merge-enforcement extension; minimal-default decision; original design's repository gate constraints |
| [Workflow Maintenance: qualification](../policies/09-workflow-maintenance.md#requalify-affected-behavior) | Fresh-context/client distinctions, seven scoped cases, private isolated fixtures, independent evaluators, unavailable observations and safe reapplication | Prompt 12 section 9; evaluation guide and Prompt 14 |
| [Workflow Maintenance: improvement](../policies/09-workflow-maintenance.md#one-bounded-improvement) | Observed failure, comparable baseline/candidate, fixed evaluators/inputs/settings, one-candidate default, measured outcomes and authorized adoption | Prompt 15 and method guide native procedure |
| [Workflow Maintenance: updates/labs](../policies/09-workflow-maintenance.md#updates-and-optional-external-labs) | Active-task versions, stopped writers, compatibility/recovery, external lab isolation, credential-free demo and separately authorized model/benchmark budgets | Prompt 15 and method guide conditional lab procedure; accepted runtime design |
| [Project Onboarding](../policies/10-project-onboarding.md) | Basic setup, complete Toolkit component applicability assessment, relevant inspection, recoverable authorized changes, useful project guidance/commands/checks, honest limits; advanced capabilities only when selected | Overview; complete tool catalog; Prompt 12; minimal-default decision |
| [Managed plan](../skills/onboard-my-project/references/managed-plan.md) | Pinned complete source/catalog inputs, bounded inventory and proposal schemas, exact effect binding, privacy, resume/review and dependency-scoped blockers for the selected managed dispatcher | Prompt 12; complete tool catalog; approved D4 plan/runtime contract; Toolkit setup, review and security sources |
| [onboard-my-project apply and recovery](../skills/onboard-my-project/references/apply-and-recovery.md) | Trusted authority distinct from JSON binding; original provenance; effect-scoped approval; file configuration and undo independent of runtime birth/source availability/executing-package equality/unrelated history; safe paths, backups, live-writer exclusion, atomic replacement, recovery and preservation of later edits | Project Onboarding; Project Scope; D4 approved plan and user-approved 2 November 2026 simplification; Toolkit setup, review and security sources |
| [Qualification and activation](../skills/onboard-my-project/references/qualification.md) | Selected managed activation or maintainer adapter qualification; actual frontend/source/configuration observations, bounded scope, writer reconciliation, native evidence limits and no implied task/merge/external authority. Disposable D4 source capture can bind and verify an inherited-hook disable-only map across both native launches. | Prompt 14 and evaluation guide; user-approved activation, staged qualification and Mac pilot hook isolation |
| [Basic onboarding procedure](../skills/onboard-my-project/references/procedure.md) | Relevant instructions/configuration; complete Toolkit component assessment for relevance; authorized recoverable changes; useful guidance/working commands/relevant checks/limits/undo; selective loading of managed and advanced references | Toolkit overview/catalog and Prompt 12; minimal-default decision |
| [Tool Integrations](../policies/11-tool-integrations.md) | Required versus optional capability, reuse, scoped configuration/authentication, current client formats, distinct discovery/trust/execution, safe probes and invalidation | Catalog scope matrix; Prompt 12 sections 2, 7, 9 |
| [Native onboarding question boxes](../skills/onboard-my-project/references/procedure.md#basic-onboarding) | Publication, merge and Workstreams questions; single-select choices, preserved labels and pending decisions | User decision after the onboarding dialogue pilot |
| [onboard-my-project procedure](../skills/onboard-my-project/references/procedure.md) | Resolve one source revision for a new run; load Prompt 12's required source bundle including explicit guides 17–23; enumerate named catalog components across sources, apply stage-specific mandate/applicability, group only explicit members with shared disposition, preserve immutable source declarations and partial assessment checkpoints; retain pinned/offline resume semantics; distinguish planning from application and qualification | Prompt 12; overview; complete tool catalog; project defaults and document manifest; required guides 17–23 |

The local contract tables also retain their triggers, onboarding decisions, evidence and blockers. Model catalogs, installation examples and external skill internals are not runtime requirements. Clients discover actual controls; setup handles integrations. A worker retains the task's applicable development/check discipline without importing another skill as a universal dependency.

## User-approved extensions and boundaries

- On 2 November 2026, the user approved the first simplification round. Configured
  enrollment does not activate runtime guards; managed operations still require
  qualified authority. Recovery considers changed effects and relevant current or
  historical writers. The user's pre-merge decision retains strict policy/runtime
  matching and original pins; automatic cross-version compatibility is deferred.
- The same approval moves routine execution choice into the policy index and the
  test-first/self-review procedure into Task Planning. Detailed delegation loads
  only for its triggers, including failure/escalation. Public reconciliation,
  guidance and resource schemas replace routine implementation lookups. Required
  review, security and full setup catalog coverage remain applicable. Repository
  merge enforcement is selected separately, while existing protections remain
  binding. These were routing and bookkeeping changes, not new exemptions.
- Executor-confirmed nonlaunch needs no independent stop claim. Started processes
  still need descendant reconciliation. Independent phase stopping can release
  that phase's capacity without ending its assignment. Retention runs at plan or
  explicit maintenance boundaries rather than on every artifact publication;
  existing retention periods, caps and protected evidence remain unchanged.

- Explicit project/client enrollment, silent activation, conflict-resolution modes and applicable policy adoption are plugin decisions. The approved [Project/Repository design](onboard-my-project-plan.md#project-and-repository-model), linked delivery, recovery and retention extend that scope; their concrete owner seams remain future implementation/qualification, without changing current D6 package or live instruction claims.
- Resource reservations, observed settings and trusted reviewer provenance supplement Toolkit guidance.
- On 30 October 2026, the user approved [capability-based client compatibility](../policies/11-tool-integrations.md#qualify-the-actual-workflow), including fresh native dispatch. [Native delegation](../clients/delegation.md) compares reobserved capabilities, surface, provider, parent and dispatch controls rather than release strings. New bindings record the actual reader; submitted observations, historical receipts, guidance snapshots and exact refresh claims retain their original provenance. Same-surface guidance reuse depends on compatible capabilities. This changes eligibility checks, not native qualification, source identity, schema validation or security-sensitive dependency pins.
- On 26 October 2026, the user simplified model authorization: native agents/models are automatically approved across all current and future platforms/providers; any non-native model or execution route requires explicit approval. This replaces the earlier subscription-only restriction and the local-only exception. Local models additionally require assessment. Existing account access, task constraints, budgets and supported dispatch still apply; this decision changes neither coordinator settings nor adapter support.
- Optional OpenRouter helpers have separate saved controls and software coverage; execution qualification remains separate. The local-model assessment workflow remains deferred.
- The user clarified on 26 October 2026 that only the coordinator's model/reasoning must remain their choice. Delegated workers use task-based model/effort selection from each platform's current official guidance. The earlier interpretation requiring defaults for every worker was incorrect. Store guidance locally and refresh it instead of fetching it for every task; preserve active assignment evidence. This applies to repository development as well as the plugin's planned workflow.
- Security review runs after implementation, includes relevant delivery working-tree layers and blocks completion/merge for confirmed high/critical findings. Medium/low findings require resolution or authorized disposition. A repository baseline is performed when requested; no separate planning-time security gate is added.
- General review before every merge and any explicitly selected trusted repository-controlled gate remain requirements. Ordinary Toolkit setup excludes branch/worktree requirements and commit/push protections; those controls are not inherited installation permissions.
- D5 local evidence can establish current task completion and local merge evidence only. Native observation establishes execution provenance, not review quality; coordinator assessment remains necessary. Local readiness does not establish D7 repository enforcement or grant merge authority.
- D6 targets native execution.
- Behavioral qualification and native usage reporting are selected D9 subdeliverables, not ordinary onboarding requirements; real merge assurance remains D7 when selected. D6 owns worker controls. Evidence may be reused across them without duplicating ownership or claiming qualification from package checks.
- The user abandoned automated project adjustments. Evaluation/reporting do not introduce self-modification, periodic analyzers or automatic changes to model/provider preferences or routing policy. Task-based worker selection stays as already approved; token savings remain unmeasured.
- Runtime contracts are locally complete. Remote Toolkit use is limited to authoring, setup and reviewed updates. This supersedes the earlier runtime instruction to follow linked upstream procedures; it does not prohibit task-specific technical research or authorized network operations.
- The user approved all eleven contracts on 25 October 2026. Approval is recorded in development documentation; runtime files carry no approval-status labels.

## Secondary evaluation references

PHIOON's umbrella `AGENTS.md` lines 143–157 and `bknd/AGENTS.md` lines 113–126 statically demonstrate explicit shared-guide pointers, precedence, preserved repository preferences and common-Git linked-worktree resolution. The user reports successful native inheritance; no fresh native probe was run for this planning update. Installed `vibe-setup` `README.md` lines 313–330 separates actual executing hook/harness source from software root, command cwd and path bases; `task-lifecycle.md` lines 192–240 links required existing task publications without a group registry; `update-contract.md` lines 75–142 supplies owned-baseline/recovery and target separation. These are implementation references, not replacement normative authority for Toolkit or permission to copy scripts/settings. That update contract infers no children: standing-scope future auto-joining is our user-approved extension, not inherited proof. Reuse applicable evidence and qualify affected new seams only.

Ruflo revision `310d78c32f28cf2c02ff9e54ffd83198485c6535` provides implementation ideas only: [provider completion remains unverified][ruflo-workflows], and [uncertain mutations require status inspection before retry][ruflo-agents]. No Ruflo runtime, billing/authentication stack, self-learning or promotional savings claims are adopted. Toolkit remains the authoring authority.

The installed Local Model Delegation `local-delegation/references/measurements.md` is a possible native-only record-format reference, not a required plugin or a qualified collector. Mixed worker bindings and actual source collection need separate D9b qualification.

## Task delivery lifecycle — 2 November 2026

The [delivery lifecycle design](superpowers/specs/2026-11-02-delivery-lifecycle-design.md) records the user's approved lifecycle direction: onboarding-selected merge policy (manual by default), explicit per-task overrides, default separate worktrees, publication and manual-merge reconciliation, remote-branch cleanup, and client-owned local retention. These remain design work, not activated runtime contracts. They extend existing D3/D4 configuration, D5/D6 task/evidence ownership, D7 delivery observation, and client adapters rather than adding another registry or cleanup service. Toolkit remains the authoring authority; branch/worktree and publication defaults are user extensions.

The user's final thread-status decision reuses canonical `WAITING` for merge waits through the existing Thread Naming owners on fetched `origin/main` at `8d21679bdc60f0f8808a156797bcedf086d11480`. No new state or display abbreviation is introduced. Existing task records retain the wait reason and resumption condition for one or multiple PRs and parent/child dependencies. Thread `READY` continues to mean ready to begin; PR `[READY]` means ready to merge. Preserve the acceptance-based meter, stable identities, manual-title protections, event-based repair, and explicit resolution of waiting guards. Delivery-required completion still needs integration with the verifier's `DONE` transition, ordinary and linked status, and retirement protection. Child completion cannot replace aggregate acceptance or integration.

The [implementation plan](superpowers/plans/2026-11-02-delivery-lifecycle.md) uses that current-main base and its existing canonical onboarding, evidence, ownership, and naming interfaces. It assigns eight testable slices, including the still-unqualified D7 production dependency, preserved local-cleanup ownership, multi-PR acceptance, and bounded manual-merge reconciliation. Context7 and official GitHub CLI/Git documentation informed expected-head merge and conditional remote-ref deletion; no new credentials, publisher, or live rules are authorized. Planning validation passed the existing 71 task-status/title/repository-gate tests and package validation. This proves the inspected baseline, not future delivery behavior; client/provider execution remains untested for this feature.

The first implementation slice uses fetched main `44de11fa14bb2dddef02a3fbabb521d9bc10ecc4`. Policy 10 and onboarding's procedure own explicit repository delivery-policy adoption; policy 02 owns task-specific scope/override decisions. `runtime/startup.py` and `runtime/state.py` bind optional policy to canonical accepted identity; `runtime/delivery.py` owns policy/plan validation and managed task-local plan/status. Existing D4 manifest/postimage machinery preserves that field; it adds no separate authority. Eleven focused tests and independent review cover this slice. The broader check has the same 4 failures/22 errors as unchanged main's onboarding tests; [validation](delivery-validation.md) records the comparison and the missing native admission prerequisite. No completion verifier, title vocabulary, publication, merge or cleanup execution is enabled by this partial implementation.

The local Vibe Setup `AGENTS.md` and `skills/vibe-setup/task-lifecycle.md` provide implementation references for draft `[BLOCKED]`, verified `[READY]`, durable publication recovery, and merge-confirmed cleanup. They are not runtime dependencies, and their prohibition on agent merge is not imported over the user's selected merge policy. Official [Codex worktree cleanup](https://learn.chatgpt.com/docs/environments/git-worktrees#worktree-cleanup) and [Claude Code cleanup](https://code.claude.com/docs/en/worktrees#clean-up-worktrees), checked on 2 November 2026, support client-specific local retention ownership. Codex documentation was also consulted through Context7; the fetched official page takes precedence over older indexed wording. These sources establish documented capabilities, not execution on this installation. The design's acceptance cases map the future instruction paths; no current policy is weakened and no production merge or cleanup qualification is claimed.

## Updating contracts

During an authorized update, resolve Toolkit once and inspect the changed requirements plus their applicable dependencies at that revision. Rewrite the affected local behavior, update this map and record material decisions separately from upstream requirements. Do not copy whole prompts or maintain a mirrored repository.

Before delivery, compare requirement coverage and run representative instruction-path cases using only the local runtime documents. Check both required actions and unnecessary loading/gates. Package checks and static simulations do not prove client execution. Preserve the accepted version for active tasks until an update is reviewed and safely applied.

[delegation]: https://github.com/soumatheusgomes/vibe-coding-toolkit/blob/790a895304e0c08b70a5b215c9d334f1c86b816c/docs/tools/02-subagent-orchestration.md
[parallel]: https://github.com/soumatheusgomes/vibe-coding-toolkit/blob/790a895304e0c08b70a5b215c9d334f1c86b816c/templates/rules/parallel-subagent-driven-development.md
[ruflo-workflows]: https://github.com/ruvnet/ruflo/blob/310d78c32f28cf2c02ff9e54ffd83198485c6535/v3/@claude-flow/cli/src/services/managed-workflows.ts
[ruflo-agents]: https://github.com/ruvnet/ruflo/blob/310d78c32f28cf2c02ff9e54ffd83198485c6535/v3/@claude-flow/cli/src/mcp-tools/managed-agent-tools.ts
[security]: https://github.com/soumatheusgomes/vibe-coding-toolkit/blob/790a895304e0c08b70a5b215c9d334f1c86b816c/docs/prompts/13-security-review.md
[setup]: https://github.com/soumatheusgomes/vibe-coding-toolkit/blob/790a895304e0c08b70a5b215c9d334f1c86b816c/docs/prompts/12-existing-project-setup.md
[planning]: https://github.com/soumatheusgomes/vibe-coding-toolkit/blob/790a895304e0c08b70a5b215c9d334f1c86b816c/docs/prompts/04-brainstorm-to-plan.md
[agents]: https://github.com/soumatheusgomes/vibe-coding-toolkit/blob/790a895304e0c08b70a5b215c9d334f1c86b816c/templates/AGENTS.md.template
[overview]: https://github.com/soumatheusgomes/vibe-coding-toolkit/blob/790a895304e0c08b70a5b215c9d334f1c86b816c/docs/00-overview.md
[catalog]: https://github.com/soumatheusgomes/vibe-coding-toolkit/blob/790a895304e0c08b70a5b215c9d334f1c86b816c/docs/tools/README.md
[evaluation-guide]: https://github.com/soumatheusgomes/vibe-coding-toolkit/blob/790a895304e0c08b70a5b215c9d334f1c86b816c/docs/07-harness-evaluation.md
[evaluation]: https://github.com/soumatheusgomes/vibe-coding-toolkit/blob/790a895304e0c08b70a5b215c9d334f1c86b816c/docs/prompts/14-harness-evaluation.md
[improvement]: https://github.com/soumatheusgomes/vibe-coding-toolkit/blob/790a895304e0c08b70a5b215c9d334f1c86b816c/docs/prompts/15-harness-improvement.md
[harness-method]: https://github.com/soumatheusgomes/vibe-coding-toolkit/blob/790a895304e0c08b70a5b215c9d334f1c86b816c/docs/tools/23-harness2.md


### Thread Naming main integration · 1 November 2026

The user approved five-cell leading meters, one space before globally reserved
family codes, stable parent/child labels and one exact task-status vocabulary.
`WORKING` describes ongoing effort; evidence-gated `DONE` keeps the full meter.
Accepted weighted milestones establish progress. Normal task handling records
phase changes, and deterministic code repairs adopted titles at workflow events
without another model turn or idle watcher.

Topic-only manual edits are adopted; other manual edits preserve the title and
pause only synchronization, with one notice and explicit resumption. Display
changes grant no task, native or permission authority. The
[naming contract](../clients/workstreams.md) and
[title procedure](../clients/workstreams-titles.md) own the operational rules.

For the requested main merge, the user explicitly selected Thread Naming and its
essential dependencies. Main's schema-2 runtime is retained. The 88 unmerged
ancestor commits, canonical schema 3, other development features and efficiency
candidates are excluded. Historical records remain read-only under new pins;
original-runtime reconciliation and the existing approved fresh-authority update
remain required. Source validation and actual native hook observation are
reported separately.

### Automatic native delegation naming · 3 November 2026

The user requested automatic Thread naming for native delegation regardless of
Workstreams enablement, preserving manual-title overrides and native identities.
[Agent Delegation](../policies/03-agent-delegation.md) and the
[native contract](../clients/delegation.md) now load the existing
[naming rules](../clients/workstreams-naming.md) for ordinary workers and
independent reviewers. The [title procedure](../clients/workstreams-titles.md)
separates existing runtime-bound synchronization from ordinary supported display
controls without requiring runtime activation. Both preserve manual pauses and
immutable creation markers. This instruction change neither enables Workstreams
features nor qualifies live title delivery.

### Implementation Thread Naming and status placement · 3 November 2026

The user clarified that automatic Thread Naming serves all implementation chats,
including solo work, while question-only, exploratory and planning chats remain
untouched even with native delegation. They selected Option 2: five meter cells,
one space, the exact uppercase status, then family/member and stable topic.
This supersedes the delegation-only activation above; it is a user-authorized
presentation decision, not an added upstream Toolkit requirement.

[Task Planning](../policies/02-task-planning.md) routes the implementation boundary
to the canonical [naming contract](../clients/workstreams-naming.md).
[Agent Delegation](../policies/03-agent-delegation.md) and the
[native contract](../clients/delegation.md) apply that eligibility to supporting
children. The [title procedure](../clients/workstreams-titles.md) preserves manual
ownership, topic edits, explicit resumption, pending delivery and native identity
for both runtime-bound and ordinary native work. Solo work acquires no managed
task prerequisite. Original runtime readers and active pins remain intact;
recognizing saved status-last presentation permits reconciliation during an
authorized update, not a bulk rename or silent runtime migration.

The sidebar width report on 4 November 2026 showed that the earlier empty meter
used more space than the full meter. The user selected one leading `U+FFFB`
before the five original cells after verifying tight spacing at 0%, 20% and
100% in the Codex sidebar on macOS. The [naming contract](../clients/workstreams-naming.md)
specifies that prefix at every fill level. Titles without it remain readable
for reconciliation. The browser fixture rendered the original Menlo shapes at
each level. CoreText did not apply that font fallback to the empty meter.

The user requested one filled cell for `WORKING` when there are no measurable
milestones. The [naming contract](../clients/workstreams-naming.md#status-and-accepted-progress)
defines this as a display indicator. Measurable plans still use accepted
milestone weights.

The user requested labels only when their action is needed. The [naming contract](../clients/workstreams-naming.md#status-and-accepted-progress)
limits labels to actionable `REVIEW`, `WAITING` and `BLOCKED`. The [title procedure](../clients/workstreams-titles.md#record-status-and-accepted-progress)
connects the next action to the ordinary and runtime-bound title paths.

Instruction-path review covers solo implementation, questions, planning with and
without workers, implementation review, follow-up questions after completion and
manual overrides. Package checks, instruction observations and installed native
execution remain separate evidence; this source map claims no live delivery.

### Thread Naming reopening · 4 November 2026

The user approved [reopening an adopted title before resumed work](superpowers/specs/2026-11-04-thread-naming-reopening-design.md).
Clear new objectives switch automatically, keep the family and member, and
start with zero accepted milestones. Only a workflow-owned topic can change
automatically. The [Codex client contract](../clients/codex.md) routes this case
to the [title procedure](../clients/workstreams-titles.md#reopen-a-completed-objective).
The decision extends the user's presentation rules and uses the existing task
and title controls.

### Initial title baseline · 3 November 2026

The user proposed retaining the feature's last title and comparing the current
title to detect external changes. Existing applied/pending reconciliation already
provides that baseline; no registry or receipt mechanism is added. A fresh
Desktop implementation check exposed the initial-adoption gap: the native title
had no exposed actor provenance, and the unknown-ownership guard prevented the
first managed title.

The [title procedure](../clients/workstreams-titles.md) now distinguishes verified
fresh, first-turn implementation from historical, forked or continued contexts.
The accepted naming policy authorizes initial adoption in that fresh case while
known manual titles, explicit instructions and pauses still win. Permission is
not proof of title origin. Initial observation, a final pre-write comparison and
successful write/readback establish the managed baseline; uncertain writes retain
pending delivery instead of adopting each observed value anew. Later unexplained
changes are preserved under the existing topic-edit and pause/resume rules,
without attributing the change to an actor. Native identity and model controls,
question/planning exclusions and runtime-free operation remain unchanged.

The client cannot distinguish a manual rename before the first observation from
the initial title, and no verified compare-and-set closes the final native write
race. These limits remain explicit; instruction changes and source checks do not
establish fresh Desktop execution.

The planning-transition fix extends that initial adoption permission to the
first authorized implementation action after planning. Existing title
ownership and pending delivery take precedence. The same limits on title
provenance and concurrent edits apply.

## Workstreams main-runtime port · 1 November 2026

The user selected Workstreams and its essential dependencies instead of importing the 88-commit development ancestry. At that historical port, the implementation used main's schema-2 task, evidence and delegation authority; it preserved independently merged Naming/status rules and omitted canonical3 lifecycle/accounting, OpenRouter and observability extensions. The later fresh canonical composition is a separate development step and does not rewrite that history or existing authorities.

The [unified skill](../skills/workstreams/SKILL.md) routes naming, feature controls, ordinary delegation, resource work and recovery. [Advanced feature controls](../skills/workstreams/references/advanced-controls.md) maintain independent revisions, preserve saved feature or legacy combined choices, default both features only for new supported scopes without a saved choice, and keep status read-only. Ordinary delegation/shared safety stays available in every preference combination. [Dispatch](../clients/workstreams-protocol.md) preserves native identities and one-use delivery evidence; continuation limits and ownership remain in base delegation.

[Resources](../clients/workstreams-resources.md) gate heavy coordinator, worker and reviewer phases on the current resource revision; they admit cooperatively, share host reservations and memory-only telemetry, retain fresh disk checks, and permit telemetry failure only through remaining gates. Recovery has a separate revision and does not require a resource lease. Task-derived job counts replace the arbitrary 32-job cap; input bounds, ownership, finite budgets, freshness and physical scopes remain. [Parent recovery](../clients/workstreams-recovery.md) retains one temporary shared monitor while parents are eligible, deletes it after final departure, uses one-minute opportunities within the original 60-minute episode, and keeps human handling for pauses, limits and shutdown. Recovery uncertainty alone does not block ordinary or resource work. Parent and child retry rules remain distinct.

[Operation schemas](../clients/delegation-requests.md) replace routine implementation discovery, retaining narrow authoritative lookups for incomplete public stop-proof and cache-publication schemas. Evidence guides preserve every original main request paragraph exactly once; [completion](../clients/evidence-completion.md) and [retirement](../clients/evidence-retirement.md) retain their separate triggers. [Naming](../clients/workstreams-naming.md) moves main's existing rules exactly apart from heading depth; [title synchronization](../clients/workstreams-titles.md) retains manual-edit and event-repair behavior.

The [port boundary](workstreams-main-port.md) separates source validation from exact-package native observation. No old authority, pin, claim, attempt or budget is rewritten. No source test or earlier package observation establishes live compatibility for this candidate.

## Fresh canonical composition · 1 November 2026

The accepted LD-C1 ruling composes canonical Project authority schema 2 with a fresh SQLite runtime schema 3 while preserving old M/E packages, readers and pins at their original paths. File configuration completes without runtime birth. Explicit managed-runtime preparation owns database creation and baseline receipts; task start rejects an absent canonical database. Proof, manifest and transaction versions remain independent. The source contract is under composition, so software behavior and native qualification remain conditional until core validation and actual client observation are reported. Existing M task vocabulary, open-DONE reopening, Workstreams defaults/preferences and public controls remain in scope. Runtime-enabled legacy enrollment does not provide current task authority; runtime-free legacy startup remains orientation-only; supported M historical inspection remains read-only and E lowercase schema-3 history stays with its original E reader.

## Onboarding authorization and presentation · 2 November 2026

The user explicitly approved full onboarding as authority for routine setup, required checks and activation once those checks pass, with questions only for unresolved material exceptions. They requested human-readable Reviewed Configuration output, preservation of checks and recovery, and no change to the reverted pilot. This is a user-authorized workflow decision, not a new upstream Toolkit requirement.

[Onboarding authority](../policies/10-project-onboarding.md#onboarding-authority) owns setup scope and authority. The [entry point](../skills/onboard-my-project/SKILL.md) and [basic procedure](../skills/onboard-my-project/references/procedure.md) define usable, recoverable default onboarding. [Managed planning](../skills/onboard-my-project/references/managed-plan.md) and [apply/recovery](../skills/onboard-my-project/references/apply-and-recovery.md) bind selected managed operations. [Qualification](../skills/onboard-my-project/references/qualification.md) applies only to selected activation or maintainer evaluation. [Reviewed Configuration](../skills/onboard-my-project/references/reviewed-configuration.md) owns presentation only; it creates no consent, receipt or runtime readiness.

The coordinated baseline is main `a0f1767`, including file-only configuration/undo and configured ordinary-entry handling. Those behaviors are reused, not reimplemented here. This instruction/output slice does not claim that a production qualification reader exists or that the broader activation design has shipped. Source checks, behavioral instruction observations and native runtime qualification remain separate evidence.

Delivery source progress after onboarding rollback: feature-only changes were replayed onto published main `9c364e5d9bd80ec23aa677b8b237f4bd00a7d718`, preserving current composition, configuration and pinning rules. [Delivery controls](../clients/delivery.md) own workspace binding, scope revision, existing-PR observation and bounded pending inspection; evidence completion/retirement and Thread Naming load them only when applicable. Completion and retention consume managed delivery scope without adding a status or local cleanup owner. [Execution evidence](delivery-validation.md) records tested behavior and incomplete orchestration separately from production qualification.

Provider authoring follows the [GitHub REST version contract](https://docs.github.com/en/rest/about-the-rest-api/api-versions), [conditional request limits](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api#use-conditional-requests), [synchronous merge API](https://docs.github.com/en/rest/pulls/pulls?apiVersion=2022-11-28), [GitHub CLI queue behavior](https://cli.github.com/manual/gh_pr_merge) and [Git explicit expected-ref leases](https://git-scm.com/docs/git-push). Existing PR title/description cannot be conditionally replaced safely, so checkpoints use labels and append-only comments. Exact-head merge alone does not bind a concurrently changed target; the separate target-binding source remains unavailable. Real disposable Git tests prove lease behavior, not provider ownership, permissions or live transport qualification. These sources inform local contracts; routine execution does not fetch upstream documentation or infer authority from references.

## Optional observability source port

The user approved optional Langfuse integration, metrics-only exports, direct Langfuse dashboards, default-off selective task collection and a separate provider-neutral Manage Observability Integration skill. Activation remains outside main onboarding; only a final optional follow-up may reference it. Disabled mode collects no optional measurements while required local evidence remains under its existing owner. The approved metrics-only remote-retention exception needs no plugin deletion backend.

The selective source composition adapts connection controls and fixtures to current canonical acquisition, consent bindings and task states; it does not establish actual native activation, collection or complete source-format qualification. See [D9's source and port boundaries](d9-observability-preparation.md), [D9's collection/privacy/retention requirements](onboard-my-project-plan.md#optional-langfuse-integration), the [skill procedure](../skills/manage-observability/references/procedure.md), [Langfuse destination and credential rules](../skills/manage-observability/references/langfuse.md), and [D11's dashboard acceptance](d11-workflow-diagnostics-plan.md). These local contracts preserve destination sharing confirmation, shared credential impact, explicit OS-store/environment choice, no secret-store inventory, no content exports and unknown source coverage. The seven reviewed source commits ending `83869be` remain preserved; this port changes no upstream Toolkit requirement, native permission or coordinator authority.

## Workflow efficiency extension

The user-approved [efficiency design](superpowers/specs/2026-11-01-workflow-efficiency-design.md) extends existing mechanisms. Policy 03 owns event/bounded waiting and compact-before-detail retrieval; policy 07 owns durable invocation repair in its responsible record. Existing policies 05, 09 and 11 retain evidence freshness, maintenance and capability qualification. The [native status reference](../clients/delegation-requests.md#status) and [worker procedure](../skills/openrouter-workers/references/procedure.md) own exact compact commands and detail-loading triggers. No new Toolkit requirement, external execution route, startup probe or general fact/failure cache is introduced.

`tests/test_compact_status.py` covers bounded rows with complete counts, unsettled ownership, privacy, malformed worker facts, result-byte integrity and CLI routing. Its managed-title regression keeps compact status observational while ordinary status still revalidates stale completion. Existing cache/prerequisite and freshness tests remain authoritative. Package/synthetic checks are separate from live client qualification and measured savings; changed pinned content uses existing authorized update/recovery without silent active-task repinning.

## Codex instruction retrieval

The prepared Vibe loading slice preserves the canonical contracts and their existing triggers. `scripts/read_instructions.py --prepare` records exact contiguous line ranges and source digests in `clients/instruction-map.json` during authoring; package validation compares it with current sources. Routine reads verify only selected sources and deliver bounded, ordered pages without copying policy text into another maintained contract. Changed sources or selection revisions invalidate continuation. The Codex startup command supplies the bootstrap before task work, including independent review, while accepted project configuration, directory instructions, external skills, selected runtime controls and qualification keep their existing authority. The reader does not establish that earlier pages remain in context or enforce arbitrary native reads/outer batching; complete required text, selective recovery and freshness checks remain binding. RTK and other plugin sources are untouched. See the [prepared loading design](superpowers/specs/2026-11-03-prepared-instruction-loading-design.md).

The [bounded-batch extension](superpowers/specs/2026-11-03-bounded-instruction-batches-design.md) reuses the same reader, selected-source validation, page numbering and revision. Opt-in `--batch` combines complete consecutive pages up to 24,000 rendered UTF-8 bytes, including metadata and continuation; the unchanged single-page route remains available for recovery. The Codex loading route selects batches after native child and outer delivery qualification. This byte bound does not guarantee that every client token cap will fit: complete delivery checks and selective recovery still apply. Tests reconstruct exact selected sources, reject stale continuations and invalid later sources without partial output, and verify metadata-inclusive bounds. Package validation and observed native execution remain separate.

The [external-instruction extension](superpowers/specs/2026-11-03-external-instruction-loading-design.md) addresses a native Graphify read whose child output clipped even though the existing advisory reached model context. The same reader's explicit `--external` mode derives bounded sections and a revision from selected absolute local Markdown files outside the package; it writes no external manifest or source. Resolved targets and exact bytes bind continuation. Packaged manifest/path checks, source authority, applicability, existing limits, complete-delivery checks and native fallback remain binding. The Codex contract routes applicable external skills and references through this mode without changing them. No hook, dependency, cache, discovery scan or successful-path sizing probe is added. Routing is cooperative; observed native execution must establish completeness separately from package checks.

The [automatic-draining extension](superpowers/specs/2026-11-03-automatic-instruction-draining-design.md) moves continuation decisions into one awaited native call. Its [packaged runner](superpowers/specs/2026-11-03-packaged-instruction-runner-design.md) replaces repeated generated loader code with a short Codex launcher that validates one fixed helper envelope per selection. The runner validates instruction envelopes, preserves the original selection/revision, revalidates sources through each existing reader call, and uses separate notifications with bounded single-page recovery without replaying emitted ranges. Exact source text, 8,000/9,500/24,000-byte bounds, external authority and package/path checks are unchanged. The user explicitly approved the extra helper command for fitting selections. Failure, cancellation and unfinished calls cannot produce a completion receipt; a running native cell must finish before task work proceeds. No hook, server, persistent cache, model change or other-plugin edit is added. Initial route adoption, notification capacity and full behavioral compliance remain separately qualified; local tests do not establish native delivery or savings.

The [scope-routing extension](superpowers/specs/2026-11-03-instruction-scope-routing-design.md) makes the same runner use `--auto` for explicitly selected mixed sources. The existing reader determines authority per path, preserves package alias keys and rejects package-origin escapes before reading external bytes. One revision binds mode, ordered original paths, authority, prepared keys, resolved targets and validated content/ranges; every continuation rechecks them. Legacy explicit modes remain available. This removes the model's global scope choice without another tool call, resolver or cache. Exceptional native recovery remains available after reader or unrecovered transport failure, with one body per notification or outer call; direct recovery uses a 10,000-token child and 12,000-token outer budget. No new recovery API bypasses prepared hashes. Arbitrary model-authored fallback remains cooperative, and source delivery does not prove semantic compliance.

[Codex routine instructions](../clients/codex.md#instruction-output-budgets) own complete child reads, aggregate outer budgets and selective recovery at the layer that omitted required content. Native observation showed child truncation even when the outer response fit: a 7,298-token policy read capped at 7,000 and a 14,178-token guide/reference read capped at 5,500. The correction uses existing supported budgets and contiguous sections, checks both results and preserves already received text. It adds no per-read sizing probe, reader, cache, permission, or tool interception layer. Existing runtime output limits remain intact. Shared policy applicability, full required procedures, freshness, independent review and recovery requirements remain authoritative; instruction changes require observed behavioral validation separately from package checks.

The former Codex startup-implementation section is retained verbatim in [its triggered reference](../clients/codex-startup-implementation.md). Routine startup, implementation, review and resume use the client entry and triggered contracts. Startup implementation, enrollment preparation, adapter qualification and registration/trust/packaging changes additionally load that reference and its shared startup contract. Package proof, actual client observations and version-specific qualification limits remain separate.

A subsequent instruction-only trial still omitted host-skill text despite receiving the full budgeting rule first. A bounded Codex 0.165.0 diagnostic established that existing `PostToolUse` callbacks receive child warning text and can deliver `additionalContext`; outer clipping occurred after that boundary. The [Codex adapter](../runtime/adapters/codex.py) therefore adds a constant advisory for the observed anchored `Bash` response prefix after the [dispatcher](../scripts/workflow.py) resolves an enrolled scope. It preserves existing feedback and successful-path silence, creates no state or subprocess, and performs no automatic recovery. The string is not authenticated clipping metadata: stdout can imitate it. Tests cover scope, malformed and ordinary results, quoted and spoofed markers, privacy and existing feedback. This is neither a completion gate nor universal truncation protection; behavioral recovery and incremental costs require separate observation.

### PR merge gate for `DONE` · 8 November 2026

The user decided that a thread is `DONE` only after every PR it opened or took over is observed merged on the remote, with an explicit no-PR decision as the exemption. The [naming contract](../clients/workstreams-naming.md#status-and-accepted-progress) owns the rule once; the [title procedure](../clients/workstreams-titles.md#record-status-and-accepted-progress) applies it to direct and runtime-bound threads, [completion evidence](../clients/evidence-completion.md) and [delivery completion](../clients/delivery.md#completion-and-retention) point to it and state the runtime boundary. Threads whose recorded scope excludes publication need no extra step. This supersedes the earlier statement in the [delivery design](superpowers/specs/2026-11-02-delivery-lifecycle-design.md) that work with no required publication reaches `DONE` through its ordinary gate. Runtime verification still enforces only required publications. As a deliberate carve-out, kept so the existing optional-PR completion test remains valid, a PR the plan marks optional under the user's recorded scope decision is exempt; marking a PR optional after opening it without that decision is not. Other opened PRs are withheld by the coordinator, not by runtime code. Package validation does not observe client behavior.
