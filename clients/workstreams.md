# Workstreams execution

Load only for managed child delivery or when a Workstreams-specific operation is
needed. Ordinary delegation always uses [shared delegation safety](delegation.md),
regardless of either feature preference. For independent acceptance, load the applicable D5
[planning](evidence-plan.md), [request fields](evidence.md#request-fields)
and [completion](evidence-completion.md) sections under delegation's
section-loading rule.
Management requests use the controls route of the
[Workstreams skill](../skills/workstreams/SKILL.md).

Resource admission and capacity recovery are independent preferences. New supported
scopes start with both enabled only if no saved choices exist; preserve each
existing choice, including a legacy combined preference. Availability alone starts
no workers or schedules.

## Heavy execution

Before an implementer, reviewer or coordinator starts a heavy local phase, check
the current `resource_admission` preference and load
[resource admission](workstreams-resources.md). The run request uses the current
`resource_revision`. If resource admission is disabled, ordinary delegation
continues without a helper-managed heavy launch. Shared task concurrency,
dependency, continuation and ownership checks always apply. Telemetry failure
permits admission only through the remaining gates; it never overrides ownership
or budgets.

## Runtime controls

For managed child dispatch or continuation, load the
[dispatch and continuation protocol](workstreams-protocol.md). It owns native
identity and one-use delivery reconciliation. Ordinary task continuation uses
base delegation's allocated `continuation_limits`; it does not depend on a
Workstreams, resource or recovery revision. Management-only requests use the
separate management procedure and do not load this execution contract.

## Contexts and ownership

Use one coordinator and the existing task plan. Group work by cohesive outcomes,
relevant files and shared decisions, keeping tightly coupled work together. Do not
create a child for an action the coordinator can finish more cheaply. Admit ready
contexts just in time within actual host capacity and task-derived budgets; a
large backlog does not establish concurrent execution capacity.

Each context retains its stable task/job ID, native child identity, bounded brief,
files/resources, dependencies, acceptance/checks and next action. Native children
remain distinct from external executions. Any separately available external route needs its own supported contract; this
skill grants none. Do not invent native lineage or external same-thread resumption.

Keep the implementer through relevant review fixes. Send findings and material
state changes, preserving history and budgets. Reconcile the implementation owner
before independent review so retained write claims do not block its readers.
Before resuming, reconcile review readers; continuation preparation reserves no
claims for a settled owner. Consume rechecks and atomically reacquires original
resources, including current aliases, available capacity and dependent work. Old
completion/readiness cannot authorize a resumed assignment. Replace a child only
for a changed outcome, unusable history or a demonstrated recovery need, after
reconciling its writers and partial changes.

### Names identify the parent and its children

For allocation, display titles, progress and status changes, load the
[naming rules](workstreams-naming.md). Naming-only requests do not require the
remaining execution procedures.

## Dispatch, acceptance and recovery

Recheck task identity, ownership, current revision and one-use delivery authority
at dispatch/continuation boundaries. Feature revisions gate only their own
resource or recovery operations. No optional feature revision is required for
ordinary delegation. A lost response requires inspection of the same identity
and history, not blind resend.

Workers implement, run relevant checks and self-review. Use required independent
review, then coordinator assessment and integration. Release dependencies only
after their accepted outputs are available in the dependent checkout. Resume ready
work on completion/dependency events with bounded rechecks. Reuse check evidence
only while its candidate, scope and environment remain valid; rerun affected proof
after changes.

Choose output/context allocation, steps, time, concurrency and recovery allowances
from each assignment and supported route. Retain consumed attempts across fixes,
renames and toggles. Provider ceilings, permissions and validation limits remain
binding. Requested limits are not measured consumption or proof of hard native
token enforcement. Material reassessment uses remaining authorized totals.

Use native completion/attention events and bounded waits; inspect full history
only for decisions, failures or contradictions. Resume an idle/completed child with
the host's continuation operation; passive messaging may not start a new turn.
On the current collaboration surface, `followup_task` starts that turn, whereas
`send_message` only delivers context. Retain the original child and verify the
continuation outcome rather than treating message delivery as execution. One progress entry per wave is
enough. Add a coordination level only when it removes observed root work.

The host owns its internal retries. Admitted child continuations need recognized
terminal cause, known-finished host retry, supported recovery, reconciled writer
state and remaining task allowance. Unknown retry intent cannot establish those
child conditions. Parent capacity recovery follows the separate monitor procedure
below, including its user-authorized waiver of native retry-exhaustion proof.
`rateLimitExceeded` and `usageLimitExceeded` wait for human `continue`.
Coordinator shutdown also waits for human continuation. An eligible activated
parent uses the [temporary shared recovery monitor](workstreams-recovery.md) only
when `capacity_recovery` is enabled; registration carries the current
`recovery_revision` and needs no resource lease. Load that procedure on activation,
monitor run and final departure. Reconcile current task, files, native activity and
budgets before continuing. Recovery uncertainty does not block ordinary delegation
or resource admission. Disabling a feature leaves collection/review/reconciliation
available and preserves occupied or uncertain ownership.

## Graphify and proportional evidence

Use an existing sufficiently current graph when it replaces exploration. Build
local structural evidence only when broad discovery justifies its cost, with
`GRAPHIFY_BACKEND=none`. Share a read-only baseline graph; inspect branch changes
directly and refresh incrementally after relevant integration. Communities suggest
boundaries; missing edges never prove independence. Verify consequential source,
configuration and shared-resource relationships. Code-only graphs do not cover
policy semantics. Direct search is the fallback. No per-child build or watcher.

Compare accepted integrated outcomes, including coordinator, reviewers, retries,
graph preparation and human intervention. Use qualified usage sources; missing
observations stay unknown and included totals/subsets are not counted twice.
Package checks and a single coordinated delivery establish neither universal
speedup nor unchanged defect rates. For support or performance claims, consult the
[latest readiness report](../docs/workstreams-main-port.md); historical
experiments are not routine execution instructions.
