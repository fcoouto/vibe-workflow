# Session Titles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a task's stage in its root session's title, `<U+FFFB><5 cells> [WAITING · ]<topic>`, bound by a spec write and advanced by git and GitHub facts read at the end of each turn; opt-in per project.

**Architecture:** `session-facts.js` reads facts (spec/plan paths, the plan's declared files, git status and diff, the branch's PR through `gh`) through an injected runner. `session-titles.js` holds the pure title and cell rules plus `registerSessionTitles`, which binds on `execute.after` of `write` and writes titles only on `session.execution.*` events. A `session_titles` onboarding feature gates it.

**Tech Stack:** Node ES modules, `node --test`, real `git` in temporary repositories for fact tests, Python `unittest` for packaging.

**Spec:** `docs/superpowers/specs/2026-11-20-session-titles-design.md`

## Global Constraints

- Title: `"￻" + "▰".repeat(n) + "▱".repeat(5 - n) + " " + ("WAITING · " if waiting) + topic`. Prefix pattern when reading: `/^￻?[▰▱]{5} (?:WAITING · )?/`. No other label text exists; never `BLOCKED`.
- Cells: 1 bound; 2 a changed path outside `docs/`; 3 at least `Math.ceil(declared / 2)` declared paths changed; 4 all declared paths changed (needs at least one declared path) or any PR exists for the branch; 5 PR `MERGED`, or `CLOSED` with a title starting `[SUPERSEDED]`, and cleanup satisfied. The highest holding fact wins; within a task the cells never decrease.
- Cleanup is satisfied when `worktree_cleanup` is not `true`, or `top` is the main checkout, or `top` does not exist, or the PR was superseded.
- Bind only on a completed `write` in a root session (no `parentID`) of a path matching `/(?:^|\/)docs\/superpowers\/(specs|plans)\/[^/]+\.md$/`, resolved against the session's `location.directory`, `top` realpath'd once at bind. A different spec path rebinds at cell 1; the same spec path changes nothing; a plan path under the bound `top` sets the plan, binds if unbound, and is ignored under another `top`.
- Titles are written only on `session.execution.started` (remove the label) and `session.execution.succeeded|failed|interrupted` (meter, and the label while not done). A spec write never renames.
- PR head: the current branch when it is not the default branch and has an upstream; otherwise, when the checkout is detached or gone, the remembered branch. Remember only branches seen with an upstream. No head means no `gh` call. `gh pr list --head <head> --state all --json state,title --limit 1`.
- Subprocesses: git 2 s timeout, `gh` 5 s, process env without `GIT_*` variables plus `LC_ALL=C` (as in `project.js`); any failure keeps the previous cells.
- Memory only: sessions capped at 128, oldest dropped. No storage, polling, model call or new dependency.
- Settings: `{"session_titles":{"enabled":true}}` at the main project path (`atProject: true`). Not onboarded, off or invalid: no reads, no writes.
- The plugin never edits `event.result`, never throws from a hook or event loop, and never starts a model turn.
- New exported files go in `scripts/manifest.py`, `package.json` `files`, `tests/test_package.py` and the copy list in `tests/plugin.test.js`. README only through the `impeccable` skill. No install, activation or publish during Tasks 1-5.

## Review Focus

- An unrelated branch's PR merging while the task runs must not move the meter, and a checkout on the default branch never queries `gh` (Tasks 1 and 4).
- A detached checkout that later gets a pushed branch, then is detached or removed by cleanup, still finds its PR through the remembered branch (Tasks 1 and 4).
- A turn start that arrives while the turn-end evaluation is still reading git must end with no `WAITING` (Task 4: per-session queue).
- A spec written outside a git repository, or a repository with no `origin`, stays at cell 1 with a title and never throws (Tasks 1 and 4).
- A user who removes the prefix, or edits the topic, is never overridden (Task 4).

---

### Task 1: Facts reader

