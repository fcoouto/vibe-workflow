# Simpler onboarding and activation

Design and delivery boundary · 2 November 2026

## Current delivery boundary

The instruction and Reviewed Configuration changes are implemented and independently assessed on main baseline `a0f1767` in `codex/onboarding-flow`. The wider runtime sections below remain design requirements, not delivered behavior. The source-update history below describes the preserved primary feature checkout; it is not the integration base of this isolated change.

The existing production code has no complete trusted qualification issuer/reader, cheap current-control invalidation, or independently anchored inactive-history lookup. Native hook notifications offer a possible observation input; discovery and stored labels are insufficient. No runtime change, installation or pilot activation is included in this instruction merge. See the [qualification record](../../onboarding-authorization-qualification.md) for the actual checks and limits.

## Goal

One onboarding request should lead to routine setup, required verification and activation. Ask only for material exceptions. Incomplete setup must not stop ordinary development. Preserve the agreed protections of an already active workflow, exact authorization, privacy, checks and recoverability.

## Development baseline

Main at `5a6bcd2` is integrated into the existing feature branch by merge commit `0e70c6b`. Branch integrations and local edits are preserved; the remaining local edits and this design draft are outside the merge commit. The source update is separate from installing the plugin or enrolling this repository. Do not copy source changes into the installed cache or recreate the reverted pilot as part of this design.

The earlier approved authorization/presentation changes are included below. The runtime simplification is a design proposal; it is not implemented by this document.

