# Development guide

How to work on this repository: where to make changes, which checks to run and how changes reach `main`. [AGENTS.md](../AGENTS.md#where-to-look) says when to read it. Local setup and installing the plugin are in the README's [Development](../README.md#development) section.

## Where to work

- This project's designated primary branch is `main`; sessions may start in its primary checkout. Follow [Task Planning](../policies/02-task-planning.md#planning-and-verification) and [delivery preparation](../clients/delivery.md#prepare-the-destination) for task entry, edits and checkout-affecting review work. Task branches start from current `main` in task worktrees.
- `main` is the merge target and the base for task branches, never a working branch: do not commit on it or push to it; it changes only when a PR merges.
- Preserve unfinished work separately and retain newer changes when reconciling older branches.
- After a merge, or when synchronizing merges made elsewhere, run [Graphify maintenance](../clients/graphify-maintenance.md) for Graphify-enabled projects. Its ignored generated output is the only maintenance write allowed in the primary checkout; tracked task changes still belong in task worktrees.

## Checks

- Validate with `python3 -B scripts/validate.py`.
- Run tests in proportion to what the change can break. Select affected modules with `python3 -B scripts/test_sharded.py <module-or-glob>...`, covering changed behavior, direct and transitive callers, shared fixtures, instruction/configuration consumers and relevant failure paths. Record the selection and its limits. Check representative tasks for instruction changes; trivial documentation edits need static checks, not artificial unit tests. Before `[READY]`, ensure relevant evidence is current; rerun only checks invalidated by changes. Neither `--fast` nor a fixed file list is a mandatory gate. A full run may be proportionate for genuinely cross-cutting or uncertain impact.
- Releases require a successful full-suite run on the exact committed release candidate before tagging, publishing or creating its archive. Follow [release qualification](release.md#qualify-the-release); reuse a full run only for the same candidate, base and execution conditions. Disposable test packages and archive-building tests are not releases.
- `python3 -B scripts/test_sharded.py` runs the full suite in parallel, longest first (predicted at about 10 minutes on an idle machine, over 18 minutes when the machine was loaded), and lists failing tests; `python3 -B -m unittest discover -s tests` runs it serially in about two hours. `--fast` skips the slow modules and does not replace a required full run; `--update-timings` records timings for the modules whose units all passed in `tests/timings.json`. Each unit runs with a private `HOME` and the summary lists files a unit wrote there (tests must never write to the real home). Rerun a failure alone before judging it, since timing-sensitive tests can fail on an overloaded machine.
- Leave the checkout unchanged while its tests run, and commit first. Tests pin the package source and check the prepared instruction map, so an edit mid-run, even to documentation, can fail unrelated tests, for example with "Prepared instruction map is stale" or `'blocked' != 'applied'`. If the checkout changed during a run, discard the run and rerun it. After changing an instruction or policy file, regenerate the map with `python3 -B scripts/read_instructions.py --prepare` before validating or testing.
- Report package validation and observed client execution separately.

## Pull requests and merging

- Publish completed task changes by committing, pushing the task branch and opening a draft PR against `main`. The [PR lifecycle](../policies/08-merge-readiness.md#pr-lifecycle) sets its title prefix: `[BLOCKED]`, `[READY]` or `[SUPERSEDED]`.
- Agents may merge here (see the [setup record](setup.md#decisions)). Merge only completed, independently reviewed slices against current `main`, in PRs marked `[READY]`, after local checks pass.
- The repository has no CI or branch protection, so these rules are not enforced mechanically.
- Create release archives only when requested, and run `python3 -B scripts/validate.py --release` first: it fails when the package version already has a release tag but shipped content changed since, so raise the version first.
