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

Every step that says "sort" means this command, with the worktree's absolute top folder:

```sh
python3 -I -B '<skill folder>/scripts/leftovers.py' sort --worktree '<worktree>'
```

`<skill folder>` is this skill's "Base directory for this skill:" path. `<worktree>` is the
path `git rev-parse --show-toplevel` prints in the worktree: absolute, with no symbolic
link in it. The script refuses a worktree path that is, or lies below, a symbolic link.
Shell-quote every argument of every command in this file: wrap it in single quotes and
write a `'` inside it as `'\''`.

The `sort`, `expand`, `manifest` and `identity` modes of this script only read, and they
never rewrite git's index. `sort` runs `git status --porcelain=v1 -z --ignored` in the
worktree, so every name arrives as it is stored, never in git's quoted display form. The line form,
`git status --porcelain --ignored`, quotes and escapes unusual names: never compare or act
on a quoted name, and quote a name only to show it to the user. Plain `git status` hides
ignored files (local notes, `.env`, private evidence folders), yet removing the worktree
deletes them without force.

- **Exit 0**: one JSON row per leftover file, such as
  `{"status": "!!", "path": "node_modules/", "group": "rebuildable"}`. `path` is the stored
  name, relative to the worktree's top folder; decode it as JSON. No row means no leftover
  files.
- **Any other exit** (exit 2 prints `refused: <reason>`): the sort failed and printed no
  row. A git warning or an unreadable folder makes it fail, because the listing would be
  incomplete. Never treat a failed sort as empty: the step that needed it stops.

Git lists nothing for a folder that holds only a repository, such as `saved/.git/config`
with no other file, yet removing the worktree deletes it. So before the sort prints no
row, it walks the whole worktree, never through a symbolic link and never onto another
device, and refuses when it finds any `.git` entry below the top folder other than the
worktree's own `.git`. The refusal names that entry. It is real work that only the user
can move or remove: report it, and never remove it or the worktree yourself.

`group` puts every row in exactly one group:

- **Rebuildable**: the status is `!!`, and the last path component of `path` equals one of
  these names: `node_modules/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`,
  `.ruff_cache/`, `.DS_Store`. A name ending in `/` matches only a folder, which git prints
  with a trailing `/`. `*.pyc` matches a file whose last path component ends in `.pyc`.
  These are tool caches that never hold user files.
- **Real work**: every other row. That is untracked (`??`), modified, staged and deleted
  paths, and ignored paths that are not rebuildable: `.env`, `reports/`, private evidence
  folders, and build or test outputs such as `dist/`, `build/`, `coverage/` and
  `.test-artifacts/`, which can hold evidence.

| Status and path | Group | Why |
|---|---|---|
| `!! node_modules/` | Rebuildable | Ignored, and the name is listed. |
| `!! runtime/__pycache__/` | Rebuildable | The last path component is `__pycache__/`. |
| `!! tools/check.pyc` | Rebuildable | The last path component ends in `.pyc`. |
| `?? node_modules/` | Real work | Not ignored. |
| `!! mynode_modules/` | Real work | Not the exact name. |
| `!! build/` | Real work | Git prints an ignored folder as one row, so `build/.env` or evidence inside it is not visible. A listed name inside it changes nothing. |
| `!! .env`, `?? notes.md`, ` M src/a.js` | Real work | Not on the list. |

The list is fixed, here and in the script: no project file, similar name or guess about a
folder's contents adds to it.

A row with `"unsafe": true` has a name with a control character, such as a newline, or
with bytes that are not UTF-8. Such a name cannot be typed into a command safely. In a
manifest, an entry named `.git` is marked the same way: it is a repository inside the
folder. An unsafe row is always real work, and you never delete, archive or commit it or
a folder that holds it.

### Manifests

A manifest fingerprints paths, so a later change to them shows:

```sh
python3 -I -B '<skill folder>/scripts/leftovers.py' manifest --worktree '<worktree>' --path '<path>' --path '<path>'
```

