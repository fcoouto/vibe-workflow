# Prepare or verify the workspace

Two modes: **verify** only reports; **prepare** also installs or updates what the
user approves. A request to inspect or plan is verify, plus a proposal of what
preparation would do. Project setup is `vibe-onboard-project`, not this skill.

Scope: OpenCode, the global `~/.config/opencode/opencode.json` or `.jsonc`, user
scope only. The partner list is `partners.json` beside this skill. A tool that is
not on it is out of scope: report it, never install it.

0. **Python first.** Run `python3 --version`. 3.11 or newer: continue.
   - Done when: the version is known.
   - On failure (missing or older): verify mode reports the gap and stops. Prepare
     mode proposes a Python-only install from the `python` entry's `docs`, asks for
     approval as in step 3, then continues.
1. **Check.** Run `python3 -I -B <this skill's directory>/scripts/check.py` (add
   `--json` for structured output). It reads only; exit 0 ready, 1 something needs
   action, 2 something unknown. Show the table. Verify mode stops here.
   - Done when: the table is shown.
   - On failure: report each `unknown` with its reason. Never treat it as ready.
2. **Tell.** List what is missing or outdated and why each matters (the row's
   `why`), and the user actions to expect.
   - Done when: the message is shown.
3. **Approve.** Ask the user to approve the specific install and update list. No
   answer is not consent. Honor a narrower list.
   - Done when: the user approved a list, or declined.
   - On failure: change nothing.
4. **Back up.** Before editing the OpenCode configuration file, copy it to
   `${XDG_STATE_HOME:-~/.local/state}/vibe-workflow/workspace-backups/<timestamp>/`,
   created with mode `0700`. Report the path and how to restore it.
   - Done when: the copy exists.
   - On failure: skip configuration edits and say so.
5. **Install.** For each approved item, read the partner's official instructions
   now from its `docs` page. Follow them at user scope. Keep the user's settings,
   permissions, models and other plugins, and add no duplicate of what OpenCode
   already loads.
   For a skill partner whose `docs` page is on skills.sh, use the `npx skills add`
   command shown there with `-g -a opencode` and `DISABLE_TELEMETRY=1`. This uses
   one shared user-scope copy in `~/.agents/skills/`, linked for OpenCode, the
   only client today. The CLI otherwise reports skill names and files. Use `-y`
   only after the user has approved that exact list.
   - Done when: each item is installed or has a reason.
   - On failure: leave the item `missing` with the error. An unreadable source is
     a gap, never a stale copy, memory or another client's steps. A tool with no
     official OpenCode support is `not-supported`: no copied configuration, no
     unofficial wrapper.
6. **User actions.** Give one consolidated list: sign-ins, trust approvals, system
   passwords, accounts, costs, licensing and restarts. Never do them for the user.
   - Done when: each is confirmed or left `needs-user`.
   - On failure: leave it `needs-user` and continue independent work.
7. **Verify and report.** Run `check.py` again. Show each item's version and state
   before and after, backup paths and open user actions. OpenCode reloads
   configuration changes; a plugin a running session already loaded stays
   `needs-user` until a new session shows it.
   - Done when: every row is `ready`, `not-supported`, or `needs-user`, `missing` or
     `unknown` with its reason.
   - On failure: report the row with its reason and next action. Do not claim
     success without the observation, and do not retry the same way.
