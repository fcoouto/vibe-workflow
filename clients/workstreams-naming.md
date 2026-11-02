# Names identify the parent and its children

For adoption, event repair and manual edits, read [title synchronization](workstreams-titles.md).

Use this display title, with exactly five meter cells and one ASCII space
between the meter and family code:

```text
<meter> [W<number>] <P|C<number>> · <short_topic> · <STATUS>
```

```text
▰▰▱▱▱ [W1] P · Thread naming · WORKING
▰▰▰▰▰ [W1] C1 · Naming rules · DONE
▰▰▰▰▱ [W1] C2 · Status tags · REVIEW
▰▰▰▱▱ [W1] C3 · Search support · BLOCKED
▰▰▰▱▱ [W1] C4 · Client check · WAITING
▱▱▱▱▱ [W2] P · Another workstream · TRIAGE
```

The literal brackets enclose the shared family code. `P` identifies the parent;
`C1`, `C2` and subsequent positive ordinals identify distinct children, including
reviewers. Keep each family's parent and child identities stable through fixes,
renames, replacement attempts and status changes. Never reuse a child ordinal for
a different context. A coordinator parenting its own workers uses `P` in its own
family; record any relationship to an enclosing family in the existing task plan.

## Allocate a family once across projects

Before naming a new family, reserve its code with the package helper:

```text
python3 -I -B <package>/scripts/workstreams_family.py --client codex --parent <native_parent_id>
```

Use the actual client identifier and stable native parent identity, never a title
or checkout path. The helper returns `{"ok": true, "family": "W1"}`. It serializes
allocation in `~/.vibe-coding-workflow/workstreams-families.sqlite3`, shared across
all projects, linked checkouts and package copies for this OS user. The same
client/parent returns the same code; distinct parents receive different codes.
Keep this register when a family completes, is cancelled or is archived. Never
reset it, delete allocations or choose a project-local counter. It stores identity
bindings only; progress and acceptance stay in the existing task plan.

Record the returned code and each child ordinal with existing task/native bindings.
For existing native contexts, preserve their immutable creation markers and record
the display-family mapping. A title alone cannot authorize adopting an old code.
On allocation failure, report the naming limitation; do not guess an ID or reset
the register. This allocator covers projects sharing the user register, not separate
machines or OS accounts; those require one coordinated allocation authority before
claiming a shared namespace.

The intended search keys are `[W1]` for a family and `[W1] C` for its children.
Verify literal matching on the current client before promising those search results;
search behavior and proportional fonts may prevent exact filtering or alignment.
Keep topics short so trailing statuses remain visible. Shorten only standard
activity words: `Implementation` becomes `Impl.` and `Refactoring` becomes
`Refactor`, regardless of original capitalization. Keep task-specific identifying
words in full. Leave `Review`, `Test`, `Fix`, family/member codes and statuses
unchanged. Keep each topic stable through fixes; add a distinguishing suffix only
when needed. Reviewer topics start with `Review`.

## Status and accepted progress

Use exactly one uppercase status at the end of every display title:

| Status | Use when |
| --- | --- |
| `TRIAGE` | A thread has just been created and scope needs assessment. |
| `READY` | Scope and prerequisites are clear and work can start. |
| `WORKING` | Work is underway. This describes active task effort. |
| `REVIEW` | A result awaits review or review is underway. |
| `WAITING` | Work awaits an expected reply, dependency or event. |
| `BLOCKED` | Work cannot continue without intervention. |
| `PAUSED` | Work was deliberately put aside. |
| `DONE` | The thread's agreed scope is complete and verified. |
| `CANCELLED` | Work was closed without completing its agreed scope. |

Compute each thread's progress from currently accepted milestones in its existing
task plan, with task-appropriate weights when outcomes differ substantially.
Parent progress covers the combined deliverable, including required independent
review and integration; do not average child meters or count work twice. Child
progress covers its own scope. Elapsed time, token consumption and worker
self-reports do not establish acceptance. Reopened or invalidated milestones stop
counting and may reduce the meter or reopen a `DONE` thread.

Fill `▰` cells from the left and use `▱` for remaining cells. Each filled cell
represents a completed 20% step; round down and cap unfinished work at four cells.
Use `▱▱▱▱▱` until there is a measurable plan. Initial status is `TRIAGE`.
Reserve `▰▰▰▰▰` for verified `DONE`, keeping both the full meter and that label.
Show no numeric percentage or `% done`. Status alone sets no intermediate progress:
`REVIEW` is not automatically four cells. `BLOCKED`, `WAITING` and `PAUSED` retain
accepted progress; `CANCELLED` retains partial progress and never implies completion.

Update display titles on meaningful milestone, phase or blocker changes during
existing handling, without extra model turns, polling or a progress registry.
For naming-only requests, change display metadata without enabling Workstreams,
starting work or altering task state. Apply this convention to requested existing
titles through supported controls; do not bulk-rename unrelated threads.

Internal responsibilities remain coordinator, deliverable owner and independent
reviewer, assigned from each objective. Models/reasoning stay in metadata. Title
changes grant no authority and do not alter immutable native identity.

Use supported client display-title controls. Preserve native creation markers,
lineage and task ownership. If a client cannot expose the requested parent/child
association, report that limitation before dispatch. Display titles do not qualify
native execution or authorize new work.