**Files:**
- Create: `session-facts.js`
- Test: `tests/session-facts.test.js`

**Interfaces:**
- Produces: `artifact(path: string) -> { kind: "spec" | "plan", top: string, slug: string } | null` (absolute `path` in; `top` is the part before `/docs/superpowers/`; `slug` is the file name without `.md`, a leading `YYYY-MM-DD-` and a trailing `-design`, dashes to spaces).
- Produces: `declaredPaths(planText: string) -> string[]`.
- Produces: `readFacts({ top, plan, remembered, fallback, run }) -> Promise<{ exists, branch, upstream, base, head, changed: string[], declared: string[], pr: { state, title } | null }>`. `remembered` is the last branch seen with an upstream (or null); `fallback` is the directory `gh` runs from when `top` is gone; `head` is the branch queried (or null).
- Produces: `defaultRun(command, args, { cwd, timeout }) -> Promise<string>` (stdout; rejects on failure or timeout), used by Task 4.

- [ ] **Step 1: Write failing tests.**

```js
test("artifact recognizes specs and plans and derives top and slug", () => {
  assert.deepEqual(artifact("/w/a/docs/superpowers/specs/2026-11-20-session-titles-design.md"),
    { kind: "spec", top: "/w/a", slug: "session titles" })
  assert.equal(artifact("/w/a/docs/superpowers/plans/2026-11-20-x.md").kind, "plan")
  for (const path of ["/w/notes-docs/superpowers/specs/a.md", "/w/docs/superpowers/other/a.md",
    "/w/docs/superpowers/specs/a/b.md", "/w/docs/superpowers/specs/a.txt"]) assert.equal(artifact(path), null)
})
test("declaredPaths reads path-like backticked tokens inside Files blocks only", () => {
  const plan = "Intro `not/this.js`\n\n**Files:**\n- Create: `a.js`\n- Modify: `b/c.js:10-20` (`FEATURES`), through `impeccable`\n- Test: `tests/a.test.js`\n\nText `x.js`\n\n**Files:**\n- Modify: `a.js`, `README.md`\n"
  assert.deepEqual(declaredPaths(plan), ["a.js", "b/c.js", "tests/a.test.js", "README.md"])
  assert.deepEqual(declaredPaths("no files here"), [])
})
```

  `readFacts` tests use a real temporary repository with a bare `origin`, a detached worktree, and a runner that runs real `git` but answers `gh` from a table keyed by `--head` and records each call:
  - detached, nothing pushed: `branch: null`, `upstream: false`, `head: null`, no `gh` call;
  - a committed `docs/` file, a committed product file, an uncommitted edit and an untracked file all appear in `changed`;
  - after `git switch -c feat/x` without a push: `head: null`, no `gh` call; after `git push -u`: `branch: "feat/x"`, `upstream: true`, `head: "feat/x"`, `pr` from the table;
  - the checkout on `main` with an upstream: `head: null`, no `gh` call;
  - detached again with `remembered: "feat/x"`: `head: "feat/x"`; `top` removed with `remembered: "feat/x"`: `exists: false`, `gh` runs from `fallback`; `top` removed with `remembered: null`: no `gh` call;
  - `declared` comes from the plan file under `top`;
  - a repository without `origin` (diff fails) and a non-repository directory resolve with `changed: []`, `pr: null`, no rejection.
- [ ] **Step 2: Run** `node --test tests/session-facts.test.js`. Expected: fails, module missing.
- [ ] **Step 3: Implement** `session-facts.js`. `declaredPaths` keeps backticked tokens containing `.` or `/` on the lines of each `**Files:**` block (until a blank line), strips `:line` suffixes, dedupes in order. `base` is `git rev-parse --abbrev-ref origin/HEAD`, falling back to `origin/main`. `changed` is `git diff --name-only --merge-base <base>` plus the `? ` lines of `git status --porcelain=v2 --branch -uall`; a failed diff gives `changed: []`. The status headers `# branch.head` and `# branch.upstream` give `branch` (null when `(detached)`) and `upstream`. `head` follows the global PR head rule, with the default branch being `base` without `origin/`.
- [ ] **Step 4: Run** `node --test tests/session-facts.test.js`. Expected: pass. Commit.

