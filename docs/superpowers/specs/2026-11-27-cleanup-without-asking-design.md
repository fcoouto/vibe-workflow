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
- Touching the primary checkout's files, HEAD or index, except writing new files under
  `.vibe-workflow/archives/` through the archive script (section 7).
- Deleting, moving or archiving anything before the merge is confirmed.

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
  deletes these without asking, only after the merge is confirmed, then reruns the status
  command.

Nothing is deleted, moved or archived before the merge is confirmed (user's rule,
2026-11-27). Sorting is read-only; the actions it leads to run after the merge.
- **Real work**: every other line. Untracked (`??`), modified, staged, and ignored paths not
  on the list (`.env`, `reports/`, `.superpowers/`, private evidence folders).

Fixed list of tool caches that never hold user files: `node_modules/`, `__pycache__/`,
`*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.DS_Store`. An entry is
rebuildable only when the path git reports itself ends in a listed name. A reported parent
such as `!! build/` is not rebuildable even if a listed name sits inside it, because git
collapses an ignored directory into one line and its contents (`build/.env`, evidence) are
not visible. Build and test outputs (`dist/`, `build/`, `coverage/`, `.test-artifacts/`) are
real work: they can hold evidence, so they are asked about at `[READY]` (section 2), with
Discard as the recommended choice. The list lives in the reference only; a project cannot
extend it in this change.

After deleting rebuildable entries the status must be empty, or the worktree is blocked on
real work.

### 2. Before the merge (reference, procedure "Merging", `[READY]` note)

When cleanup is on, the agent runs the sort on its own worktree **before** it marks a pull
request `[READY]`, and it is part of readiness: the pull request stays a `[BLOCKED]` draft
until the sort is clean or the user has answered. So nobody can merge before the question
is settled. The check itself only reads.

- Only rebuildable or nothing: no question; cleanup will run after the merge.
- Real work: ask in a message of its own, listing each path. Choices per group of paths:
  - untracked or ignored paths: **Commit to this PR**, **Archive** (move to the project's
    archive folder, section 7), or **Discard**;
  - modified, staged or deleted tracked files: **Commit to this PR** or **Keep the
    worktree** (no archive: moving the visible file would lose a different staged version,
    and a staged deletion has nothing to move).

  Recommended choices for council records (`docs/vibe-workflow/council-sessions/<date>_<title>/`,
  which the `vibe-council` rules commit with the work):
  - an untracked session folder that has `result.md`: **Commit to this PR (Recommended)**;
  - an untracked session folder without `result.md` (a run that did not finish):
    **Archive (Recommended)**, since it may hold members' answers;
  - the folder shown as ignored (`!!`), because the project chose to keep records out of
    git: **Archive (Recommended)**.

  **Commit** is acted on before `[READY]`; it adds to the head, so readiness evidence is
  gathered after it. **Archive** and **Discard** are recorded and run only after the merge
  is confirmed (section 3); if the pull request closes unmerged, nothing is touched. The
  answer lives in the session. If the merge is later seen from another session, that
  session does not know the answer, so the sweep treats those paths as real work: skipped
  and reported (section 4).

The `[READY]` note (section 5) is a backstop reminder; it names merged worktrees, not this
question. When the user merges (`agent_merge` off), the flow is the same.

### 3. After the merge (reference)

The existing checks and order stay, except check 4 becomes: once checks 1–3 and 5 pass, run
the archive and discard answers from section 2, delete rebuildable entries, then rerun the
sort; it must now be empty, or cleanup is blocked. The final
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
3. Runs `git -C <worktree> switch --detach` (git refuses to delete a branch still checked
   out), then `git --git-dir=<common git dir> branch -d <branch>` (never `-D`). A detached
   worktree has no branch to delete and skips this. If git refuses, it switches the worktree
   back, reports and moves to the next one.
4. Reruns the sort immediately before removing, and stops for that worktree if anything new
   appeared. Then removes the worktree with the client's mechanism, never with force. No
   session move is needed because the session is not in it.