- **Exit 0**: for each `--path`, one row per entry at and below it, with its stored name,
  its `type` (`file`, `folder`, `link` or `other`), for a file the `sha256` of its content,
  and `status`, every status git gives that name: `["??"]`, `["!!"]`, or more, such as
  `["??", "D "]` for a staged deletion next to the same untracked file. A name git lists
  that is not on disk gets a row of type `missing`. Then one digest row for that path,
  such as
  `{"digest": …, "path": …, "entries": 3, "statuses": ["??"], "tracked": 0, "staged": 0}`.
  The digest covers all of its rows, so it changes when a name, a type, any content or any
  git status below the path changes. `tracked` counts the names git's index holds in it.
  `staged` counts the statuses in it other than `??` and `!!`. The digest row has
  `"unsafe": true` when any row in it is unsafe.
- **Any other exit**: a read failed and no row was printed. The paths count as changed.

The script reads through folders it opens from the worktree's top folder, never through a
symbolic link: a link is recorded by its target and never followed, and a path below a
linked folder is refused. A folder on another device than the top folder is a mount and
is refused too. A bind mount of the same device is not detected.

### Removing

Leftover files are removed only by these two commands, never by a shell command of your
own. Both remove through folders opened from the worktree's top folder, never through a
symbolic link and never onto another device.

Both commands are pinned to the merge you confirmed:

- `<primary>` is the primary checkout's path.
- `<branch>` is the branch the merge was confirmed for. For a detached worktree, give
  `--detached` in place of `--branch '<branch>'`.
- `<head>` is the full commit hash the merge was confirmed for, never a short hash or a
  name such as `HEAD`. For your own worktree it is what `git rev-parse HEAD` prints when
  check 2 passes. In the sweep, `<branch>` and `<head>` are the `branch` and `head` the
  identity command printed, once step 3 confirmed the merge for them.

Before it removes anything, each command makes the identity check of "Sweeping older
merged worktrees" step 2 on the worktree: the repository registers exactly that path on
that branch, its backlink points at it, and its common git dir is the repository's. It
then refuses, with nothing removed, when the worktree is on another branch, is detached
when you named a branch, is on a branch when you said detached, or its `HEAD` is another
commit. A worktree that moved after the merge was confirmed is not the one you confirmed:
confirm the merge again from the start, never pass the new values just to get past the
refusal.

The discard command removes approved **Discard** paths. Give each path, then the digest
recorded for it:

```sh
python3 -I -B '<skill folder>/scripts/leftovers.py' discard --worktree '<worktree>' --primary '<primary>' --branch '<branch>' --head '<head>' --path '<path>' --digest '<digest>' --path '<path>' --digest '<digest>'
```

It takes the paths in order. For each one it builds the manifest again and checks it
immediately before it removes that path. It refuses when the digest differs from the one
you gave, when the path holds anything tracked or staged, when anything in it is unsafe,
or when a file in it is neither untracked nor ignored.

The caches command removes every rebuildable row:

```sh
python3 -I -B '<skill folder>/scripts/leftovers.py' caches --worktree '<worktree>' --primary '<primary>' --branch '<branch>' --head '<head>'
```

It sorts the worktree itself. It reads each rebuildable row, then asks git again
immediately before it removes that row. The row must still be one ignored entry, with
nothing tracked or staged in it, nothing unsafe in it, and every file in it ignored.

Read the result of either command the same way:

- **Exit 0**: one `removed: <path>` line for each path it removed.
- **Exit 2**: a `removed: <path>` line for each path it fully removed, then one refusal,
  and it stops there:
  - `refused: <path>: <reason>; nothing in it was removed`: the check before the removal
    failed.
  - `refused: <path>: <reason>; it may be partly removed`: the removal itself failed.
    This follows any error, such as a folder that cannot be changed, and not only a
    change made by someone else.
  - `refused: <path>: it was removed, but reporting that failed (<error>); nothing after
    it was touched`: the removal succeeded and its `removed:` line could not be written.
    The path is gone.
  - `refused: <reason>` with no path: the command stopped before its first removal, such
    as on a failed identity, branch or `HEAD` check. This run removed nothing.

  Report the lines and the refusal. Remove nothing more, by this command or any other
  means: cleanup of this worktree is blocked.
