import assert from "node:assert/strict"
import { mkdirSync, mkdtempSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import test from "node:test"
import { DEFAULT_TITLE, cellsFor, compose, registerSessionTitles, strip } from "../../src/session-titles.js"

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

test("DEFAULT_TITLE matches generated titles only", () => {
  assert.match("New session - 2026-11-20T10:11:12.345Z", DEFAULT_TITLE)
  assert.doesNotMatch("New session - later", DEFAULT_TITLE)
})

const SPEC = "docs/superpowers/specs/2026-11-20-fix-auth-design.md"
const OTHER = "docs/superpowers/specs/2026-11-21-add-cache-design.md"
const PLAN = "docs/superpowers/plans/2026-11-20-fix-auth.md"
const settle = async () => { for (let i = 0; i < 10; i++) await new Promise((done) => setTimeout(done, 2)) }

function pushable(signal) {
  const queue = [], waiters = []
  signal.addEventListener("abort", () => { for (const wake of waiters.splice(0)) wake({ done: true }) })
  return {
    push(value) { const wake = waiters.shift(); if (wake) wake({ value, done: false }); else queue.push(value) },
    [Symbol.asyncIterator]: () => ({ next: () => queue.length ? Promise.resolve({ value: queue.shift(), done: false })
      : signal.aborted ? Promise.resolve({ done: true }) : new Promise((wake) => waiters.push(wake)) }),
  }
}

function memoryStorage({ failing = false } = {}) {
  const data = new Map(), calls = { get: 0, set: 0, scan: 0 }
  const fail = () => { if (failing) throw Error("storage") }
  return { data, calls,
    get: async (key) => { calls.get++; fail(); return data.has(key) ? structuredClone(data.get(key)) : undefined },
    set: async (key, value) => { calls.set++; fail(); data.set(key, structuredClone(value)) },
    remove: async (key) => { fail(); data.delete(key) },
    scan: async ({ prefix, after, limit = 50 }) => {
      calls.scan++
      fail()
      const keys = [...data.keys()].filter((key) => key.startsWith(prefix) && (after === undefined || key > after)).sort()
      const page = keys.slice(0, limit)
      return { entries: page.map((key) => ({ key, value: structuredClone(data.get(key)) })),
        ...(keys.length > limit && { next: page.at(-1) }) }
    } }
}

async function host(t, { state = "on", cleanup = false, events = true, directory, project, gitState = "on",
  storage = memoryStorage(), root, located = true, deadline } = {}) {
  if (!root) {
    root = realpathSync(mkdtempSync(join(tmpdir(), "session-titles-")))
    const made = root
    t.after(() => rmSync(made, { recursive: true, force: true }))
  }
  directory ??= root
  const titles = new Map(), calls = { get: [], update: [], run: [], hook: [], status: [], gh: [], active: 0 }
  const facts = { branch: "feat/x", upstream: true, changed: [], prs: {}, fail: false, gate: null,
    active: [], parents: {}, activeFail: false, activeWrap: false, activeRaw: undefined, activeHang: false }
  const preference = { state }
  let stream, subscribed
  const ctx = {
    ...(located && { location: { directory: root } }), storage,
    tool: { hook: async (name, handler) => calls.hook.push([name, handler]) },
    session: {
      get: async ({ sessionID }) => {
        calls.get.push(sessionID)
        if (facts.failGet) throw Error("get")
        return { id: sessionID, title: titles.get(sessionID) ?? "Fix login flow", location: { directory },
          ...((facts.parents[sessionID] ?? (sessionID.startsWith("child") ? "ses_1" : undefined)) && {
            parentID: facts.parents[sessionID] ?? "ses_1" }) }
      },
      active: async () => {
        calls.active++
        if (facts.activeFail) throw Error("active")
        if (facts.activeHang) await new Promise(() => {})
        if (facts.activeRaw !== undefined) return facts.activeRaw
        const running = Object.fromEntries(facts.active.map((id) => [id, { type: "running" }]))
        return facts.activeWrap ? { data: running } : running
      },
      update: async ({ sessionID, title }) => {
        calls.update.push([sessionID, title])
        if (facts.failUpdate) throw Error("update")
        titles.set(sessionID, title)
      },
    },
    ...(events && { event: { subscribe: ({ types, signal }) => { subscribed = { types, signal }; return stream = pushable(signal) } } }),
  }
  const run = async (command, args, options) => {
    calls.run.push([command, ...args])
    if (args[0] === "status") calls.status.push({ args, cwd: options?.cwd })
    if (command === "gh") calls.gh.push(options?.cwd)
    if (facts.fail) throw Error("run")
    if (command === "gh") {
      await facts.gate
      return JSON.stringify(facts.prs[args[args.indexOf("--head") + 1]] ?? [])
    }
    if (args[0] === "status") await facts.statusGate
    if (args[0] === "status") return `# branch.head ${facts.branch ?? "(detached)"}\n` +
      (facts.upstream ? `# branch.upstream origin/${facts.branch}\n` : "")
    if (args[0] === "rev-parse") return "origin/main\n"
    return facts.changed.join("\n")
  }
  const titlesFor = await registerSessionTitles(ctx, { run, ...(deadline && { deadline }), settings: { inspect: async () => preference },
    gitWorkflow: { inspect: async () => ({ state: gitState, project: project ?? root, worktree_cleanup: cleanup }) } })
  t.after(() => titlesFor.close())
  const write = async (path, sessionID = "ses_1", more = {}) => {
    const event = { tool: "write", status: "completed", sessionID, input: { path }, ...more }
    for (const [name, handler] of calls.hook) if (name === "execute.after") await handler(event)
    return event
  }
  const shell = async (command, sessionID = "ses_1", more = {}) => {
    const event = { tool: "shell", status: "completed", sessionID, input: { command }, ...more }
    for (const [name, handler] of calls.hook) if (name === "execute.after") await handler(event)
    return event
  }
  const emit = async (type, sessionID = "ses_1") => { stream.push({ type, data: { sessionID } }); await settle() }
  return { root, ctx, calls, facts, titles, preference, write, shell, emit, storage, registered: titlesFor, subscribed: () => subscribed,
    push: (event) => stream.push(event), end: (sessionID) => emit("session.execution.succeeded", sessionID),
    start: (sessionID) => emit("session.execution.started", sessionID) }
}

test("a spec write binds without renaming; turn boundaries write the meter and label", async (t) => {
  const h = await host(t)
  assert.equal(h.registered.registered, true)
  assert.deepEqual(h.subscribed().types, ["session.execution.started", "session.execution.succeeded",
    "session.execution.failed", "session.execution.interrupted"])
  await h.write(SPEC)
  assert.deepEqual(h.calls.update, [])
  await h.end()
  assert.equal(h.titles.get("ses_1"), "￻▰▱▱▱▱ WAITING · Fix login flow")
  await h.start()
  assert.equal(h.titles.get("ses_1"), "￻▰▱▱▱▱ Fix login flow")
  assert.equal(h.calls.update.length, 2)
})

test("default or empty titles take the spec slug; topic switches only when untouched", async (t) => {
  for (const initial of ["New session - 2026-11-20T10:11:12.345Z", ""]) {
    const h = await host(t)
    h.titles.set("ses_1", initial)
    await h.write(SPEC)
    await h.end()
    assert.equal(h.titles.get("ses_1"), "￻▰▱▱▱▱ WAITING · fix auth")
  }
  const h = await host(t)
  await h.write(SPEC)
  h.facts.changed = ["z.js"]
  await h.end()
  assert.equal(h.titles.get("ses_1"), "￻▰▰▱▱▱ WAITING · Fix login flow")
  await h.write(SPEC)
  h.facts.changed = []
  await h.end()
  assert.equal(h.titles.get("ses_1"), "￻▰▰▱▱▱ WAITING · Fix login flow")
  await h.write(OTHER)
  await h.end()
  assert.equal(h.titles.get("ses_1"), "￻▰▱▱▱▱ WAITING · add cache")
  h.titles.set("ses_1", "￻▰▱▱▱▱ WAITING · My words")
  await h.write(SPEC)
  await h.end()
  assert.equal(h.titles.get("ses_1"), "￻▰▱▱▱▱ WAITING · My words")
})

test("facts drive cells 2 to 5, never decrease, and cell 5 releases the session", async (t) => {
  const h = await host(t)
  mkdirSync(join(h.root, "docs/superpowers/plans"), { recursive: true })
  writeFileSync(join(h.root, PLAN), "**Files:**\n- Modify: `a.js`, `b.js`\n")
  await h.write(SPEC)
  await h.write(PLAN)
  const meter = async () => { await h.end(); return h.titles.get("ses_1") }
  h.facts.changed = ["z.js"]
  assert.equal(await meter(), "￻▰▰▱▱▱ WAITING · Fix login flow")
  h.facts.changed = ["a.js"]
  assert.equal(await meter(), "￻▰▰▰▱▱ WAITING · Fix login flow")
  h.facts.changed = []
  assert.equal(await meter(), "￻▰▰▰▱▱ WAITING · Fix login flow")
  h.facts.changed = ["a.js", "b.js"]
  assert.equal(await meter(), "￻▰▰▰▰▱ WAITING · Fix login flow")
  h.facts.prs = { "feat/x": [{ state: "MERGED", title: null }] }
  assert.equal(await meter(), "￻▰▰▰▰▰ Fix login flow")
  const counts = [h.calls.get.length, h.calls.run.length, h.calls.update.length]
  await h.start()
  await h.end()
  assert.deepEqual([h.calls.get.length, h.calls.run.length, h.calls.update.length], counts)
  Object.assign(h.facts, { prs: {}, changed: [] })
  await h.write(OTHER)
  await h.end()
  assert.equal(h.titles.get("ses_1"), "￻▰▱▱▱▱ WAITING · add cache")
})

test("plans set declared paths under the same checkout, bind when unbound, and are ignored elsewhere", async (t) => {
  const h = await host(t)
  mkdirSync(join(h.root, "docs/superpowers/plans"), { recursive: true })
  writeFileSync(join(h.root, PLAN), "**Files:**\n- Modify: `a.js`, `b.js`\n")
  h.facts.changed = ["a.js"]
  await h.write(PLAN)
  await h.end()
  assert.equal(h.titles.get("ses_1"), "￻▰▰▰▱▱ WAITING · Fix login flow")
  const elsewhere = await host(t)
  mkdirSync(join(elsewhere.root, "docs/superpowers/plans"), { recursive: true })
  writeFileSync(join(elsewhere.root, PLAN), "**Files:**\n- Modify: `a.js`, `b.js`\n")
  const other = await host(t)
  other.facts.changed = ["a.js"]
  await other.write(SPEC)
  await other.write(join(elsewhere.root, PLAN))
  await other.end()
  assert.equal(other.titles.get("ses_1"), "￻▰▰▱▱▱ WAITING · Fix login flow")
})

test("a symlinked session directory reaches cell 5 through the primary checkout", async (t) => {
  const real = realpathSync(mkdtempSync(join(tmpdir(), "session-real-")))
  const link = join(tmpdir(), `session-link-${process.pid}-${Date.now()}`)
  symlinkSync(real, link)
  t.after(() => { rmSync(link, { force: true }); rmSync(real, { recursive: true, force: true }) })
  const h = await host(t, { cleanup: true, directory: link, project: real })
  h.facts.prs = { "feat/x": [{ state: "MERGED", title: "x" }] }
  await h.write(SPEC)
  await h.end()
  assert.equal(h.titles.get("ses_1"), "￻▰▰▰▰▰ Fix login flow")
})

test("a primary-checkout task reaches cell 5 after the checkout returns to the default branch", async (t) => {
  const h = await host(t, { cleanup: true })
  h.facts.prs = { "feat/x": [{ state: "OPEN", title: "x" }] }
  await h.write(SPEC)
  await h.end()
  assert.equal(h.titles.get("ses_1"), "￻▰▰▰▰▱ WAITING · Fix login flow")
  h.facts.branch = "main"
  h.facts.prs = { "feat/x": [{ state: "MERGED", title: "x" }] }
  await h.end()
  assert.equal(h.titles.get("ses_1"), "￻▰▰▰▰▰ Fix login flow")
})

test("cleanup counts only while the git workflow feature is on", async (t) => {
  const h = await host(t, { cleanup: true, gitState: "off", project: "/elsewhere" })
  h.facts.prs = { "feat/x": [{ state: "MERGED", title: "x" }] }
  await h.write(SPEC)
  await h.end()
  assert.equal(h.titles.get("ses_1"), "￻▰▰▰▰▰ Fix login flow")
})

test("children, events without a session and unbound sessions write nothing; failed and interrupted evaluate", async (t) => {
  const h = await host(t)
  await h.write(SPEC, "child_1")
  await h.end("child_1")
  h.push({ type: "session.execution.succeeded", data: {} })
  h.push({ type: "session.execution.succeeded" })
  await h.end("ses_2")
  assert.deepEqual(h.calls.update, [])
  await h.write(SPEC)
  await h.emit("session.execution.failed")
  assert.equal(h.titles.get("ses_1"), "￻▰▱▱▱▱ WAITING · Fix login flow")
  h.facts.changed = ["z.js"]
  await h.emit("session.execution.interrupted")
  assert.equal(h.titles.get("ses_1"), "￻▰▰▱▱▱ WAITING · Fix login flow")
})

test("a removed prefix silences the session and an unchanged title is not rewritten", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  await h.end()
  assert.equal(h.calls.update.length, 1)
  h.titles.set("ses_1", "Plain title")
  h.facts.changed = ["z.js"]
  await h.end()
  await h.write(OTHER)
  await h.end()
  await h.start()
  assert.equal(h.calls.update.length, 1)
  assert.equal(h.titles.get("ses_1"), "Plain title")
})

