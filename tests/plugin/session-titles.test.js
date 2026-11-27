import assert from "node:assert/strict"
import { mkdirSync, mkdtempSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs"
import fs from "node:fs/promises"
import { syncBuiltinESMExports } from "node:module"
import { tmpdir } from "node:os"
import { join } from "node:path"
import test from "node:test"
import { DEFAULT_TITLE, cellsFor, compose, registerSessionTitles, serverStart, strip } from "../../src/session-titles.js"

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
const realSetTimeout = setTimeout
const settle = async () => { for (let i = 0; i < 10; i++) await new Promise((done) => realSetTimeout(done, 2)) }
const gitStatus = (facts, args) => {
  const sep = args.includes("-z") ? "\0" : "\n"
  return [`# branch.head ${facts.branch ?? "(detached)"}`, ...(facts.upstream ? [`# branch.upstream origin/${facts.branch}`] : []), ""].join(sep)
}

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
  storage = memoryStorage(), root, located = true, deadline, idleHook = true, started = Date.now() - 60 * 60 * 1000 } = {}) {
  if (!root) {
    root = realpathSync(mkdtempSync(join(tmpdir(), "session-titles-")))
    const made = root
    t.after(() => rmSync(made, { recursive: true, force: true }))
  }
  directory ??= root
  const titles = new Map(), calls = { get: [], update: [], run: [], hook: [], status: [], gh: [], list: 0 }
  const facts = { branch: "feat/x", upstream: true, changed: [], prs: {}, fail: false, gate: null,
    active: [], finished: [], stale: [], silent: [], busyOnly: [], busyAgo: {}, longBusy: [], busyAt: {}, many: 0, parents: {}, listFail: false, listArray: false, listRaw: undefined, listHang: false }
  const preference = { state }
  let stream, subscribed, idle
  const parentOf = (id) => facts.parents[id] ?? (id.startsWith("child") ? "ses_1" : undefined)
  const timeOf = (id) => {
    if (facts.times?.[id]) return facts.times[id]
    const now = Date.now()
    // stale: busy before this server started (a run killed by a restart); longBusy: busy for 11 minutes in this run.
    if (facts.busyAt[id] !== undefined) return { created: facts.busyAt[id] - 5000, updated: facts.busyAt[id], busy: facts.busyAt[id] }
    if (facts.active.includes(id) && facts.longBusy.includes(id)) return { created: now - 12 * 60 * 1000, updated: now - 11 * 60 * 1000, busy: now - 11 * 60 * 1000 }
    const old = facts.stale.includes(id) ? now - started + 60 * 1000 : 0
    if (id.startsWith("child_m")) return { created: now - 9000, updated: now - 1000, busy: now - 2000, idle: now - 1000 }
    return { created: now - old - 5000,
      ...(!facts.busyOnly.includes(id) && { updated: facts.silent.includes(id) ? now - 11 * 60 * 1000 : now - old }),
      ...(facts.active.includes(id) && { busy: now - old - (facts.busyAgo[id] ?? 0) }),
      ...(facts.finished.includes(id) && { busy: now - 2000, idle: now - 1000 }) }
  }
  const ctx = {
    // The copy that runs a session's hooks is the one for the session's folder, as on the real host.
    ...(located && { location: { directory } }), storage,
    tool: { hook: async (name, handler) => calls.hook.push([name, handler]) },
    session: {
      hook: async (name, handler) => {
        if (!idleHook || name !== "experimental.idle") throw Error("unsupported hook")
        idle = handler
      },
      get: async ({ sessionID }) => {
        calls.get.push(sessionID)
        if (facts.failGet) throw Error("get")
        return { id: sessionID, title: titles.get(sessionID) ?? "Fix login flow", location: { directory }, time: timeOf(sessionID),
          ...(parentOf(sessionID) && { parentID: parentOf(sessionID) }) }
      },
      list: async ({ parentID, limit = 50, cursor }) => {
        calls.list++
        await facts.listGate
        if (facts.listFail) throw Error("list")
        if (facts.listHang) await new Promise(() => {})
        if (facts.listRaw !== undefined) return facts.listRaw
        const where = parentID ?? facts.lastParent
        facts.lastParent = where
        const ids = [...new Set([...Array.from({ length: facts.many }, (_, n) => `child_m${n}`), ...facts.active, ...facts.finished,
          ...Object.keys(facts.parents), "child_1"])]
        const all = ids.filter((id) => parentOf(id) === where).map((id) => ({ id, parentID: where, time: timeOf(id) }))
        const start = cursor ? Number(cursor) : 0, items = all.slice(start, start + limit)
        if (facts.listArray) return items
        return { data: items, cursor: { previous: null, next: start + limit < all.length ? String(start + limit) : null } }
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
    if (args[0] === "status") return gitStatus(facts, args)
    if (args.includes("--git-common-dir")) {
      const common = facts.common?.[options?.cwd]
      if (!common) throw Error("not a repository")
      return `${common}\n`
    }
    if (args[0] === "rev-parse") return "origin/main\n"
    return facts.changed.join("\0")
  }
  const titlesFor = await registerSessionTitles(ctx, { run, started, ...(deadline && { deadline }), settings: { inspect: async () => preference },
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
  return { started, root, ctx, calls, facts, titles, preference, write, shell, emit, storage, registered: titlesFor, subscribed: () => subscribed,
    idle: (background, sessionID = "ses_1") => idle?.({ sessionID, background }),
    push: (event) => stream.push(event), end: (sessionID) => emit("session.execution.succeeded", sessionID),
    start: (sessionID) => emit("session.execution.started", sessionID) }
}

// Two or more plugin copies, one per folder, as OpenCode runs them: one shared storage and session table, every copy
// receives every event, and a tool hook runs only in the copy for the session's current folder.
async function world(t, { folders = ["main", "wt"], project = "p1", projects = {}, separateSettings = false,
  started = Date.now() - 60 * 60 * 1000 } = {}) {
  const base = realpathSync(mkdtempSync(join(tmpdir(), "session-world-")))
  t.after(() => rmSync(base, { recursive: true, force: true }))
  const dirs = Object.fromEntries(folders.map((name) => [name, join(base, name)]))
  for (const dir of Object.values(dirs)) mkdirSync(dir, { recursive: true })
  const storage = memoryStorage(), table = new Map(), preference = { state: "on" }, reads = new Map()
  const facts = { branch: "feat/x", upstream: true, changed: [], prs: {}, gate: null, storageGate: null, getGate: null,
    updateGate: null, failGet: new Set() }
  const sessionOf = (id) => table.get(id) ?? { location: dirs[folders[0]] }
  const copies = {}
  for (const name of folders) {
    const calls = { get: 0, gh: 0, set: 0, update: [], status: [] }, hooks = []
    let stream
    const ctx = {
      location: { directory: dirs[name], project: { id: projects[name] ?? project } },
      storage: { data: storage.data,
        get: async (key) => { await facts.storageGate; return storage.get(key) },
        set: async (key, value) => { calls.set++; await facts.setGate; return storage.set(key, value) },
        remove: (key) => storage.remove(key), scan: (options) => storage.scan(options) },
      tool: { hook: async (hookName, handler) => hooks.push([hookName, handler]) },
      session: {
        get: async ({ sessionID }) => {
          calls.get++
          await facts.getGate
          if (facts.failGet.has(sessionID)) throw Error("get")
          const s = sessionOf(sessionID)
          reads.set(sessionID, (reads.get(sessionID) ?? 0) + 1)
          if (facts.gateNth?.id === sessionID && reads.get(sessionID) === facts.gateNth.n) await facts.gateNth.gate
          return { id: sessionID, title: s.title ?? "Fix login flow", location: { directory: s.location }, time: s.time ?? {},
            ...(s.parentID && { parentID: s.parentID }) }
        },
      list: async () => {
        await facts.listGate
        return { data: [], cursor: { previous: null, next: null } }
      },
        update: async ({ sessionID, title }) => {
          calls.update.push([sessionID, title])
          await facts.updateGate
          table.set(sessionID, { ...sessionOf(sessionID), title })
        },
      },
      event: { subscribe: ({ signal }) => (stream = pushable(signal)) },
    }
    const run = async (command, args, { cwd } = {}) => {
      if (command === "gh") {
        calls.gh++
        await facts.gate
        return JSON.stringify(facts.prs[args[args.indexOf("--head") + 1]] ?? [])
      }
      if (args[0] === "status") {
        calls.status.push(cwd)
        if (facts.failStatus) throw Error("status")
        return gitStatus(facts, args)
      }
      if (args.includes("--git-common-dir")) {
        if (!facts.common?.[cwd]) throw Error("not a repository")
        return `${facts.common[cwd]}\n`
      }
      if (args[0] === "rev-parse") return "origin/main\n"
      return facts.changed.join("\0")
    }
    const root = separateSettings || (projects[name] && projects[name] !== project) ? dirs[name] : dirs[folders[0]]
    const registered = await registerSessionTitles(ctx, { run, started,
      settings: { inspect: async () => ({ root, ...(facts.inspect?.() ?? preference) }) },
      gitWorkflow: { inspect: async () => ({ state: "on", project: dirs[folders[0]], worktree_cleanup: false }) } })
    t.after(() => registered.close())
    copies[name] = { ctx, calls, hooks, registered, push: (event) => stream.push(event) }
  }
  const push = async (event) => { for (const copy of Object.values(copies)) copy.push(event); await settle() }
  const hookIn = async (copy, event) => { for (const [hookName, handler] of copy.hooks) if (hookName === "execute.after") await handler(event) }
  const hook = (event) => hookIn(Object.values(copies).find((c) => c.ctx.location.directory === sessionOf(event.sessionID).location), event)
  return { dirs, copies, table, facts, storage, preference, push,
    step: (sessionID, directory, files) => push({ type: "session.step.ended", location: { directory }, data: { sessionID, files } }),
    session: (id, folder, more = {}) => table.set(id, { location: dirs[folder], ...more }),
    write: (path, id = "ses_1") => hook({ tool: "write", status: "completed", sessionID: id, input: { path } }),
    start: (id = "ses_1") => push({ type: "session.execution.started", data: { sessionID: id } }),
    end: (id = "ses_1") => push({ type: "session.execution.succeeded", data: { sessionID: id } }),
    move: async (id, folder) => {
      const from = sessionOf(id).location
      table.set(id, { ...sessionOf(id), location: dirs[folder] })
      await push({ type: "session.moved", location: { directory: from }, data: { sessionID: id, location: { directory: dirs[folder] } } })
    },
    // A move whose event this world never delivers, as if a copy had missed it.
    moveSilently: (id, folder) => table.set(id, { ...sessionOf(id), location: dirs[folder] }),
    writeIn: (copy, path, id = "ses_1") => hookIn(copies[copy], { tool: "write", status: "completed", sessionID: id, input: { path } }),
    title: (id = "ses_1") => table.get(id)?.title,
    record: (id = "ses_1") => storage.data.get(`session-titles/${id}`) }
}

// A stored binding as an earlier turn left it: the session's title is the meter it last wrote.
function seed(w, id, folder, more = {}) {
  const record = { location: w.dirs[folder], project: "p1", at: 1, last: compose("Fix login flow", 1, true), silenced: false,
    diag: null, task: { top: w.dirs[folder], spec: `${w.dirs[folder]}/docs/superpowers/specs/2026-11-25-x-design.md`, plan: null,
      remembered: null, cells: 1, slug: "x", fresh: false }, ...more }
  w.storage.data.set(`session-titles/${id}`, record)
  w.table.set(id, { ...(w.table.get(id) ?? { location: w.dirs[folder] }), title: record.last })
}

test("a spec write binds without renaming; turn boundaries write the meter and label", async (t) => {
  const h = await host(t)
  assert.equal(h.registered.registered, true)
  assert.deepEqual(h.subscribed().types, ["session.execution.started", "session.execution.succeeded",
    "session.execution.failed", "session.execution.interrupted", "session.moved", "permission.asked", "form.created",
    "permission.replied", "form.replied", "form.cancelled", "shell.exited", "session.step.ended", "experimental.job.updated", "session.deleted"])
  await h.write(SPEC)
  assert.deepEqual(h.calls.update, [])
  await h.end()
  assert.equal(h.titles.get("ses_1"), "￻▰▱▱▱▱ WAITING · Fix login flow")
  await h.start()
  assert.equal(h.titles.get("ses_1"), "￻▰▱▱▱▱ Fix login flow")
  assert.equal(h.calls.update.length, 2)
})

test("default titles take the spec slug and a distinct spec replaces the previous topic", async (t) => {
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
  assert.equal(h.titles.get("ses_1"), "￻▰▱▱▱▱ WAITING · fix auth")
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

test("a child's spec write binds its root, never the child; events without a session and unbound sessions write nothing; failed and interrupted evaluate", async (t) => {
  const h = await host(t)
  await h.write(SPEC, "child_1")
  await h.end("child_1")
  assert.deepEqual(h.calls.update.map(([id]) => id), ["ses_1"], "the child's end wakes the root it bound")
  h.push({ type: "session.execution.succeeded", data: {} })
  h.push({ type: "session.execution.succeeded" })
  await h.end("ses_2")
  assert.equal(h.calls.update.length, 1)
  await h.write(SPEC)
  await h.emit("session.execution.failed")
  assert.equal(h.titles.get("ses_1"), "￻▰▱▱▱▱ WAITING · Fix login flow")
  h.facts.changed = ["z.js"]
  await h.emit("session.execution.interrupted")
  assert.equal(h.titles.get("ses_1"), "￻▰▰▱▱▱ WAITING · Fix login flow")
})

test("a removed prefix silences its task, but a different spec starts fresh", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  await h.end()
  assert.equal(h.calls.update.length, 1)
  h.titles.set("ses_1", "Plain title")
  h.facts.changed = ["z.js"]
  await h.end()
  await h.write(PLAN)
  await h.end()
  assert.equal(h.titles.get("ses_1"), "Plain title")
  const reads = h.calls.run.length
  h.facts.changed = []
  await h.write(OTHER)
  assert.equal(h.calls.run.length, reads)
  assert.equal(h.calls.update.length, 1)
  await h.end()
  assert.equal(h.titles.get("ses_1"), "￻▰▱▱▱▱ WAITING · add cache")
})

test("new-task rename wins even before the first new meter", async (t) => {
  for (const name of ["New manual name", "New session - 2026-11-26T00:00:00Z"]) {
    const h = await host(t)
    await h.write(SPEC)
    await h.end()
    await h.write(OTHER)
    h.titles.set("ses_1", name)
    await h.end()
    assert.equal(h.titles.get("ses_1"), name)
    assert.equal(h.calls.update.length, 1)
  }
})

test("confirmed completion retires the whole task without changing a manual title", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  h.titles.set("ses_1", "My own title")
  h.facts.prs["feat/x"] = [{ state: "MERGED", title: "x" }]
  await h.end()
  assert.equal(h.titles.get("ses_1"), "My own title")
  assert.equal(h.storage.data.has(KEY), false)

  h.facts.prs = {}
  h.facts.times = { ses_1: { busy: h.started + 20, idle: h.started + 10 } }
  await h.write(PLAN)
  await h.end()
  assert.equal(h.titles.get("ses_1"), "￻▰▱▱▱▱ WAITING · fix auth")
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

test("another copy's disable fences an admitted evaluation, even after off-on", async (t) => {
  for (const reenable of [false, true]) {
    const w = await world(t)
    w.session("ses_1", "main")
    seed(w, "ses_1", "main")
    const before = w.title()
    const stored = structuredClone(w.record())
    w.facts.changed = ["src/app.js"]
    let release
    w.facts.gate = new Promise((done) => { release = done })
    await w.end()
    assert.equal(w.copies.main.calls.gh, 1)
    w.preference.state = "off"
    w.copies.wt.registered.preferenceSaved?.(false)
    if (reenable) {
      w.preference.state = "on"
      w.copies.wt.registered.preferenceSaved?.(true)
    }
    release()
    await settle()
    assert.equal(w.title(), before)
    assert.deepEqual(w.record(), stored)
    assert.equal(w.storage.calls.scan, 0)
    if (reenable) {
      await w.end()
      assert.equal(w.title(), "￻▰▰▱▱▱ WAITING · Fix login flow")
    } else {
      await w.write(`${w.dirs.main}/${OTHER}`)
      assert.deepEqual(w.record(), stored)
    }
  }
})

test("disable does not recall a sent title but prevents its later storage write", async (t) => {
  const w = await world(t)
  w.session("ses_1", "main")
  seed(w, "ses_1", "main")
  const stored = structuredClone(w.record())
  w.facts.changed = ["src/app.js"]
  let release
  w.facts.updateGate = new Promise((done) => { release = done })
  await w.end()
  assert.equal(w.copies.main.calls.update.length, 1)
  w.preference.state = "off"
  w.copies.wt.registered.preferenceSaved?.(false)
  release()
  await settle()
  assert.equal(w.title(), "￻▰▰▱▱▱ WAITING · Fix login flow")
  assert.deepEqual(w.record(), stored)
  w.preference.state = "on"
  w.copies.wt.registered.preferenceSaved?.(true)
  w.facts.changed = []
  await w.end()
  assert.equal(w.title(), "￻▰▰▱▱▱ WAITING · Fix login flow")
  assert.equal(w.record().task.cells, 2)
})

test("a sent replacement title is not mistaken for a user rename after off-on", async (t) => {
  const w = await world(t)
  w.session("ses_1", "main")
  await w.write(join(w.dirs.main, SPEC))
  await w.end()
  await w.write(join(w.dirs.main, OTHER))
  let release
  w.facts.updateGate = new Promise((done) => { release = done })
  await w.end()
  assert.equal(w.copies.main.calls.update.length, 2)
  w.preference.state = "off"
  w.copies.wt.registered.preferenceSaved?.(false)
  w.preference.state = "on"
  w.copies.wt.registered.preferenceSaved?.(true)
  release()
  await settle()
  await w.start()
  assert.equal(w.title(), "￻▰▱▱▱▱ add cache")
  assert.equal(w.record().silenced, false)
})

test("preference publication for a different project does not cancel a pending title", async (t) => {
  const w = await world(t, { projects: { wt: "other" } })
  w.session("ses_1", "main")
  seed(w, "ses_1", "main")
  w.facts.changed = ["src/app.js"]
  let release
  w.facts.gate = new Promise((done) => { release = done })
  await w.end()
  w.copies.wt.registered.preferenceSaved?.(false)
  release()
  await settle()
  assert.equal(w.title(), "￻▰▰▱▱▱ WAITING · Fix login flow")
})

test("a different checkout's Title Sync preference does not cancel work in this checkout", async (t) => {
  const w = await world(t, { separateSettings: true })
  w.session("ses_1", "main")
  seed(w, "ses_1", "main")
  w.facts.changed = ["src/app.js"]
  let release
  w.facts.gate = new Promise((done) => { release = done })
  await w.end()
  assert.equal(w.copies.main.calls.gh, 1)
  w.copies.wt.registered.preferenceSaved(false)
  release()
  await settle()
  assert.equal(w.title(), "￻▰▰▱▱▱ WAITING · Fix login flow")
})

async function progressWorld(t, { adopt = false } = {}) {
  const w = await world(t)
  w.session("ses_1", "main")
  w.session("child_1", adopt ? "wt" : "main", { parentID: "ses_1" })
  const top = w.dirs[adopt ? "wt" : "main"], plan = join(top, PLAN)
  w.facts.common = { [w.dirs.main]: "same", [w.dirs.wt]: "same" }
  mkdirSync(join(plan, ".."), { recursive: true })
  writeFileSync(plan, "**Files:** `src/a.js`, `src/b.js`, `src/c.js`, `src/d.js`\n")
  await w.write(join(w.dirs.main, SPEC))
  await w.write(plan)
  await w.end()
  return { w, top, plan, calls: w.copies.main.calls }
}

test("root and child steps advance local milestones and keep WAITING without a PR lookup", async (t) => {
  const { w, top, calls } = await progressWorld(t)
  const gh = calls.gh
  w.facts.changed = ["src/a.js", "src/b.js"]
  await w.step("ses_1", top, w.facts.changed)
  assert.equal(w.title(), "￻▰▰▰▱▱ WAITING · Fix login flow")
  w.facts.changed.push("src/c.js", "src/d.js")
  await w.step("child_1", top, ["src/c.js", "src/d.js"])
  assert.equal(w.title(), "￻▰▰▰▰▱ WAITING · Fix login flow")
  assert.equal(calls.gh, gh)
})

test("a child step in the adopted checkout is verified and published only by the main owner", async (t) => {
  const { w, top, calls } = await progressWorld(t, { adopt: true })
  const before = calls.status.length
  w.facts.changed = ["src/a.js", "src/b.js"]
  await w.step("child_1", top, w.facts.changed)
  assert.deepEqual(calls.status.slice(before), [top])
  assert.deepEqual(w.copies.wt.calls.status, [])
  assert.deepEqual(w.copies.wt.calls.update, [])
  assert.equal(w.title(), "￻▰▰▰▱▱ WAITING · Fix login flow")
})

test("higher hints arriving during verification are not lost", async (t) => {
  const { w, top, calls } = await progressWorld(t)
  const before = calls.status.length
  let release
  w.facts.getGate = new Promise((done) => { release = done })
  w.facts.changed = ["src/a.js", "src/b.js"]
  await w.step("ses_1", top, w.facts.changed)
  w.facts.changed = ["src/a.js", "src/b.js", "src/c.js", "src/d.js"]
  await w.step("ses_1", top, ["src/c.js", "src/d.js"])
  release()
  await settle()
  assert.equal(w.title(), "￻▰▰▰▰▱ WAITING · Fix login flow")
  assert.deepEqual(calls.status.slice(before), [top, top])
})

test("bad hints do not earn cells or retry an already-checked milestone", async (t) => {
  const { w, top, calls } = await progressWorld(t)
  const before = calls.status.length
  w.session("ses_other", "main")
  await w.step("ses_other", top, ["src/a.js", "src/b.js"])
  await w.step("ses_1", w.dirs.wt, ["src/a.js", "src/b.js"])
  await w.step("ses_1", join(top, "src"), ["a.js", "b.js"])
  await w.step("ses_1", top, ["../escape.js", "/tmp/file.js", "src/../x.js", null])
  assert.equal(calls.status.length, before)
  await w.step("ses_1", top, ["src/a.js", "src/a.js"])
  await w.step("ses_1", top, ["src/a.js"])
  assert.equal(calls.status.length, before + 1)
  assert.equal(w.title(), "￻▰▱▱▱▱ WAITING · Fix login flow")
  w.facts.failStatus = true
  w.facts.changed = ["src/a.js", "src/b.js"]
  await w.step("ses_1", top, w.facts.changed)
  assert.equal(w.title(), "￻▰▱▱▱▱ WAITING · Fix login flow")
})

test("steps do not create a first meter or reuse unverified cache after reload", async (t) => {
  const w = await world(t)
  w.session("ses_1", "main")
  await w.write(join(w.dirs.main, SPEC))
  w.facts.changed = ["src/a.js"]
  await w.step("ses_1", w.dirs.main, w.facts.changed)
  assert.deepEqual(w.copies.main.calls.status, [])
  assert.deepEqual(w.copies.main.calls.update, [])

  const cold = await world(t)
  cold.session("ses_1", "main")
  seed(cold, "ses_1", "main")
  await cold.start()
  cold.facts.changed = ["src/a.js"]
  await cold.step("ses_1", cold.dirs.main, cold.facts.changed)
  assert.deepEqual(cold.copies.main.calls.status, [])
  assert.equal(cold.title(), "￻▰▱▱▱▱ Fix login flow")
  await cold.end()
  assert.equal(cold.title(), "￻▰▰▱▱▱ WAITING · Fix login flow")
})

test("step verification does not resurrect a renamed title or advance a removed checkout", async (t) => {
  const named = await progressWorld(t)
  named.w.table.get("ses_1").title = "Manual name"
  named.w.facts.changed = ["src/a.js", "src/b.js"]
  await named.w.step("ses_1", named.top, named.w.facts.changed)
  assert.equal(named.w.title(), "Manual name")

  const missing = await progressWorld(t, { adopt: true })
  const before = missing.calls.status.length
  rmSync(missing.top, { recursive: true })
  missing.w.facts.changed = ["src/a.js", "src/b.js"]
  await missing.w.step("child_1", missing.top, missing.w.facts.changed)
  assert.equal(missing.calls.status.length, before)
  assert.equal(missing.w.title(), "￻▰▱▱▱▱ WAITING · Fix login flow")
})

test("a second plan edit during verification invalidates old declarations until turn end", async (t) => {
  const { w, top, plan, calls } = await progressWorld(t)
  const readFile = fs.readFile
  let planReads = 0
  const spy = t.mock.method(fs, "readFile", async (path, ...args) => {
    if (path === plan) planReads++
    return readFile(path, ...args)
  })
  syncBuiltinESMExports()
  t.after(() => { spy.mock.restore(); syncBuiltinESMExports() })
  writeFileSync(plan, "**Files:** `docs/a.md`, `docs/b.md`, `docs/c.md`, `docs/d.md`\n")
  await w.step("ses_1", top, [PLAN])
  let release
  w.facts.getGate = new Promise((done) => { release = done })
  const before = calls.status.length
  w.facts.changed = ["docs/a.md", "docs/b.md"]
  await w.step("ses_1", top, w.facts.changed)
  assert.equal(calls.status.length, before + 1, "the first plan edit was read")
  writeFileSync(plan, "**Files:** `docs/e.md`, `docs/f.md`\n")
  w.facts.changed = ["docs/e.md", "docs/f.md"]
  await w.step("ses_1", top, [PLAN, ...w.facts.changed])
  release()
  await settle()
  assert.equal(calls.status.length, before + 1)
  assert.equal(planReads, 1)
  assert.equal(w.title(), "￻▰▱▱▱▱ WAITING · Fix login flow")
  await w.end()
  assert.equal(w.title(), "￻▰▰▰▰▱ WAITING · Fix login flow")
})

test("one disabled step does not repeatedly read settings", async (t) => {
  const { w, top } = await progressWorld(t)
  let reads = 0
  w.preference.state = "off"
  w.facts.inspect = () => {
    if (++reads === 8) w.copies.main.registered.close()
    return w.preference
  }
  await w.step("ses_1", top, ["src/a.js"])
  assert.equal(reads, 1)
  assert.equal(w.title(), "￻▰▱▱▱▱ WAITING · Fix login flow")
})

test("a plan edit during turn-end activity checks invalidates declaration credit", async (t) => {
  const { w, top, plan, calls } = await progressWorld(t)
  writeFileSync(plan, "**Files:** `docs/a.md`, `docs/b.md`\n")
  await w.end()
  let release
  w.facts.listGate = new Promise((done) => { release = done })
  const before = calls.gh
  w.facts.changed = ["docs/a.md", "docs/b.md"]
  await w.end()
  assert.equal(calls.gh, before + 1)
  writeFileSync(plan, "**Files:** `docs/c.md`, `docs/d.md`\n")
  await w.step("ses_1", top, [PLAN])
  release()
  await settle()
  assert.equal(w.title(), "￻▰▱▱▱▱ WAITING · Fix login flow")
  await w.end()
  assert.equal(w.title(), "￻▰▱▱▱▱ WAITING · Fix login flow")
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

test("the children are listed only for a bound root's turn end and the end of a session below it", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  await h.start()
  await h.end("ses_2")
  await h.write(SPEC, "child_1")
  assert.equal(h.calls.list, 0, "a start, an unbound parentless end and a write read nothing")
  await h.end()
  assert.equal(h.calls.list, 1)
  await h.end("child_1")
  assert.equal(h.calls.list, 3, "the child's end reads once to decide and once for the label")
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

test("a running child counts; an idle or finished one, one busy before the server started, another root's child and the root itself do not", async (t) => {
  const waiting = compose("Fix login flow", 1, true), quiet = compose("Fix login flow", 1, false)
  for (const [set, label] of [
    [{ active: ["child_1"] }, quiet],
    [{ parents: { child_9: "ses_2" }, active: ["child_9"] }, waiting],
    [{ active: ["ses_1"] }, waiting],
    [{ finished: ["child_1"] }, waiting],
    [{ active: ["child_1"], stale: ["child_1"] }, waiting],
    [{ parents: { grand_1: "child_1" }, active: ["grand_1"] }, waiting],
    [{ parents: { grand_1: "child_1" }, active: ["child_1", "grand_1"] }, quiet],
  ]) {
    const h = await host(t)
    await h.write(SPEC)
    Object.assign(h.facts, set)
    await h.end()
    assert.equal(h.titles.get("ses_1"), label, JSON.stringify(set))
  }
})

test("list result shapes and failures", async (t) => {
  const waiting = compose("Fix login flow", 1, true)
  for (const [set, label] of [
    [{ listArray: true, active: ["child_1"] }, compose("Fix login flow", 1, false)],
    [{ active: ["child_1"], listFail: true }, waiting],
    [{ listRaw: null }, waiting],
    [{ listRaw: "x" }, waiting],
    [{ listRaw: { data: 5 } }, waiting],
    [{ listRaw: { data: [{ id: "child_1" }, null, { time: 5 }] } }, waiting],
  ]) {
    const h = await host(t)
    await h.write(SPEC)
    Object.assign(h.facts, set)
    await h.end()
    assert.equal(h.titles.get("ses_1"), label, JSON.stringify(set))
  }
})

test("a child still listed as running at its own end event does not hide the label", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  h.facts.active = ["child_1"]
  h.facts.busyAgo = { child_1: 1000 }
  await h.end()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, false))
  await h.end("child_1")
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, true))
})

test("a fresh busy mark counts even when updated is old or missing; odd rows do not count", async (t) => {
  const waiting = compose("Fix login flow", 1, true), quiet = compose("Fix login flow", 1, false), now = Date.now()
  for (const [set, label] of [
    [{ active: ["child_1"], silent: ["child_1"] }, quiet],
    [{ active: ["child_1"], busyOnly: ["child_1"] }, quiet],
    [{ listRaw: { data: [{ id: "child_1", time: { busy: Infinity } }] } }, waiting],
    [{ listRaw: { data: [{ id: "child_1", time: { busy: now, updated: "x" } }] } }, quiet],
    [{ listRaw: { data: [{ time: { busy: now, updated: now } }] } }, waiting],
  ]) {
    const h = await host(t)
    await h.write(SPEC)
    Object.assign(h.facts, set)
    await h.end()
    assert.equal(h.titles.get("ses_1"), label, JSON.stringify(set))
  }
})

test("a running child on a later page of the list counts", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  Object.assign(h.facts, { many: 60, active: ["child_run"] })
  await h.end()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, false))
  assert.equal(h.calls.list, 2, "the second page was read through the cursor")
  Object.assign(h.facts, { many: 60, active: [] })
  await h.end()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, true))
})

