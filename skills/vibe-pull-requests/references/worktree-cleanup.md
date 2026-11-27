# Worktree cleanup after a merge

Clean up the task's worktree and its local branch after a merged pull request, only
when the project chose it. These steps are guidance for you; nothing is checked or
blocked for you. Remote branches are not yours to delete: GitHub's own setting does
that, and you never pass `--delete-branch` to `gh pr merge` unless the user asks.

Nothing is deleted, moved or archived before the merge is confirmed. Before the merge you
only read and ask. Older merged worktrees are covered by "Sweeping older merged worktrees"
at the end.

## When

Call `vibe_workflow_onboard` with `{"v":1,"op":"inspect","feature":"git_workflow"}`.
This reads only the main checkout. Never use onboarding's worktree preview to decide.
Clean up only when `state` is `"on"` **and** `worktree_cleanup` is `true`.
A missing, `false`, `null`, off or blocked result means do nothing.

## Leftover files

The leftover files of a worktree are the lines `git status --porcelain --ignored` prints
when run in it. Plain `git status` hides ignored files (local notes, `.env`, private
evidence folders), yet removing the worktree deletes them without force. Sorting only
reads. Put every line in exactly one group:

- **Rebuildable**: the status is `!!`, and the last path component of the path, exactly as
  git printed it, equals one of these names: `node_modules/`, `__pycache__/`, `*.pyc`,
  `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.DS_Store`. A name ending in `/` matches
  only a folder, which git prints with a trailing `/`. `*.pyc` matches a last path component
  that ends in `.pyc`. These are tool caches that never hold user files.
- **Real work**: every other line. That is untracked (`??`), modified, staged and deleted
  paths, and ignored paths that are not rebuildable: `.env`, `reports/`, private evidence
  folders, and build or test outputs such as `dist/`, `build/`, `coverage/` and
  `.test-artifacts/`, which can hold evidence.

| Line | Group | Why |
|---|---|---|
| `!! node_modules/` | Rebuildable | Ignored, and the name is listed. |
| `!! runtime/__pycache__/` | Rebuildable | The last path component is `__pycache__/`. |
| `!! tools/check.pyc` | Rebuildable | The last path component ends in `.pyc`. |
| `?? node_modules/` | Real work | Not ignored. |
| `!! mynode_modules/` | Real work | Not the exact name. |
| `!! build/` | Real work | Git prints an ignored folder as one line, so `build/.env` or evidence inside it is not visible. A listed name inside it changes nothing. |
| `!! .env`, `?? notes.md`, ` M src/a.js` | Real work | Not on the list. |

The list is fixed here: no project file, similar name or guess about a folder's contents
adds to it. A path git prints in double quotes is real work.

To delete a path, when a step below says so: run `rm -r -- '<worktree>/<path>'`, with the
path exactly as git printed it minus the trailing `/`. Shell-quote it: wrap it in single
quotes and write a `'` inside it as `'\''`. Run one `rm` for each printed line, and never
give it a parent folder, a pattern or a path git did not print.

## Before `[READY]`

When cleanup is on ("When") and your session works in a linked worktree, sort that
worktree's leftover files before you mark its pull request `[READY]`. This is part of
readiness, whoever merges. It only reads.

- **No lines, or only rebuildable lines**: there is no question. Cleanup runs after the
  merge.
- **Any real work**: the pull request stays a `[BLOCKED]` draft until the user has answered.
  Ask in a message of its own, never at the end of a report. List every real-work path. For
  a folder, also show its file list (`find '<path>' | sort`, run in the worktree's top
  folder), because git prints a folder as one line and the user must see what they decide.

Offer these choices. The user may answer differently for different paths; the paths that
share one answer are a group.

| Kind of path | Choices |
|---|---|
| Untracked (`??`) or ignored (`!!`) paths | **Commit to this PR**, **Archive** or **Discard** |
| Modified, staged or deleted tracked files (every other status) | **Commit to this PR** or **Keep the worktree** |

- **Commit to this PR**: commit and push the paths now, before `[READY]`. This adds to the
  head, so gather readiness evidence after it.
- **Archive**: after the merge, the paths move into the primary checkout's archive folder
  ("Archive" below).
- **Discard**: after the merge, the paths are deleted.
- **Keep the worktree**: nothing changes. After the merge, cleanup is blocked, and the
  worktree and its branch stay. Tracked files have no Archive choice: moving the visible
  file would lose a different staged version, and a staged deletion has nothing to move.

Mark a choice with `(Recommended)` only in these cases:

- Council records in `docs/vibe-workflow/council-sessions/<date>_<title>/`. Git may print
  only a parent folder; find each session folder in the file list.
  - An untracked session folder that has `result.md`: **Commit to this PR (Recommended)**.
  - An untracked session folder without `result.md`, from a run that did not finish:
    **Archive (Recommended)**, since it may hold members' answers.
  - A session folder shown as ignored (`!!`), because the project keeps records out of git:
    **Archive (Recommended)**.
