# Layout skill eval harness (short design)

Status: approach approved by the user 2026-11-27 ("go as you recommended"); this doc records it.

## Intent

`vibe-project-layout` ships to other users, whose projects look nothing like this repo. Every
earlier live run used this repo itself, and the earlier A/B batches ran through `opencode api`,
whose shared service loads the user's installed plugins: the installed copy tracks `main`, so both
arms most likely read the new wording. Decisions about the skill need evidence from varied
projects, with the skill under test being the only copy loaded and the model pinned.

## Goals

- A dev-only harness that runs one coding prompt per trial in a small generated fixture project and
  scores the result from the project's real files.
- Only the fixture's plugin copy loads: each trial is a private `opencode run --standalone` with an
  empty config directory (`live_check.isolated_env`). The harness records every skill file the
  agent read and marks a trial invalid if it read a copy outside the fixture.
- The model is pinned per trial (`-m`).
- Results answer: does description C load the skill more often, and register new files more
  often, than the current description, in projects without a recorded layout? Does a recorded
  layout change registration? Does the agent offer to record a layout?

## Non-goals

- Not shipped: not in `scripts/manifest.py` or `package.json` `files`.
- No change to the shipped skill in this PR. The description variant is patched into the fixture's
  plugin copy only. A description change, if the data supports it, is its own PR.
- No paid routes: only `opencode-go/hungerg-free` (free) and the two included-subscription models
  already recorded for live checks (`claude-agent/haiku`, `openai/gpt-6.1-terra`).

## Approaches

1. **Generated fixtures plus isolated standalone runs (chosen).** Fixtures are defined in the script,
   so no fixture tree joins the repo; isolation reuses `live_check`'s tested environment.
2. **Fixture folders committed under `tests/`.** Easier to read, but adds many files and invites the
   layout questions the harness is meant to measure.
3. **Keep using `opencode api`.** Rejected: it loads the installed plugins, which is what broke the
   earlier comparison.

## Design

Fixtures (each a git repo with an initial commit, plus `.opencode/plugins/live-check` holding this
checkout's package, id renamed, `.opencode/` excluded from git):

| Fixture | Shape | A new module must appear in |
|---|---|---|
| `node-files` | `src/` modules, `test/`, `package.json` `files` lists each module | `package.json` |
| `python-modules` | `src/` modules, `tests/`, `pyproject.toml` `py-modules` lists each module | `pyproject.toml` |
| `monorepo` | `packages/api` and `packages/web`, each `package.json` `files` lists its modules | `packages/api/package.json` |
| `flat-barrel` | flat root modules with tests beside them; `index.js` re-exports every module | `index.js` |

The prompt asks for a duration-parser helper and a test (the monorepo prompt names the `api`
package), and says to create the files and stop, with no tests, installs or network.

Conditions: description `current` or `C` (Fable's symptom-keyed wording); recorded layout absent
(no `AGENTS.md`) or present (`AGENTS.md` with a `## Project layout` section naming the list).

Scoring per trial, from git against the initial commit, in the fixture and in any linked worktree
the agent created: the new code file (not a test); whether every required list gained its file
name; whether the skill loaded; whether the reply offers to record a layout. A trial is valid only
if the run finished, a code file exists and no skill copy outside the fixture was read.

Runs: E1 no layout, both descriptions, 4 fixtures x 3 reps on the free model (24). E2 recorded
layout, current description, 4 x 3 on the free model (12). E3 no layout, both descriptions, 4 x 1 on
each subscription model (16). Three trials run at once.

`live_check.run_model` gains a `timeout` parameter (default unchanged) so slow models finish.

## Not verified

Other users' plugin sets (the isolated config loads only vibe-workflow), other OpenCode versions,
and real-world projects. Three reps per cell detect only large effects.

## Left to the builder

Fixture contents, the offer regex, the result summary format.
