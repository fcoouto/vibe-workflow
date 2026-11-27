# Cleanup without asking

## Status and intent

The user reported that agents clean up merged worktrees only when asked, so obsolete
worktrees stay on disk. Approved in conversation on 2026-11-27: option 1 (sort leftover
files, delete rebuildable ones, ask about real work before the merge), extended to older
merged worktrees, built as approach B. The user's constraint: "Hooks should be very very
thin and quick." This document does not authorize implementation; the plan follows it.

Why it fails today (`skills/vibe-pull-requests/references/worktree-cleanup.md`):

1. Check 4 stops on any line of `git status --porcelain --ignored`. A worktree where tests
   ran always has some (`node_modules/`, `__pycache__/`, `.DS_Store`), so cleanup is blocked
   by design, not by accident.
2. The stop says "leave the worktree for the user to decide" with no moment or form for
   the question. In the reported case the agent asked once, as the last line of a merge
   report, and the user had to ask again.
3. The check runs only after the merge, when the decision could have been taken while CI ran.
4. Only the session's own worktree is eligible (check 3). Merges made later or from another
   chat are never revisited.

Evidence in this repository (cleanup on), 2026-11-27: 7 linked worktrees, 5 merged into
`origin/main` and still on disk. Four were blocked only by ignored entries
(`runtime/__pycache__/`, `dist/`, `.superpowers/`); one (`witty-otter`) is clean and
detached, likely a cleanup that stopped after the branch delete. A second project hit a
worktree whose `.git` file pointed at the repository's old path.

## Goals

- After a merge, the session's worktree is removed without the user asking, whenever the
  only leftovers are rebuildable.
- Older merged worktrees of this repository are found and removed the same way, without
  the user asking.
- Real work is never deleted silently. The question about it comes before the merge, as
  its own clear question, and the merge report never ends on a buried question.
- The plugin's added cost stays tiny: a few local git reads at three moments, bounded by
  a hard timeout, silent on any failure.

## Non-goals