### Task 2: Title and cell rules

**Files:**
- Create: `session-titles.js`
- Test: `tests/session-titles.test.js`

**Interfaces:**
- Produces: `MARK = "￻"`, `PREFIX` (the reading pattern), `strip(title) -> string`, `compose(topic, cells, waiting) -> string`, `cellsFor({ changed, declared, pr, exists, cleanupOn, primary }) -> 1..5`, `DEFAULT_TITLE = /^New session - \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/`.

- [ ] **Step 1: Write failing tests.**

```js
test("compose and strip", () => {
  assert.equal(compose("Fix auth", 2, true), "￻▰▰▱▱▱ WAITING · Fix auth")
  assert.equal(compose("Fix auth", 5, false), "￻▰▰▰▰▰ Fix auth")
  assert.equal(strip("￻▰▰▱▱▱ WAITING · Fix auth"), "Fix auth")
  assert.equal(strip("▰▱▱▱▱ Fix auth"), "Fix auth")
  assert.equal(strip("Fix auth"), "Fix auth")
})
test("cellsFor takes the highest fact that holds", () => {
  const base = { changed: [], declared: ["a.js", "b.js", "c.js"], pr: null, exists: true, cleanupOn: false, primary: false }
  assert.equal(cellsFor(base), 1)
  assert.equal(cellsFor({ ...base, changed: ["docs/x.md"] }), 1)
  assert.equal(cellsFor({ ...base, changed: ["z.js"] }), 2)
  assert.equal(cellsFor({ ...base, changed: ["a.js", "b.js"] }), 3)
  assert.equal(cellsFor({ ...base, changed: ["a.js", "b.js", "c.js"] }), 4)
  assert.equal(cellsFor({ ...base, declared: [], changed: ["z.js"] }), 2)
  assert.equal(cellsFor({ ...base, pr: { state: "OPEN", title: "[BLOCKED] x" } }), 4)
  assert.equal(cellsFor({ ...base, pr: { state: "MERGED", title: "x" } }), 5)
  assert.equal(cellsFor({ ...base, pr: { state: "MERGED", title: "x" }, cleanupOn: true }), 4)
  assert.equal(cellsFor({ ...base, pr: { state: "MERGED", title: "x" }, cleanupOn: true, exists: false }), 5)
  assert.equal(cellsFor({ ...base, pr: { state: "MERGED", title: "x" }, cleanupOn: true, primary: true }), 5)
  assert.equal(cellsFor({ ...base, pr: { state: "CLOSED", title: "[SUPERSEDED] x" }, cleanupOn: true }), 5)
  assert.equal(cellsFor({ ...base, pr: { state: "CLOSED", title: "x" } }), 4)
})
```

- [ ] **Step 2: Run** `node --test tests/session-titles.test.js`. Expected: fails.
- [ ] **Step 3: Implement** the exports in `session-titles.js`.
- [ ] **Step 4: Run** the test. Expected: pass. Commit.

### Task 3: The `session_titles` setting and onboarding question

**Files:**
- Modify: `project.js` (`FEATURES`), `onboarding.js`, `index.js`, `skills/vibe-onboard-project/references/procedure.md`
- Test: `tests/project.test.js`, `tests/onboarding.test.js`

