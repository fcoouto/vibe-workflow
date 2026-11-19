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
- Hook "success" means `event.status === "completed"` and no known exit field is a nonzero number (`e.result?.metadata?.exit`, `e.result?.metadata?.exitCode`, `e.exitCode`, `e.metadata?.exit`); an event with no exit field counts as success, which is another unverified assumption.
- Hook: registered only if `typeof ctx.tool.hook === "function"`; acts only for tools `bash` and `shell`, only when `event.status === "completed"`, and stays silent whenever a field it needs is missing or has an unexpected shape. It is **unverified on the installed OpenCode 2.2.6**; no live inference run and no claim of enforcement anywhere.
- Notes start with `vibe-workflow:`. Cases 1, 3 and 4 fire on every occurrence; case 2 is armed by case 1 or 3 and fires on the next successful `git push`, then disarms; case 5 fires once per resulting branch name per session. State is in memory only.
- Session identity is `e.sessionID ?? e.session?.id`; when it is missing, cases 2 and 5 stay silent and cases 1, 3 and 4 still fire. Per-session maps are bounded (200 sessions, oldest evicted), cleared in the plugin's cleanup, and the handler does nothing once the plugin is closed.
- Readiness text never says "enforced" except in "not enforced"; the hook-unavailable fallback is one context line, only when the feature is enabled.
- The skill and template never mention or point to Superpowers.
- Python 3.11+ standard library and Node ES modules only; no new dependencies; no machine paths in exported files; read-only subprocesses with a 2-second timeout and `GIT_*` removed from the environment.
- New exported files go in `scripts/manifest.py`, `package.json` `files` and `tests/test_package.py`. README edits go through the `impeccable` skill and stay short.
- No install, activation, push, PR or merge happens during development.

## Review Focus

- A hook event with a missing field or an unexpected result shape never throws and never changes the result. Pinned in Task 5.
- A command that only mentions a PR command inside text (`git commit -m "fix gh pr merge"`, `echo "gh pr create"`) stays silent. Pinned in Task 4.
- A worktree's own settings file saying `agent_merge: true` never grants merge when the main checkout says false. Pinned in Task 1.
- Disabled, not-onboarded or unreadable settings add nothing. Pinned in Tasks 4 and 5.
- Both onboarding choices apply in sequence on a primary checkout, where the two features share one file and snapshot. Pinned in Task 2.
- A failed command (nonzero exit) never produces a note. Pinned in Task 5.
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
- Entry validation: each instance validates only its own entry: `pull_requests` needs boolean `enabled`, and boolean `agent_merge` when present, else `project_settings_invalid`; every other entry only has to be an object. An invalid entry of one feature never blocks the other feature's instance.
- `apply` for `pull_requests` requires boolean `enabled` and boolean `agent_merge` (else `{ state: "blocked", reason: "invalid_request" }`); `unchanged` compares both fields. Results for the default feature never gain an `agent_merge` key.
- `apply` writes `{ pull_requests: { ...existing, enabled, agent_merge } }` at the entry's index (or appended), keeping other entries, key order, mode and every existing safety check.

