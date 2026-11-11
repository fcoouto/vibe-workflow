# Worker brief

Send this as the worker's assignment; the rules are in [Agent Delegation](../policies/03-agent-delegation.md#select-capability-and-dispatch). Omit lines that don't apply and never invent values.

- Objective: [one self-contained, reviewable outcome]
- Coordinator and report route: [the one coordinator who receives reports, and the permitted route: native completion or a local report]
- Checkout: [path and branch the worker inspects or edits, normally the coordinator's task worktree and branch]
- Files: [exact files it may change, or none; everything else is read-only]
- Context: [instructions to load and decisions already made; not the whole conversation]
- Dependencies and resources: [what must exist first; ports, build directories, databases or services it owns]
- Acceptance: [observable criteria]
- Checks: [commands to run and the expected results]
- Limits: [tools and permissions granted; time, attempt and resource budgets; commit or publish only if stated here]
- Stop when: [the stop condition; report a blocker instead of widening scope]
- Report: follow the [compact child report rules](../policies/07-context-and-handoff.md#lifecycle-record-and-reports); optional layout: [task record](task-record.md)

<!-- task-profile:start (generated from templates/task-profile.json) -->
- Task profile: [choose each level from the table below and leave out a field you cannot judge: complexity_band (low|medium|high|critical), risk_band (low|medium|high|critical), review_policy (none|self|independent|strict), uncertainty (low|medium|high), context_tokens (integer, 0 to 1000000000), tools (list)]

| Field | Level | Definition | Example |
| --- | --- | --- | --- |
| complexity_band | low | One file or one mechanical change with an obvious solution. | Fix a typo, rename a local variable, update a constant. |
| complexity_band | medium | A few files in one component, following a pattern that already exists. | Add an option to an existing command, with its test. |
| complexity_band | high | Several components, a new design choice, or behaviour that is hard to test. | Add a module with its own state and callers in two components. |
| complexity_band | critical | A change across shared core, concurrency or stored formats, where a wrong design is costly to undo. | Change the ledger's reservation rules or a stored record format. |
| risk_band | low | A mistake is caught by existing checks and affects only this task. | Documentation, comments, test-only changes. |
| risk_band | medium | A mistake could break one feature and is easy to revert. | A change to one command's output or validation. |
| risk_band | high | A mistake could lose data, spend money, weaken a safety rule or break many callers. | Funding, permissions, migrations, shared state. |
| risk_band | critical | A mistake could be irreversible or expose a secret. | Credential handling, destructive operations, release steps. |

Floors: a missing field takes complexity_band high, risk_band high, review_policy independent; a job that writes files needs at least complexity_band medium and risk_band medium.
<!-- task-profile:end -->
