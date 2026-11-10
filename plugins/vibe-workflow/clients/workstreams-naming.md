# Thread Naming

Only the coordinator's thread gets a managed title. Native children get none:
give each a short description at dispatch through the client's own field. For
adoption, event repair and manual edits, read [title synchronization](workstreams-titles.md).

## Automatic implementation naming

Activate title synchronization immediately before the first authorized
implementation action, including a test-first change or implementation-worker
dispatch. A request such as “Fix this bug” needs no later “Implement it” phrase.
Apply this to solo work and delegated work, regardless of Workstreams preferences
or runtime activation. Question-only, exploratory and planning chats do not
activate naming, even when they delegate work.

Once active, keep naming through tests, review, fixes, waits and integration. A
follow-up question alone does not reopen a completed task. Explicit naming-only
requests remain supported without starting implementation. Naming changes display
metadata only; it does not activate managed task controls or change prepared
native names, lineage or ownership.

Follow the title procedure's initial-adoption and later baseline-comparison rules.
Preserve known manual titles, explicit title instructions and paused
synchronization; do not configure again or resume merely because a worker starts
or a feature preference changes. Touch only the coordinator's own thread or
explicitly requested titles. If title controls or the procedure's required
observations are unavailable, report the naming limitation without guessing a
title, identity or successful update.

## Title format

Start every managed title with one `U+FFFB` (interlinear annotation terminator),
immediately followed by five visible meter cells and one ASCII space. Include
the status field and its separator only when the user needs to act. The examples
include the leading character.

```text
<U+FFFB><meter> <short_topic>
<U+FFFB><meter> <STATUS> · <short_topic>
```

```text
￻▰▰▱▱▱ Thread naming
￻▰▰▰▰▱ REVIEW · Status tags
￻▰▰▰▱▱ WAITING · Client check
```

Keep topics short and recognizable, at most 160 characters and without ` · `.
Shorten only standard activity words: `Implementation` becomes `Impl.` and
`Refactoring` becomes `Refactor`, regardless of original capitalization. Keep
task-specific identifying words in full and the topic stable through fixes.

## Status and accepted progress

Use these canonical task states in the existing task plan:

| Status | Use when |
| --- | --- |
| `TRIAGE` | A thread has just been created and scope needs assessment. |
| `WORKING` | Work is underway. This describes active task effort. |
| `REVIEW` | A result awaits review or review is underway. |
| `WAITING` | Work awaits a reply, decision, dependency, event or the user's intervention. |
| `PAUSED` | Work was deliberately put aside. |
| `DONE` | The thread's agreed scope is complete and verified, and the PR merge gate below holds. |
| `CANCELLED` | Work was closed without completing its agreed scope. |

Once triage settles the scope, work moves straight to `WORKING`, or to `REVIEW`
when the work is a review; a thread that cannot start yet is `WAITING` for what
it needs. Record in the task plan what a waiting thread waits for.

Show `REVIEW` when the user is asked to review a result and `WAITING` when the
user owes an action such as a reply, approval, decision, manual merge or an
intervention to unblock work. Hide the label for internal
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
the replacing PR must be merged. This does not cover a publication that selected
managed delivery requires: it keeps the thread out of `DONE` until the user
chooses another scope or `CANCELLED`. If the remote state cannot be read, report
it unverified and withhold `DONE`.

A thread that opened no PR needs no extra step when its recorded scope excludes
publication, such as read-only, investigation or review work. Any other editable
thread needs an explicit no-PR decision in its task plan. A PR the plan marks optional under the user's recorded scope decision is
such a decision; marking it optional after opening it, without that decision,
is not. Opening a PR after `DONE` reopens the thread to `WORKING`, `REVIEW` or
`WAITING` by its actual next action. A PR a child opened gates the coordinator
when its deliverable depends on it.

Compute progress from currently accepted milestones in the existing task plan,
with task-appropriate weights when outcomes differ substantially. It covers the
whole deliverable, including children's accepted work, required independent
review and integration; count nothing twice. Elapsed time, token consumption and worker
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
milestones. `WAITING` and `PAUSED` retain
accepted progress; `CANCELLED` retains partial progress and never implies completion.

Update display titles on meaningful milestone, phase, blocker or next-action
owner changes during existing handling, without extra model turns, polling or
a progress registry.
For naming-only requests, change display metadata without enabling Workstreams, starting work or altering task
state. Use supported controls; do not bulk-rename unrelated threads.

Display titles grant no authority, do not alter native identity or lineage, and
do not qualify native execution or authorize new work.
