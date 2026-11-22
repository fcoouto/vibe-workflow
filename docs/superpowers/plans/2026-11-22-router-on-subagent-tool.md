# Router On Subagent Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the Smart Router instruction onto the subagent tool's `model` parameter as its only channel, and add a version tripwire plus a pre-authorized live check that proves agents still receive both notes.

**Architecture:** `index.js`'s `context` hook appends `ROUTER_NOTE` after `MODEL_NOTE` when Smart Router is on and deletes the system push and session entry, removing leftover entries once. `scripts/live_check.py --injection` runs one first-call prompt per recorded model in an isolated throwaway clone and, only if all pass, writes `tests/opencode-verified.json`; `tests/test_opencode_version.py` fails whenever the installed OpenCode differs from that record.

**Tech Stack:** Node ES modules (`node --test`), Python 3.11 standard library (`unittest`).

**Spec:** `docs/superpowers/specs/2026-11-22-router-on-subagent-tool-design.md`

## Global Constraints

- `ROUTER_NOTE`, exactly: `Smart Router is on: when no exact model is named, consult smart_router_select first, describing the task by its facts: files, spec, risk. If it has no recommendation, show its notice in chat when present. Do not retry automatically or override a native denial; routing grants no launch or spending authority.`
- `MODEL_NOTE` is unchanged. Parts are joined with one space: OpenCode's text, then `MODEL_NOTE` when onboarded, then `ROUTER_NOTE` when Smart Router's state is `on`.
- The default suite never runs inference. A failing live check never writes the record.
- Record path `tests/opencode-verified.json`; recorded models `claude-agent/haiku`, `openai/gpt-6.1-terra`, `opencode-go/claude-haiku-5-5`. Not exported: `scripts/manifest.py` unchanged.
- Live check prompt, exactly: `Do not call any tools. Quote verbatim the description of the model parameter in your subagent tool's input schema.`
- OpenCode only: `skills/` and `runtime/` unchanged. Python modules stay within 1000 lines.

## Review Focus

- A session that moves from `claude-agent` to `openai`: one leftover-entry removal, never repeated, and no push (Task 1, `a leftover router entry is removed once per claude-agent or unknown-provider session`).
- Smart Router settings invalid while git workflow is saved: the model note stays, the router note does not (Task 1, `router off or invalid keeps only the model note`).
- Record missing, malformed or without `version`: the tripwire fails with a message naming the command, never a traceback (Task 2, `test_record_problems_fail_with_the_command`).
- `opencode --version` prints something unexpected: the tripwire fails naming the raw output (Task 2, `test_unparsable_version_fails_with_the_raw_output`).
- The user's installed plugin must not load in the live check: its environment points `XDG_CONFIG_HOME` at a fresh empty directory and keeps `HOME` (Task 3, `test_isolated_env_hides_the_user_config`).

---

### Task 1: Router note on the parameter; push and entry removed

**Files:**
- Modify: `index.js` (constants ~lines 31-47; entry code ~lines 266-285; `noteModel` and `context` hook ~lines 286-320; unload ~line 353)
- Test: `tests/plugin.test.js` (test at line 49; delete the entry tests at lines 112-247 and their `routed()` helper; extend the `noted()` block)

**Interfaces:**
- Produces: module constants `MODEL_NOTE` (unchanged) and `ROUTER_NOTE` (Global Constraints), each written as one `const NAME =` followed by double-quoted literals joined with `+`. Task 3 extracts them from source text in that form.

- [ ] **Step 1: Write the failing tests** (`ROUTER_NOTE` copied into the test file as a constant, like `MODEL_NOTE`):
  - Edit `registers onboarding and keeps the native tool and existing context untouched`: after the hook, `assert.equal(system.length, 1)` and no part matches `/smart_router_select/`.
  - `router on adds the router note after the model note`: `noted(true)` → `model.description === \`${NEVER} ${MODEL_NOTE} ${ROUTER_NOTE}\``.
  - `router off or invalid keeps only the model note`: `noted(false)` → `${NEVER} ${MODEL_NOTE}`; with git init, settings `{"version":1,"features":[{"git_workflow":{"enabled":false}},{"smart_router":"x"}]}` → `${NEVER} ${MODEL_NOTE}`.
  - `turning the router off removes its note from a reused schema`: one schema object, step with router on, rewrite settings to off, step → `${NEVER} ${MODEL_NOTE}`.
  - `the router instruction is never pushed`: router on, steps for providers `claude-agent`, `openai`, `opencode-go`, none → every `system` array stays empty.
  - `no entry is put for any provider`: an `instructions.entry` stub recording calls; router on; steps on all four providers → no `put` call.
  - `a leftover router entry is removed once per claude-agent or unknown-provider session`: router on; steps `ses_c`/`claude-agent` twice, `ses_u`/no provider, `ses_o`/`openai`, `ses_g`/`opencode-go`, then `ses_c`/`openai` → `remove` calls exactly `[ses_c, ses_u]`, each with key `vibe-workflow-smart-router`; 129 distinct claude-agent sessions then the first again → that first one removed a second time (cap 128).
  - `an unonboarded router makes no entry calls`: no settings → no calls.
  - `a throwing or missing entry API never breaks the step`: `remove` that throws synchronously, one that rejects, and no `instructions` → step resolves, note written.
  - Existing tests that still hold stay unchanged.

