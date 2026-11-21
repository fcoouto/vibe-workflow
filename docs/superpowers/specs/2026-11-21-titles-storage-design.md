# Session titles survive a restart

## Intent

Session titles kept each session's task binding in memory, so an OpenCode server restart
(twice in one session on 2026-11-20/21) froze every meter until the next spec write. The
user chose to keep the binding in OpenCode's plugin storage (`ctx.storage`) over rebuilding
it from session history, because the plugin API only exposes messages since the last
compaction (`ctx.session.context`). Approved 2026-11-21. This reverses the 2026-11-20
"memory only" choice, which had been accepted as a known gap.

## Behavior

- Key `session-titles/<sessionID>`, value `{ location, at, task, last, silenced }`, where
  `location` is this plugin instance's `ctx.location.directory` and `at` is the save time.
- Save after every change to a session's binding (bind, rebind, plan set, cells, last
  written title, silenced). A session with no task and not silenced is removed from storage,
  so reaching cell 5 deletes its record; a silenced session stays stored so the user's
  removal of the prefix survives a restart.
- Load lazily: the first time a session is seen (a spec or plan write, or a turn event),
  read its record once per server lifetime; only a record whose `location` equals this
  instance's is used, so another project's plugin instance never acts on it. Unknown
  sessions cost one storage read per server lifetime, and only in projects where session
  titles are on.
- Cap: at most 128 stored records; when a new session is saved and the count exceeds it,
  the oldest by `at` are removed.
- Any storage error falls back to today's memory-only behavior; nothing throws.

## Not in scope

Rebuilding from session history; migrating titles written before this change (their
sessions bind again on the next spec write, as today).
