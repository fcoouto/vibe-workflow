# Session titles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prefix a root session's title with `[DESIGN]`, `[PLAN]` or `[BUILD]` when superpowers writes a design, writes an implementation plan, or starts executing a plan; opt-in per project.

**Architecture:** One new module, `session-titles.js`, holds two pure functions (`classify`, `retitle`) and `registerSessionTitles(ctx, { settings })`, which adds a second `execute.after` hook, reads the session with `ctx.session.get` and renames it with `ctx.session.update`. A third onboarding feature, `session_titles` (one boolean, like Smart Router), gates it. No storage, no model call, no polling.

**Tech Stack:** Node ES modules, `node --test`, Python `unittest` for packaging.

**Spec:** `docs/superpowers/specs/2026-11-20-session-titles-design.md`

## Global Constraints

- Title format is exactly `[DESIGN] <topic>`, `[PLAN] <topic>` or `[BUILD] <topic>`; stage regex `^\[(DESIGN|PLAN|BUILD)\] `.
- Only completed `write` calls of paths matching `(?:^|/)docs/superpowers/(specs|plans)/[^/]+\.md$` and completed `skill` calls with `input.id` of `subagent-driven-development` or `executing-plans` are events. `edit`, `patch`, shell writes and every other tool are ignored.
- Rename only root sessions (no `parentID`). Never rename when `settings().state !== "on"`.
- Topic is whatever follows an existing stage prefix; an empty or OpenCode-default title (`(?:New|Child) session - <ISO time>`) takes the file-name topic (date and trailing `-design` removed, dashes to spaces); with no topic, write nothing.
- If the plugin renamed a session earlier and its title now has no stage prefix, never rename it again (in-memory record, capped at 128 sessions like `notices` in `index.js`).
- Equal result means no `session.update` call. Every error is swallowed; the hook never throws, never edits `event.result`, never starts a model turn.
- Settings: `{"session_titles": {"enabled": <bool>}}`; not onboarded or off means no work. Smart Router and `git_workflow` results stay byte-identical; existing tests are not edited except to add the feature name where a list is asserted.
- A new exported file goes in `scripts/manifest.py`, `package.json` `files` and `tests/test_package.py`. Python 3.11+ standard library and Node ES modules only.
- README edits go through the `impeccable` skill and stay short. No install, activation, publish or inference during development.

## Review Focus

- `edit` of a plan during `[BUILD]` never moves the stage back (Task 1, Task 3).
- A child session (`parentID` set) and a default-titled session with a skill event are never renamed (Task 3).
- A user who removes or replaces the prefix is never overridden again; a user-edited topic after the prefix is kept (Task 3).
- Off, not onboarded, invalid settings and a host without `tool.hook` or `session.update` cost nothing and break nothing (Task 3).
- The `session_titles` onboarding path never runs the Smart Router setup check and leaves other features untouched (Task 2).

---

### Task 1: Pure classification and retitling

**Files:**
- Create: `session-titles.js`
- Test: `tests/session-titles.test.js`

**Interfaces:**
- Produces: `classify(event) -> { stage, slug? } | null` and `retitle(title, { stage, slug }) -> string | null`.

- [ ] **Step 1: Write failing tests** in `tests/session-titles.test.js`: `classify` returns `DESIGN` with slug `session titles` for `write` of `/x/docs/superpowers/specs/2026-11-20-session-titles-design.md`; `PLAN` with the same slug for `.../plans/2026-11-20-session-titles.md`; relative `docs/superpowers/plans/a.md`; `filePath` instead of `path`; Windows separators; `BUILD` for `skill` with each executor id; `null` for `edit`, other skills, `status: "error"`, `docs/superpowers/other/a.md`, `notes-docs/superpowers/specs/a.md` without the separator, missing or non-string input, and `undefined`. `retitle("Fix auth", {stage:"PLAN", slug:"x"})` is `[PLAN] Fix auth`; `retitle("[PLAN] Fix auth", {stage:"BUILD"})` is `[BUILD] Fix auth`; `retitle("New session - 2026-11-20T10:00:00.000Z", {stage:"DESIGN", slug:"session titles"})` is `[DESIGN] session titles`; the same default title with no slug, and `""` with no slug, give `null`; `retitle("[PLAN] x", {stage:"PLAN"})` equals its input.
- [ ] **Step 2: Run** `node --test tests/session-titles.test.js`. Expected: fail (module missing).
- [ ] **Step 3: Implement** in `session-titles.js`:

