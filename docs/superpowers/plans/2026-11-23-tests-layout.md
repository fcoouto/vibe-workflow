# Tests layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Group `tests/` by what each test covers (`plugin/`, `skills/`, `runtime/`, `tooling/`) with every test still running and every reference fixed.

**Architecture:** `git mv` the 31 test files into four domain folders, fix only the paths, imports and commands the move breaks, and add a small guard test that fails loudly when the suite is started the old way. `runtime/` and `scripts/` layouts are untouched.

**Tech Stack:** Node `--test`, Python `unittest`, `git mv`.

**Spec:** `docs/superpowers/specs/2026-11-23-tests-layout-design.md`

## Global Constraints

- Test commands after this PR: `python3 -B -m unittest discover -s tests -t .` and `node --test "tests/**/*.test.js"`.
- Baseline on `4a16045`: Node 244 pass, Python 215 pass. After Task 1 the Python count is 217 (two guard tests).
- `tests/__init__.py` stays at the top of `tests/`; only it stays there.
- Tests are not shipped: `scripts/manifest.py`, `package.json` `files` and `validate.py`'s 48-file count stay unchanged.
- Do not change test content or behavior; change only the lines this plan names.
- `README.md` is edited only through the `impeccable` skill.
- `docs/dependencies.md` lines 116 and 499 onward are an earlier plugin's inventory: leave them.
- Use `git mv` for every move. No force, no inference, no `live_check.py` run.

## Review Focus

1. Suite started the old way (`-s tests` without `-t .`) must fail loudly with the right command: `test_layout.py` (Task 1).
2. A test run against the real home folder: `test_layout.py` asserts `HOME` is redirected (Task 1).
3. Silently fewer tests: exact counts checked after Tasks 2 and 3 (244 Node, 217 Python).
4. The two `parents[1]` inside Python stubs in `verify_cancel.mjs` are not repo-root lookups and must not change: diff check in Task 2.
5. `resources: ["../.vibe-workflow/settings.json"]` in `onboarding.test.js` and `tests/...` strings in `session-facts.test.js` are not file paths and must not change: diff check in Task 3.

---

### Task 1: Guard test and baseline

**Files:**
- Create: `tests/tooling/__init__.py` (empty), `tests/tooling/test_layout.py`

**Interfaces:**
- Produces: package `tests.tooling` (Task 2 moves files into it).

- [ ] **Step 1: Record the baseline.** Run both suites as they are today and write the two counts in the Task 1 commit message. Expected: Node `ℹ pass 244`, Python `Ran 215 tests ... OK`.

- [ ] **Step 2: Write the guard tests in `tests/tooling/test_layout.py`**, class `DiscoveryTest`:
  - `test_module_is_imported_through_the_tests_package`: `self.assertTrue(__name__.startswith("tests."), "run the suite with: python3 -B -m unittest discover -s tests -t .")`.
  - `test_home_is_not_the_real_home`: `os.path.realpath(os.environ["HOME"])` differs from `os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)`.

- [ ] **Step 3: Prove the guard can fail.** Run `python3 -B -m unittest discover -s tests`. Expected: `FAIL: test_module_is_imported_through_the_tests_package` (module name is `tooling.test_layout`), message names the new command.

- [ ] **Step 4: Prove it passes the right way.** Run `python3 -B -m unittest discover -s tests -t .`. Expected: `Ran 217 tests ... OK`.

- [ ] **Step 5: Commit** `test: guard the suite against being started without -t .`

### Task 2: Move the Python tests and tooling

**Files:**
- Move to `tests/runtime/` (new `__init__.py`): `test_chatgpt_limit.py`, `test_council.py`, `test_readers.py`, `test_routing.py`, `test_scores.py`, `test_selector.py`, `test_storage.py`, `native_reader.py`.
- Move to `tests/tooling/`: `test_live_check.py`, `test_opencode_version.py`, `test_package.py`, `test_verification.py`, `test_workspace_check.py`, `verify_group.py`, `verify_native_loading.py`, `verify_cancel.mjs`, `opencode-verified.json`.
- Modify: `scripts/live_check.py:29`, and the lines below.

**Interfaces:**
- Consumes: package `tests.tooling` from Task 1.
- Produces: all Python tests under `tests.runtime` and `tests.tooling`.

- [ ] **Step 1: `git mv` the files above** and create empty `tests/runtime/__init__.py`. Run `python3 -B -m unittest discover -s tests -t .`. Expected: failures from the references below, not an import error for the folders.

- [ ] **Step 2: Fix exactly these lines:**
  - `parents[1]` to `parents[2]`: `tests/tooling/test_package.py:13`, `test_live_check.py:15`, `test_workspace_check.py:12`, `test_opencode_version.py:14`, `tests/runtime/test_council.py:171`, `tests/tooling/verify_group.py:13`.
  - `tests/runtime/test_council.py:10`: `from tests.runtime import test_selector as sel`.
  - `tests/tooling/test_verification.py:5`: `from tests.tooling.verify_native_loading import inspect_result`.
  - `scripts/live_check.py:29`: `RECORD = ROOT / "tests" / "tooling" / "opencode-verified.json"`.
  - `tests/tooling/verify_cancel.mjs:10`: usage text names `tests/tooling/verify_cancel.mjs`. Lines 26 and 46 stay unchanged.

