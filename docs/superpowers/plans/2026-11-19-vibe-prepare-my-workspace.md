# vibe-prepare-my-workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a `vibe-prepare-my-workspace` skill that checks the OpenCode partner toolset with a read-only script and, after approval, installs or updates what is missing or outdated.

**Architecture:** A stdlib-only `check.py` inside the skill folder reads `partners.json`, detects local state, compares it with upstream through injected probes, and prints a JSON table. `SKILL.md` and `references/procedure.md` hold the approve, back up, install, verify flow. The plugin registers the skill like `vibe-onboard-project`.

**Tech Stack:** Python 3.11+ standard library, Node ES modules, `unittest` and `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-19-vibe-prepare-my-workspace-design.md`

## Global Constraints

- Python 3.11+ standard library only; `scripts/validate.py` rejects other imports, `/Users/<name>/` paths in any exported `.py`, `.js`, `.json` or `.md`, and files over 1 MiB or modules over 1000 lines.
- `check.py` is read-only: it never installs, edits or writes a file.
- Network: HTTPS GET to the public metadata hosts named in `partners.json` only (`registry.npmjs.org`, `api.github.com`, `raw.githubusercontent.com`), short timeouts, no credentials, tokens or local paths sent.
- States are exactly `ready`, `missing`, `outdated`, `unknown`, `not-supported`. `unknown` always carries a reason and is never `ready`.
- A tool with an OpenCode integration is `ready` only when the machine-level piece exists and OpenCode is wired to use it.
- git, gh and python are checked for presence and a minimum version only; python minimum is 3.11.
- Read global `~/.config/opencode/opencode.json` or `.jsonc` only, both `plugins` and legacy `plugin` keys. Installs go to user scope.
- Config backups go to `${XDG_STATE_HOME:-~/.local/state}/vibe-workflow/workspace-backups/<timestamp>/`, directory mode `0700`.
- Exit codes: 0 all ready, 1 any `missing` or `outdated` (wins over 2), 2 otherwise when any `unknown`.
- Nothing installs without approval of the specific list. No real install runs during development.
- README edits go through the `impeccable` skill and stay short.

## Review Focus

