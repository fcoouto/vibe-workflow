# Pull requests and the primary checkout

The agent that opens or takes over a pull request keeps its title prefix and
draft state in step with its readiness. These rules are guidance; nothing here is
checked or blocked for you.

## Who may merge

Call `vibe_workflow_onboard` with `{"v":1,"op":"preview","feature":"pull_requests"}`.
Agents may merge only when `previous_enabled` is `true` and `previous_agent_merge`
is `true`. A missing, off, blocked or not-onboarded result means the user merges.
An explicit request from the user authorizes one merge either way.

## Lifecycle

1. **Open as a draft.** Title it `[BLOCKED] <title>` and open it as a draft. Write
   the body from [the template](pull-request-template.md), unless the project has
   its own `.github/pull_request_template.md`, which wins. `[BLOCKED]` is the
   normal state while work, checks or review continue.
2. **Mark ready.** When the current head is ready, change the prefix to `[READY]`
   and mark the pull request ready for review.
3. **Return after a change.** Before pushing to a `[READY]` pull request, and as
   soon as a base change, failed check or new finding makes its evidence stale,
   return it to `[BLOCKED]` and to draft (`gh pr ready --undo`, then retitle).
4. **Supersede.** When other work replaces a pull request, retitle it
   `[SUPERSEDED]`, name the replacement in a closing comment, close it unmerged
   and keep its branch.
5. **The prefix is yours.** Never ask a person to change it. Re-read the title
   before changing it and keep the rest of it. A prefix reports readiness; it is
   never evidence of readiness, and one set by anyone else establishes nothing.

## Readiness

A pull request is ready only when, for its current head and base: every required
check passed (skipped, cancelled, timed-out and absent checks do not count);
review findings are resolved or recorded with a reason; and nothing was pushed
since the evidence was gathered. Evidence is output observed on this head, never a
recollection and never a prefix. A rebase, conflict fix, base update or new commit
makes affected evidence stale.

## Merging

- If agents may not merge, stop at `[READY]` and tell the user the pull request
  awaits their merge.
- If agents may merge, merge only a `[READY]` pull request whose evidence is still
  current, then check the result before reporting it.

## The primary checkout

Keep the primary checkout on `main`. Never switch, detach, rename or rebase it;
do task work in a worktree. After an agent merge, if the primary checkout is clean
and on `main`, run `git fetch origin main` and then `git merge --ff-only
origin/main` there. A change to `.vibe-workflow/settings.json` alone does not make
it unclean for this rule. If it is dirty, diverged, detached or uncertain, or the
fast-forward is refused, report that and change nothing.
