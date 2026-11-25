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
  drops its memory of a session on `session.moved`; the owner loads a stored record saved by any folder of the same
  project.
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
- **F9.** (a) *Chosen:* the first plan of a task, with the spec's topic, written in another checkout of the same
  repository moves the task there. (b) Also require the old checkout to be off a task branch: that read fails
  ambiguously and the spec's checkout can change branch before the plan arrives; the topic match identifies the task
  better. (c) Follow `session.moved`: misses agents that work in a worktree through absolute paths.

## Design

All changes are in `src/session-titles.js` and `src/session-facts.js`, tested in `tests/plugin/`. Host facts used
below were checked on OpenCode 2.3.2: a session located in a subfolder gets its own plugin copy for exactly that
folder; the main checkout, its subfolders and its linked worktrees share one `ctx.location.project.id`; `session.moved`
carries the new `location.directory`.

### 1. One owner (F1)

- `here`: the realpath of `ctx.location.directory`, computed once; `project`: `ctx.location.project.id`. A copy with
  no location keeps today's behavior (memory only, every session its own), so the one-owner guarantee applies to
  hosts that supply a location; OpenCode 2.3.2 always does.
- `owns(info)`: `realpath(info.location.directory) === here` (realpaths cached, bounded). A failed session read or
  realpath means "unknown": the copy does nothing this time and does not remember the session as checked, so the
  next event retries.
