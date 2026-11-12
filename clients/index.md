# Clients

Read only the client instructions for the current task. Shared workflow requirements live in the [policy index](../policies/00-index.md).

For workspace preparation or a checkout-affecting operation, including review-only PR checkout, load [delivery](delivery.md) before acting. Also load it for task-owned publication evidence. [Task Planning](../policies/02-task-planning.md#planning-and-verification) owns the rule for every onboarded repository. The callable workspace binding does not activate a client or qualify publication/merge execution.

| Client | Load when |
| --- | --- |
| [Codex](codex.md) | Codex startup activates the workflow, or you implement, validate or diagnose its adapter. |
| [Codex task runtime](runtime.md) | A Codex task needs explicit task controls, a doctor report, or lifecycle troubleshooting. |
| [Workstreams](../skills/workstreams/SKILL.md) | Name threads, manage saved mode, or coordinate a suitable enabled assignment. |
| [Claude Code](claude-code.md) | Claude Code startup activates the workflow, or you implement, validate or diagnose its adapter. |
| [OpenCode](opencode.md) | You implement, test or diagnose Smart Routing on OpenCode: its adapter, routing plugin or a routed launch. Not a startup or onboarding client. |

The [workspace requirements](workspace-requirements.md) list what every installed client needs; `prepare-my-workspace` and `onboard-my-project` read it through `workflow.py workspace status`.

Read the [shared startup contract](startup.md) only when implementing, diagnosing or preparing enrollment. Startup remains distinct from Codex's optional runtime controls. Add another native adapter with its implementation and separate execution evidence; hosting an agent does not create another hook protocol.
