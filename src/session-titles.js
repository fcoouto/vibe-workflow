import { randomUUID } from "node:crypto"
import { realpath } from "node:fs/promises"
import { isAbsolute, relative, resolve } from "node:path"
import { classify } from "./pr-advice.js"
import { artifact, branchOf, commonDir, defaultRun, readFacts, readPlanPaths } from "./session-facts.js"

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
const MAX_SHELLS = 64
const SHELL_EXIT_GRACE_MS = 200
const JOB = "experimental.job.updated"
const STEP = "session.step.ended"
const TYPES = [...TURNS, "session.moved", ...Object.keys(REQUESTS), "shell.exited", STEP, JOB, "session.deleted"]
const MAX_GENERATIONS = 256
const MAX_PARENTS = 1024
// OpenCode evaluates plugin modules separately per location in the same server realm.
const PREFERENCES = Symbol.for("vibe-workflow.session-titles.preferences.v1")

export async function registerSessionTitles(ctx, { settings, gitWorkflow, run = defaultRun, deadline = 3000, started = serverStart() }) {
  const sessions = new Map(), chains = new Map(), checked = new Set(), saved = new Map(), controller = new AbortController()
  const generations = new Map(), parents = new Map(), reals = new Map(), pending = new Map(), shells = new Map()
  const exited = new Set(), deleted = new Set(), endedShellOwners = new WeakMap()
  const shellGraces = new Map(), shellRoots = new Map(), shellStarts = new Map(), taskRevisions = new WeakMap()
  let taskRevision = 0
  const progress = new WeakMap(), planVersions = new WeakMap(), sentTitles = new WeakMap()
  const where = ctx.location?.directory ?? null, storage = where ? ctx.storage : undefined
  const project = typeof ctx.location?.project?.id === "string" ? ctx.location.project.id : null
  let preference = { revision: 0, copies: 0 }, leave = () => {}
  const close = () => {
    controller.abort()
    for (const id of shellGraces.keys()) cancelShellGrace(id)
    leave(); sessions.clear(); checked.clear(); saved.clear(); pending.clear(); shells.clear()
    shellStarts.clear(); shellRoots.clear(); exited.clear(); deleted.clear()
  }
  const unavailable = { registered: false, close, preferenceSaved: () => {} }
  if (typeof ctx.tool?.hook !== "function" || typeof ctx.event?.subscribe !== "function") return unavailable
  const here = where ? await realpath(where).catch(() => where) : null
  const on = async () => !controller.signal.aborted && (await settings.inspect()).state === "on"

  // One plugin copy runs per folder and every copy receives every session's events, so only the copy for a session's
  // current folder owns it: it alone loads, writes and saves. A move makes every copy forget the session; a job whose
  // session was forgotten after it was queued (its generation changed) publishes and writes nothing.
  const generation = (sessionID) => generations.get(sessionID) ?? 0
  const admit = (sessionID, revision = preference.revision) => ({ generation: generation(sessionID), revision })
  const current = (sessionID, admitted) => !controller.signal.aborted && !deleted.has(sessionID) &&
    generation(sessionID) === admitted.generation &&
    preference.revision === admitted.revision
  const enqueue = (sessionID, job, admitted = admit(sessionID)) => {
    const grace = shellGraces.get(sessionID)
    if (grace) grace.queued++
    const next = (chains.get(sessionID) ?? Promise.resolve()).then(() => {
      if (current(sessionID, admitted)) return job(admitted)
    }).catch(() => {})
    chains.set(sessionID, next)
    next.then(() => { if (chains.get(sessionID) === next) chains.delete(sessionID) })
    return next
  }
  function forget(sessionID) {
    cancelShellGrace(sessionID)
    sessions.delete(sessionID)
    checked.delete(sessionID)
    saved.delete(sessionID)
    pending.delete(sessionID)
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
  // true or false, or undefined when the folder cannot be resolved (the caller then does nothing and retries later).
  // Only successful resolutions are cached; this copy's own folder still matches by name once it is gone.
  async function owns(info) {
    if (!where) return true
    const dir = info?.location?.directory
    if (typeof dir !== "string") return undefined
    if (!reals.has(dir)) {
      const real = await realpath(dir).catch(() => null)
      if (real === null) return dir === where ? true : undefined
      if (reals.size > 256) reals.delete(reals.keys().next().value)
      reals.set(dir, real)
    }
    return reals.get(dir) === here
  }

  // The binding is kept in plugin storage so a server restart does not freeze the meter. The owner reads a record once
  // per server lifetime; it accepts one saved by any folder of its project (a record without a project, saved before
  // projects were recorded, only from its own folder). Any storage error leaves memory only.
  function keep(sessionID, entry) {
    for (const shell of shells.values()) if (shell.root === sessionID && !shell.entry) shell.entry = entry
    sessions.delete(sessionID)
    sessions.set(sessionID, entry)
    if (sessions.size > MAX_SESSIONS) {
      const oldest = sessions.keys().next().value
      cancelShellGrace(oldest)
      sessions.delete(oldest)
      checked.delete(oldest)
      saved.delete(oldest)
      pending.delete(oldest)
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
      !(task.bound === undefined || task.bound === null || finite(task.bound)) ||
      !(task.resetFrom === undefined || text(task.resetFrom))) return null
    const { top, spec, plan, remembered, cells, slug, fresh } = task
    return { top, spec, plan, remembered, cells, slug, fresh, seen: task.seen ?? null, bound: task.bound ?? null,
      ...(task.resetFrom != null && { resetFrom: task.resetFrom }) }
  }

  // `known` is a session read the caller already made, so loading costs no second read.
  async function load(sessionID, admitted, known) {
    if (sessions.has(sessionID) || checked.has(sessionID)) return
    let mine
    try { mine = await owns(known ?? await read(sessionID)) } catch { return }
    if (mine === undefined || !current(sessionID, admitted)) return
    let record
    try { if (mine) record = await storage?.get(KEY + sessionID) } catch {}
    if (!current(sessionID, admitted)) return
    checked.add(sessionID)
    if (checked.size > MAX_SESSIONS * 8) checked.delete(checked.values().next().value)
    try {
      if (!record || (typeof record.project === "string" ? record.project !== project : record.location !== where)) return
      const entry = { task: restore(record.task), last: typeof record.last === "string" ? record.last : null,
        silenced: record.silenced === true, diag: diagOf(record.diag) }
      if (!entry.task || !current(sessionID, admitted)) return
      keep(sessionID, entry)
      saved.set(sessionID, JSON.stringify(entry))
    } catch {}
  }

  // A failed remove can leave a finished task behind; it is only read again after the session leaves memory, and a
  // finished task then shows its last meter again until the next spec write. The 128-record cap is shared by projects.
  // Every caller saves only an entry that is still this copy's memory of the session; a move or close removes it, so no
  // save starts for a forgotten session.
  async function save(sessionID, entry, admitted) {
    const live = () => current(sessionID, admitted) && sessions.get(sessionID) === entry
    try {
      if (!storage || !live()) return
      const snapshot = JSON.stringify(entry), known = saved.has(sessionID)
      if (saved.get(sessionID) === snapshot) return
      if (!entry.task) {
        await storage.remove(KEY + sessionID)
        if (live()) saved.delete(sessionID)
        return
      }
      await storage.set(KEY + sessionID, { location: where, project, at: Date.now(), task: entry.task, last: entry.last,
        silenced: entry.silenced, diag: entry.diag ?? null })
      if (!live()) return
      saved.set(sessionID, snapshot)
      if (known) return
      const entries = []
      let page = await storage.scan({ prefix: KEY })
      while (page) {
        if (!live()) return
        entries.push(...(page.entries ?? []))
        page = page.next ? await storage.scan({ prefix: KEY, after: page.next }) : null
      }
      entries.sort((a, b) => (a.value?.at ?? 0) - (b.value?.at ?? 0))
      for (const { key } of entries.slice(0, Math.max(0, entries.length - MAX_SESSIONS))) {
        if (!live()) return
        await storage.remove(key)
      }
    } catch {}
  }

  async function retire(sessionID, entry, admitted) {
    cancelShellGrace(sessionID)
    entry.task = null
    await save(sessionID, entry, admitted)
    if (sessions.get(sessionID) === entry) {
      sessions.delete(sessionID)
      saved.delete(sessionID)
      pending.delete(sessionID)
    }
  }

  // A sent write can settle after a preference change. Only its exact visible title can acknowledge it later.
  async function updateTitle(sessionID, entry, title, cells) {
    const task = entry.task
    await ctx.session.update({ sessionID, title })
    if (sessions.get(sessionID) === entry && entry.task === task) sentTitles.set(task, { title, cells })
  }

  function reconcileTitle(entry, title) {
    const task = entry.task, sent = sentTitles.get(task)
    if (!sent) return
    sentTitles.delete(task)
    if (title !== sent.title) return
    task.cells = Math.max(task.cells, sent.cells)
    task.fresh = false
    delete task.resetFrom
    entry.last = title
  }

  // A spec or plan write. A child's write binds its root; the bind runs on the root's queue so it cannot race the root's
  // own evaluation. `directory` is the folder of the session that wrote, which a relative path resolves against.
  async function route(sessionID, path, admitted) {
    if (!await on() || !current(sessionID, admitted)) return
    const info = await read(sessionID)
    const directory = info?.location?.directory
    if (typeof directory !== "string") return
    if (typeof info.parentID !== "string") {
      if (await owns(info) === true) await bind(sessionID, path, directory, admitted, info)
      return
    }
    const root = await rootOf(sessionID)
    if (!root || root === sessionID) return
    // The root's generation before its read: a move during the read or while the bind waits on the root's queue
    // makes the bind do nothing, so stale root information can never write the old folder's record.
    const before = admit(root, admitted.revision)
    const rootInfo = await read(root)
    if (await owns(rootInfo) !== true || !current(root, before)) return
    await enqueue(root, () => {
      if (current(sessionID, admitted)) return bind(root, path, directory, before, rootInfo)
    }, before)
  }

  async function bind(sessionID, path, directory, admitted, known) {
    const absolute = resolve(directory, path), found = artifact(absolute)
    if (!found) return
    const top = await realpath(found.top).catch(() => found.top)
    const file = top + absolute.slice(found.top.length)
    await load(sessionID, admitted, known)
    const held = sessions.get(sessionID)?.task
    // The task's first plan, with the spec's topic, written in another checkout of the same repository, moves the task
    // there (its spec may sit in the main checkout while the work happens in a worktree). Branch evidence from the old
    // checkout does not belong to the new one.
    const adopt = found.kind === "plan" && held && held.top !== top && !held.plan && found.slug === held.slug &&
      await sameRepository(held.top, top)
    if (!current(sessionID, admitted)) return
    let entry = sessions.get(sessionID)
    if (!held || (found.kind === "spec" && held.spec !== file)) {
      cancelShellGrace(sessionID)
      const title = known.title ?? ""
      const reused = held || finite(known.time?.idle) || (title.startsWith(MARK) && PREFIX.test(title))
      const task = { top, spec: found.kind === "spec" ? file : null, plan: found.kind === "plan" ? file : null,
        remembered: null, cells: 1, slug: found.slug, fresh: true, seen: null, bound: Date.now(),
        ...(reused && { resetFrom: title }) }
      entry = { task, last: null, silenced: false, diag: null }
      if (held) taskRevisions.set(entry, ++taskRevision)
    } else if (found.kind === "plan" && entry.task.top === top) entry.task.plan = file
    else if (adopt && entry.task === held) entry.task = { ...held, top, plan: file, remembered: null, seen: null }
    keep(sessionID, entry)
    await save(sessionID, entry, admitted)
  }

  async function sameRepository(a, b) {
    const [left, right] = await Promise.all([commonDir({ top: a, run }), commonDir({ top: b, run })])
    return left !== null && left === right
  }

  // The top of a session's parent chain; itself when it has no parent, null when a read fails or the chain is too long.
  async function rootOf(sessionID, cachedOnly = false) {
    try {
      let id = sessionID
      for (let hops = 0; hops <= MAX_HOPS; hops++) {
        if (cachedOnly && !parents.has(id)) return null
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
        if (Array.isArray(items) && items.some((info) => typeof info?.id === "string" && !deleted.has(info.id) && running(info?.time) &&
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
    if (!branch || task.remembered === branch || !current(sessionID, admitted) || sessions.get(sessionID) !== entry || entry.task !== task) return
    task.remembered = branch
    await save(sessionID, entry, admitted)
  }

  // A session below a bound root ended: the root may now be idle with nothing running, so it re-evaluates its label.
  // `created` is the end event's own time: a late delivery must not push the cutoff past a run that restarted since.
  // It is used only when it is within 5 minutes of now, so a different unit or clock can never hide the label.
  async function wakeRoot(sessionID, created) {
    const source = admit(sessionID)
    const received = Date.now()
    const at = typeof created === "number" && Math.abs(created - received) <= 5 * 60 * 1000 ? created : received
    if (parents.get(sessionID) === null || !await on()) return
    const root = await rootOf(sessionID)
    if (root && root !== sessionID && current(sessionID, source)) {
      await enqueue(root, (admitted) => {
        if (current(sessionID, source)) return evaluate(root, true, { id: sessionID, at }, admitted)
      }, admit(root, source.revision))
    }
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
      enqueue(sessionID, (admitted) => relabel(sessionID, false, admitted, {
        running: !hasShell(sessionID) && !activeShellGrace(sessionID), still: () => !pending.get(sessionID)?.size,
      }))
    }
  }

  // Sets or clears WAITING on the meter as it stands, with no git or gh read. Never while the task is fresh: before its
  // first meter (OpenCode titles a new session during its first turn) or when it was bound in the current turn.
  // `known` reuses the shell idle check's session read. `still()` rechecks state before the write.
  async function relabel(sessionID, waiting, admitted, { running: whileRunning = false, known, still = () => true } = {}) {
    if (!await on()) return
    const entry = sessions.get(sessionID), task = entry?.task
    if (!task || entry.silenced || (task.fresh && !sentTitles.has(task))) return
    const info = known ?? await read(sessionID)
    if (await owns(info) !== true || !current(sessionID, admitted) || sessions.get(sessionID) !== entry ||
        (whileRunning && !running(info.time)) || !still()) return
    const title = info.title ?? ""
    reconcileTitle(entry, title)
    if (task.cells === 5) { await retire(sessionID, entry, admitted); return }
    if (task.fresh) return
    if (!PREFIX.test(title)) { entry.silenced = true; await save(sessionID, entry, admitted); return }
    const next = compose(strip(title) || task.slug, task.cells, waiting)
    if (next !== title) await updateTitle(sessionID, entry, next, task.cells)
    if (!current(sessionID, admitted) || sessions.get(sessionID) !== entry || entry.task !== task) return
    sentTitles.delete(task)
    entry.last = next
    await save(sessionID, entry, admitted)
  }

  // Background work belongs to the root's current task, even when the shell runs in a different checkout.
  // An id that already exited is never tracked (a fast command can exit before the tool hook runs).
  function hasShell(root) {
    const entry = sessions.get(root)
    for (const shell of shells.values()) if (shell.root === root && (!shell.entry || shell.entry === entry)) return true
    return false
  }

  function track(shellID, root, owner = root) {
    if (exited.has(shellID) || deleted.has(root) || controller.signal.aborted) return false
    const entry = sessions.get(root), previous = shells.get(shellID)
    // A local tool result may arrive before the broadcast resolves its owning root.
    if (previous && (previous.entry || (!entry && previous.root === root))) return false
    const first = !hasShell(root)
    cancelShellGrace(root)
    shells.set(shellID, { root, entry, owner })
    if (shells.size > MAX_SHELLS) {
      // Unrelated broadcasts must not displace a bound task's shells.
      const unbound = [...shells].find(([, shell]) => !shell.entry || sessions.get(shell.root) !== shell.entry)
      shells.delete(unbound?.[0] ?? shells.keys().next().value)
    }
    return first
  }

  function cancelShellGrace(root) {
    clearTimeout(shellGraces.get(root)?.timer)
    shellGraces.delete(root)
  }

  function activeShellGrace(root) {
    const grace = shellGraces.get(root)
    return Boolean(grace && current(root, grace.admission))
  }

  async function shellRoot(sessionID) {
    if (sessions.has(sessionID)) return sessionID
    if (parents.get(sessionID) === null) return null
    // Before a bind, use cached ancestry only; a later idle snapshot can supply any missing shells.
    if (!sessions.size) return rootOf(sessionID, true)
    if (shellRoots.has(sessionID)) return shellRoots.get(sessionID)
    if (shellRoots.size >= MAX_SHELLS) return null
    const lookup = (async () => {
      const cached = await rootOf(sessionID, true)
      if (cached || !await on()) return cached
      return rootOf(sessionID)
    })().finally(() => shellRoots.delete(sessionID))
    shellRoots.set(sessionID, lookup)
    return lookup
  }

  function shellJob(info) {
    if (info?.kind !== "shell" || typeof info.shellID !== "string") return
    const { sessionID, shellID, status } = info
    if (status === "completed" || status === "error" || status === "cancelled") return shellExited(shellID)
    if (status !== "running" || typeof sessionID !== "string" || deleted.has(sessionID) || controller.signal.aborted ||
        exited.has(shellID) || shells.get(shellID)?.entry ||
        shellStarts.has(shellID) || shellStarts.size >= MAX_SHELLS) return
    const source = admit(sessionID)
    const revision = taskRevision
    const start = shellRoot(sessionID).then((root) => {
      const entry = sessions.get(root)
      if (!root || !current(sessionID, source) || entry?.silenced || (taskRevisions.get(entry) ?? 0) > revision ||
          !track(shellID, root, sessionID) || !entry?.task) return
      void enqueue(root, (admitted) => relabel(root, false, admitted, {
        still: () => sessions.get(root) === entry && hasShell(root) && !pending.get(root)?.size,
      }), admit(root, source.revision))
    }).catch(() => {}).finally(() => shellStarts.delete(shellID))
    // Record the start before ancestry resolves so it can veto a competing idle write.
    shellStarts.set(shellID, start)
  }

  async function shellTurnStarted(sessionID) {
    if (shellGraces.has(sessionID)) return cancelShellGrace(sessionID)
    if (!shellGraces.size || sessions.has(sessionID)) return
    const before = new Map(shellGraces)
    const root = await shellRoot(sessionID)
    if (!deleted.has(sessionID) && before.has(root) && before.get(root) === shellGraces.get(root)) cancelShellGrace(root)
  }

  // Wait outside the title queue. Retry the read if a competing title operation or an unresolved shell start
  // arrives before the write. Cold starts share one deadline so an unrelated lookup cannot strand the title.
  async function finishShellGrace(root, grace, still) {
    let timer, cutoff
    let expired = false
    const unresolved = () => expired ? [] : [...shellStarts].flatMap(([id, start]) => exited.has(id) ? [] : [start])
    try {
      while (still()) {
        const queued = chains.get(root)
        if (queued) {
          await queued
          continue
        }
        const starts = unresolved()
        if (starts.length) {
          cutoff ??= new Promise((done) => { timer = setTimeout(done, deadline, false) })
          expired = !await Promise.race([Promise.all(starts).then(() => true), cutoff])
          continue
        }
        const revision = grace.queued
        const info = await read(root)
        if (running(info.time) || !still()) return
        if (grace.queued !== revision || unresolved().length) continue
        let retry = false
        await enqueue(root, (admitted) => relabel(root, true, admitted, {
          known: info,
          still: () => {
            retry = grace.queued !== revision + 1 || unresolved().length > 0
            return !retry && still()
          },
        }), grace.admission)
        if (!retry) return
      }
    } finally { clearTimeout(timer) }
  }

  function shellExited(shellID) {
    if (typeof shellID !== "string") return
    exited.add(shellID)
    if (exited.size > MAX_SHELLS) exited.delete(exited.values().next().value)
    const shell = shells.get(shellID)
    if (!shells.delete(shellID)) return
    const { root, owner } = shell, entry = sessions.get(root)
    if (!entry?.task || (shell.entry && shell.entry !== entry)) return
    if (owner !== root && !deleted.has(owner)) {
      const owners = endedShellOwners.get(entry) ?? new Set()
      owners.delete(owner)
      owners.add(owner)
      if (owners.size > MAX_SHELLS) owners.delete(owners.values().next().value)
      endedShellOwners.set(entry, owners)
    }
    scheduleShellGrace(root, entry)
  }

  function sessionDeleted(sessionID) {
    if (deleted.has(sessionID)) return
    deleted.add(sessionID)
    if (deleted.size > MAX_GENERATIONS) deleted.delete(deleted.values().next().value)
    forget(sessionID)
    // A cancelled shell can start a turn just before deletion, and an in-flight idle check may still see it.
    for (const [root, entry] of sessions) {
      if (!endedShellOwners.get(entry)?.delete(sessionID) || hasShell(root)) continue
      cancelShellGrace(root)
      scheduleShellGrace(root, entry)
    }
  }

  function scheduleShellGrace(root, entry) {
    if (!entry.task || entry.silenced || hasShell(root) || activeShellGrace(root)) return
    const grace = { admission: admit(root), timer: null, queued: 0 }
    const sameGrace = () => shellGraces.get(root) === grace
    const still = () => sameGrace() && activeShellGrace(root) && sessions.get(root) === entry &&
      !hasShell(root) && !pending.get(root)?.size
    // Give OpenCode time to resume a turn with the shell result before showing an idle WAITING.
    grace.timer = setTimeout(async () => {
      try {
        if (!still() || !await on()) return
        const now = await activity(root)
        if (!now.children) await finishShellGrace(root, grace, still)
      } catch {} finally {
        if (sameGrace()) cancelShellGrace(root)
      }
    }, SHELL_EXIT_GRACE_MS)
    shellGraces.set(root, grace)
  }

  const validLocal = (facts) => facts.exists && !facts.errors.some(({ step }) => step === "status" || step === "diff")
  const nonDoc = (path) => !path.startsWith("docs/")
  const filePath = (path) => typeof path === "string" && !/[\0\\]/.test(path) &&
    path.split("/").every((part) => part && part !== "." && part !== "..")
  const planVersion = (task) => planVersions.get(task) ?? 0
  const progressState = (task, declared = null, confirmed = new Set()) => ({
    plan: task.plan, declared, confirmed, hints: new Set(), work: false, attempted: 1, queued: false,
    events: 0, reread: false, reload: false,
  })

  function rememberLocal(state, facts) {
    state.declared = facts.errors.some(({ step }) => step === "plan") ? null : new Set(facts.declared)
    state.confirmed = new Set(validLocal(facts) ? facts.changed.filter((path) => state.declared?.has(path)) : [])
    for (const path of state.hints) if (!state.declared?.has(path)) state.hints.delete(path)
  }

  function milestone(state) {
    const count = state.declared?.size ?? 0
    let done = state.confirmed.size
    for (const path of state.hints) if (!state.confirmed.has(path)) done++
    if (count && done === count) return 4
    if (count && done >= Math.ceil(count / 2)) return 3
    return state.work ? 2 : 1
  }

  // A snapshot is only a hint. Verify with the real checkout and the current plan, then preserve the actual label.
  function scheduleProgress(sessionID, entry, state, admitted) {
    const task = entry.task
    const live = () => current(sessionID, admitted) && sessions.get(sessionID) === entry && entry.task === task &&
      progress.get(task) === state && state.plan === task.plan && task.cells < 4 && !entry.silenced
    const wanted = () => milestone(state) > Math.max(task.cells, state.attempted)
    if (state.queued || !live() || (!state.reload && !wanted())) return
    state.queued = true
    let observed
    enqueue(sessionID, async () => {
      if (!live() || !await on() || !live()) return
      observed = state.events
      if (state.reload) {
        const version = planVersion(task)
        state.reload = false
        const paths = await readPlanPaths(task.plan).catch(() => null)
        if (!live()) return
        if (planVersion(task) === version && paths) state.declared = new Set(paths)
      }
      if (!wanted()) return
      state.attempted = milestone(state)
      const version = planVersion(task)
      const facts = await readFacts({ top: task.top, localOnly: true, run })
      if (!live() || !validLocal(facts)) return
      const info = await read(sessionID)
      if (await owns(info) !== true || !live()) return
      const title = info.title ?? "", prefix = title.match(PREFIX)?.[0]
      if (!title.startsWith(MARK) || !prefix) return
      reconcileTitle(entry, title)
      const declared = planVersion(task) === version && state.declared ? [...state.declared] : []
      const cells = cellsFor({ ...facts, declared })
      if (cells <= task.cells) return
      const next = compose(strip(title) || task.slug, cells, prefix.endsWith("WAITING · "))
      if (next !== title) await updateTitle(sessionID, entry, next, cells)
      if (!live()) return
      sentTitles.delete(task)
      task.cells = cells
      entry.last = next
      await save(sessionID, entry, admitted)
    }, admitted).then(() => {
      state.queued = false
      if (observed !== undefined && state.events !== observed && live() && (state.reload || wanted())) {
        scheduleProgress(sessionID, entry, state, admitted)
      }
    })
  }

  function stepProgress(event, sessionID) {
    const { files } = event.data, directory = event.location?.directory
    if (!Array.isArray(files) || !files.length || typeof directory !== "string") return
    const top = reals.get(directory) ?? directory
    const source = admit(sessionID)
    const candidate = (entry) => {
      const task = entry?.task
      if (!task || entry.silenced || task.cells >= 4 || task.top !== top) return null
      const planPath = task.plan && relative(top, task.plan)
      const edited = planPath && files.includes(planPath)
      const state = progress.get(task)
      if (!edited && (task.fresh || !state || state.plan !== task.plan)) return null
      return { task, state, plan: task.plan, planPath, edited }
    }
    const accept = (root, entry, match, admitted) => {
      const { task, state, plan, planPath, edited } = match
      if (!current(sessionID, source) || !current(root, admitted) || sessions.get(root) !== entry ||
          entry.task !== task || task.plan !== plan) return
      if (edited) planVersions.set(task, planVersion(task) + 1)
      if (!state || task.fresh || progress.get(task) !== state || state.plan !== plan) return
      state.events++
      if (edited) {
        state.declared = null
        state.confirmed = new Set()
        state.hints.clear()
        state.reload = !state.reread
        state.reread = true
      }
      for (const path of files) {
        if (!filePath(path) || path === planPath) continue
        if (nonDoc(path)) state.work = true
        if (state.declared?.has(path)) state.hints.add(path)
      }
      scheduleProgress(root, entry, state, admitted)
    }
    const own = sessions.get(sessionID)
    if (own) {
      const match = candidate(own)
      if (match) accept(sessionID, own, match, source)
      return
    }
    if (parents.get(sessionID) === null) return
    const candidates = new Map()
    for (const [root, entry] of sessions) {
      const match = candidate(entry)
      if (match) candidates.set(root, [entry, match, admit(root, source.revision)])
    }
    if (candidates.size) void rootOf(sessionID).then((root) => {
      const match = candidates.get(root)
      if (match) accept(root, ...match)
    }).catch(() => {})
  }

  function resetProgress(sessionID) {
    const task = sessions.get(sessionID)?.task, state = progress.get(task)
    if (state) progress.set(task, progressState(task, state.declared, state.confirmed))
  }

  async function evaluate(sessionID, ended, fromChild, admitted) {
    if (!sessions.has(sessionID)) {
      if (checked.has(sessionID) || !await on()) return
      await load(sessionID, admitted)
    }
    const entry = sessions.get(sessionID), task = entry?.task
    if (!task || entry.silenced || (!ended && task.fresh && !sentTitles.has(task)) || !await on()) return
    if (fromChild) {
      const now = await activity(sessionID, { ended: fromChild, withSelf: true })
      if (now.children || now.self) return
    }
    const state = progress.get(task), version = planVersion(task)
    let diag = entry.diag, facts, proof
    const updates = {}
    if (ended) {
      const { state, project = null, worktree_cleanup } = await gitWorkflow.inspect()
      facts = await readFacts({ top: task.top, plan: task.plan, remembered: task.remembered, seen: task.seen,
        bound: task.bound, fallback: project ?? task.top, run })
      const clip = (value) => typeof value === "string" ? value.slice(0, 200) : null
      if (facts.head && facts.head === facts.branch) updates.remembered = facts.head
      // The branch seen here lets the PR be found once the checkout is removed or detached, even if it was never pushed
      // from this session; it is looked up only then, so an unpushed checkout still makes no gh call.
      if (facts.branch && facts.branch !== facts.base.replace(/^origin\//, "")) updates.seen = clip(facts.branch)
      diag = { instance, exists: facts.exists, branch: clip(facts.branch), upstream: facts.upstream, head: clip(facts.head),
        pr: PR_STATES.includes(facts.pr?.state) ? facts.pr.state : null, declared: facts.declared.length,
        done: facts.declared.filter((path) => facts.changed.includes(path)).length, errors: facts.errors }
      const pr = facts.pr && { state: facts.pr.state, title: typeof facts.pr.title === "string" ? facts.pr.title : "" }
      proof = { ...facts, pr, cleanupOn: state === "on" && worktree_cleanup === true, primary: task.top === project }
    }
    const busy = ended ? await activity(sessionID, { ended: fromChild, withSelf: Boolean(fromChild) }) : null
    const info = await read(sessionID)
    if ((fromChild && busy.self) || await owns(info) !== true || !current(sessionID, admitted) ||
        sessions.get(sessionID) !== entry || entry.task !== task) return
    const title = info.title ?? ""
    reconcileTitle(entry, title)
    if (!ended && task.fresh) return
    const samePlan = planVersion(task) === version
    const cells = proof ? Math.max(task.cells, cellsFor({ ...proof, declared: samePlan ? proof.declared : [] })) : task.cells
    const reset = task.fresh && typeof task.resetFrom === "string"
    const renamed = reset && title !== task.resetFrom
    if (renamed || (entry.last !== null && !PREFIX.test(title))) {
      if (cells === 5) await retire(sessionID, entry, admitted)
      else {
        Object.assign(task, updates)
        entry.diag = diag
        entry.silenced = true
        await save(sessionID, entry, admitted)
      }
      return
    }
    const kept = strip(title)
    const topic = !kept || DEFAULT_TITLE.test(kept) || reset ? task.slug : kept
    const waiting = pending.get(sessionID)?.size > 0 ||
      (ended && !busy.children && !hasShell(sessionID) && !activeShellGrace(sessionID))
    const next = compose(topic, cells, cells < 5 && waiting)
    if (next !== title) await updateTitle(sessionID, entry, next, cells)
    if (!current(sessionID, admitted) || sessions.get(sessionID) !== entry || entry.task !== task) return
    sentTitles.delete(task)
    if (cells === 5) { await retire(sessionID, entry, admitted); return }
    Object.assign(task, updates, { cells, fresh: false })
    entry.diag = diag
    delete task.resetFrom
    entry.last = next
    if (facts && cells < 4 && progress.get(task) === state && samePlan && planVersion(task) === version) {
      const nextState = state ?? progressState(task)
      rememberLocal(nextState, facts)
      progress.set(task, nextState)
    }
    await save(sessionID, entry, admitted)
  }

  try {
    const stream = await ctx.event.subscribe({ types: TYPES, signal: controller.signal })
    await ctx.tool.hook("execute.after", async (event) => {
      try {
        if (event.status !== "completed" || typeof event.sessionID !== "string") return
        const input = event.input, metadata = event.result?.metadata
        if (SHELLS.has(event.tool) && typeof metadata?.shellID === "string" && metadata.status === "running" &&
            !shells.has(metadata.shellID)) track(metadata.shellID, event.sessionID)
        if (event.tool === "write") {
          if (typeof input?.path === "string" && ARTIFACT.test(input.path)) await enqueue(event.sessionID, (admitted) => route(event.sessionID, input.path, admitted))
        } else if (SHELLS.has(event.tool) && typeof input?.command === "string" &&
          classify(input.command).some(({ kind }) => RECORDING.has(kind))) {
          await enqueue(event.sessionID, (admitted) => record(event.sessionID, admitted))
        }
      } catch {}
    })
    // Older hosts may lack job broadcasts. Their idle snapshot already lists the root's descendant shells.
    try {
      await ctx.session.hook?.("experimental.idle", ({ sessionID, background }) => {
        if (!sessions.get(sessionID)?.task || !Array.isArray(background)) return
        for (const job of background) {
          if (job?.kind === "shell" && typeof job.shellID === "string") track(job.shellID, sessionID, job.sessionID)
        }
      })
    } catch {}
    // Settings are saved at the checkout root, which several location copies may share.
    let preferenceRoot
    try { if (where) preferenceRoot = (await settings.inspect()).root } catch {}
    if (typeof preferenceRoot === "string" && isAbsolute(preferenceRoot)) {
      const registry = globalThis[PREFERENCES] ??= new Map()
      if (!registry.has(preferenceRoot)) registry.set(preferenceRoot, preference)
      preference = registry.get(preferenceRoot)
      preference.copies++
      leave = () => {
        leave = () => {}
        if (--preference.copies === 0) registry.delete(preferenceRoot)
      }
    }
    ;(async () => {
      try {
        for await (const event of stream) {
          if (REQUESTS[event?.type]) { request(...REQUESTS[event.type](event.data)); continue }
          if (event?.type === JOB) { shellJob(event.data?.info); continue }
          if (event?.type === "shell.exited") { shellExited(event.data?.id); continue }
          const sessionID = event?.data?.sessionID
          if (typeof sessionID !== "string") continue
          if (event.type === "session.deleted") { sessionDeleted(sessionID); continue }
          if (deleted.has(sessionID)) continue
          if (event.type === STEP) stepProgress(event, sessionID)
          else if (event.type === "session.moved") forget(sessionID)
          else if (TURNS.includes(event.type)) {
            if (event.type === TURNS[0]) shellTurnStarted(sessionID).catch(() => {})
            // A blocking request cannot outlive its session's own turn boundary; this also drops a missed reply.
            pending.delete(sessionID)
            resetProgress(sessionID)
            enqueue(sessionID, (admitted) => evaluate(sessionID, event.type !== TURNS[0], null, admitted))
            if (event.type !== TURNS[0] && !sessions.has(sessionID)) wakeRoot(sessionID, event.created).catch(() => {})
          }
        }
      } catch {}
    })()
  } catch { close(); return unavailable }
  return { registered: true, close, preferenceSaved(enabled) {
    if (typeof enabled === "boolean" && !controller.signal.aborted) {
      preference.revision++
      if (!enabled) for (const id of shellGraces.keys()) cancelShellGrace(id)
    }
  } }
}