test("settings off, not onboarded or invalid do no work", async (t) => {
  for (const state of ["off", "not_onboarded", "blocked"]) {
    const h = await host(t, { state })
    await h.write(SPEC)
    await h.end()
    await h.start()
    assert.deepEqual([h.calls.get, h.calls.run, h.calls.update], [[], [], []], state)
  }
  const h = await host(t)
  await h.write(SPEC)
  h.preference.state = "off"
  await h.end()
  await h.start()
  assert.deepEqual([h.calls.get.length, h.calls.run, h.calls.update], [1, [], []])
})

test("failing reads and writes keep the cells and the next turn end repairs", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  h.facts.changed = ["z.js"]
  await h.end()
  assert.equal(h.titles.get("ses_1"), "￻▰▰▱▱▱ WAITING · Fix login flow")
  h.facts.fail = true
  await h.end()
  assert.equal(h.titles.get("ses_1"), "￻▰▰▱▱▱ WAITING · Fix login flow")
  h.facts.fail = false
  h.facts.failGet = true
  h.facts.changed = ["a.js"]
  h.facts.prs = { "feat/x": [{ state: "OPEN", title: "[BLOCKED] x" }] }
  await h.end()
  h.facts.failGet = false
  h.facts.failUpdate = true
  await h.end()
  assert.equal(h.titles.get("ses_1"), "￻▰▰▱▱▱ WAITING · Fix login flow")
  h.facts.failUpdate = false
  await h.start()
  assert.equal(h.titles.get("ses_1"), "￻▰▰▱▱▱ Fix login flow")
  await h.end()
  assert.equal(h.titles.get("ses_1"), "￻▰▰▰▰▱ WAITING · Fix login flow")
})

