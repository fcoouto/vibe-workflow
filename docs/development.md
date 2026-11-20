# Development

This branch contains only the OpenCode advisory selector. Python 3.11+ standard
library and JavaScript ES modules are sufficient; no install or build step exists.

Work on task branches in separate worktrees. Preserve local changes, credentials,
native configuration and other plugins. Do not install, activate, publish or run
inference without authorization covering that action.

Write behavior tests before nontrivial changes. Validate, commit, then run:

```sh
python3 -B -m unittest discover -s tests
node --test tests/*.test.js
python3 -B scripts/validate.py
python3 -B scripts/package.py --output dist
python3 -B scripts/validate.py --root dist
```

Use a new output path for each export. Keep each Python module within 1000 lines.
There is no linter, type checker, package manager, CI or release process here.
Test native readers only with synthetic transports and isolated private homes.
Report deterministic package checks separately from observed native execution.
Nontrivial changes require independent behavior/security review before acceptance.

`scripts/manifest.py` owns the complete runtime export list.
`docs/dependencies.md` records the extraction boundary and original source.
The extraction plan and design retain the task's explicit local-only delivery
and authorization. Its original workflow policy remains pinned for that task;
removing old product instructions does not waive its completion gates.

## The pull request hook: what is known

Read from the installed OpenCode's own code (2.2.6) and its database, not from a
live run of the hook:

- `execute.before` and `execute.after` run in one pipeline, for every tool call the
  session makes. Shell commands can arrive rewritten by rtk's own `execute.before`
  hook (for example `rtk gh pr ...`), so the classifier accepts an `rtk ` prefix.
- `execute.after` receives `{ tool, sessionID, agent, messageID, id, input, status,
  result }`. `input` is the input after earlier hooks, so commands arrive as
  `rtk gh pr ...` and `rtk git ...`.
- For the shell tool `result` is `{ content, output?, metadata }`, where `content`
  is an array of `{ type: "text", text }` parts and `metadata` is
  `{ status, truncated, exit }`. A failing command still has `status: "completed"`
  with a nonzero `exit`.
- The model is given `content` unchanged when it is a non-empty array or a string,
  and the stringified `output` only when `content` is empty. A note therefore has to
  go into `content`; text added to `output` is never shown. (Found when the first
  live `gh pr create` produced no note: the handler had appended to `output`.)

Observed live (OpenCode 2.2.6, 19 November 2026, plugin at `77274f2`): after the
fix, a note reaches the model after `gh pr edit <n> --title "[READY] ..."` (case 3),
after `git push` once a pull request was created or readied in the session (case 2)
and after `gh pr create` (case 1). Cases 4 (`gh pr merge`) and 5 (checkout in the
primary) share the same append path but have not been observed. Other OpenCode
versions are unverified. To repeat the check, run one matching command and look for
a `vibe-workflow:` line in its result.

## Session titles: what is known

The opt-in `session_titles` setting is described in
`docs/superpowers/specs/2026-11-20-session-titles-design.md`. Its "Evidence
(2026-11-20)" section records the session timelines and the throwaway-plugin probe it rests on, and its
"Known gaps (documented, not closed)" section lists what is not verified. Read
those two sections before changing `session-facts.js` or `session-titles.js`; this
file does not copy them.

Observed live (OpenCode 2.3.2, 20 November 2026): the packaged plugin, exported
from branch commit `04a0104`, was loaded as a project-local copy in a throwaway
local clone, with one API-created session and no push. Each quoted title starts
with the invisible U+FFFB marker. The title was empty at creation; the first turn
end wrote `▰▱▱▱▱ WAITING · live probe`, and it stayed unchanged 20 seconds later.
Mid-turn the `WAITING` label was removed, and later turn ends showed `▰▰▰▱▱` and
`▰▰▰▰▱` with `WAITING`. A background child kept its own title, and the root title
was unchanged after the child's report woke it. After the checkout moved to a
branch whose pull request #346 was already merged on GitHub, the turn end showed
`▰▰▰▰▰` (cell 5) through a real `gh pr list` call from the plugin process.

Not observed:

- OpenCode's own title generation: the API-created session never got a generated
  title, so the first-turn race was not exercised. Desktop and terminal sessions
  are unverified for that race.
- Cell 2 on its own: turn 2 wrote the plan and the first declared file in one turn,
  so the meter went from 1 to 3 cells.
- The cleanup-on path: the clone's `git_workflow` had no `worktree_cleanup`, so
  cleanup counted as satisfied; worktree removal holding or releasing cell 5 was
  not exercised.
- A real merge: cell 5 came from a branch whose pull request #346 was already merged
  on GitHub, reached through a local-only upstream ref; no task was taken through
  to a merge.
- A sample taken while the background child was still running: the root's `WAITING`
  during the child's run was inferred from the turn-end title.

Other versions are unverified.
