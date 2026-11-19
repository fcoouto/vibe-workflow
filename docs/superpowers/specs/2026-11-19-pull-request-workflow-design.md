# Pull request workflow and primary checkout

## Status and intent

The user approved bringing the earlier plugin's PR lifecycle, PR template and merge
question into this repository, plus keeping the local primary checkout on `main`
and current with `origin/main`. This document does not authorize installing,
activating, publishing or merging anything.

Why: agents forget the PR conventions unless something reminds them at the right
moment, and without explicit instructions they run `git checkout -b` in the shared
checkout and leave it stale. The user wants those conventions followed through
short advisory messages at the right moment, not blocking.

Scope sources: issue #328 (PR handling) and the part of #329 (templates) that
concerns PRs. References: the earlier plugin at `/Users/fcouto/dev/git/old-vibe-workflow`
(`policies/08-merge-readiness.md` "PR lifecycle", `templates/pull-request.md`,
`skills/onboard-my-project/references/procedure.md` merge question,
`docs/superpowers/specs/2026-11-12-primary-checkout-stability-design.md`).

## Goals

- Agents open PRs as `[BLOCKED]` drafts, set `[READY]` only when the current head
  is ready, return to `[BLOCKED]` after a push, and use `[SUPERSEDED]` for a PR
  that other work replaces.
- A short PR template.
- An onboarding choice: agents merge `[READY]` PRs, or only mark them and wait.
- After an agent merge, the agent brings the primary checkout current with
  `origin/main` (fast-forward only, only when clean and on `main`).
- The primary checkout stays on `main`; task work happens in a worktree.
- Adherence comes from advisory hooks where the host provides them.

## Non-goals

- Blocking, rewriting or asking about any agent action. Advisory notes only.
- Managed delivery, a GitHub App, merge queue, repository enforcement or a trusted
  verifier from the earlier plugin.
- Pointing agents at Superpowers' skills for readiness. The readiness rule is
  written here, in our own words.
- Guarding `git merge` or `git push` to `main`, `gh api`, the GitHub web UI, other
  clients or other tools. This is a client-side reminder, never enforcement.
- Post-merge branch or worktree cleanup, a `[READY]` lookup before merging, and
  detecting merges made outside this agent's tool calls.
- Cleaning up the stale `crisp-forest` worktree.

## Anti-goals

Failure even if it technically works:

- Any message or output that claims a rule is enforced or the PR is ready because
  a prefix says so. A prefix reports readiness; it is never evidence of it.
- The plugin running a git or `gh` write command. Only the agent acts, on advice.
- Granting merge authority the user did not give, or a task branch granting it to
  itself.
- Per-turn context cost for projects that have not enabled the feature.
- Changing how Smart Router onboarding, settings or selection behave today.

## Approaches

- **A. Skill only.** Rules and template in a skill, nothing else. Smallest, but
  agents skip it, which is the problem being solved.
- **B. Skill plus one advisory `execute.after` hook — selected by the user.** The
  skill holds rules and template; the hook adds a short note to the result of the
  matching shell calls. Never blocks. Needs the host hook to exist.
- **C. Native permission rules.** Propose `permission.bash["gh pr merge*"]: "ask"`
  in the user's own OpenCode config. Touches native configuration and does nothing
  for titles or checkout. Rejected; blocking was declined.

A consult with Opus 5.5 informed B. Hook availability on the installed OpenCode
2.2.6 is **not verified**: the documented V2 hook API comes from the web docs and
from `plugins/rtk.ts` and `plugins/caveman/server.js`, with no local V2 typings.
Slice 0 handles that with feature detection, a defensive handler and a fallback,
not a live run.

## Settings

One feature entry, booleans only, like Smart Router. The entry was named
`pull_requests` in 0.5.0 and 0.5.1 and was renamed `git_workflow` on 2026-11-19 so
related behavior (such as worktree cleanup) can live under one umbrella. The old
name is no longer read: an old `pull_requests` entry is just an unknown entry and is
kept as is, so a project saved under it is asked again by onboarding:

```json
{"git_workflow": {"enabled": true, "agent_merge": false}}
```

- `enabled`: the hook and reminders run only when true. Absent means not
  onboarded: nothing runs.
- `agent_merge`: default false. True means agents may merge their `[READY]` PRs.
- Read and written at the **main project path** (`project` from `project.js`), not
  the worktree's own file. A task branch can edit its own copy, so a worktree copy
  must never grant merge authority. This differs from the per-worktree file Smart
  Router keeps, by design and for this feature only.
- A missing, unreadable or invalid entry means `enabled: false` and
  `agent_merge: false`.
