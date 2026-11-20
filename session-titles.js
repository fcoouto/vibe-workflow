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
const ARTIFACT = /(?:^|\/)docs\/superpowers\/(?:specs|plans)\/[^/]+\.md$/
const TYPES = ["session.execution.started", "session.execution.succeeded", "session.execution.failed",
  "session.execution.interrupted"]

export async function registerSessionTitles(ctx, { settings, gitWorkflow, run = defaultRun }) {
  const sessions = new Map(), chains = new Map(), controller = new AbortController()
  const close = () => { controller.abort(); sessions.clear() }
  if (typeof ctx.tool?.hook !== "function" || typeof ctx.event?.subscribe !== "function") return { registered: false, close }
  const on = async () => !controller.signal.aborted && (await settings.inspect()).state === "on"
  const enqueue = (sessionID, job) => {
    const next = (chains.get(sessionID) ?? Promise.resolve()).then(job).catch(() => {})
    chains.set(sessionID, next)
    next.then(() => { if (chains.get(sessionID) === next) chains.delete(sessionID) })
    return next
  }

  async function bind(sessionID, path) {
    if (!await on()) return
    const session = await ctx.session.get({ sessionID })
    if (session.parentID) return
    const absolute = resolve(session.location.directory, path), found = artifact(absolute)
    if (!found) return
    const top = await realpath(found.top).catch(() => found.top)
    const file = top + absolute.slice(found.top.length)
    const entry = sessions.get(sessionID) ?? { task: null, last: null, silenced: false }
    const task = { top, spec: null, plan: null, remembered: null, cells: 1, slug: found.slug, fresh: true }
    if (found.kind === "spec" && entry.task?.spec !== file) entry.task = { ...task, spec: file }
    else if (found.kind === "plan" && !entry.task) entry.task = { ...task, plan: file }
    else if (found.kind === "plan" && entry.task.top === top) entry.task.plan = file
    sessions.delete(sessionID)
    sessions.set(sessionID, entry)
    if (sessions.size > MAX_SESSIONS) sessions.delete(sessions.keys().next().value)
  }

  async function evaluate(sessionID, ended) {
    const entry = sessions.get(sessionID), task = entry?.task
    if (!task || entry.silenced || (!ended && task.fresh) || !await on()) return
    let cells = task.cells
    if (ended) {
      const { state, project = null, worktree_cleanup } = await gitWorkflow.inspect()
      const facts = await readFacts({ top: task.top, plan: task.plan, remembered: task.remembered,
        fallback: project ?? task.top, run })
      if (facts.head && facts.head === facts.branch) task.remembered = facts.head
      const pr = facts.pr && { state: facts.pr.state, title: typeof facts.pr.title === "string" ? facts.pr.title : "" }
      cells = Math.max(cells, cellsFor({ ...facts, pr, cleanupOn: state === "on" && worktree_cleanup === true, primary: task.top === project }))
    }
    const title = (await ctx.session.get({ sessionID })).title ?? ""
    if (entry.last !== null && !PREFIX.test(title)) { entry.silenced = true; return }
    const kept = strip(title)
    const topic = !kept || DEFAULT_TITLE.test(kept) || (task.fresh && title === entry.last) ? task.slug : kept
    const next = compose(topic, cells, ended && cells < 5)
    if (next !== title) await ctx.session.update({ sessionID, title: next })
    if (sessions.get(sessionID) !== entry || entry.task !== task) return
    Object.assign(task, { cells, fresh: false })
    entry.last = next
    if (cells === 5) entry.task = null
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
