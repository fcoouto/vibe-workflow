# Subagent Model Note Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In onboarded projects, append one note to the OpenCode `subagent` tool's `model` parameter description so agents set subagent models, and stop the router instruction from deferring to the tool's never-set default.

**Architecture:** The existing `context` session hook in `index.js` edits `event.tools.subagent.input.properties.model.description` on every step, gated on the existing `onboarded()` check. `ROUTER_INSTRUCTION` loses its "permitted native controls" and "existing selection" clauses. No new channel, entry key, setting or onboarding change.

**Tech Stack:** JavaScript ES modules (`node --test`), Python 3.11 standard library (`unittest`).

**Spec:** `docs/superpowers/specs/2026-11-22-subagent-model-note-design.md`

## Global Constraints

- OpenCode only: changes live in `index.js`; `skills/` and `runtime/` code do not change.
- Onboarding question 1 and its choices stay exactly as they are; no new question or setting.
- Note text, exact (constant `MODEL_NOTE` in `index.js`), appended after one space:
  `vibe-workflow: your human partner asks you to set the subagent tool's \`model\` and variant in this project; this is the explicit request that tool requires. Use the first that applies: a model they or your active skill name exactly; Smart Router's recommendation, when on; the tier your skill or plan calls for, on this session's provider. Otherwise leave it unset.`
- Gate: `onboarded()` (`index.js:235`): any of Smart Router, git workflow, session titles is `on` or `off`.
- Missing path, non-string description, or a host that refuses the write: no change, never throws.
- `ROUTER_INSTRUCTION` keeps "Do not retry automatically or override a native denial." and "Routing grants no launch or spending authority."
- Python modules stay within 1000 lines. Validate, commit, then run the affected tests (AGENTS.md).

## Review Focus

- Host reuses the same schema object across steps: the note must appear once, not once per step (Task 1, `appends the note once when the host reuses the schema`).
- Project opted out after the note was written into a reused object: the note must go away (Task 1, `removes the note when the project is no longer onboarded`).
- Frozen or non-writable `model` schema: no throw, context hook still pushes the router instruction (Task 1, `a frozen model schema is left alone and the hook still runs`).
- Host without `tools`, with `tools` as a Map, or without `subagent`: untouched, no throw (Task 1, `missing or odd tool shapes are a no-op`).
- Plugin unloaded (`closed`): no edit after cleanup (Task 1, `no note after unload`).

---

### Task 1: Model note on the subagent `model` parameter

**Files:**
- Modify: `index.js` (constants near `ROUTER_INSTRUCTION` ~line 32; `context` hook ~lines 278-298)
- Test: `tests/plugin.test.js`

**Interfaces:**
- Consumes: `onboarded(): Promise<boolean>` (`index.js:235`), `closed` flag.
- Produces: exported nothing new; module constant `MODEL_NOTE: string`; inner `async function noteModel(event): Promise<void>`.

- [ ] **Step 1: Write the failing tests** in `tests/plugin.test.js`, using a helper `schema()` returning
  `{ subagent: { description: "Spawns…", input: { type: "object", properties: { model: { type: "string", description: "NEVER set this unless the user explicitly asks for a particular model or variant." } } } } }`
  and the existing `preference()` / `host()` helpers:
  - `onboarded projects get the model note once on the subagent model parameter`: with `preference(root, false)` (router off), one `context` call on `{ system: [], tools: schema(), sessionID: "ses_a" }` makes `model.description` equal the original + `" "` + the exact `MODEL_NOTE` text from Global Constraints; `subagent.description` unchanged. Repeat with router `true`: same result.
  - `onboarded by git workflow alone still gets the note`: run `git init -q` in the temp root first (at-project settings need a resolvable git project, `project.js:167`), write settings `{ version: 1, features: [{ git_workflow: { enabled: false } }] }` → note present.
  - `unonboarded projects keep the model description untouched`: no settings file → description unchanged.
  - `appends the note once when the host reuses the schema`: same `tools` object through three `context` calls → note occurs exactly once.
  - `removes the note when the project is no longer onboarded`: note present, then delete the settings file, next `context` call on the same object → description equals the original.
  - `a frozen model schema is left alone and the hook still runs`: `Object.freeze(model)`, router on → no throw, description unchanged, `system` still gets the router instruction.
  - `missing or odd tool shapes are a no-op`: `tools` undefined, a `Map`, `{}`, `{ subagent: { input: null } }`, and `description: 7` → no throw, nothing changes.
  - `no note after unload`: call the cleanup returned by `setup`, then `context` → description unchanged.

- [ ] **Step 2: Run to verify they fail**

  Run: `node --test tests/plugin.test.js`
  Expected: the new tests FAIL (description unchanged); existing tests pass.

