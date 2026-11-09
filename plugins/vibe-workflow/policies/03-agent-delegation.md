# Agent Delegation

Apply with the [shared contract rules](00-index.md).

**Mandatory adoption.** Meet acceptance criteria with the least necessary model usage and coordination.

**Project Onboarding:** discover configured native clients/accounts; establish task constraints, concurrency limits, finite time/attempt/resource budgets and fallback rules. Preserve the user's coordinator settings. Qualify worker model/effort controls, settings observations and ownership mechanisms; prepare the active platform's local selection guidance.

| Element | Contract |
| --- | --- |
| Trigger | When considering or dispatching a worker; reassess after failed attempts, stalled repair/validation progress, or scope/capacity changes affecting delegated work. |
| Decision | Use deterministic tooling or direct execution when sufficient. Delegate for useful context isolation, specialization, required independence or genuinely independent parallel work. Select worker capability and verification by complexity, ambiguity, consequences and total effort, including retries and review. |
| Required action | When delegating, define a bounded worker brief, select an authorized worker model/effort using applicable platform guidance, reserve writable files and mutable resources, and verify returned work before integration. Apply the procedures below. |
| Evidence | Record the rationale, task/worker identity, guidance version/freshness, requested/configured/observed worker model and reasoning, ownership, actual checks, verified outcome and limitations. Unknown settings remain unknown; do not invent usage or savings measurements. |
| Blocking condition | No suitable authorized execution option, unresolved ownership or exhausted task budget. Use a qualified, already-approved fallback when available; otherwise block dependent work. |

## Select capability and dispatch

