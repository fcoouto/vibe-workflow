# Workstreams project preference

Use this procedure for ordinary Workstreams status, enable and disable requests,
including the onboarding question. Manage one project choice: **Enabled** or
**Disabled**, saved as the `workstreams` feature in the project's committed
`.vibe-workflow/settings.json` ([feature settings](../../../clients/features.md)).
No runtime enrollment, database, qualification or separate activation is required.

## Meaning and scope

Enabled automatically selects useful cooperative delivery through native children.
Follow [Agent Delegation](../../../policies/03-agent-delegation.md), including its
[outcome ownership rules](../../../policies/03-agent-delegation.md#outcome-ownership).
Native children execute this saved preference; it selects no separate service.

Disabled stops optional future automatic native child work. Explicit user
delegation requests and required independent review still apply. Reconcile
existing children safely; disabling never cancels them or releases their claims.
Unknown ownership stays reserved. [Naming](../../../clients/workstreams-naming.md)
remains independent of this choice.

This choice does not change resource admission, capacity recovery, schedules,
managed-runtime authority or existing protections. Load [advanced controls](advanced-controls.md)
only when the user explicitly selects one of those feature controls. Missing
advanced prerequisites do not prevent this project choice from taking effect.

## Read or change the choice

Run the installed package's command in the selected checkout:

```text
python3 -I -B <package>/scripts/workflow.py features status workstreams --cwd <checkout>
python3 -I -B <package>/scripts/workflow.py features enable workstreams --cwd <checkout>
python3 -I -B <package>/scripts/workflow.py features disable workstreams --cwd <checkout>
```

1. Identify the selected checkout. Reuse the user's explicit answer; do not ask again.
2. For status, read only. Report the feature's `effective` value as **Enabled** or
   **Disabled** and its `desired.source`: `project` (saved), `default` (not
   configured) or `legacy` (an unmigrated `AGENTS.md` block). `invalid_settings`
   means unavailable: report the error and leave automatic coordination off.
3. For enable or disable, run the command and report the effective value it
   returns. Repeating the effective choice is a no-op. On an error, such as
   private files not being ignored or the file changing during the write, report
   it and leave files alone; do not edit the settings file by hand around it.
4. When status reports `legacy`, the project still holds the old `AGENTS.md`
   block. Within an authorized change, run `features migrate plan` and then
   `features migrate apply` with the same `--cwd`. It moves the choice into the
   settings file, replaces the block with a short pointer, keeps a private backup,
   and refuses when the block and the file disagree. `features migrate undo
   --backup <backup>` restores only files still matching the migration's result.
5. Report the effective choice and exact checkout. It applies to subsequent work
   under these instructions, including this chat. Other checkouts receive it
   through normal Git integration of the committed file.

The settings file is the source of truth. Onboarding records may retain the
submitted answer as provenance; they are not a second active setting. Enabling
saves a coordination preference, not evidence of enforced isolation, managed
resource capacity, recovery availability or observed client execution.
