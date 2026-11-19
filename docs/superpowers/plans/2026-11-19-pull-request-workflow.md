# Pull request workflow and primary checkout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a `vibe-pull-requests` skill, a `pull_requests` onboarding choice and one advisory `execute.after` hook that reminds agents of the PR lifecycle and keeps the primary checkout on a current `main`.

**Architecture:** `project.js` becomes feature-aware (booleans only; `pull_requests` read and written at the main project path). `onboarding.js` gets a backward-compatible `feature` input. A new `pr-advice.js` classifies shell commands and returns short notes, registered through `ctx.tool.hook("execute.after")` only when the host provides it. A skill carries the rules and the template. Nothing blocks; the plugin never runs a write command.

**Tech Stack:** Node ES modules, `node --test`, Python `unittest` for packaging.

**Spec:** `docs/superpowers/specs/2026-11-19-pull-request-workflow-design.md`

## Global Constraints

- Advisory only: never block, rewrite, ask about or run any agent action; only append a note to a result.
- Settings: `{"pull_requests": {"enabled": <bool>, "agent_merge": <bool>}}`; booleans only; missing, unreadable or invalid means `enabled: false` and `agent_merge: false`; unknown feature entries are preserved.
- `pull_requests` is read and written at the main project path (`project`), never from a worktree's own copy. Smart Router stays per checkout and its calls, results, snapshots and locking stay exactly as they are (existing tests stay untouched and green).
- Hook: registered only if `typeof ctx.tool.hook === "function"`; acts only for tools `bash` and `shell`, only when `event.status === "completed"`, and stays silent whenever a field it needs is missing or has an unexpected shape. It is **unverified on the installed OpenCode 2.2.6**; no live inference run and no claim of enforcement anywhere.
- Notes start with `vibe-workflow:`. Cases 1, 3 and 4 fire on every occurrence; case 2 is armed by case 1 or 3 and fires on the next successful `git push`, then disarms; case 5 fires once per resulting branch name per session. State is in memory only.
- Readiness text never says "enforced"; the hook-unavailable fallback is one context line, only when the feature is enabled.
- The skill and template never mention or point to Superpowers.
- Python 3.11+ standard library and Node ES modules only; no new dependencies; no machine paths in exported files; read-only subprocesses with a 2-second timeout and `GIT_*` removed from the environment.
- New exported files go in `scripts/manifest.py`, `package.json` `files` and `tests/test_package.py`. README edits go through the `impeccable` skill and stay short.
- No install, activation, push, PR or merge happens during development.

## Review Focus

- A hook event with a missing field or an unexpected result shape never throws and never changes the result. Pinned in Task 5.
- A command that only mentions a PR command inside text (`git commit -m "fix gh pr merge"`, `echo "gh pr create"`) stays silent. Pinned in Task 4.
- A worktree's own settings file saying `agent_merge: true` never grants merge when the main checkout says false. Pinned in Task 1.
- Disabled, not-onboarded or unreadable settings add nothing. Pinned in Tasks 4 and 5.
- Onboarding from a linked worktree writes only the main checkout's file, and a denied or missing permission writes nothing. Pinned in Task 2.

---

### Task 1: Feature-aware project settings

**Files:**
- Modify: `project.js`
- Test: `tests/project.test.js`

**Interfaces:**
- Produces: `createProjectSettings(directory: string, feature?: "smart_router" | "pull_requests", options?: { atProject?: boolean }) -> { inspect(), preview(values?), apply(values, expectedSnapshot, { signal }?) }`. The default feature `"smart_router"` behaves exactly as today (`values` may be a boolean or `{ enabled }`). For `"pull_requests"`, `values` is `{ enabled?: boolean, agent_merge?: boolean }`. Any other feature name throws `Error("unsupported feature")`.
- `atProject: true` makes the settings root `found.project` (the main worktree from `findRoot`) instead of `found.root`.
- `inspect()` and `preview()` for `pull_requests` add `agent_merge: boolean | null` (null when not onboarded) and, in `preview()`, `previous_agent_merge`. `preview()` defaults `enabled` to the saved value or `true`, and `agent_merge` to the saved value or `false`. Non-boolean values give `{ state: "blocked", reason: "invalid_request" }`.
- Entry validation: `pull_requests` needs boolean `enabled`, and boolean `agent_merge` when present, else `project_settings_invalid`. A `smart_router` instance still only requires other entries to be objects.
- `apply` writes `{ pull_requests: { ...existing, enabled, agent_merge } }` at the entry's index (or appended), keeping other entries, key order, mode and every existing safety check.