test("a terminal event delivered late does not hide a run that started after it happened", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  h.facts.active = ["child_1"]
  await h.end()
  const writes = h.calls.update.length
  h.facts.busyAgo = { child_1: 500 }
  h.push({ type: "session.execution.succeeded", created: Date.now() - 1000, data: { sessionID: "child_1" } })
  await settle()
  assert.equal(h.calls.update.length, writes)
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, false))
})

test("an implausible event time falls back to the receipt time", async (t) => {
  for (const created of [1, "x", Date.now() + 60 * 60 * 1000]) {
    const h = await host(t)
    await h.write(SPEC)
    h.facts.active = ["child_1"]
    await h.end()
    h.facts.busyAgo = { child_1: 1000 }
    h.push({ type: "session.execution.succeeded", created, data: { sessionID: "child_1" } })
    await settle()
    assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, true), String(created))
  }
})

test("a root still inside a long silent tool call is not labeled when its child ends", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  await h.start()
  const writes = h.calls.update.length
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, false))
  Object.assign(h.facts, { active: ["ses_1"], longBusy: ["ses_1"] })
  await h.end("child_1")
  assert.equal(h.calls.update.length, writes)
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, false))
})

test("a failing child list does not hide that the root is running again", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  h.facts.active = ["child_1"]
  await h.end()
  const writes = h.calls.update.length
  Object.assign(h.facts, { active: ["ses_1"], listFail: true })
  await h.end("child_1")
  assert.equal(h.calls.update.length, writes)
})