- [ ] **Step 3: Verify.** `python3 -B -m unittest discover -s tests -t .` expects `Ran 217 tests ... OK`. `node --test "tests/*.test.js"` still expects `ℹ pass 244` (Node tests have not moved yet). `git diff -U0 HEAD -- tests/tooling/verify_cancel.mjs` shows only line 10 changed.

- [ ] **Step 4: Verify history.** `git diff -M --stat HEAD` lists every move as a rename (similarity above 90%).

- [ ] **Step 5: Commit** `refactor: group the Python tests by runtime and tooling`

### Task 3: Move the Node tests

**Files:**
- Move to `tests/plugin/`: `council`, `onboarding`, `plugin`, `pr-advice`, `pr-hook`, `project`, `session-facts`, `session-titles`, `skill-advice` (`.test.js`).
- Move to `tests/skills/`: `council-skill`, `project-layout-skill`, `pull-request-skill`, `workspace-skill`, `worktree-cleanup` (`.test.js`).

**Interfaces:**
- Produces: Node tests under `tests/plugin/` and `tests/skills/`, found by `node --test "tests/**/*.test.js"`.

- [ ] **Step 1: `git mv` the files.** Run `node --test "tests/**/*.test.js"`. Expected: failures from the relative specifiers.

- [ ] **Step 2: Add one `../` level, only to file specifiers** in the 13 files that have them (all except `worktree-cleanup.test.js`): `new URL("../...")`, `` new URL(`../${name}`) `` (`onboarding.test.js:397`, `plugin.test.js:100`), `import("../...")` and `from "../..."`. A line-scoped edit that matches those three forms is enough.

- [ ] **Step 3: Verify.** `node --test "tests/**/*.test.js"` expects `ℹ pass 244` and `ℹ fail 0`. `git diff -U0 HEAD -- tests | grep -c 'settings.json'` expects `0`, and `git diff -U0 HEAD -- tests/plugin/session-facts.test.js | grep -c 'tests/'` expects `0`.

- [ ] **Step 4: Commit** `refactor: group the Node tests by plugin and skills`

### Task 4: Commands, docs and the recorded layout

**Files:**
- Modify: `AGENTS.md`, `README.md` (through `impeccable`), `docs/development.md`, `docs/native-verification.md`, `docs/dependencies.md`.

**Interfaces:**
- Consumes: the final paths from Tasks 2 and 3.

- [ ] **Step 1: Commands.** In `AGENTS.md:21-22`, `README.md:110-111` and `docs/development.md:13-14`, use the two commands from Global Constraints. For `README.md`, run the `impeccable` skill's setup and copy playbook first.

- [ ] **Step 2: Moved-file paths.** `AGENTS.md:13`, `docs/development.md:29, 69, 440` (`tests/tooling/test_opencode_version.py`, `tests/tooling/opencode-verified.json`), `docs/native-verification.md:63-65` and `docs/dependencies.md:38-40` (`tests/tooling/verify_*`, `tests/tooling/test_verification.py`).

- [ ] **Step 3: Recorded layout.** Replace the `AGENTS.md:32` bullet with one that says: `tests/` groups by what it covers; `plugin/` and `skills/` hold Node `<module>.test.js`, `runtime/` and `tooling/` hold Python `test_<module>.py` (`tooling/` also holds the dev-tool checks and `opencode-verified.json`); only `__init__.py` stays at the top; run with `-t .`.

- [ ] **Step 4: Verify everything on this head.**
  - `python3 -B -m unittest discover -s tests -t .` expects `Ran 217 tests ... OK`; `node --test "tests/**/*.test.js"` expects `ℹ pass 244`.
  - `python3 -B scripts/validate.py` expects `passed (48 files`; `python3 -B scripts/package.py --output dist` succeeds.
  - Smoke: `python3 -I -B tests/tooling/verify_group.py --help` and `.../verify_native_loading.py --help` print usage and exit 0; `node tests/tooling/verify_cancel.mjs` exits nonzero with the usage text naming the new path.
  - Stale paths: `grep -rnE 'tests/(test_|verify_|opencode-verified|[a-z-]+\.test\.js)' . --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=dist --exclude-dir=superpowers --exclude-dir=vibe-council` expects no hits outside `docs/dependencies.md` lines 116 and 499 onward.

- [ ] **Step 5: Commit** `docs: record the grouped tests layout and the new commands`

Then: independent review of the branch, push, `[BLOCKED]` draft, `[READY]`, merge, clean up (the project's settings allow agent merge and cleanup). Check first that `fix/live-check-own-sessions` is idle or merged.