- [ ] **Step 1: Write failing tests** in `tests/project.test.js`, extending the fixture helper's `create` to pass `(directory, feature, options)`: `pull_requests preview defaults on with user merge and writes nothing` (preview gives `enabled: true`, `agent_merge: false`, `change: "create"`; apply saves exactly `{version:1,features:[{pull_requests:{enabled:true,agent_merge:false}}]}`; a second preview has `change: "none"`); `pull_requests apply keeps the Smart Router entry, unknown entries and order`; `invalid pull_requests entries block that feature only` (`{enabled:1}` and `{enabled:true,agent_merge:"yes"}` give `project_settings_invalid` for `pull_requests`, while the `smart_router` instance on the same file is not blocked); `a worktree copy never grants merge` (git root whose file has `agent_merge:false`, a linked worktree whose own file has `agent_merge:true`: `create(linked, "pull_requests", { atProject: true }).inspect()` has `root` equal to the main root and `agent_merge: false`, while without `atProject` it reads the worktree copy); `the default feature ignores a pull_requests entry` (file with only that entry gives `not_onboarded`); `unsupported feature throws`.
- [ ] **Step 2: Run** `node --test tests/project.test.js`. Expected: the new tests fail, the existing 11 pass.
- [ ] **Step 3: Implement** the signature above by replacing the hardcoded `smart_router` handling in `readSettings`, `current()`, `preview` and `apply` with a small feature table (`smart_router: ["enabled"]`, `pull_requests: ["enabled", "agent_merge"]`); keep the legacy top-level `smart_router` rejection.
- [ ] **Step 4: Run** `node --test tests/*.test.js`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: make project settings feature-aware`.

### Task 2: Onboarding tool and question

**Files:**
- Modify: `onboarding.js`, `index.js`, `skills/vibe-onboard-project/references/procedure.md`
- Test: `tests/onboarding.test.js`

**Interfaces:**
- Consumes: Task 1.
- Produces: `registerOnboarding(ctx, projects, checkSetup, prGuard)` where `projects = { smart_router, pull_requests }` (each from `createProjectSettings`; `pull_requests` created with `{ atProject: true }`) and `prGuard: () => { state: "ready" | "setup_needed", reason: string | null }`.
- The tool input gains optional `feature` (`"smart_router"` default, or `"pull_requests"`) and boolean `agent_merge`. `agent_merge` with the default feature is `invalid_request`. Calls without `feature` give byte-identical results to today. For `pull_requests`, results carry the settings result plus `client` and `readiness` (`prGuard()` when enabled, `{ state: "off", reason: null }` when not). Apply still goes through `ctx.permission.assert` with the path relative to the session directory (it may start with `../`).
- `index.js` passes `prGuard = () => ({ state: "setup_needed", reason: "guard_unavailable" })` for now; Task 5 replaces it.
- Procedure: after the Smart Router answer, ask **Who merges `[READY]` PRs in this project?** with: **I merge them. Agents mark the PR `[READY]` and wait.** (default), **Agents merge their own `[READY]` PRs.**, **Skip pull request guidance.** (sets `enabled: false`). Preview both features, ask one confirmation for both, then apply each with its own `expected_snapshot`. The preview must say the main checkout's `.vibe-workflow/settings.json` will change and that committing it is the user's step. Readiness lines: off is **Off — no pull request guidance**; ready is **On · Reminders active — advisory only, not enforced**; `guard_unavailable` is **On · Guidance only — reminders are not available in this OpenCode**. If apply is denied or permission is unavailable, nothing is written and the user is told to run onboarding from the main checkout. Keep the existing Smart Router wording, including **Use Smart Router for this project?**.

- [ ] **Step 1: Write failing tests** in `tests/onboarding.test.js`: `pull_requests preview defaults on with user merge and writes nothing`; `pull_requests apply asks for edit permission and saves only that entry` (the recorded permission resources equal `["../.vibe-workflow/settings.json"]`; the file holds the entry and the Smart Router entry is untouched); `agent_merge with the default feature is invalid`; `pull_requests readiness reports guard_unavailable`; `pull_requests apply from a linked worktree writes the main checkout only` (git worktree fixture, session directory inside the worktree: the main file is saved, the worktree has no `.vibe-workflow`, and a thrown permission writes nothing); `procedure asks who merges and keeps the Smart Router question` (the procedure text includes `Who merges`, `Use Smart Router for this project?`, `feature`).
- [ ] **Step 2: Run** `node --test tests/onboarding.test.js`. Expected: new tests fail.
- [ ] **Step 3: Implement** `registerOnboarding` as described (keep the request validation style; extend the allowed key list), wire `index.js` (second settings instance; `prGuard` placeholder), and update the procedure.
- [ ] **Step 4: Run** `node --test tests/*.test.js` and `python3 -B -m unittest discover -s tests`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: ask who merges pull requests during onboarding`.

### Task 3: Skill, template and registration

**Files:**
- Create: `skills/vibe-pull-requests/SKILL.md`, `skills/vibe-pull-requests/references/procedure.md`, `skills/vibe-pull-requests/references/pull-request-template.md`
- Modify: `index.js`, `scripts/manifest.py`, `package.json`, `tests/test_package.py`, `tests/onboarding.test.js`, `tests/plugin.test.js`, `tests/workspace-skill.test.js`
- Test: `tests/pull-request-skill.test.js`

**Interfaces:**
- `SKILL.md` frontmatter: `name: vibe-pull-requests`, one-line `description` (use when opening, updating, marking ready, merging or superseding a pull request, or when the shared checkout may have left `main`), short body loading `references/procedure.md`.
- `procedure.md` states design rules 1 to 7 verbatim in meaning, in our own words, including the readiness rule, `[SUPERSEDED]`, the project's own `.github/pull_request_template.md` taking priority, and rule 7's `git fetch origin main` then `git merge --ff-only origin/main` with the settings-file exception. It reads `agent_merge` from the onboarding choice.
- The template has sections `Why`, `What changes`, `Checks`, `Review`, `Readiness`.
- `index.js` calls `await registerSkill(ctx, "vibe-pull-requests")` after the workspace skill; registered order is onboarding, workspace, pull requests.

- [ ] **Step 1: Write failing tests**: `tests/pull-request-skill.test.js` registers through the plugin (as `tests/workspace-skill.test.js` does) and checks the third skill's id, path and `references/procedure.md` link, that the procedure includes `[BLOCKED]`, `[READY]`, `[SUPERSEDED]`, `git fetch origin main`, `--ff-only`, `agent_merge` and `.github/pull_request_template.md`, that the template has the five section headings, and that no skill file matches `/superpowers/i`. Update the expected skill lists in `tests/onboarding.test.js`, `tests/workspace-skill.test.js` and the Node assertion in `tests/test_package.py`, the expected file set in `tests/test_package.py`, and the fixture copy list in `tests/plugin.test.js`.
- [ ] **Step 2: Run** `node --test tests/*.test.js` and `python3 -B -m unittest tests.test_package`. Expected: failures for the missing skill.
- [ ] **Step 3: Implement** the three files, the registration, three `scripts/manifest.py` entries and `skills/vibe-pull-requests` in `package.json` `files`.
- [ ] **Step 4: Run** `python3 -B scripts/validate.py`, `python3 -B -m unittest discover -s tests`, `node --test tests/*.test.js`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: add the vibe-pull-requests skill`.

### Task 4: Advice core, cases 1 to 3

**Files:**
- Create: `pr-advice.js`
- Test: `tests/pr-advice.test.js`

**Interfaces:**
- Produces: `classify(command: string) -> Array<{ kind: "pr_create", draft: boolean, title: string | null } | { kind: "pr_ready" } | { kind: "pr_retitle", title: string } | { kind: "pr_merge", auto: boolean } | { kind: "push" } | { kind: "checkout" }>`. A command counts only at command position (start, or after `;`, `&&`, `||`, `|`, `(`, with optional `VAR=value` prefixes and an optional `rtk ` prefix); text inside quotes or after other words does not. `--draft` and `-d` mean draft; the title comes from `-t`, `--title` or `--title=`. `gh pr ready --undo` is not `pr_ready`.
- Produces: `createAdvisor({ settings: () => Promise<{ state: string, agent_merge: boolean | null }>, primary: () => Promise<string | null>, checkout: (directory: string) => Promise<{ top: string, branch: string } | null> }) -> { advise(event: { tool: string, command: string, directory?: string, ok: boolean }, sessionID: string) -> Promise<string | null> }`. `advise` never throws, returns `null` unless `tool` is `bash` or `shell`, `ok` is true and `settings().state === "on"`, and joins the notes of every matching action.
- Notes (all start `vibe-workflow:`): case 1 states the lifecycle in one or two sentences; a missing draft adds `gh pr ready --undo`; a missing `[BLOCKED]` prefix adds `gh pr edit --title "[BLOCKED] <title>"`; an unparsable title gives the summary only. Case 2 says to return a `[READY]` PR to `[BLOCKED]` draft with `gh pr ready --undo`. Case 3 with `agent_merge` false says the PR awaits the user's merge unless the user asked for this merge; with true it says to confirm the evidence covers the current head before merging.

- [ ] **Step 1: Write failing tests**: `classify` table (`FOO=1 rtk gh pr create --draft --title "[BLOCKED] x"`, `cd a && gh pr create -d -t "[BLOCKED] a"`, `--title="[BLOCKED] a"`, `gh pr create --fill`, `gh pr ready`, `gh pr ready --undo`, `gh pr edit 3 --title "[READY] a"`, `gh pr merge 3 --auto`, `git push origin x`, `git switch -c x`, `git commit -m "fix gh pr merge"` and `echo "gh pr create"` give nothing); `case 1 fires with a fix for each missing piece`; `case 2 is armed by cases 1 and 3, fires once on the next push, then disarms, per session`; `case 3 wording follows agent_merge`; `disabled, not onboarded, unreadable or failing settings add nothing and never throw`; `a failed call adds nothing`.
- [ ] **Step 2: Run** `node --test tests/pr-advice.test.js`. Expected: failure, module missing.
- [ ] **Step 3: Implement** `classify` and `createAdvisor` (cases 1 to 3; the `checkout` and `pr_merge` actions are classified but produce nothing until Task 5).
- [ ] **Step 4: Run** `node --test tests/*.test.js`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: advise on pull request creation, readiness and pushes`.

### Task 5: Cases 4 and 5, hook wiring and fallback

**Files:**
- Modify: `pr-advice.js`, `index.js`, `scripts/manifest.py`, `package.json`, `tests/test_package.py`, `tests/plugin.test.js`
- Test: `tests/pr-advice.test.js`, `tests/pr-hook.test.js`

**Interfaces:**
- Consumes: Tasks 1 and 4.
- Produces: case 4 note: names the primary path and says to run `git fetch origin main` then `git merge --ff-only origin/main` there only if it is clean (ignoring `.vibe-workflow/settings.json`) and on `main`, else to report and change nothing; with `auto` it says to do this once the auto-merge completes; with `agent_merge` false it adds that the user chose to merge themselves. Case 5 note: when `checkout(directory)` shows `top` equal to the primary path and `branch` not `main` (`"HEAD"` means detached), it names the branch and says to do task work in a worktree; once per branch name per session.
- Produces: `registerPrAdvice(ctx, { settings, primary, checkout? }) -> Promise<boolean>`: returns `false` without registering when `typeof ctx.tool?.hook !== "function"` or registration throws; otherwise registers `ctx.tool.hook("execute.after", handler)` and returns `true`. The handler reads `tool = e.tool`, `command = e.input?.command ?? e.args?.command`, `directory = e.input?.workdir ?? e.input?.cwd ?? ctx.location.directory`, `ok = e.status === "completed"`, calls `advise`, and appends the note to `e.result` only when it is a string or an object with a string `output` or `content`; any other shape is left alone. It never throws. Default `checkout` runs `git rev-parse --show-toplevel --abbrev-ref HEAD` (2-second timeout, `GIT_*` removed) and returns `{ top, branch }` with `top` realpathed, or `null`.
- `index.js`: second settings instance `createProjectSettings(directory, "pull_requests", { atProject: true })`; `primary = async () => (await instance.inspect()).project ?? null`; `registerPrAdvice` runs before `registerOnboarding`; `prGuard` becomes `() => guard ? { state: "ready", reason: null } : { state: "setup_needed", reason: "guard_unavailable" }`; the existing `context` hook adds one line (`vibe-workflow: open pull requests as [BLOCKED] drafts and follow the vibe-pull-requests skill.`) only when the feature is enabled and the hook was not registered.

- [ ] **Step 1: Write failing tests**: in `tests/pr-advice.test.js`, `case 4 names the primary path and the fast-forward commands`, `case 4 with auto and with agent_merge false`, `case 5 names the branch once, stays silent on main, another repository and failure`, `detached HEAD is reported`. In `tests/pr-hook.test.js` (real plugin setup with a fake `ctx.tool.hook` capturing the handler, a git fixture and a written settings file): `registers only when the host provides tool.hook`; `appends a note to a string result and to an output or content object`; `leaves failed calls, other tools, missing commands and unknown result shapes unchanged`; `never throws when settings fail`; `adds nothing when the feature is disabled`; `fallback context line only when enabled and no hook`; `onboarding readiness follows hook availability`.
- [ ] **Step 2: Run** `node --test tests/*.test.js`. Expected: new tests fail.
- [ ] **Step 3: Implement** the interfaces; add `pr-advice.js` to `scripts/manifest.py`, `package.json` `files` and `tests/test_package.py`, and to the fixture copy list in `tests/plugin.test.js`.
- [ ] **Step 4: Run** `python3 -B scripts/validate.py`, `python3 -B -m unittest discover -s tests`, `node --test tests/*.test.js`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: advise after merges and checkout changes, register the hook`.

### Task 6: Docs, README and final package check

**Files:**
- Modify: `PRODUCT.md`, `README.md`, `docs/dependencies.md`, `docs/development.md`

- [ ] **Step 1: Update** `PRODUCT.md` (move PR handling and the PR template out of "Planned evaluations" into available capabilities, referencing issues #328 and #329 without closing them, and keep the advisory and not-enforced wording), `docs/dependencies.md` (new files and the earlier-plugin sources; managed delivery not carried over), and `docs/development.md` (a short manual observation recipe: a throwaway logging plugin the user runs once to record `execute.after` event keys, saying the hook is unverified until then).
- [ ] **Step 2: Update** `README.md` through the `impeccable` skill: a few lines saying onboarding also asks who merges `[READY]` PRs and that the reminders are advisory and not enforced.
- [ ] **Step 3: Run** `python3 -B scripts/validate.py`, `python3 -B -m unittest discover -s tests`, `node --test tests/*.test.js`, then `python3 -B scripts/package.py --output /private/tmp/vw-<random>` and `python3 -B scripts/validate.py --root` on it. Expected: all pass.
- [ ] **Step 4: Commit** `docs: record the pull request workflow`.

## After the tasks

Get an independent review of the whole branch (AGENTS.md requires it), then open a PR. Report unit proof, package checks and the unverified host hook separately; no install, activation, real PR or merge happens in this change.
