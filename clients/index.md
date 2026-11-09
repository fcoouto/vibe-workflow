# Clients

Read only the client instructions for the current task. Shared workflow requirements live in the [policy index](../policies/00-index.md).

For delivery workspace preparation and task-owned publication evidence, load [delivery](delivery.md) only when that operation applies, including before the first edit when the project's instructions or delivery policy select separate worktrees. Its current callable workspace binding does not activate a client or qualify publication/merge execution.

| Client | Load when |
| --- | --- |
| [Codex](codex.md) | Codex startup activates the workflow, or you implement, validate or diagnose its adapter. |
| [Codex task runtime](runtime.md) | A Codex task needs explicit task controls, a doctor report, or lifecycle troubleshooting. |
| [Workstreams](../skills/workstreams/SKILL.md) | Name threads, manage saved mode, or coordinate a suitable enabled assignment. |
| [Claude Code](claude-code.md) | Claude Code startup activates the workflow, or you implement, validate or diagnose its adapter. |
| [OpenCode](opencode.md) | You implement, test or diagnose Smart Routing on OpenCode: its adapter, routing plugin or a routed launch. Not a startup or onboarding client. |

Read the [shared startup contract](startup.md) only when implementing, diagnosing or preparing enrollment. Startup remains distinct from Codex's optional runtime controls. Add another native adapter with its implementation and separate execution evidence; hosting an agent does not create another hook protocol.