test("a child inside one tool call for 11 minutes still counts as running", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  Object.assign(h.facts, { active: ["child_1"], longBusy: ["child_1"] })
  await h.end()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, false))
})

test("a busy time 1 ms before the server start reads idle and 1 ms after reads running", async (t) => {
  for (const [offset, label] of [[-1, true], [1, false]]) {
    const h = await host(t)
    await h.write(SPEC)
    Object.assign(h.facts, { active: ["child_1"], busyAt: { child_1: h.started + offset } })
    await h.end()
    assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, label), `child ${offset}`)
  }
  for (const [offset, writes] of [[-1, true], [1, false]]) {
    const h = await host(t)
    await h.write(SPEC)
    await h.end()
    await h.start()
    const before = h.calls.update.length
    Object.assign(h.facts, { active: ["ses_1"], busyAt: { ses_1: h.started + offset } })
    await h.end("child_1")
    assert.equal(h.calls.update.length > before, writes, `root ${offset}`)
  }
})

test("the server start is the same for copies registered at different times", async () => {
  const first = serverStart()
  await new Promise((done) => setTimeout(done, 30))
  assert.ok(Math.abs(serverStart() - first) < 5)
  assert.ok(serverStart() <= Date.now() - process.uptime() * 1000 + 5)
})