- [ ] **Step 3: Implement `noteModel(event)` in `index.js`** and call it at the top of the `context` hook, after the `closed` check and before the git-workflow push and the router early return. Read `event?.tools?.subagent?.input?.properties?.model`; return unless its `description` is a string. Strip any existing `" " + MODEL_NOTE` first, then append it when `!closed && await onboarded()`. Wrap the write in `try {} catch {}` so frozen schemas never throw.

- [ ] **Step 4: Run to verify they pass**

  Run: `node --test tests/plugin.test.js`
  Expected: PASS, all tests.

- [ ] **Step 5: Validate, commit, run affected tests**

  ```bash
  python3 -B scripts/validate.py
  git add index.js tests/plugin.test.js
  git commit -m "feat: ask agents to set subagent models in onboarded projects"
  node --test tests/*.test.js
  ```

### Task 2: Router instruction stops deferring to the never-set default

**Files:**
- Modify: `index.js:32-38` (`ROUTER_INSTRUCTION`)
- Test: `tests/plugin.test.js` (test at line 48), `tests/test_selector.py` (`test_selects_cheapest_qualifying_and_two_alternatives`)

**Interfaces:**
- Consumes: `MODEL_NOTE` wording from Task 1 (the instruction refers to it as "the vibe-workflow subagent model note").
- Produces: new `ROUTER_INSTRUCTION` text, exactly:
  `Before delegating work, consult smart_router_select for an advisory provider/model/effort recommendation, describing the task by its facts: files, spec, risk. Use its recommendation unless the user named a model. If no recommendation is available, show its notice in chat when present, then follow the vibe-workflow subagent model note. Do not retry automatically or override a native denial. Routing grants no launch or spending authority. The caller retains task ownership, native execution, reviews, permissions, continuation, and results.`

- [ ] **Step 1: Write the failing assertions**
  - In the test at `tests/plugin.test.js:48`, add: `assert.doesNotMatch(system[1].text, /permitted native controls|existing selection/)`, `assert.match(system[1].text, /unless the user named a model/)`, `assert.match(system[1].text, /subagent model note/)`, `assert.match(system[1].text, /no launch or spending authority/)`, `assert.match(system[1].text, /override a native denial/)`.
  - In `test_selects_cheapest_qualifying_and_two_alternatives`, add `self.assertEqual(result["route"]["provider"], "openai")` (pins the native provider beside the existing `model` assertion, so the pick is dispatchable as `provider/model`).

- [ ] **Step 2: Run to verify**

  Run: `node --test tests/plugin.test.js` → the line-48 test FAILS.
  Run: `python3 -B -m unittest tests.test_selector` → PASS (a pin on current behavior, not new behavior).

- [ ] **Step 3: Replace `ROUTER_INSTRUCTION`** with the exact text above.

- [ ] **Step 4: Run to verify it passes**

  Run: `node --test tests/plugin.test.js`
  Expected: PASS.

- [ ] **Step 5: Validate, commit, run affected tests**

  ```bash
  python3 -B scripts/validate.py
  git add index.js tests/plugin.test.js tests/test_selector.py
  git commit -m "fix: let the router instruction set subagent models"
  node --test tests/*.test.js && python3 -B -m unittest discover -s tests
  ```

### Task 3: Record, package and observe

**Files:**
- Modify: `docs/development.md` (new section `## Subagent model note: what is known`, after "Context instructions on claude-agent"), `README.md` (one sentence, through the `impeccable` skill)

- [ ] **Step 1: Write the `docs/development.md` section**: point to the spec's "Evidence (2026-11-22)" and "Known gaps" sections instead of copying them; add one line each for the bridge's 2048-character cut of the tool description (why the note sits on the parameter) and for OpenCode 2.3.2 as the only verified version.

- [ ] **Step 2: README**: load the `impeccable` skill and add one sentence under "Onboard a project" saying that in an onboarded project, agents set each subagent's model (your named model first, then Smart Router's pick when on, then the workflow's own tier). Keep "Pause or remove" accurate.

- [ ] **Step 3: Package and validate**

  ```bash
  python3 -B scripts/package.py --output dist-model-note
  python3 -B scripts/validate.py --root dist-model-note
  ```
  Expected: both succeed. Then `rm -rf dist-model-note`.

- [ ] **Step 4: Live dispatch check (runs inference: ask the user first)**

  Run: `python3 -B scripts/live_check.py --settings '{"version":1,"features":[{"smart_router":{"enabled":false}}]}' --prompt "Use a subagent to count the lines in README.md, then report the number."`
  Expected: the `subagent` tool call's `input` has a `model` set (a session-provider model). Record the model, provider and outcome in the new `docs/development.md` section; if no model is set, record that as observed and report it.

- [ ] **Step 5: Commit**

  ```bash
  git add docs/development.md README.md
  git commit -m "docs: record the subagent model note"
  ```