Coordination with [Genesis Impl](codex://threads/01a172e9-9e25-7d93-b6af-ea5e4eaf2571) identified a newer shared baseline: commit `e3128c6` (PR #30) already implements file-only configuration and undo without requiring runtime birth. Genesis has integrated that change; this checkout's merge predates it. Reconcile that reviewed change before onboarding implementation rather than rebuilding it here. The coordination work does not merge another candidate, update the installed package or activate a project.

Genesis confirmed PR #31's actual main merge as `a0f1767e36d3433df5b24154eaa34bf3dd8c398f`, from reviewed candidate `fa48eba7426e1d5ddfdb90f731a79639f054cb59` and unchanged premerge base `e3128c6`. Use that exact merge as the next coordination input; this record does not merge it into this checkout or establish production activation. It retains strict policy/runtime matching: no upgrade exception, automatic repinning or compatibility declaration. Preserve the original reader for mismatched historical state. Native client version compatibility is a separate concern and cannot waive these source-identity checks.

## Confirmed problem

The installed entry resolver accepts only active or outside scopes. Every other result raises an error. The edit hook turns that error into an edit denial. Thus a valid configured-only project has enforcement consequences before its workflow activates.

A read-only comparison established the original mismatch: the project's stored runtime digest matched the original installed pilot commit, while the installed runtime differed. Its policy digest still matched. At that time the project was configured with qualification pending and no accepted active scope. This identifies the historical mismatch without treating stored labels as proof of trusted historical inactivity.

The pilot was subsequently reverted by the user. This source update does not reinstall or reactivate it.

## Decisions already made

- Full onboarding authorizes routine setup, verification and activation after required checks pass, within established scope, permissions and resource limits.
- Ask only for material exceptions. Reuse confirmed decisions.
- Reviewed Configuration is a readable explanation, not another approval gate.
- Keep internal milestone labels, hashes and coordinator mechanics in linked technical records.
- Preserve limited inspect, plan and configuration-only requests.
- Preserve the reverted pilot and its historical recovery records. Do not reenroll, activate or alter the installed package during this source/design task.
- Merge main into the existing branch; do not replace or discard branch-only features or local changes.

## Which controls this flow owns

Repeated planning, apply and activation confirmations are workflow choices. Remove them when the user's existing onboarding request covers the concrete effects. Reviewed Configuration explains those effects; it does not create a new consent requirement.

The configured-project edit denial also comes from this workflow's hook and resolver. Change that boundary only after trusted records establish that the selected workflow has never activated. Keep actual task admission separate from ordinary editing.

File integrity, ownership, current qualification, recovery and exact operation binding are internal correctness checks. Keep them automatic and place their technical details in records. A successful check is not a reason to ask the user again.

File-only configuration and undo do not need runtime creation or native qualification. Apply the existing file-transaction checks to those effects. Do not recreate a database or an onboarding assessment merely to undo files. Missing or replaced previously allocated runtime state still requires the existing writer-ownership checks; a database that was never required is a different case.

Host permissions, unavailable credentials, paid services and effects outside the request are external constraints or material exceptions. This flow cannot grant those permissions itself. Ask only when an unresolved exception blocks the next authorized action.

## Recommended user flow

1. Establish the selected project, checkout, client and existing preferences. Ask only if a missing decision materially affects the work.
2. Inspect and plan complete applicable Toolkit coverage. Reuse compatible tools and documents.
3. Present Reviewed Configuration: scope, changes and purposes, next steps, material decisions, current status, recovery and technical links.
4. Apply authorized routine setup with private recovery records. Use the existing file-only path where applicable; prepare managed runtime only when the selected effects require it.
5. Run the required checks and reviews. Continue independent authorized work while a dependency is blocked.
6. Activate the agreed workflow after its mandatory requirements qualify and activation is observed. Confirm completion once.

No separate planning approval, apply approval or activation approval is added when existing authority already covers the exact effects. Trusted host evidence still binds each concrete operation; no generated page or JSON field supplies consent.

## User-visible states

| State | Meaning and next action |
| --- | --- |
| Setup in progress | Preparation or checks remain. Ordinary work can continue when no active workflow protection applies. Name the next action. |
| Workflow active | Mandatory checks passed and activation was observed for the selected scope. Existing task controls apply. |
| Workflow needs attention | A previously active workflow cannot establish current readiness. Explain the affected protection and recovery step; do not silently remove it. |

An unresolved material exception is a named decision, not another onboarding mode. Technical states remain available in technical records. A failed check is not completed onboarding; incomplete setup is not an application-wide failure.

## Reviewed Configuration example

This is illustrative user-facing output, not a proposal to reenroll the reverted pilot. Real output must use the selected project's exact paths and link its concrete proposed contents and recovery records.

> **Reviewed Configuration — Example project**
>
> **Scope:** Set up this project checkout in Codex, preserving existing preferences.
>
> **Changes:** Add the setup guide and project registration. Store private state and recovery records locally.
>
> **Next:** Apply the configuration, run the required checks, and enable the agreed workflow when those checks pass.
>
> **Decisions needed:** None. Your onboarding request covers these changes. We will ask before any material exception.
>
> **Status:** Reviewed; setup and checks remain. Saving the configuration alone does not complete onboarding.
>
> **Recovery:** Resume from the recorded step after interruption. Undo only changes that still match the recorded results; preserve later edits.
>
> **Technical records:** Links to proposed file contents, check results and exact recovery instructions.

Use concrete changed paths and purposes in actual output. Omit irrelevant or empty sections. Keep internal state names, receipt identifiers and hashes in the linked records.

## Runtime boundary

Keep the strict resolver and existing admission rules for activation, workflow-owned task execution, evidence/completion claims and recovery. Change the hook's use of readiness so that verified unfinished onboarding does not itself deny ordinary editing.

The hook must distinguish reliably inactive setup from an active workflow whose current configuration, qualification or ownership cannot be verified. Reuse existing per-client activation state and owned transaction/history evidence. Do not trust an editable configured flag, missing record, null digest or reason string alone as proof that active protection never existed.

For reliably inactive setup, ordinary edit hooks pass without starting workflow task state, publishing readiness or issuing an accepted active scope. Setup status may be explained during onboarding or startup; do not repeat a warning before every edit. Configuration drift in such a setup requires revalidation before activation, not a new restriction on ordinary work.

For a previously active workflow, retain existing checks and fail-closed behavior for the operations it protects. Changing a discovered active record to configured must not silently downgrade its protection. If observed history is ambiguous, preserve the existing rejection and provide the supported repair action. Never catch every readiness error and return success. Complete removal of every authority record is not currently protected by an independent historical lookup; adding that new security guarantee is outside this simplification. Keep the existing documented limitation explicit.

Reuse existing bounded acquisition and ownership mechanisms. Do not introduce another user preference, approval mode, parallel status database or per-tool network/model call. Before implementing, verify whether existing trusted acquisition exposes sufficient inactive-history evidence within its current hook budget. If not, extend that internal observation minimally; never weaken the proof to make the shortcut work.

## Trusted observation and activation ownership

The production entry path currently calls acquisition without a qualification provider. Tests inject one. Therefore publishing an active record alone cannot establish working activation. Fixing the inactive edit path without wiring a real reader would leave onboarding unable to finish.

The startup acquisition owner must supply a bounded, read-only qualification reader to the actual hook and workflow command entry paths. It consumes owner-issued retained qualification receipts, the exact project/checkout/client binding and current relevant installed/control facts. It checks receipt provenance and freshness through the existing evidence authority; user-authored JSON, stored flags and hashes alone are not observations. It performs no new model calls, login, native probes or mutable setup on each edit. Missing proof leaves activation unavailable.

The same owner must expose a reliable inactive-setup classification without returning an active task scope. Use existing owned configuration transaction records and physically bound authority; consult managed birth/history records only where managed runtime was allocated. File-only configuration may legitimately have `runtime:null` or an unallocated planned location. Do not require runtime birth to prove that such configuration is unfinished, or treat missing previously allocated state as equivalent. The production reader must establish the selected client's activation history without inventing a separate approval record or assuming history completeness. Missing, conflicting or unreadable required proof remains an explicit limitation. Demonstrate this predicate using real retained records before the hook exception ships; unit tests must not substitute a fixture assertion for the production reader.

`runtime/startup.py` owns acquisition/classification; `scripts/workflow.py` owns entry routing and hook decisions. `runtime/changes.py` and `scripts/onboard_coordinator.py` own the concrete configured and active operations. Reuse existing history/evidence APIs where their contracts supply the required proof; do not add an independent status database.

For a full onboarding request, prepare the exact configured operation and the activation intent within its scope. Complete the configured transaction, collect the required proof, then bind a separate exact managed-runtime/activation operation when different effects or a different postimage are required. Reuse the original trusted onboarding authority only while it covers those concrete effects, including any runtime allocation. Explicit effect scope does not require another conversational confirmation when the full request already supplies it. Never mutate the configured transaction's frozen manifest, reuse its approval digest for different effects, or proceed with unresolved ownership. A configuration-only request has no such activation authority.

On interruption, resume the original operation from its receipts before starting dependent activation. Tests must cover interruption between the operations, stale proof, drift and limited authority. An internal second transaction is not a second user approval or a new onboarding phase to manage.

## Options considered

1. **Change wording only.** Small, but leaves the edit lockout intact. Insufficient.
2. **Distinguish inactive setup at the hook boundary.** Recommended. Reuses current transaction and activation machinery while separating ordinary editing from workflow admission.
3. **Redesign publication so pending configuration is never discoverable.** Can make future startup simpler, but expands transaction, birth, recovery and compatibility changes. Defer unless the narrower boundary cannot be made reliable.

The hook recommendation remains a design proposal. The earlier authorization and presentation flow is already approved.

## Material exceptions

Ask only about unresolved effects beyond established authority: additional repositories/clients; machine-wide changes; permissions; external data sharing; providers/models; charges; destructive changes or migrations; workflow replacement; new enforcement; recovery accepting loss or overwriting later edits. Optional integrations and experiments remain separate. Verification operates within established resource limits and never fabricates missing evidence.

## Checks and recovery retained

Keep complete catalog/policy coverage, current source pins and compatibility for new setup/current readiness, mandatory behavior checks, general/applicable security review and observed activation. File-only undo instead uses its supported journal, receipts and backups: preserve original provenance without requiring current Toolkit availability, unchanged unrelated files or HEAD, native requalification, or evidence republication. Keep exact effect binding, before-state checks, locks, durable backups and receipts, relevant writer checks, reconciliation, dependency order and preservation of later edits. Never alter an old frozen transaction or widen its configuration-only authority.

Use existing matched evidence readers and their provenance/currentness guarantees. Genesis's private evidence-format and retention candidates do not establish production qualification and are not prerequisites for ordinary file configuration or undo. This design does not change retention periods, resource budgets, historical readers or public evidence writers.

Keep exact policy/runtime matching wherever existing runtime admission or recovery requires it. File-only undo's narrower prerequisites do not create a runtime upgrade exception. Uninterrupted upgrades remain separate work; this design neither repins old state nor adds a declaration that substitutes for matching sources.

A new supported configuration operation may eventually reconcile a changed installed runtime. It must retain the original transaction and its recovery records, preserve inactivity unless activation is separately covered, and establish current evidence. That operation is not authorized by this source-update/design task and has not run.

## Implementation reach after the source merge

- `runtime/startup.py`: wire the real trusted qualification reader and bounded inactive-setup classification; preserve strict readiness and scope binding.
- `scripts/workflow.py`: wire the reader into production entry; consume the classification for ordinary edit hooks while preserving workflow command admission.
- `runtime/changes.py` and `scripts/onboard_coordinator.py`: bind configured and active operations to verified existing onboarding authority without changing frozen transactions.
- `runtime/adapters/codex.py` and corresponding client routes: only change response adaptation if necessary; retain existing permissions and event contracts.
- `policies/10-project-onboarding.md`, skill entry point and inspect/apply references: integrate the approved authorization flow once, retaining conditional loading and the 150-word entry limit.
- A conditionally loaded Reviewed Configuration reference: use plain-language output, exact useful paths and visible recovery limits.
- Startup/runtime contracts and `docs/policy-sources.md`: keep behavior and source-to-contract coverage consistent.
- Existing startup/hook/transaction tests: add behavior cases at the responsible boundary rather than assertions about wording.

The source merge must retain branch-only Council/integration features and unrelated local edits. Resolve shared runtime interfaces semantically; do not select one side for every conflict. Do not recreate the separately delivered file-inventory fix.

## Acceptance scenarios

1. No enrollment: ordinary editing remains unaffected.
2. Genuine never-activated setup: ordinary editing passes; workflow task admission and readiness claims remain unavailable.
3. The same setup with installed-source drift: editing still passes when inactive history is established; activation waits for supported revalidation.
4. Previously active discovered workflow with drift or missing qualification: changing its label cannot silently remove existing protections. Test complete authority removal separately to document the current limitation, without claiming new hardening.
5. Wrong checkout, tampered authority, unsafe paths and uncertain ownership retain scoped rejection; neighboring worktrees gain no authority.
6. Setup checks fail: report the specific next action, continue independent work, and do not activate.
7. Full onboarding with successful checks: a separately bound activation operation needs no additional conversational approval; the real unmocked entry wiring consumes retained trusted proof and observes active scope.
8. Plan-only or configuration-only authority: never advances beyond its boundary.
9. Interrupted setup and rollback: preserve later edits, frozen bindings, backups and uncertain ownership.
10. Reviewed Configuration: a reader understands changes, decisions, status and recovery without milestone codes.
11. File-only configuration succeeds without runtime birth. Undo preserves later edits and works from supported original recovery records despite unrelated HEAD changes or unavailable Toolkit sources.
12. A missing previously allocated runtime retains writer-ownership protection; a never-allocated optional runtime does not acquire that restriction. Neither case fabricates activation proof.

Run package validation and focused fixture checks on the actual merged candidate. Native client observation in an authorized disposable environment is separate proof; fixtures cannot establish that the host obeys hook outcomes. Preserve settings and use bounded, reversible cases.

## Non-goals

No pilot activation, retroactive consent, cache patching, global settings changes, alternate model/provider setup, release, publishing, new workflow mode or automatic optional integration. No redesign of the separate file-inventory work.

## Coordination with Genesis Impl

The user requested coordinated simplification across the workflow. Keep this implementation coordination separate from the user-facing onboarding flow; it adds no onboarding approval steps.

Genesis acknowledged these boundaries on 2 November 2026:

| Change | Owner and boundary |
| --- | --- |
| Onboarding language, existing-authority reuse and Reviewed Configuration | This chat owns the instruction/output implementation and its independent review. The wider runtime interface remains unresolved. |
| File-only configuration and undo | Reuse reviewed upstream PR #30. Genesis coordinates any follow-up with its existing implementation owner. |
| Separate workflow simplification | [The existing simplification chat](codex://threads/01a19c5d-2c1f-7d43-87bd-accb203a179e) delivered PR #31 as main merge `a0f1767`. This chat does not duplicate its startup, transaction, ledger, check or resource changes. |
| Evidence formats, state, compact proof and retention | Genesis owns its current core contract and candidate, including the narrow delegation-guidance and transaction retained-support interfaces. This chat does not modify or release them. |
| Startup classification, production qualification reader and activation transactions | Shared integration boundary: assign one implementer for each concrete slice with Genesis before editing overlapping files. Do not create competing readers or authority records. |
| Installed pilot qualification and activation | [The existing pilot chat](codex://threads/01a19be9-a212-7152-833b-3d2659b2c611) retains that scope. The pilot remains reverted; retained recovery/history is preserved. This coordination grants no installation, activation or native-campaign authority. |

Integrate in this order:

1. Reconcile main merge `a0f1767e36d3433df5b24154eaa34bf3dd8c398f`, including PR #30, with this branch while preserving local edits. Genesis will compare the branches and the `0e70c6b` evidence fix at its next frozen checkpoint. Genesis development HEAD `8eef892b72dd4fe501fe67ffecd90708aea19ca0` is a work-in-progress retention candidate, not an integration or release baseline. That historical reconciliation is complete for the isolated instruction slice based on `a0f1767`; the primary feature checkout remains preserved.
2. Reconcile this design against the existing file-operation contract. Keep independent instruction/output changes small and separate from evidence-format or retention work.
3. Settle the real activation-reader contract with Genesis and the existing pilot owner, then assign one owner for the concrete UX/transaction implementation slice. Implement the shared startup/qualification/activation changes only against those agreed interfaces and acceptance scenarios. Full onboarding authority still covers its already-agreed effects; this is developer coordination, not a repeated user-consent gate.
4. Review the combined diff and run the affected original regressions plus the acceptance cases above. Report package/fixture checks separately from native observation. Keep installation and pilot activation outside this coordination task.

Specific overlap to reconcile: merge `0e70c6b` contains an exact protected-target exclusion in `runtime/evidence.py::_source_authorities` and restored safety regressions. Genesis's evidence work touches the same area. Preserve the tested protection once; compare behavior and ownership before carrying either implementation forward. The restored local capability-compatibility edits are a separate change and must not be swept into onboarding or core-format integration without review.

Genesis will compare that exact evidence change before deciding whether to carry it into its candidate; its presence there is not assumed. The agreed common regression set covers ordinary editing for verified never-active setup, protection of active/mismatched workflows, file-only rollback preserving edits/history, actual admission for runtime-bearing membership, refusal of unknown qualification, exact source pins, current scoped writer ownership and recovery of the original intent. Existing budgets and native-qualification limits remain unchanged.

Genesis's authoring references are `docs/lean-data-contract.md` and its private `LD-C3-check-provenance-contract.md` and `LD-C4-compact-closure-contract.md` under `.superpowers/sdd/implementation-preparation/` in the Genesis checkout. The latter is a draft under review. These are coordination references, not routine onboarding loading requirements; private candidate status must not be presented as shipped behavior.

## Design review and remaining proof

Independent read-only review identified three gaps in the first draft: missing production qualification-reader ownership, an unsupported claim about deleted authority records, and an unspecified configured-to-active transaction path. This revision assigns the owners and bounded proof requirements, retains the existing removal limitation, and describes separate frozen operations under reused user authority.

This document does not claim native execution or prove that the current retained-history interfaces already implement the inactive predicate. Implementation must establish that evidence path and actual client behavior before delivery. The design adds no pilot mutation or installed-package update. Package/fixture validation and native client observation must be reported separately.
