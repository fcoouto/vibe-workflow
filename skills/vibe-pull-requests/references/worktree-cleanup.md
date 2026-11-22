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

1. Tell the user first: "Merge confirmed. Cleaning up now: branch <name> and worktree
   <path>. I will move this chat to the primary checkout and verify from its path
   afterwards." The session's own directory disappears at the end, so the chat must leave
   it first.
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
   A chat left in a removed folder cannot send its next message. Some clients apply the
   move only at the next step, so confirm it in a later step with a read-only command that
   prints the working directory. If the move fails or cannot be confirmed, stop, report,
   and leave the worktree in place. If the client has no way to move a session, tell the
   user the chat will lose its folder and ask before removing.
6. Remove the worktree as the last action that changes anything, only after step 5 is
   confirmed, with the client's own mechanism and never with force (table below).
7. Verify with read-only commands that name the primary checkout's path, because your own
   directory is gone: `git -C <primary> worktree list` and
   `git -C <primary> branch --list <branch>`. Report what they show, or say it could not
   be verified. Never claim cleanup from step 6 alone.

Metadata operations that leave the primary checkout's files, HEAD and index alone
(`git --git-dir=… branch -d`, `git worktree remove`, `git -C <primary> worktree list`)
do not count as touching it.

## Removing the worktree

| Client | Move the session first (step 5) | Remove the worktree (step 6) |
|---|---|---|
| OpenCode | `session_move` with the primary checkout's path, in a call of its own: the move lands at the next step, so nothing that needs the new folder runs in the same call. Confirm with `pwd` in a later step. | `worktree_remove` with the worktree's directory (it needs `edit` permission on it). Never `force`. Its docs describe removing the worktree only; step 7 verifies the result. |
| Claude Code | `ExitWorktree` returns the session to the original folder, so there is nothing extra to move. | `ExitWorktree` with the remove action. It may already have removed a clean worktree on exit; then there is nothing to do. |
| Codex | No known mechanism: ask before removing. This row is unverified. | Codex removes its own managed worktrees past a limit. Do not remove Codex-managed worktrees yourself. This row is unverified. |
| Any other client, or no tool | No known mechanism: ask before removing. | `git worktree remove <path>`, run from outside the worktree and without `--force`. |
