# Workstreams project preference

Use this procedure for ordinary Workstreams status, enable and disable requests,
including the onboarding question. Manage one project choice: **Enabled** or
**Disabled**. Use native instruction reading and editing; no runtime enrollment,
database, qualification or separate activation is required.

## Meaning and scope

Enabled authorizes automatic, suitable task-scoped creation or reuse and coordination
of workstream sessions without repeated approval for each session, within the user's
scope, permissions and available capacity. Follow [Agent Delegation](../../../policies/03-agent-delegation.md),
including its [outcome ownership rules](../../../policies/03-agent-delegation.md#outcome-ownership).
It selects no separate service and does not authorize control of unrelated peer chats.

Disabled stops optional automatic workstream-session coordination. Explicit user
delegation and required independent review still apply. Ordinary subagent assistance
follows Agent Delegation and is not evidence Workstreams ran. Reconcile existing
assignments safely; disabling never cancels them or releases their claims. Unknown
ownership stays reserved. [Naming](../../../clients/workstreams-naming.md) remains
independent of this choice.

This choice does not change resource admission, capacity recovery, schedules,
managed-runtime authority or existing protections. Load [advanced controls](advanced-controls.md)
only when the user explicitly selects one of those feature controls. Missing
advanced prerequisites do not prevent this project choice from taking effect.

## Read or change the choice

1. Identify the selected checkout and read its applicable project instructions.
   Reuse the user's explicit answer; do not ask again. Resolve the current choice
   from the owned block below in the applicable `AGENTS.md`. Honor normal
   instruction scope and precedence. An inherited choice applies only within its
   instruction scope; another checkout's file is not inherited through shared Git.
2. For status, read only. Report **Enabled**, **Disabled**, or **Not configured**
   with the effective instruction path. Missing, malformed or conflicting blocks
   do not enable automatic coordination or establish advanced feature status. If the
   target or an instruction conflict is unresolved, report it and leave files alone.
3. For enable or disable, edit only this block in the selected checkout's root
   `AGENTS.md`; create that file if absent. Use `[x]` for Enabled and `[ ]` for
   Disabled. Include a resolvable local link to this installed procedure, obtained
   from the skill's location or startup pointers. Replace the placeholder below;
   never retain an author's machine path. Do not add a second local block. A local
   block may override an inherited default under normal instruction precedence.
4. Follow the existing [recoverable configuration rules](../../../policies/10-project-onboarding.md#apply-compatible-changes):
   preserve unrelated bytes and user edits, retain a private preimage and hash,
   check safe target ownership/paths and unchanged preimage before writing, and
   read back the exact result. Repeating the effective choice is a no-op. Do not
   overwrite conflicting instructions or another writer's changes.
5. Report the effective choice and exact checkout. Once saved and read back, it
   applies to subsequent work under these instructions, including this chat. No
   new approval or activation step is needed. Other checkouts receive it through
   their applicable instructions or normal Git integration. Undo restores only
   this owned block while preserving later edits.

```markdown
<!-- vibe-workflow:workstreams:start -->
- [x] Workstreams
Follow the [Workstreams project preference](<resolved-local-procedure-path>).
<!-- vibe-workflow:workstreams:end -->
```

The instruction block is the current source of truth. Onboarding records may
retain the submitted answer as provenance; they are not a second active setting.
Enabling saves a coordination preference, not evidence of enforced isolation,
managed resource capacity, recovery availability or observed client execution.
