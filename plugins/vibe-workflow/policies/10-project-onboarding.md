# Project Onboarding

Apply with the [shared contract rules](00-index.md).

Onboard the selected project with the smallest useful, recoverable setup.

**Setup inputs:** the target project, current Toolkit guidance, relevant components, existing project decisions and authorized scope. Assess the complete Toolkit component catalog for applicability, but inspect project files and client behavior only as needed for the selected setup. Do not turn catalog coverage into a whole-file census or maintainer qualification campaign.

For each new setup/update, resolve the current Toolkit revision once and use catalog guides, procedures and templates from that revision. Resume interrupted setup at its recorded revision. A trusted versioned copy may support offline setup with freshness explicitly unverified; unavailable sources block only their dependent setup work.

| Element | Contract |
| --- | --- |
| Trigger | Explicit project enrollment, an authorized setup update/repair, or expansion to additional repositories or clients. |
| Decision | Identify relevant components, compatible existing tools and any material choices. Resolve conflicts without weakening security, permissions or explicit user requirements. |
| Required action | Inspect relevant instructions/configuration, make authorized changes recoverably, and leave useful guidance, working project commands, relevant checks, limitations and an undo location. |
| Evidence | Record the target, choices, changes, checks actually run, limitations and recovery location. Keep unknown or unobserved behavior explicit. |
| Blocking condition | Uncertain target/authority, a material unresolved decision, unsafe recovery, or a missing prerequisite blocks only dependent work. Optional enforcement or unavailable qualification does not block basic onboarding. |

## Onboarding authority

An explicit onboarding request authorizes routine, in-project setup and relevant local checks within the selected project, clients, permissions and agreed effects. Verify that existing authority covers each concrete operation. Preserve inspect-only and plan-only limits. A reviewed plan or configuration document never supplies consent by itself.

Ask only for an unresolved material exception: an effect outside the established scope, new permissions or costs, external data sharing, a provider/model change, destructive action, migration, workflow replacement, additional enforcement, or recovery that would discard later edits. Reuse decisions already made; continue independent authorized work while the exception remains pending. When a selected capability lacks required evidence, resolve only that prerequisite; missing evidence is not a reason to ask the user to approve it.

When using managed planning, present the [Reviewed Configuration](../skills/onboard-my-project/references/reviewed-configuration.md) in plain language. It explains the work and its status without adding an approval gate. Keep internal stages, digests and receipt mechanics in linked technical records.

## Inspect and plan

1. Apply [Project Scope](01-project-scope.md). Keep machine preparation, new-project creation and existing-project enrollment distinct. Do not configure this plugin's own repository as an application merely because the agent is working here; confirm the actual destination.
2. Read applicable project instructions and inspect only relevant configuration, commands and paths. Identify the stack and existing checks needed for this setup; separate observed facts from assumptions.
3. Assess every Toolkit catalog component for relevance, adoption or a reason it does not apply. Load detailed setup or qualification procedures only when that component or an explicitly selected advanced capability requires them.
4. Use [Task Planning](02-task-planning.md) for material design choices. Reuse prior decisions; ask only for missing choices that change scope, behavior, cost, access or enforcement.

If the user selects managed delivery or merge enforcement, load its procedure and record only its own prerequisites and authority. Otherwise ordinary onboarding does not establish merge, publication, worktree or external-route policy. Preserve existing repository protections and any explicitly selected enforcement. Local worktree cleanup remains client-owned.

## Client startup integration

For each selected client, read its [client instructions](../clients/index.md) and the [startup contract](../clients/startup.md). Inspect checkout/client membership and the client's actual plugin discovery and trust. Installed hooks alone do not enroll a project; a documentation-only repository still needs startup registration.

