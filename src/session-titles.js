import { randomUUID } from "node:crypto"
import { realpath } from "node:fs/promises"
import { resolve } from "node:path"
import { classify } from "./pr-advice.js"
import { artifact, branchOf, defaultRun, readFacts } from "./session-facts.js"

export const MARK = "\uFFFB"
export const PREFIX = /^\uFFFB?[▰▱]{5} (?:WAITING · )?/
export const DEFAULT_TITLE = /^New session - \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/

export const strip = (title) => title.replace(PREFIX, "")

// When this server process started; every run it starts is busy from a later time.
export const serverStart = () => Date.now() - process.uptime() * 1000

export const compose = (topic, cells, waiting) =>
  `${MARK}${"▰".repeat(cells)}${"▱".repeat(5 - cells)} ${waiting ? "WAITING · " : ""}${topic}`

export function cellsFor({ changed, declared, pr, exists, cleanupOn, primary }) {
  const superseded = pr?.state === "CLOSED" && pr.title.startsWith("[SUPERSEDED]")
  const cleaned = !cleanupOn || primary || !exists || superseded
  if ((pr?.state === "MERGED" || superseded) && cleaned) return 5
  const done = declared.filter((path) => changed.includes(path)).length
  if (pr || (declared.length > 0 && done === declared.length)) return 4
  if (declared.length > 0 && done >= Math.ceil(declared.length / 2)) return 3
  if (changed.some((path) => !path.startsWith("docs/"))) return 2
  return 1
}

const MAX_SESSIONS = 128
const MAX_HOPS = 5
const MAX_CHILDREN = 50
const MAX_PAGES = 4
const KEY = "session-titles/"
const ARTIFACT = /(?:^|\/)docs\/superpowers\/(?:specs|plans)\/[^/]+\.md$/
const SHELLS = new Set(["bash", "shell"])
const RECORDING = new Set(["push", "pr_create", "pr_merge"])
const TURNS = ["session.execution.started", "session.execution.succeeded", "session.execution.failed",
  "session.execution.interrupted"]
// A request the agent waits on mid-turn: [session id, request id, asked (true) or answered (false)].
const REQUESTS = {
  "permission.asked": (data) => [data?.sessionID, data?.id, true],
  "form.created": (data) => [data?.form?.sessionID, data?.form?.id, true],
  "permission.replied": (data) => [data?.sessionID, data?.requestID, false],
  "form.replied": (data) => [data?.sessionID, data?.id, false],
  "form.cancelled": (data) => [data?.sessionID, data?.id, false],
}
const MAX_PENDING = 16
const TYPES = [...TURNS, "session.moved", ...Object.keys(REQUESTS)]
const MAX_GENERATIONS = 256
const MAX_PARENTS = 1024