- **Anything else** (another exit code, no refusal line, or output that is cut off):
  treat it as refused, the same way, and never conclude that nothing was removed. Sort
  the worktree to see what is left, and report that.

Accepted residual risk: a path replaced between its last check and its removal, or a
folder moved out while it is being removed, is not detected, because a worktree has one
user and no adversary.

## Before `[READY]`

When cleanup is on ("When") and your session works in a linked worktree, sort that
worktree before you mark its pull request `[READY]`. This is part of readiness, whoever
merges. It only reads.

- **No row, or only rebuildable rows**: there is no question. Cleanup runs after the merge.
- **Any real work**: the pull request stays a `[BLOCKED]` draft until the user has answered.
  Ask in a message of its own, never at the end of a report. List every real-work path. For
  each untracked or ignored one, run its manifest and show its rows as the file list,
  because git prints a folder as one row and the user must see what they decide. If a
  manifest fails, that path gets **Keep the worktree** only.

Offer these choices. The user may always answer **Keep the worktree** instead. Offer
**Archive** or **Discard** only for a path whose digest row has `"tracked": 0`,
`"staged": 0` and no `"unsafe": true`.

| Kind of path | Choices |
|---|---|
| Untracked (`??`) | **Commit to this PR**, **Archive** or **Discard** |
| Ignored (`!!`), not rebuildable | **Archive** or **Discard** |
| Modified, staged or deleted tracked files (every other status) | **Commit to this PR** or **Keep the worktree** |
| Any path whose row or digest row has `"unsafe": true` | **Keep the worktree**, unless the user renames or removes the unsafe entry themselves |

- **Commit to this PR**: commit and push the path now, before `[READY]`. This adds to the
  head, so gather readiness evidence after it. An ignored path gets no such choice:
  committing one needs forced staging, which you never use.
- **Archive**: after the merge, the path moves into the primary checkout's archive folder
  ("Archive" below).
- **Discard**: after the merge, the path is deleted.
- **Keep the worktree**: nothing changes. After the merge, cleanup is blocked, and the
  worktree and its branch stay. Tracked files have no Archive choice: moving the visible
  file would lose a different staged version, and a staged deletion has nothing to move.

### One choice, one exact path

A choice applies to one exact path and everything in it. Approved paths never overlap: no
approved path lies inside another.

- By default the exact path is a row's `path`, and the whole row takes one choice.
- When one real-work row lies inside another row's folder, such as an ignored file inside
  an untracked folder, ask about the outer path only. Its manifest shows the inner one, and
  **Archive** or **Discard** covers both. After a **Commit to this PR**, the next sort
  still prints the inner row; ask about it then.
- When the choices differ inside one row, split it only like this:
  - An untracked (`??`) folder: expand it, then ask for each child.

    ```sh
    python3 -I -B '<skill folder>/scripts/leftovers.py' expand --worktree '<worktree>' --path '<path>'
    ```

    It runs `git status --porcelain=v1 -z --untracked-files=all` and prints one row for
    each untracked file in the folder. A child is one of these files, or a folder inside
    the row's path that the user names or that is a council session folder (below). Each
    child takes its own choice and its own manifest. A file that lies in no answered child
    is real work with no answer.

    If the command exits with anything but 0, the expansion failed and the row is blocked:
    it gets no **Archive** or **Discard** answer, split or not.
    Never fall back to one choice for the whole row.
  - An ignored (`!!`) folder: git cannot expand it. The whole row takes one choice, or the
    user keeps the worktree.
- Never promote a child's choice to its parent. An answer for a child never covers its
  parent, a sibling, or a file that appears later.

### Recommended choices

Mark a choice with `(Recommended)` only in these cases.

Council records are session folders, `docs/vibe-workflow/council-sessions/<date>_<title>/`.
When an untracked row's path is above the session folders, expand it and ask for each
session folder.

