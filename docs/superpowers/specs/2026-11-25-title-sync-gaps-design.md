# Title Sync: close six confirmed gaps between the title and the chat

## Status and intent

A review of Title Sync found places where a session title disagrees with what is happening in the chat. Each
finding was reproduced in a fresh lab: a private OpenCode 2.3.2 server, the plugin from `main` (15e8d88) under a
separate id, a `gh` shim, throwaway repositories and a small model (`claude-agent/haiku`, low effort). This design
covers the six findings the user chose to fix now. Two more (a catch-up re-read for stale idle titles, and a meter
that moves during a long turn) are under a separate investigation because their fixes cost more.

The user's standing preference is a lightweight workflow: on-demand reads, reuse of data already at hand, no
polling, no extra model calls. Every fix here adds no background work.

## Findings in scope (all reproduced)

| # | Finding | Lab evidence |
|---|---|---|
| F1 | A session moved between folders gets two plugin copies; the stale copy overwrites the title and record | Meter 4 → 2, the plan binding lost, the record's `location` flipped back. `execute.after` ran only in the copy for the session's current folder; both loaded copies received the events; on the first move into a folder the new copy loaded after that move's turn-end events. |
| F2 | The agent waits for the user mid-turn without `WAITING` | During a pending permission request and a pending question the session stayed running, no turn-end event, title without `WAITING`; the label appeared only after the answer ended the turn. `permission.asked`/`replied` and `form.created`/`replied`/`cancelled` reach plugins with the session id; the question tool creates a form. |
| F3 | A child inside one long tool call reads as idle; a root left busy by a shutdown reads as running | `WAITING` appeared from 634 s while a child was still in a 700 s `sleep` (`time.updated` never moved during the call). After a crash or stop, a root kept `time.busy` later than `time.idle`. All plugin copies share one process and the same start time. |
| F4 | A background shell makes the session look finished | `WAITING` showed from 3 to 42 s while a `sleep 45` background shell ran; the host then resumed the agent with a synthetic message. The shell tool result carries `metadata.shellID` and `status: "running"`; `shell.exited.data.id` is the same id. |
| F5 | A worktree removed before any branch was recorded leaves the meter stuck | The plugin read `feat/lab` with no upstream (`diag.branch`) but did not remember it; after merge and worktree removal the meter stayed at 4. |
| F7 | A spec written by a subagent never binds | Neither root nor child had a meter or record. A `write` control bound; an `edit` of an existing spec correctly did not. |
| F9 | A spec in one checkout and the work in another freezes the meter | Spec in the main checkout, plan and work in a linked worktree: 1 cell throughout; the plan write was ignored. |

## Goals

- A session's title is written by exactly one plugin copy, and moving a session never brings back older state.
- `WAITING` shows while the agent waits on the user mid-turn (a permission request or a form/question).
- A long child tool call never reads as idle, and a run killed by a server restart never reads as running.
- A pending background shell keeps `WAITING` off, as a running child does.
- A task's PR is still found after its worktree is removed, once the branch was seen at any turn end.
- A spec a subagent writes binds its root session; a plan written in the worktree adopts a task whose spec sits in
  the main checkout.

## Non-goals

- Re-reading idle sessions (late merges, crashed turns, the feature turned off) and refreshing the meter inside a
  long turn: under separate investigation.
- Binding on the `patch` tool (not testable in the lab), on edits, or on shell-written specs.
- Requests raised inside child sessions (whether they should label the root is unverified).
- Any change to cells, topic rules, `diag`, storage limits, notes, skills, onboarding or the README.

## Anti-goals

- Background work: no timers, polling, sweeps or model calls. New reads happen only on events the plugin already
  receives, and the new git calls only on a rare plan write.
- A title write during a session's first turn before the first meter: OpenCode generates its own title then, and a
  write that lands first would be read as the user removing the prefix.
- A failed or missing read that hides `WAITING` for good: every new read falls back to today's behavior.

## Approaches

- **F1.** (a) *Chosen:* one owner per session, the copy whose folder is the session's current folder; every copy
  drops its memory of a session on `session.moved`; the owner loads the stored record whatever folder saved it.
  (b) Re-read the stored record at every turn end: also correct, but costs a storage read per turn end and still
  needs an owner rule to stop two writers; (a) makes it unnecessary. The table shown to the user named (b); this
  design refines it to (a) for that reason. (c) Key records by folder: keeps both copies' state, does not stop the
  overwrite.
- **F2.** (a) *Chosen:* the five request events. (b) `execute.before`/`after` around the question tool: misses
  permission requests and forms raised by other tools.
- **F3.** (a) *Chosen:* "running" means busy since this server process started and not idle since. (b) A longer
  age limit: still wrong for longer calls and still needs a number. (c) Per-tool activity events: more events, more
  state, same answer.
- **F4.** (a) *Chosen:* track running background shells from the shell tool result; clear them on `shell.exited`.
  (b) Track `shell.created` metadata: needs a second event and the session id is inside metadata.
- **F5.** (a) *Chosen:* remember the branch seen at a turn end in a separate field used only once the checkout no
  longer shows a branch. (b) Store it in `remembered`: adds a `gh` call at every turn end of an unpushed branch,
  because `remembered` is looked up whenever there is no upstream.
- **F7.** (a) *Chosen:* bind a child's spec or plan write to its root when the same plugin copy owns the root.
  (b) Bind the child itself: children never show meters, so nothing would change for the user.
- **F9.** (a) *Chosen:* a plan written in another checkout of the same repository moves the task there, when the
  current task folder is not on a task branch. (b) Follow `session.moved`: misses agents that work in a worktree
  through absolute paths without moving the session.

## Design

All changes are in `src/session-titles.js` and `src/session-facts.js`, tested in `tests/plugin/`.

