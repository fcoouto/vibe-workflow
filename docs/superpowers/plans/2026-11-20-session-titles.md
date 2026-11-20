# Session titles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prefix a root session's title with a three-step meter (`▰▱▱▱▱`, `▰▰▱▱▱`, `▰▰▰▱▱`), plus `WAITING · ` while the user owes an approval, when superpowers writes a design, writes an implementation plan, or starts executing a plan; opt-in per project.

**Architecture:** One new module, `session-titles.js`, holds two pure functions (`classify`, `retitle`) and `registerSessionTitles(ctx, { settings })`, which adds a second `execute.after` hook, reads the session with `ctx.session.get` and renames it with `ctx.session.update`. A third onboarding feature, `session_titles` (one boolean, like Smart Router), gates it. No storage, no model call, no polling.

**Tech Stack:** Node ES modules, `node --test`, Python `unittest` for packaging.

**Spec:** `docs/superpowers/specs/2026-11-20-session-titles-design.md`

## Global Constraints

- Title format: five cells, a space, an optional label, the topic: `▰▱▱▱▱ WAITING · <topic>` after a design, `▰▰▱▱▱ WAITING · <topic>` after a plan, `▰▰▰▱▱ <topic>` once a plan executes. Text appears **only** as `WAITING · ` (or `BLOCKED · `, see Open point in the spec); otherwise the meter alone. Prefix regex `^[▰▱]{5} (?:(?:WAITING|BLOCKED) · )?`; a user title that already starts with a meter is re-prefixed, never nested. No `U+FFFB` (that was a Codex font workaround). The stage is the count of `▰`; cells 4 and 5 stay unused (reserved for review and merge, not built).
- Only completed `write` calls of paths matching `(?:^|/)docs/superpowers/(specs|plans)/[^/]+\.md$` and completed `skill` calls whose `input.id`, after its last `:` or `/`, is `subagent-driven-development` or `executing-plans` are events (so `superpowers:executing-plans` counts). `edit`, `patch`, shell writes and every other tool are ignored.
- Rename only root sessions (no `parentID`). A plan (2 cells) never replaces a 3-cell meter (a plan rewrite mid-build is a fix); a design (1 cell) always replaces it (new design, new cycle). Never rename when `settings().state !== "on"`.
- Topic is whatever follows an existing stage prefix; an empty or OpenCode-default title (`(?:New|Child) session - <ISO time>`) takes the file-name topic (date and trailing `-design` removed, dashes to spaces); with no topic, write nothing.
- If the plugin renamed a session earlier and its title now has no stage prefix, never rename it again (in-memory `Set`, capped at 128 sessions like `notices` in `index.js`; lost on server restart, so a removed prefix can return once after a restart, and the onboarding text says so).
- Equal result means no `session.update` call. `session.get` and `session.update` are raced against a 2 s timeout so naming never holds up a tool result; the read-then-write gap is not atomic and a rename inside it can be overwritten (accepted, documented). Every error is swallowed; the hook never throws, never edits `event.result`, never starts a model turn.
- Settings file shape: `{"version":1,"features":[{"session_titles":{"enabled":<bool>}}]}`, read at the main project path (`atProject: true`, like `git_workflow`) so a linked worktree needs no copy of the file; not onboarded or off means no work. Smart Router and `git_workflow` results stay byte-identical; existing tests are not edited except to add the feature name where a list is asserted.
- A new exported file goes in `scripts/manifest.py`, `package.json` `files` and `tests/test_package.py`. Python 3.11+ standard library and Node ES modules only.
- README edits go through the `impeccable` skill and stay short. No install, activation, publish or inference during development.

## Review Focus

- `edit` of a plan while building, and a plan rewrite while building, never move the meter back (Task 1, Task 3).
- Registering a second `execute.after` hook must not break the pull request hook: its test fixture keeps one handler per hook name, so Task 3 changes that fixture to keep a list and fire all of them; Task 0 confirms the host runs both.
- A child session (`parentID` set) and a default-titled session with a skill event are never renamed (Task 3).
- A user who removes or replaces the prefix is never overridden again; a user-edited topic after the prefix is kept (Task 3).
- Off, not onboarded, invalid settings and a host without `tool.hook` or `session.update` cost nothing and break nothing (Task 3).
- The `session_titles` onboarding path never runs the Smart Router setup check, reports `setup_needed` when the hook did not register, and leaves other features untouched; `index.js` supplies its project entry (Task 2).