test("a turn start during a slow evaluation ends without WAITING", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  let release
  h.facts.gate = new Promise((done) => { release = done })
  h.push({ type: "session.execution.succeeded", data: { sessionID: "ses_1" } })
  h.push({ type: "session.execution.started", data: { sessionID: "ses_1" } })
  await settle()
  assert.ok(h.calls.run.some(([command]) => command === "gh"))
  release()
  await settle()
  assert.equal(h.titles.get("ses_1"), "￻▰▱▱▱▱ Fix login flow")
})

test("a root turn that ends while a child runs shows the meter without WAITING", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  h.facts.active = ["child_1"]
  await h.end()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, false))
  h.facts.active = []
  await h.end()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, true))
})

test("a grandchild counts; another root's child, the root itself and a chain past 5 steps do not", async (t) => {
  const waiting = compose("Fix login flow", 1, true), quiet = compose("Fix login flow", 1, false)
  const chain = { d1: "ses_1", d2: "d1", d3: "d2", d4: "d3", d5: "d4", d6: "d5" }
  for (const [parents, active, label] of [
    [{ grand_1: "child_1" }, ["grand_1"], quiet],
    [{ child_9: "ses_2" }, ["child_9"], waiting],
    [{}, ["ses_1"], waiting],
    [chain, ["d5"], quiet],
    [chain, ["d6"], waiting],
  ]) {
    const h = await host(t)
    await h.write(SPEC)
    Object.assign(h.facts, { parents, active })
    await h.end()
    assert.equal(h.titles.get("ses_1"), label, JSON.stringify({ parents, active }))
  }
})