Within authorized setup, reuse valid membership or configure missing startup-only membership through the [file configuration contract](../skills/onboard-my-project/references/apply-and-recovery.md#file-configuration-and-undo). New canonical startup-only entries use `configured` and `runtime:null`; preserve existing managed controls, other members and trust settings. Inspect-only requests produce a proposal without writes. A missing or unsupported client capability blocks its integration, not independent setup work.

Apply [Tool Integrations](11-tool-integrations.md#qualify-the-actual-workflow) to discovery/trust and startup checks. Verify a representative startup operation supplies the selected checkout's local client, policy and configuration pointers. Record registration, discovery/trust, handler execution and native-session delivery separately. A direct handler probe does not prove fresh-client delivery; keep any restart or trust intervention explicit. Do not add a runtime database, task enforcement or a maintainer qualification campaign to satisfy this check.

## Apply compatible changes

- Preserve product contracts, data, customizations, local changes and the existing stack. Reuse working tools and canonical documents. Install missing pieces at the narrowest supported scope through [Tool Integrations](11-tool-integrations.md); out-of-project changes require authorization covering that scope.
- Author project guidance with explicit task triggers and resolvable paths to complete installed local contracts. For example, routine security review loads [Security Review](04-security-review.md). Resolve the installed package through the selected client's startup pointers or skill location; do not hard-code the author's machine path into portable project guidance. Keep upstream Toolkit links as authoring/setup/update provenance, never a runtime instruction to fetch or invoke a prompt. Do not copy prompts into project instructions.
- Update dependencies/configuration in compatible groups after checking stable versions, changelogs, peers, engines and lockfiles. Do not force installs, apply automatic audit downgrades, add redundant dependencies or migrate frameworks/providers to match an example. Major incompatibilities, production/data migrations, new costs and destructive changes need a specific scoped decision.
- Refactor only for observed responsibility, dependency or duplication problems. Preserve public behavior, validation, error handling, accessibility and tests; broad architectural changes are separate decisions. Apply quality-rule changes through [Tests and Builds](05-tests-and-builds.md).
- Before altering meaningful configuration, retain a recoverable private backup and record original location/content identity. Bind the change plan to the target and current content; journal applied operations. Replace only authorized sections in mixed files and retire obsolete registrations as well as their files. Keep backups outside source control and instruction discovery paths.
- Resume from verified actual state. Restore only content still matching this operation's output; subsequent user edits require reconciliation. Do not overwrite them during rollback. Preserve recovery records even when some setup components remain blocked.

Use the [configuration and undo contract](../skills/onboard-my-project/references/apply-and-recovery.md#file-configuration-and-undo) for Toolkit-managed file operations. Do not create runtime state for ordinary development. Recovery preserves retained history and works even when activation or qualification is unavailable.

## Verify and finish

Run useful project commands and relevant local/client checks. State exactly what they establish and what remains unobserved; adapter qualification campaigns and advanced execution procedures are maintainer work unless explicitly selected.

Use [Tests and Builds](05-tests-and-builds.md) for relevant project behavior. Check that authorized changes work and can be undone without overwriting later user edits. A restart, login or trust step remains a pending limitation until observed.

Update canonical records through [Context and Handoff](07-context-and-handoff.md), including commands, decisions, coverage, validated versus configured-only capabilities and maintenance/recovery instructions. Apply required [Code Review](06-code-review.md), including post-implementation security coverage. Repository gates follow [Merge Readiness](08-merge-readiness.md) as a selected plugin requirement, not an implied permission to alter live rules or publish.

Check representative tasks through the generated project instructions: each trigger must reach its complete local procedure without Toolkit retrieval, while unrelated procedures stay unloaded. A catalog row count, valid links or package validation alone does not establish this behavior.

Report documentation setup and client integration separately. Missing required registration, trust or representative startup execution leaves client integration incomplete; report unobserved native-session delivery as pending, not verified. Basic onboarding completion also requires usable guidance and project commands, relevant checks or stated limits, and recoverable changes that preserve later edits. Report changes, evidence, limitations and the next action. Advanced selected capabilities retain their own readiness criteria.

For an explicitly approved disposable qualification target, follow [staged qualification](../skills/onboard-my-project/references/qualification.md#staged-disposable-qualification). Keep the complete scope-specific catalog and all nine behavior cases recorded, including pending evidence. Startup acceptance is distinct from successful onboarding; a passing bounded attempt completes only its independently verified entries. Ordinary projects and primary activation retain all applicable prerequisites.