- [ ] **Step 1: Write failing tests** in `tests/project.test.js`, extending the fixture helper's `create` to pass `(directory, feature, options)`: `pull_requests preview defaults on with user merge and writes nothing` (preview gives `enabled: true`, `agent_merge: false`, `change: "create"`; apply saves exactly `{version:1,features:[{pull_requests:{enabled:true,agent_merge:false}}]}`; a second preview has `change: "none"`); `pull_requests apply keeps the Smart Router entry, unknown entries and order`; `invalid pull_requests entries block that feature only` (`{enabled:1}` and `{enabled:true,agent_merge:"yes"}` give `project_settings_invalid` for `pull_requests`, while the `smart_router` instance on the same file is not blocked); `a worktree copy never grants merge` (git root whose file has `agent_merge:false`, a linked worktree whose own file has `agent_merge:true`: `create(linked, "pull_requests", { atProject: true }).inspect()` has `root` equal to the main root and `agent_merge: false`, while without `atProject` it reads the worktree copy); `the default feature ignores a pull_requests entry` (file with only that entry gives `not_onboarded`); `pull_requests apply requires both booleans and unchanged compares both`; `an invalid smart_router entry does not block the pull_requests instance`; `default-feature results have no agent_merge key`; `unsupported feature throws`.
- [ ] **Step 2: Run** `node --test tests/project.test.js`. Expected: the new tests fail, all existing tests pass.
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
- Procedure: after the Smart Router answer, ask **Who merges `[READY]` PRs in this project?** with: **I merge them. Agents mark the PR `[READY]` and wait.** (default), **Agents merge their own `[READY]` PRs.**, **Skip pull request guidance.** (sets `enabled: false`). Preview both features and ask one confirmation for both. Both features share one file and one snapshot on a primary checkout, so apply Smart Router first, then preview `pull_requests` again with the confirmed values; if its `change` and values match what the user confirmed, apply with that fresh `snapshot` without asking again, otherwise ask again. `pull_requests` apply always sends both booleans; **Skip** sends `enabled: false` and `agent_merge: false`. The preview must say the main checkout's `.vibe-workflow/settings.json` will change and that committing it is the user's step. Readiness lines: off is **Off — no pull request guidance**; ready is **On · Reminders registered — not yet observed in this OpenCode; advisory only, not enforced**; `guard_unavailable` is **On · Guidance only — reminders are not available in this OpenCode**. If apply is denied or permission is unavailable, nothing is written and the user is told to run onboarding from the main checkout. Keep the existing Smart Router wording, including **Use Smart Router for this project?**. Update the tool `description` to mention the pull request preference.

- [ ] **Step 1: Write failing tests** in `tests/onboarding.test.js`: `pull_requests preview defaults on with user merge and writes nothing`; `pull_requests apply asks for edit permission and saves only that entry` (the recorded permission resources equal `["../.vibe-workflow/settings.json"]`; the file holds the entry and the Smart Router entry is untouched); `agent_merge with the default feature is invalid`; `pull_requests readiness reports guard_unavailable`; `pull_requests apply from a linked worktree writes the main checkout only` (git worktree fixture, session directory inside the worktree: the main file is saved, the worktree has no `.vibe-workflow`, and a thrown permission writes nothing); `both choices apply in sequence on a primary checkout` (apply Smart Router, re-preview, apply `pull_requests` with the fresh snapshot; both entries are saved); `skip saves enabled false and agent_merge false with readiness off`; `pull_requests apply without agent_merge is blocked`; `Smart Router results have no agent_merge key`; `a missing ctx.permission blocks pull_requests apply with project_permission_unavailable`; `procedure asks who merges and keeps the Smart Router question` (the procedure text includes `Who merges`, `Use Smart Router for this project?`, `feature`, `Reminders registered`).
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
- `procedure.md` states design rules 1 to 7 verbatim in meaning, in our own words, including the readiness rule, `[SUPERSEDED]`, the project's own `.github/pull_request_template.md` taking priority, and rule 7's `git fetch origin main` then `git merge --ff-only origin/main` with the settings-file exception. To learn the merge choice it calls `vibe_workflow_onboard` with `{"v":1,"op":"preview","feature":"pull_requests"}`; agents may merge only when `previous_enabled === true` and `previous_agent_merge === true`, and a missing, off, blocked or not-onboarded result means the user merges.
- The template has sections `Why`, `What changes`, `Checks`, `Review`, `Readiness`.
- `index.js` calls `await registerSkill(ctx, "vibe-pull-requests")` after the workspace skill; registered order is onboarding, workspace, pull requests.