test("active result shapes and failures", async (t) => {
  const waiting = compose("Fix login flow", 1, true)
  for (const [set, label] of [
    [{ activeWrap: true, active: ["child_1"] }, compose("Fix login flow", 1, false)],
    [{ active: ["child_1"], activeFail: true }, waiting],
    [{ activeRaw: null }, waiting],
    [{ activeRaw: "x" }, waiting],
    [{ activeRaw: { data: 5 } }, waiting],
  ]) {
    const h = await host(t)
    await h.write(SPEC)
    Object.assign(h.facts, set)
    await h.end()
    assert.equal(h.titles.get("ses_1"), label, JSON.stringify(set))
  }
})

test("the active sessions are read only for a bound root's turn end and the end of a session below it", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  await h.start()
  await h.end("ses_2")
  await h.write(SPEC, "child_1")
  assert.equal(h.calls.active, 0, "a start, an unbound parentless end and a write read nothing")
  await h.end()
  assert.equal(h.calls.active, 1)
  await h.end("child_1")
  assert.equal(h.calls.active, 3, "the child's end reads once to decide and once for the label")
})

test("the label appears when the last running child ends and the root is idle", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  h.facts.active = ["child_1"]
  await h.end()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, false))
  h.facts.active = []
  await h.end("child_1")
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, true))
})

test("with two children running, only the last to end writes the label", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  Object.assign(h.facts, { parents: { child_2: "ses_1" }, active: ["child_1", "child_2"] })
  await h.end()
  const writes = h.calls.update.length
  h.facts.active = ["child_2"]
  await h.end("child_1")
  assert.equal(h.calls.update.length, writes)
  h.facts.active = []
  await h.end("child_2")
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, true))
})

test("a child's end writes nothing while the root is running again", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  h.facts.active = ["child_1"]
  await h.end()
  const [writes, lookups] = [h.calls.update.length, h.calls.gh.length]
  h.facts.active = ["ses_1"]
  await h.end("child_1")
  assert.equal(h.calls.update.length, writes)
  assert.equal(h.calls.gh.length, lookups)
})

test("a failed or interrupted child counts as ended", async (t) => {
  for (const type of ["session.execution.failed", "session.execution.interrupted"]) {
    const h = await host(t)
    await h.write(SPEC)
    h.facts.active = ["child_1"]
    await h.end()
    h.facts.active = []
    await h.emit(type, "child_1")
    assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, true), type)
  }
})

