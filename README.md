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

OpenCode asks whether to use Smart Router and who merges `[READY]` pull
requests, previews the changes and saves only after you confirm. The choices live
in `.vibe-workflow/settings.json`. In a linked worktree, the questions name the
main project; the Smart Router file stays in the worktree, and the pull request
choice is saved in the main checkout.

Pull request reminders are advisory, not enforced. Agents open PRs as `[BLOCKED]`
drafts, mark them `[READY]`, and keep the main checkout on a current `main`.

## Prepare your workspace

Ask: **“Use `vibe-prepare-my-workspace` to check my workspace.”**

It reports whether superpowers, caveman, ponytail, impeccable, context7, rtk,
graphify, git, gh and Python are present and current. It installs or updates
only what you approve, and backs up your OpenCode config first.

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