test("a hanging list read is cut off and reads as nothing running", async (t) => {
  const h = await host(t, { deadline: 30 })
  await h.write(SPEC)
  h.facts.listHang = true
  h.push({ type: "session.execution.succeeded", data: { sessionID: "ses_1" } })
  await new Promise((done) => setTimeout(done, 200))
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, true))
})

test("the lab move sequence keeps the plan", async (t) => {
  const w = await world(t)
  w.session("ses_1", "main")
  const spec = `${w.dirs.wt}/docs/superpowers/specs/2026-11-25-x-design.md`, plan = `${w.dirs.wt}/docs/superpowers/plans/2026-11-25-x.md`
  mkdirSync(join(w.dirs.wt, "docs/superpowers/plans"), { recursive: true })
  writeFileSync(plan, "**Files:** `src/app.js`\n")
  await w.write(spec)
  await w.end()
  assert.equal(w.title(), compose("Fix login flow", 1, true))
  await w.move("ses_1", "wt")
  await w.write(plan)
  w.facts.changed = ["src/app.js"]
  await w.end()
  assert.equal(w.title(), compose("Fix login flow", 4, true))
  await w.move("ses_1", "main")
  await w.end()
  assert.equal(w.title(), compose("Fix login flow", 4, true), "the meter never goes back")
  assert.deepEqual([w.record().task.plan, w.record().task.cells, w.record().project], [plan, 4, "p1"])
})

