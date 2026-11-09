# Context and Handoff

Apply with the [shared contract rules](00-index.md).

**Mandatory adoption.** Keep decisions, instructions and continuation records accurate, discoverable and proportionate to the task.

**Project Onboarding:** map canonical documents, directory instructions, client-specific loading and actual memory locations. Reuse working records and supported client mechanisms; verify discovery and retrieval where observable.

| Element | Contract |
| --- | --- |
| Trigger | When starting or resuming a task; when behavior, commands, schema, boundaries, configuration or material decisions change; before handing off unfinished work. |
| Decision | Identify the authoritative record, relevant context and evidence needed now. Separate durable knowledge from temporary progress, observations from assumptions, and current decisions from superseded ones. |
| Required action | Retrieve applicable records, reconcile them against actual state, and update the responsible document alongside the change. Preserve enough verified context to continue without copying entire conversations or maintaining competing instructions. |
| Evidence | Record canonical paths and loading mechanisms; durable facts include their source, scope, last verification and invalidation trigger. Task records distinguish actual checks, pending work and unavailable observations. |
| Blocking condition | Missing, contradictory or stale information that materially affects scope or correctness blocks dependent decisions. Apply [Project Scope](01-project-scope.md) to unresolved authority; continue independent authorized work. |

## Canonical documents and selective retrieval

- Map existing equivalents for the overview, product scope, shared/client instructions, architecture, interface design, onboarding, quality and setup. Preserve useful directory rules and established filenames. Check case-insensitive collisions before consolidating files; preserve useful content and update references rather than create parallel authorities.
- Keep shared entry instructions short: actual commands, essential constraints and links with clear loading triggers. Client-specific files reference shared rules through supported mechanisms and retain only necessary client details. Load relevant details and tools on demand.
- After verifying an invocation repair, fix the owning command/configuration or instruction so later tasks use it. Retain its scope, evidence and invalidation conditions in the existing responsible record; a separate lesson does not repair a still-faulty invocation. Reuse existing prerequisite and retry controls before adding failure storage.
- Update the document that owns the changed behavior, decision or command. Keep complex plans discoverable with evidence, outstanding issues and next action. Describe planned or unavailable capabilities honestly; a written preference does not prove installation or execution.
- Prefer native memory and existing versioned documents. Add an external knowledge store only for a concrete need and authorized destination; do not create a vault, server or export merely to complete a checklist.
- Recheck memory against current code and authoritative decisions. Update or retire superseded entries, retaining useful decision history without leaving obsolete instructions active. Verify retrieval in a clean context when observable; a file's presence alone is not retrieval evidence.
- Keep credentials, personal data, sensitive traces and unnecessary transcripts out of shared records. Store necessary private evidence in an appropriate restricted location; share sanitized summaries. A graph or index may be incomplete or stale: state coverage limits rather than invent relationships.

## Lifecycle record and reports

Use the existing task context or plan for starts, meaningful checkpoints, completion and unfinished work. Keep standing context once and update only changes, in short prose or the optional [task record template](../templates/task-record.md). Keep planned, observed, reviewable and accepted results distinct, and stopped, running and unknown workers or processes; claim background work only when active execution is verified.

A child report sends this record's changed result, snapshot-bound candidate/proof references when output changes, material exceptions and necessary next action/owner. Reuse known assignment context, saved role declarations and native sender identity; supply compact context when the receiver cannot resolve it after resume or reassignment. Reports grant no authority and replace neither independent verification nor required decision-time observations of readiness, permissions, liveness and ownership.

Report reviewable output, material blockers/decisions and changed dependency/ownership conditions. Start normally updates local state without acknowledgment. Omit unchanged progress, heartbeat/acknowledgment loops, repeated roles/instructions, routine title receipts, reasoning and raw logs. Retrieve detail for acceptance, recovery or diagnosis; brevity must not hide material risk. The coordinator records receipt during existing handling without a required reply turn, then applies [delivery, supervision and acceptance](03-agent-delegation.md#progress-and-result-retrieval).

At applicable starts, checkpoints and completion, follow the existing [Thread Naming owner](../clients/workstreams-naming.md), including eligibility, overrides, pauses and pending-write recovery. Keep actual readback or a truthful preserved/pending/unavailable outcome in existing presentation metadata. Report only decision-relevant display exceptions; display failure remains nonblocking. A turn ending or child report never implies `DONE`: child output awaits parent acceptance; root completion includes the full agreed deliverable and required integration. Before marking a task or its title `DONE`, apply the [PR merge gate](../clients/workstreams-naming.md#status-and-accepted-progress), including its scope exemptions, whether or not naming is active. Add no model turns solely for acknowledgments or title repair.

The template adds no required artifact or status store and replaces no policy, machine request or continuation contract.

## Handoff and resumption

Use the [lifecycle record](#lifecycle-record-and-reports) for unfinished work, adding the template's handoff lines.
On resume, inspect the actual checkout, branch, files, checks and processes before trusting the handoff. Edits found outside the [resolved destination](02-task-planning.md#planning-and-verification), such as on a merge target, are a discrepancy: preserve them, report it and stop editing there. Reconcile discrepancies and use [Agent Delegation](03-agent-delegation.md) before taking over uncertain worker resources. Preserve pauses and limits; continue only the authorized remainder. Keep temporary progress out of permanent memory. Durable lessons belong once in the responsible record, supported by evidence and a condition for rechecking them.

Selected managed delivery also loads the [continuation protocol](../clients/workstreams-protocol.md). Reconcile writers and review readers before resuming; preserve identity, original scope, pins and remaining budgets.
