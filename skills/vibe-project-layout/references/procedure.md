# The project layout procedure

Use this when you create a file or folder, and when the user asks you to organize or
restructure a project. A project that has its own layout is respected: learn it, keep
new files consistent with it, and never push it toward the examples. Move, rename or
delete nothing on your own: a restructure needs a plan the user approved, while a
single move or rename the user asks for directly needs no further approval.

## Placement order

Put a new file where the first of these answers:

1. A path a framework, tool or package manifest requires (Next.js `app/`, a package's
   `main`, a skill's `skills/<id>/SKILL.md`).
2. The project's recorded layout: the placement rules in its agent instructions file
   (see Recording the layout).
3. The nearest comparable existing file or folder: put the file beside what it most
   resembles, with the same naming.
4. The [example layouts](layouts.md), only when 1 to 3 are silent: there, group by module
   or domain, with layers only inside a domain.

Tests and docs follow the project's own convention, whether mirrored, colocated or flat.
Never force one on a project that uses another. Test discovery outranks mirroring.

## Hints, not rules

Some signs suggest a folder may help: several files that share a domain, or one folder
that mixes domains and keeps growing. Treat them as a prompt, not a trigger. Raise one
proposal at the end of the task and let the user decide. Do not move files on your own.
A folder with one file is fine when the ecosystem requires it.

## Restructure on request

Run this only when the user asks for it, or approves your proposal.

1. **Analyze.** Read the tree. For every file you may move, measure what refers to it:
   imports, package manifests and file lists, test fixtures, build and export lists,
   docs and scripts.
2. **Write the source-to-destination map.** One line per move, with the references that
   break and how each is fixed. Mark anything unusual.
3. **Get it approved.** Show the map to the user. Nothing moves before they approve it.
4. **Move.** Hand the approved moves to the `safe-refactor` skill when it is installed;
   otherwise make them yourself, one map line at a time. Use `git mv` so history
   survives, and fix every reference the map listed.
5. **Check.** Run the project's tests and any build or export check. Fix what the move
   broke, and nothing else.
6. **Summarize.** Report what moved, what changed in references, and the test result. The
   approved map is the move log.

Stop and ask when you meet a surprise: a file the map did not expect, a reference you
cannot fix mechanically, a failing test that predates the move. Report suspected
dead files as findings; never delete them.

## Recording the layout

With the user's approval, write the project's placement rules into its existing agent
instructions file (`AGENTS.md`, or the file the project already uses), under a
`## Project layout` heading; reuse a layout section the file already has. If the project
has no agent instructions file, ask before creating one. Keep it to about ten lines that
say where new code, tests, scripts and docs go, plus any exception worth knowing. Record
rules, never a file listing, because a listing ages. When a restructure changes the
layout, update those lines in the same pull request. When the project has no recorded
layout yet, offer once, at the end of the task, to record the layout you followed.