5. Verifies with `git -C <primary> worktree list` and reports one line per worktree.

Known residual risk: the busy check (section 5) is taken when the note is built, and a chat
could resume, or a file appear, between the rerun in step 4 and the removal. The window is
one tool call; no lock is added.

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

For each detached worktree, one `git merge-base --is-ancestor <sha> origin/<default>`
(about 7 ms each, inside the same deadline). `--independent` is not used: it drops a head
that is an ancestor of another detached head, and keeps one equal to the default branch.

Excluded from the list: the primary checkout, the calling session's own worktree (the
after-merge steps cover it), locked worktrees, and any worktree where a session is busy
now (from `ctx.session.list`, already used by session titles; the builder confirms it
exposes each session's directory). If the deadline passes or anything fails, the note
omits the list and keeps its current text. No cache, no background work, no network.

Note shape, appended to the existing note: "vibe-workflow: merged worktrees still on disk:
<name> (<branch>), …. Sweep them per the worktree cleanup steps in vibe-pull-requests."
At most 10 names, then "and N more".

### 6. Settings promise (user's decision, 2026-11-27)

An existing `worktree_cleanup: true` authorizes the new behavior; nothing is asked again.

- Onboarding's question 3 context (`skills/vibe-onboard-project/references/procedure.md`,
  today "It never removes unmerged work, uncommitted work or ignored files; it stops and
  tells you.") becomes: "It also removes older merged worktrees and tool caches such as
  `node_modules`. It never removes unmerged work, and asks before the merge about any other
  files left in the folder." Exact wording goes through the same shape test; the builder may
  tighten it.
- The first sweep note a plugin process sends adds one sentence telling the agent to state
  the new behavior in its report: "Cleanup now also removes older merged worktrees and tool
  caches; other files are asked about before the merge." Tracked in memory only, so it may
  repeat after a restart.

### 7. Archive folder (user's choice, 2026-11-27)

Archive moves paths to `<primary checkout>/.vibe-workflow/archives/<YYYY-MM-DD>_<branch>/`,
keeping each path's place relative to the worktree root (user's choice, 2026-11-27, the
same `<date>_<title>` pattern as `docs/vibe-workflow/council-sessions/`). `<YYYY-MM-DD>` is
the local date when the archive runs; every `/` in the branch becomes `-`, so the folders
stay flat (`fix/title-sync` becomes `2026-11-27_fix-title-sync/`). No leading dot, matching
PR #408's `.vibe-workflow/security-reports/`. If that folder already exists, `-2`, then
`-3` (up to `-99`) is added; nothing is overwritten. The folder is the second exception to "never touch the
primary checkout", next to `.vibe-workflow/settings.json` (PR #408's reports folder is a
third), and like that file it does not make the primary checkout unclean for the
fast-forward rule. Writing it changes no tracked file, HEAD or index.

