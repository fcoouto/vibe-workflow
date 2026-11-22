import { realpath } from "node:fs/promises"
import { resolve } from "node:path"
import { artifact, defaultRun, readFacts } from "./session-facts.js"

export const MARK = "\uFFFB"
export const PREFIX = /^\uFFFB?[▰▱]{5} (?:WAITING · )?/
export const DEFAULT_TITLE = /^New session - \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/

export const strip = (title) => title.replace(PREFIX, "")

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
const KEY = "session-titles/"
const ARTIFACT = /(?:^|\/)docs\/superpowers\/(?:specs|plans)\/[^/]+\.md$/
const TYPES = ["session.execution.started", "session.execution.succeeded", "session.execution.failed",
  "session.execution.interrupted"]

export async function registerSessionTitles(ctx, { settings, gitWorkflow, run = defaultRun }) {
  const sessions = new Map(), chains = new Map(), checked = new Set(), saved = new Map(), controller = new AbortController()
  const close = () => { controller.abort(); sessions.clear(); checked.clear(); saved.clear() }
  const where = ctx.location?.directory ?? null, storage = where ? ctx.storage : undefined
  if (typeof ctx.tool?.hook !== "function" || typeof ctx.event?.subscribe !== "function") return { registered: false, close }
  const on = async () => !controller.signal.aborted && (await settings.inspect()).state === "on"
  const enqueue = (sessionID, job) => {
    const next = (chains.get(sessionID) ?? Promise.resolve()).then(job).catch(() => {})
    chains.set(sessionID, next)
    next.then(() => { if (chains.get(sessionID) === next) chains.delete(sessionID) })
    return next
  }

  // The binding is kept in plugin storage so a server restart does not freeze the meter. A record is read once per
  // server lifetime and only by the instance of the project that saved it; any storage error leaves memory only.
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
  const instance = Math.random().toString(36).slice(2, 10)
  const STEPS = ["status", "diff", "plan", "pr"]
  // Only these fields are kept, so a stored record can never smuggle anything else into the next save.
  function diagOf(value) {
    if (!value || typeof value !== "object") return null
    const { instance: id, exists, branch, upstream, head, pr, errors } = value
    if (typeof id !== "string" || typeof exists !== "boolean" || typeof upstream !== "boolean" || ![branch, head, pr].every(text) ||
      !Array.isArray(errors) || !errors.every((error) => error && STEPS.includes(error.step) && typeof error.code === "string")) return null
    return { instance: id, exists, branch, upstream, head, pr, errors: errors.map(({ step, code }) => ({ step, code })) }
  }
  function restore(task) {
    if (!task || typeof task.top !== "string" || !Number.isInteger(task.cells) || task.cells < 1 || task.cells > 4 ||
      typeof task.slug !== "string" || typeof task.fresh !== "boolean" || ![task.spec, task.plan, task.remembered].every(text) ||
      !(task.spec || task.plan)) return null
    const { top, spec, plan, remembered, cells, slug, fresh } = task
    return { top, spec, plan, remembered, cells, slug, fresh }
  }

  async function load(sessionID) {
    if (sessions.has(sessionID) || checked.has(sessionID)) return
    checked.add(sessionID)
    if (checked.size > MAX_SESSIONS * 8) checked.delete(checked.values().next().value)
    try {
      const record = await storage?.get(KEY + sessionID)
      if (record?.location !== where) return
      const entry = { task: restore(record.task), last: typeof record.last === "string" ? record.last : null,
        silenced: record.silenced === true, diag: diagOf(record.diag) }
      if (!entry.task && !entry.silenced) return
      keep(sessionID, entry)
      saved.set(sessionID, JSON.stringify(entry))
    } catch {}
  }

  // A failed remove can leave a finished task behind; it is only read again after the session leaves memory, and a
  // finished task then shows its last meter again until the next spec write. The 128-record cap is shared by projects.
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
      await storage.set(KEY + sessionID, { location: where, at: Date.now(), task: entry.task, last: entry.last,
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

  async function bind(sessionID, path) {
    if (!await on()) return
    const session = await ctx.session.get({ sessionID })
    if (session.parentID) return
    const absolute = resolve(session.location.directory, path), found = artifact(absolute)
    if (!found) return
    const top = await realpath(found.top).catch(() => found.top)
    const file = top + absolute.slice(found.top.length)
    await load(sessionID)
    const entry = sessions.get(sessionID) ?? { task: null, last: null, silenced: false, diag: null }
    const task = { top, spec: null, plan: null, remembered: null, cells: 1, slug: found.slug, fresh: true }
    if (found.kind === "spec" && entry.task?.spec !== file) entry.task = { ...task, spec: file }
    else if (found.kind === "plan" && !entry.task) entry.task = { ...task, plan: file }
    else if (found.kind === "plan" && entry.task.top === top) entry.task.plan = file
    keep(sessionID, entry)
    await save(sessionID, entry)
  }

  async function evaluate(sessionID, ended) {
    if (!sessions.has(sessionID)) {
      if (checked.has(sessionID) || !await on()) return
      await load(sessionID)
    }
    const entry = sessions.get(sessionID), task = entry?.task
    if (!task || entry.silenced || (!ended && task.fresh) || !await on()) return
    let cells = task.cells
    if (ended) {
      const { state, project = null, worktree_cleanup } = await gitWorkflow.inspect()
      const facts = await readFacts({ top: task.top, plan: task.plan, remembered: task.remembered,
        fallback: project ?? task.top, run })
      if (facts.head && facts.head === facts.branch) task.remembered = facts.head
      entry.diag = { instance, exists: facts.exists, branch: facts.branch, upstream: facts.upstream, head: facts.head,
        pr: facts.pr ? facts.pr.state : null, errors: facts.errors }
      const pr = facts.pr && { state: facts.pr.state, title: typeof facts.pr.title === "string" ? facts.pr.title : "" }
      cells = Math.max(cells, cellsFor({ ...facts, pr, cleanupOn: state === "on" && worktree_cleanup === true, primary: task.top === project }))
    }
    const title = (await ctx.session.get({ sessionID })).title ?? ""
    if (entry.last !== null && !PREFIX.test(title)) { entry.silenced = true; await save(sessionID, entry); return }
    const kept = strip(title)
    const topic = !kept || DEFAULT_TITLE.test(kept) || (task.fresh && title === entry.last) ? task.slug : kept
    const next = compose(topic, cells, ended && cells < 5)
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
        const path = event?.input?.path
        if (event.tool !== "write" || event.status !== "completed" || typeof event.sessionID !== "string"
          || typeof path !== "string" || !ARTIFACT.test(path)) return
        await enqueue(event.sessionID, () => bind(event.sessionID, path))
      } catch {}
    })
    ;(async () => {
      try {
        for await (const event of stream) {
          const sessionID = event?.data?.sessionID
          if (typeof sessionID === "string" && TYPES.includes(event.type)) {
            enqueue(sessionID, () => evaluate(sessionID, event.type !== TYPES[0]))
          }
        }
      } catch {}
    })()
  } catch { close(); return { registered: false, close } }
  return { registered: true, close }
}