export async function registerSessionTitles(ctx, { settings, gitWorkflow, run = defaultRun, deadline = 3000, started = serverStart() }) {
  const sessions = new Map(), chains = new Map(), checked = new Set(), saved = new Map(), controller = new AbortController()
  const generations = new Map(), parents = new Map(), reals = new Map(), pending = new Map()
  const close = () => { controller.abort(); sessions.clear(); checked.clear(); saved.clear(); pending.clear() }
  const where = ctx.location?.directory ?? null, storage = where ? ctx.storage : undefined
  const project = typeof ctx.location?.project?.id === "string" ? ctx.location.project.id : null
  if (typeof ctx.tool?.hook !== "function" || typeof ctx.event?.subscribe !== "function") return { registered: false, close }
  const here = where ? await realpath(where).catch(() => where) : null
  const on = async () => !controller.signal.aborted && (await settings.inspect()).state === "on"

  // One plugin copy runs per folder and every copy receives every session's events, so only the copy for a session's
  // current folder owns it: it alone loads, writes and saves. A move makes every copy forget the session; a job whose
  // session was forgotten after it was queued (its generation changed) publishes and writes nothing.
  const generation = (sessionID) => generations.get(sessionID) ?? 0
  const current = (sessionID, admitted) => !controller.signal.aborted && generation(sessionID) === admitted
  const enqueue = (sessionID, job) => {
    const admitted = generation(sessionID)
    const next = (chains.get(sessionID) ?? Promise.resolve()).then(() => job(admitted)).catch(() => {})
    chains.set(sessionID, next)
    next.then(() => { if (chains.get(sessionID) === next) chains.delete(sessionID) })
    return next
  }
  function forget(sessionID) {
    sessions.delete(sessionID)
    checked.delete(sessionID)
    saved.delete(sessionID)
    const next = generation(sessionID) + 1
    generations.delete(sessionID)
    generations.set(sessionID, next)
    if (generations.size > MAX_GENERATIONS) generations.delete(generations.keys().next().value)
  }
  // A session read that also remembers the session's parent (or that it has none), so later walks need no read.
  async function read(sessionID) {
    const info = await ctx.session.get({ sessionID })
    parents.delete(sessionID)
    parents.set(sessionID, typeof info?.parentID === "string" ? info.parentID : null)
    if (parents.size > MAX_PARENTS) parents.delete(parents.keys().next().value)
    return info
  }
  // true or false, or undefined when the folder cannot be read (the caller then does nothing and retries later).
  async function owns(info) {
    if (!where) return true
    const dir = info?.location?.directory
    if (typeof dir !== "string") return undefined
    if (!reals.has(dir)) {
      if (reals.size > 256) reals.delete(reals.keys().next().value)
      reals.set(dir, await realpath(dir).catch(() => dir))
    }
    return reals.get(dir) === here
  }

  // The binding is kept in plugin storage so a server restart does not freeze the meter. The owner reads a record once
  // per server lifetime; it accepts one saved by any folder of its project (a record without a project, saved before
  // projects were recorded, only from its own folder). Any storage error leaves memory only.
  function keep(sessionID, entry) {
    sessions.delete(sessionID)
    sessions.set(sessionID, entry)
    if (sessions.size > MAX_SESSIONS) {
      const oldest = sessions.keys().next().value
      sessions.delete(oldest)
      checked.delete(oldest)
      saved.delete(oldest)
    }
  }

  const text = (value) => value === null || typeof value === "string"
  const finite = (value) => typeof value === "number" && Number.isFinite(value)
  const running = (time) => finite(time?.busy) && time.busy >= started && !(finite(time.idle) && time.idle >= time.busy)
  const instance = randomUUID().slice(0, 8)
  const STEPS = ["status", "diff", "plan", "pr"]
  const PR_STATES = ["OPEN", "MERGED", "CLOSED"]
  const short = (value) => value === null || (typeof value === "string" && value.length <= 200)
  const count = (value) => Number.isInteger(value) && value >= 0
  const KEYS = ["instance", "exists", "branch", "upstream", "head", "pr", "declared", "done", "errors"]
  // Only these fields, within these bounds, are kept: a stored record can never smuggle anything else into the next save.
  function diagOf(value) {
    if (!value || typeof value !== "object" || Object.keys(value).some((key) => !KEYS.includes(key))) return null
    const { instance: id, exists, branch, upstream, head, pr, declared, done, errors } = value
    if (typeof id !== "string" || !/^[a-z0-9]{1,16}$/.test(id) || typeof exists !== "boolean" || typeof upstream !== "boolean" ||
      ![branch, head].every(short) || !(pr === null || PR_STATES.includes(pr)) || !count(declared) || !count(done) || !Array.isArray(errors) || errors.length > 5 ||
      !errors.every((error) => error && Object.keys(error).length === 2 && STEPS.includes(error.step) &&
        typeof error.code === "string" && /^[\w:]{1,40}$/.test(error.code))) return null
    return { instance: id, exists, branch, upstream, head, pr, declared, done, errors: errors.map(({ step, code }) => ({ step, code })) }
  }
  function restore(task) {
    if (!task || typeof task.top !== "string" || !Number.isInteger(task.cells) || task.cells < 1 || task.cells > 4 ||
      typeof task.slug !== "string" || typeof task.fresh !== "boolean" || ![task.spec, task.plan, task.remembered].every(text) ||
      !(task.spec || task.plan) || !(task.seen === undefined || short(task.seen)) ||
      !(task.bound === undefined || task.bound === null || finite(task.bound))) return null
    const { top, spec, plan, remembered, cells, slug, fresh } = task
    return { top, spec, plan, remembered, cells, slug, fresh, seen: task.seen ?? null, bound: task.bound ?? null }
  }

  // `known` is a session read the caller already made, so loading costs no second read.
  async function load(sessionID, admitted, known) {
    if (sessions.has(sessionID) || checked.has(sessionID)) return
    let mine
    try { mine = await owns(known ?? await read(sessionID)) } catch { return }
    if (mine === undefined || !current(sessionID, admitted)) return
    checked.add(sessionID)
    if (checked.size > MAX_SESSIONS * 8) checked.delete(checked.values().next().value)
    if (!mine) return
    try {
      const record = await storage?.get(KEY + sessionID)
      if (!record || (typeof record.project === "string" ? record.project !== project : record.location !== where)) return
      const entry = { task: restore(record.task), last: typeof record.last === "string" ? record.last : null,
        silenced: record.silenced === true, diag: diagOf(record.diag) }
      if ((!entry.task && !entry.silenced) || !current(sessionID, admitted)) return
      keep(sessionID, entry)
      saved.set(sessionID, JSON.stringify(entry))
    } catch {}
  }

  // A failed remove can leave a finished task behind; it is only read again after the session leaves memory, and a
  // finished task then shows its last meter again until the next spec write. The 128-record cap is shared by projects.
  // Every caller saves only an entry that is still this copy's memory of the session; a move or close removes it, so no
  // save starts for a forgotten session.
  async function save(sessionID, entry) {
    try {
      if (!storage) return
      const snapshot = JSON.stringify(entry), known = saved.has(sessionID)
      if (saved.get(sessionID) === snapshot) return
      if (!entry.task && !entry.silenced) {
        await storage.remove(KEY + sessionID)
        saved.delete(sessionID)
        return
      }
      await storage.set(KEY + sessionID, { location: where, project, at: Date.now(), task: entry.task, last: entry.last,
        silenced: entry.silenced, diag: entry.diag ?? null })
      saved.set(sessionID, snapshot)
      if (known) return
      const entries = []
      let page = await storage.scan({ prefix: KEY })
      while (page) {
        entries.push(...(page.entries ?? []))
        page = page.next ? await storage.scan({ prefix: KEY, after: page.next }) : null
      }
      entries.sort((a, b) => (a.value?.at ?? 0) - (b.value?.at ?? 0))
      for (const { key } of entries.slice(0, Math.max(0, entries.length - MAX_SESSIONS))) await storage.remove(key)
    } catch {}
  }

  // A spec or plan write. A child's write binds its root; the bind runs on the root's queue so it cannot race the root's
  // own evaluation. `directory` is the folder of the session that wrote, which a relative path resolves against.
  async function route(sessionID, path, admitted) {
    if (!await on()) return
    const info = await read(sessionID)
    const directory = info?.location?.directory
    if (typeof directory !== "string") return
    if (typeof info.parentID !== "string") {
      if (await owns(info) === true) await bind(sessionID, path, directory, admitted, info)
      return
    }
    const root = await rootOf(sessionID)
    if (!root || root === sessionID) return
    const rootInfo = await read(root)
    if (await owns(rootInfo) !== true) return
    await enqueue(root, (rootAdmitted) => bind(root, path, directory, rootAdmitted, rootInfo))
  }

  async function bind(sessionID, path, directory, admitted, known) {
    const absolute = resolve(directory, path), found = artifact(absolute)
    if (!found) return
    const top = await realpath(found.top).catch(() => found.top)
    const file = top + absolute.slice(found.top.length)
    await load(sessionID, admitted, known)
    if (!current(sessionID, admitted)) return
    const entry = sessions.get(sessionID) ?? { task: null, last: null, silenced: false, diag: null }
    const task = { top, spec: null, plan: null, remembered: null, cells: 1, slug: found.slug, fresh: true, seen: null,
      bound: Date.now() }
    if (found.kind === "spec" && entry.task?.spec !== file) entry.task = { ...task, spec: file }
    else if (found.kind === "plan" && !entry.task) entry.task = { ...task, plan: file }
    else if (found.kind === "plan" && entry.task.top === top) entry.task.plan = file
    keep(sessionID, entry)
    await save(sessionID, entry)
  }

  // The top of a session's parent chain; itself when it has no parent, null when a read fails or the chain is too long.
  async function rootOf(sessionID) {
    try {
      let id = sessionID
      for (let hops = 0; hops <= MAX_HOPS; hops++) {
        const parentID = parents.has(id) ? parents.get(id) : (await read(id), parents.get(id))
        if (typeof parentID !== "string") return id
        id = parentID
      }
    } catch {}
    return null
  }

  // Whether a child of the root, or with `withSelf` the root itself, is running now: busy since this server process
  // started and not idle since. A run killed by a restart keeps an old busy time and reads as not running; a long tool
  // call, which moves no timestamp, still reads as running. `ended` ({ id, at }) is a session whose end event arrived
  // at `at`: that run counts as idle whatever the host still says, but a run that began after `at` does not. The two
  // reads are independent and share one deadline of `deadline` ms; what has not answered by then, or failed, counts as
  // "nothing runs", so a stuck host call cannot hold the session's queue.
  async function activity(rootID, { ended = null, withSelf = false } = {}) {
    const childRuns = async () => {
      let page = await ctx.session.list({ parentID: rootID, limit: MAX_CHILDREN, order: "desc" })
      for (let pages = 1; ; pages++) {
        const items = Array.isArray(page) ? page : page?.data
        if (Array.isArray(items) && items.some((info) => typeof info?.id === "string" && running(info?.time) &&
          !(ended && info.id === ended.id && info.time.busy <= ended.at))) return true
        const next = page?.cursor?.next
        if (typeof next !== "string" || pages >= MAX_PAGES) return false
        page = await ctx.session.list({ cursor: next, limit: MAX_CHILDREN })
      }
    }
    const rootRuns = async () => withSelf && running((await ctx.session.get({ sessionID: rootID })).time)
    const result = { children: false, self: false }
    let timer
    await Promise.race([
      Promise.all([childRuns().then((value) => { result.children = value }, () => {}),
        rootRuns().then((value) => { result.self = value }, () => {})]),
      new Promise((done) => { timer = setTimeout(done, deadline) }),
    ])
    clearTimeout(timer)
    return { ...result }
  }

  // At a push, PR create or PR merge the checkout still has its branch; after cleanup only this record can find the PR.
  async function record(sessionID, admitted) {
    if (!await on()) return
    const info = await read(sessionID)
    if (info.parentID || await owns(info) !== true) return
    await load(sessionID, admitted, info)
    const entry = sessions.get(sessionID), task = entry?.task
    if (!current(sessionID, admitted) || !task || entry.silenced) return
    const branch = await branchOf({ top: task.top, run })
    if (!branch || task.remembered === branch || sessions.get(sessionID) !== entry || entry.task !== task) return
    task.remembered = branch
    await save(sessionID, entry)
  }

  // A session below a bound root ended: the root may now be idle with nothing running, so it re-evaluates its label.
  // `created` is the end event's own time: a late delivery must not push the cutoff past a run that restarted since.
  // It is used only when it is within 5 minutes of now, so a different unit or clock can never hide the label.
  async function wakeRoot(sessionID, created) {
    const received = Date.now()
    const at = typeof created === "number" && Math.abs(created - received) <= 5 * 60 * 1000 ? created : received
    if (parents.get(sessionID) === null || !await on()) return
    const root = await rootOf(sessionID)
    if (root && root !== sessionID) await enqueue(root, (admitted) => evaluate(root, true, { id: sessionID, at }, admitted))
  }

  // Requests are tracked only for roots bound in this copy's memory, so other sessions cost nothing. A reply removes only
  // an id it knows; the label clears when none is left and the session is still running, so a stray reply never
  // removes an idle session's WAITING.
  function request(sessionID, id, asked) {
    if (typeof sessionID !== "string" || typeof id !== "string" || !sessions.get(sessionID)?.task) return
    const open = pending.get(sessionID) ?? new Set()
    if (asked) {
      if (open.has(id) || open.size >= MAX_PENDING) return
      pending.set(sessionID, open.add(id))
      enqueue(sessionID, (admitted) => relabel(sessionID, true, admitted, { still: () => pending.get(sessionID)?.size > 0 }))
    } else if (open.delete(id) && !open.size) {
      pending.delete(sessionID)
      enqueue(sessionID, (admitted) => relabel(sessionID, false, admitted, { running: true, still: () => !pending.get(sessionID)?.size }))
    }
  }

  // Sets or clears WAITING on the meter as it stands, with no git or gh read. Never while the task is fresh: before its
  // first meter (OpenCode titles a new session during its first turn) or when it was bound in the current turn.
  // `running`: only while the session runs. `still()`: re-checked right before the write, so a request answered (or a
  // new one asked) while this job waited on its reads leaves no flash.
  async function relabel(sessionID, waiting, admitted, { running: whileRunning = false, still = () => true } = {}) {
    if (!await on()) return
    const entry = sessions.get(sessionID), task = entry?.task
    if (!task || entry.silenced || task.fresh) return
    const info = await read(sessionID)
    if (!current(sessionID, admitted) || await owns(info) !== true || (whileRunning && !running(info.time)) || !still()) return
    const title = info.title ?? ""
    if (!PREFIX.test(title)) { entry.silenced = true; await save(sessionID, entry); return }
    const next = compose(strip(title) || task.slug, task.cells, waiting)
    if (next !== title) await ctx.session.update({ sessionID, title: next })
    if (sessions.get(sessionID) !== entry || entry.task !== task) return
    entry.last = next
    await save(sessionID, entry)
  }

  async function evaluate(sessionID, ended, fromChild, admitted) {
    if (!sessions.has(sessionID)) {
      if (checked.has(sessionID) || !await on()) return
      await load(sessionID, admitted)
    }
    const entry = sessions.get(sessionID), task = entry?.task
    if (!task || entry.silenced || (!ended && task.fresh) || !await on()) return
    if (fromChild) {
      const now = await activity(sessionID, { ended: fromChild, withSelf: true })
      if (now.children || now.self) return
    }
    let cells = task.cells
    if (ended) {
      const { state, project = null, worktree_cleanup } = await gitWorkflow.inspect()
      const facts = await readFacts({ top: task.top, plan: task.plan, remembered: task.remembered,
        fallback: project ?? task.top, run })
      if (facts.head && facts.head === facts.branch) task.remembered = facts.head
      const clip = (value) => typeof value === "string" ? value.slice(0, 200) : null
      entry.diag = { instance, exists: facts.exists, branch: clip(facts.branch), upstream: facts.upstream, head: clip(facts.head),
        pr: PR_STATES.includes(facts.pr?.state) ? facts.pr.state : null, declared: facts.declared.length,
        done: facts.declared.filter((path) => facts.changed.includes(path)).length, errors: facts.errors }
      const pr = facts.pr && { state: facts.pr.state, title: typeof facts.pr.title === "string" ? facts.pr.title : "" }
      cells = Math.max(cells, cellsFor({ ...facts, pr, cleanupOn: state === "on" && worktree_cleanup === true, primary: task.top === project }))
    }
    const busy = ended ? await activity(sessionID, { ended: fromChild, withSelf: Boolean(fromChild) }) : null
    const info = await read(sessionID)
    if (!current(sessionID, admitted) || (fromChild && busy.self) || await owns(info) !== true) return
    const title = info.title ?? ""
    if (entry.last !== null && !PREFIX.test(title)) { entry.silenced = true; await save(sessionID, entry); return }
    const kept = strip(title)
    const topic = !kept || DEFAULT_TITLE.test(kept) || (task.fresh && title === entry.last) ? task.slug : kept
    const next = compose(topic, cells, ended && cells < 5 && !busy.children)
    if (next !== title) await ctx.session.update({ sessionID, title: next })
    if (sessions.get(sessionID) !== entry || entry.task !== task) return
    Object.assign(task, { cells, fresh: false })
    entry.last = next
    if (cells === 5) entry.task = null
    await save(sessionID, entry)
  }

  try {
    const stream = await ctx.event.subscribe({ types: TYPES, signal: controller.signal })
    await ctx.tool.hook("execute.after", async (event) => {
      try {
        if (event.status !== "completed" || typeof event.sessionID !== "string") return
        const input = event.input
        if (event.tool === "write") {
          if (typeof input?.path === "string" && ARTIFACT.test(input.path)) await enqueue(event.sessionID, (admitted) => route(event.sessionID, input.path, admitted))
        } else if (SHELLS.has(event.tool) && typeof input?.command === "string" &&
          classify(input.command).some(({ kind }) => RECORDING.has(kind))) {
          await enqueue(event.sessionID, (admitted) => record(event.sessionID, admitted))
        }
      } catch {}
    })
    ;(async () => {
      try {
        for await (const event of stream) {
          if (REQUESTS[event?.type]) { request(...REQUESTS[event.type](event.data)); continue }
          const sessionID = event?.data?.sessionID
          if (typeof sessionID !== "string") continue
          if (event.type === "session.moved") forget(sessionID)
          else if (TURNS.includes(event.type)) {
            // A blocking request cannot outlive its session's own turn boundary; this also drops a missed reply.
            pending.delete(sessionID)
            enqueue(sessionID, (admitted) => evaluate(sessionID, event.type !== TURNS[0], null, admitted))
            if (event.type !== TURNS[0] && !sessions.has(sessionID)) wakeRoot(sessionID, event.created).catch(() => {})
          }
        }
      } catch {}
    })()
  } catch { close(); return { registered: false, close } }
  return { registered: true, close }
}
