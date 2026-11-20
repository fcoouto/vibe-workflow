# Worktree consent follows onboarding

## Intent

With the git workflow on, the plugin's rule is "never touch the primary checkout; do
all task work in a worktree" (`vibe-pull-requests` procedure). Superpowers'
`using-git-worktrees` skill, loaded when plan execution starts, asks "Would you like me
to set up an isolated worktree?" when the session sits in the primary checkout, and
works in place if the user says no. Its own rule skips the question when "the user
already indicated their worktree preference in your instructions".

Today that skip happens only through hand-written `AGENTS.md` lines in the user's own
projects (found in `vibe-workflow` and `opus` on 2026-11-20) or because a session already
started in a worktree. A new user who onboards with the git workflow on has neither, so
the agent asks again, and a "no" contradicts the plugin's rule. The fix ships in the
plugin for every user.

## Goals

- When the `skill` tool loads `using-git-worktrees` in a project whose git workflow is
  on, append a `vibe-workflow:` note to that tool result that declares the preference,
  so the skill's own skip applies.
- Off, skipped, not onboarded or invalid settings: no note; the skill asks as today.
- Reuse the finishing-note mechanism (PR #348) instead of adding a second one.
- Reword the shipped finishing note into the same direct shape (declared choice, action,
  override), so both notes read alike to an agent (approved by the user 2026-11-20).

## Non-goals

- No new setting or onboarding question: "git workflow on" implies worktrees, because the
  workflow's rules require them and onboarding question 3 defines a worktree as "the
  separate folder an agent works in for one task". The user agreed to rely on that.
- No standing system-prompt line, no `AGENTS.md` writes, no upstream change.
- A skill the user attaches to a prompt (no tool call) gets no note, as for finishing.

## Behavior

- The finishing trigger becomes a small table keyed by the skill id after its last `:`
  or `/`; a completed `skill` load whose id is in the table, in a project whose
  `git_workflow` state is `on`, gets that entry's note through the existing `appendNote`.
- Entries:
  - `finishing-a-development-branch`, exactly (replaces the 0.6.0 wording):

    > vibe-workflow: your human partner's declared integration choice, set during
    > onboarding: push the branch and open a pull request, following the
    > vibe-pull-requests skill ([BLOCKED] draft, then [READY]). Skip the options menu and
    > do not ask; the skill's other steps still apply. {merge} {cleanup} An explicit
    > request from your human partner in this session overrides this.

    `{merge}`: `agent_merge: true` → "You may merge it yourself once it is [READY] and
    its evidence is current."; otherwise "Stop at [READY] and tell your human partner it
    awaits their merge." `{cleanup}`: `worktree_cleanup: true` → "After a confirmed
    merge, follow the worktree cleanup steps in vibe-pull-requests."; otherwise "Keep
    the worktree after the merge."
  - `using-git-worktrees`, exactly:

    > vibe-workflow: your human partner's declared worktree preference, set during
    > onboarding: always work in a worktree, never in the primary checkout. Do not ask
    > for consent. An explicit request from your human partner in this session overrides
    > this.

  Wording rules (both notes): the declared choice and the action come first; no plugin
  setting names; the agent is addressed as "you"; the menu is never referred to by
  number.

- The id check comes before the settings read; any error gives no note; the hook never
  throws; shell-command notes are unchanged.
- The `vibe-pull-requests` procedure's "The primary checkout" section gains one sentence
  saying the same, without naming the partner skill (skill files never mention it).

## Approaches

1. **Second entry in the skill-load table (chosen).** About 10 lines, reuses the hook,
   the settings read and `appendNote`; fits the skill's own declared-preference rule.
2. Standing line in every request's instructions. Covers more paths, costs tokens on
   every request; not chosen for the same reason as for finishing.
3. Write a line into each project's `AGENTS.md` during onboarding. Touches user files and
   repeats today's hand-made fix; rejected.
4. Upstream change. Not needed: the skill already honors declared preferences.

## Constraints

- Advisory only; never blocks; the note goes into the result's `content`.
- Skill files (`skills/vibe-pull-requests/**`) never contain "superpowers" or "enforce"
  outside "not enforced".

## Unverified until checked live

Whether an agent that receives the worktree note in the primary checkout creates a
worktree without asking, and whether the reworded finishing note is still followed. Plan: one live check in a throwaway project, as for the finishing note.

## Left to the builder

The table's shape and the advisor method's name (replacing `finish`), and test fixtures.
