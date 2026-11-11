# Existing-project onboarding

Use for an explicitly selected existing project, authorized update, repair or recovery. Basic onboarding checks workspace readiness, inspects relevant instructions/configuration, assesses the complete Toolkit component catalog for relevance, makes authorized changes recoverably, and leaves useful guidance, working project commands, relevant checks, limitations and an undo location. Do not turn it into a whole-file census or client qualification campaign.

## Basic onboarding

Read the [policy index](../../../policies/00-index.md), [Project Scope](../../../policies/01-project-scope.md), [Project Onboarding](../../../policies/10-project-onboarding.md), applicable local instructions and linked policies. Run the steps in order. A failed step blocks only the work that depends on it: continue independent steps and carry the gap to step 7. Inspect-only ends after step 2 with a proposal; plan-only ends after step 4 with the plan. Neither installs, writes or applies.

0. **Readiness.** Run `python3 -I -B "$PLUGIN_ROOT/scripts/workflow.py" workspace status`. `PLUGIN_ROOT` is the installed package root (the parent of this skill's `skills` directory), never the target checkout. When the command may observe a sandbox instead of the host (for example Claude Desktop Cowork), report the affected items `unknown`. For each `missing` or `outdated` required item, say what it is and why it matters (the item's `why`), then run [prepare-my-workspace](../../prepare-my-workspace/SKILL.md) in prepare mode, scoped to those items (its steps 1–7), as its own operation under the [consent rule](../../../policies/11-tool-integrations.md#choose-and-configure), and resume here. Carry items still `needs-user` or `unknown` to the report; wait only where work depends on them, such as that client's startup verification. For an inspect-only or plan-only request, report what is missing and propose preparation without running it.
   *Done when* every required item is `ready`, `not-supported` with its reason, or carried to the report with the action that resolves it. *On failure* (status cannot run, or exit 2): report it, treat the affected items as unknown and continue.
1. **Target and clients.** Confirm the selected checkout; never the toolkit checkout just because it is the working directory; keep new-project creation separate. Name the client running this session and the agent clients installed, and select the clients to enroll (installed clients the plugin can enroll, by default). A client the plugin cannot enroll yet, such as OpenCode until its plugin client ships, is an explicit gap: say what is unavailable (startup orientation, enrollment) and that its tools come from step 0; never skip it silently. Follow [client startup integration](../../../policies/10-project-onboarding.md#client-startup-integration) for each selected client.
   *Done when* the checkout, the selected clients and every gap are stated. *On failure* (uncertain target): make no writes until it is confirmed.
2. **Sources and inspection.** Resolve the canonical [Vibe Coding Toolkit](https://github.com/soumatheusgomes/vibe-coding-toolkit) revision once for this setup or update; resume an interrupted setup at its recorded revision; a trusted versioned copy may serve offline setup with freshness marked unverified. Read the component guidance and linked sources needed for the selected work; keep provenance in authoring records, not routine task loading. Inspect relevant instructions, configuration, commands, paths and baseline checks. A runtime or tool the project itself declares is a project need, reported and checked with the project's own commands. Separate observed facts from assumptions; preserve user changes, data, permissions, model/provider choices and existing protections.
   *Done when* the revision, the observed facts, the project needs and the baseline results are recorded. *On failure* (source unavailable): block only the work that depends on it.
3. **Preferences.** Resolve missing [project preferences](#project-preference-questions), one at a time, before preparing changes.
   *Done when* each question is answered, reused, defaulted (4 and 5 only) or reported pending or inapplicable with its reason.
4. **Plan.** Assess every component in the complete Toolkit catalog for relevance, current adoption or an equivalent, and any useful action; [adapt the nine project documents](#adapt-project-documents) and [author the policy entry point](#author-the-policy-entry-point). Each component ends planned, reused with evidence, inapplicable with its reason, or blocked with its prerequisite and resume condition. An applicable component is omitted only through a recorded user decision linked from its row; a mandatory one never is.
   *Done when* every catalog component has one of those four outcomes and all nine documents map to a path or a reason. A plan-only request ends here.
5. **Apply.** Make only authorized, useful changes. Retain a recoverable private backup before meaningful configuration changes, then use the [configuration and undo contract](apply-and-recovery.md#file-configuration-and-undo) for managed file operations and each selected client's startup membership. Undo restores only content that still matches. Load detailed procedures only for a selected capability.
   *Done when* the changes are applied with their backup and undo location recorded. *On failure*: recover from the backup, keep later user edits and report.
6. **Verify.** Run useful project commands and relevant local/client checks, including [policy discovery](#verify-policy-discovery) and startup in a new session per selected client. State what each check establishes and what remains unobserved.
   *Done when* every check has a recorded result and its limit.
7. **Report.** Per client: version, trust and startup observed. Also: changes made, pending user actions (including step 0's), limitations and the undo location. Report documentation setup and client integration separately.
   *Done when* the report shows all of these, and useful guidance and working project commands are left.

When using packaged file tools for standalone basic setup, select `project_scope.inspection_scope: "relevant"`. This inspects only the project inputs needed for the requested edits: applicable ancestor/directory instructions, selected inputs and targets, their preimages, and the physical target scope. The tools capture planned write/delete/evidence paths; unrelated checkout state and a full file census are outside this partial snapshot. It grants no command execution inside the file transaction, runtime activation or managed authority. Run ordinary project checks natively under existing permission. Keep the Toolkit component-catalog assessment complete even when only selected files are inspected. Explain this as “inspect relevant project files”; do not ask users to select an internal JSON mode.

Use [Verify and finish](../../../policies/10-project-onboarding.md#verify-and-finish) to assess completion, including startup integration and the local procedures reached through generated project guidance. This does not establish AcceptedScope, task readiness, managed runtime activation, qualification, merge readiness or external authority.

Present onboarding preference choices one at a time in the client’s native single-select question box. Preserve default and recommendation labels even when the client orders the recommendation first. Record only submitted answers. If the box is unavailable or unanswered, leave the choice pending and continue independent authorized setup, except questions 4 and 5, which apply their default as described there.

## Adapt project documents

Adapt the local shapes below to project facts; they authorize neither prompt execution nor a default stack. During setup/update, reconcile them with the Toolkit revision already resolved for that operation; do not silently ignore a material source change. Map all nine manifest responsibilities in the existing assessment/plan to canonical paths or explicit reasons. Read only shapes needed for authorized additions or repairs. Reuse useful equivalents, including shared destinations; create no empty files or duplicates to match template filenames. Replace template-origin author notes and placeholders with verified facts or explicit unresolved decisions; never copy package-relative links unchanged into another project.

Distinguish observed facts, accepted decisions and proposed work. An unapproved next delivery stays proposed, with a concrete task and observable criterion. Infer required tools from inspected commands/code; an unknown minimum version does not make the tool itself unknown. Retain supplied dates and decision provenance in the existing owner.

Use selective native reads and existing checks by default. If a specific template-preparation or mechanical-check need warrants the optional CLI, load [template assistance](../../../clients/template-tools.md) and choose only the needed operation. Onboarding and document adaptation do not require helper invocation. Preserve the same mapping, original-content, link-verification and semantic-review duties with either approach.

| Responsibility | Adaptation |
| --- | --- |
| Product (`PRODUCT.md`) | [Product shape](../../../templates/project/product.md), in the canonical product document or overview. |
| Overview (`README.md`) | [Overview shape](../../../templates/project/overview.md), preserving useful introduction/commands. |
| Shared instructions (`AGENTS.md`) | [Shared-instruction shape](../../../templates/project/shared-instructions.md); [author an explicit policy entry point](#author-the-policy-entry-point). |
| Client instructions (`CLAUDE.md`) | [Client bridge shape](../../../templates/project/claude-instructions.md) for a selected client; record why it is unnecessary otherwise. |
| Architecture | [Architecture shape](../../../templates/project/architecture.md), preserving the established filename and capitalization. |
| Design and accessibility | [Design shape](../../../templates/project/design.md) when an interface exists; record a reason otherwise. |
| Local preparation | [Preparation shape](../../../templates/project/local-preparation.md) in the contributor guide or equivalent. |
| Quality | [Quality shape](../../../templates/project/quality.md) in its existing owner. |
| Setup and evidence | The [setup record](../../../templates/setup-record.md) or an equivalent. |

Preserve user-owned sections, selected clients, filenames, commands and stack; replace only authorized content. Missing information or access stays unresolved, not inapplicable. Document mapping, complete component-catalog assessment and relevant-file inspection remain distinct.

Resolve relative links from the generated destination: `handbook/development.md` reaches root instructions via `../AGENTS.md`. Verify adapted targets. Follow [project-guidance authoring](../../../policies/10-project-onboarding.md#apply-compatible-changes) for complete local contracts: selected-client installed-package pointers and explicit loading triggers, never Toolkit retrieval or author-machine paths. For delegated handoff or multi-session continuation, link the local [lifecycle record and reports](../../../policies/07-context-and-handoff.md#lifecycle-record-and-reports) owner instead of embedding another template.

Use the existing [Reviewed Configuration](reviewed-configuration.md) layout; distinguish file configuration, project checks, client startup proof and selected managed activation. Recovery retains its [configuration and undo owner](apply-and-recovery.md#file-configuration-and-undo).

### Author the policy entry point

Use the shared instruction file actually loaded by the selected client, normally
the project's root `AGENTS.md`. Keep supported client bridges pointing to that
owner. A hook-supplied pointer or a passive document list does not replace an
explicit instruction to read the policy index before substantive work.

Resolve the accepted installed package for this checkout/client from its startup
pointers or the invoked workflow skill's location. Verify its policy index and
record the package identity in the existing setup record; retain exact machine
and cache paths in its private evidence. Shared, tracked instructions use the
package name and package-relative policy path with the local resolution recipe
below, or an existing portable repository-relative target. Resolve relative links
from the generated file, not the author's checkout; do not copy policies or create
an installation just to make a link portable. Missing or conflicting sources
require reconciliation, not selection of the newest cache directory, a development
checkout or an upstream prompt. Preserve active-task pins and use the existing
update/recovery procedure before changing references that affect them.

Adapt the [template's starting section](../../../templates/project/shared-instructions.md#start-here) to the project's actual instruction owner.

Keep the policy index's trigger table authoritative. Add only useful project
navigation: actual commands, canonical product/architecture documents, conditional
client/skill entry points, and setup/undo. Preserve existing rules, memory load
paths and saved preferences, including `.vibe-workflow/settings.json`. Reapplication of
the same effective guidance is a no-op. For an authorized update, reconcile owned
stale pointers through [file configuration and undo](apply-and-recovery.md#file-configuration-and-undo),
preserving later edits and other clients/checkouts; do not append a competing router.

### Verify policy discovery

Apply [Verify and finish](../../../policies/10-project-onboarding.md#verify-and-finish)
to the generated instructions, not just the onboarding agent's loaded context:

1. Check the selected client's actual entry path, readable local links and complete
   procedure delivery. Check preserved instructions/preferences and safe reapplication
   through existing configuration/recovery checks. File existence and a successful
   reader invocation prove neither task selection nor agent compliance.
2. Walk representative project requests from that entry point: a small change,
   review-only work, resume, and applicable delegation, setup-repair or merge work.
   Use the canonical policy triggers to check that required procedures are reached
   before the affected action and unrelated procedures stay unloaded. Include a
   missing or conflicting package pointer when changed resolution needs coverage.
3. For observed routing, use a fresh native context with the request, generated
   entry point and necessary raw project inputs. Do not preload the expected policy
   list or the author's conclusions. Observe actual reads and actions using
   [Workflow Maintenance](../../../policies/09-workflow-maintenance.md)'s existing
   bounded, disposable evaluation method. A static walkthrough or fresh worker is
   not proof of a restarted client's startup delivery. Record unavailable or skipped
   observations explicitly; basic onboarding does not require a maintainer campaign.
4. Record the four evidence layers separately in the existing
   [setup record](../../../templates/setup-record.md#evidence-layers). Report only the
   layers established; hooks and package checks cannot stand in for observed routing.

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
   Explain that agents open each PR as a `[BLOCKED]` draft and mark it `[READY]`
   only after its checks and review pass ([PR lifecycle](../../../policies/08-merge-readiness.md#pr-lifecycle)),
   and that this choice decides who merges `[READY]` PRs. Permission is scoped to
   the named target.
   - No. I will merge `[READY]` PRs myself. (default)
   - Yes. Agents merge their PRs once they are `[READY]`.
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
   --no-cluster`: no model call, no Git hook. The Graphify command and client
   skill are required workspace items already handled in step 0; this question
   covers only the project's graph, and a still-missing item blocks only that
   setup. Record the project's Graphify choice in the setup record. Enabled
   projects run [post-merge maintenance](../../../clients/graphify-maintenance.md)
   automatically during merges and synchronization; the graph excludes documents
   and uncommitted task-worktree edits. Skip the question when the project has no code.
   - Yes, set it up now. (default, recommended)
   - Not now.
5. **Connect `<project_name>` to a long-term memory vault?**
   Explain that lasting facts stay outside the always-loaded instructions so
   those stay short. With an existing vault, add a pointer to the shared and
   client instructions; agents read it when relevant, write only when asked, and
   never copy its content into the repository. With none, create plain Markdown
   files at the location shown in the reviewed plan: no server, dependency or
   hook.
   - Yes, connect or set it up now. (default, recommended)
   - Not now.

Apply questions 4 and 5 from the Toolkit revision resolved for this setup. Yes
plans and completes the setup with its evidence (graph query result, readable
pointer). If a question is unanswered, apply its default and report it as
"default applied, not answered"; the reviewed plan names any location outside the
project. Not now is a deferral: record it as a `confirmed` decision (selected
`Not now`, its `rule` the component's row id), list its id in the row's
`decision_ids`, and end the row `omit`; name the next onboarding update as the
resume condition; never a silent omission. If the user declines in their own words, record that decision and do
not ask again. Reuse a recorded Yes or decline; ask again about a recorded
Not now only at the next onboarding update. Report a question that cannot apply
(for example Graphify with no code) as inapplicable, with the reason.

Retain submitted choices in the setup record with their scope and provenance.
Publication and merge choices are separate. The `[READY]` prefix summarizes
verified readiness and grants no authority; agents set it, never the user. The current Workstreams choice is the
`workstreams` entry in `.vibe-workflow/settings.json`; the setup record retains
provenance. Resource
admission and capacity recovery remain independent advanced settings. Report
unapplied choices as pending, with the concrete blocker; do not claim completion
or ask the user to enable the same choice again.

## Selected managed planning

Read [managed-plan](managed-plan.md) only when the selected operation uses the canonical managed dispatcher. It defines its pinned source bundle, bounded plan/inventory schemas, review and recovery bindings. Do not use its task-state or qualification prerequisites to block ordinary development. “Managed planning” refers to that dispatcher; standalone file-only authoring may still use packaged file tools without managed runtime, task state or managed authority.

## Selected advanced capabilities

Read the relevant command, runtime, adapter qualification, delivery or external-route procedure only when that capability is selected. Preserve its actual permissions, known-writer ownership, data-loss protections, review and recovery constraints. Adapter qualification campaigns are maintainer validation, not basic project onboarding.
