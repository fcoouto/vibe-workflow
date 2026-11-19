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

## Observing the pull request hook once

The pull request notes use `ctx.tool.hook("execute.after")`, which is documented
for OpenCode V2 but has not been observed on the installed version, and no
inference may run without authorization. To observe it once, add a throwaway
plugin that registers the same hook and appends each event's keys and
`input` keys to a file under `$TMPDIR`, load it in a disposable OpenCode home, run
one trivial shell command in a session you start, and read the file. Check the
event has `tool`, `status`, `sessionID`, `input.command`, an `input.workdir` or
`cwd`, a `result` that can be appended to, and any exit field. Remove the plugin
afterwards. Until then the hook stays reported as unverified.
