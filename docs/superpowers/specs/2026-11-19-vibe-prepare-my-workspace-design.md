# vibe-prepare-my-workspace

## Status and intent

The user approved bringing the earlier plugin's `prepare-my-workspace` skill
into this repository as `vibe-prepare-my-workspace`, rebuilt in the shape below.
This document does not authorize installing anything on a real machine.

Two moments, one skill:

1. **Set up a new machine:** bring the partner toolset to working from nothing.
2. **Catch a partner tool that is missing or outdated:** report it and fix it.

The skill partners with existing tools by installing them through their own
official instructions. It never copies, wraps or replaces how a partner ships.

Reference: `/Users/fcouto/dev/git/old-vibe-workflow/skills/prepare-my-workspace/`
(`SKILL.md`, `references/procedure.md`, `references/upgrade.md`) and
`clients/workspace-requirements.json` in that repository.

## Goals

- One read-only check that reports every partner tool as a table:
  tool, state, installed version, upstream version, why it matters, reason.
- Detect "outdated" by comparing the installed or cached version with upstream.
- Prepare mode installs or updates only the items the user approved.
- OpenCode now; adding Codex or Claude Code later is data, not new code.

## Non-goals

- Hooks, upgrade records, run directories, `workflow.py`, startup lines,
  expected-version records or the old 100-line upgrade procedure.
- Project onboarding or project dependencies. Anything not on the partner list
  is reported as out of scope, never installed.
- Codex or Claude Code support in this delivery.
- Scheduled, background or automatic checks and updates.

## Anti-goals

Failure even if it technically works:

- Becoming a package manager or maintaining copies of partner install steps.
- Installing, updating or editing configuration without approval of that
  specific list.
- Reporting `ready` for anything that was not observed.
- Changing unrelated configuration, permissions, models, credentials or plugins.

## Approaches

- **A. Skill and partner list only.** Least code, but results depend on each
  session's interpretation, and nothing is testable.
- **B. Skill plus a read-only check script — selected by the user.** Repeatable,
  unit-testable checks; installing stays with the agent and the user's approval;
  client-neutral, so new clients are data.
- **C. Check exposed as a plugin tool.** Structured, but ties the check to the
  OpenCode plugin and puts network lookups in the plugin process, against
  "others by design".

## Partner list

Plain data in the skill folder (`partners.json`). Each entry: `id`, `why`,
`docs` (official install/update instructions URL), how to read the local version
per client, and the upstream source to compare against.

Approved entries:

| id | Machine level | OpenCode integration | Upstream |
|---|---|---|---|
| superpowers | none | plugin `superpowers@git+https://github.com/obra/superpowers.git` | GitHub default branch |
| ponytail | none | plugin `@dietrichgebert/ponytail` | npm registry |
| caveman | none | local plugin `./plugins/caveman` and its skills | its source repository |
| impeccable | none | skill folder `~/.config/opencode/skills/impeccable` | its source repository |
| context7 | none | MCP server entry | hosted; presence only |
| rtk | command on `PATH` | its OpenCode hook (`plugins/rtk.ts`) | its releases |
| graphify-cli | command on `PATH` | none | its releases |
| graphify-skill | none | its OpenCode skill | tied to graphify-cli |
| git, gh | command on `PATH` | none | minimum version only |
| python | `python3` 3.11+ on `PATH` | none | minimum version only |

`graphify-skill` restores the earlier list's separate item, which the first
draft dropped by accident.

Builder verifies each upstream and docs URL from the partner's own pages.
`vibe-workflow` itself is not on the list (my call: the skill only runs once it
is installed, and its own updates are a separate concern).

## Scope calls

- **Presence and minimum for git, gh and python.** macOS git usually comes from
  Apple's Command Line Tools and always trails upstream; an upstream comparison
  would show `outdated` forever and keep offering a second git. Homebrew is used
  when present but is not a partner the skill installs. (My call.)
- **Global OpenCode configuration only.** Read and edit
  `~/.config/opencode/opencode.json(c)`, including both its `plugins` and legacy
  `plugin` keys. Installs go to user scope, never a project. (My call.)

## States

A tool with an OpenCode integration is `ready` only when the machine-level
piece exists **and** OpenCode is wired to use it (for rtk, its hook; for
graphify, its skill). A binary with no integration is `missing` for the
integration, never `ready`. (User's choice.)

- `ready`: present, integrated where an integration exists, and not behind
  upstream (or meets the minimum).
- `missing`: not found, or found without its OpenCode integration.
- `outdated`: installed or cached version is behind upstream, or below minimum.
- `unknown`: could not determine; always carries a reason (lookup failed,
  unreadable version, timeout). Never treated as ready.
- `not-supported`: the partner has no official support for the client.

## Check script

`check.py`, Python 3.11+ standard library only, like `scripts/select.py`.

- Reads `partners.json`, detects local versions with bounded subprocesses,
  looks up upstream over HTTPS with short timeouts, prints JSON (and a short
  table for humans). Exit codes: 0 all ready, 1 something needs action,
  2 something unknown.
- Read-only: never installs, edits configuration or writes files.
- Network: only the public metadata endpoints named in `partners.json`.
  Sends no credentials, tokens or local paths. A rate limit or offline lookup
  is `unknown` with the reason.
- Inputs injectable for tests: command runner, upstream fetcher, config paths.

## Skill flow

`SKILL.md` stays short and loads `references/procedure.md`.

0. **Python first.** `check.py` needs Python 3.11+, so it cannot report on
   Python itself. The skill runs `python3 --version` before step 1. If Python is
   missing or older than 3.11, check mode reports that gap and stops; prepare
   mode offers a Python-only install for approval (as in step 3), then continues.
1. **Check.** Run `check.py`; show the table. Check mode stops here.
2. **Tell.** List what is missing or outdated, why each matters, and the user
   actions to expect (sign-ins, trust prompts, restarts, costs).
3. **Approve.** Ask the user to approve the specific install/update list.
   No answer is not consent. The user may narrow the list.
4. **Back up.** Before editing a client configuration file, copy it to
   `${XDG_STATE_HOME:-~/.local/state}/vibe-workflow/workspace-backups/<timestamp>/`
   (directory `0700`, my call) and report the path and how to restore.
5. **Install.** For each approved item, read the partner's official instructions
   now from `docs`, follow them at the narrowest scope, and add no duplicates.
   An unreadable source leaves the item `missing` with the reason; never fall
   back to memory, a stale copy or another client's steps.
6. **User actions.** One consolidated list. Never sign in, approve trust or enter
   passwords for the user.
7. **Verify and report.** Run `check.py` again. Report each item's before/after
   version and state, backup paths and open user actions. A plugin change that
   only a new session can load stays pending until observed.

## Packaging and registration

- Register the skill through the plugin's skill transform, as
  `vibe-onboard-project` is registered today.
- Add the skill files to `scripts/manifest.py`, `package.json` `files` and
  `tests/test_package.py`.
- Keep the README short; any README change goes through the `impeccable` skill.

## Proof expected

- Unit tests with a fake runner and fake fetcher: each state, version parsing,
  timeouts and failed lookups produce `unknown` (never `ready`), and no network
  or subprocess beyond the injected ones.
- A test that `check.py` writes nothing.
- Package export includes the new skill files and validates.
- No real install runs during development; live checks are reported separately
  from unit proof.

## Left to the builder

- Exact `partners.json` schema and how per-client entries are keyed.
- How to read each local version (OpenCode cache layout, CLI `--version` output).
- Comparing unpinned git sources (commit vs version) against upstream.
- Whether `check.py` lives in the skill folder or `scripts/`, given the
  package allowlist.
