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

OpenCode asks whether to use Smart Router, who merges `[READY]` pull requests,
and whether to clean up a task's worktree and local branch after a merge. If
GitHub's auto-delete of merged branches is off, it also offers to turn it on.
Last, it asks whether to show a five-step meter in the session title, with
`WAITING` while the chat needs you. This is opt-in and off by default.
OpenCode says what it will save, then saves your answers; your client's own edit
permission still applies. The choices live in `.vibe-workflow/settings.json`. In a
linked worktree, the Smart Router file stays in the worktree and the git
workflow choices are saved in the main checkout.

Reminders are advisory, not enforced. Agents open PRs as `[BLOCKED]` drafts, mark
them `[READY]`, keep the main checkout on a current `main`, and clean up only
when chosen and after a verified merge.

## Prepare your workspace

Ask: **“Use `vibe-prepare-my-workspace` to check my workspace.”**

It reports whether superpowers, caveman, ponytail, impeccable, context7, rtk,
graphify, git, gh and Python are present and current. It installs or updates
only what you approve, and backs up your OpenCode config first.

## Ask the council

Ask: **“Use `vibe-council` to review this plan.”** or run
`/vibe-council <what to review>`.

Each member reviews on its own and reports back. The default members are Astra
(`openai/gpt-6.2-astra`), Opus (`claude-agent/opus`) and Fable
(`claude-agent/claude-fable-5-2`); you can change them per run. Members run as
OpenCode's `explore` agent, which cannot use edit tools or start subagents.
Shell stays available, so the brief tells members to stay read-only. Seats are
limited to the Claude, OpenAI and OpenCode Go plans Smart Router routes through;
no other model is substituted. Before it starts,
`vibe_council_roster` checks your accounts with the same local, token-free reads
as Smart Router, whether or not Smart Router is on. The record is saved in
`docs/vibe-council/sessions/<hash>_<topic>/` of the checkout where the work
happens, and is committed with it. The coordinator never votes; you decide.

## Scores

There is nothing to configure. Smart Router ranks models by public scores from
Artificial Analysis via OpenRouter, refreshed about weekly with no credentials.
Routes use your existing native logins, such as the Claude CLI.
When ChatGPT reports its usage limit, Smart Router pauses ChatGPT routes until
the reset time, read once from your Codex sign-in if Codex is installed (no
model call); otherwise it pauses them for one hour.

## Pause or remove

- Pause: run onboarding again and choose to keep your existing model selection.
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
