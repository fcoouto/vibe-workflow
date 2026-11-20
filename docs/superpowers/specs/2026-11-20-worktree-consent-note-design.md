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
  - `finishing-a-development-branch`: the existing note, unchanged.
  - `using-git-worktrees`, exactly:

    > vibe-workflow: the git workflow is on, so your human partner already chose during
    > onboarding to do task work in a worktree and never in the primary checkout. Treat
    > this as their declared worktree preference: use a worktree without asking for
    > consent. An explicit request from your human partner in this session still wins.

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

Whether an agent that receives the note in the primary checkout creates a worktree
without asking. Plan: one live check in a throwaway project, as for the finishing note.

## Left to the builder

The table's shape and the advisor method's name (replacing `finish`), and test fixtures.