**Interfaces:**
- Produces: `FEATURES.session_titles = ["enabled"]`; `createProjectSettings(dir, "session_titles", { atProject: true })` behaves as the Smart Router feature (boolean or `{ enabled }`) at the main project path.
- Produces: `registerOnboarding(ctx, projects, checkSetup, prGuard, titlesGuard)`; the tool's `feature` enum gains `"session_titles"`; its readiness is `{ state: "off", reason: null }` when disabled, otherwise `titlesGuard()`. `prefs` stays `git_workflow` only.
- Produces in `index.js`: `const sessionTitles = createProjectSettings(directory, "session_titles", { atProject: true })`, added to the onboarding `projects` map, and a temporary `titlesGuard = () => ({ state: "setup_needed", reason: "events_unavailable" })` that Task 4 replaces.

- [ ] **Step 1: Write failing tests.** `project.test.js`: preview, apply and `unchanged` for `session_titles`; the entry coexisting with `smart_router` and `git_workflow` entries (unknown entries kept); a worktree copy never turning it on at the project path. `onboarding.test.js`: preview and apply through the tool for `session_titles`; `agent_merge` with it gives `invalid_request`; readiness comes from `titlesGuard` and `checkSetup` is not called; the question-form test expects five questions, the fifth with `- Question: Show task progress in the session title?`; the flow test expects `Up to five short questions.` and no longer `Up to four`, plus the phrase `Session titles: not saved.`
- [ ] **Step 2: Run** `node --test tests/project.test.js tests/onboarding.test.js`. Expected: new and changed tests fail.
- [ ] **Step 3: Implement.** Add question 5 to the procedure in the existing question style, asked also when git workflow guidance is skipped, with choices "Show progress (Recommended)" and "Keep titles as they are"; its context says the title gets a five-step meter and WAITING while the session needs you, that only the meter part is changed, and that removing it stops updates for that chat. Draft the copy through the `impeccable` skill. Add the steps: preview `session_titles` after git workflow; on a blocked preview skip question 5 and report `Session titles: not saved.` with the reason; apply it last with a fresh preview's `snapshot`; add its line to the Saving summary; readiness lines `Session titles: Off — titles are left alone.`, `Session titles: On — updated at the end of each turn.`, `Session titles: On · Setup needed — this OpenCode does not deliver session events.`
- [ ] **Step 4: Run** `node --test tests/*.test.js` and `python3 -B scripts/validate.py`. Expected: pass. Commit.

### Task 4: Registration, wiring and packaging

**Files:**
- Modify: `session-titles.js`, `index.js`, `scripts/manifest.py`, `package.json`, `tests/test_package.py`, `tests/plugin.test.js` (copy list only)
- Test: `tests/session-titles.test.js` (registration section)

**Interfaces:**
- Consumes: Task 1 `artifact`, `readFacts`, `defaultRun`; Task 2 rules; Task 3 settings objects.
- Consumes, host shapes verified on OpenCode 2.3.2: `ctx.tool.hook("execute.after", handler)` with `event.tool === "write"`, `event.status === "completed"`, `event.sessionID`, `event.input.path` (relative to the session directory); `ctx.event.subscribe({ types, signal })` returning an async iterable of `{ type, data: { sessionID } }`; `ctx.session.get({ sessionID })` returning `{ title, parentID?, location: { directory } }`; `ctx.session.update({ sessionID, title })`.
- Produces: `registerSessionTitles(ctx, { settings, gitWorkflow, run = defaultRun }) -> Promise<{ registered: boolean, close() }>`. `settings` and `gitWorkflow` are `createProjectSettings(...)` objects (`inspect()` gives `state`, and for git workflow `project` and `worktree_cleanup`). Registers nothing and returns `registered: false` unless both `ctx.tool.hook` and `ctx.event.subscribe` are functions.

