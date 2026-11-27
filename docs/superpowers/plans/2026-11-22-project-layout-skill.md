# Project Layout Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `vibe-project-layout` skill and one added sentence on the existing `writing-plans` note, so agents place files by the project's layout.

**Architecture:** A prompt-only skill (`SKILL.md` plus two references) registered with the existing `registerSkill`, listed in the export inventory. The note is one added sentence in `SKILL_NOTES["writing-plans"]` in `pr-advice.js`, behind its existing `onboarded` gate. No new runtime code, setting, tool or hook.

**Tech Stack:** Markdown skill files, JavaScript ES modules (`node --test`), Python 3.11 `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-22-project-layout-skill-design.md` (council record: `docs/vibe-workflow/council-sessions/2026-11-21_file-layout-skill/result.md`)

## Global Constraints

- Smallest sufficient change; no new runtime code, no dependencies.
- No file moves in the skill PR. This repo's own restructure is a later, phased PR series.
- No onboarding question, new setting, tool or runtime code. The `writing-plans` note uses the same `onboarded` gate; no new setting.
- No reference to `writing-plans` inside the skill text. The plugin note is the only link between them.
- No restating of `safe-refactor`. The skill hands approved moves to it.
- Package inventory: add the three skill files to `scripts/manifest.py` `FILES` and the skill folder to `package.json` `files`.
- `README.md` is edited only through the `impeccable` skill (repo rule).
- Failing test before behavior changes; independent review before acceptance.
- Work on a task branch in this worktree (`HEAD` is detached at `b5d5faa`).

## Review Focus

- Not-onboarded project or a failing `onboarded()` read: the `writing-plans` note stays absent, with the new sentence too (`tests/pr-advice.test.js` already covers this; keep it green).
- The export drops the new skill: `package.json` `files` must list it, as the 0.7.1 council fix showed (`tests/test_package.py` compares `FILES` with it).
- Machine paths in the skill text fail `scripts/validate.py` (`machine_path_in_export`): no `/Users/` or `/home/` in examples (pinned in Task 2).
- The skill pushes one layout: the procedure must state precedent-first placement and "has its own layout" (pinned in Task 2).
- The registered-skills list is asserted in five places; the new skill is appended last everywhere (Task 2).

---

### Task 1: Task branch and design record

**Files:**
- Create (already written): `docs/superpowers/specs/2026-11-22-project-layout-skill-design.md`, `docs/superpowers/plans/2026-11-22-project-layout-skill.md`, `docs/vibe-workflow/council-sessions/2026-11-21_file-layout-skill/` (`brief.md`, `answers/*.md`, `result.md`)

- [ ] **Step 1: Create the branch**

Run: `git switch -c feat/project-layout-skill`
Expected: `Switched to a new branch 'feat/project-layout-skill'`

- [ ] **Step 2: Commit the records**

```bash
git add docs/superpowers docs/vibe-council
git commit -m "docs: design, plan and council record for the project layout skill"
```

---

### Task 2: The `vibe-project-layout` skill

