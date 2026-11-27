# Security Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a `vibe-security-review` skill and `/vibe-security-review` command: a proportional ship check prompted at delivery, and an on-demand full project review with private report storage.

**Architecture:** The method lives in a new skill (`SKILL.md` plus four references). Existing advisory channels point to it: skill-load notes in `src/skill-advice.js` and PR shell notes in `src/pr-advice.js`. The command reuses the council's command pattern through a shared helper. No new runtime, storage code or enforcement.

**Tech Stack:** JavaScript ES modules (Node 22 test runner), Markdown skills, Python 3.11 package tooling.

**Spec:** `docs/superpowers/specs/2026-11-27-security-review-design.md` (read it in full before Task 1; section numbers below refer to it).

## Global Constraints

- Read `AGENTS.md` and `docs/development.md` lines 1-86 before editing.
- Skill frontmatter must match `^---\nname: <id>\ndescription: <one line>\n---\n` (`registerSkill` in `src/onboarding.js:11`).
- Every new shipped file goes in `package.json` `files` (a directory entry covers its files) and in `scripts/manifest.py` `FILES`, in the same commit.
- Plugin notes start with `vibe-workflow:` and end with `An explicit request from your human partner in this session overrides this.` (skill notes only).
- No Trail of Bits text is copied into any file (CC-BY-SA-4.0). Name their skills; describe ideas in our own words.
- Wording is client-neutral; never say a check is enforced.
- `README.md` changes only through the `impeccable` skill.
- Do not install, activate or publish anything. `python3 -B scripts/live_check.py --injection` is pre-authorized (`AGENTS.md`) because this plan changes injected notes; any other live check needs the user.
- Commands that prove a task: `node --test "tests/**/*.test.js"`, `python3 -B -m unittest discover -s tests -t .`, `python3 -B scripts/validate.py`.

## Review Focus

1. A project whose own `.github/pull_request_template.md` wins: the `Security` section must still be added (spec §1 Result). Pinned in Task 1's skill test.
2. Reports folder `.gitignore` present but edited by someone (not exactly `*`): it is rewritten before the write, and `git check-ignore` decides (spec §3). Pinned in Task 1's skill test.
3. No main checkout found (not a Git repository, or `git worktree list` fails): nothing is written; the report goes to chat with the reason. Pinned in Task 1's skill test.
4. Onboarded project with the git workflow off: `finishing-a-development-branch` still gets the security note, alone. Pinned in Task 3's test.
5. The command with an empty or whitespace prompt sends only the base line. Pinned in Task 2's test.

---

### Task 1: The `vibe-security-review` skill

**Files:**
- Create: `skills/vibe-security-review/SKILL.md`
- Create: `skills/vibe-security-review/references/ship-check.md`
- Create: `skills/vibe-security-review/references/full-review.md`
- Create: `skills/vibe-security-review/references/reviewer-brief.md`
- Create: `skills/vibe-security-review/references/report-storage.md`
- Modify: `package.json` (`files`: add `"skills/vibe-security-review"`)
- Modify: `scripts/manifest.py` (`FILES`: add the five paths above)
- Modify: `tests/tooling/test_package.py:105-121` (expected export set: add the five paths)
- Test: `tests/skills/security-review-skill.test.js`

**Interfaces:**
- Produces: skill id `vibe-security-review`; reference names `ship-check.md`, `full-review.md`, `reviewer-brief.md`, `report-storage.md`. Tasks 2-4 link to these by name.