- `load(sessionID)` reads the session first and returns early for a session it does not own. An owner accepts a
  stored record when `record.project` equals its project id; a record without `project` (saved before this change)
  only when `record.location === where` (today's rule). Records from another project are ignored. `save` writes
  `project` as well as `location`.
- `bind`, `record`, `relabel` and `evaluate` check `owns` on the session read each already makes; in `evaluate` it is
  the read that fetches the title, so a session that moved during a slow fact read is not written.
- A per-session generation number in each copy: forgetting a session bumps it. A job notes the generation when it
  is QUEUED (not when it starts), and checks it before publishing a loaded record into memory, before a title
  update and before a save; on a change it does nothing. A child bind also re-checks root ownership after the parent
  walk. A title update or storage write already sent is not recalled; the guarantee is that no NEW write starts after
  a move or a close (the window is the time of one session read or one storage call). Generations
  are kept only for the last 256 forgotten sessions; dropping an older one could only matter to a job that waited
  through 256 moves.
- `session.moved` (new subscription): every copy forgets the session (memory, checked, saved snapshot, pending
  requests, generation bump), with no I/O. Nothing is evaluated on the move itself: when the destination copy is
  already loaded it receives the move's own turn-end event and evaluates as owner then (the source copy, owner at
  the move's start, may have removed `WAITING`; that end restores it); when the destination copy is not loaded yet
  it misses the move event too, so a move-time evaluation could not help. In that cold case (seen once in the lab,
  for a session moved while idle) the session's next turn start repairs the label and its next turn end the cells.
  A move the agent makes mid-turn applies within that turn, whose end is evaluated by the new owner.
- Cost in copies that do not own a session: one cold ownership read per session per copy lifetime (turn-end events
  carry no folder, so a copy must read the session once to learn it is not the owner), and none after that for the
  session's own turn events. Request events and shell exits act only on sessions bound in this copy's memory (no I/O
  otherwise). The child-end wake-up (`wakeRoot`) stays exactly as today, including its parent-walk session reads on
  the end of any session not in memory, so a root restored from storage after a reload is still woken by its child;
  those reads are today's cost, not new.

### 2. Waiting requests (F2)

- New subscriptions: `permission.asked` (`data.id`, `data.sessionID`), `permission.replied` (`data.requestID`,
  `data.sessionID`), `form.created` (`data.form.id`, `data.form.sessionID`), `form.replied` and `form.cancelled`
  (`data.id`, `data.sessionID`).
- `pending`: session id → set of request ids (at most 16 per session, bound sessions only; a 17th id is ignored, so
  with more than 16 open requests the label may clear before the last one is answered). An ask adds its id and
  queues `relabel(sessionID, true)`. A reply or cancel removes its id only if present (an unmatched reply is
  ignored); when the set becomes empty it queues `relabel(sessionID, false)`, which writes only while the session is
  running (rule of §3), so an idle session's `WAITING` is never removed by a stray reply.
- `relabel(sessionID, waiting)`: no git or `gh` reads. It acts only when the feature is on, the session is a bound
  root this copy owns, not silenced, a meter was already written for this task (`entry.last` set and the task not
  fresh, which also covers a replacement task bound in the current turn). It reads the title, applies the existing
  silence rule, keeps the topic, writes `compose(topic, task.cells, waiting)` only if it differs, and saves `last`.
- The pending set is cleared at the root's own turn start and turn end (a blocking request cannot outlive them),
  never by a child-triggered evaluation. Requests raised in child sessions are not tracked. A missed reply leaves
  `WAITING` until the root's next turn boundary (accepted: the alternative is polling). A non-blocking form created
  while a turn runs shows `WAITING` while the agent keeps working, until it is answered or the turn ends.

### 3. Running since this server started (F3)

- `started = Date.now() - process.uptime() * 1000`, computed once per copy, with no slack: a run is started by the
  server after its process starts, so its busy time is never earlier. It is the same in every copy of one server
  process, including after a plugin reload (the process, and so its uptime, continue).
- `running(time)`: `time.busy` finite and at or after `started`, and no finite `time.idle` at or after `time.busy`.
  Used for children (replacing the 10-minute age rule and the `time.updated` read) and for the root.
- Claim: a run killed by a server restart reads as not running (observed busy times support it). A run OpenCode
  resumes after a restart is expected to get a new busy time and read as running, but no real resume was observed
  (none happened within 70 s in the lab); if a resumed run kept its old busy time it would read as not running, and
  its root could show `WAITING` while it runs, until that run ends. The ended-run exclusion (`{ id, at }`) is
  unchanged.

### 4. Background shells (F4)

- In `execute.after`, a completed shell call whose `result.metadata.shellID` is a string and
  `result.metadata.status === "running"` is tracked: shell id → session id (at most 64; a 65th drops the oldest
  tracked shell, whose session may then show `WAITING` early). An id that already exited
  (a bounded list of the last 64 exited ids) is not tracked, so a very fast command that exits before the hook runs
  cannot stay "running". The existing push/PR recording is unaffected, background or not.
- `shell.exited` (new subscription) removes the id. When the session has no other tracked shell, a check is queued
  on its chain: if neither the session nor a child is running (§3), `relabel(sessionID, true)` right away. The
  user chose this over waiting for the next turn: OpenCode normally resumes the agent within milliseconds, so a
  sub-second `WAITING` may flash, but a session that is not resumed never stays without `WAITING`. Unchanged writes
  are skipped.
- At a turn end, `WAITING` also requires that the session has no tracked running shell.

### 5. The branch seen at a turn end (F5)

- `readFacts` reports two new facts with positive evidence only: `gone` (the task folder's `stat` failed with
  `ENOENT`) and `detached` (a successful `git status` read showing a detached head). A failed read sets neither.
- The stored task gains `seen` (a branch name or null) and `bound` (the bind time in ms or null); missing fields load
  as null. At each turn end, when a successful status read shows a branch other than the default, `task.seen` is set.
- The PR lookup order is: the current branch when it has an upstream (today); otherwise `remembered` (today);
  otherwise `seen`, but only when the checkout is `gone` or `detached`. An existing checkout on its unpushed branch
  still makes no `gh` call, and a failed read holds the cells.
- A PR found through `seen` counts only if its `createdAt` (requested in the same `gh` call) is at or after the
  task's `bound` time, with no allowance; a task without `bound` does not use `seen`. This keeps a reused branch name
  from crediting a PR opened before the task. It cannot tell this task's PR from a later, unrelated PR on the same
  branch name; the lookup trusts the newest PR created after the bind. (The older risk through `remembered` is
  unchanged and stays documented.)

### 6. Subagent writes bind the root (F7)

- In the hook, a spec or plan write by a session with a parent is resolved first (the existing parent walk, at most
  5 steps, returning the root's session info); the bind is then queued on the ROOT's chain, so it cannot race the
  root's evaluation. The path resolves against the child's folder. The bind happens only when this copy owns the
  root; children normally share the root's folder (146 of 158 descendants in the user's largest real session did).

### 7. A plan adopts the task (F9)

- In `bind`, a plan in a checkout other than the task's adopts the task when all hold: the task has no plan yet;
  the plan's topic (date and `-design` stripped, as for specs; a plan is often written on a later day than its spec)
  equals the task's; and both checkouts report the same
  `git rev-parse --path-format=absolute --git-common-dir` (two successful reads; a failed read means no adoption).
- Adoption changes `top` and `plan`, keeps the spec and cells, and resets `remembered` and `seen` (branch evidence
  from the old checkout does not belong to the new one). It happens at most once per task because the task then has
  a plan. Cost: two git calls per plan written in another checkout.

## Known gaps (accepted)

- A child in a different folder from its root cannot bind the root (another copy owns the root).
- A session moved while idle, when the destination copy loads after the move's events: corrected at its next turn.
- A shell tracked by one copy is unknown to the next owner after a move; that session's next turn end may show
  `WAITING` while the shell still runs. A missed `shell.exited` keeps `WAITING` off for that session until the
  server restarts.
- A child's background shell or request does not keep the root from `WAITING`; a non-blocking form created while a
  turn runs is cleared at the turn's end.
- A session stuck busy within one server run (a host fault) keeps `WAITING` off until the server restarts.
- A sub-second `WAITING` may flash between a background shell's exit and the resumed turn (the user's choice).
- A reused branch name can still credit an older PR through `remembered` (unchanged), and a later unrelated PR on
  the same branch name through `seen`.
