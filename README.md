# Smart Router for OpenCode

Smart Router recommends a provider, model and effort before OpenCode delegates
work. It only advises: it never launches a worker, reserves quota or grants
spending authority. Your client still runs and controls every task.

## Requirements

- OpenCode V2
- Git
- Python 3.11+ on macOS or Linux

## Install

Add the package directory to the `plugins` array in `opencode.jsonc`, keeping
your other entries:

```jsonc
{
  "plugins": [
    { "package": "/absolute/path/to/package" }
  ]
}
```

Keep the whole directory; `index.js` needs the files beside it.

## Onboard a project

In the project's OpenCode chat, ask:
**“Use `vibe-onboard-project` to onboard this project.”**

OpenCode asks whether to use Smart Router, previews the change and saves only
after you confirm. The choice lives in `.vibe-workflow/settings.json`. In a
linked worktree, the question names the main project and the file stays in the
worktree.

## Configure routes

Smart Router selects nothing until you configure it. In
`~/.local/state/vibe-workflow/smart-router` (under `$XDG_STATE_HOME` when set),
create these files from `config/`:

- `router.json` from `router.example.json`: set `enabled` to `true` and list
  path IDs from `routes.json`.
- `strategy-table.json` from `strategy-table.example.json`: add the routes you
  have verified for your accounts.

Keep the directory private (`0700`) and the files private (`0600`). Routes use
your existing native logins, such as the Claude CLI.

## Pause or remove

- Pause: set `enabled` to `false` in `router.json`.
- Remove: delete the plugin's entry from `opencode.jsonc`.

Credentials and state are left untouched either way.

## Development

```sh
python3 -B -m unittest discover -s tests
node --test tests/*.test.js
python3 -B scripts/validate.py
python3 -B scripts/package.py --output dist
python3 -B scripts/validate.py --root dist
```