### 1. One owner (F1)

- `here`: the realpath of `ctx.location.directory`, computed once. With no location, the copy keeps today's
  behavior: memory only, and it treats every session as its own.
- `owns(info)`: `realpath(info.location.directory) === here` (realpaths cached, bounded).
- `load(sessionID)` (once per session per copy, as today) first reads the session; when this copy does not own it,
  it marks the session checked and returns. When it does, it accepts the stored record whatever its `location`
  (the field is still written, for diagnosis). The old "only the saving folder may load" rule goes.
- `bind`, `record` and every title write already read the session; each checks `owns` and does nothing otherwise.
  In `evaluate` the check uses the read that fetches the title, so a session that moved during a slow fact read is
  not written.
- On `session.moved` (new subscription), every copy forgets the session: `sessions`, `checked`, `saved`, pending
  requests. The next event in the new folder loads the record there. The move's own turn-end events may reach the
  new copy before it exists; that is harmless, because a move changes no task fact, and the next turn end updates
  the title.

### 2. Waiting requests (F2)

- New subscriptions: `permission.asked` (`data.id`, `data.sessionID`), `permission.replied` (`data.requestID`,
  `data.sessionID`), `form.created` (`data.form.id`, `data.form.sessionID`), `form.replied` and `form.cancelled`
  (`data.id`, `data.sessionID`).
- `pending`: session id → set of request ids (bounded per session and overall). An ask adds, a reply or cancel
  removes; each queues `relabel(sessionID)` on the session's chain.
- `relabel(sessionID, waiting)`: no git or `gh` reads. It runs only when the feature is on, the session is a bound
  owned root, not silenced, a meter was already written (`entry.last` set) and the task is not fresh. It reads the
  title, applies the existing silence rule, keeps the user's topic, writes `compose(topic, task.cells, waiting)` if
  it changed, and saves `last`. Here `waiting` is "any request pending".
- A turn start or end clears the session's pending set (a turn cannot start or end with a request pending; this also
  drops ids whose reply was missed).

### 3. Running since this server started (F3)

- `started = Date.now() - process.uptime() * 1000`, computed once per copy, with 2 s of slack.
- A child runs when `time.busy` is finite, at or after `started`, and no finite `time.idle` is at or after it. The
  10-minute age rule and the `time.updated` read go. The root's own check uses the same rule.
- The ended-run exclusion (`{ id, at }`) is unchanged.

### 4. Background shells (F4)

- In `execute.after`, a completed shell call whose `result.metadata.shellID` is a string and
  `result.metadata.status === "running"` is tracked: shell id → session id (bounded).
- `shell.exited` (new subscription) removes the id. When that session has no other tracked shell, the copy queues a
  check on its chain: unless the session or a child is running (the activity rule from §3), `relabel(sessionID,
  true)`. The host normally resumes the agent right after the exit; then the resumed turn's start removes the label
  again. A brief `WAITING` in between is accepted, because without the check a session that is not resumed would
  stay idle without `WAITING`.
- At a turn end, `WAITING` also requires that the session has no tracked running shell.

### 5. The branch seen at a turn end (F5)

- The stored task gains `seen` (string or null; a missing field loads as null).
- At each turn end, when the checkout exists and is on a branch other than the default, `task.seen` is set to it.
- `readFacts` takes `seen`. The PR is looked up by: the current branch when it has an upstream (today); otherwise
  `remembered` (today); otherwise `seen`, but only when the checkout is gone or not on a branch (detached by
  cleanup). An existing checkout on its unpushed branch therefore still makes no `gh` call.

### 6. Subagent writes bind the root (F7)

- In `bind`, a session with a parent resolves its root (the existing parent walk, at most 5 steps). The path
  resolves against the child's folder. The binding goes to the root when this copy owns the root; otherwise
  nothing happens (a documented limit; children normally share the root's folder).

### 7. A plan adopts the task (F9)

- In `bind`, a plan whose checkout differs from the task's moves the task to the plan's checkout when both share
  one git repository (`git rev-parse --path-format=absolute --git-common-dir` equal in both) and the task's current
  checkout is not on a task branch (`branchOf` returns null). The spec, cells and recorded branches are kept; `top`
  and `plan` change. Otherwise the plan stays ignored, as today. Cost: up to three git calls per plan written in
  another checkout.

## Known gaps (accepted)

- A child in a different folder from its root cannot bind the root (another copy owns the root).
- A shell tracked by one copy is unknown to the next owner after a move; that session's next turn end may show
  `WAITING` while the shell still runs.
- A child's background shell or permission request does not keep the root from `WAITING`.
- A session stuck busy within one server run (a host fault) keeps `WAITING` off until the server restarts.
- A brief `WAITING` between a background shell's exit and the resumed turn.

## Constraints

Node ES modules, standard library only; advisory only (reads git, never writes it); tests first; the existing
guarantees stay: an unchanged turn writes nothing to storage, nothing is written after close, the title is never
written at tool time for binding, child sessions never get their own meter.

## Proof expected

- Unit: a two-copy fake host (two `registerSessionTitles` with different locations, one shared storage, both
  receiving every event, hooks run by the owner) reproduces F1 and shows the fix; request events add and remove
  `WAITING` without git reads; a child busy for 11 minutes still counts as running and a root busy before the server
  started does not; a running background shell holds the label and its exit sets it; a removed worktree with a
  seen branch reaches 5 cells and an existing unpushed checkout makes no `gh` call; a child's spec write binds the
  root; a plan in a linked worktree of the same repo adopts a main-checkout task, one in another repo does not.
- Live, in the same lab: rerun the scenarios that confirmed F1, F2 (permission and question), F4, F5, F7 and F9
  against the changed plugin, and F3 with the 13-minute child; each must now show the expected title.
