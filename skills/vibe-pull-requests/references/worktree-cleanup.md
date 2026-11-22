# Worktree cleanup after a merge

Clean up the task's worktree and its local branch after a merged pull request, only
when the project chose it. These steps are guidance for you; nothing is checked or
blocked for you. Remote branches are not yours to delete: GitHub's own setting does
that, and you never pass `--delete-branch` to `gh pr merge` unless the user asks.

## When

Call `vibe_workflow_onboard` with `{"v":1,"op":"preview","feature":"git_workflow"}`.
Clean up only when `previous_enabled` is `true` **and** `previous_worktree_cleanup` is
`true`. The preview's proposed values are defaults, not the saved choice. A missing,
`false`, `null`, off or blocked result means do nothing.

## Confirm the merge

By observation, never memory: your own `gh pr merge`, the user saying it merged, or
finding it merged. Then check all of these, and report which one fails and change
nothing if any does:

1. `gh pr view <n> --json state,headRefName,headRefOid,baseRefName` says `MERGED`, the
   base is the default branch, and `headRefName` is this worktree's branch. The default
   branch comes from `gh repo view --json defaultBranchRef`, or from
   `git symbolic-ref --short refs/remotes/origin/HEAD` without GitHub. Without GitHub, use
   `git merge-base --is-ancestor HEAD origin/<default branch>` after
   `git fetch origin <default branch>`.
2. `HEAD` equals `headRefOid` or is an ancestor of `origin/<default branch>`, so nothing
   was added after the merge.
3. This is a linked worktree (its git dir differs from the common git dir), not the
   primary checkout, and it is your session's own working directory or one you created
   in this session. Never another worktree, and never one found only by listing worktrees.
4. `git status --porcelain --ignored` is empty. Plain `git status` hides ignored files
   (local notes, `.env`, private evidence folders), yet removing the worktree deletes them
   without force. If it lists any, show the paths, stop, and leave the worktree for the
   user to decide; never delete them silently.
5. The worktree is not locked (`git worktree list --porcelain` shows no `locked` line for
   it) and has no submodules (no `.gitmodules`), because git would refuse to remove it
   after the branch is already deleted.

## Order

1. Tell the user first, before anything changes. Get the primary checkout's path first: the
   first `worktree` line of `git worktree list --porcelain`. If the table below says not to
   remove this worktree, stop here; nothing has changed. Then say: "Merge confirmed.
   Cleaning up now: branch <name> and worktree <path>. I will first move this chat to the
   main project folder <primary path> so it keeps working, then check the result from
   there."
   - When the client cannot move a session (table below), the removal tool itself makes the
     move, or the session is not in this worktree, replace the last sentence with: "I will
     check the result from the main project folder <primary path> afterwards."
   - If the client cannot move a session and the session is in this worktree, also say this
     chat will stop working once the worktree is removed and a new chat in <primary path>
     will be needed, then ask before removing, before step 3 changes anything. If the user
     declines, stop; nothing has changed.
   - The session's own directory disappears at the end, so the chat must leave it first.
2. Capture the absolute common git dir (`git rev-parse --path-format=absolute --git-common-dir`) and the branch
   name. Check that the primary checkout is on the default branch with
   `git --git-dir=<common git dir> symbolic-ref --short HEAD`; if it is not, stop.
3. In the worktree, run `git switch --detach`.
4. Run `git --git-dir=<common git dir> branch -d <branch>`. This safe delete is only a
   backstop: it refuses a branch with a commit that exists nowhere else (not in the
   default branch and not on the branch's upstream). It does not tell you whether the pull
   request merged; the checks above do. If git refuses, switch back to the branch, report,
   and stop. Never use `-D` to get around a refusal. A squash or rebase merge is cleaned
   up only while the branch's remote branch still exists.
5. Move the session. If the session's working directory is this worktree, move the session
   to the primary checkout's absolute path with the client's own mechanism (table below).
   A chat left in a removed folder cannot send its next message.
   - Some clients apply a move only after the current tool call returns. Make the move in
     a tool call of its own, then confirm it in a separate, later tool call, before step 6,
     with a read-only command that prints the working directory. The output must be the
     primary checkout's path; anything else counts as not confirmed.
   - If the client's removal tool already returns the session to the original folder
     (table below), there is no separate move: go to step 6 and confirm in step 7.
   - If the move fails or cannot be confirmed, stop and report that the local branch is
     already deleted and the worktree remains, detached. Leave the worktree in place.
   - If the client has no way to move a session, tell the user this chat will stop working
     once the worktree is removed and that they will need a new chat in <primary path>.
     Then ask before removing. Step 1 does this before anything changes; if it did not,
     do it now, and if the user declines, stop, report that the local branch is already
     deleted and the worktree remains, detached, and leave it in place.
6. Remove the worktree as the last action that changes anything, with the client's own
   mechanism and never with force (table below). Do this only after step 5 confirmed the
   move, found none was needed, the removal tool itself makes the move, or the user
   agreed to remove without one.
7. Verify with read-only commands that name the primary checkout's path, because your own
   directory is gone: `git -C <primary> worktree list` and
   `git -C <primary> branch --list <branch>`. If the removal tool made the move (step 5),
   also print the working directory; it must be the primary checkout's path. Report what
   they show, or say it could not be verified. Never claim cleanup from step 6 alone.

Metadata operations that leave the primary checkout's files, HEAD and index alone
(`git --git-dir=… branch -d`, `git worktree remove`, `git -C <primary> worktree list`)
do not count as touching it.

## Removing the worktree

| Client | Move the session first (step 5) | Remove the worktree (step 6) |
|---|---|---|
| OpenCode | Call `session_move` alone with the primary checkout's absolute path. The move applies only after that call returns, so never put `worktree_remove` or anything that needs the new folder in the same call. Then run `pwd` in a separate call; it must print the primary checkout's path. | `worktree_remove` with the worktree's directory (it needs `edit` permission on it). Never `force`. Its docs describe removing the worktree only; step 7 verifies the result. |
| Claude Code | No separate move: the `ExitWorktree` call in step 6 returns the session to the original folder. Confirm the folder in step 7. | `ExitWorktree` with the remove action. It may already have removed a clean worktree on exit; then there is nothing to do. |
| Codex | Not applicable: you do not remove Codex-managed worktrees, so there is nothing to move for. For a worktree you made yourself there is no known mechanism: ask before removing. This row is unverified. | Codex removes its own managed worktrees past a limit. Do not remove Codex-managed worktrees yourself. For a worktree you made yourself, use the last row. This row is unverified. |
| Any other client, or no tool | No known mechanism: ask before removing. | `git worktree remove <path>`, run from outside the worktree and without `--force`. |