test("a record from another project is ignored and a legacy record loads only in its folder", async (t) => {
  const w = await world(t)
  for (const [id, more, loaded] of [["ses_a", { project: "other" }, false], ["ses_b", { project: undefined }, true]]) {
    w.session(id, "main")
    seed(w, id, "main", more)
    w.facts.changed = ["src/app.js"]
    await w.end(id)
    assert.equal(w.title(id) === compose("Fix login flow", 2, true), loaded, id)
  }
  w.session("ses_c", "main")
  seed(w, "ses_c", "wt", { project: undefined })
  await w.end("ses_c")
  assert.equal(w.title("ses_c"), compose("Fix login flow", 1, true), "a legacy record saved in another folder is not loaded")
  assert.equal(w.record("ses_c").at, 1)
})

test("a failed ownership read retries on the next event", async (t) => {
  const w = await world(t)
  w.session("ses_1", "main")
  seed(w, "ses_1", "main")
  w.facts.failGet.add("ses_1")
  w.facts.changed = ["src/app.js"]
  await w.end()
  assert.equal(w.title(), compose("Fix login flow", 1, true))
  w.facts.failGet.delete("ses_1")
  await w.end()
  assert.equal(w.title(), compose("Fix login flow", 2, true))
})

test("a non-owner makes one cold ownership read and no gh call or storage write", async (t) => {
  const w = await world(t)
  w.session("ses_1", "main")
  seed(w, "ses_1", "main")
  await w.end()
  const reads = w.copies.wt.calls.get
  for (let i = 0; i < 3; i++) await w.end()
  assert.ok(reads >= 1 && reads <= 2, `cold reads ${reads}`)
  assert.equal(w.copies.wt.calls.get, reads, "no further reads for the session's own turn events")
  assert.deepEqual([w.copies.wt.calls.gh, w.copies.wt.calls.set, w.copies.wt.calls.update.length], [0, 0, 0])
  assert.ok(w.copies.main.calls.gh > 0, "the owner evaluated")
})

test("a move during a slow evaluation or a slow storage read writes nothing in the old copy", async (t) => {
  const w = await world(t)
  w.session("ses_1", "main")
  seed(w, "ses_1", "main")
  await w.end()
  let release
  w.facts.gate = new Promise((done) => { release = done })
  const before = { ...w.copies.main.calls, update: w.copies.main.calls.update.length }
  w.facts.changed = ["src/app.js"]
  w.copies.main.push({ type: "session.execution.succeeded", data: { sessionID: "ses_1" } })
  await settle()
  await w.move("ses_1", "wt")
  release()
  await settle()
  assert.deepEqual([w.copies.main.calls.update.length, w.copies.main.calls.set], [before.update, before.set])

  const v = await world(t)
  v.session("ses_2", "main")
  seed(v, "ses_2", "main")
  v.facts.storageGate = new Promise((done) => { release = done })
  v.copies.main.push({ type: "session.execution.succeeded", data: { sessionID: "ses_2" } })
  await settle()
  await v.move("ses_2", "wt")
  release()
  await settle()
  assert.deepEqual([v.copies.main.calls.update.length, v.copies.main.calls.set], [0, 0], "no stale record published or written")
})

test("a close during a slow evaluation starts no new write", async (t) => {
  const w = await world(t)
  w.session("ses_1", "main")
  seed(w, "ses_1", "main")
  let release
  w.facts.gate = new Promise((done) => { release = done })
  await w.end()
  w.copies.main.registered.close()
  release()
  await settle()
  assert.deepEqual([w.copies.main.calls.update.length, w.copies.main.calls.set], [0, 0])
})

test("a copy that missed a move does not write the session", async (t) => {
  const w = await world(t)
  w.session("ses_1", "main")
  seed(w, "ses_1", "main")
  await w.end()
  w.moveSilently("ses_1", "wt")
  const before = { update: w.copies.main.calls.update.length, set: w.copies.main.calls.set }
  w.facts.changed = ["src/app.js"]
  await w.end()
  assert.deepEqual([w.copies.main.calls.update.length, w.copies.main.calls.set], [before.update, before.set])
})

test("a move during a slow title update starts no storage write", async (t) => {
  const w = await world(t)
  w.session("ses_1", "main")
  seed(w, "ses_1", "main")
  let release
  w.facts.updateGate = new Promise((done) => { release = done })
  w.facts.changed = ["src/app.js"]
  w.copies.main.push({ type: "session.execution.succeeded", data: { sessionID: "ses_1" } })
  await settle()
  assert.equal(w.copies.main.calls.update.length, 1, "the update is in flight")
  await w.move("ses_1", "wt")
  release()
  await settle()
  assert.equal(w.copies.main.calls.set, 0)
})

test("a load interrupted by a move does not publish a stale binding", async (t) => {
  const w = await world(t)
  w.session("ses_1", "main")
  seed(w, "ses_1", "main")
  let release
  w.facts.storageGate = new Promise((done) => { release = done })
  w.copies.main.push({ type: "session.execution.started", data: { sessionID: "ses_1" } })
  await settle()
  await w.move("ses_1", "wt")
  release()
  await settle()
  w.facts.storageGate = null
  const newer = `${w.dirs.wt}/docs/superpowers/specs/2026-11-26-y-design.md`
  await w.write(newer)
  await w.end()
  w.moveSilently("ses_1", "main")
  await w.end()
  assert.equal(w.record().task.spec, newer, "the main copy never held the older binding")
})

test("a hook delivered to a copy that does not own the session does nothing", async (t) => {
  const w = await world(t)
  w.session("ses_1", "main")
  await w.writeIn("wt", `${w.dirs.main}/docs/superpowers/specs/2026-11-25-x-design.md`)
  assert.equal(w.record(), undefined)
})

test("a child bind whose root moves while it is being read does not write the old folder's record", async (t) => {
  const w = await world(t)
  w.session("ses_1", "main")
  w.session("child_1", "main", { parentID: "ses_1" })
  let release
  w.facts.gateNth = { id: "ses_1", n: 2, gate: new Promise((done) => { release = done }) }
  const writing = w.write(`${w.dirs.main}/docs/superpowers/specs/2026-11-25-x-design.md`, "child_1")
  await settle()
  await w.move("ses_1", "wt")
  release()
  await writing
  await settle()
  assert.equal(w.record(), undefined)
})

test("a close during a storage write starts no pruning scan", async (t) => {
  const w = await world(t)
  w.session("ses_1", "main")
  let release
  w.facts.setGate = new Promise((done) => { release = done })
  const writing = w.write(`${w.dirs.main}/docs/superpowers/specs/2026-11-25-x-design.md`)
  await settle()
  w.copies.main.registered.close()
  release()
  await writing
  await settle()
  assert.equal(w.storage.calls.scan, 0)
})

test("a folder that cannot be resolved yet is retried, not cached as someone else's", async (t) => {
  const w = await world(t)
  const link = join(w.dirs.main, "..", "link-later")
  w.session("ses_1", "main")
  seed(w, "ses_1", "main")
  w.table.set("ses_1", { ...w.table.get("ses_1"), location: link })
  w.facts.changed = ["src/app.js"]
  await w.end()
  assert.equal(w.title(), compose("Fix login flow", 1, true))
  symlinkSync(w.dirs.main, link)
  await w.end()
  assert.equal(w.title(), compose("Fix login flow", 2, true))
})

test("the move's turn end is evaluated once, by the destination", async (t) => {
  const w = await world(t)
  w.session("ses_1", "main")
  seed(w, "ses_1", "main")
  await w.end()
  const gh = { main: w.copies.main.calls.gh, wt: w.copies.wt.calls.gh }
  await w.start()
  await w.move("ses_1", "wt")
  await w.end()
  assert.deepEqual([w.copies.main.calls.gh - gh.main, w.copies.wt.calls.gh - gh.wt], [0, 1])
  assert.equal(w.title(), compose("Fix login flow", 1, true))
})

test("a child end that comes first after a reload restores and wakes its stored root", async (t) => {
  const w = await world(t)
  w.session("ses_1", "main")
  w.session("child_1", "main", { parentID: "ses_1" })
  seed(w, "ses_1", "main", { last: compose("Fix login flow", 1, false) })
  w.table.set("ses_1", { ...w.table.get("ses_1"), title: compose("Fix login flow", 1, false) })
  await w.end("child_1")
  assert.equal(w.title(), compose("Fix login flow", 1, true))
})

test("a child's spec write binds the root, and a child in another folder does not", async (t) => {
  const w = await world(t)
  const spec = (dir) => `${dir}/docs/superpowers/specs/2026-11-25-x-design.md`
  w.session("ses_1", "main")
  w.session("child_1", "main", { parentID: "ses_1" })
  await w.write(spec(w.dirs.main), "child_1")
  assert.equal(w.record().task.spec, spec(w.dirs.main))
  assert.equal(w.record("child_1"), undefined)
  await w.end()
  assert.equal(w.title(), compose("Fix login flow", 1, true))
  w.session("ses_2", "main")
  w.session("child_2", "wt", { parentID: "ses_2" })
  await w.write(spec(w.dirs.wt), "child_2")
  assert.equal(w.record("ses_2"), undefined)
})