- [ ] **Step 1: Write failing tests** with a fake `ctx` (as above; `event.subscribe` returns a pushable async iterable that honors `signal`) and a fake `run`:
  - a spec `write` (relative path) binds and renames nothing; the next `session.execution.succeeded` writes `￻▰▱▱▱▱ WAITING · <current title>`; `session.execution.started` writes it without `WAITING`;
  - a `New session - …` or empty title takes the spec slug as topic; a later different spec, with the title unchanged since our write, switches the topic to its slug and resets to cell 1; with the title edited since, the edited topic is kept; the same spec written again keeps cells and topic;
  - facts drive cells 2, 3, 4 and 5; at cell 5 the title has no label and later events change nothing until a new spec write; cells never decrease when facts regress;
  - a plan `write` under the same `top` sets the declared paths; a plan `write` with no binding binds; a plan `write` under another `top` is ignored;
  - a symlinked session directory still reaches cell 5 through `primary` (top is realpath'd);
  - child sessions (`parentID`), events without `data.sessionID`, unbound sessions write nothing; `failed` and `interrupted` of bound sessions evaluate like `succeeded`;
  - a title without our prefix after we wrote one stops all writes for that session; an unchanged result writes nothing;
  - settings off, `not_onboarded` or invalid: no `session.get`, no `run`, no `update`;
  - `run`, `session.get` or `update` rejecting changes no cells and throws nothing; the next turn end repairs;
  - a `started` event pushed while a slow `succeeded` evaluation runs leaves the final title without `WAITING`;
  - another branch's merge in the `gh` fake never advances the meter;
  - the 129th session evicts the oldest;
  - `close()` aborts the subscription; a host without `ctx.event.subscribe` gets `registered: false` and no `execute.after` registration;
  - the `event.result` object is never modified.

  Add `session-facts.js` and `session-titles.js` to the expected export set in `tests/test_package.py`.
- [ ] **Step 2: Run** `node --test tests/session-titles.test.js`. Expected: registration tests fail.
- [ ] **Step 3: Implement.** State is `Map<sessionID, { task: { top, spec, plan, remembered, cells, slug, fresh } | null, last: string | null, silenced: boolean }>`, capped at 128. Each session's work runs on its own promise chain, so events apply in arrival order. At each evaluation: `fallback` and `primary` come from `gitWorkflow.inspect().project` (`primary` is `top === project`), `cleanupOn` is `worktree_cleanup === true`; remember `facts.head` when it is the current branch with an upstream. In `index.js`, call `registerSessionTitles` after `registerPrAdvice`, replace the Task 3 guard with `() => titles.registered ? { state: "ready", reason: null } : { state: "setup_needed", reason: "events_unavailable" }`, and call `titles.close()` in the cleanup. Add both new files to `scripts/manifest.py` `FILES`, `package.json` `files` and the copy list in `tests/plugin.test.js`.
- [ ] **Step 4: Run** `node --test tests/*.test.js`, `python3 -B -m unittest discover -s tests` and `python3 -B scripts/validate.py`. Expected: pass; `pr-hook.test.js` unchanged (its host lacks `ctx.event`, so nothing registers there). Commit.

### Task 5: Docs and export check

**Files:**
- Modify: `docs/development.md`, `README.md` (through `impeccable`)

- [ ] **Step 1: Add** to `docs/development.md` a short "Session titles" section that points to the spec's Evidence and Known gaps sections and holds the Task 6 results. In `README.md` add two lines under "Onboard a project" naming the new choice.
- [ ] **Step 2: Run** `python3 -B scripts/package.py --output <new dir>` and `python3 -B scripts/validate.py --root <new dir>`. Expected: pass. Commit.

### Task 6: Live check (needs the user's authorization)

Deterministic tests do not show native behavior. With the user's go-ahead, load the packaged plugin into a throwaway project (as the design's probe did), run one real first-turn spec write and one task through to a merged PR on a scratch branch or repository the user names, and record in `docs/development.md`:

- [ ] OpenCode's own title generation does not replace the first title written at the end of the first turn (spec gap).
- [ ] Cells 1 to 4 and `WAITING` appear at the expected turn ends; cell 5 after the merge (and cleanup, when on).
- [ ] A background child holding the root's turn shows `WAITING` only until its report wakes the root.

Report any item not observed as unverified.
