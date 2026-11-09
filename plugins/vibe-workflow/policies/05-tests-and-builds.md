# Tests and Builds

Apply with the [shared contract rules](00-index.md).

**Mandatory adoption.** Support completion claims with current, relevant execution evidence.

**Project Onboarding:** identify canonical lint, formatting, type, test, rule and build commands; accepted quality thresholds; safe environments; existing failures; and equivalent CI checks. Record unavailable checks and the prerequisites needed to run them.

| Element | Contract |
| --- | --- |
| Trigger | When selecting implementation checks; before accepting a completed step, deliverable or merge; after changes affecting the candidate, check definitions, policy or execution environment. |
| Decision | Select the smallest set of checks that proves the acceptance criteria and relevant failure paths. Determine which existing evidence remains valid and which must be rerun. |
| Required action | Map criteria to actual commands or observable behavior, execute applicable checks safely, inspect their results and compare failures with the baseline. Follow the procedure below. |
| Evidence | Record candidate/base and working-tree layers, check definitions and policy version, environment/tool versions, executor, exact commands or observations, results and limitations. Distinguish passed, failed and not run. |
| Blocking condition | Required proof is missing, stale or failed; an introduced regression remains unresolved; or material uncertainty prevents judging an acceptance criterion. Block dependent completion, not independent authorized work. |

## Select and run proof

1. Use the acceptance criteria from [Task Planning](02-task-planning.md) and the project's actual commands. Select relevant lint, types, tests, local rules, build, dependency audits and integration checks; do not invent scripts or install an unrelated toolchain to fill a checklist. Use the same canonical commands in local verification and existing CI where applicable. Distinguish production from development/build dependency risks and identify incompatible fixes without applying them implicitly.
2. Test behavior and invariants, including affected boundaries, invalid inputs and failure paths. Reproduce a bug and establish regression proof when feasible. New nontrivial checking scripts need a self-check or test. Trivial edits do not need tests that merely restate their implementation.
3. Inspect unfamiliar commands and their side effects before running them. Use test data, free ports and disposable or already-authorized services; isolate or serialize shared resources. A combined check must not publish, contact production or apply migrations. Stop only processes and temporary resources created for this work.
4. Where affected, verify local startup, critical user flows, relevant errors, logs and changed contracts. Check links and configuration syntax for instruction/configuration changes. Distinguish static analysis, mocks, generated SQL, embedded engines, real services and observed client execution; one does not establish the others.
5. Inspect each result before accepting it. Fix introduced failures before dependent steps. Diagnose pre-existing failures, fix those within scope and record the rest with their impact and next action. A baseline failure does not excuse a new regression or automatically satisfy a required criterion. Retry only with new evidence or a changed condition, subject to [stuck-pattern escalation](03-agent-delegation.md#stuck-pattern-escalation).

## Preserve quality and evidence

Apply rules only where their contracts exist, using the adopted gate's self-check and native stack tooling. For compatible TypeScript, evaluate `no-floating-promises` and `no-misused-promises`; measure runtime/memory on the actual runner before adoption. Use 350 lines as a setup reference for size rules, adapting to the accepted project limit and counting semantics. Refactor by responsibility, preserving APIs, validation, error handling, accessibility and tests.

Preserve existing validation, test coverage and gate severity. Do not clear failures with broad ignores, generic suppressions, increased ceilings or deleted tests. New rules with legacy violations may start at warning severity with measured counts, scope and a reduction plan; violation-free new rules use error severity. Keep an existing zero-warning or error gate intact, measuring adoption separately where necessary. Changes to accepted thresholds require a scoped decision.

Bind results to actual content, including staged, unstaged and relevant untracked changes; HEAD alone is insufficient. Changes to relevant content, dependencies, environment, check definitions or policy invalidate affected results. Recheck current content before completion even if a hook reported no change. Reuse unaffected evidence only with a recorded reason.

Obtain applicable [Code Review](06-code-review.md) and [Security Review](04-security-review.md) evidence before accepting completion or merge; passing checks alone cannot replace required review. Report what ran, what remains unverified and any residual risk; a worker's success message is not proof. Stop repeating checks once sufficient proof is current unless new changes, failures or unresolved concerns justify more work.