const ask = (h, type, id, sessionID = "ses_1") => {
  const data = type === "form.created" ? { form: { id, sessionID } }
    : type === "permission.replied" ? { sessionID, requestID: id, reply: "once" }
    : type === "permission.asked" ? { id, sessionID, action: "shell", resources: [] } : { id, sessionID }
  h.push({ type, data })
  return settle()
}
// A bound root that already shows its meter and is running a turn.
async function running(t) {
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  await h.start()
  h.facts.active = ["ses_1"]
  return h
}
const quiet = compose("Fix login flow", 1, false), waiting = compose("Fix login flow", 1, true)

test("a permission request sets WAITING mid-turn without git reads and its reply removes it", async (t) => {
  const h = await running(t)
  assert.equal(h.titles.get("ses_1"), quiet)
  const reads = h.calls.run.length
  await ask(h, "permission.asked", "per_1")
  assert.equal(h.titles.get("ses_1"), waiting)
  await ask(h, "permission.replied", "per_1")
  assert.equal(h.titles.get("ses_1"), quiet)
  assert.equal(h.calls.run.length, reads, "no git or gh read")
})

test("a question form does the same, and cancel removes it", async (t) => {
  const h = await running(t)
  await ask(h, "form.created", "frm_1")
  assert.equal(h.titles.get("ses_1"), waiting)
  await ask(h, "form.replied", "frm_1")
  assert.equal(h.titles.get("ses_1"), quiet)
  await ask(h, "form.created", "frm_2")
  await ask(h, "form.cancelled", "frm_2")
  assert.equal(h.titles.get("ses_1"), quiet)
})

test("overlapping requests keep WAITING until the last answer", async (t) => {
  const h = await running(t)
  await ask(h, "permission.asked", "per_1")
  await ask(h, "form.created", "frm_1")
  await ask(h, "permission.replied", "per_1")
  assert.equal(h.titles.get("ses_1"), waiting)
  await ask(h, "form.replied", "frm_1")
  assert.equal(h.titles.get("ses_1"), quiet)
})

test("a reply never removes an idle session's WAITING", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  const writes = h.calls.update.length
  await ask(h, "permission.replied", "per_x")
  await ask(h, "form.cancelled", "frm_x")
  await ask(h, "form.created", "frm_1")
  await ask(h, "form.replied", "frm_1")
  assert.equal(h.titles.get("ses_1"), waiting)
  assert.equal(h.calls.update.length, writes)
})

test("a request answered before its label is written leaves none; a new one before the clear keeps it", async (t) => {
  const h = await running(t)
  let writes = h.calls.update.length
  h.push({ type: "permission.asked", data: { id: "per_1", sessionID: "ses_1" } })
  h.push({ type: "permission.replied", data: { sessionID: "ses_1", requestID: "per_1" } })
  await settle()
  assert.equal(h.titles.get("ses_1"), quiet)
  assert.equal(h.calls.update.length, writes, "no WAITING flashed")
  await ask(h, "permission.asked", "per_2")
  writes = h.calls.update.length
  h.push({ type: "permission.replied", data: { sessionID: "ses_1", requestID: "per_2" } })
  h.push({ type: "permission.asked", data: { id: "per_3", sessionID: "ses_1" } })
  await settle()
  assert.equal(h.titles.get("ses_1"), waiting)
  assert.equal(h.calls.update.length, writes, "the label never cleared in between")
})

test("a dropped reply is cleared at the root's next turn boundary", async (t) => {
  const h = await running(t)
  await ask(h, "permission.asked", "per_dropped")
  await h.end()
  await h.start()
  await ask(h, "permission.asked", "per_2")
  assert.equal(h.titles.get("ses_1"), waiting)
  await ask(h, "permission.replied", "per_2")
  assert.equal(h.titles.get("ses_1"), quiet, "the dropped id no longer holds the label")
})

test("no request label before the first meter or for a task bound this turn", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  h.facts.active = ["ses_1"]
  await ask(h, "permission.asked", "per_1")
  assert.deepEqual(h.calls.update, [])
  const g = await running(t)
  await g.write(OTHER)
  await ask(g, "permission.asked", "per_1")
  assert.equal(g.titles.get("ses_1"), quiet)
})

test("a child-triggered evaluation does not clear pending requests", async (t) => {
  const h = await running(t)
  await ask(h, "permission.asked", "per_1")
  await h.end("child_1")
  await ask(h, "form.created", "frm_2")
  await ask(h, "form.replied", "frm_2")
  assert.equal(h.titles.get("ses_1"), waiting, "per_1 still pending")
})

test("a 17th request is ignored", async (t) => {
  const h = await running(t)
  for (let i = 1; i <= 17; i++) await ask(h, "permission.asked", `per_${i}`)
  for (let i = 1; i <= 16; i++) await ask(h, "permission.replied", `per_${i}`)
  assert.equal(h.titles.get("ses_1"), quiet)
})

test("requests of unbound or child sessions do nothing", async (t) => {
  const h = await running(t)
  const before = { get: h.calls.get.length, update: h.calls.update.length }
  await ask(h, "permission.asked", "per_1", "ses_2")
  await ask(h, "form.created", "frm_1", "child_1")
  assert.deepEqual([h.calls.get.length, h.calls.update.length], [before.get, before.update])
})

const background = (h, id, command = "sleep 45") => h.shell(command, "ses_1", { result: { content: [], metadata: { shellID: id, status: "running" } } })
const exited = (h, id) => { h.push({ type: "shell.exited", data: { id, exit: 0, status: "exited" } }); return settle() }

const job = (sessionID, shellID, status = "running", kind = "shell") => ({
  type: "experimental.job.updated", data: { info: { kind, id: shellID, sessionID, shellID, status } },
})

function shellClock(t) {
  t.mock.timers.enable({ apis: ["setTimeout"] })
  return async (ms = 200) => { t.mock.timers.tick(ms); await settle() }
}

function delaySessionRead(h, id) {
  const get = h.ctx.session.get
  let release
  const gate = new Promise((done) => { release = done })
  h.ctx.session.get = async (input) => {
    if (input.sessionID === id) await gate
    return get(input)
  }
  return release
}

test("a running background shell keeps WAITING off until 200 ms after its final idle exit", async (t) => {
  const tick = shellClock(t)
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  await h.start()
  await background(h, "sh_1")
  await h.end()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, false))
  await exited(h, "sh_1")
  await h.end()
  await tick(199)
  assert.equal(h.titles.get("ses_1"), quiet)
  await tick(1)
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, true))
})

test("a background shell's exit while the session runs again writes nothing", async (t) => {
  const tick = shellClock(t)
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  await h.start()
  await background(h, "sh_1")
  await h.end()
  await h.start()
  h.facts.active = ["ses_1"]
  const writes = h.calls.update.length
  await exited(h, "sh_1")
  await tick()
  assert.equal(h.calls.update.length, writes)
})

test("with two background shells only the last exit labels", async (t) => {
  const tick = shellClock(t)
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  await h.start()
  await background(h, "sh_1")
  await background(h, "sh_2")
  await h.end()
  await exited(h, "sh_1")
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, false))
  await exited(h, "sh_2")
  await tick()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, true))
})

test("cross-worktree background jobs clear WAITING only for their bound ancestor", async (t) => {
  const tick = shellClock(t)
  const w = await world(t)
  w.session("ses_1", "main")
  w.session("child_1", "wt", { parentID: "ses_1" })
  w.session("child_2", "wt", { parentID: "child_1" })
  w.session("other", "wt")
  await w.write(SPEC)
  await w.end()
  await w.push(job("other", "sh_other"))
  await w.push(job("child_2", "sh_image", "running", "image"))
  assert.equal(w.title(), waiting)
  const gh = w.copies.main.calls.gh
  await w.push(job("child_2", "sh_1"))
  assert.equal(w.title(), quiet)
  await w.push(job("child_2", "sh_2"))
  await w.push(job("child_2", "sh_1", "completed"))
  await tick()
  assert.equal(w.title(), quiet)
  await w.push(job("child_2", "sh_2", "cancelled"))
  await tick(199)
  assert.equal(w.title(), quiet)
  await tick(1)
  assert.equal(w.title(), waiting)
  assert.deepEqual(w.copies.main.calls.update, [["ses_1", waiting], ["ses_1", quiet], ["ses_1", waiting]])
  assert.equal(w.copies.main.calls.gh, gh)
  assert.deepEqual(w.copies.wt.calls.update, [])
})