**Content each file must carry** (prose is the implementer's; every decision below comes from the spec):

- `SKILL.md`: description line, for example `Use when work is about to be delivered (code review, finishing a branch, completing a plan) to run a security check sized to the change, or when the user asks for a full security review of a project.` Body: two modes, which reference to read for each, read-only rule, "no findings is not proof of safety".
- `ship-check.md` (spec §1): what is reviewed (merge-base to head plus staged, unstaged and untracked files about to be committed); the tier table 0-3 with triggers verbatim from the spec; the three tier rules plus the never-tier-0 list; the short list; that tier 2 adds a security section to the existing reviewer's brief and tier 3 dispatches one reviewer with `reviewer-brief.md`; the `Security` PR section fields (head commit, tier and reason, checked, findings with severity and status, limits), added whatever template the project uses, including its own `.github/pull_request_template.md`; public-repository redaction; D1 blocking rule; re-check only the tiers new commits reach; going public covers Git history (`gitleaks` if installed, else `git log -p`).
- `reviewer-brief.md` (spec §1 Reviewer): the brief to fill in for a tier-3 reviewer or a full-review sweep: `agent: "explore"`; model via `smart_router_select` with `reviewing` set to the author's model, else the top tier; only providers already in use; read-only by instruction, no installs, builds, scanner runs or partner scripts without the user's approval of that exact run; the trace / counterevidence / confirmed-likely-hardening / introduced-or-pre-existing rules; secrets quoted as file, line and type only; Trail of Bits `differential-review` and `fp-check` used when listed in the agent's available skills, with any report file pointed at the reports folder.
- `full-review.md` (spec §2): steps 1-6 (bound first, map, sweep by area with the seven areas, verify with a refute pass and sibling search, report to the reports folder plus a chat summary with the path, no fixes); partner tools used when present, `supply-chain-risk-auditor` only after asking.
- `report-storage.md` (spec §3): main checkout from the first entry of `git worktree list --porcelain`; folder `.vibe-workflow/security-reports/`; folder `.gitignore` exactly `*`, rewritten when missing or different; `git -C <main checkout> check-ignore -q <report path>` before writing, and on failure no write, report in chat with the reason; no main checkout found means the same chat fallback; file name `YYYY-MM-DD-HHMM-<full|ship>-<topic>.md`, never overwritten; `0700` folder, `0600` files; never `git add -f`, never copy a report into a commit, PR, issue or log; the stated limits (backups, cloud sync, `git clean -x`).

- [ ] **Step 1: Write the failing test** `tests/skills/security-review-skill.test.js`, in the style of `tests/skills/council-skill.test.js` (`read`, `hasAll` helpers):

```js
test("skill frontmatter matches the registration pattern and links every reference", async () => {
  const text = await read("SKILL.md")
  assert.match(text, /^---\nname: vibe-security-review\ndescription: [^\n]+\n---\n[\s\S]+/)
  for (const ref of ["ship-check.md", "full-review.md", "reviewer-brief.md", "report-storage.md"]) assert.ok(text.includes(`references/${ref}`), ref)
})
test("ship check carries the tiers, rules and PR section", async () => {
  hasAll(await read("references/ship-check.md"), ["Tier 0", "Tier 1", "Tier 2", "Tier 3", "merge-base", "untracked",
    "take the higher one", "Removing a check", "AGENTS.md", "SKILL.md", "## Security", ".github/pull_request_template.md",
    "[BLOCKED]", "pre-existing", "introduced", "could not run", "git log -p", "gitleaks", "reviewer-brief.md", "report-storage.md"])
})
test("reviewer brief carries the read-only, routing and redaction rules", async () => {
  hasAll(await read("references/reviewer-brief.md"), ['agent: "explore"', "smart_router_select", "reviewing", "top tier",
    "counterevidence", "confirmed", "likely", "hardening", "pre-existing", "file, line and type", "differential-review", "fp-check",
    "available skills", "approved that exact run"])
})
test("full review states its bound, areas, verification and output", async () => {
  hasAll(await read("references/full-review.md"), ["bound", "supply chain", "AI-agent", "refute", "sibling",
    "report-storage.md", "No fixes", "supply-chain-risk-auditor", "agentic-actions-auditor", "sharp-edges", "Semgrep", "CodeQL"])
})
test("report storage keeps reports private and outside the task worktree", async () => {
  hasAll(await read("references/report-storage.md"), [".vibe-workflow/security-reports/", "git worktree list --porcelain",
    "check-ignore -q", "exactly `*`", "rewrite", "chat", "0700", "0600", "git add -f", "never overwritten", "git clean -x",
    "not a Git repository"])
})
test("no skill file copies Trail of Bits text or claims enforcement", async () => {
  for (const name of ["SKILL.md", ...["ship-check", "full-review", "reviewer-brief", "report-storage"].map((n) => `references/${n}.md`)]) {
    const text = await read(name)
    for (const copied of ["Heartbleed was 2 lines", "pit of success", "Rationalizations"]) assert.ok(!text.includes(copied), `${name}: ${copied}`)
    assert.doesNotMatch(text.replaceAll("not enforced", ""), /enforce/i, name)
    assert.doesNotMatch(text, /superpowers/i, name)
  }
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --test tests/skills/security-review-skill.test.js`
Expected: FAIL with `ENOENT` on `SKILL.md`.

- [ ] **Step 3: Write the five skill files** with the content listed above. Keep each reference under about 120 lines.

- [ ] **Step 4: Register the files** in `package.json` `files`, `scripts/manifest.py` `FILES`, and the expected set in `tests/tooling/test_package.py`.

- [ ] **Step 5: Run the tests**

Run: `node --test tests/skills/security-review-skill.test.js && python3 -B -m unittest discover -s tests -t . && python3 -B scripts/validate.py`
Expected: all pass. `validation: passed`.

- [ ] **Step 6: Commit**

```bash
git add skills/vibe-security-review package.json scripts/manifest.py tests/skills/security-review-skill.test.js tests/tooling/test_package.py
git commit -m "feat: add vibe-security-review skill"
```

### Task 2: Register the skill and the `/vibe-security-review` command

**Files:**
- Modify: `src/onboarding.js:11-16` (add `registerSkillCommand` beside `registerSkill`)
- Modify: `src/council.js:172-185` (use `registerSkillCommand`; behavior unchanged)
- Modify: `index.js:600` (register skill and command after `vibe-project-layout`)
- Modify: `tests/plugin/onboarding.test.js:56`, `tests/skills/pull-request-skill.test.js:22`, `tests/skills/workspace-skill.test.js:24` (append `vibe-security-review` to the skill lists)
- Modify: `tests/plugin/plugin.test.js:145` (fixture copies `skills/vibe-security-review/SKILL.md`)
- Test: `tests/plugin/plugin.test.js` (new test beside the council command test at line 1079)

**Interfaces:**
- Consumes: skill id from Task 1.
- Produces: `export async function registerSkillCommand(ctx, { name: string, description: string, line: string }): Promise<void>`: a no-op without `ctx.command?.transform`; otherwise adds a command whose `execute({ sessionID, prompt, delivery })` calls `ctx.session.prompt` with `text` = `line`, or `` `${line} ${extra}` `` when the trimmed prompt text is non-empty, and strips `mention` from `files`, `agents` and `skills`, exactly as `src/council.js:176-184` does today.

- [ ] **Step 1: Write the failing test** in `tests/plugin/plugin.test.js`, copying the council command test's setup:

```js
test("security review command forwards the skill line and strips mentions", async () => {
  // same harness as "council command forwards the skill line and strips mentions"
  const command = commands.get("vibe-security-review")
  assert.equal(command.description, "Run a full security review of this project.")
  await command.execute({ sessionID: "s1", delivery: "d", prompt: { text: "  ", files: [{ path: "a", mention: "m" }] } })
  await command.execute({ sessionID: "s1", delivery: "d", prompt: { text: " src/api " } })
  assert.equal(prompts[0].text, "Use the vibe-security-review skill for a full review.")
  assert.deepEqual(prompts[0].files, [{ path: "a" }])
  assert.equal(prompts[1].text, "Use the vibe-security-review skill for a full review. src/api")
})
```

Also update the three skill-list assertions to end with `"vibe-security-review"` (the workspace test lists `["vibe-security-review", "vibe-security-review"]`).

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/plugin/plugin.test.js tests/plugin/onboarding.test.js tests/skills/*.test.js`
Expected: FAIL: no `vibe-security-review` command, skill lists one short.

- [ ] **Step 3: Implement** `registerSkillCommand` in `src/onboarding.js`; switch `registerCouncil` to call it with `{ name: "vibe-council", description: "Run the council on a set of decisions.", line: "Use the vibe-council skill." }`; in `index.js` after `registerSkill(ctx, "vibe-project-layout")` add `registerSkill(ctx, "vibe-security-review")` then `registerSkillCommand(ctx, { name: "vibe-security-review", description: "Run a full security review of this project.", line: "Use the vibe-security-review skill for a full review." })`. Registration is unconditional (any project, like the council).

- [ ] **Step 4: Run the full Node suite**

Run: `node --test "tests/**/*.test.js"`
Expected: all pass, including the unchanged council command test and "setup succeeds without a command API".

- [ ] **Step 5: Commit**

```bash
git add src/onboarding.js src/council.js index.js tests
git commit -m "feat: register the security review skill and command"
```

### Task 3: Ship-check skill notes

**Files:**
- Modify: `src/skill-advice.js`
- Test: `tests/plugin/skill-advice.test.js`

**Interfaces:**
- Consumes: `createSkillAdvisor({ settings, onboarded })` as today.
- Produces: `skillNote(skill)` returns, for the three skills below in an onboarded project, the security note; for `finishing-a-development-branch` with the git workflow on, the existing note, then `"\n"`, then the security note. Everything else unchanged.

Exact note strings (each ends with `OVERRIDE`):

```js
const SHIP_REVIEW = "vibe-workflow: your human partner asks for a security check sized to the change. Before you " +
  "dispatch the code reviewer, follow the vibe-security-review skill's ship check: pick the change's tier, add its " +
  "security section to the reviewer's brief at tier 2, and dispatch its security reviewer at tier 3. " + OVERRIDE
const SHIP_FINAL = "vibe-workflow: your human partner asks for a security check sized to the change. At the final " +
  "whole-branch review, follow the vibe-security-review skill's ship check once for the whole branch, not per task. " + OVERRIDE
const SHIP_FINISH = "vibe-workflow: before you open or ready the pull request, confirm the vibe-security-review ship " +
  "check covers the current head and its Security section is in the body; run it if not. " + OVERRIDE
```

Keys: `requesting-code-review` → `SHIP_REVIEW`, `subagent-driven-development` → `SHIP_FINAL`, `finishing-a-development-branch` → `SHIP_FINISH`. Gate: `onboarded() === true`.

- [ ] **Step 1: Write the failing tests** in `tests/plugin/skill-advice.test.js`:

```js
test("skillNote sends the ship check on review and final-review skills in any onboarded project", async () => {
  for (const settings of [on, { state: "off" }, null]) {
    const { skillNote } = advisor(settings)
    assert.equal(await skillNote("superpowers:requesting-code-review"), SHIP_REVIEW)
    assert.equal(await skillNote("subagent-driven-development"), SHIP_FINAL)
  }
  assert.equal(await advisor(on, { isOnboarded: false }).skillNote("requesting-code-review"), null)
  assert.equal(await advisor(on, { onboarded: async () => { throw new Error("boom") } }).skillNote("requesting-code-review"), null)
})
test("finishing gets the security note alone when the git workflow is off, and after the workflow note when on", async () => {
  assert.equal(await advisor({ state: "off" }).skillNote("finishing-a-development-branch"), SHIP_FINISH)
  const user = advisor({ state: "on", agent_merge: false, worktree_cleanup: null })
  assert.equal(await user.skillNote("finishing-a-development-branch"), LEAD +
    "Stop at [READY] and tell your human partner it awaits their merge. Keep the worktree after the merge. " + LAST + "\n" + SHIP_FINISH)
  assert.equal(await advisor({ state: "off" }, { isOnboarded: false }).skillNote("finishing-a-development-branch"), null)
})
```

Update the existing tests: "disabled ... add no skill note" must pass `{ isOnboarded: false }` for `finishing-a-development-branch`, and the "finishing and worktree" expectations for `finishing-a-development-branch` gain `"\n" + SHIP_FINISH`. Add `assert.doesNotMatch(note, MODEL_NAME)` for each new constant.

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/plugin/skill-advice.test.js`
Expected: FAIL: `null` where the security notes are expected.

- [ ] **Step 3: Implement** a second table `SHIP_NOTES` keyed by skill name in `src/skill-advice.js`; `skillNote` computes the existing note and, when the key is in `SHIP_NOTES` and `onboarded()` is `true`, the security note; it returns them joined by `"\n"`, or `null` when both are absent. A throw in either gate returns only the other note's result, never throws. Keep the `Object.hasOwn` lookup so `__proto__` and `toString` still return `null`.

- [ ] **Step 4: Run the full Node suite**

Run: `node --test "tests/**/*.test.js"`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/skill-advice.js tests/plugin/skill-advice.test.js
git commit -m "feat: prompt the security ship check at review and finish"
```

### Task 4: Pull request guidance

**Files:**
- Modify: `src/pr-advice.js:156-167` (`SUMMARY`, `NOTES.userMerges`, `NOTES.agentMerges`)
- Modify: `skills/vibe-pull-requests/references/pull-request-template.md` (add `## Security` between `## Review` and `## Readiness`)
- Modify: `skills/vibe-pull-requests/references/procedure.md` (Readiness and The primary checkout sections)
- Test: `tests/plugin/pr-advice.test.js`, `tests/skills/pull-request-skill.test.js`

**Interfaces:**
- Consumes: skill and reference names from Task 1.

Exact additions:
- `SUMMARY` gains, before ` See the vibe-pull-requests skill.`: ` Its Security section must cover the current head before [READY] (vibe-security-review ship check).`
- `NOTES.userMerges` and `NOTES.agentMerges` each gain a leading sentence after `vibe-workflow: `: `confirm the Security section covers this head and no blocking security finding is open; `. Keep the rest of each string as is.
- Template section body: `[head commit; tier and reason; what was checked; findings with severity and status, or "None substantiated"; limits. Follow the vibe-security-review skill.]`
- Procedure, Readiness: add that the `Security` section covers the current head and no blocking finding (vibe-security-review ship check) is open; the section is added even when the project's own template wins.
- Procedure, The primary checkout: add a second exception: writing a report into the ignored `.vibe-workflow/security-reports/` folder, as the vibe-security-review skill's report storage describes. It does not make the checkout unclean.

- [ ] **Step 1: Write the failing tests**
  - `tests/plugin/pr-advice.test.js`: `gh pr create --draft --title "[BLOCKED] x"` note matches `/Security section must cover the current head before \[READY\]/`; `gh pr ready 3` note matches `/no blocking security finding is open/` for both `agent_merge` true and false.
  - `tests/skills/pull-request-skill.test.js`: the "five sections" test becomes six, adding `Security`, and asserts its index lies between `## Review` and `## Readiness`; the procedure phrase list gains `".vibe-workflow/security-reports/"`, `"vibe-security-review"`, `"Security section"`.

- [ ] **Step 2: Run to verify they fail**

Run: `node --test tests/plugin/pr-advice.test.js tests/skills/pull-request-skill.test.js`
Expected: FAIL on the new assertions.

- [ ] **Step 3: Implement** the strings and Markdown edits above. Keep "skill files never point to Superpowers or claim enforcement" passing.

- [ ] **Step 4: Run the full Node suite**

Run: `node --test "tests/**/*.test.js"`
Expected: all pass (fix any exact-string expectation of `SUMMARY` in `tests/plugin/pr-hook.test.js` or `plugin.test.js`).

- [ ] **Step 5: Commit**

```bash
git add src/pr-advice.js skills/vibe-pull-requests tests
git commit -m "feat: add the Security section to pull request guidance"
```

### Task 5: Trail of Bits portability check (static, no install)

**Files:**
- Modify: `docs/development.md` (new section `## Security review: what is known`, after the last section)

- [ ] **Step 1: Read, do not install.** Fetch the `SKILL.md` of `differential-review`, `fp-check`, `agentic-actions-auditor`, `sharp-edges` and `supply-chain-risk-auditor` from `https://raw.githubusercontent.com/trailofbits/skills/main/plugins/<name>/skills/<name>/SKILL.md`. For each, record: tools named in `allowed-tools`; uses of `{baseDir}`, `${CLAUDE_PLUGIN_ROOT}`, the `Workflow` tool or plugin agents; bundled scripts and their runtime needs (`uv`, `gh`, network).
- [ ] **Step 2: Write the section**: one line per skill with those facts and a verdict, `likely portable` or `Claude Code only`, marked "static reading, not observed on OpenCode", with the date and the upstream commit read (`gh api repos/trailofbits/skills/commits/main --jq .sha`).
- [ ] **Step 3: Stop.** Do not edit `skills/vibe-prepare-my-workspace/partners.json`. Its procedure marks a tool without official OpenCode support as `not-supported`, and listing needs a live load check that requires installing. Report the verdicts to the user and ask whether to authorize a live check or leave the skills unlisted (detection through the available skills list still works).
- [ ] **Step 4: Commit**

```bash
git add docs/development.md
git commit -m "docs: record Trail of Bits portability, static reading"
```

### Task 6: Product docs, README and live injection check

**Files:**
- Modify: `PRODUCT.md` (move "code and security review" out of Planned evaluations; add a Security review bullet under Available now: skill, command, ship-check notes, report folder, advisory and not yet observed live)
- Modify: `README.md` (a short `## Security review` section, through the `impeccable` skill)
- Modify: `docs/development.md` (in the Task 5 section: the note texts' triggers and that skill-load notes are not covered by `--injection`)
- Modify: `tests/tooling/opencode-verified.json` (only if the injection check writes it)

- [ ] **Step 1: Update `PRODUCT.md` and `docs/development.md`** as listed.
- [ ] **Step 2: Load the `impeccable` skill and add the README section**: what the ship check does, how to ask for a full review (`/vibe-security-review` or "Use `vibe-security-review` to review this project."), where reports go and that they are never committed. Keep it under 15 lines.
- [ ] **Step 3: Run every check**

```sh
python3 -B -m unittest discover -s tests -t .
node --test "tests/**/*.test.js"
python3 -B scripts/validate.py
python3 -B scripts/package.py --output dist-security-review
python3 -B scripts/validate.py --root dist-security-review
```

Expected: all pass; `validation: passed` twice. Delete `dist-security-review` afterwards.

- [ ] **Step 4: Commit**

```bash
git add PRODUCT.md README.md docs/development.md
git commit -m "docs: describe the security review"
```

- [ ] **Step 5: Run the injection check** (pre-authorized): `python3 -B scripts/live_check.py --injection`. Expected: every model and the shell probe pass. If it writes `tests/tooling/opencode-verified.json`, commit that file (`git commit -m "test: record injection check"`). If it fails, stop and tell the user; do not retry another way.
