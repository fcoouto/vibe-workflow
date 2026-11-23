import { randomUUID } from "node:crypto"
import { realpath } from "node:fs/promises"
import { resolve } from "node:path"
import { classify } from "./pr-advice.js"
import { artifact, branchOf, defaultRun, readFacts } from "./session-facts.js"

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
const MAX_HOPS = 5
const MAX_CHILDREN = 50
const MAX_PAGES = 4
const STALE_MS = 10 * 60 * 1000
const KEY = "session-titles/"
const ARTIFACT = /(?:^|\/)docs\/superpowers\/(?:specs|plans)\/[^/]+\.md$/
const SHELLS = new Set(["bash", "shell"])
const RECORDING = new Set(["push", "pr_create", "pr_merge"])
const TYPES = ["session.execution.started", "session.execution.succeeded", "session.execution.failed",
  "session.execution.interrupted"]

export async function registerSessionTitles(ctx, { settings, gitWorkflow, run = defaultRun, deadline = 3000 }) {
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

  // The top of a session's parent chain; itself when it has no parent, null when a read fails or the chain is too long.
  async function rootOf(sessionID) {
    try {
      let id = sessionID
      for (let hops = 0; hops <= MAX_HOPS; hops++) {
        const { parentID } = await ctx.session.get({ sessionID: id })
        if (typeof parentID !== "string") return id
        id = parentID
      }
    } catch {}
    return null
  }

  // Whether a child of the root, or with `withSelf` the root itself, is running now. A session runs when it became busy
  // and has not gone idle since; the age rule (newest of `updated` and `busy`) keeps a busy mark left by a crash from
  // hiding the label for good. `ended` ({ id, at }) is a session whose end event arrived at `at`: that run counts as
  // idle whatever the host still says, but a run that began after `at` does not. The two reads are independent and
  // share one deadline of `deadline` ms; what has not answered by then, or failed, counts as "nothing runs", so a stuck
  // host call cannot hold the session's queue.
  async function activity(rootID, { ended = null, withSelf = false } = {}) {
    const finite = (value) => typeof value === "number" && Number.isFinite(value)
    const running = (info, now) => {
      const time = info?.time
      if (!finite(time?.busy) || (finite(time.idle) && time.idle >= time.busy)) return false
      return now - Math.max(time.busy, finite(time.updated) ? time.updated : 0) < STALE_MS
    }
    const childRuns = async () => {
      const now = Date.now()
      let page = await ctx.session.list({ parentID: rootID, limit: MAX_CHILDREN, order: "desc" })
      for (let pages = 1; ; pages++) {
        const items = Array.isArray(page) ? page : page?.data
        if (Array.isArray(items) && items.some((info) => typeof info?.id === "string" && running(info, now) &&
          !(ended && info.id === ended.id && info.time.busy <= ended.at))) return true
        const next = page?.cursor?.next
        if (typeof next !== "string" || pages >= MAX_PAGES) return false
        page = await ctx.session.list({ cursor: next, limit: MAX_CHILDREN })
      }
    }
    const rootRuns = async () => withSelf && running(await ctx.session.get({ sessionID: rootID }), Date.now())
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
  async function record(sessionID) {
    if (!await on()) return
    await load(sessionID)
    const entry = sessions.get(sessionID), task = entry?.task
    if (controller.signal.aborted || !task || entry.silenced || (await ctx.session.get({ sessionID })).parentID) return
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
    if (!await on()) return
    const root = await rootOf(sessionID)
    if (root && root !== sessionID) await enqueue(root, () => evaluate(root, true, { id: sessionID, at }))
  }

  async function evaluate(sessionID, ended, fromChild = null) {
    if (!sessions.has(sessionID)) {
      if (checked.has(sessionID) || !await on()) return
      await load(sessionID)
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
    const title = (await ctx.session.get({ sessionID })).title ?? ""
    if (controller.signal.aborted || (fromChild && busy.self)) return
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
          if (typeof input?.path === "string" && ARTIFACT.test(input.path)) await enqueue(event.sessionID, () => bind(event.sessionID, input.path))
        } else if (SHELLS.has(event.tool) && typeof input?.command === "string" &&
          classify(input.command).some(({ kind }) => RECORDING.has(kind))) {
          await enqueue(event.sessionID, () => record(event.sessionID))
        }
      } catch {}
    })
    ;(async () => {
      try {
        for await (const event of stream) {
          const sessionID = event?.data?.sessionID
          if (typeof sessionID === "string" && TYPES.includes(event.type)) {
            enqueue(sessionID, () => evaluate(sessionID, event.type !== TYPES[0]))
            if (event.type !== TYPES[0] && !sessions.has(sessionID)) wakeRoot(sessionID, event.created).catch(() => {})
          }
        }
      } catch {}
    })()
  } catch { close(); return { registered: false, close } }
  return { registered: true, close }
}
