# Thread Naming

For adoption, event repair and manual edits, read [title synchronization](workstreams-titles.md).

## New-thread scope

Apply this revision only to a native context created under it. Establish creation
from the existing native observation, creation brief or task record. First
implementation, first adoption, continuation, resume or task replacement does not
make an existing thread new; an absent saved title proves nothing. If creation
cannot be established, leave its title and record untouched and continue
authorized work. Existing contexts keep their current titles, records, original
controls, readers and pins; this fix adds no migration or repair. Once an eligible
new context is adopted, its ordinary title lifecycle continues through that record.

## Automatic implementation naming

Activate title synchronization immediately before the first authorized
implementation action, including a test-first change or implementation-worker
dispatch. A request such as “Fix this bug” needs no later “Implement it” phrase.
Apply this to solo work and delegated work, regardless of Workstreams preferences
or runtime activation. Question-only, exploratory and planning chats do not
activate naming, even when they delegate work.

Once active, keep naming through tests, review, fixes, waits and integration.
Native children supporting that implementation, including read-only investigators
and independent reviewers, are eligible. A follow-up question alone does not
reopen a completed task. Explicit naming-only requests remain supported without
starting implementation. Naming changes display metadata only; it does not
activate managed task controls.

At activation, resolve the [display-family association](#resolve-the-display-family)
before allocating a family. Adopt the title through the title
procedure. Keep prepared native `task_name`, attempt marker, lineage and ownership
unchanged.

Follow the title procedure's initial-adoption and later baseline-comparison rules.
Preserve known manual titles, explicit title instructions and paused
synchronization; do not configure again or resume merely because another worker
starts or a feature preference changes. The title
procedure owns manual-edit reconciliation for both runtime-bound and ordinary
native work. Touch only the current implementation family or explicitly requested
titles, not unrelated chats. If title controls or the procedure's required
observations are unavailable, report the naming limitation without guessing a
title, identity or successful update.

Start every managed title with one `U+FFFB` (interlinear annotation terminator),
immediately followed by five visible meter cells and one ASCII space. Include
the status field and its separator only when the user needs to act. The examples
include the leading character.

```text
<U+FFFB><meter> [W<number>] <P|C> · <short_topic>
<U+FFFB><meter> <STATUS> · [W<number>] <P|C> · <short_topic>
```

```text
￻▰▰▱▱▱ [W1] P · Thread naming
￻▰▰▰▰▰ [W1] C · Naming rules
￻▰▰▰▰▱ REVIEW · [W1] C · Status tags
￻▰▰▰▱▱ BLOCKED · [W1] C · Search support
￻▰▰▰▱▱ WAITING · [W1] C · Client check
￻▰▱▱▱▱ [W2] P · Solo fix
```

The literal brackets enclose the shared family code. Keep display identities
stable through fixes, renames and status changes.

## Resolve the display family

First inspect the existing task record for an explicitly adopted coordinator or
deliverable association. A source-thread ID or title alone does not establish it.
Keep native lineage, user-owned peer chats, task associations, display identity
and worktree scope distinct.

`P` identifies only the root coordinator; associated deliverable chats and native
workers display `C`. A deliverable chat stays in that root's family even when it
coordinates workers. Nested owners do not allocate another family for the same objective.
Standalone unrelated chats start their own families. An uncertain or unavailable
association is a naming limitation: report it without guessing; continue authorized
work.

Before new native dispatch or explicitly requested sidebar-chat creation, carry
the root coordinator UUID, family, role `C` and mapping owner in the brief. Record
the actual returned native UUID as the context's identity, then verify title
readback through the title procedure. Follow-ups reuse that binding; a new context
has its own returned UUID. Recognizable topics distinguish children; repeated `C`
roles need no counter. Never represent display association as native parentage.

## Allocate a family once across projects

For a standalone root without a recorded family, reserve its code with the
package helper, even when no children exist:

```text
python3 -I -B <package>/scripts/workstreams_family.py --client codex --parent <root_coordinator_uuid>
```

Use the actual client identifier and stable root coordinator UUID, never a title
or checkout path. The helper returns `{"ok": true, "family": "W1"}`. It serializes
allocation in `~/.vibe-workflow/workstreams-families.sqlite3`, shared across
all projects, linked checkouts and package copies for this OS user. The same
client/root returns the same code; distinct roots receive different codes.
Keep this register when a family completes, is cancelled or is archived. Never
reset it, delete allocations or choose a project-local counter. It stores identity
bindings only; progress and acceptance stay in the existing task plan.

Record the returned code with existing task/native bindings. Preserve existing
contexts' immutable creation markers when recording their display association.
On allocation failure, report the naming limitation; do not guess an ID or reset
the register. This allocator covers projects sharing the user register, not separate
machines or OS accounts; those require one coordinated allocation authority before
claiming a shared namespace.

The intended search keys are `[W1]` for a family and `[W1] C` for its children.
Verify literal matching on the current client before promising those search results;
search behavior and proportional fonts may prevent exact filtering or alignment.
Keep topics short and recognizable. Shorten only standard
activity words: `Implementation` becomes `Impl.` and `Refactoring` becomes
`Refactor`, regardless of original capitalization. Keep task-specific identifying
words in full. Leave `Review`, `Test`, `Fix`, family/member codes and statuses
unchanged. Keep each topic stable through fixes; add a distinguishing suffix only
when needed. Reviewer topics start with `Review`.

## Status and accepted progress

Use these canonical task states in the existing task plan:

| Status | Use when |
| --- | --- |
| `TRIAGE` | A thread has just been created and scope needs assessment. |
| `READY` | Scope and prerequisites are clear and work can start. |
| `WORKING` | Work is underway. This describes active task effort. |
| `REVIEW` | A result awaits review or review is underway. |
| `WAITING` | Work awaits an expected reply, dependency or event. |
| `BLOCKED` | Work cannot continue without intervention. |
| `PAUSED` | Work was deliberately put aside. |
| `DONE` | The thread's agreed scope is complete and verified, and the PR merge gate below holds. |
| `CANCELLED` | Work was closed without completing its agreed scope. |

Show `REVIEW` when the user is asked to review a result, `WAITING` when the
user owes an action such as a reply, approval or manual merge, and `BLOCKED`
when the user must intervene to unblock work. Hide the label for internal
review, agent-owned next steps, background dependencies and all other states.
Decide from the actual next action and its owner. Clear the label when the user
has acted or the next action moves to someone else, even if the task state stays
the same.

**PR merge gate.** `DONE` also requires every PR the thread opened or took over to
be observed merged on the remote: provider state merged, with a merge commit (for
example `gh pr view <PR> --json state,mergedAt,mergeCommit`). A local merge, an
approval, a `[READY]` prefix, the user's statement or a title does not count.
Until then the task state is `WAITING` while a `[READY]` PR awaits the user's
merge, or its actual `WORKING`/`REVIEW` state while other work remains. A PR's
`[BLOCKED]` or `[READY]` prefix ([PR lifecycle](../policies/08-merge-readiness.md#pr-lifecycle))
is not a thread status: a `[BLOCKED]` PR still in progress leaves the thread
`WORKING` or `REVIEW`. A PR closed unmerged keeps the
thread out of `DONE` until the user chooses another scope or `CANCELLED`. A PR
closed as `[SUPERSEDED]` passes through its replacement instead: when the user
decided it, that decision is the scope choice; when an agent replaced its own PR,
the replacing PR must be merged. If the remote state cannot be read, report it
unverified and withhold `DONE`.

A thread that opened no PR needs no extra step when its recorded scope excludes
publication, such as read-only, investigation or review work, or a child scoped
away from PRs. Any other editable thread needs an explicit no-PR decision in its
task plan. A PR the plan marks optional under the user's recorded scope decision is
such a decision; marking it optional after opening it, without that decision,
is not. Opening a PR after `DONE` reopens the thread to `WORKING`, `REVIEW` or
`WAITING` by its actual next action. A child's PR gates that child; a parent whose
deliverable depends on it is gated too.

Compute each thread's progress from currently accepted milestones in its existing
task plan, with task-appropriate weights when outcomes differ substantially.
Parent progress covers the combined deliverable, including required independent
review and integration; do not average child meters or count work twice. Child
progress covers its own scope. Elapsed time, token consumption and worker
self-reports do not establish acceptance. Reopened or invalidated milestones stop
counting and may reduce the meter or reopen a `DONE` thread.

Fill `▰` cells from the left and use `▱` for remaining cells. With measurable
milestones, each filled cell represents a completed 20% step. Round down and cap
unfinished work at four cells. Without measurable milestones, show one filled
cell during `WORKING` as an activity indicator. Other unfinished statuses use
an empty meter in that case. At adoption, use the actual phase. Newly started
implementation uses `WORKING`, not an automatic `TRIAGE` reset.
Reserve `▰▰▰▰▰` for verified `DONE`.
Show no numeric percentage or `% done`. Measured progress comes from accepted
milestones. `BLOCKED`, `WAITING` and `PAUSED` retain
accepted progress; `CANCELLED` retains partial progress and never implies completion.

Update display titles on meaningful milestone, phase, blocker or next-action
owner changes during existing handling, without extra model turns, polling or
a progress registry.
For naming-only requests within the [new-thread scope](#new-thread-scope), change
display metadata without enabling Workstreams, starting work or altering task
state. Use supported controls; do not bulk-rename unrelated threads.

Internal responsibilities remain coordinator, deliverable owner and independent
reviewer, assigned from each objective. Models/reasoning stay in metadata. Title
changes grant no authority and do not alter immutable native identity.

Use supported client display-title controls. Preserve native creation markers,
lineage and task ownership. Display titles do not qualify native execution or
authorize new work. If the user requests native parent/child linkage that the
client cannot expose, disclose that before dispatch. Peer-chat display association
does not prove native linkage or grant permission to message other chats.