- Config with `//` inside a URL string, `//` and `/* */` comments, and trailing commas parses; an unparseable config is `unknown`, never `missing`. Pinned in Task 2.
- rtk binary present but `plugins/rtk.ts` absent is `missing`, not `ready`. Pinned in Task 4.
- Upstream lookup that times out, returns non-JSON, rate-limits or answers a non-allowlisted host is `unknown` with a reason, never `ready`. Pinned in Task 3.
- Version strings with prefixes or suffixes (`v1.2.3`, `rtk 1.1.0`, `git version 2.39.5 (Apple Git-154)`, `1.0.0-rc1`) parse to the numeric prefix; no digits is `unknown`. Pinned in Task 1.
- A symlinked skill folder counts as present; a dangling symlink is `missing`. A partner with no comparable local version (for example the caveman plugin's own `0.1.0`) is `unknown: no_comparable_version`, not a guess. Pinned in Task 2.

---

### Task 1: Versions, states and exit code

**Files:**
- Create: `skills/vibe-prepare-my-workspace/scripts/check.py`
- Test: `tests/test_workspace_check.py`

**Interfaces:**
- Produces: `STATES: tuple[str, ...]` (the five states); `parse_version(text: str) -> tuple[int, ...]` (first run of dot-separated integers; `ValueError` when none); `compare(installed: str, latest: str) -> int` (-1, 0 or 1 by padded integer tuples); `worst(states: list[str]) -> str` (precedence `missing`, `outdated`, `unknown`, `not-supported`, `ready`); `exit_code(states: list[str]) -> int`.
- The test module loads `check.py` with `importlib.util.spec_from_file_location`; it is not on `sys.path`.

- [ ] **Step 1: Write failing tests** `test_parse_version` (`"v1.2.3"`, `"rtk 1.1.0"`, `"git version 2.39.5 (Apple Git-154)"`, `"1.0.0-rc1"` give `(1,2,3)`, `(1,1,0)`, `(2,39,5)`, `(1,0,0)`; `"none"` raises `ValueError`), `test_compare` (`"1.2"` equals `"1.2.0"`, `"1.10.0"` above `"1.9.9"`), `test_exit_code` (`["ready"]` is 0, `["ready","unknown"]` is 2, `["outdated","unknown"]` is 1, `["missing"]` is 1, `["not-supported","ready"]` is 0).
- [ ] **Step 2: Run** `python3 -B -m unittest tests.test_workspace_check -v`. Expect failure: file missing.
- [ ] **Step 3: Implement** the interfaces above in `check.py`.
- [ ] **Step 4: Run** the same command. Expect pass.
- [ ] **Step 5: Commit** `feat: add workspace check versions and states`.

### Task 2: Local detection for OpenCode

**Files:**
- Modify: `skills/vibe-prepare-my-workspace/scripts/check.py`
- Test: `tests/test_workspace_check.py`

**Interfaces:**
- Consumes: `parse_version` from Task 1.
- Produces: `read_config(path: Path) -> dict` (strips `//` and `/* */` comments outside strings and trailing commas, then `json.loads`; `ValueError` on failure or a missing file); `integration_present(spec: dict, config: dict, home: Path) -> tuple[bool, str | None]` returning presence and a local version or `None`.
- `spec` keys by `kind`: `plugin` (`match`: substring of any `plugins` or `plugin` entry, entries may be strings or `[string, options]`), `mcp` (`name`: key under `mcp.servers` or `mcp`), `skill` (`name`: folder or symlink under `<home>/.config/opencode/skills`, must resolve), `file` (`path` relative to `<home>/.config/opencode`, must resolve to a file). `version_from` optionally names a `package.json` glob relative to `<home>/.cache/opencode/npm`; its `version` is the local version.

- [ ] **Step 1: Write failing tests** with a temp `home`: `test_read_config_keeps_urls_and_drops_comments` (a `"url": "https://x.test/a"` string with comments and a trailing comma parses and keeps the URL), `test_read_config_rejects_garbage` (`ValueError`), `test_plugin_matches_both_keys` (a match under `plugin` and one under `plugins` both found; `[name, options]` entries too), `test_mcp_and_file_and_skill` (present and absent cases), `test_symlinked_skill_is_present_and_dangling_is_missing`, `test_version_from_reads_cache_package_json`, `test_no_version_source_returns_none`.
- [ ] **Step 2: Run** `python3 -B -m unittest tests.test_workspace_check -v`. Expect the new tests to fail.
- [ ] **Step 3: Implement** both functions; the comment stripper is a small character scanner that tracks string state.
- [ ] **Step 4: Run** the same command. Expect pass.
- [ ] **Step 5: Commit** `feat: detect OpenCode integrations in workspace check`.

### Task 3: Upstream lookups

**Files:**
- Modify: `skills/vibe-prepare-my-workspace/scripts/check.py`
- Test: `tests/test_workspace_check.py`

**Interfaces:**
- Consumes: `parse_version`.
- Produces: `ALLOWED_HOSTS = ("registry.npmjs.org", "api.github.com", "raw.githubusercontent.com")`; `latest_version(spec: dict, fetch: Callable[[str], str]) -> str` where `spec["type"]` is `npm` (`package`; URL `https://registry.npmjs.org/<package with / as %2F>/latest`, JSON `version`), `github` (`repo`; `https://api.github.com/repos/<repo>/releases/latest`, JSON `tag_name`), or `raw` (`url` on an allowed host; the first version found in the body). Any other type, a host outside `ALLOWED_HOSTS`, or a `fetch` failure raises `LookupError(reason)`; `reason` is one of `lookup_failed`, `bad_response`, `host_not_allowed`, `unsupported_source`.
- Produces: `http_fetch(url: str) -> str` using `urllib.request` with a 5-second timeout and a fixed `User-Agent`; any `OSError`, `TimeoutError` or HTTP error is re-raised as `OSError`.

- [ ] **Step 1: Write failing tests** with a fake `fetch`: `test_npm_url_and_version`, `test_github_tag_name`, `test_raw_first_version`, `test_unlisted_host_is_refused_before_fetch` (fetch never called), `test_fetch_error_is_lookup_failed`, `test_non_json_is_bad_response`.
- [ ] **Step 2: Run** `python3 -B -m unittest tests.test_workspace_check -v`. Expect the new tests to fail.
- [ ] **Step 3: Implement** both; check the host from the parsed URL before any call.
- [ ] **Step 4: Run** the same command. Expect pass.
- [ ] **Step 5: Commit** `feat: add upstream lookups to workspace check`.

### Task 4: Partner list, judgment and command line

**Files:**
- Create: `skills/vibe-prepare-my-workspace/partners.json`
- Modify: `skills/vibe-prepare-my-workspace/scripts/check.py`
- Test: `tests/test_workspace_check.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: `partners.json` `{"version": 1, "partners": [...]}`. Each partner has `id`, `why`, `docs` (official install or update URL), optional `machine` (`command`, `version_args`, `minimum`), optional `clients.opencode` (the Task 2 `spec`), and `upstream` (a Task 3 `spec`, or `{"type": "minimum", "version": "<x>"}` or `{"type": "hosted"}`). Entries: the table in the spec; the implementer verifies each upstream and docs URL from the partner's own pages and records unverifiable ones as `{"type": "none"}` so the row reports `unknown: no_upstream_source`.
- Produces: `judge(partner: dict, config: dict | None, env: Env) -> dict` returning `{"id", "state", "installed", "upstream", "why", "reason"}`; `Env` is a dataclass with `home: Path`, `run: Callable[[list[str]], str]` (raises `FileNotFoundError` when the command is absent, other `OSError` or `TimeoutError` when it cannot run), `fetch: Callable[[str], str]`. `config is None` means the config was unreadable: integration cells become `unknown: config_unreadable`.
- Produces: `main(argv: list[str] | None = None, env: Env | None = None) -> int` with `--partners PATH` (default: `partners.json` beside the script) and `--json`; prints the JSON table (`{"client": "opencode", "rows": [...]}`) or a plain table, and returns `exit_code`.

- [ ] **Step 1: Write failing tests** `test_rtk_binary_without_hook_is_missing`, `test_rtk_with_hook_and_current_version_is_ready`, `test_behind_upstream_is_outdated`, `test_lookup_failure_is_unknown_with_reason`, `test_git_below_minimum_is_outdated_and_never_compared_upstream` (Apple git string, fetch never called), `test_hosted_context7_is_ready_when_present`, `test_unreadable_config_is_unknown_not_missing`, `test_main_prints_rows_and_exit_code`, `test_check_writes_nothing` (snapshot every path and mtime under a temp home before and after `main`), and `test_shipped_partners_json_is_valid` (loads the real file: every partner has `id`, `why`, `docs`; ids match the spec table; every `github`/`npm`/`raw` host is allowed).
- [ ] **Step 2: Run** `python3 -B -m unittest tests.test_workspace_check -v`. Expect the new tests to fail.
- [ ] **Step 3: Implement** `partners.json`, `judge`, `Env`, `main`; wire the real `Env` (`subprocess.run` with a 5-second timeout, `http_fetch`, `Path.home()`) only in the `__main__` path.
- [ ] **Step 4: Run** the same command and `python3 -B skills/vibe-prepare-my-workspace/scripts/check.py --json` once against this machine as a live observation, reported separately from the unit result. Expect the unit tests to pass; the live result is reported as seen.
- [ ] **Step 5: Commit** `feat: add partner list and judgment to workspace check`.

### Task 5: Skill, registration and packaging

**Files:**
- Create: `skills/vibe-prepare-my-workspace/SKILL.md`, `skills/vibe-prepare-my-workspace/references/procedure.md`
- Modify: `onboarding.js`, `index.js`, `scripts/manifest.py`, `package.json`, `tests/test_package.py`, `tests/onboarding.test.js`, `tests/plugin.test.js`, `docs/dependencies.md`, `README.md`
- Test: `tests/workspace-skill.test.js`

**Interfaces:**
- Produces: `registerSkill(ctx: object, name: string) -> Promise<void>` exported from `onboarding.js`: reads `skills/<name>/SKILL.md`, parses its `name` and `description` frontmatter, and calls `ctx.skill.transform` to add `{ id, name, path, description, content }`; `registerOnboarding` calls it for `vibe-onboard-project`. `index.js` calls `await registerSkill(ctx, "vibe-prepare-my-workspace")` after `registerOnboarding`.
- `SKILL.md` frontmatter: `name: vibe-prepare-my-workspace`, one-line `description` (check, prepare or repair the partner tools in OpenCode; not project onboarding), and a short body that loads `references/procedure.md` before step 0.
- `procedure.md` follows the spec's skill flow, steps 0–7 with each step's done condition and failure handling, runs `check.py` from the skill's own base directory, and states the approval, backup and user-action rules verbatim from the spec.

- [ ] **Step 1: Write failing tests.** `tests/workspace-skill.test.js`: the registered skills are `vibe-onboard-project` then `vibe-prepare-my-workspace`, the new skill's `path` and `content` exist, `SKILL.md` references `references/procedure.md`, and `procedure.md` contains `python3 --version`, `check.py`, `approve` and the backup directory path. Update `tests/onboarding.test.js` (skills list has both), `tests/plugin.test.js` (copy the new `SKILL.md` into the transport fixture), and `tests/test_package.py` (expected files add the four new paths: `SKILL.md`, `references/procedure.md`, `scripts/check.py`, `partners.json`; the Node assertion lists both skills).
- [ ] **Step 2: Run** `node --test tests/*.test.js` and `python3 -B -m unittest discover -s tests`. Expect failures for the missing skill and manifest entries.
- [ ] **Step 3: Implement** the skill files, `registerSkill`, the `index.js` call, the four `scripts/manifest.py` entries, and `package.json` `files` gaining `skills/vibe-prepare-my-workspace`. Add one paragraph to `docs/dependencies.md` recording the source (`prepare-my-workspace` from the earlier plugin at `697e1797`) and that its hooks, upgrade records, run directories and `workflow.py` were not carried over. Update `README.md` through the `impeccable` skill: one short "Prepare your workspace" section that names the skill and says it asks before installing.
- [ ] **Step 4: Run** `python3 -B scripts/validate.py`, `python3 -B -m unittest discover -s tests`, `node --test tests/*.test.js`, then `python3 -B scripts/package.py --output /private/tmp/vw-<random>` and `python3 -B scripts/validate.py --root` on it. Expect all to pass.
- [ ] **Step 5: Commit** `feat: add the vibe-prepare-my-workspace skill`.

## After the tasks

Get an independent review of the whole branch (AGENTS.md requires it for nontrivial work), then open a PR. Report unit proof, package checks and the one live `check.py` observation separately; no install, activation or real preparation run happens in this change.