- Two specs with the same topic on different days are one topic to the plan adoption.
- More than 16 open requests, or more than 64 running background shells, can clear or set the label early.

## Constraints

Node ES modules, standard library only; advisory only (reads git, never writes it); tests first. Existing guarantees
stay: one queue per session within a copy, nothing written after close, an unchanged turn writes nothing to storage,
the title is not written at tool time for binding, child sessions never get their own meter, cells never go down.
No new background work: every new read happens on an event the plugin already receives.

## Proof expected

- Unit, with a two-copy fake host (two `registerSessionTitles` with different locations and one project id, one
  shared storage, both receiving every event, hooks run only in the copy for the session's folder):
  - F1: the lab's sequence (spec bound in the main checkout, moved, plan bound in the worktree, moved back) keeps
    the plan and cells; a record from another project is ignored and a legacy record loads only in its folder; a
    failed ownership read retries on the next event; a non-owner makes one cold ownership read per session and no
    further reads for that session's own turn events, and no `gh` call or storage write, for turn ends, request
    events and shell exits (the existing child-end wake-up reads are counted separately); a move (destination
    taken from `data.location.directory`, source and destination copies both loaded) during a slow evaluation or a
    slow storage read writes neither title nor storage in the old copy and publishes no stale record, and the move's
    turn end is evaluated once, by the destination; a close during a delayed storage read or evaluation starts no new
    title or storage write; a
    child end that is the first event after a reload restores and wakes its stored root.
  - F2: ask → `WAITING` with no git reads; reply → removed while running; overlapping requests; cancel; an unmatched
    reply leaves an idle session's `WAITING`; a dropped reply keeps `WAITING` until the root's next boundary; no write
    before the first meter or for a task bound this turn; the set is cleared at the root's turn boundaries and not by
    a child-triggered evaluation; a 17th request is ignored.
  - F3: a child busy for 11 minutes counts as running; a busy time 1 ms before the server start reads idle for the
    child and the root, and 1 ms after reads running; two copies registered at different times compute the same
    start.
  - F4: a running background shell holds the label at a turn end; its exit with nothing running sets `WAITING`; two
    shells; an exit before the hook; a 65th shell drops the oldest; a background `git push` still records its branch.
  - F5 (fixtures carry `createdAt`): a removed worktree with a seen branch reaches 5 cells; with cleanup on, a
    detached but present checkout shows 4 and reaches 5 once removed; a failed `stat` or `status` read holds the
    cells; an unpushed existing checkout makes no `gh` call; a PR created 30 s before the bind does not count; the
    `remembered` path behaves as today.
  - F7: a child's spec write binds the root through the root's queue; a child in another folder does not.
  - F9: a same-topic first plan in a linked worktree adopts the task and resets the branch evidence, also when the
    main checkout switched branch between the spec and the plan; a different topic, a second plan, another
    repository, or a failed read does not.
  - The existing suite stays green, including "an unchanged turn writes nothing to storage".
- Live, in the same lab: rerun the scenarios that confirmed F1, F2 (permission and question), F4, F5, F7 and F9
  against the changed plugin, and F3 with the 13-minute child; each must now show the expected title.