| Session folder | Recommended choice |
|---|---|
| Untracked, with `result.md` | **Commit to this PR (Recommended)** |
| Untracked, without `result.md` (the run did not finish, and the folder may hold members' answers) | **Archive (Recommended)** |
| Ignored (`!!`) session folder, or an ignored folder above it (the project keeps records out of git) | **Archive (Recommended)** |

Build and test outputs (`dist/`, `build/`, `coverage/`, `.test-artifacts/`):
**Discard (Recommended)**.

### After the answers

1. For each **Archive** or **Discard** answer, record the choice, the exact path and its
   whole digest row (digest, statuses, tracked and staged counts), from the manifest the
   user saw. Change nothing for them yet. If the pull request closes unmerged, they are
   never touched.
2. Act on each **Commit to this PR** answer: stage exactly the approved path, commit, push.
3. Sort again. The question is settled when every real-work row left is covered by a
   recorded **Archive**, **Discard** or **Keep the worktree** answer (check 4 says what
   covered means). Ask about any row that is not, the same way. Only then continue to
   `[READY]`.

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
   was added after the merge. Record the full hash `git rev-parse HEAD` prints: it is the
   `<head>` the removing commands need.
3. This is a linked worktree (its git dir differs from the common git dir), not the
   primary checkout, and it is your session's own working directory or one you created
   in this session. Never another worktree, and never one found only by listing worktrees.
4. Every leftover file is covered. Sort the worktree. A row is covered when one of these
   holds:
   - its group is rebuildable;
   - its `path` is a path with an **Archive** or **Discard** answer recorded in this
     session ("Before `[READY]`"), or lies inside one;
   - it is an untracked folder that was split, and expanding it again shows every file
     inside such a path.

   Then list them again: run the manifest for every recorded path and compare each digest
   row with the recorded one. Any difference in the row, a missing path or a failed read
   stops that group, which is the paths that share one answer: none of them is touched,
   and the group is reported. This check fails when the sort fails, when a group stops, or when any row
   is not covered: real work with no answer in this session, or a **Keep the worktree**
   answer. Report the paths and leave the worktree; never delete real work silently. This
   check only reads: the covered files are acted on in step 3 of the order, after checks
   1–3 and 5 also pass.
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
3. Act on the leftover files, in this order, one command straight after the other. If a
   command fails or refuses, or a rule stops you, stop there: cleanup is blocked, and
   everything else stays as it is.
   - **Archive.** Run the manifest for the **Archive** paths only. Each digest row must
     equal the recorded one. Then run the archive command as the very next command, with
     every **Archive** path ("Archive" below). If no path has an **Archive** answer,
     skip the archive command and this manifest.
   - **Discard.** If a path has a **Discard** answer, run the discard command with every
     such path and its recorded digest ("Removing" above). It checks each path again
     itself, immediately before it removes that path.
   - **Caches.** Run the caches command.
   - **Empty.** Sort. It must exit 0 and print no row; any row blocks cleanup.

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
   agreed to remove without one. Immediately before removing, sort once more, naming the
   worktree by its absolute path, because the session may have left it. The sort must
   exit 0 and print no row. If it fails or prints a row, stop and report the paths, and
   that the local branch is already deleted and the worktree remains, detached.
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
and straight after the manifest of the **Archive** paths matched:

```sh
python3 -I -B '<skill folder>/scripts/archive.py' --worktree '<worktree>' --branch '<branch>' --path '<relative path>' --path '<relative path>'
```

- `<worktree>` is the worktree's absolute top folder. The script finds the primary checkout
  from it; the shell's current folder does not matter.
- `<branch>` is the worktree's branch name, unchanged. The script builds the folder name.
- Give one `--path` for each path with an **Archive** answer and no other: the exact path,
  relative to the worktree's top folder, as its row or the user's approval gave it. One
  trailing `/` is accepted. Each name must match the stored name byte for byte.
- If no path has an **Archive** answer, do not run the command at all.

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
  what already changed: archived paths, discarded paths, a deleted branch.

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
note named it and steps 1 to 3 below pass. Never sweep the primary checkout, your
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

Then, for each named worktree, in this order:

1. **Check the client, before anything else.** Codex-managed worktrees are never removed,
   repaired or changed. Treat the worktree as Codex-managed when your client is Codex and
   you did not create it yourself in this session, or when its path has a `.codex` folder
   in it (a guess that only ever skips; it is unverified). If you cannot establish who
   manages it, skip it and report it.
2. **Check its identity.** A note can be stale: the folder may now hold another branch,
   belong to another repository, or be a link to another worktree. Run this with the
   note's path, unchanged, and the note's branch:

   ```sh
   python3 -I -B '<skill folder>/scripts/leftovers.py' identity --worktree '<worktree>' --primary '<primary>' --branch '<branch>'
   ```

   When the note says `detached`, give `--detached` in place of `--branch '<branch>'`. It
   exits 0 and prints one row with `worktree`, `branch` and `head` only when all of these
   hold:
   - the path is absolute, and neither is nor passes through a symbolic link;
   - this repository registers exactly that path as a worktree, not locked, on that
     branch, and no other registered worktree resolves to it;
   - the folder's own common git dir is this repository's;
   - the repository's backlink for that worktree points back at the path's `.git`;
   - its live `HEAD` is on that branch, or detached when you said so.

   Then:
   - Exit 0: continue. Skip the worktree if it has a `.gitmodules` file.
   - Exactly `refused: the worktree's Git pointer is broken` (the note may say
     `needs repair`, and `git worktree list` may show `prunable`). The script says this
     only when the path's `.git` file names a git folder that does not exist. It reads
     that file as git does, so it strips trailing CR and LF, and it has verified that the
     folder is absent. Run `git -C <primary> worktree repair <worktree>`, which rewrites
     git's own metadata only. Then run the identity command again: a `git status` that
     works is not enough. Unless it now exits 0, skip the worktree and report it.
   - Any other refusal or exit, such as a git error, warning or timeout, or a git folder
     that cannot be checked because of a permission or other error: skip the worktree and
     report it. Never repair it.
3. **Confirm the merge.** `HEAD` is the `head` the identity command printed. It is the
   `<head>` of step 4, so the caches command refuses if `HEAD` moved after this step.
   - With a branch: run
     `gh pr list --head <branch> --state merged --json number,headRefOid,baseRefName`. It
     must return a pull request whose `baseRefName` is the default branch, and `HEAD` must
     equal its `headRefOid` or pass the ancestor test below. An empty list means not merged.
   - Detached, or without GitHub: `HEAD` must pass the ancestor test.
   - The ancestor test is
     `git -C <worktree> merge-base --is-ancestor HEAD origin/<default branch>`.

   If the merge is not confirmed, skip the worktree and report it.
4. **Sort its leftover files.** If the sort fails or any row is real work, skip the
   worktree, change nothing in it, and report it once as blocked, with the paths. Answers
   from another session do not count. If every row is rebuildable, run the caches command
   with the `branch` and `head` that step 3 confirmed ("Removing" says how); if it
   refuses, stop for this worktree.
5. **Delete the branch.** A detached worktree has no branch: go to step 6. Otherwise run
   `git -C <worktree> switch --detach`, because git refuses to delete a branch that is
   still checked out, then `git --git-dir=<common git dir> branch -d <branch>`.
   If git refuses, never use `-D`: run `git -C <worktree> switch <branch>`, report, and stop
   for this worktree.
6. **Sort again, then remove.** Sort once more. It must exit 0 and print no row; if not,
   stop for this worktree and report the paths; the worktree stays, detached. Otherwise
   remove the worktree as the very next action. Use the client's own mechanism when it can
   remove a worktree the session is not in (OpenCode's `worktree_remove`); otherwise run
   `git -C <primary> worktree remove <worktree>`. Never remove with force. No session move
   is needed: the session is not in it.
7. **Verify.** Run `git -C <primary> worktree list` and, when it had a branch,
   `git -C <primary> branch --list <branch>`. Neither may still show it.

Report one line for each named worktree: removed, or blocked with the reason and the
paths. The report does not end on a question.

Known residual risk: the note lists only worktrees where no chat was busy when it was
built. A chat could resume there, or a file appear, between the sort in step 6 and the
removal. The window is one tool call; there is no lock.