- `project.js` becomes feature-aware (validation and read/write of a named feature
  entry). Smart Router results, snapshots, locking and outputs stay byte-for-byte
  as they are. Unknown feature entries stay preserved.

## Onboarding

One added question, after the existing Smart Router question, in the same
control-shaped form as every onboarding question (header of at most 30
characters, context before the choices, one-to-five-word labels with
descriptions, "(Recommended)" first or "(Current)" for a saved choice). The copy
was fixed after an impeccable critique and standardized across all questions on
2026-11-19; `skills/vibe-onboard-project/references/procedure.md` is the source of
truth and a test checks its shape:

- Header **Pull request merging**; question **Who merges ready pull requests in
  this project?**
- **I merge them (Recommended)**, **Agents merge their own**, **Skip pull request
  guidance** (sets `enabled: false`; without it the `enabled` flag has no way to
  be false).

The default is a proposal, not permission to write. There is no separate
confirmation question (removed 2026-11-19): the answers are the consent, the flow
says what it will save in one plain line, and the client's native edit permission
still applies; cancelling leaves everything untouched. If every preview says
nothing changes, the flow says so and stops. The
onboarding tool is extended in a backward-compatible way: today's calls keep their
exact behavior and results. The `agent_merge` choice is the one setting a task
checkout must not be able to give itself, so it is read and written at the main
project path (see Settings).

## The skill: `vibe-pull-requests`

Registered like the other skills. Short body plus a procedure, loading the template.

Rules it states (all advisory guidance to the agent):

1. **Opening.** Open each PR as a draft titled `[BLOCKED] <title>`, body from the
   template, unless the project has its own PR template, which wins.
2. **Ready.** When the current head is ready, change the prefix to `[READY]` and
   mark it ready for review. Return it to `[BLOCKED]` draft before pushing to it
   and as soon as a base change, failed check or new finding makes evidence stale.
3. **Readiness** (own words): a PR is ready only when, for its current head and
   base, required checks passed (not skipped, cancelled, timed out or absent),
   review findings are resolved or recorded, and nothing was pushed since the
   evidence was gathered. Evidence is output you observed on this head, never a
   recollection or a prefix.
4. **Superseded.** When other work replaces a PR, retitle `[SUPERSEDED]`, name the
   replacement in a closing comment, close unmerged and keep the branch.
5. **Merging.** With `agent_merge` false, stop at `[READY]` and tell the user the
   PR awaits their merge, unless the user explicitly asks for this merge. With it
   true, merge only a `[READY]` PR whose evidence is current, then verify the
   result before reporting.
6. **The prefix belongs to the agent.** Never ask a person to change it. Re-read
   the title before changing it and keep the rest.
7. **Primary checkout.** Never touch it: no edits, commits, switching, detaching,
   renaming or rebasing, and all task work in a worktree. If it has left `main`,
   tell the user and change nothing. The one exception is the user's decision to
   keep it current: after an agent merge, if the primary is clean and on `main`,
   run `git fetch origin main` then
   `git merge --ff-only origin/main` there. If dirty, diverged, detached or
   uncertain, report and change nothing. A change to `.vibe-workflow/settings.json`
   alone does not make the primary dirty for this rule; if the merge is then
   refused because upstream also changed that file, report it and change nothing.

## The template

`templates/pull-request.md`, shipped with the skill. Sections: Why, What changes,
Checks (commands, result, commit), Review (reviewer, findings and disposition),
Readiness (candidate and base, which evidence covers it, what was not run). Omit
inapplicable lines; never invent values.

## The hook

One `ctx.tool.hook("execute.after")` handler, registered only when the host
provides it, acting only for the shell tools (`bash`, `shell`) and only when the
call succeeded. It matches the command with a pre-compiled regex tolerant of an
`rtk ` prefix, reads settings only on a match, and appends a short note to the
result. It never spawns a write command and never blocks. Per session, in memory,
each note fires at most once per event, and cases 2 and 5 only after the session
did something that makes them relevant.

| # | Trigger (success) | Note |
|---|---|---|
| 1 | `gh pr create` | Rules summary; or the specific fix when `--draft` or the `[BLOCKED]` title prefix is missing (for example `gh pr ready --undo`, `gh pr edit --title`). A title that cannot be parsed gets the summary only. |
| 2 | `git push`, in a session that created or retitled a PR | If that PR is `[READY]`, return it to `[BLOCKED]` draft. |
| 3 | `gh pr ready`, or a retitle to `[READY]` | With `agent_merge` false: stop and tell the user it awaits their merge. With true: check evidence is current before merging. |
| 4 | `gh pr merge` | Update the primary checkout (rule 7), naming its path. With `agent_merge` false, also note that the user chose to merge themselves. |
| 5 | `git checkout`, `git switch` or `git branch -m` run in the primary checkout | If it is no longer on `main`, say so and point to a worktree. One bounded, read-only `git symbolic-ref` call, only on these commands. |