- Build and test outputs (`dist/`, `build/`, `coverage/`, `.test-artifacts/`):
  **Discard (Recommended)**.

Then:

1. For each **Archive** or **Discard** answer, record the choice, the exact status lines and
   each folder's file list, as shown to the user. Change nothing for them yet. If the pull
   request closes unmerged, they are never touched.
2. Act on each **Commit to this PR** answer.
3. Sort again. The question is settled when every real-work line left has a recorded
   **Archive**, **Discard** or **Keep the worktree** answer. Ask about any line that does
   not, the same way. Only then continue to `[READY]`.

The answers live in this session only. Another session does not know them, so it treats
the same paths as real work with no answer.

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
4. Every leftover file is covered. Sort them ("Leftover files"). A line is covered when it
   is rebuildable, or when it is real work with an **Archive** or **Discard** answer
   recorded in this session ("Before `[READY]`"). For each such answer, list them again (the
   status lines, and each folder's file list) and compare with what the user approved. A
   path that is new, missing or changed stops that group: none of its paths is touched, and
   the group is reported. This check fails when any line is not covered: real work with no
   answer in this session, a **Keep the worktree** answer, or a stopped group. Report the
   paths and leave the worktree; never delete real work silently. This check only reads:
   the covered files are acted on in step 3 of the order, after checks 1–3 and 5 also pass.
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
   - If check 4 found files to archive, discard or delete, add one sentence that names
     them: "First I will archive <paths>, discard <paths> and delete the tool caches
     <paths>."
2. Capture the absolute common git dir (`git rev-parse --path-format=absolute --git-common-dir`) and the branch
   name. Check that the primary checkout is on the default branch with
   `git --git-dir=<common git dir> symbolic-ref --short HEAD`; if it is not, stop.
3. Act on the leftover files that check 4 covered, in this order. If one action fails,
   stop there: cleanup is blocked, and everything else stays as it is.
   - Run the archive command once, with every **Archive** path ("Archive" below).
   - Delete every **Discard** path ("Leftover files" says how).
   - Delete every rebuildable entry.
   - Sort again. The output must be empty; any line it prints blocks cleanup.

   Then, in the worktree, run `git switch --detach`.
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

## Archive

Archive moves the approved paths to
`<primary checkout>/.vibe-workflow/archives/<YYYY-MM-DD>_<branch>/`, each at the same place
it had below the worktree's top folder. `<YYYY-MM-DD>` is the local date when the archive
runs, and every `/` in the branch name is replaced by `-`. If that folder exists, `-2` is
added to its name, then `-3`, up to `-99`; nothing is overwritten. Example: branch
`fix/title-sync` archived on 2026-11-27 goes to
`.vibe-workflow/archives/2026-11-27_fix-title-sync/`, and a second archive that day goes to
`.vibe-workflow/archives/2026-11-27_fix-title-sync-2/`.

The folder is written only by this skill's script, never by hand: no `mv`, `cp`, `mkdir`
or edit there. Run the script only in step 3 of the order, so after the merge is confirmed
and after check 4 listed the paths again:

```sh
python3 -I -B '<skill folder>/scripts/archive.py' --worktree '<worktree>' --branch '<branch>' --path '<relative path>' --path '<relative path>'
```

- `<skill folder>` is this skill's "Base directory for this skill:" path.
- `<worktree>` is the worktree's absolute top folder. The script finds the primary checkout
  from it; the shell's current folder does not matter.
- `<branch>` is the worktree's branch name, unchanged. The script builds the folder name.
- Give one `--path` for each approved path and no other: the path relative to the
  worktree's top folder, as git printed it. One trailing `/` is accepted. Each name must
  match the stored name byte for byte, so for a path git printed in double quotes give the
  real name, not the quoted form.
- Shell-quote every argument: wrap it in single quotes and write a `'` inside it as `'\''`.

Then read the result:

- **Exit 0**: it prints one absolute destination per line. Report these destinations as
  printed; do not work the folder name out yourself.
- **Exit 2**: it prints `archived: <destination>` for each path it already moved, then one
  `refused: <reason>` line, and moves nothing more. Report the reason and the `archived:`
  lines. Archive nothing more: not by other means, and not by running the script again
  unless the user asks. Cleanup of this worktree is blocked; discard and delete nothing
  else in it.
- **Anything else** (another exit code, or no Python): treat it as refused, the same way.

## Report

After a confirmed merge with cleanup on, your report on the session's worktree states
exactly one of these, in these words:

- **Cleanup verified**, with the step 7 output and any archive destinations.
- **Cleanup blocked**, with the reason (the check or step that stopped it), the paths, and
  what already changed: archived paths, deleted files, a deleted branch.

The report never ends on a question about leftover files: that question was asked before
`[READY]`. A blocked cleanup is a statement; the user decides what to do next.

## Removing the worktree

| Client | Move the session first (step 5) | Remove the worktree (step 6) |
|---|---|---|
| OpenCode | Call `session_move` alone with the primary checkout's absolute path. The move applies only after that call returns, so never put `worktree_remove` or anything that needs the new folder in the same call. Then run `pwd` in a separate call; it must print the primary checkout's path. | `worktree_remove` with the worktree's directory (it needs `edit` permission on it). Never `force`. Its docs describe removing the worktree only; step 7 verifies the result. |
| Claude Code | No separate move: the `ExitWorktree` call in step 6 returns the session to the original folder. Confirm the folder in step 7. | `ExitWorktree` with the remove action. It may already have removed a clean worktree on exit; then there is nothing to do. |
| Codex | Not applicable: you do not remove Codex-managed worktrees, so there is nothing to move for. For a worktree you made yourself there is no known mechanism: ask before removing. This row is unverified. | Codex removes its own managed worktrees past a limit. Do not remove Codex-managed worktrees yourself. For a worktree you made yourself, use the last row. This row is unverified. |
| Any other client, or no tool | No known mechanism: ask before removing. | `git worktree remove <path>`, run from outside the worktree and without `--force`. |

## Sweeping older merged worktrees

A `vibe-workflow:` note may say "merged worktrees still on disk" and name them, each as
`<path> (<branch>)`, `<path> (detached)` or `<path> (<branch>, needs repair)`. When cleanup
is on ("When"), sweep the worktrees it names without being asked. If your own worktree's
cleanup is due too, sweep first: the sweep needs no session move, and your own cleanup may
end this chat.

For the sweep only, check 3's ownership rule is relaxed: a worktree is eligible when the
note named it and step 2 below confirms its merge. Never sweep the primary checkout, your
session's own worktree (the steps above cover it), or a worktree the note did not name,
such as those behind "and N more"; a later note names them.

Never ask a question during the sweep. When a step says to skip or stop, or a command in
it fails, change nothing more in that worktree, name it in the report, and go on to the
next one.

Before the first worktree:

- `<primary>` is the first `worktree` line of `git worktree list --porcelain`.
  `<common git dir>` is `git -C <primary> rev-parse --path-format=absolute --git-common-dir`.
- The primary checkout must be on the default branch
  (`git --git-dir=<common git dir> symbolic-ref --short HEAD`; check 1 says how to find the
  default branch). If it is not, sweep nothing and report that.
- Run `git -C <primary> fetch origin <default branch>` once.

Then, for each named worktree:

1. **Repair if needed.** Read `git -C <primary> worktree list --porcelain`. Skip the
   worktree if it shows `locked` or has a `.gitmodules` file. If the note says
   `needs repair`, the list shows `prunable` for it, or `git -C <worktree> status` fails,
   run `git -C <primary> worktree repair <worktree>`, which rewrites git's own metadata
   only. If `git -C <worktree> status` still fails, skip the worktree and report it.
2. **Confirm the merge.** Read `git -C <worktree> rev-parse HEAD`.
   - With a branch: run
     `gh pr list --head <branch> --state merged --json number,headRefOid,baseRefName`. It
     must return a pull request whose `baseRefName` is the default branch, and `HEAD` must
     equal its `headRefOid` or pass the ancestor test below. An empty list means not merged.
   - Detached, or without GitHub: `HEAD` must pass the ancestor test.
   - The ancestor test is
     `git -C <worktree> merge-base --is-ancestor HEAD origin/<default branch>`.

   If the merge is not confirmed, skip the worktree and report it.
3. **Sort its leftover files** ("Leftover files"). If any line is real work, skip the
   worktree, change nothing in it, and report it once as blocked, with the paths. Answers
   from another session do not count. If every line is rebuildable, delete each entry.
4. **Delete the branch.** A detached worktree has no branch: go to step 5. Otherwise run
   `git -C <worktree> switch --detach`, because git refuses to delete a branch that is
   still checked out, then `git --git-dir=<common git dir> branch -d <branch>`.
   If git refuses, never use `-D`: run `git -C <worktree> switch <branch>`, report, and stop
   for this worktree.
5. **Sort again, then remove.** Sort the leftover files once more. If it prints any line,
   stop for this worktree and report the paths; the worktree stays, detached. Otherwise
   remove the worktree as the very next action. Use the client's own mechanism when it can
   remove a worktree the session is not in (OpenCode's `worktree_remove`); otherwise run
   `git -C <primary> worktree remove <worktree>`. Never remove with force, and never remove
   a Codex-managed worktree. No session move is needed: the session is not in it.
6. **Verify.** Run `git -C <primary> worktree list` and, when it had a branch,
   `git -C <primary> branch --list <branch>`. Neither may still show it.

Report one line for each named worktree: removed, or blocked with the reason and the
paths. The report does not end on a question.

Known residual risk: the note lists only worktrees where no chat was busy when it was
built. A chat could resume there, or a file appear, between the sort in step 5 and the
removal. The window is one tool call; there is no lock.
