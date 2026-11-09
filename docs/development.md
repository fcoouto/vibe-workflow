# Development guide

How to work on this repository: where to make changes, which checks to run and how changes reach `main`. [AGENTS.md](../AGENTS.md#where-to-look) says when to read it. Local setup and installing the plugin are in the README's [Development](../README.md#development) section.

## Where to work

- Sessions start in the primary checkout on `main`; use it only to read, coordinate and fast-forward. Before creating or changing files for a task, move to a task worktree on its own branch from current `main`, created with the client's native worktree tool when it has one, and edit, commit and push only there. Setup records, documentation and one-line fixes are task changes too.
- `main` is the merge target and the base for task branches, never a working branch: do not commit on it or push to it; it changes only when a PR merges.
- Preserve unfinished work separately and retain newer changes when reconciling older branches.

## Checks

- Validate with `python3 -B scripts/validate.py`.
- Run tests in proportion to what a change can break. While working, run the affected modules with `python3 -B scripts/test_sharded.py <module-or-glob>...`: the test modules that import or run a changed file, directly or through another module; that read a changed instruction or policy file, including by reading its whole directory; or that are named for the changed area. Find them with `grep -l` in `tests/*.py` for the changed file's name, its directory and the modules that import it, repeating for their importers (a change to `runtime/task_statuses.py` reaches every test that imports `runtime/state.py`). Before marking a PR `[READY]`, run the affected modules once more and `python3 -B scripts/test_sharded.py --fast`.
- Run the full suite once per slice, before marking its PR `[READY]`, only when the slice changes shared core: `hooks/`, `scripts/workflow.py`, `scripts/validate.py`, `scripts/test_sharded.py`, `tests/__init__.py`, `runtime/__init__.py`, `runtime/adapters/`, `runtime/state.py`, `runtime/startup.py`, `runtime/evidence.py`, `runtime/changes.py`, `runtime/delegation.py` or `runtime/engine.py`. Also run it before creating a release archive, and when the affected modules cannot be identified. Reuse a full run only for the same candidate and base.
- `python3 -B scripts/test_sharded.py` runs the full suite in parallel, longest first (predicted at about 10 minutes on an idle machine, over 18 minutes when the machine was loaded), and lists failing tests; `python3 -B -m unittest discover -s tests` runs it serially in about two hours. `--fast` skips the slow modules and does not replace a required full run; `--update-timings` records timings for the modules whose units all passed in `tests/timings.json`. Each unit runs with a private `HOME` and the summary lists files a unit wrote there (tests must never write to the real home). Rerun a failure alone before judging it, since timing-sensitive tests can fail on an overloaded machine.
- Report package validation and observed client execution separately.

## Pull requests and merging

- Publish completed task changes by committing, pushing the task branch and opening a draft PR against `main`. The [PR lifecycle](../policies/08-merge-readiness.md#pr-lifecycle) sets its `[BLOCKED]` and `[READY]` prefixes.
- Agents may merge here (see the [setup record](setup.md#decisions)). Merge only completed, independently reviewed slices against current `main`, in PRs marked `[READY]`, after local checks pass.
- The repository has no CI or branch protection, so these rules are not enforced mechanically.
- Create release archives only when requested.
