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

Still not observed: a note reaching the model in a live session. To observe it,
run one `gh pr create`, `gh pr ready` or `gh pr merge` after updating the plugin
and look for a `vibe-workflow:` line in its result. Do this in a normal OpenCode
session; no extra inference is needed beyond the command itself.