**Files:**
- Create: `skills/vibe-project-layout/SKILL.md`, `skills/vibe-project-layout/references/procedure.md`, `skills/vibe-project-layout/references/layouts.md`
- Create: `tests/project-layout-skill.test.js`
- Modify: `index.js` (register), `scripts/manifest.py` (`FILES`), `package.json` (`files`)
- Modify: `tests/onboarding.test.js:54`, `tests/pull-request-skill.test.js:22`, `tests/workspace-skill.test.js:19-24`, `tests/test_package.py` (the `expected` set and the node snippet's skills list)

**Interfaces:**
- Produces: skill id `vibe-project-layout`, registered by `registerSkill(ctx, "vibe-project-layout")`, last in the list after `vibe-council`.

- [ ] **Step 1: Write the failing tests**

Create `tests/project-layout-skill.test.js` in the style of `tests/council-skill.test.js` (`root = new URL("../skills/vibe-project-layout/", import.meta.url)`, `read(name)`, a `hasAll(text, phrases)` helper that asserts `includes` with a `missing: <phrase>` message). Tests:

- `skill frontmatter matches the registration pattern and links both references`: `SKILL.md` matches `/^---\nname: vibe-project-layout\ndescription: [^\n]+\n---\n[\s\S]+/` and includes `references/procedure.md` and `references/layouts.md`.
- `procedure carries the placement, restructure and recording rules`: `hasAll(procedure, ["Placement order", "recorded layout", "nearest comparable", "has its own layout", "git mv", "safe-refactor", "source-to-destination map", "approved", "AGENTS.md", "about ten lines", "never a file listing", "dead files"])`.
- `no skill file names writing-plans or carries a machine path`: for each of the three files, `!text.includes("writing-plans")` and `!/\/Users\/|\/home\//.test(text)`.
- `layouts has at most four example trees and the convention-owned folders`: with `trees = (text.match(/^```/gm) || []).length / 2`, assert `1 <= trees <= 4`, `(text.match(/example, not rule/g) || []).length >= trees`, and `hasAll(text, ["app/", "tests/", "skills/<id>/SKILL.md"])`.

Update the four existing list assertions to append the new id after `"vibe-council"`: `tests/onboarding.test.js:54` (`[...skills.keys()]`), `tests/pull-request-skill.test.js:22`, `tests/workspace-skill.test.js:19-24` (append `["vibe-project-layout", "vibe-project-layout"]`), and in `tests/test_package.py` both the `expected` set (add the three `skills/vibe-project-layout/...` paths) and the node snippet's skills list (append `['vibe-project-layout', 'vibe-project-layout']`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `node --test tests/project-layout-skill.test.js tests/onboarding.test.js tests/pull-request-skill.test.js tests/workspace-skill.test.js`
Expected: FAIL (`ENOENT` for the skill files; list mismatches).
Run: `python3 -B -m unittest discover -s tests -p "test_package.py"`
Expected: FAIL (inventory mismatch).

- [ ] **Step 3: Write the skill files**

`SKILL.md`: the exact frontmatter below, then a body of about five lines in the style of `skills/vibe-pull-requests/SKILL.md`: read `references/procedure.md` from the skill's bundled directory before creating, moving or renaming a file or folder and before any restructure; `references/layouts.md` holds example trees to consult when nothing else decides.

```
---
name: vibe-project-layout
description: Use when creating, moving or renaming files or folders, deciding where code, tests, scripts or docs go, or when asked to organize or restructure a project's layout.
---
```

`references/procedure.md` writes the spec's Design section as rules, with each phrase from the Step 1 contract appearing verbatim: **Placement order** (framework-required path; the recorded layout; the nearest comparable file or folder; the examples, with domain grouping only as the tiebreak; tests and docs follow the project's own convention, never forced mirroring); **Hints** (signs a folder may be wanted prompt one proposal at the end of the task, never a move; a one-file folder is fine when the ecosystem requires it; a project that has its own layout is respected, never pushed toward the examples); **Restructure on request** (analyze, measure references: imports, manifests, package file lists, test fixtures, docs; write the source-to-destination map with break points; the user approves; moves go through `safe-refactor` using `git mv`; run the project's tests; summarize; stop and ask on a surprise; report suspected dead files, never delete them); **Recording the layout** (with approval, about ten lines of placement rules in the project's existing agent instructions file such as `AGENTS.md`, rules and exceptions only, never a file listing, updated in the same PR as any move).

`references/layouts.md`: a one-line intro saying every tree is an example, not rule; then four short fenced trees: Node/TS app, Python package, mixed JS and Python plugin (the `src/`, `runtime/`, `scripts/`, `tests/`, `docs/` target of this repo, with `index.js` at the root), monorepo. Each tree is followed by one placement decision for a test, a doc and a shipped asset. A closing list names convention-owned folders to leave alone: Next.js `app/`, Python `tests/`, `skills/<id>/SKILL.md`. Use only relative paths.

- [ ] **Step 4: Register and list the skill**

In `index.js`, add `await registerSkill(ctx, "vibe-project-layout")` directly after the `await registerCouncil(...)` statement and before `await ctx.session.hook("context", ...)`. In `scripts/manifest.py` add the three `skills/vibe-project-layout/...` paths to `FILES` after the council entries. In `package.json` add `"skills/vibe-project-layout"` to `files` after `"skills/vibe-council"`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `node --test tests/*.test.js`
Expected: PASS
Run: `python3 -B -m unittest discover -s tests`
Expected: `OK`
Run: `python3 -B scripts/validate.py`
Expected: `validation: passed (...)`

- [ ] **Step 6: Commit**

```bash
git add skills/vibe-project-layout index.js scripts/manifest.py package.json tests
git commit -m "feat: add the vibe-project-layout skill"
```

---

### Task 3: The `writing-plans` note

**Files:**
- Modify: `pr-advice.js:182-185` (`SKILL_NOTES["writing-plans"].note`)
- Test: `tests/pr-advice.test.js:207-209` (the `PLAN` constant), `tests/pr-hook.test.js:219-231`

**Interfaces:**
- Consumes: the `onboarded` gate and `OVERRIDE` in `pr-advice.js`; unchanged.
- Produces: the `writing-plans` note text below, still `null` outside onboarded projects.

- [ ] **Step 1: Write the failing tests**

In `tests/pr-advice.test.js` replace the `PLAN` constant with the existing text plus the new sentence between `before implementing. ` and `An explicit request`:

```
"Before you fill in each task's Files block, follow the vibe-project-layout skill: put every new file and test where the project's recorded layout says. If a file belongs elsewhere or a folder needs splitting, propose it as its own task for your human partner to approve, never as a silent move. "
```

In `tests/pr-hook.test.js` (`a writing-plans load gets the method pre-approval...`) add `assert.match((await load(router.hooks)).content[1].text, /follow the vibe-project-layout skill/)` after the existing `match`.

- [ ] **Step 2: Run to verify they fail**

Run: `node --test tests/pr-advice.test.js tests/pr-hook.test.js`
Expected: FAIL (note text mismatch).

- [ ] **Step 3: Add the sentence in `pr-advice.js`**

In `SKILL_NOTES["writing-plans"].note`, add the sentence from Step 1 (ending with a space) as a new string segment between the existing text (which ends `before implementing. `) and `OVERRIDE`. Leave the existing text, gate and `OVERRIDE` unchanged.

- [ ] **Step 4: Run to verify they pass**

Run: `node --test tests/*.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pr-advice.js tests/pr-advice.test.js tests/pr-hook.test.js
git commit -m "feat: point writing-plans loads at the project layout skill"
```

---

### Task 4: Docs

**Files:**
- Modify: `docs/dependencies.md`, `PRODUCT.md`, `README.md` (README only through `impeccable`)

- [ ] **Step 1: Update `docs/dependencies.md`**

Add a paragraph after the `worktree-cleanup.md` paragraph: `skills/vibe-project-layout/` adds the placement and restructure guidance; its source is the analyze, plan, approve, move flow of the Composio `file-organizer` skill (https://github.com/ComposioHQ/awesome-claude-skills/blob/master/file-organizer/SKILL.md), rewritten for code projects; duplicate finding, date folders, archiving, deletion and the personal-folder material were not carried over; `pr-advice.js` adds one sentence pointing to it on a `writing-plans` load.

- [ ] **Step 2: Update `PRODUCT.md`**

Add a bullet at the end of "Available now": the `vibe-project-layout` skill (placement order, restructure through `safe-refactor` on request, a short recorded layout), always registered with no setting, plus the sentence on the `writing-plans` note in any onboarded project; advisory, and not yet observed live.

- [ ] **Step 3: Update `README.md` through `impeccable`**

Invoke the `impeccable` skill and, through it, add a short "Keep files organized" section after "Ask the council": ask **"Use `vibe-project-layout` to decide where this goes."**; it follows the project's own layout, hands approved moves to `safe-refactor`, and moves nothing without approval; onboarded projects also get a reminder when `writing-plans` loads.

- [ ] **Step 4: Verify and commit**

Run: `node --test tests/*.test.js && python3 -B -m unittest discover -s tests && python3 -B scripts/validate.py`
Expected: all pass.

```bash
git add docs/dependencies.md PRODUCT.md README.md
git commit -m "docs: describe the project layout skill"
```

---

### Task 5: Verify and review

- [ ] **Step 1: Export and validate the package**

Run: `python3 -B scripts/package.py --output dist/layout-check && python3 -B scripts/validate.py --root dist/layout-check`
Expected: `validation: passed (...)`, with the three new skill files in the inventory. `dist/` is git-ignored; use a new path for every export.

- [ ] **Step 2: Independent review**

Dispatch one fresh reviewer on the most capable model over `git diff main...HEAD` for behavior and security, per repo rules. Fix findings in new commits. Report that the live check (`scripts/live_check.py`, which runs inference) was not run unless the user approves it.
