import { execFile } from "node:child_process"
import { realpath } from "node:fs/promises"
import { promisify } from "node:util"
import { isRunning } from "./session-titles.js"

const exec = promisify(execFile)
const MERGED = "refs/remotes/origin/HEAD"
const REFS = ["for-each-ref", "--merged", MERGED, "--format=%(refname) %(upstream) %(symref) %(upstream:track)", "refs/heads", MERGED]
const LISTED = 10
const PROBES = 8
const PAGE = 50
const MAX_PAGES = 4
const SWEEP = "Sweep them per the worktree cleanup steps in vibe-pull-requests."
const EXPLAIN = "Tell your human partner once in your report: cleanup now also removes older merged worktrees and " +
  "tool caches; other files are asked about before the merge."
const PROCESS = { explained: false }

// Input is `git worktree list --porcelain -z`: NUL-ended fields, and an empty field ends each record. Paths stay raw.
export function parseWorktrees(porcelain) {
  const entries = []
  let entry = null
  for (const field of porcelain.split("\0")) {
    if (field === "") entry = null
    else if (field.startsWith("worktree ")) {
      entry = { path: field.slice("worktree ".length), head: null, branch: null, locked: false, prunable: false }
      entries.push(entry)
    } else if (!entry) continue
    else if (field.startsWith("HEAD ")) entry.head = field.slice("HEAD ".length)
    else if (field.startsWith("branch ")) entry.branch = field.slice("branch ".length).replace(/^refs\/heads\//, "")
    else if (field === "locked" || field.startsWith("locked ")) entry.locked = true
    else if (field === "prunable" || field.startsWith("prunable ")) entry.prunable = true
  }
  return entries
}

// Input is the REFS answer: one `<ref> <its upstream> <its symbolic target> <its distance to the upstream>` line per
// merged ref. A branch with no commits of its own is merged too, so a branch counts only when it has a remote branch
// other than the default one, that remote branch exists here, and it holds the branch's tip: the distance is empty or
// only `[behind N]`, never `[gone]` or `[ahead N]`. Returns null when the default branch's own line is missing.
function pushedBranches(listing) {
  const lines = listing.split("\n").filter(Boolean).map((line) => line.split(" "))
  const target = lines.find(([ref]) => ref === MERGED)?.[2]
  if (!target) return null
  const held = (track) => track === "" || /^\[behind \d+\]$/.test(track)
  return new Set(lines
    .filter(([ref, upstream, , ...track]) => ref.startsWith("refs/heads/") && upstream?.startsWith("refs/remotes/") &&
      upstream !== target && held(track.join(" ")))
    .map(([ref]) => ref.slice("refs/heads/".length)))
}

// At most `limit` calls run at once, and none starts once the deadline has passed.
async function pooled(items, limit, signal, work) {
  const results = []
  let next = 0
  const worker = async () => {
    while (next < items.length && !signal.aborted) {
      const index = next++
      results[index] = await work(items[index])
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker))
  return results
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
export function createSweep({ primary, run = defaultRun, busy = async () => null, deadline = 500, process = PROCESS,
  real = realpath }) {
  // A folder under its given name and its resolved one: a session may name its folder through a symbolic link, and a
  // folder that cannot be resolved keeps the name it was given.
  const forms = async (path) => {
    try { return [path, await real(path)] } catch { return [path] }
  }

  async function find(signal, exclude) {
    const top = await primary()
    if (!top || signal.aborted) return null
    const [listed, merged, sessions] = await Promise.all([
      run(top, ["worktree", "list", "--porcelain", "-z"], signal),
      run(top, REFS, signal),
      busy(signal),
    ])
    if (listed.code !== 0 || merged.code !== 0 || sessions?.complete === false || signal.aborted) return null
    const [, ...others] = parseWorktrees(listed.stdout)
    const candidates = others.filter((entry) => !entry.locked)
    const names = pushedBranches(merged.stdout)
    if (!names) return null
    const heads = candidates.filter((entry) => entry.branch === null && entry.head !== null)
    const ancestry = await pooled(heads, PROBES, signal, (entry) => run(top, ["merge-base", "--is-ancestor", entry.head, MERGED], signal))
    if (signal.aborted || ancestry.some(({ code }) => code !== 0 && code !== 1)) return null
    const ancestors = new Set(heads.filter((_, i) => ancestry[i].code === 0))
    const found = candidates.filter((entry) => entry.branch === null ? ancestors.has(entry) : names.has(entry.branch))
    const given = [...exclude.filter((dir) => typeof dir === "string"), ...(sessions?.dirs ?? [])]
    const resolved = await pooled([...given, ...found.map((entry) => entry.path)], PROBES, signal, forms)
    if (signal.aborted) return null
    const dirs = resolved.slice(0, given.length).flat(), tops = resolved.slice(given.length)
    const inside = (dir, top) => dir === top || dir.startsWith(top + "/")
    return found.filter((_, i) => !dirs.some((dir) => tops[i].some((top) => inside(dir, top))))
  }

  return {
    async note({ exclude = [] } = {}) {
      const controller = new AbortController(), { signal } = controller
      const timer = setTimeout(() => controller.abort(), deadline)
      const cancelled = new Promise((_, reject) => signal.addEventListener("abort", () => reject(signal.reason), { once: true }))
      try {
        const found = await Promise.race([find(signal, exclude), cancelled])
        if (!found?.length) return null
        // Names are data: JSON keeps a path or branch with a newline, a quote or a bracket inside its own string.
        const named = found.slice(0, LISTED).map(({ path, branch, prunable }) => ({ path, branch, repair: prunable }))
        const more = found.length > LISTED ? ` and ${found.length - LISTED} more` : ""
        let text = `vibe-workflow: merged worktrees still on disk (JSON): ${JSON.stringify(named)}${more}. ${SWEEP}`
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
    let page, unlocated = false
    try { page = await ctx.session.list({ limit: PAGE, order: "desc" }) } catch { return { complete: false, dirs } }
    for (let pages = 1; ; pages++) {
      const items = Array.isArray(page) ? page : page?.data
      if (!Array.isArray(items)) return { complete: false, dirs }
      for (const item of items) {
        if (!isRunning(item?.time, started)) continue
        const directory = item?.location?.directory
        if (typeof directory === "string") dirs.add(directory)
        else unlocated = true
      }
      // Folders count only when every running session gives one. None at all means the host gives no folders (null);
      // some without one means the list is incomplete.
      if (unlocated && dirs.size) return { complete: false, dirs }
      const next = page?.cursor?.next
      if (typeof next !== "string") return unlocated ? null : { complete: true, dirs }
      if (pages >= MAX_PAGES || signal.aborted) return { complete: false, dirs }
      try { page = await ctx.session.list({ cursor: next, limit: PAGE }) } catch { return { complete: false, dirs } }
    }
  }
}