---

### Task 0: Observe the host first (throwaway, user-authorized)

Unverified host facts decide the shape of Tasks 1 to 3, so observe them before building. With the user's authorization for one real session, load a throwaway logging plugin (not committed) that writes each `execute.after` event's `tool`, `status`, `sessionID` and the keys of `input` to a private file, and the `ctx.session.get` result keys for the session. Record the findings in `docs/development.md` and report them separately from the deterministic tests:

- [ ] `skill` load: event tool name, `input.id` form (bare or namespaced). Also a skill the user invokes directly: does it produce a tool call at all? If not, the 3-cell meter is missed in that case; accept and document.
- [ ] File writes: tool name and path field (`path`, `filePath`; absolute or relative). Whether the host gives some models `apply_patch` instead of `write`; if so, also accept an added `*** Add File:` path.
- [ ] `ctx.session.get` returns `title` and `parentID`; the real default-title format (compare with `opencode api get /openapi.json`).
- [ ] Two `execute.after` registrations from one plugin both fire, in order.
- [ ] Calling `session.update` inside `execute.after` works, and whether it reorders the session list by updated time.
- [ ] A plugin-set title survives OpenCode's own title generation and later turns; desktop and terminal lists show it without a reload.
- [ ] A subagent's skill load or file write carries the child's `sessionID`, not the coordinator's.

If a fact differs from the spec, amend the spec and this plan first.

### Task 1: Pure classification and retitling

**Files:**
- Create: `session-titles.js`
- Test: `tests/session-titles.test.js`

**Interfaces:**
- Produces: `classify(event) -> { stage, slug? } | null` and `retitle(title, { stage, slug }) -> string | null`.

- [ ] **Step 1: Write failing tests** in `tests/session-titles.test.js`: `classify` returns `{stage: "DESIGN"}` with slug `session titles` for `write` of `/x/docs/superpowers/specs/2026-11-20-session-titles-design.md`; `PLAN` with the same slug for `.../plans/2026-11-20-session-titles.md`; relative `docs/superpowers/plans/a.md`; `filePath` instead of `path`; Windows separators; `BUILD` for `skill` with each executor id, bare and namespaced (`superpowers:executing-plans`); `null` for `edit`, other skills, `status: "error"`, `docs/superpowers/other/a.md`, `notes-docs/superpowers/specs/a.md` without the separator, missing or non-string input, and `undefined`. `retitle("Fix auth", {stage:"PLAN", slug:"x"})` is `▰▰▱▱▱ WAITING · Fix auth`; `retitle("▰▰▱▱▱ WAITING · Fix auth", {stage:"BUILD"})` is `▰▰▰▱▱ Fix auth` (label gone); a `DESIGN` result is `▰▱▱▱▱ WAITING · ...`; `retitle("New session - 2026-11-20T10:00:00.000Z", {stage:"DESIGN", slug:"session titles"})` is `▰▱▱▱▱ WAITING · session titles`; the same default title with no slug, and `""` with no slug, give `null`; `retitle("▰▰▱▱▱ WAITING · x", {stage:"PLAN"})` equals its input; a title that is only a meter with no topic and a slug gives the slug as topic.
- [ ] **Step 2: Run** `node --test tests/session-titles.test.js`. Expected: fail (module missing).
- [ ] **Step 3: Implement** in `session-titles.js`:

```js
const PREFIX = /^[▰▱]{5} (?:(?:WAITING|BLOCKED) · )?/
const STAGES = { DESIGN: [1, true], PLAN: [2, true], BUILD: [3, false] } // [filled cells, user owes approval]
const ARTIFACT = /(?:^|\/)docs\/superpowers\/(specs|plans)\/([^/]+)\.md$/
const EXECUTORS = new Set(["subagent-driven-development", "executing-plans"])
const DEFAULT_TITLE = /^(?:New|Child) session - \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/

export function classify(event) {
  if (event?.status !== "completed") return null
  const input = event.input ?? event.args ?? {}
  if (event.tool === "skill") return typeof input.id === "string" && EXECUTORS.has(input.id.split(/[:/]/).pop()) ? { stage: "BUILD" } : null
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
  if (!topic) return null
  const [cells, waiting] = STAGES[stage]
  return `${"▰".repeat(cells)}${"▱".repeat(5 - cells)} ${waiting ? "WAITING · " : ""}${topic}`
}
```

- [ ] **Step 4: Run** `node --test tests/session-titles.test.js`. Expected: pass. Commit.

### Task 2: The `session_titles` setting and onboarding

**Files:**
- Modify: `project.js`, `onboarding.js`, `index.js`, `skills/vibe-onboard-project/SKILL.md`, `skills/vibe-onboard-project/references/procedure.md`
- Test: `tests/project.test.js`, `tests/onboarding.test.js`, `tests/plugin.test.js`

**Interfaces:**
- Produces: `FEATURES.session_titles = ["enabled"]`, so `createProjectSettings(dir, "session_titles", { atProject: true })` behaves like the Smart Router feature (boolean or `{ enabled }`, same preview/apply/snapshot rules, same file) but at the main project path. `registerOnboarding` gains a fourth callback `titlesGuard` (after `prGuard`) and a readiness choice per feature: Smart Router keeps `checkSetup`, `git_workflow` keeps `prGuard`, `session_titles` returns `{ state: "ready", reason: null }` when on and the hook registered, `{ state: "setup_needed", reason: "hook_unavailable" }` when on and it did not, `{ state: "off", reason: null }` when off. `index.js` adds the `session_titles` settings object to the `projects` map it passes.

- [ ] **Step 1: Write failing tests**: `project.test.js` gets previews, apply, `unchanged`, and coexistence with the other two features' entries (unknown entries kept) for `session_titles`; `onboarding.test.js` gets preview and apply through the tool for the new feature, each readiness case above, `agent_merge` or `worktree_cleanup` with it giving `invalid_request`, and a check that the Smart Router setup callback is not called; `plugin.test.js` gets a case through the real plugin tool (preview and apply for `session_titles` without a throw).
- [ ] **Step 2: Run** `node --test tests/project.test.js tests/onboarding.test.js`. Expected: new tests fail.
- [ ] **Step 3: Implement** the feature in `FEATURES`, the `features` list and tool description in `onboarding.js`, and the `ready` branch (`prefs` stays `git_workflow` only; the new feature takes the non-prefs value path but a readiness that skips `checkSetup`). In the skill procedure add one question: header at most 30 characters, context starting `Project: <project path>.`, choices "Show progress in session name (Recommended)" and "Leave names alone", saying plainly that it renames the user's chat with a small progress meter (and `WAITING` while it needs the user's approval) when a design or plan is written or a plan starts, keeps the words the user put after the prefix, and stops for a chat whose prefix the user removed (until OpenCode restarts). Edit every procedure part that counts or orders features, not only the new question: the "up to four short questions" line, the apply order, the readiness lines, the saved-where line (the file lives at the main project path) and the saving summary; extend `SKILL.md`'s description if it names the features.
- [ ] **Step 4: Run** `node --test tests/*.test.js` and `python3 -B scripts/validate.py`. Expected: pass. Commit.

### Task 3: Hook registration and wiring

**Files:**
- Modify: `session-titles.js`, `index.js`
- Test: `tests/session-titles.test.js` (hook section, fixture modeled on `tests/pr-hook.test.js`), `tests/pr-hook.test.js` (fixture only)

**Interfaces:**
- Consumes: Task 1 functions; `createProjectSettings(directory, "session_titles").inspect()`.
- Produces: `registerSessionTitles(ctx, { settings }) -> { registered, close }`, called from `index.js` after `registerPrAdvice`, `close` joined to the cleanup function.

