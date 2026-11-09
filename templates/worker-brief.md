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