test("a child's end under an unbound or silenced root does no read and no rename", async (t) => {
  const unbound = await host(t)
  await unbound.end("child_1")
  assert.deepEqual(unbound.calls.update, [])
  assert.deepEqual(unbound.calls.run, [])
  const silenced = await host(t)
  await silenced.write(SPEC)
  await silenced.end()
  silenced.titles.set("ses_1", "My own title")
  await silenced.end("child_1")
  assert.equal(silenced.titles.get("ses_1"), "My own title")
  const writes = silenced.calls.update.length
  await silenced.end("child_1")
  assert.equal(silenced.calls.update.length, writes)
})

test("only an entry that says it is running counts", async (t) => {
  for (const [raw, label] of [
    [{ child_1: { type: "idle" } }, compose("Fix login flow", 1, true)],
    [{ data: { child_1: {} } }, compose("Fix login flow", 1, true)],
    [{ child_1: { type: "running" } }, compose("Fix login flow", 1, false)],
  ]) {
    const h = await host(t)
    await h.write(SPEC)
    h.facts.activeRaw = raw
    await h.end()
    assert.equal(h.titles.get("ses_1"), label, JSON.stringify(raw))
  }
})

test("a child-triggered evaluation writes nothing once the root is running again", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  h.facts.active = ["child_1"]
  await h.end()
  const writes = h.calls.update.length
  h.facts.active = []
  let release
  h.facts.gate = new Promise((done) => { release = done })
  h.push({ type: "session.execution.succeeded", data: { sessionID: "child_1" } })
  await settle()
  assert.ok(h.calls.gh.length > 0, "the evaluation is waiting on its fact reads")
  h.facts.active = ["ses_1"]
  release()
  await settle()
  assert.equal(h.calls.update.length, writes)
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, false))
})

test("an evaluation still reading facts when the plugin closes renames nothing", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  let release
  h.facts.gate = new Promise((done) => { release = done })
  h.push({ type: "session.execution.succeeded", data: { sessionID: "ses_1" } })
  await settle()
  assert.ok(h.calls.gh.length > 0, "the evaluation is waiting on its fact reads")
  h.registered.close()
  release()
  await settle()
  assert.deepEqual(h.calls.update, [])
})

test("a hanging active read is cut off and reads as nothing running", async (t) => {
  const h = await host(t, { deadline: 30 })
  await h.write(SPEC)
  h.facts.activeHang = true
  h.push({ type: "session.execution.succeeded", data: { sessionID: "ses_1" } })
  await new Promise((done) => setTimeout(done, 200))
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, true))
})

test("a rename during slow fact reads is kept", async (t) => {
  for (const [renamed, expected] of [["Plain title", "Plain title"], ["￻▰▱▱▱▱ My words", "￻▰▱▱▱▱ WAITING · My words"]]) {
    const h = await host(t)
    await h.write(SPEC)
    await h.end()
    await h.start()
    let release
    h.facts.gate = new Promise((done) => { release = done })
    h.push({ type: "session.execution.succeeded", data: { sessionID: "ses_1" } })
    await settle()
    assert.ok(h.calls.run.some(([command]) => command === "gh"))
    h.titles.set("ses_1", renamed)
    release()
    await settle()
    assert.equal(h.titles.get("ses_1"), expected)
  }
})

test("another branch's merge never advances the meter", async (t) => {
  const h = await host(t)
  h.facts.prs = { "feat/other": [{ state: "MERGED", title: "y" }] }
  await h.write(SPEC)
  await h.end()
  assert.equal(h.titles.get("ses_1"), "￻▰▱▱▱▱ WAITING · Fix login flow")
})

test("the 129th session evicts the oldest", async (t) => {
  const h = await host(t)
  for (let i = 1; i <= 129; i++) await h.write(SPEC, `ses_${i}`)
  await h.end("ses_1")
  await h.end("ses_2")
  await h.end("ses_129")
  assert.deepEqual(h.calls.update.map(([id]) => id), ["ses_2", "ses_129"])
})

test("close aborts the subscription and hosts without events register nothing", async (t) => {
  const h = await host(t)
  h.registered.close()
  assert.equal(h.subscribed().signal.aborted, true)
  await h.write(SPEC)
  await h.end()
  assert.deepEqual(h.calls.update, [])
  const bare = await host(t, { events: false })
  assert.equal(bare.registered.registered, false)
  assert.deepEqual(bare.calls.hook, [])
})

test("the tool result is never modified", async (t) => {
  const h = await host(t)
  const writes = []
  const record = (target) => new Proxy(target, {
    get: (object, key) => typeof object[key] === "object" && object[key] !== null ? record(object[key]) : object[key],
    set: (object, key, value) => { writes.push(key); object[key] = value; return true },
    defineProperty: (object, key, descriptor) => { writes.push(key); return Reflect.defineProperty(object, key, descriptor) },
    deleteProperty: (object, key) => { writes.push(key); return delete object[key] },
  })
  const result = record({ output: "ok", content: [{ type: "text", text: "ok" }] })
  const event = await h.write(SPEC, "ses_1", { result })
  await h.end()
  assert.equal(event.result, result)
  assert.deepEqual(writes, [])
})

