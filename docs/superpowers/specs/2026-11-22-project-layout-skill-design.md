# Project layout skill (short design)

Status: approved by the user 2026-11-22, including option A: the skill is always registered and
the `writing-plans` note is gated on "onboarded"; it is not a feature with its own setting or
onboarding question.
Council record: [`docs/vibe-workflow/council-sessions/2026-11-21_file-layout-skill/result.md`](../../vibe-workflow/council-sessions/2026-11-21_file-layout-skill/result.md).

## Intent

`vibe-workflow`'s own root holds seven `.js` modules next to the config files, and
`runtime/` is a flat list of sixteen Python modules. The user wants agents to keep files
and folders organized by module or domain from the start, so a young project does not need
a large refactor later. The skill must work in any coding project, not only this one, and
must respect the layout a project already has.

## Goals

- Agents know where a new file, test, script or doc goes before they create it.
- A project's layout is recorded once, briefly, so later sessions follow it without
  re-deriving it.
- When a restructure is asked for, it is planned, approved, and moved with history and
  references intact.
- Agents see concrete example layouts, marked as examples.

## Non-goals

- No file moves in the skill PR. This repo's own restructure is a later, phased PR series.
- No onboarding question, new setting, tool or runtime code.
- No duplicate finding, date folders, archiving or deletion (the Composio baseline's
  personal-folder material). Suspected dead files are reported as findings only.
- No restating of `safe-refactor`. The skill hands approved moves to it.
- No reference to `writing-plans` inside the skill text. The plugin note (below) is the
  only link between them.
- No subfolders inside `runtime/` (council item I11: deferred).

## Anti-goals

- A skill that pushes one preferred layout onto a project that already has its own.
- A skill that moves, renames or deletes without an approved plan.
- Layout guidance that goes stale: no full file listing in a recorded layout.

## Approaches

1. **Placement skill plus a recorded layout, hand moves to `safe-refactor` (chosen).** A short
   `SKILL.md` and two references. The council backed it 2 to 1 on shipping as a skill; every
   seat wanted moves handed to `safe-refactor`.
2. **Only a "Layout" section in each project's `AGENTS.md`.** Cheapest, but nothing tells an
   agent how to decide on a project that has none, and nothing gives example trees.
3. **A restructure-only skill built directly from the Composio flow.** Fits the user's
   present pain, but the council's top concern is drift during ordinary feature work, which
   a restructure-only skill never sees.

## Design

### The skill: `vibe-project-layout`

Files, in the house style of `vibe-pull-requests`:

- `skills/vibe-project-layout/SKILL.md`: a short entry whose one-line description names its
  triggers (creating, moving or renaming files or folders; organizing or restructuring;
  deciding where code, tests, scripts or docs go) and links the procedure.
- `references/procedure.md`: the rules below.
- `references/layouts.md`: at most four short example trees, each marked "example, not
  rule": a Node/TS app, a Python package, a mixed JS plus Python plugin (this repo's target
  layout), a monorepo. Each includes a placement decision for a test, a doc and a shipped
  asset, and lists convention-owned folders that must not be "fixed" (Next.js `app/`,
  Python `tests/`, `skills/<id>/SKILL.md`).

### Placement order (council I3)

An agent places a new file by the first answer that applies:

1. A path a framework, tool or package manifest requires.
2. The project's recorded layout (see below).
3. The nearest comparable existing file or folder.
4. The examples in `layouts.md`, where domain-first grouping breaks the tie. Tests and docs
   follow the project's own convention, never forced mirroring.

### Hints, not rules (council I6)

Signs a folder may be wanted (several files sharing a domain; a folder mixing domains and
growing) prompt one proposal at the end of the task. Nothing moves automatically. A folder
with one file is fine when the ecosystem requires it.

### Restructure on request (council I2)

Analyze the tree and measure what references each file (imports, manifests, package file
lists, test fixtures, docs) → write a source-to-destination map with the break points →
the user approves → moves go through `safe-refactor` with `git mv` → run the project's
tests → summarize. The approved map is the move log. An unexpected finding stops the work
and asks.

### Recording the layout (council I5)