```js
const PREFIX = /^\[(?:DESIGN|PLAN|BUILD)\] /
const ARTIFACT = /(?:^|\/)docs\/superpowers\/(specs|plans)\/([^/]+)\.md$/
const EXECUTORS = new Set(["subagent-driven-development", "executing-plans"])
const DEFAULT_TITLE = /^(?:New|Child) session - \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/

export function classify(event) {
  if (event?.status !== "completed") return null
  const input = event.input ?? event.args ?? {}
  if (event.tool === "skill") return EXECUTORS.has(input.id) ? { stage: "BUILD" } : null
  if (event.tool !== "write") return null
  const path = input.path ?? input.filePath
  const match = typeof path === "string" ? ARTIFACT.exec(path.replaceAll("\\", "/")) : null
  if (!match) return null
  const slug = match[2].replace(/^\d{4}-\d{2}-\d{2}-/, "").replace(/-design$/, "").replaceAll("-", " ")
  return { stage: match[1] === "specs" ? "DESIGN" : "PLAN", slug }
}

export function retitle(title, { stage, slug }) {
  const bare = title.replace(PREFIX, "")
  const topic = bare && !DEFAULT_TITLE.test(bare) ? bare : slug
  return topic ? `[${stage}] ${topic}` : null
}
```

- [ ] **Step 4: Run** `node --test tests/session-titles.test.js`. Expected: pass. Commit.

### Task 2: The `session_titles` setting and onboarding

**Files:**
- Modify: `project.js`, `onboarding.js`, `skills/vibe-onboard-project/SKILL.md`, `skills/vibe-onboard-project/references/procedure.md`
- Test: `tests/project.test.js`, `tests/onboarding.test.js`

**Interfaces:**
- Produces: `FEATURES.session_titles = ["enabled"]`, so `createProjectSettings(dir, "session_titles")` behaves exactly like the Smart Router feature (boolean or `{ enabled }`, same preview/apply/snapshot rules, same file). The onboarding tool accepts `feature: "session_titles"`; its readiness is `{ state: "ready", reason: null }` when enabled and `{ state: "off", reason: null }` otherwise, with no setup check.

- [ ] **Step 1: Write failing tests**: `project.test.js` gets previews, apply, `unchanged`, and coexistence with the other two features' entries (unknown entries kept) for `session_titles`; `onboarding.test.js` gets preview and apply through the tool for the new feature, `agent_merge` or `worktree_cleanup` with it giving `invalid_request`, and a check that the Smart Router setup callback is not called.
- [ ] **Step 2: Run** `node --test tests/project.test.js tests/onboarding.test.js`. Expected: new tests fail.
- [ ] **Step 3: Implement** the feature in `FEATURES`, the `features` list and tool description in `onboarding.js`, and the `ready` branch (`prefs` stays `git_workflow` only; the new feature takes the non-prefs value path but a readiness that skips `checkSetup`). In the skill procedure add one question: header at most 30 characters, context starting `Project: <project path>.`, choices "Show stage in session name (Recommended)" and "Leave names alone", saying plainly that it renames the user's chat to `[DESIGN]`, `[PLAN]` or `[BUILD]` when a design or plan is written or a plan starts, and never touches a name the user changed.
- [ ] **Step 4: Run** `node --test tests/*.test.js` and `python3 -B scripts/validate.py`. Expected: pass. Commit.

### Task 3: Hook registration and wiring

**Files:**
- Modify: `session-titles.js`, `index.js`
- Test: `tests/session-titles.test.js` (hook section, fixture modeled on `tests/pr-hook.test.js`)

**Interfaces:**
- Consumes: Task 1 functions; `createProjectSettings(directory, "session_titles").inspect()`.
- Produces: `registerSessionTitles(ctx, { settings }) -> { registered, close }`, called from `index.js` after `registerPrAdvice`, `close` joined to the cleanup function.