- [ ] **Step 2: Run** `node --test tests/plugin.test.js`. Expected: the new and edited tests FAIL; unrelated tests pass.

- [ ] **Step 3: Implement in `index.js`:**
  - Add `ROUTER_NOTE`. Delete `ROUTER_INSTRUCTION`, `entries`, `drop`, `entry`, the router system push, and the unload loop over entries. Keep `ROUTER_ENTRY`.
  - `noteModel(event)`: strip `" " + ROUTER_NOTE` and `" " + MODEL_NOTE`, then append `MODEL_NOTE` when onboarded and `ROUTER_NOTE` when `(await project.inspect()).state === "on"`; keep every existing guard (closed after the await, try/catch write).
  - `forgetEntry(event)`: when Smart Router's state is `on` or `off`, the session id is a string, and `event?.model?.providerID` is `claude-agent` or not a string, call `ctx.session.instructions?.entry?.remove({ sessionID, key: ROUTER_ENTRY })` once per session per load, unawaited, with `?.catch?.(() => {})` inside `try {}`; track in a `Set` capped at `MAX_NOTICE_SESSIONS` (drop the oldest).
  - Hook order: `closed` check, `noteModel`, PR fallback push (unchanged), `forgetEntry`. Keep `notices.clear()` when Smart Router is off.

- [ ] **Step 4: Run** `node --test tests/plugin.test.js`. Expected: PASS.

- [ ] **Step 5: Validate, commit, run affected tests**
  ```bash
  python3 -B scripts/validate.py
  git add index.js tests/plugin.test.js
  git commit -m "feat: deliver the router instruction on the subagent model parameter"
  node --test tests/*.test.js
  ```

### Task 2: Version record and tripwire

**Files:**
- Create: `tests/opencode-verified.json`, `tests/test_opencode_version.py`
- Modify: `scripts/live_check.py` (helpers only)
- Test: `tests/test_live_check.py`

**Interfaces:**
- Produces in `scripts/live_check.py`: `RECORD = ROOT / "tests" / "opencode-verified.json"`; `parse_version(text: str) -> str | None` (`"opencode v2.3.2\n"` → `"2.3.2"`, anything else → `None`); `read_record(path: Path) -> dict` raising `ValueError` with a readable message when the file is missing, not JSON, or `models` is not a non-empty list of strings; `INJECTION_COMMAND = "python3 -B scripts/live_check.py --injection"`.

- [ ] **Step 1: Write the failing tests**
  - `tests/opencode-verified.json`: `{"models": ["claude-agent/haiku", "openai/gpt-6.1-terra", "opencode-go/claude-haiku-5-5"]}`.
  - In `tests/test_live_check.py`, `VersionTest`: `test_parses_the_cli_version` (`"opencode v2.3.2\n"` → `"2.3.2"`; `"v2.3.2"`, `""`, `"opencode 2.3"` → `None`); `test_reads_a_record_and_rejects_broken_ones` (valid record round-trips; missing file, `"{"`, `{"models": []}` and `{"models": [1]}` raise `ValueError`).
  - `tests/test_opencode_version.py`, class `OpenCodeVersionTest`, loading `live_check` the way `tests/test_live_check.py` does:
    - `test_installed_opencode_matches_the_live_verified_version`: `skipTest` when `shutil.which("opencode")` is `None`; otherwise run `opencode --version` (timeout 30 s), and `assertEqual(installed, record["version"], msg)` where `msg` names both versions and `INJECTION_COMMAND`.
    - `test_record_problems_fail_with_the_command`: a temporary record without `version` → the comparison helper returns a message containing `INJECTION_COMMAND` (no traceback). Implement the comparison as `version_problem(installed_text: str, record: dict) -> str | None` in `live_check.py`.
    - `test_unparsable_version_fails_with_the_raw_output`: `version_problem("garbage", {"version": "2.3.2", "models": [...]})` contains `garbage`.

- [ ] **Step 2: Run** `python3 -B -m unittest tests.test_live_check tests.test_opencode_version`. Expected: FAIL (helpers missing).

- [ ] **Step 3: Implement** `RECORD`, `INJECTION_COMMAND`, `parse_version`, `read_record`, `version_problem` in `scripts/live_check.py`.