With the user's approval, the skill writes at most about ten lines of placement rules
("where new X goes") into the project's existing agent instructions file (`AGENTS.md`, or the
file the project already uses). It records rules and exceptions, never a file listing, and
the same PR that moves files updates it.

### The `writing-plans` note (user decision, council minority view)

`pr-advice.js` already appends a note when `writing-plans` loads, for any onboarded
project (`SKILL_NOTES["writing-plans"]`, gate `onboarded`). That note pre-approves the
execution method. `writing-plans` itself covers how to split code into files (one
responsibility per file; follow established patterns) but never which folder a file goes in
or that moving files needs approval. The note gains one sentence after the existing text,
before the shared override sentence:

> before you fill in each task's Files block, follow the vibe-project-layout skill: put every
> new file and test where the project's recorded layout says. If a file belongs elsewhere or a
> folder needs splitting, propose it as its own task for your human partner to approve, never
> as a silent move.

Same gate, no new setting. The existing note text is unchanged.

## Constraints

- Smallest sufficient change; no new runtime code, no dependencies.
- Package inventory: add the three skill files to `scripts/manifest.py` `FILES` and the skill
  folder to `package.json` `files`. `scripts/validate.py` checks the export inventory.
- Several tests assert the registered skill list and the exact `writing-plans` note text;
  they change with the skill and the note. The skill is registered in `index.js` beside
  `vibe-pull-requests`.
- `README.md` is edited only through the `impeccable` skill (repo rule). It gets a short
  "Keep files organized" entry. `PRODUCT.md` "Available now" and `docs/dependencies.md` list
  the shipped pieces and are updated the same way the council skill did.
- Failing test before behavior changes; independent review before acceptance.
- Work on a task branch in this worktree (`HEAD` is detached at `b5d5faa`). The council
  record under `docs/vibe-workflow/council-sessions/` ships with the PR.

## Follow-up (separate PRs, not this one)

Phased restructure of this repo, driven by the skill, each its own PR with its own plan:

1. The six root modules move to a flat `src/`; `index.js` stays at the root because
   `scripts/validate.py` pins `exports` to `./index.js`. Known break points: the
   `onboarding.js` skill URL, `scripts/manifest.py`, `package.json` `files`, two tests that
   copy root modules into fixtures (`tests/plugin.test.js`, `tests/onboarding.test.js`),
   `docs/dependencies.md`.
   The same PR splits `pr-advice.js` by trigger (user decision 2026-11-22): `pr-advice.js`
   keeps the shell scanner, `classify` and `advise` (shell-command notes); a new
   `skill-advice.js` takes `SKILL_NOTES` and `skillNote` (skill-load notes, including the
   `writing-plans` note and its layout sentence). Open for that PR's plan: how the two share
   the `execute.after` hook (a small dispatcher, or two registrations, which are unverified on
   the host), and whether the files end up in a `src/advice/` folder. The layout PR does not
   touch this: its sentence goes into `pr-advice.js` as it stands today.
2. `tests/` and then `runtime/`, decided when each is planned. `runtime/` and `scripts/`
   stay separate and the dev tools stay in `scripts/` (council I9); nested Python
   subpackages stay deferred (I11). Decided 2026-11-23: `tests/` is grouped by domain (PR
   #380). `runtime/` stays flat. An `ast` count that includes function-level imports found
   two import cycles (`claude_usage` and `native_catalog`; `routing_observations` and
   `opencode_account`), so the routing and accounts clusters depend on each other and any
   split would be cyclic, or layered rather than by domain. A split would also rewrite
   24 imports, the validator's single-level rule, `scripts/manifest.py`, `scripts/select.py`
   and nine test strings in the shipped router's import surface, with no behavior gain.
   Revisit when the cycles are gone or `runtime/` passes about 25 modules.

## Not verified

- Whether agents pick the skill up during ordinary feature work, with or without the plan
  note. A live check (`scripts/live_check.py`) runs inference and needs the user's go-ahead.
- Other OpenCode versions; the note path was observed live only on 2.3.2 for the existing
  method note.

## Left to the builder

Exact wording of the skill's description, the trees in `layouts.md`, and the procedure text,
within the rules above; the test names; whether the new skill registers before or after
`vibe-council` (list-order assertions change either way).