- [ ] **Step 1: Write failing tests** with a fake `ctx` (`tool.hook` capturing handlers, `session.get` returning `{ id, title, parentID? }`, `session.update` recording calls): rename on spec write, plan write and `skill` events; `edit` after `[BUILD]` writes nothing; plan rewrite after `[BUILD]` gives `[PLAN]` (latest wins); child session skipped; default title plus skill event writes nothing; default title plus spec write uses the slug; user-edited topic after the prefix kept; after the plugin renamed a session and the title lost its prefix, the next event writes nothing; same title writes nothing; settings off, `not_onboarded` and invalid write nothing and call neither `session.get` nor `session.update`; `session.get` or `update` rejecting does not throw; host without `tool.hook` returns `registered: false`; after `close()` events do nothing; `event.result` is never changed; a second handler (pull request hook) still runs.
- [ ] **Step 2: Run** `node --test tests/session-titles.test.js`. Expected: hook tests fail.
- [ ] **Step 3: Implement**:

```js
export async function registerSessionTitles(ctx, { settings }) {
  const written = new Map()
  let closed = false
  const close = () => { closed = true; written.clear() }
  if (typeof ctx.tool?.hook !== "function") return { registered: false, close }
  try {
    await ctx.tool.hook("execute.after", async (event) => {
      try {
        const change = classify(event)
        const id = event.sessionID ?? event.session?.id
        if (closed || !change || typeof id !== "string" || (await settings()).state !== "on") return
        const session = await ctx.session.get({ sessionID: id })
        const title = typeof session?.title === "string" ? session.title : ""
        if (session?.parentID || (written.has(id) && !PREFIX.test(title))) return
        const next = retitle(title, change)
        if (!next || next === title) return
        await ctx.session.update({ sessionID: id, title: next })
        written.delete(id)
        written.set(id, next)
        if (written.size > 128) written.delete(written.keys().next().value)
      } catch {}
    })
  } catch { return { registered: false, close } }
  return { registered: true, close }
}
```

  In `index.js`: `const titles = await registerSessionTitles(ctx, { settings: () => createProjectSettings(directory, "session_titles").inspect() })` (create the settings object once, beside `gitWorkflow`) and call `titles.close()` in the cleanup.
- [ ] **Step 4: Run** `node --test tests/*.test.js`. Expected: pass; existing `plugin.test.js` and `pr-hook.test.js` unchanged and passing. Commit.

### Task 4: Packaging and docs

**Files:**
- Modify: `scripts/manifest.py`, `package.json`, `tests/test_package.py`, `docs/development.md`, `README.md` (through `impeccable`)

- [ ] **Step 1: Write the failing package test**: `session-titles.js` in the expected export set in `tests/test_package.py`.
- [ ] **Step 2: Add** `session-titles.js` to `scripts/manifest.py` and `package.json` `files`. In `docs/development.md` add a "Session titles: what is known" section listing the traces, the unverified host facts from the spec and the live-check procedure of Task 5. In `README.md` add two or three lines under "Onboard a project": the new choice and the three labels.
- [ ] **Step 3: Run** `python3 -B -m unittest discover -s tests`, `node --test tests/*.test.js`, `python3 -B scripts/validate.py`, `python3 -B scripts/package.py --output <new dir>` and `python3 -B scripts/validate.py --root <new dir>`. Expected: all pass. Commit.

### Task 5: Observation on the installed host (user-authorized, separate report)

Deterministic tests do not establish native behavior. With the user's authorization to run one real session on the installed OpenCode, record in `docs/development.md` what was observed, and report it separately from Tasks 1 to 4:

- [ ] The `execute.after` event for a `skill` load: tool name and `input.id` field.
- [ ] The `write` input field (`path` or `filePath`) and whether it is absolute or relative.
- [ ] `ctx.session.get` returns `title` and `parentID` (compare with `opencode api get /openapi.json`).
- [ ] A plugin-set title survives OpenCode's own title generation and the next turns.
- [ ] The desktop and terminal session lists show the new name without a reload.
- [ ] A subagent's skill load or file write does not rename the coordinator.

If a fact differs, fix `classify` or the registration with a failing test first, then repeat the check. State any item not observed as unverified.
