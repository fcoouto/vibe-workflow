# Finishing follows onboarding

## Intent

When a project has the git workflow on, its user already decided during onboarding
how finished work is integrated: through a pull request, who merges it, and whether
the worktree is cleaned up after the merge. Superpowers' `finishing-a-development-branch`
skill still presents its menu ("merge locally / push and create a PR / keep as-is")
and waits, so the user is asked again. The fix ships in the plugin, so every user who
onboarded gets it.

Evidence: on 2026-11-20 the agent presented that menu for PR #347 although the
project's settings said git workflow on and agents merge.

## Goals

- When the `skill` tool loads `finishing-a-development-branch` in a project whose
  git workflow is on, the agent is told, in that tool result, what the user decided
  and not to present the menu.
- Off, not onboarded or invalid settings: no note; the skill behaves as today.

## Non-goals

- No other skill, no standing system-prompt line, no upstream change (considered and
  not chosen; see Approaches).
- No new setting and no new onboarding question.
- A skill the user attaches to a prompt (no tool call) gets no note.

## Behavior

- Trigger: a completed `execute.after` event whose `tool` is `skill` and whose skill
  id (`input.id`, else `input.name`), after its last `:` or `/`, is
  `finishing-a-development-branch`.
- Read the `git_workflow` settings the hook already uses. Only `state: "on"` adds a note.
- Append one `vibe-workflow:` note with `appendNote`, built from the settings:

  > vibe-workflow: your human partner already decided during onboarding how finished
  > work is integrated in this project, so do not present the integration options or
  > ask. Push the branch and open a pull request, following the vibe-pull-requests
  > skill ([BLOCKED] draft, then [READY]). {merge} {cleanup}

  - `{merge}`: `agent_merge: true` → "Agents merge their own [READY] pull requests
    once the evidence is current." Otherwise → "Stop at [READY] and tell your human
    partner it awaits their merge."
  - `{cleanup}`: `worktree_cleanup: true` → "After a confirmed merge, follow the
    worktree cleanup steps." Otherwise → "Keep the worktree after the merge."
  - An explicit request from the user in the session still wins (the vibe-pull-requests
    rule "an explicit request from the user authorizes one merge either way" stands).
- The `vibe-pull-requests` procedure gains one short section saying the same, so an
  agent that reads the skill gets the same rule.

## Approaches

1. **Note when the skill loads (chosen by the user).** Fires only at the moment the
   menu would appear, zero standing cost, about 15 lines in `pr-advice.js`, reusing
   `appendNote` and the settings read. Misses user-attached skills and improvised
   menus.
2. Standing "project decisions" line in every request's instructions. Covers every
   path, costs tokens on every request. Not chosen.
3. Upstream clause in superpowers' skill. Fixes the root for everyone; needs the
   maintainers. Not chosen now.

## Constraints

- Advisory only; never blocks or edits anything but the tool result's content.
- The note goes into `content` (the host shows `content`, not `output`), as the
  existing notes do.
- Never throws from the hook.

## Unverified until checked live

The `skill` tool's `execute.after` result shape and whether an appended note reaches
the model after a skill load (proven so far only for shell commands). Plan: one live
check in a throwaway project, as for session titles.

## Left to the builder

Test fixtures; exact placement inside `registerPrAdvice` (the hook currently returns
early when `input.command` is not a string).