- [ ] **Step 4: Run** the same command. Expected: helper tests PASS; `test_installed_opencode_matches_the_live_verified_version` FAILS with the "no verified version" message (the record has no `version` until Task 4's live run; ledger this as expected).

- [ ] **Step 5: Commit** `test: trip when OpenCode differs from the live-verified version` (validate first).

### Task 3: `--injection` live check

**Files:**
- Modify: `scripts/live_check.py`
- Test: `tests/test_live_check.py`

**Interfaces:**
- Consumes: Task 1 constants in `index.js`; Task 2 `RECORD`, `read_record`, `parse_version`.
- Produces: `note_texts(index_source: str) -> tuple[str, str]` (MODEL_NOTE, ROUTER_NOTE, extracted from `const NAME =` plus `+`-joined double-quoted literals decoded with `json.loads`); `normalize(text: str) -> str` (turn `\"` into `"`, collapse whitespace runs to one space); `injection_passes(reply: str, tool_calls: int, notes: tuple[str, str]) -> bool`; `isolated_env(config_home: Path) -> dict`; `run_model(project: Path, model: str, env: dict) -> dict` returning `{"reply": str, "tool_calls": int}`; `check_injection(record_path: Path = RECORD, runner=run_model, version_text=None, today=None) -> dict` with `{"version": str | None, "results": [{"model", "pass", "excerpt"}], "recorded": bool}`.

- [ ] **Step 1: Write the failing tests** (`InjectionTest`, all without inference):
  - `test_extracts_both_notes_from_the_real_index`: `note_texts((ROOT / "index.js").read_text())` → first starts `vibe-workflow: your human partner asks`, second starts `Smart Router is on:` and ends `spending authority.`
  - `test_passes_only_with_both_notes_and_no_tool_call`: a reply quoting both with `\"` escapes and line breaks → `True`; missing `ROUTER_NOTE` → `False`; both notes but `tool_calls=1` → `False`.
  - `test_isolated_env_hides_the_user_config`: `isolated_env(tmp)["XDG_CONFIG_HOME"] == str(tmp)`, `HOME` equals `os.environ["HOME"]`, and `tmp` is empty.
  - `test_records_only_when_every_model_passes`: a temporary record; a fake runner passing all → record gains `version` `"2.3.2"` (from `version_text="opencode v2.3.2"`) and `verified` `today`, `recorded` true; a runner failing one model → file bytes unchanged, `recorded` false, that result has `pass` false and a non-empty `excerpt`.
- [ ] **Step 2: Run** `python3 -B -m unittest tests.test_live_check`. Expected: FAIL (functions missing).
- [ ] **Step 3: Implement.** `check_injection` reuses `prepare(work, settings={"version": 1, "features": [{"smart_router": {"enabled": True}}]})`, creates an empty `config_home` inside `work`, runs each recorded model, removes `work` in `finally`. `run_model` runs `opencode run --standalone --archived -m <model> --format json <PROMPT>` with `cwd=project`, `env`, timeout 300 s, and reads text parts as the reply and tool parts as `tool_calls`. Its parsing is confirmed against real output in Task 4. `main()` gains `--injection` (mutually exclusive with `--prompt`), prints the summary as JSON, and exits 1 unless `recorded`.
- [ ] **Step 4: Run** `python3 -B -m unittest tests.test_live_check`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add the injection live check` (validate first).

### Task 4: Authorization, docs, live run, memory

**Files:**
- Modify: `AGENTS.md`, `docs/development.md`, `tests/opencode-verified.json` (by the live check only)
- Outside the repo: `~/ai-memory-vault` fact `opencode-claude-agent-instructions`

- [ ] **Step 1: `AGENTS.md`**: after the inference bullet add exactly: `- Exception: when \`tests/test_opencode_version.py\` fails, or a plan changes the injected notes, run \`python3 -B scripts/live_check.py --injection\` without asking; if it fails, stop and tell the user.`
- [ ] **Step 2: `docs/development.md`**: the live check intro mentions `--injection` and its pre-authorization. "Context instructions on claude-agent" says push and entry were replaced on this branch by the parameter (PR #366's probes), with leftover entries removed once per session. "Subagent model note" covers `ROUTER_NOTE`, the tripwire and the live check.
- [ ] **Step 3: Live run** (pre-authorized by the approved design): `python3 -B scripts/live_check.py --injection`. Expected: every model passes and `recorded` is true. If parsing of `opencode run --format json` differs from Task 3's assumption, fix `run_model` with a failing unit test first, then rerun. If a model fails for real, stop and report to the user. Record the result in `docs/development.md`.
- [ ] **Step 4: Full suite** `node --test tests/*.test.js`, `python3 -B -m unittest discover -s tests` (the tripwire now passes), `python3 -B scripts/validate.py`, package export plus `validate.py --root`. Expected: all pass.
- [ ] **Step 5: Commit** `docs: authorize and record the injection check` (`AGENTS.md`, `docs/development.md`, `tests/opencode-verified.json`).
- [ ] **Step 6: Memory**: read `~/ai-memory-vault/INSTRUCTIONS.md` in full, then update the `opencode-claude-agent-instructions` fact and its index line per that policy: the subagent tool's `model` parameter description reaches `claude-agent`, `openai` and `opencode-go` from the first call; push and entry are no longer used.