- Deleting remote branches (GitHub's setting does that; unchanged).
- Detecting squash or rebase merges locally. The agent still confirms each merge with `gh`.
- Cleaning other repositories, or worktrees outside `git worktree list`.
- Cleanup when `worktree_cleanup` is not `true`.

## Anti-goals

- Deleting untracked files, `.env`-like files, or any ignored path not on the rebuildable list
  without the user's answer.
- Using `--force`, `-D`, or `worktree_remove` with `force`.
- Breaking a chat that is still working in a worktree.
- A plugin hook that runs per step, polls, calls the network or runs a write command.
- Touching the primary checkout's files, HEAD or index.

## Approaches

- **A. Skill rules plus reminder notes.** The reference gains the file sort, the
  `[READY]` check and the sweep; existing notes remind the agent at three moments. No new
  plugin reads. Weakness: the generic reminder is what was skipped today.
- **B. A, plus the plugin names the merged worktrees — selected.** At the same three moments
  the plugin runs two local read-only git commands and its note names each merged worktree.
  A named list is harder to skip. Measured 18.6 ms sequential (about 10 ms in parallel) on
  7 worktrees and 18 branches; cost does not grow with the count.
- **C. A plugin tool that deletes.** Rejected: the plugin never runs write commands.

## Design

### 1. Sorting leftover files (reference, replaces check 4)

Run `git status --porcelain --ignored` in the worktree. Every line falls in one group:

- **Rebuildable**: an ignored entry (`!!`) whose path matches the fixed list below. The agent
  deletes these without asking, then reruns the status command.
- **Real work**: every other line. Untracked (`??`), modified, staged, and ignored paths not
  on the list (`.env`, `reports/`, `.superpowers/`, private evidence folders).

Fixed list, matched on any path segment: `node_modules/`, `__pycache__/`, `*.pyc`,
`.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.DS_Store`, `dist/`, `build/`,
`coverage/`, `.coverage`, `.test-artifacts/`. The list lives in the reference only; a
project cannot extend it in this change.

After deleting rebuildable entries the status must be empty, or the worktree is blocked on
real work.

### 2. Before the merge (reference, procedure "Merging", `[READY]` note)

When the agent marks a pull request `[READY]` and cleanup is on, it runs the sort on its
own worktree:

- Only rebuildable or nothing: no question; cleanup will run after the merge.
- Real work: ask now, in a message of its own, listing each path, with three choices per
  group of paths: **Commit to this PR**, **Archive** (move to
  `~/.local/share/vibe-workflow/archive/<repo>/<branch>/`, outside every checkout), or
  **Discard**. Committing makes the evidence stale, so the pull request returns to
  `[BLOCKED]` per the lifecycle. The answer is acted on before the merge.

When the user merges (`agent_merge` off), the same question goes in the `[READY]` message.

### 3. After the merge (reference)

The existing checks and order stay, except check 4 becomes the sort in section 1. The final
report states exactly one of: **Cleanup verified** (with the step 7 output), or **Cleanup
blocked** (the reason and the paths). It never ends on an open question; any question was
asked at `[READY]`.

### 4. Sweeping older merged worktrees (reference, new section)

At the three moments below, for each worktree the plugin's note names, the agent:

1. Confirms the merge with `gh pr list --head <branch> --state merged --json number,headRefOid`
   (or, for a detached worktree, `git merge-base --is-ancestor <HEAD> origin/<default>` after
   a fetch) and that `HEAD` equals the merged head or is an ancestor of the default branch.
2. Runs the sort. Rebuildable-only: delete, continue. Real work: skip it and list it once
   in the report as blocked, with its paths. No question mid-sweep.
3. Runs `git --git-dir=<common git dir> branch -d <branch>` (never `-D`), then removes the
   worktree with the client's mechanism, never with force. No session move is needed
   because the session is not in it.
4. Verifies with `git -C <primary> worktree list` and reports one line per worktree.

Check 3's ownership rule is relaxed for the sweep only: a worktree is eligible when the
plugin's note named it and step 1 confirmed the merge. Locked worktrees and ones with
submodules are skipped, as today.

A worktree whose git pointer is broken (`git worktree list` marks it `prunable`, or its
`.git` file names a missing path) gets `git -C <primary> worktree repair <path>` first, which
rewrites git's own metadata only. If it is still broken, it is skipped and reported.

### 5. The plugin's note (`src/pr-advice.js`, `src/skill-advice.js`)

Moments, only when `git_workflow` is on and `worktree_cleanup` is `true`:

- after a successful `gh pr ready` or a retitle to `[READY]` (existing match),
- after a successful `gh pr merge` (existing match; skipped with `--auto`),
- when `using-git-worktrees` loads (existing note).

Reads, in parallel, from the primary checkout, sharing one 500 ms deadline:

- `git worktree list --porcelain`
- `git for-each-ref --merged origin/<default> --format=%(refname:short) refs/heads`

For detached worktrees, one `git merge-base --independent origin/<default> <sha>...`
call (a sha that is an ancestor of the default branch drops out of the output).

Excluded from the list: the primary checkout, the calling session's own worktree (the
after-merge steps cover it), locked worktrees, and any worktree where a session is busy
now (from `ctx.session.list`, already used by session titles; the builder confirms it
exposes each session's directory). If the deadline passes or anything fails, the note
omits the list and keeps its current text. No cache, no background work, no network.

Note shape, appended to the existing note: "vibe-workflow: merged worktrees still on disk:
<name> (<branch>), …. Sweep them per the worktree cleanup steps in vibe-pull-requests."
At most 10 names, then "and N more".

## Constraints

- Plugin: read-only git, no `gh`, no network, no new dependencies; Node ES modules.
- Settings unchanged: `worktree_cleanup` stays the only switch, read at the main project path.
- README changes only through the `impeccable` skill. New shipped files go in `package.json`
  `files` and `scripts/manifest.py`.
- No real cleanup of this repository's worktrees during development, unless the user asks.

## Proof expected

- Unit: the note lists merged worktrees and omits the primary, the caller's own, locked
  and busy ones; detached merged worktrees are found; a timeout or git failure yields the
  current note unchanged; nothing is read when cleanup is off or the command did not match.
- Timing: a test asserts the reads are skipped outside the three moments; a measurement
  on this repository is recorded in `docs/development.md`.
- Git fixture (extends `tests/skills/worktree-cleanup.test.js`): a worktree with only
  listed ignored entries becomes removable after deleting them; `.env`, an untracked file,
  or an unlisted ignored folder still blocks; `worktree repair` fixes a moved-repository
  pointer.
- Reference text: the list, the three choices, the `[READY]` timing, the
  "verified or blocked" report, the sweep steps, and the never-force rules are present.

## Calls made (the writer's; the user can change any)

- The rebuildable list is fixed in the reference; no per-project list.
- Archive location is outside every checkout, under `~/.local/share/vibe-workflow/archive/`.
- The sweep never asks mid-way; blocked worktrees are reported, and asked about only at the
  next `[READY]` of that branch, which may never come. A blocked older worktree therefore
  stays until the user acts on the report.
- An idle chat whose folder is a merged worktree loses its folder when swept. Only busy
  sessions are protected.
- `--auto` merges get no sweep list on the merge note; the next moment covers them.

## Left to the builder

Exact note wording, how the parallel reads and deadline are shared with existing code,
whether `ctx.session.list` gives directories (if not, say so and fall back to excluding
nothing beyond the caller's own worktree, recorded as a known risk), and the reference's
layout.