- [ ] **Step 1: Write failing tests**: `tests/pull-request-skill.test.js` registers through the plugin (as `tests/workspace-skill.test.js` does) and checks the third skill's id, path and `references/procedure.md` link, that the procedure includes `[BLOCKED]`, `[READY]`, `[SUPERSEDED]`, `git fetch origin main`, `--ff-only`, `agent_merge` and `.github/pull_request_template.md`, that the template has the five section headings, that nothing under `skills/vibe-pull-requests/` matches `/superpowers/i` and that no text there says "enforced" except in "not enforced", and that the procedure also includes `.vibe-workflow/settings.json`, `worktree`, `--undo`, `"feature":"pull_requests"`, `previous_agent_merge` and an evidence phrase for rule 3 (`observed on this head`). Update the expected skill lists in `tests/onboarding.test.js`, `tests/workspace-skill.test.js` and the Node assertion in `tests/test_package.py`, the expected file set in `tests/test_package.py`, and the fixture copy list in `tests/plugin.test.js`.
- [ ] **Step 2: Run** `node --test tests/*.test.js` and `python3 -B -m unittest tests.test_package`. Expected: failures for the missing skill.
- [ ] **Step 3: Implement** the three files, the registration, three `scripts/manifest.py` entries and `skills/vibe-pull-requests` in `package.json` `files`.
- [ ] **Step 4: Run** `python3 -B scripts/validate.py`, `python3 -B -m unittest discover -s tests`, `node --test tests/*.test.js`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: add the vibe-pull-requests skill`.

### Task 4: Advice core, cases 1 to 3

**Files:**
- Create: `pr-advice.js`
- Test: `tests/pr-advice.test.js`

**Interfaces:**
- Produces: `classify(command: string) -> Array<{ kind: "pr_create", draft: boolean, title: string | null } | { kind: "pr_ready", undo: boolean } | { kind: "pr_retitle", title: string } | { kind: "pr_merge", auto: boolean } | { kind: "push" } | { kind: "checkout", dir: string | null, moved: boolean }>`. Rules: scan the command tracking single quotes, double quotes, `$(...)`, backticks and heredocs (`<<EOF`, `<<'EOF'`, `<<-EOF` until the delimiter line); only text outside all of those is a candidate. Split it into simple commands at `;`, `&&`, `||`, `|`, `&`, newline, `(`, `)`, `{` and `}`. In each, skip leading `VAR=value` words (the value may be quoted) and one optional `rtk` word, then match `gh pr <create|ready|edit|merge>` or `git [-C <dir>] [-c <k=v>] <push|checkout|switch|branch -m|branch -M|branch --move>`. Flags count only as unquoted tokens: `--draft` and `-d` mean draft; the title comes from `-t`, `--title` or `--title=`. `gh pr ready --undo` is `{ kind: "pr_ready", undo: true }`. `git push --dry-run` is not a push. `moved` is true when a `cd` or `pushd` simple command came earlier; `dir` is the `-C` value.
- Produces: `createAdvisor({ settings: () => Promise<{ state: string, agent_merge: boolean | null }>, primary: () => Promise<string | null>, checkout: (directory: string) => Promise<{ top: string, branch: string } | null> }) -> { advise(event: { tool: string, command: string, directory?: string, ok: boolean }, sessionID: string) -> Promise<string | null> }`. `advise` never throws. It runs `classify` first and returns `null` without reading settings when nothing matches or `tool` is not `bash` or `shell` or `ok` is false; only then does it call `settings()` and return `null` unless `state === "on"`. It joins the notes of every matching action. Per-session state (case 2 armed flag, case 5 seen branches) is keyed by `sessionID`, bounded and clearable (`advisor.clear()`); with no `sessionID`, cases 2 and 5 are skipped.
- Notes (all start `vibe-workflow:`): case 1 states the lifecycle in one or two sentences; a missing draft adds `gh pr ready --undo`; a missing `[BLOCKED]` prefix adds `gh pr edit --title "[BLOCKED] <title>"`; an unparsable title gives the summary only. Case 2 says to return a `[READY]` PR to `[BLOCKED]` draft with `gh pr ready --undo`; it disarms on its first note, on `gh pr ready --undo` and on a retitle to `[BLOCKED]`. Case 3 with `agent_merge` false says the PR awaits the user's merge unless the user asked for this merge; with true it says to confirm the evidence covers the current head before merging.

- [ ] **Step 1: Write failing tests**: `classify` table (`FOO=1 rtk gh pr create --draft --title "[BLOCKED] x"`, `cd a && gh pr create -d -t "[BLOCKED] a"`, `--title="[BLOCKED] a"`, `gh pr create --fill`, `gh pr ready`, `gh pr ready --undo`, `gh pr edit 3 --title "[READY] a"`, `gh pr merge 3 --auto`, `git push origin x`, `git switch -c x`, `git commit -m "fix gh pr merge"` and `echo "gh pr create"` give nothing; a newline-separated `a\ngh pr create --draft`; a `gh pr create --body "$(cat <<'EOF' ... gh pr merge ... EOF)"` heredoc body gives only the create; a `--body` that mentions `--draft` leaves `draft` false; a quoted `FOO="a b"` prefix; `git -C dir checkout -b x` gives `dir`; `cd d && git switch x` gives `moved: true`; `git branch -m`; `git push --dry-run` gives nothing; `gh pr edit --title "[BLOCKED] x"` gives `pr_retitle`); `case 1 fires with a fix for each missing piece`; `case 2 is armed by cases 1 and 3, fires once on the next push, then disarms, per session`; `case 3 wording follows agent_merge`; `disabled, not onboarded, unreadable or failing settings add nothing and never throw`; `settings is never read for a non-matching command`; `case 2 disarms on gh pr ready --undo and on a retitle to [BLOCKED]`; `missing sessionID skips cases 2 and 5 only`; `per-session state is bounded`; `a failed call adds nothing`.
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
- Produces: case 4 note: names the primary path and says to run `git fetch origin main` then `git merge --ff-only origin/main` there only if it is clean (ignoring `.vibe-workflow/settings.json`) and on `main`, else to report and change nothing; with `auto` it says to do this once the auto-merge completes; with `agent_merge` false it adds that the user chose to merge themselves. Case 5 note: stay silent when the classified action has `moved: true`; otherwise use `dir` (resolved against the event directory) or the event directory; when `checkout(directory)` shows `top` equal to the primary path and `branch` not `main` (`"HEAD"` means detached), it names the branch and says to do task work in a worktree; once per branch name per session.
- Produces: `registerPrAdvice(ctx, { settings, primary, checkout? }) -> Promise<boolean>`: returns `false` without registering when `typeof ctx.tool?.hook !== "function"` or registration throws; otherwise registers `ctx.tool.hook("execute.after", handler)` and returns `true`. The handler reads `tool = e.tool`, `command = e.input?.command ?? e.args?.command`, `directory = e.input?.workdir ?? e.input?.cwd ?? ctx.location.directory`, `sessionID = e.sessionID ?? e.session?.id`, `ok` per the Global Constraints rule (completed and no nonzero exit field), calls `advise`, and appends the note to `e.result` only when it is a string or an object with a string `output` or `content`, inside a `try` so a frozen event is left alone; any other shape is left alone. It never throws and does nothing after the plugin closes; the plugin's cleanup calls `advisor.clear()`. Default `checkout` runs `git rev-parse --show-toplevel --abbrev-ref HEAD` (2-second timeout, `GIT_*` removed; a deliberate improvement on the spec's `symbolic-ref`, one call giving both facts) and returns `{ top, branch }` with `top` realpathed, or `null`.
- `index.js`: second settings instance `createProjectSettings(directory, "pull_requests", { atProject: true })`; `primary = async () => (await instance.inspect()).project ?? null`; `registerPrAdvice` runs before `registerOnboarding`; `prGuard` becomes `() => guard ? { state: "ready", reason: null } : { state: "setup_needed", reason: "guard_unavailable" }`; the existing `context` hook adds one line (`vibe-workflow: open pull requests as [BLOCKED] drafts and follow the vibe-pull-requests skill.`) before its Smart Router early return, only when the hook was not registered and the `pull_requests` feature is enabled; when the hook is registered, that read is skipped.

- [ ] **Step 1: Write failing tests**: in `tests/pr-advice.test.js`, `case 4 names the primary path and the fast-forward commands`, `case 4 with auto and with agent_merge false`, `case 5 names the branch once, stays silent on main, another repository and failure`, `detached HEAD is reported`. In `tests/pr-hook.test.js` (real plugin setup with a fake `ctx.tool.hook` capturing the handler, a git fixture and a written settings file): `registers only when the host provides tool.hook`; `appends a note to a string result and to an output or content object`; `leaves failed calls, other tools, missing commands and unknown result shapes unchanged`; `never throws when settings fail`; `adds nothing when the feature is disabled`; `fallback context line appears with Smart Router off, pull requests on and no hook, and never when the hook is registered`; `a nonzero exit field or a failed status adds nothing and does not arm or disarm case 2`; `a frozen event is left unchanged without throwing`; `a missing sessionID keeps cases 1, 3 and 4 working`; `cleanup clears state and later events do nothing`; `onboarding readiness follows hook availability`.
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