const KEY = "session-titles/ses_1"
test("a binding survives a restart through plugin storage", async (t) => {
  const storage = memoryStorage()
  const first = await host(t, { storage })
  await first.write(SPEC)
  await first.end()
  assert.equal(first.titles.get("ses_1"), compose("Fix login flow", 1, true))
  first.registered.close()
  const second = await host(t, { storage, root: first.root })
  second.titles.set("ses_1", first.titles.get("ses_1"))
  second.facts.changed = ["src/login.js"]
  await second.end()
  assert.equal(second.titles.get("ses_1"), compose("Fix login flow", 2, true))
})

test("reaching cell 5 removes the stored binding, and a silenced session stays stored across a restart", async (t) => {
  const storage = memoryStorage()
  const h = await host(t, { storage })
  await h.write(SPEC)
  await h.end()
  assert.ok(storage.data.has(KEY))
  h.facts.prs["feat/x"] = [{ state: "MERGED", title: "x" }]
  await h.end()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 5, false))
  assert.equal(storage.data.has(KEY), false)
  h.facts.prs = {}
  await h.write(SPEC, "ses_2")
  await h.end("ses_2")
  h.titles.set("ses_2", "My own title")
  await h.end("ses_2")
  assert.equal(storage.data.get("session-titles/ses_2").silenced, true)
  h.registered.close()
  const again = await host(t, { storage, root: h.root })
  again.titles.set("ses_2", "My own title")
  await again.write(OTHER, "ses_2")
  await again.end("ses_2")
  assert.equal(again.titles.get("ses_2"), "My own title")
})

test("a record saved by another project's instance is ignored", async (t) => {
  const storage = memoryStorage()
  storage.data.set(KEY, { location: "/elsewhere", at: 1, last: null, silenced: false,
    task: { top: "/elsewhere", spec: "/elsewhere/s.md", plan: null, remembered: null, cells: 2, slug: "x", fresh: false } })
  const h = await host(t, { storage })
  await h.end()
  assert.deepEqual(h.calls.update, [])
})

test("storage failures fall back to memory and never throw", async (t) => {
  const h = await host(t, { storage: memoryStorage({ failing: true }) })
  await h.write(SPEC)
  await h.end()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, true))
})

test("an unknown session reads storage once, and never when session titles are off", async (t) => {
  const h = await host(t)
  await h.end("ses_9")
  await h.end("ses_9")
  assert.equal(h.storage.calls.get, 1)
  const off = await host(t, { state: "off" })
  await off.end("ses_9")
  assert.equal(off.storage.calls.get, 0)
})

test("at most 128 records are stored; the oldest go first", async (t) => {
  const storage = memoryStorage()
  const h0 = await host(t, { storage })
  for (let i = 0; i < 128; i++) storage.data.set(`session-titles/old_${i}`, { location: h0.root, at: i, task: null,
    last: null, silenced: true })
  await h0.write(SPEC)
  await h0.end()
  assert.equal([...storage.data.keys()].filter((key) => key.startsWith("session-titles/")).length, 128)
  assert.equal(storage.data.has("session-titles/old_0"), false)
  assert.ok(storage.data.has(KEY))
})

test("a malformed stored record is ignored instead of breaking the session", async (t) => {
  const good = { top: "/x", spec: "/x/s.md", plan: null, remembered: null, cells: 2, slug: "x", fresh: false }
  for (const task of [{ ...good, cells: 9 }, { ...good, cells: 0 }, { ...good, cells: 2.5 }, { ...good, slug: 5 },
    { ...good, plan: 7 }, { ...good, spec: null }, { ...good, fresh: "no" }, { ...good, top: null }]) {
    const storage = memoryStorage()
    const h = await host(t, { storage })
    storage.data.set(KEY, { location: h.root, at: 1, task, last: null, silenced: false })
    await h.end()
    await h.start()
    assert.deepEqual(h.calls.update, [], JSON.stringify(task))
  }
})

test("a session dropped from memory is reloaded from storage", async (t) => {
  const storage = memoryStorage()
  const h = await host(t, { storage })
  await h.write(SPEC)
  for (let i = 0; i < 127; i++) await h.write(SPEC, `ses_other_${i}`)
  await h.end()
  await h.write(SPEC, "ses_last")
  h.facts.changed = ["src/login.js"]
  await h.end()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 2, true))
})

test("an unchanged turn writes nothing to storage, and only a new session triggers pruning", async (t) => {
  const storage = memoryStorage()
  const h = await host(t, { storage })
  await h.write(SPEC)
  await h.end()
  const after = { ...storage.calls }
  await h.end()
  assert.equal(storage.calls.set, after.set)
  assert.equal(storage.calls.scan, after.scan)
  assert.equal(after.scan, 1)
})

test("without a known location, storage is never used", async (t) => {
  const storage = memoryStorage()
  const h = await host(t, { storage, located: false })
  await h.write(SPEC)
  await h.end()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, true))
  assert.deepEqual(storage.calls, { get: 0, set: 0, scan: 0 })
})