test("a resumed descendant or replacement shell cancels the final-exit grace", async (t) => {
  const tick = shellClock(t)
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  h.push(job("child_1", "sh_1"))
  await settle()
  await exited(h, "sh_1")
  await h.start("child_1")
  await tick()
  assert.equal(h.titles.get("ses_1"), quiet)
  h.push(job("child_1", "sh_2"))
  await settle()
  await exited(h, "sh_2")
  await tick(100)
  const release = delaySessionRead(h, "child_slow")
  h.push(job("child_slow", "sh_3"))
  await settle()
  await tick(100)
  assert.equal(h.titles.get("ses_1"), quiet)
  release()
  await settle()
  assert.equal(h.titles.get("ses_1"), quiet)
  await exited(h, "sh_3")
  await tick()
  assert.equal(h.titles.get("ses_1"), waiting)
})

test("deleting a child restores its cancelled shell grace and ignores a late start", async (t) => {
  const tick = shellClock(t)
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  h.push(job("child_1", "sh_1"))
  await settle()
  await exited(h, "sh_1")
  await h.start("child_1")
  await tick()
  assert.equal(h.titles.get("ses_1"), quiet)
  for (let i = 0; i < 64; i++) h.push({ type: "shell.exited", data: { id: `sh_unrelated_${i}` } })
  await settle()
  const reads = h.calls.run.length
  await h.emit("session.deleted", "child_1")
  await h.start("child_1")
  await tick(199)
  assert.equal(h.titles.get("ses_1"), quiet)
  await tick(1)
  assert.equal(h.titles.get("ses_1"), waiting)
  assert.equal(h.calls.run.length, reads)
})

test("deletion during a stale busy-child check re-arms shell grace", async (t) => {
  const tick = shellClock(t)
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  h.push(job("child_1", "sh_1"))
  await settle()
  h.facts.listRaw = { data: [{ id: "child_1", get time() {
    h.push({ type: "session.deleted", data: { sessionID: "child_1" } })
    return { busy: Date.now() }
  } }] }
  await exited(h, "sh_1")
  await tick()
  assert.equal(h.titles.get("ses_1"), quiet)
  await tick()
  assert.equal(h.titles.get("ses_1"), waiting)
})

test("a legacy snapshot after deletion still holds WAITING until the shell exits", async (t) => {
  const tick = shellClock(t)
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  await h.start()
  await h.emit("session.deleted", "child_1")
  await h.idle([{ kind: "shell", sessionID: "child_1", shellID: "sh_1" }])
  await h.end()
  assert.equal(h.titles.get("ses_1"), quiet)
  await exited(h, "sh_1")
  await tick()
  assert.equal(h.titles.get("ses_1"), waiting)
})

test("a late tool result cannot bind a deleted root even if the host still returns it", async (t) => {
  const h = await host(t)
  await h.emit("session.deleted")
  await h.write(SPEC)
  assert.equal(h.storage.data.has(KEY), false)
  assert.deepEqual(h.calls.get, [])
})

test("deletion before a legacy shell exit does not label while another shell runs", async (t) => {
  const tick = shellClock(t)
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  await h.start()
  await h.idle([
    { kind: "shell", sessionID: "child_1", shellID: "sh_1" },
    { kind: "shell", sessionID: "child_2", shellID: "sh_2" },
  ])
  await h.end()
  await h.emit("session.deleted", "child_1")
  h.facts.active = ["child_1"] // A stale native list row cannot keep the root busy after deletion.
  await exited(h, "sh_1")
  await h.start("child_1")
  await tick()
  assert.equal(h.titles.get("ses_1"), quiet)
  await exited(h, "sh_2")
  await h.start("child_1")
  await tick()
  assert.equal(h.titles.get("ses_1"), waiting)
})

test("a real permission prompt shows WAITING immediately during shell activity and grace", async (t) => {
  const tick = shellClock(t)
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  await ask(h, "permission.asked", "per_shell")
  h.push(job("child_1", "sh_1"))
  await settle()
  assert.equal(h.titles.get("ses_1"), waiting)
  await ask(h, "permission.replied", "per_shell")
  assert.equal(h.titles.get("ses_1"), quiet)
  let release
  h.facts.listGate = new Promise((done) => { release = done })
  await exited(h, "sh_1")
  await tick()
  await ask(h, "permission.asked", "per_grace")
  assert.equal(h.titles.get("ses_1"), waiting)
  release()
  await settle()
  assert.equal(h.titles.get("ses_1"), waiting)
})

test("a shell starting during the final idle read still suppresses WAITING", async (t) => {
  const tick = shellClock(t)
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  h.push(job("child_1", "sh_1"))
  await settle()
  let releaseList
  h.facts.listGate = new Promise((done) => { releaseList = done })
  await exited(h, "sh_1")
  await tick()
  const releaseStart = delaySessionRead(h, "child_slow")
  h.push(job("child_slow", "sh_2"))
  await settle()
  releaseList()
  await settle()
  assert.equal(h.titles.get("ses_1"), quiet)
  releaseStart()
  await settle()
  assert.equal(h.titles.get("ses_1"), quiet)
})

test("an unreadable unrelated shell cannot strand the idle title", async (t) => {
  const tick = shellClock(t)
  const h = await host(t, { deadline: 50 })
  h.facts.parents.child_other = "other"
  await h.write(SPEC)
  await h.end()
  h.push(job("child_1", "sh_1"))
  await settle()
  await exited(h, "sh_1")
  const release = delaySessionRead(h, "child_other")
  h.push(job("child_other", "sh_other"))
  await settle()
  await tick()
  await tick(50)
  assert.equal(h.titles.get("ses_1"), waiting)
  release()
  await settle()
  assert.equal(h.titles.get("ses_1"), waiting)
})

test("a manual rename during the shell exit checks wins", async (t) => {
  const tick = shellClock(t)
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  h.push(job("child_1", "sh_1"))
  await settle()
  let release
  h.facts.listGate = new Promise((done) => { release = done })
  await exited(h, "sh_1")
  await tick()
  h.titles.set("ses_1", "My own title")
  release()
  await settle()
  assert.equal(h.titles.get("ses_1"), "My own title")
})

test("a manual rename while shell grace waits behind a title save wins", async (t) => {
  const tick = shellClock(t)
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  const save = h.storage.set
  let release
  const gate = new Promise((done) => { release = done })
  h.storage.set = async (...args) => { await gate; return save(...args) }
  h.push(job("child_1", "sh_1"))
  await settle()
  assert.equal(h.titles.get("ses_1"), quiet)
  await exited(h, "sh_1")
  await tick()
  h.titles.set("ses_1", "My own title")
  release()
  await settle()
  assert.equal(h.titles.get("ses_1"), "My own title")
})

test("a cold shell start during a refreshed title read can still veto WAITING", async (t) => {
  const tick = shellClock(t)
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  h.push(job("child_1", "sh_1"))
  await settle()
  h.facts.parents.child_other = "other"
  const releaseOther = delaySessionRead(h, "child_other")
  h.push(job("child_other", "sh_other"))
  await settle()
  await exited(h, "sh_1")
  await tick()
  const releaseRoot = delaySessionRead(h, "ses_1")
  releaseOther()
  await settle()
  const releaseStart = delaySessionRead(h, "child_slow")
  h.push(job("child_slow", "sh_2"))
  await settle()
  releaseRoot()
  await settle()
  assert.equal(h.titles.get("ses_1"), quiet)
  releaseStart()
  await settle()
  assert.equal(h.titles.get("ses_1"), quiet)
})

test("the final idle read does not queue a real request behind a slow root read", async (t) => {
  const tick = shellClock(t)
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  h.push(job("child_1", "sh_1"))
  await settle()
  let release
  const gated = new Promise((done) => { release = done })
  const get = h.ctx.session.get
  let firstRootRead = true
  h.ctx.session.get = async (input) => {
    if (input.sessionID === "ses_1" && firstRootRead) {
      firstRootRead = false
      await gated
    }
    return get(input)
  }
  await exited(h, "sh_1")
  await tick()
  assert.equal(firstRootRead, false)
  await ask(h, "form.created", "frm_grace")
  assert.equal(h.titles.get("ses_1"), waiting)
  release()
  await settle()
  assert.equal(h.titles.get("ses_1"), waiting)
})

test("disabled Title Sync does not look up cold shell ancestry", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  h.preference.state = "off"
  h.registered.preferenceSaved(false)
  const reads = h.calls.get.length
  h.push(job("child_unknown", "sh_1"))
  await settle()
  assert.equal(h.calls.get.length, reads)
  assert.equal(h.titles.get("ses_1"), waiting)
})

test("a delayed background start cannot relabel a replacement task", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  const release = delaySessionRead(h, "child_slow")
  h.push(job("child_slow", "sh_old"))
  await settle()
  await h.write(OTHER)
  await h.end()
  release()
  await settle()
  assert.equal(h.titles.get("ses_1"), "￻▰▱▱▱▱ WAITING · add cache")
})

