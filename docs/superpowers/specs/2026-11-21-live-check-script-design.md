# Live-check script

## Intent

Live checks of plugin behavior (session titles, skill-load notes) were run by hand three
times on 2026-11-20 with the same steps, and hand-written commands hit setup problems
(macOS has no `timeout`, zsh word splitting, short SHAs). A dev-only script makes the
check repeatable and always cleans up. Approved by the user 2026-11-21.

## Behavior

`python3 -B scripts/live_check.py --prompt TEXT [--prompt TEXT ...] [--settings JSON] [--origin URL] [--keep]`

1. Export the package from this checkout (`scripts/package.py`) into a temporary directory.
2. Clone this checkout into the temporary directory; exclude `.opencode/` locally.
3. Copy the package to `.opencode/plugins/live-check/` in the clone and rename its plugin
   id from `vibe-workflow` to `vibe-workflow-live` (exactly one occurrence), so it does not
   collide with an installed copy.
4. With `--settings`: write that JSON to `.vibe-workflow/settings.json`, commit it in the
   clone, and move the clone's local `origin/main` to that commit so the task diff starts
   empty. With `--origin`: set the clone's `origin` URL (for `gh` lookups); nothing is pushed.
5. Create one root session at the clone through `opencode api`, send each prompt in
   order, waiting for the session after each.
6. Print one JSON object: the session id, its final title, and the ordered events (tool
   calls with name, input and any `vibe-workflow:` note excerpt; assistant replies).
7. Always delete the session and the temporary directory, unless `--keep`.

All waits use Python timeouts; no `timeout` binary, no shell. The script removes its own
directory from the import path first, because `scripts/select.py` shadows the standard
`select` module that `subprocess` needs. Temporary directories are named
`vibe-live-check-*`. The script is never exported
(`scripts/manifest.py` is unchanged). Pure parts (`rename_plugin_id`, `summarize`) are
unit-tested; the native steps are not run in tests.

## Not in scope

Restarting the OpenCode server, simulating clients other than the API, and asserting
expectations (the caller reads the output).