The primary checkout is the `project` path. When the command's working directory
cannot be determined, case 5 stays silent. Nothing is added when the feature is
not enabled, and no per-turn context line is added.

Fallback when the host does not expose `ctx.tool.hook` (detected at setup): ship
the skill, add one short context line only when the feature is enabled, and
report "guard unavailable" in readiness. When the hook exists but an event lacks
what a case needs, that case stays silent. Never imply protection.

## Calls made after the builder check

Marked as the writer's calls; the user can change any of them.

- **Tracked settings file.** `git_workflow` stays in `.vibe-workflow/settings.json`
  (this repository commits that file, and the PR template's readiness story
  needs it portable). Onboarding's preview says the main checkout's file will
  change and that committing it is the user's step. Rule 7's "clean" ignores that
  one file (see rule 7); a refused fast-forward is reported, never forced.
- **Permission outside the session directory.** Assumed, not verified: the host
  may refuse an edit permission for a path outside the session directory. Write
  the path as a `/`-separated path relative to the session directory (with `..`
  when needed), call `ctx.permission.assert` as today, and when it is missing or
  denied, write nothing and return the existing blocked reasons. The procedure
  then tells the user to run onboarding from the main checkout.
- **Hook scope.** The feature switch comes from the session's project. Cases 1 to
  4 fire for any matching shell call in the session, in any repository, because
  resolving each command's repository would add a subprocess per call; this is a
  documented limit. Case 5 compares against the primary path.
- **"Once per event".** Cases 1, 3 and 4 fire on every occurrence; each is a
  distinct, rare event. Case 2 is armed by case 1 or 3 and fires on the first
  successful `git push` after arming, then disarms. Case 5 fires once per
  resulting branch name per session. State is in memory only.
- **Ambiguous commands.** Case 4 also fires for `gh pr merge --auto` and for a
  base other than `main`, with wording that stays conditional ("if this merge
  landed on `main`" / "once the auto-merge completes"). Case 3's wording stays
  conditional because the hook cannot know whether the user explicitly asked for
  the merge.

## Constraints

- Python 3.11+ standard library and Node ES modules; no new dependencies.
- The export allowlist (`scripts/manifest.py`, `package.json` `files`) and
  validator rules apply; new files go in both and in the package test.
- No machine paths in exported files; read-only subprocesses with timeouts.
- README changes go through the `impeccable` skill and stay short.
- Nothing installs or activates; no real PR or merge during development.
- `PRODUCT.md`: move PR handling out of "Planned evaluations" once shipped;
  reference #328 and #329 but do not close them from here.

## Build slices (smallest valuable first)

0. **Host check, no inference.** `AGENTS.md` forbids inference without explicit
   authorization, and a tool hook only fires after a model-driven tool call, so
   the builder does not run a live spike. Instead: feature-detect the hook at
   runtime (`typeof ctx.tool.hook === "function"`), write the handler defensively
   (stay silent when the tool name, command, directory or result is missing or
   has an unexpected shape), unit-test it with fake events shaped like the
   documented ones, and ship a short manual observation recipe (a throwaway
   logging plugin the user can run once) in the development notes. Report the hook
   as **unverified on the installed host** until the user runs it. If the user
   authorizes one bounded live run, it replaces this step. This is the builder's
   call, made so the user can still override it.
1. Feature-aware settings in `project.js` (tests first), including the
   main-project-path read for `git_workflow`.
2. Onboarding tool extension and the added question in the procedure.
3. The skill and template.
4. Hook cases 1 to 3.
5. Hook cases 4 and 5.
6. Docs, packaging, README (through `impeccable`), `PRODUCT.md`.

## Proof expected

- Settings: Smart Router outputs unchanged (existing tests untouched and green);
  `git_workflow` valid, invalid, missing and unknown-entry cases; the worktree
  copy never changes the effective `agent_merge`.
- Hook: each case fires on its command, stays silent otherwise, tolerates an
  `rtk ` prefix, never throws into the host, never mutates a failed call, follows
  the once-per-event rules above; nothing is added when disabled; a missing hook
  API selects the fallback; events with missing fields keep the case silent.
- Skill and template are registered and packaged; the export validates.
- Observed host behavior (spike and any live check) is reported separately from
  unit proof. No claim of enforcement anywhere.

## Left to the builder

Exact regexes and message wording (kept short), event field names as observed, the
combined onboarding confirmation flow, how `project.js` is made feature-aware with
the least change, and file names within the stated structure.