test("another copy's preference revision invalidates the old shell grace", async (t) => {
  const tick = shellClock(t)
  const w = await world(t)
  w.session("ses_1", "main")
  w.session("child_1", "wt", { parentID: "ses_1" })
  await w.write(SPEC)
  await w.end()
  await w.push(job("child_1", "sh_1"))
  await w.push(job("child_1", "sh_1", "completed"))
  w.preference.state = "off"
  w.copies.wt.registered.preferenceSaved(false)
  await tick(100)
  assert.equal(w.title(), quiet)
  w.preference.state = "on"
  w.copies.wt.registered.preferenceSaved(true)
  await w.end()
  assert.equal(w.title(), waiting)
})

test("a background start with known ancestry can precede the first task bind", async (t) => {
  const h = await host(t, { idleHook: false })
  await h.start()
  await h.start("child_1")
  const reads = h.calls.get.length
  h.push(job("child_1", "sh_1"))
  await settle()
  assert.equal(h.calls.get.length, reads)
  await h.write(SPEC)
  await h.end()
  assert.equal(h.titles.get("ses_1"), quiet)
  await h.write(OTHER)
  await h.end()
  assert.equal(h.titles.get("ses_1"), "￻▰▱▱▱▱ WAITING · add cache")
})

test("unbound background jobs cannot evict a bound task's running shell", async (t) => {
  const h = await host(t)
  h.facts.parents.child_other = "other"
  await h.write(SPEC)
  await h.end()
  h.push(job("child_1", "sh_owned"))
  await settle()
  for (let i = 0; i < 64; i++) h.push(job("child_other", `sh_other_${i}`))
  await settle()
  await h.end()
  assert.equal(h.titles.get("ses_1"), quiet)
})

test("an idle snapshot supplies descendant shells on hosts without job updates", async (t) => {
  const tick = shellClock(t)
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  await h.start()
  await h.idle([{ kind: "shell", sessionID: "child_1", shellID: "sh_1" }])
  await h.end()
  assert.equal(h.titles.get("ses_1"), quiet)
  await exited(h, "sh_1")
  await tick()
  assert.equal(h.titles.get("ses_1"), waiting)
})

test("an unsupported idle hook does not disable Title Sync", async (t) => {
  const h = await host(t, { idleHook: false })
  await h.write(SPEC)
  await h.end()
  assert.equal(h.titles.get("ses_1"), waiting)
})

test("a shell that exits before its hook runs is never tracked", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  await h.start()
  await exited(h, "sh_fast")
  await background(h, "sh_fast")
  await h.end()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, true))
})

test("a 65th background shell drops the oldest", async (t) => {
  const tick = shellClock(t)
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  await h.start()
  for (let i = 0; i <= 64; i++) await background(h, `sh_${i}`)
  await h.end()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, false))
  for (let i = 1; i <= 64; i++) await exited(h, `sh_${i}`)
  await tick()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, true))
})


test("a finished shell result is never tracked, and an unbound session's shell exit costs no read", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  await h.start()
  await h.shell("ls", "ses_1", { result: { content: [], metadata: { shellID: "sh_done", status: "exited", exit: 0 } } })
  await h.end()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, true))
  await h.shell("sleep 9", "ses_2", { result: { content: [], metadata: { shellID: "sh_other", status: "running" } } })
  const reads = { get: h.calls.get.length, list: h.calls.list }
  await exited(h, "sh_other")
  assert.deepEqual([h.calls.get.length, h.calls.list], [reads.get, reads.list])
})

test("a shell started while an earlier shell's exit check runs keeps WAITING off", async (t) => {
  const tick = shellClock(t)
  const h = await host(t)
  await h.write(SPEC)
  await h.end()
  await h.start()
  await background(h, "sh_1")
  await h.end()
  let release
  h.facts.listGate = new Promise((done) => { release = done })
  h.push({ type: "shell.exited", data: { id: "sh_1", status: "exited" } })
  await settle()
  await tick()
  await background(h, "sh_2")
  release()
  await settle()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, false))
})

test("a background git push still records its branch", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  Object.assign(h.facts, { branch: "feat/x", upstream: false })
  await background(h, "sh_push", "git push -u origin feat/x")
  assert.equal(h.storage.data.get(KEY).task.remembered, "feat/x")
})

const ago = (ms) => new Date(Date.now() - ms).toISOString()
const later = () => new Date(Date.now() + 60000).toISOString()

test("a removed worktree with a seen branch reaches 5 cells, and an unpushed checkout makes no gh call", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  Object.assign(h.facts, { branch: "feat/x", upstream: false, prs: { "feat/x": [{ state: "MERGED", title: "x", createdAt: later() }] } })
  await h.end()
  await h.end()
  assert.equal(h.calls.gh.length, 0)
  assert.equal(h.storage.data.get(KEY).task.seen, "feat/x")
  rmSync(h.root, { recursive: true, force: true })
  await h.end()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 5, false))
})

test("a PR opened before the bind is not credited through the seen branch", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  Object.assign(h.facts, { branch: "feat/x", upstream: false, prs: { "feat/x": [{ state: "MERGED", title: "old", createdAt: ago(60000) }] } })
  await h.end()
  rmSync(h.root, { recursive: true, force: true })
  await h.end()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, true))
})


test("the default branch is never remembered as the seen branch", async (t) => {
  const h = await host(t)
  await h.write(SPEC)
  Object.assign(h.facts, { branch: "main", upstream: true, prs: { main: [{ state: "MERGED", title: "x", createdAt: later() }] } })
  await h.end()
  assert.equal(h.storage.data.get(KEY).task.seen, null)
  rmSync(h.root, { recursive: true, force: true })
  await h.end()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 1, true))
})

test("with cleanup on, a detached present checkout shows 4 and reaches 5 once removed", async (t) => {
  const h = await host(t, { cleanup: true, project: join(tmpdir(), "session-primary-elsewhere") })
  await h.write(SPEC)
  Object.assign(h.facts, { branch: "feat/x", upstream: false })
  await h.end()
  Object.assign(h.facts, { branch: null, prs: { "feat/x": [{ state: "MERGED", title: "x", createdAt: later() }] } })
  await h.end()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 4, true))
  rmSync(h.root, { recursive: true, force: true })
  await h.end()
  assert.equal(h.titles.get("ses_1"), compose("Fix login flow", 5, false))
})

test("only the first same-topic plan in a checkout of the same repository adopts the task", async (t) => {
  const dirs = {}
  for (const name of ["wt", "third", "elsewhere"]) {
    dirs[name] = realpathSync(mkdtempSync(join(tmpdir(), `session-${name}-`)))
    t.after(() => rmSync(dirs[name], { recursive: true, force: true }))
  }
  const plan = (dir, name = "2026-11-20-fix-auth") => `${dir}/docs/superpowers/plans/${name}.md`
  const scenario = async (prepare) => {
    const h = await host(t)
    h.facts.common = { [h.root]: "/repo/.git", [dirs.wt]: "/repo/.git", [dirs.third]: "/repo/.git", [dirs.elsewhere]: "/other/.git" }
    await h.write(SPEC)
    Object.assign(h.facts, { branch: "feat/old", upstream: false })
    await h.end()
    await prepare(h)
    return h.storage.data.get(KEY).task
  }
  let task = await scenario(async (h) => { h.facts.branch = "feat/switched"; await h.write(plan(dirs.wt)) })
  assert.deepEqual([task.top, task.plan, task.remembered, task.seen], [dirs.wt, plan(dirs.wt), null, null])
  task = await scenario(async (h) => { await h.write(plan(dirs.wt)); await h.write(plan(dirs.third)) })
  assert.equal(task.top, dirs.wt, "a second plan does not move the task again")
  task = await scenario((h) => h.write(plan(dirs.wt, "2026-11-20-other-thing")))
  assert.deepEqual([task.plan, task.seen], [null, "feat/old"], "another topic")
  task = await scenario((h) => h.write(plan(dirs.elsewhere)))
  assert.equal(task.plan, null, "another repository")
  task = await scenario(async (h) => { delete h.facts.common[dirs.wt]; await h.write(plan(dirs.wt)) })
  assert.equal(task.plan, null, "a failed read")
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

test("completion drops its binding and task silence survives a restart until rebind", async (t) => {
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
  await again.end("ses_2")
  assert.equal(again.titles.get("ses_2"), "My own title")
  await again.write(OTHER, "ses_2")
  await again.end("ses_2")
  assert.equal(again.titles.get("ses_2"), "￻▰▱▱▱▱ WAITING · add cache")
})

test("a legacy taskless silence record cannot silence a new binding", async (t) => {
  const h = await host(t)
  h.storage.data.set(KEY, { location: h.root, at: 1, task: null, last: null, silenced: true })
  await h.write(SPEC)
  await h.end()
  assert.equal(h.titles.get("ses_1"), "￻▰▱▱▱▱ WAITING · Fix login flow")
  assert.equal(h.storage.data.get(KEY).silenced, false)
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
