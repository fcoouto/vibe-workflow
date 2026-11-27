import { execFile } from "node:child_process"
import { promisify } from "node:util"
import { isRunning } from "./session-titles.js"

const exec = promisify(execFile)
const MERGED = "refs/remotes/origin/HEAD"
const LISTED = 10
const PAGE = 50
const MAX_PAGES = 4
const SWEEP = "Sweep them per the worktree cleanup steps in vibe-pull-requests."
const EXPLAIN = "Tell your human partner once in your report: cleanup now also removes older merged worktrees and " +
  "tool caches; other files are asked about before the merge."
const PROCESS = { explained: false }

export function parseWorktrees(porcelain) {
  return porcelain.split("\n\n").filter(Boolean).map((block) => {
    const entry = { path: "", head: null, branch: null, locked: false, prunable: false }
    for (const line of block.split("\n")) {
      if (line.startsWith("worktree ")) entry.path = line.slice("worktree ".length)
      else if (line.startsWith("HEAD ")) entry.head = line.slice("HEAD ".length)
      else if (line.startsWith("branch ")) entry.branch = line.slice("branch ".length).replace(/^refs\/heads\//, "")
      else if (line === "locked" || line.startsWith("locked ")) entry.locked = true
      else if (line === "prunable" || line.startsWith("prunable ")) entry.prunable = true
    }
    return entry
  })
}

async function defaultRun(cwd, args, signal) {
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_")))
  try {
    const { stdout } = await exec("git", args, { cwd, env: { ...env, LC_ALL: "C" }, signal, maxBuffer: 1 << 20 })
    return { code: 0, stdout }
  } catch (error) {
    if (signal.aborted || typeof error?.code !== "number") throw error
    return { code: error.code, stdout: error.stdout ?? "" }
  }
}

// Each note call gets one deadline; every read shares its signal, and a read that outlives the deadline is abandoned.
export function createSweep({ primary, run = defaultRun, busy = async () => null, deadline = 500, process = PROCESS }) {
  async function find(signal, exclude) {
    const top = await primary()
    if (!top) return null
    const [listed, merged, sessions] = await Promise.all([
      run(top, ["worktree", "list", "--porcelain"], signal),
      run(top, ["for-each-ref", "--merged", MERGED, "--format=%(refname:short)", "refs/heads"], signal),
      busy(signal),
    ])
    if (listed.code !== 0 || merged.code !== 0 || sessions?.complete === false || signal.aborted) return null
    const [, ...others] = parseWorktrees(listed.stdout)
    const candidates = others.filter((entry) => !entry.locked)
    const names = new Set(merged.stdout.split("\n").filter(Boolean))
    const heads = candidates.filter((entry) => entry.branch === null && entry.head !== null)
    const ancestry = await Promise.all(heads.map((entry) => run(top, ["merge-base", "--is-ancestor", entry.head, MERGED], signal)))
    if (ancestry.some(({ code }) => code !== 0 && code !== 1) || signal.aborted) return null
    const ancestors = new Set(heads.filter((_, i) => ancestry[i].code === 0))
    const dirs = [...exclude.filter((dir) => typeof dir === "string"), ...(sessions?.dirs ?? [])]
    return candidates
      .filter((entry) => entry.branch === null ? ancestors.has(entry) : names.has(entry.branch))
      .filter((entry) => !dirs.some((dir) => dir === entry.path || dir.startsWith(entry.path + "/")))
  }

  return {
    async note({ exclude = [] } = {}) {
      const controller = new AbortController(), { signal } = controller
      const timer = setTimeout(() => controller.abort(), deadline)
      const cancelled = new Promise((_, reject) => signal.addEventListener("abort", () => reject(signal.reason), { once: true }))
      try {
        const found = await Promise.race([find(signal, exclude), cancelled])
        if (!found?.length) return null
        const label = (entry) => [entry.branch ?? "detached", entry.prunable && "needs repair"].filter(Boolean).join(", ")
        const names = found.slice(0, LISTED).map((entry) => `${entry.path} (${label(entry)})`)
        if (found.length > LISTED) names.push(`and ${found.length - LISTED} more`)
        let text = `vibe-workflow: merged worktrees still on disk: ${names.join(", ")}. ${SWEEP}`
        if (!process.explained) {
          process.explained = true
          text += " " + EXPLAIN
        }
        return text
      } catch {
        return null
      } finally {
        clearTimeout(timer)
        controller.abort()
      }
    },
  }
}

export function busyDirectories(ctx, started) {
  return async (signal) => {
    const dirs = new Set()
    let page
    try { page = await ctx.session.list({ limit: PAGE, order: "desc" }) } catch { return null }
    for (let pages = 1; ; pages++) {
      const items = Array.isArray(page) ? page : page?.data
      if (!Array.isArray(items)) return pages === 1 ? null : { complete: false, dirs }
      for (const item of items) {
        if (!isRunning(item?.time, started)) continue
        const directory = item?.location?.directory
        if (typeof directory !== "string") return null
        dirs.add(directory)
      }
      const next = page?.cursor?.next
      if (typeof next !== "string") return { complete: true, dirs }
      if (pages >= MAX_PAGES || signal.aborted) return { complete: false, dirs }
      try { page = await ctx.session.list({ cursor: next, limit: PAGE }) } catch { return { complete: false, dirs } }
    }
  }
}
