# Title Sync: show `WAITING` only when nothing is still running

## Status and intent

Issue #379: a session title says `WAITING` while there is nothing to wait for. A real session wrote
its design, dispatched a subagent to check it, and ended its turn; the title read
`▰▱▱▱▱ WAITING · …` while the subagent was still running. The user asked for a check that the
session is idle before the label is set.

Today `WAITING` is `ended && cells < 5` at the end of every root turn (`src/session-titles.js`,
`evaluate`). It never asks whether anything the session started is still running. The 2026-11-20
design accepted this ("a root turn can end while a background child still runs; the label then
shows until the child's report wakes the session"). This change reverses that decision.

Cause, verified in the code and the screenshot:

- A root turn ends (`session.execution.succeeded`) while a background child session still runs.
- The plugin already receives the start and end events of children and ignores them (a child has no
  binding).

Amendment, 2026-11-23, after the first live run: the original design read `ctx.session.active()`.
The real plugin context (OpenCode 2.3.2) has no such method; its session methods are `hook`, `list`,
`create`, `get`, `remove`, `switchAgent`, `switchModel`, `prompt`, `generate`, `command`, `compact`,
`synthetic`, `interrupt`, `update`, `move`, `wait`, `context` and `instructions`. The code caught the
error and fell back to "nothing runs", so the live run still showed `WAITING` while a subagent ran.
Verified from inside the plugin on the same version, with no model call: `ctx.session.list({ parentID,
limit, order })` returns `{ data, cursor }` of full session objects and an unknown parent gives an empty
list; `Session.Info.time` carries `busy` and `idle` (a session that is processing has `busy` and no
`idle`, a fresh one has neither). The design below uses those.

## Goals

- At the end of a root turn, add `WAITING` only when no descendant session of that root is running.
  The meter is still written at every turn end, with or without the label.
- When the last running descendant ends and the root stays idle, the label appears (a child that
  finishes without waking the root must not leave the title unlabeled for good).
- When a child's report wakes the root, the existing start/end events decide, as today.

## Non-goals

- No new label text (no `RUNNING`), no change to the cell rules, the guards, the storage or the
  topic rules.
- No change to what a `WAITING` title means when it does show: the root has stopped and the task
  is not done.
- PR B (record ownership across plugin instances) is separate.

## Anti-goals

- A title that never gets `WAITING` because a read failed or a child never reported: a failed or
  missing `active` read counts as "nothing running" (today's behavior), never as "something runs".
- A title write at tool time, or any extra work on turns of unbound sessions: the walk up the
  parents happens only for a bound root's turn end and for the end of a session that has a parent.
- Blocking a turn: every read is bounded and failures are silent, like the rest of the plugin.

## Approaches

- **A. Check the running descendants at the turn end, and re-evaluate the root when a child ends
  (chosen).** One authoritative read of the host's active sessions; no state of our own to go stale.
- **B. Check only that the root itself is idle.** Rejected: the root's own turn has just ended, so
  this does not change the reported case. The child is what is still running.
- **C. Track child starts and ends from events alone.** Rejected: one missed end event leaves a
  child "running" forever and the label never appears; the host's own list cannot go stale.
- **D. Show another label while children run.** Rejected for now: a product change to the title
  format that nobody asked for; the issue asks for `WAITING` to be correct.

## Design

1. `activity(rootID, { ended, withSelf })`: list the root's children with `ctx.session.list({ parentID:
   rootID, limit: 50, order: "desc" })` and follow `cursor.next` (with `{ cursor, limit }`) for at most 4
   pages. A child runs when `time.busy` is a finite number, `time.idle` is not a finite number at or
   after it, and the newest of `time.updated` and `time.busy` is less than 10 minutes old. The age
   uses the newest of the two because a session that restarts sets `busy` without necessarily touching
   `updated`; the age rule itself keeps a busy mark left by a crash from hiding the label for good.
   Rows without a string `id` are ignored. Only direct children count: a running grandchild implies a
   running child in practice. With `withSelf` the root is judged by the same rule on `ctx.session.get`
   (only the child-triggered path needs it).
   In a child-triggered evaluation `ended` is `{ id, at }`, the session whose end event arrived at `at`:
   that session is treated as idle while its `time.busy` is not after `at`, so a host that updates
   `time.idle` a moment after the end event cannot hide the label, and a run that started after the
   event still counts. The child list and the root read are independent and share one 3-second
   deadline; a read that failed or has not answered by then counts as "nothing runs", and the other
   read's result is kept. A missing method or an odd shape does the same.
2. In `evaluate(sessionID, ended, fromChild)`, after the facts are read, the label becomes
   `ended && cells < 5 && !activity.children`. The check runs only after the existing guards (bound,
   not silenced, not fresh) and nothing is written after the plugin closed.
3. For the end of any session that is not itself bound (events already arrive for them), look up its
   root by walking `parentID` (at most 5 steps) when the feature is on; when that root has a binding,
   queue one more evaluation of it for the root's chain, carrying `{ id, at }`. Before any fact read
   it returns when a child still runs or the root itself runs (a woken root writes its own end of
   turn), and it checks the root again before writing. A session without a parent does nothing.
4. The saved record's `diag` is unchanged.
5. Docs: update the "WAITING label" and "Known gaps" text of the 2026-11-20 design to point here.

## Constraints

Node ES modules, standard library only; advisory only; tests first; no README, onboarding or injected
note change, so the live injection check is not triggered. The tests live under `tests/plugin/`.

## Known gaps (accepted until the live check says otherwise)

- A child that has run for over 10 minutes without any update reads as not running, so `WAITING`
  may show during a very long silent child (today's behavior).
- Beyond 4 pages (200 children) a running child is not seen; a root with that many subagents reads as
  not running while only an old one is active.
- A sibling that starts while a child-triggered evaluation reads facts still gets the meter written
  (without the label); the meter is correct either way.
- A short flash of `WAITING` if the child's end event is handled before the host marks the woken
  root active; the root's own start event removes it.
- Only direct children count; a running grandchild under an idle child is ignored. The walk up to
  the root for a child-end event still follows at most 5 parent steps.
- A child that runs after its root's turn ended and never ends (a stuck child) keeps the label off
  until it ends or is removed.

## Left to the builder

The test fake and the exact constants (page size 50, age 10 minutes, deadline 3 seconds, which are
this design's calls).

## Proof expected

- A root turn that ends with a running child writes the meter without `WAITING`; with no child it
  still writes `WAITING`.
- When that child ends and the root is idle, the label appears; when the root is active again, nothing
  is written.
- A running child counts; an idle or stale one, a child of another root and the root itself do not.
- A missing or failing `list`, or one that hangs past the deadline, leaves today's behavior.
- A child end for an unbound root, or a session with no parent, does no work.
- Live (the user approved one inference session): a real subagent turn shows no `WAITING` while it
  runs and `WAITING` after it ends. The first live run, on the `active()` design, failed this check.
