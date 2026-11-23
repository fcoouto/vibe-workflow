# Tests layout (short design)

Status: awaiting the user's review. Follow-up 2, first PR, of
[`2026-11-22-project-layout-skill-design.md`](2026-11-22-project-layout-skill-design.md).
The user chose the domain-based approach and deferred `runtime/` to its own PR.

## Intent

`tests/` is one flat folder of 32 files that mixes three kinds of subject (the plugin's
JavaScript, the shipped skills, the Python runtime) and the development tooling's checks, in
two languages. The user wants files grouped by module or domain so a new test has an obvious
home, as the `vibe-project-layout` skill recommends.

## Goals

- Tests live in a folder named for what they test, so a reader finds `pr-advice`'s test next to
  the other plugin tests and the router's tests next to the other runtime tests.
- Every test still runs, with the same counts as before the move (244 Node, 215 Python on
  `4a16045`), plus two new guard tests (217 Python after the move).
- History survives (`git mv`); every reference to a moved path is fixed.
- The recorded layout in `AGENTS.md` describes the new shape in the same PR.

## Non-goals

- No change to `runtime/` or `scripts/` layout. `runtime/` stays separate from `scripts/`;
  its own layout is a separate PR with its own plan.
- No change to test content or behavior. Only paths, imports and the commands that run them.
- No move of the `verify_*` operator scripts to `scripts/` (a later, separate PR if wanted).
- No new tooling, runner or dependency.

## Anti-goals

- A move that silently runs fewer tests, or runs Python tests against the real home folder.
- A folder named `runtime` that shadows the shipped `runtime` package when run the old way.

## Approaches

1. **Keep it flat.** No cost, but it is the mixed-domain folder the skill flags.
2. **By domain, mirroring what is under test (chosen).** Each folder is almost single-language
   and a new test has an obvious home.
3. **By language** (`tests/js`, `tests/py`). Fewer path edits, but it splits by technical
   layer, which the skill advises against.

## Design

| New folder | Moves in | Subject |
|---|---|---|
| `tests/plugin/` | `council`, `onboarding`, `plugin`, `pr-advice`, `pr-hook`, `project`, `session-facts`, `session-titles`, `skill-advice` (`.test.js`) | `index.js`, `src/` |
| `tests/skills/` | `council-skill`, `project-layout-skill`, `pull-request-skill`, `workspace-skill`, `worktree-cleanup` (`.test.js`) | `skills/` |
| `tests/runtime/` | `test_chatgpt_limit`, `test_council`, `test_readers`, `test_routing`, `test_scores`, `test_selector`, `test_storage` (`.py`), `native_reader.py` | `runtime/` |
| `tests/tooling/` | `test_live_check`, `test_opencode_version`, `test_package`, `test_verification`, `test_workspace_check` (`.py`), `verify_group.py`, `verify_native_loading.py`, `verify_cancel.mjs`, `opencode-verified.json`, plus the new `test_layout.py` | `scripts/`, packaging, native checks |

`tests/__init__.py` stays at the top: it redirects `HOME` for the whole suite. `tests/runtime/`
and `tests/tooling/` get an `__init__.py`; the two JavaScript folders do not need one.

Commands become:

```sh
python3 -B -m unittest discover -s tests -t .
node --test "tests/**/*.test.js"
```

Why the `-t .` is required (measured on a throwaway copy): without it, Python imports the
folder as the top-level name `runtime` and finds only 114 of the 215 tests, with failures.
With it, all 215 are found and every test is imported as `tests.<folder>.<module>`, so
`tests/__init__.py` (the `HOME` isolation) always runs first. Today a single test file that
does not import `tests` runs against the real home; a selective run no longer can. A small
guard file, `tests/tooling/test_layout.py`, fails with the right command in its message when
the suite is started the old way: it asserts its own module name starts with `tests.` and
that `HOME` is not the account's real home. The Node glob must be quoted so Node expands
it, not the shell.

References the move breaks (all fixed in this PR):

- The two commands in `AGENTS.md`, `README.md` and `docs/development.md`.
- `scripts/live_check.py:29` (`RECORD`), which reads `tests/opencode-verified.json`.
- Six repo-root lookups: `parents[1]` becomes `parents[2]` in `test_package`, `test_live_check`,
  `test_workspace_check`, `test_opencode_version`, inline in `test_council.py:171`, and in
  `verify_group.py:13`.
- Two imports: `tests.test_selector` and `tests.verify_native_loading`.
- Relative `../` specifiers in 13 Node tests (`new URL(...)`, `import(...)`, `from "..."`).
  Only these change. `resources: ["../.vibe-workflow/settings.json"]` in
  `onboarding.test.js` is a permission name, not a path, and `tests/a.test.js` strings in
  `session-facts.test.js` are data.
- `verify_cancel.mjs` holds two `parents[1]` inside Python stubs it writes into a temp
  folder. They do not refer to the repo and must not change; only its usage text does.
- Path mentions of moved files in `AGENTS.md`, `docs/development.md`,
  `docs/native-verification.md` and `docs/dependencies.md:38-40`.
- `docs/dependencies.md` lines 116 and 499 onward list an earlier plugin's files; they are
  historical and stay.

## Constraints

- `README.md` is edited only through the `impeccable` skill (repo rule).
- Tests are not shipped: `scripts/manifest.py`, `package.json` `files` and the export stay
  unchanged, and `validate.py` must still report 48 files.
- Failing check before each change; independent review before merge.
- Other open branches touch moved files (`fix/live-check-own-sessions` edits live-check tests,
  `fix/title-meter-branch-record` edited session tests). Git follows renames, but whichever
  merges second may need a small manual fix. Merge this PR when those are idle, or after them.

## Not verified

- Other test runners or editors that hard-code `tests/*.test.js` (none found in the repo).
- `verify_cancel.mjs` end to end: it needs a built export and a private evidence folder. The
  plan checks only its usage message; the `verify_*` Python scripts get an import smoke run.

## Left to the builder

The guard test's exact wording, commit grouping inside each task, and the order of edits
inside a task, within the rules above.
