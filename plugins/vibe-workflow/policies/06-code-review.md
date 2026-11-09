# Code Review

Apply with the [shared contract rules](00-index.md).

**Mandatory adoption.** Review requirements, behavior and regressions before every merge; match independence and specialist coverage to actual risk.

**Project Onboarding:** identify critical flows and boundaries, qualified review mechanisms, evidence requirements and merge-blocking criteria. Record which client mechanisms can establish reviewer independence.

| Element | Contract |
| --- | --- |
| Trigger | Before every merge; before completing nontrivial work or work requiring review under the task/project rules; when explicitly requested; after changes affecting prior review evidence. |
| Decision | Select complementary review scopes from the actual diff, acceptance criteria and consequences. Determine necessary independent and specialist coverage without a fixed panel. |
| Required action | Establish the exact target, inspect changes and connected behavior, substantiate findings, verify their disposition and rerun affected checks after authorized fixes. |
| Evidence | Record target/base and working-tree layers, review scope, reviewer provenance, findings and disposition, checks, unreviewed areas and residual risks. |
| Blocking condition | Required review is missing or stale, a relevant blocking finding remains unresolved, or material uncertainty prevents judging the change. Unavailable capacity does not make required coverage inapplicable. |

## Choose coverage

- For bounded, low-risk work, perform focused requirement/diff review and applicable deterministic checks. Do not infer low risk from file extension alone.
- For nontrivial behavior, require independent behavior/regression review. Sensitive data, authorization, migrations, concurrency, broad architecture changes or uncertain scope need appropriately qualified specialist coverage and stronger evidence.
- Inspect relevant requirements and behavior, regressions and tests, integration/configuration, data/security, accessibility/UX, measured performance, and clarity/maintenance. Select only affected scopes; explain reduced or inapplicable coverage from actual evidence.
- Apply [Security Review](04-security-review.md) after implementation when its trigger applies. One review may cover general and security concerns only when its scope and reviewer qualification satisfy both. Required independence cannot be replaced by implementer self-review.

## Review the actual change

Fix the base/candidate or working-tree snapshot before review, including relevant staged, unstaged and untracked changes. Inspect current code plus affected callers, interfaces, contracts and tests. Trace the promised behavior and failure paths; verify compatibility and explicitly requested constraints rather than judging isolated lines.

Reviewers remain read-only. Use [Agent Delegation](03-agent-delegation.md) for worker capability, permissions, resource ownership, verified identity and review-round bounds. Use [Tests and Builds](05-tests-and-builds.md) for safe execution and current evidence. A report's claimed author or success cannot establish an independent execution.

Each finding needs severity, precise location, a concrete failure scenario and supporting evidence. Seek counterevidence before calling a concern a defect. Separate confirmed defects, hypotheses and optional improvements; do not invent performance claims without measurements. The coordinator verifies findings, deduplicates shared root causes and reassesses dependencies when overlap reveals coupled work.

Return fixes to the responsible writer within existing authorization, subject to [stuck-pattern escalation](03-agent-delegation.md#stuck-pattern-escalation). Recheck affected findings and rerun relevant checks after changes; preserve unaffected evidence only with a reason. Do not repeat a full review merely because an internal fix occurred, reset requested round counts, or expand scope to obtain a clean report.

Finish when relevant findings are resolved or explicitly dispositioned, required checks pass, and coverage and residual risks are recorded. A blocking finding cannot be silently reclassified as optional. Additional review needs new evidence, unresolved risk or an explicit request; a cycle count never proves correctness. Compare current content with the reviewed target before accepting the result. Report unavailable independent review and remaining uncertainty. A passed review supplies evidence; it grants no new authority to merge or publish.