test("a saved record says what the last read saw and which read failed", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  const { instance, ...seen } = h.storage.data.get(KEY).diag
  assert.ok(typeof instance === "string" && instance.length > 0)
  assert.deepEqual(seen, { exists: true, branch: "feat/x", upstream: true, head: "feat/x", pr: null, declared: 0, done: 0, errors: [] })
  h.facts.fail = true
  await h.end()
  const { diag } = h.storage.data.get(KEY)
  assert.ok(diag.errors.length > 0)
  assert.deepEqual([diag.branch, diag.head], [null, "feat/x"])
  assert.doesNotMatch(JSON.stringify(diag), /run/)
})

test("a malformed stored diag is dropped and replaced at the next save", async (t) => {
  const task = (root) => ({ top: root, spec: `${root}/s.md`, plan: null, remembered: null, cells: 1, slug: "x", fresh: false })
  const ok = { instance: "a", exists: true, branch: null, upstream: false, head: null, pr: null, declared: 0, done: 0, errors: [] }
  for (const diag of [{ instance: 5 }, "x", { ...ok, errors: "no" }, { ...ok, errors: [{ step: "nope", code: "x" }] },
    { ...ok, branch: 3 }, { ...ok, extra: "x" }, { ...ok, declared: -1 }, { ...ok, done: 1.5 }, { ...ok, branch: "b".repeat(201) },
    { ...ok, errors: [{ step: "pr", code: "has space and /secret/path" }] },
    { ...ok, errors: Array.from({ length: 6 }, () => ({ step: "pr", code: "error" })) },
    { ...ok, errors: [{ step: "pr", code: "error", message: "x" }] },
    { ...ok, instance: "/Users/me/secret" }, { ...ok, instance: "" }, { ...ok, pr: "https://token@example.invalid" },
    { ...ok, pr: "open" }]) {
    const storage = memoryStorage()
    const h = await host(t, { storage })
    storage.data.set(KEY, { location: h.root, at: 1, last: null, silenced: false, diag, task: task(h.root) })
    await h.start()
    assert.equal(storage.data.get(KEY).diag, null, `a turn start keeps nothing from ${JSON.stringify(diag)}`)
    await h.end()
    const saved = storage.data.get(KEY).diag
    assert.ok(typeof saved.instance === "string" && Array.isArray(saved.errors), JSON.stringify(diag))
  }
  const storage = memoryStorage()
  const h = await host(t, { storage })
  const merged = { ...ok, pr: "MERGED", instance: "k3x9a7b2" }
  storage.data.set(KEY, { location: h.root, at: 1, last: null, silenced: false, diag: merged, task: task(h.root) })
  await h.start()
  assert.deepEqual(storage.data.get(KEY).diag, merged)
})

test("the saved diag keeps only a known PR state, and its instance id survives a reload even when Math.random returns 0", async (t) => {
  t.mock.method(Math, "random", () => 0)
  const storage = memoryStorage()
  const h = await host(t, { storage })
  await h.write(SPEC)
  h.facts.prs = { "feat/x": [{ state: "https://token@example.invalid", title: "x" }] }
  await h.end()
  const diag = { ...storage.data.get(KEY).diag }
  assert.equal(diag.pr, null, "an unknown PR state is never stored")
  assert.match(diag.instance, /^[a-z0-9]{1,16}$/)
  h.facts.prs = { "feat/x": [{ state: "OPEN", title: "x" }] }
  await h.end()
  const open = structuredClone(storage.data.get(KEY).diag)
  assert.equal(open.pr, "OPEN")
  const again = await host(t, { storage, root: h.root })
  const writes = storage.calls.set
  await again.start()
  assert.ok(storage.calls.set > writes, "the restarted host loaded the record and saved it again")
  assert.deepEqual(storage.data.get(KEY).diag, open, "the whole valid diag survives the reload, not dropped or rebuilt")
})

test("a push whose record was still loading when the plugin closed does no git read and no write", async (t) => {
  const storage = memoryStorage()
  const h = await host(t, { storage })
  storage.data.set(KEY, { location: h.root, at: 1, last: null, silenced: false, diag: null,
    task: { top: h.root, spec: `${h.root}/s.md`, plan: null, remembered: null, cells: 1, slug: "x", fresh: false } })
  let release
  const gate = new Promise((resolve) => { release = resolve })
  const get = storage.get
  storage.get = async (key) => { await gate; return get(key) }
  const pending = h.shell("git push")
  await settle()
  h.registered.close()
  release()
  await pending
  await settle()
  assert.equal(statusCalls(h), 0)
  assert.equal(storage.calls.set, 0)
  assert.equal(storage.data.get(KEY).task.remembered, null)
})

