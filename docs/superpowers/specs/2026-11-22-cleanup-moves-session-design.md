# Cleanup moves the session first

## Status and intent

The user reported that after a post-merge cleanup removes the task's worktree, the chat
is left orphaned: the next message fails because the session's folder no longer exists.
Approved in conversation on 2026-11-22, including the instruction to keep this generic
across clients, not OpenCode only.

## Goals

- Before the worktree is removed, the session moves to the primary checkout, so the next
  message works.
- Client-neutral rule; the per-client mapping table carries the mechanism.
- Never remove a worktree the session still points at.

## Non-goals

- Code or hook changes: cleanup stays advisory skill guidance.
- Fixing worktrees removed outside this procedure (the user, another tool).
- Restoring the session after a failed move.

## Anti-goals

- Removing the worktree before the move is confirmed.
- Writing to the primary checkout's files, HEAD or index while moving.
- Claiming the move happened without observing it.

## Approaches

- **A. A move step in the cleanup reference, with a table column — selected.** One more
  step between the safe delete and the removal, one more mapping column, one test.
- **B. A hook note on `gh pr merge` only.** Rejected: the note already points at the
  reference, and the order lives there.
- **C. A plugin tool that moves the session.** Rejected: the plugin runs no write command.

## Evidence (scratch worktree, OpenCode 2.2.6, 2026-11-22)

`session_move` does not apply inside the call or the same step. The working directory
changed at the next step boundary, so a following step sees it. Moving the session to the
primary, then calling `worktree_remove` on the folder it had left, then running `pwd`
and `git worktree list` from the session worked: the session stayed in the primary and
the worktree was gone. So the order is: move, confirm in a later step, then remove.

## Changes (`skills/vibe-pull-requests/references/worktree-cleanup.md`)

- Order step 1's notice also says the chat moves: "Merge confirmed. Cleaning up now: branch
  <name> and worktree <path>. I will move this chat to the primary checkout and verify
  from its path afterwards."
- New step 5, **Move the session**: if the session's working directory is the worktree,
  move it to the primary checkout's absolute path with the client's mechanism (table).
  Some clients apply the move only at the next step, so confirm in a later step with a
  read-only command that prints the working directory. If the move fails or cannot be
  confirmed, stop and report, and leave the worktree in place. If the client has no way to
  move a session, tell the user the chat will lose its folder and ask before removing.
- Old steps 5 and 6 become 6 and 7. Step 6 (remove) is still the last action that changes
  anything and runs only after step 5 is confirmed.
- Table gains a column **Move the session first**: OpenCode `session_move` with the primary
  path, in its own call, not in the same call as anything that depends on the new folder;
  Claude Code `ExitWorktree` already returns to the original folder (nothing extra);
  Codex unverified, no known mechanism, so ask; any other client, ask.

## Proof expected

- A test in `tests/pull-request-skill.test.js` asserts the reference names the move step
  (`session_move`, "Move the session"), places it before the removal step, and keeps the
  "ask before removing" fallback.
- Existing package and validate checks pass.

## Calls made (the writer's)

- Move is step 5 after the detach and safe delete, so those git steps still run inside the
  worktree and the move stays next to the removal it protects.
- Confirm by a working-directory print, because the move lands one step later.
- Codex and unknown clients: ask, never assume.
- No `PRODUCT.md` or README change: user-facing behavior ("cleanup removes the worktree
  and its local branch") is unchanged.
