# Task record

An optional layout for the existing task context or plan; the rules are in [Context and Handoff](../policies/07-context-and-handoff.md#lifecycle-record-and-reports). Short prose is fine for small tasks. Omit lines that don't apply and never invent values.

## Assignment

- Objective: [requested outcome]
- Scope: [authorized work: included and excluded]
- Acceptance: [observable completion criteria]
- Constraints: [standing limits and decisions]

## Result and proof

- Result: [current result or phase: planned, observed, reviewable or accepted]
- Candidate: [commit or snapshot, checkout and branch, relevant paths]
- Checks: [commands actually run, their results and when; failures and unrun checks named as such]
- Evidence: [references]

## Material exceptions

- Findings and blockers: [cause, affected work and the condition for resuming]
- Unknowns and pending work: [what is not yet known or done]
- Changed limits: [permissions, remaining budgets, dependencies]
- Resources: [owned processes, ports, build directories, databases: stopped, running or unknown; cleanup still needed]
- Recovery: [how to recover or undo, when relevant]

## Next

- Action and owner: [the smallest next action and who takes it]

## Handoff

Add these only when unfinished work passes to another session:

- State checked: [date, checkout, revision and local changes when written; never assume a clean tree]
- Context to revisit: [sources or assumptions that may have changed]

## Example child report

When the assignment and sender are already known:

> Reviewable: `src/cleaning.py`, candidate `abc123`; linked `checks.txt` records `python3 -B check.py` passed on that snapshot. Independent review pending. Next: coordinator reviews; child retains ownership.