How it stays out of git (user's choice, 2026-11-27): the folder ignores itself with its own
`.gitignore` containing exactly `*`, the same pattern as PR #408's reports folder. A
folder's own `*` is the deepest rule, so no other ignore file can undo it; it needs no
commit, and already-onboarded projects never show a new untracked path. Rejected: one
tracked `.vibe-workflow/.gitignore` for all private folders, because a task branch could
edit it to stop ignoring a folder, and each project would need a commit before it works.

How it is written (user's choice, 2026-11-27): never by hand. PR #408 found that hand
writes into such a folder are unsafe (a linked `.gitignore` could be written through), and
routes every write through a bundled script. Archive does the same and shares #408's
folder checks rather than copying them:

- The private-folder checks of `skills/vibe-security-review/scripts/store_report.py` move
  into one shared module that both scripts use: find the main checkout, refuse a linked or
  non-directory `.vibe-workflow` or target folder, refuse tracked files in it, require the
  checkout and `.vibe-workflow` to belong to this user and not be group- or world-writable,
  create the folder `0700`, keep its `.gitignore` at exactly `*` (replaced atomically, never
  through a link), and confirm with `git check-ignore` before writing. `store_report.py`
  keeps its behavior and its tests.
- A new archive script in `skills/vibe-pull-requests/scripts/` uses that module and moves
  each approved path with a rename only: never a copy, never following a link, refusing a
  path git tracks, a path outside the worktree, a destination that exists, and a move
  across file systems. It prints each archived destination, or `refused: <reason>` and
  moves nothing more.
- This depends on PR #408 being merged; the branch is rebased onto it first.

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
- The sweep never asks mid-way; blocked worktrees are reported, and asked about only at the
  next `[READY]` of that branch, which may never come. A blocked older worktree therefore
  stays until the user acts on the report.
- An idle chat whose folder is a merged worktree loses its folder when swept. Only busy
  sessions are protected.
- `--auto` merges get no sweep list on the merge note; the next moment covers them.
- Only tool caches are deleted without asking; build and test outputs are asked about at
  `[READY]` with Discard recommended (from Astra's review: `!! build/` can hide `build/.env`).
- The real-work question comes before `[READY]`, and the pull request stays `[BLOCKED]`
  until it is settled (Astra's review).
- Archive covers untracked and ignored paths only; changed tracked files are committed or
  the worktree is kept (Astra's review).
- Squash and rebase merges stay out of the sweep: their tips are not on the default branch,
  and once GitHub deletes the remote branch, `branch -d` refuses them anyway. Allowing them
  would mean relaxing the never-`-D` rule, which this design does not do.

## Review

Astra (builder check plus flaws, 2026-11-27) raised three questions and five flaws. The
allowlist, the readiness order, the archive scope, the missing detach, the `--independent`
test and the residual busy-check window were settled above as the writer's calls; squash
merges stay a non-goal. The upgrade question was the user's to answer: existing
`true` applies the new behavior (section 6).

## Left to the builder

Exact note wording, how the parallel reads and deadline are shared with existing code,
whether `ctx.session.list` gives directories (if not, say so and fall back to excluding
nothing beyond the caller's own worktree, recorded as a known risk), and the reference's
layout.

## Addendum: what changed during the build (2026-11-28)

Task reviews and the final security review changed parts of this design. Where this
addendum and the sections above disagree, the addendum and the shipped reference
(`skills/vibe-pull-requests/references/worktree-cleanup.md`) win.

- **Leftover handling runs through `skills/vibe-pull-requests/scripts/leftovers.py`**
  (`sort`, `expand`, `manifest`, `discard`, `caches`, `identity`), not through hand-run
  `git status` and `rm`. Reviews showed that quoted names, partial listings, link-following
  and status changes made the prose steps unsafe. The rebuildable list now lives in the
  script and the reference (section 1 said "the reference only").
- **Discard and cache removal** recheck each path's git status and content digest just
  before removing it, through descriptors that follow no link and cross no device. Any
  nested `.git`, an `assume-unchanged` or `skip-worktree` entry, or a paused git operation
  blocks cleanup. A failed sort may be answered with **Keep the worktree**, so `[READY]` is
  never blocked for good.
- **Commit** is offered only for untracked paths, and only when exactly the approved paths
  are staged and committed.
- **Archive** (`archive.py`) takes `--head` and refuses unless the worktree's registration,
  backlink, live branch and HEAD match the confirmed merge. Detached worktrees are not
  archived.
- **The note** names entries as a JSON array, only for branches with their own pushed
  upstream (never-started branches are not named), with at most 8 concurrent detached
  checks. It is suppressed when any running session lacks a folder while others report one.
  Being named is a candidate, never authorization.
- **The sweep never runs `git worktree repair`** (section 4 said it repairs first): a broken
  worktree is skipped and reported with the command. Hooks are disabled for the detach and
  branch delete; user-configured filter drivers are an accepted residual.
- Every ruling behind these changes, with its cost if wrong, is listed in the pull request.