- Preserve the user's chosen coordinator model, reasoning and authentication. Native agents and models are automatically approved across all current and future platforms/providers and versions. Discover available native capabilities through existing account access, without approval prompts or a static allowlist.
- Any non-native model or execution route requires explicit user approval before use, including local models, proxies and separately billed API routes. Local models also require capability assessment, even when a client supports them natively.
- Select each worker's model and reasoning from that platform's current official recommendations, the task's requirements and locally available controls. Prefer the lightest capable option by total effort; do not downgrade solely for price. Set worker choices through supported dispatch controls without changing the coordinator or global defaults. When only inheritance/configuration is supported, record the limitation and use it only if it satisfies the assignment. Keep commercial model names out of portable rules.
- Read a concise local copy of the relevant platform guidance. Store source URLs, checked time, applicability and a content/version digest. Refresh during periodic environment maintenance, on relevant capability/catalog changes, missing/corrupt guidance or user request, outside hooks; apply [capability-based compatibility](11-tool-integrations.md#qualify-the-actual-workflow) to client updates and do not query upstream on every task. Cache age is a maintenance signal, not a correctness guarantee or automatic dispatch blocker. A failed refresh retains the last verified copy with stale freshness; suppress repeated attempts for the same trigger until a later maintenance run, relevant change or explicit retry. Reuse guidance only while its applicability and available controls remain compatible; otherwise hold dependent dispatch and continue independent work. Discover actual model availability separately.
- Give each worker its objective, necessary context/instructions, exact files, dependencies, mutable resources, acceptance criteria, checks and stop condition. Apply [handoff and supervision](#progress-and-result-retrieval). Grant only necessary tools and permissions; avoid copying the whole conversation or loading an unused specialist roster.
- Apply the test-first and self-review procedure in [Task Planning](02-task-planning.md#planning-and-verification) to each assignment; parallel dispatch preserves it.
- Split work by the [outcome ownership rules](#outcome-ownership). Use narrow specialist roles with distinct triggers; retain a standing role only for a recurring need.

When a bounded assignment is a useful delegation candidate, apply the [shared external selection and consent contract](../clients/external-delegation.md) before native implementation dispatch. Preserve explicit choices and the approved local preference; consideration does not transmit or spend. Load only the chosen external executor.

When considering an already authorized Usage Routing proposal, load its [proposal selection, verification and recovery procedure](../skills/manage-usage-routing/references/delegation.md). Load routing setup only for explicit routing management.

Before optional automatic native delegation, honor the [project preference](../skills/workstreams/references/procedure.md) in applicable project instructions. Onboarding and the Workstreams skill manage this same Enabled/Disabled choice. It needs no managed runtime. Explicit delegation and required independent review remain available when disabled or not configured. Preserve explicitly selected protections.

For native delegation, load the [display naming rules](../clients/workstreams-naming.md) to assess parent/child eligibility, including ordinary workers and independent reviewers. Follow their implementation trigger; dispatch alone does not authorize title adoption. This route is independent of Workstreams feature preferences and does not select managed task controls.

For role preparation, follow the [shared contract](../clients/worker-visibility.md#declare-before-dispatch) and its applicable native or external procedure.

Resource admission and capacity recovery are independent preferences. A new supported scope with no saved choice starts with both enabled; preserve each existing choice, including a legacy combined choice where separate settings do not exist. The [Workstreams skill](../skills/workstreams/SKILL.md) routes naming, feature controls, heavy resource work and parent recovery. Ordinary delegation and its shared dependency, continuation, ownership and acceptance checks apply in all four preference combinations. Resource-managed coordinator, worker and reviewer phases require current resource authority; parent recovery registration requires current recovery authority and no resource lease. A stale/disabled feature permission never authorizes the other feature. Uncertain recovery state does not block ordinary delegation or resource admission. Saved preferences grant no route, ownership, budget or native capability; load only the applicable operation procedure.

## Outcome ownership

Assign delegated implementation as small, self-contained outcomes rather than
helper files. The child owns implementation, focused tests, relevant guidance,
self-review and fixes from independent review through verified handoff. Reuse
that native child for follow-ups.
Keep tightly coupled work together; trivial tasks can stay direct.

The coordinator owns scope, priorities, interfaces/dependencies, ownership,
review/acceptance, shared tracking/configuration, lockfiles, Git operations and
integration. Small integration fixes are reasonable. Keeping most feature
implementation in the coordinator needs a task-specific reason.

Outcome ownership is responsibility; a native child is its reusable execution
context. A worktree separates checkout files. A user-owned sidebar chat is a
separate conversation: create one only on explicit user request. Prefer separate
worktrees for independent concurrent reviewable outcomes when useful. Preserve
partial work in place rather than migrate it merely for presentation.

Owners are not simultaneous workers. Decide dependent interfaces before affected
work; start ready independent outcomes just in time within actual host capacity
and the original budgets. A backlog does not establish capacity. Keep outcome-to-child bindings and the
[family/member mapping](../clients/workstreams-naming.md) in the existing task
record. Use [handoff and supervision](#progress-and-result-retrieval) for checkpoint
receipt and continuation.

## Ownership and parallel work

1. Reserve canonical writable files and mutable resources before dispatch, including path aliases, ancestor overlaps, ports, build directories, databases and services. Parallel tasks need disjoint files, independent or isolated mutable resources, and no direct or transitive dependencies. Unknown scope means serialize or combine the affected tasks. Different sections of one file are not independent scopes.
2. Make conflicting reservations mutually exclusive. Describe ownership/read-only restrictions as enforced only when the qualified mechanism prevents violations; otherwise record the limitation. Uncontrolled external writers remain a limitation. Branch names and worktrees alone do not isolate shared services.
3. Cap concurrency by actual authorized slots, host resources and the task budget. For waves, retain each task's brief, start eligible workers together, wait for their results before integration, and let the coordinator record one progress entry per wave.
4. Serialize authorized commits in task order, recording current HEAD immediately before each. If commits are not authorized, review recorded working-tree snapshots instead; workers must not commit or publish implicitly.

## Progress and result retrieval

Each brief names one coordinator and the permitted handoff route. Use the
[lifecycle record's compact child report](07-context-and-handoff.md#lifecycle-record-and-reports)
and its report triggers rather than repeating the assignment.
Use native completion/attention delivery. Separate sidebar peer chats may push
only with direct human authorization to message the recipient; a coordinator's
brief grants none. Otherwise report locally for coordinator retrieval.

The coordinator collects and records receipt in the existing plan, then applies
[review and integration](#review-integration-and-recovery) before acceptance.
Release dependencies only when accepted outputs are available in the dependent
checkout. A child may stop at reviewable output; this does not complete the
coordinator's requested scope or release uncertain ownership. Continue already
authorized, dependency-ready work with the same owner through the supported
continuation operation; passive message delivery is not execution. Reading status
or receiving a report grants no permission to start a peer's next turn. Keep ready
independent work moving when another child is blocked.

While assigned work remains active, the coordinator uses bounded native waits or
completion/attention events, retaining current cursors when supported. Dispatch,
a zero-timeout snapshot or a temporary timeout does not complete supervision. Consolidate user
updates, blockers and decisions in the parent chat; users primarily deal with the
coordinator. End supervision when the agreed scope is verified, the user explicitly
pauses, an original limit is reached, or remaining work needs a real user action or
unavailable capability. Record the stop reason and required resumption honestly;
do not promise unobserved background supervision or automatic wakeup. Selected
managed continuation and recovery controls still apply.

For managed progress, prefer the existing compact status view; expand the exact assignment/result only when acceptance, reconciliation, a changed observation or a specific diagnostic need requires it. Unchanged status does not justify rereading reports or whole logs. Required user updates may truthfully report waiting without another diagnostic read. Use logs when needed to investigate a failure or stall. Compact recorded progress cannot establish current readiness, permissions, liveness or stopped ownership; retain the existing authoritative checks at their decision points. Ordinary task/evidence status may revalidate readiness and is not a progress heartbeat.

## Review, integration and recovery

- Before review, fix the target base/candidate or working-tree snapshot, including staged, unstaged and relevant untracked changes. Reviewers inspect the diff and relevant callers, contracts and tests. They remain read-only; checks with shared mutable resources must be isolated or serialized. Review does not authorize fixes, commits or publishing.
- Review each wave's task changes before starting the next wave. Resolve blocking findings and verify affected checks before releasing dependent work; final integration review does not replace these reviews. Keep one coordinator progress entry per completed wave.
- Required independent review cannot be replaced by the implementer's self-review. Choose review scope and specialists by risk and evidence; apply [Security Review](04-security-review.md) when its trigger applies. Set review scope, requested rounds and stop conditions before dispatch. Internal fixes do not restart the round count. Further rounds need new risk, an evidence gap or a user request.
- The coordinator inspects the diff, deduplicates findings by root cause, checks their evidence and disposition, validates integration, and records actual checks and residual uncertainty. When findings overlap, reassess dependencies before the next wave. Verify reviewer identity through observed dispatch or a trusted integration; a report's claimed identity or success is insufficient.
- Diagnose failure before retrying: context, implementation, tooling or capability. An unchanged attempt needs new evidence; apply [stuck-pattern escalation](#stuck-pattern-escalation) before further repairs.
- Confirm an interrupted worker has stopped before taking over its files. Inspect partial changes and resume from verified state. Uncertain workers retain ownership; elapsed time alone does not release it. Continue independent authorized work while dependent work waits.

## Stuck-pattern escalation

Applies to coordinator work and delegated work. A stuck pattern includes recurring root causes, fixes undoing earlier fixes, or repair/validation cycles producing new findings without converging on the same acceptance criteria. A new finding alone is not a stall; repeated churn without verified progress is.

1. On detection, immediately stop the affected repair cycle and notify the coordinator/user. Do not try one more fix, wait for a fixed retry count, or treat fresh findings as permission to continue.
2. Escalate to an independent worker with stronger task-relevant diagnostic capability, selected through [worker selection](#select-capability-and-dispatch). Provide the target, acceptance criteria, attempted fixes, finding history, check results and remaining budget. Request a root-cause diagnosis and bounded recovery plan before further edits.
3. Preserve the coordinator's model, reasoning and authentication, permissions, ownership and original time, attempt, resource and review-round limits. Escalation consumes remaining allowances; it never resets them. If no suitable authorized worker or budget is available, report the blocker and continue only independent work.
4. Resume affected repairs only after the coordinator accepts an evidence-backed diagnosis, a changed approach and a focused verification plan. Record the escalation and decision; retain unresolved findings and required quality gates.
