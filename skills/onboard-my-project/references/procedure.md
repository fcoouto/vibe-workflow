# Existing-project onboarding

Use for an explicitly selected existing project, authorized update, repair or recovery. Basic onboarding inspects relevant instructions/configuration, assesses the complete Toolkit component catalog for relevance, makes authorized changes recoverably, and leaves useful guidance, working project commands, relevant checks, limitations and an undo location. Do not turn it into a whole-file census or client qualification campaign.

## Basic onboarding

Read the [policy index](../../../policies/00-index.md), [Project Scope](../../../policies/01-project-scope.md), [Project Onboarding](../../../policies/10-project-onboarding.md), applicable local instructions and linked policies. For setup authoring, resolve the canonical [Vibe Coding Toolkit](https://github.com/soumatheusgomes/vibe-coding-toolkit) revision once. Assess every component in its complete catalog for relevance, current adoption or an equivalent, and any useful action. Read the component guidance and linked sources needed for the selected work; keep provenance in authoring records, not routine task loading.

1. Confirm the selected checkout and applicable instructions. Follow [client startup integration](../../../policies/10-project-onboarding.md#client-startup-integration) for each selected client. Keep workspace preparation and new-project creation separate; do not enroll the toolkit checkout just because it is the current working directory.
2. Inspect relevant project configuration, commands and paths. [Adapt project documents](#adapt-project-documents) to those facts. Separate observed facts from assumptions, and preserve user changes, data, permissions, model/provider choices and existing protections. Resolve missing [project preferences](#project-preference-questions) before preparing changes.
3. Make only authorized, useful changes. Retain a recoverable private backup before meaningful configuration changes; use the [configuration and undo contract](apply-and-recovery.md#file-configuration-and-undo) for managed file operations. Load detailed procedures only for a selected capability.
4. Run useful project commands and relevant local/client checks. Report what they establish and what remains unknown. Leave the user useful guidance, working commands, limitations and an undo location.

When using packaged file tools for standalone basic setup, select `project_scope.inspection_scope: "relevant"`. This inspects only the project inputs needed for the requested edits: applicable ancestor/directory instructions, selected inputs and targets, their preimages, and the physical target scope. The tools capture planned write/delete/evidence paths; unrelated checkout state and a full file census are outside this partial snapshot. It grants no command execution inside the file transaction, runtime activation or managed authority. Run ordinary project checks natively under existing permission. Keep the Toolkit component-catalog assessment complete even when only selected files are inspected. Explain this as “inspect relevant project files”; do not ask users to select an internal JSON mode.

Use [Verify and finish](../../../policies/10-project-onboarding.md#verify-and-finish) to assess completion, including startup integration and the local procedures reached through generated project guidance. This does not establish AcceptedScope, task readiness, managed runtime activation, qualification, merge readiness or external authority.

Present onboarding preference choices one at a time in the client’s native single-select question box. Preserve default and recommendation labels even when the client orders the recommendation first. Record only submitted answers. If the box is unavailable or unanswered, leave the choice pending and continue independent authorized setup, except questions 4 and 5, which apply their default as described there.

## Adapt project documents

Adapt pinned Toolkit templates to project facts; they authorize neither prompt execution nor a default stack. Map all nine manifest responsibilities in the existing assessment/plan to canonical paths or explicit reasons. Reuse useful equivalents, including shared destinations; create no empty files or duplicates to match template filenames.

| Responsibility | Adaptation |
| --- | --- |
| Product (`PRODUCT.md`) | Objective, scope, flows and acceptance criteria in the canonical product document or overview; unknown decisions stay explicit. |
| Overview (`README.md`) | Preserve introduction/commands; link canonical documents. |
| Shared instructions (`AGENTS.md`) | Preserve custom and ancestor/directory rules plus command/policy pointers; retain nested ownership. |
| Client instructions (`CLAUDE.md`) | Supported shared-rule loading and existing customizations for selected clients; explain why an unselected client's file is unnecessary. |
| Architecture | Observed code, boundaries, dependencies and data decisions in the established file, preserving capitalization. |
| Design and accessibility | Relevant interface guidance, or a reason when no interface exists. |
| Local preparation | Actual prerequisites and first-run/check commands in the contributor guide or equivalent; invent no package manager, service or database. |
| Quality | Accepted checks, applicable review/security procedures and known baseline limits in their existing owner. |
| Setup and evidence | Adopted components, decisions, actual checks, pending observations and maintenance/recovery pointers in the setup record or equivalent. |

Preserve user-owned sections, selected clients, filenames, commands and stack; replace only authorized content. Missing information or access stays unresolved, not inapplicable. Document mapping, complete component-catalog assessment and relevant-file inspection remain distinct.

Resolve relative links from the generated destination: `handbook/development.md` reaches root instructions via `../AGENTS.md`. Verify adapted targets. Follow [project-guidance authoring](../../../policies/10-project-onboarding.md#apply-compatible-changes) for complete local contracts: selected-client installed-package pointers and explicit loading triggers, never Toolkit retrieval or author-machine paths. For delegated handoff or multi-session continuation, link the local [lifecycle record and reports](../../../policies/07-context-and-handoff.md#lifecycle-record-and-reports) owner instead of embedding another template.

Use the existing [Reviewed Configuration](reviewed-configuration.md) layout; distinguish file configuration, project checks, client startup proof and selected managed activation. Recovery retains its [configuration and undo owner](apply-and-recovery.md#file-configuration-and-undo).

## Project preference questions

After identifying the repository, remote and primary branch, and before preparing
changes, resolve missing preferences in the order below. Use the native
single-select rule above for each question. Reuse existing decisions and keep
answers bound to the selected repository and target. Explain the effect before
asking. If the remote does not support PRs or the target is unknown, report the
affected choice as inapplicable or pending, respectively.

1. **How should agents publish completed task changes in `<repository_name>`?**
   State the remote and explain that draft PRs make changes and checks available
   for review.
   - Keep changes local. Ask me before committing or publishing. (default)
   - Commit the task changes, push a task branch, and open a draft PR. (recommended)
2. **May agents merge pull requests into `<project_name>`’s primary branch, `<primary_branch_name>`?**
   Explain that permission is scoped to the named target and still depends on
   [merge readiness](../../../policies/08-merge-readiness.md).
   - No. I will review and merge them myself. (default)
   - Yes. Merge independently reviewed PRs marked `[READY]` when all required checks pass.
3. **Enable Workstreams for `<project_name>`?**
   Explain that Enabled automatically uses suitable native child work; small
   tasks can stay with the coordinator. Show one choice:
   - Disabled. (default)
   - Enabled. (recommended)
   Reuse an existing explicit answer or saved choice. Apply a submitted answer
   through the shared [Workstreams project preference](../../workstreams/references/procedure.md)
   during this setup. Verify readback before reporting it enabled or disabled.
   Ordinary coordination needs no runtime activation or qualification campaign.
4. **Set up Graphify for `<project_name>`?**
   Explain that a local code map lets agents answer "what depends on this?"
   without reading many files. Setup adds `.graphifyignore` (secrets, `.env*`,
   caches, tool state) and a Git ignore for `graphify-out/`, then builds a
   code-only graph with `GRAPHIFY_NO_AUTO_REFRESH=1 graphify extract . --code-only
   --no-cluster`: no model call, no Git hook. It reuses the installed Graphify and
   client skill; for a missing one, the reviewed plan shows the exact install
   command and scope. The graph excludes documents and goes stale until
   regenerated. Skip the question when the project has no code.
   - Yes, set it up now. (default, recommended)
   - Not now. Nothing changes; I'll ask again at the next onboarding update, or you can tell me not to.
5. **Connect `<project_name>` to a long-term memory vault?**
   Explain that lasting facts stay outside the always-loaded instructions so
   those stay short. With an existing vault, add a pointer to the shared and
   client instructions; agents read it when relevant, write only when asked, and
   never copy its content into the repository. With none, create plain Markdown
   files at the location shown in the reviewed plan: no server, dependency or
   hook.
   - Yes, connect or set it up now. (default, recommended)
   - Not now. Nothing changes; I'll ask again at the next onboarding update, or you can tell me not to.

Apply questions 4 and 5 from the Toolkit revision resolved for this setup. Yes
plans and completes the setup with its evidence (graph query result, readable
pointer). If a question is unanswered, apply its default and report it as
"default applied, not answered"; the reviewed plan names any install command or
location outside the project. Not now is a deferral: record the user's decision
as its prerequisite and the next onboarding update as the resume condition, never
a silent omission. If the user declines in their own words, record that decision
and do not ask again. Reuse a recorded Yes or decline; ask again about a recorded
Not now only at the next onboarding update. Report a question that cannot apply
(for example Graphify with no code) as inapplicable, with the reason.

Retain submitted choices in the setup record with their scope and provenance.
Publication and merge choices are separate. The `[READY]` marker summarizes
verified readiness and grants no authority. The project instruction block is the
current Workstreams choice; the setup record retains provenance. Resource
admission and capacity recovery remain independent advanced settings. Report
unapplied choices as pending, with the concrete blocker; do not claim completion
or ask the user to enable the same choice again.

## Selected managed planning

Read [managed-plan](managed-plan.md) only when the selected operation uses the canonical managed dispatcher. It defines its pinned source bundle, bounded plan/inventory schemas, review and recovery bindings. Do not use its task-state or qualification prerequisites to block ordinary development. “Managed planning” refers to that dispatcher; standalone file-only authoring may still use packaged file tools without managed runtime, task state or managed authority.

## Selected advanced capabilities

Read the relevant command, runtime, adapter qualification, delivery or external-route procedure only when that capability is selected. Preserve its actual permissions, known-writer ownership, data-loss protections, review and recovery constraints. Adapter qualification campaigns are maintainer validation, not basic project onboarding.
