# Task Planning

Apply with the [shared contract rules](00-index.md).

**Mandatory adoption.** Establish the requested outcome, boundaries and proof of completion before implementation. Match planning depth to ambiguity, dependencies and risk.

**Project Onboarding:** identify canonical product and architecture documents, actual check commands, relevant environments and existing design/plan approval requirements.

| Element | Contract |
| --- | --- |
| Decision | Determine the smallest sufficient change, unresolved choices, necessary design/plan depth and observable acceptance criteria. Do not invent requirements or treat assumptions as decisions. |
| Required action | Read affected code and instructions; resolve where edits happen; state the outcome, included/excluded work and relevant constraints. Define acceptance criteria and checks before editing. Keep bounded work in the existing task context; document a plan when complexity, dependencies or multi-session work require one. Give each planned step a verification check and respect its dependencies. |
| Evidence | Use the existing [lifecycle record](07-context-and-handoff.md#lifecycle-record-and-reports) for the request, planning decisions and proof. Retain material assumptions, check commands or observations, the plan when needed and relevant known baseline failures. A planned check is not a passing result. |
| Blocking condition | An unresolved material choice, missing required approval, unavailable prerequisite or unverifiable acceptance condition blocks dependent implementation or completion. Continue independent authorized work; report missing checks rather than treating them as passed. |

## Planning and verification

At the transition to implementation and later checkpoints/completion, use the
[lifecycle record and reports](07-context-and-handoff.md#lifecycle-record-and-reports),
including its applicable title-owner route for direct or delegated work.

For an enrolled delivery policy, record the task's effective policy and complete required publication/companion scope before claiming completion. A no-publication decision is explicit. Snapshot the adopted defaults; a task-specific merge override never changes repository settings or grants authority by itself. Keep required unpublished work visible and preserve existing authorization/revocation boundaries.

- Apply [Project Scope](01-project-scope.md) to project boundaries, permissions, instruction conflicts and changed decisions.
- Every repository onboarded to `vibe-workflow`, existing or future, keeps its primary checkout on its designated branch: do not automatically switch or detach it. Task edits, commits and publication use a separate task worktree. Before the first edit or any checkout-affecting operation, including a review-only PR checkout, follow [delivery preparation](../clients/delivery.md#prepare-the-destination). This rule needs no GitHub remote, managed delivery, Workstreams or runtime database. Only the user's explicit authorization for a particular operation supplies an exception; a historical standing opt-out does not. Setup records, maintenance, documentation and one-line fixes are task changes too. A direct commit or push to a merge target is a merge under [Merge Readiness](08-merge-readiness.md#perform-and-confirm).
- For open or ambiguous requests, expose the plausible interpretations and clarify the intended outcome, boundaries and constraints before coding. Keep questions focused; skip clarification when scope is already clear.
- When this clarification-and-plan procedure is needed or explicitly requested, present steps with observable checks and obtain lightweight confirmation before code. Reconfirm only changed parts; preserve existing authorization. Clear, already-authorized tasks follow applicable project approvals without an added universal planning gate.
- For a smallest-change request, select one concrete issue and apply the following outcome-based deletion check both before presenting the plan and during self-review: would the remaining plan still resolve that issue and satisfy applicable requirements if a proposed edit were removed? If so, omit that edit as independent cleanup. Retain necessary dependent edits and required safety, verification and workflow work, even across multiple files or steps. Do not substitute file, bullet or word counts for necessity or narrow an explicitly broader request.
- Use existing commands and checks that prove relevant behavior and failures. Fixes need reproduction and regression proof when feasible; trivial edits do not need tests that merely repeat the implementation. Do not add features or infrastructure just to fill a plan.
- For new behavior and bug fixes, write the focused test or executable reproduction first, observe failure for the intended reason, then implement and rerun it. For behavior-preserving refactors, establish a passing baseline and rerun affected tests. Documentation and trivial nonbehavior edits need relevant static checks, not tests that restate the edit. Missing required proof remains a blocker under [Tests and Builds](05-tests-and-builds.md), not an implicit exception. Implementers self-review and report completion, a blocker or a pending decision.
- Run each planned check and report its actual result before marking the step complete. Fix a failed prerequisite before dependent steps; missing or unavailable verification stays explicit.
- On resume, follow [handoff and resumption](07-context-and-handoff.md#handoff-and-resumption). Reassess affected criteria after material changes and apply [Agent Delegation](03-agent-delegation.md) when its triggers apply.

Planning identifies relevant security requirements and checks. It adds no separate security review, checklist, report or approval gate; post-implementation review follows its own contract.

## Example

The user requests a case-insensitive search fix with existing sorting preserved. Inspect matching and sorting, record those two acceptance criteria, and select regression checks. A short task-level plan is sufficient. Switching search providers would change scope and needs a separate decision.