test("a push whose git read was still running when the plugin closed saves nothing", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  const writes = h.storage.calls.set
  let release
  h.facts.statusGate = new Promise((resolve) => { release = resolve })
  const pending = h.shell("git push")
  await settle()
  assert.equal(statusCalls(h), 1, "the read started")
  h.registered.close()
  release()
  await pending
  await settle()
  assert.equal(h.storage.calls.set, writes)
  assert.equal(h.storage.data.get(KEY).task.remembered, null)
})

test("the saved diag counts declared and changed plan paths", async (t) => {
  const h = await host(t)
  mkdirSync(join(h.root, "docs/superpowers/plans"), { recursive: true })
  writeFileSync(join(h.root, PLAN), "**Files:** Modify `a.js`, `b.js`\n")
  await h.write(SPEC)
  await h.write(PLAN)
  h.facts.changed = ["a.js"]
  await h.end()
  const { diag } = h.storage.data.get(KEY)
  assert.deepEqual([diag.declared, diag.done], [2, 1])
})

const statusCalls = (h) => h.calls.run.filter((call) => call[1] === "status").length

test("a push, PR create or PR merge records the bound checkout's branch, with or without an upstream", async (t) => {
  for (const command of ["git push origin HEAD", "rtk git push -u origin feat/x", "git -C /elsewhere push", "gh pr create --draft",
    "gh pr merge 5 --merge", "cd /x && git push"]) {
    const elsewhere = realpathSync(mkdtempSync(join(tmpdir(), "session-titles-elsewhere-")))
    const commandDir = realpathSync(mkdtempSync(join(tmpdir(), "session-titles-command-")))
    t.after(() => { rmSync(elsewhere, { recursive: true, force: true }); rmSync(commandDir, { recursive: true, force: true }) })
    const h = await host(t, { directory: elsewhere })
    await h.write(`${h.root}/${SPEC}`)
    Object.assign(h.facts, { branch: "feat/x", upstream: false })
    await h.shell(command, "ses_1", { input: { command, workdir: commandDir } })
    assert.equal(h.storage.data.get(KEY).task.remembered, "feat/x", command)
    assert.deepEqual(h.calls.update, [], "the title is not written at tool time")
    const status = h.calls.status.at(-1)
    assert.notEqual(h.root, elsewhere)
    assert.equal(status.cwd, h.root, `${command} reads the bound checkout, not the session or command directory`)
    assert.ok(status.args.includes("-uno"), command)
  }
})

test("merge and cleanup inside one turn still reach cell 5 once the branch was recorded at merge time", async (t) => {
  const flow = async (record) => {
    const primary = realpathSync(mkdtempSync(join(tmpdir(), "session-titles-primary-")))
    t.after(() => rmSync(primary, { recursive: true, force: true }))
    const h = await host(t, { cleanup: true, project: primary })
    await h.write(SPEC)
    h.facts.branch = "feat/x"
    if (record) await h.shell("gh pr merge 5 --merge")
    rmSync(h.root, { recursive: true, force: true })
    h.facts.prs = { "feat/x": [{ state: "MERGED", title: "x" }] }
    h.calls.gh.length = 0
    await h.end()
    assert.deepEqual(h.calls.gh, record ? [primary] : [], "the PR is looked up, from the existing primary checkout, only when the branch was recorded")
    return h.titles.get("ses_1")
  }
  assert.equal(await flow(true), compose("Fix login flow", 5, false))
  assert.equal(await flow(false), compose("Fix login flow", 1, true))
})

test("a command that is not a push, PR create or PR merge, or that failed, reads nothing", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  await h.shell("git status")
  await h.shell("gh pr view 5")
  await h.shell("git push", "ses_1", { status: "error" })
  await h.shell("git push", "ses_9")
  assert.equal(statusCalls(h), 0)
  assert.equal(h.storage.data.get(KEY).task.remembered, null)
})

test("a child session records nothing", async (t) => {
  const storage = memoryStorage()
  const h = await host(t, { storage })
  storage.data.set("session-titles/child_1", { location: h.root, at: 1, last: null, silenced: false,
    task: { top: h.root, spec: `${h.root}/s.md`, plan: null, remembered: null, cells: 1, slug: "x", fresh: false } })
  await h.shell("git push", "child_1")
  assert.equal(statusCalls(h), 0)
  assert.equal(storage.data.get("session-titles/child_1").task.remembered, null)
})

test("the default branch and a detached checkout are never recorded", async (t) => {
  for (const branch of ["main", null]) {
    const h = await host(t)
    await h.write(SPEC)
    h.facts.branch = branch
    await h.shell("git push")
    assert.equal(h.storage.data.get(KEY).task.remembered, null, String(branch))
  }
})

test("with the feature off or a failing read, a push does no work and never throws", async (t) => {
  const off = await host(t)
  await off.write(SPEC)
  off.preference.state = "off"
  await off.shell("git push")
  assert.equal(statusCalls(off), 0)
  assert.equal(off.storage.data.get(KEY).task.remembered, null)
  const h = await host(t)
  await h.write(SPEC)
  h.facts.fail = true
  await h.shell("git push")
  assert.equal(h.storage.data.get(KEY).task.remembered, null)
})