- [ ] **Step 1: Write failing tests** with a fake `ctx` (`tool.hook` capturing handlers, `session.get` returning `{ id, title, parentID? }`, `session.update` recording calls): rename on spec write, plan write and `skill` events; `edit` while building writes nothing; a plan rewrite while building stays `▰▰▰▱▱` while a new spec write gives `▰▱▱▱▱ WAITING · ...`; the label clears when the next event arrives; child session skipped; default title plus skill event writes nothing; default title plus spec write uses the slug; user-edited topic after the prefix kept; after the plugin renamed a session and the title lost its prefix, the next event writes nothing; same title writes nothing; settings off, `not_onboarded` and invalid write nothing and call neither `session.get` nor `session.update`; `session.get` or `update` rejecting does not throw; host without `tool.hook` returns `registered: false`; after `close()` events do nothing; `event.result` is never changed; a slow `session.get` or `update` (never resolves) is abandoned after the timeout; with both registered, the pull request hook and the title hook each still run.
- [ ] **Step 2: Run** `node --test tests/session-titles.test.js`. Expected: hook tests fail.
- [ ] **Step 3: Implement**:

```js
const within = (promise, ms = 2000) => Promise.race([promise, new Promise((_, no) => setTimeout(no, ms).unref?.())])

export async function registerSessionTitles(ctx, { settings }) {
  const written = new Set()
  let closed = false
  const close = () => { closed = true; written.clear() }
  if (typeof ctx.tool?.hook !== "function") return { registered: false, close }
  try {
    await ctx.tool.hook("execute.after", async (event) => {
      try {
        const change = classify(event)
        const id = event.sessionID ?? event.session?.id
        if (closed || !change || typeof id !== "string" || (await settings()).state !== "on") return
        const session = await within(ctx.session.get({ sessionID: id }))
        const title = typeof session?.title === "string" ? session.title : ""
        if (session?.parentID || (written.has(id) && !PREFIX.test(title))) return
        if (change.stage === "PLAN" && (title.match(/^▰*/)[0].length >= 3)) return
        const next = retitle(title, change)
        if (!next || next === title) return
        await within(ctx.session.update({ sessionID: id, title: next }))
        written.delete(id)
        written.add(id)
        if (written.size > 128) written.delete(written.keys().next().value)
      } catch {}
    })
  } catch { return { registered: false, close } }
  return { registered: true, close }
}
```

  In `index.js`: `const titles = await registerSessionTitles(ctx, { settings: () => createProjectSettings(directory, "session_titles").inspect() })` (create the settings object once with `{ atProject: true }`, beside `gitWorkflow`, and share it with the onboarding `projects` map; pass `titlesGuard` as `() => titles.registered ? ready : setup_needed`) and call `titles.close()` in the cleanup.
- [ ] **Step 4: Run** `node --test tests/*.test.js`. Expected: pass; existing `plugin.test.js` and `pr-hook.test.js` assertions unchanged and passing; the `pr-hook.test.js` fixture change is only that it keeps a list of handlers per hook name and `fire` runs all of them. Commit.

### Task 4: Packaging and docs

**Files:**
- Modify: `scripts/manifest.py`, `package.json`, `tests/test_package.py`, `docs/development.md`, `README.md` (through `impeccable`)

- [ ] **Step 1: Write the failing package test**: `session-titles.js` in the expected export set in `tests/test_package.py`.
- [ ] **Step 2: Add** `session-titles.js` to `scripts/manifest.py` and `package.json` `files`. In `docs/development.md` add a "Session titles: what is known" section listing the traces, the unverified host facts from the spec and the live-check procedure of Task 0. In `README.md` add two or three lines under "Onboard a project": the new choice and the three labels.
- [ ] **Step 3: Run** `python3 -B -m unittest discover -s tests`, `node --test tests/*.test.js`, `python3 -B scripts/validate.py`, `python3 -B scripts/package.py --output <new dir>` and `python3 -B scripts/validate.py --root <new dir>`. Expected: all pass. Commit.
