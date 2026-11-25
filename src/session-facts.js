import { execFile } from "node:child_process"
import { readFile, stat } from "node:fs/promises"
import { resolve } from "node:path"
import { promisify } from "node:util"

const exec = promisify(execFile)
const KINDS = { specs: "spec", plans: "plan" }

export function artifact(path) {
  const at = path.lastIndexOf("/docs/superpowers/")
  if (at < 0) return null
  const parts = path.slice(at + "/docs/superpowers/".length).split("/")
  const kind = KINDS[parts[0]]
  if (!kind || parts.length !== 2 || !parts[1].endsWith(".md")) return null
  const slug = parts[1].slice(0, -3).replace(/^\d{4}-\d{2}-\d{2}-/, "").replace(/-design$/, "").replaceAll("-", " ")
  return { kind, top: path.slice(0, at), slug }
}

export function declaredPaths(planText) {
  const found = new Set()
  let inside = false
  const read = (text) => {
    for (const [, token] of text.matchAll(/`([^`]+)`/g)) {
      if (/[./]/.test(token)) found.add(token.replace(/:\d+(?:-\d+)?$/, ""))
    }
  }
  for (const line of planText.split("\n")) {
    if (line.trim() === "") inside = false
    else if (line.startsWith("**Files:**")) { inside = true; read(line.slice("**Files:**".length)) }
    else if (inside) read(line)
  }
  return [...found]
}

export async function defaultRun(command, args, { cwd, timeout }) {
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_")))
  // GIT_OPTIONAL_LOCKS=0: `git status` must never refresh the index or hold index.lock against the agent's own git commands.
  const { stdout } = await exec(command, args, { cwd, env: { ...env, LC_ALL: "C", GIT_OPTIONAL_LOCKS: "0" }, timeout, maxBuffer: 1 << 20 })
  return stdout
}

const lines = (text) => text.split("\n").filter(Boolean)

// A short code for a failed read: never the message, which can carry paths or remote URLs.
const codeOf = (error) => error?.killed ? "timeout"
  : typeof error?.code === "string" && /^[\w:]{1,40}$/.test(error.code) ? error.code
  : Number.isInteger(error?.code) ? `exit:${error.code}` : "error"

// The branch checked out in `top`, for a tool-time record: null when detached, on the default branch or unreadable.
export async function branchOf({ top, run = defaultRun }) {
  try {
    const status = await run("git", ["status", "--porcelain=v2", "--branch", "-uno"], { cwd: top, timeout: 2000 })
    const head = status.match(/^# branch\.head (.+)$/m)?.[1]
    if (!head || head === "(detached)") return null
    const base = (await run("git", ["rev-parse", "--abbrev-ref", "origin/HEAD"], { cwd: top, timeout: 2000 }).catch(() => "")).trim()
    return head === (base || "origin/main").replace(/^origin\//, "") ? null : head
  } catch { return null }
}

// The shared git folder of a checkout: equal for a main checkout and its linked worktrees; null when unreadable.
export async function commonDir({ top, run = defaultRun }) {
  try {
    const out = (await run("git", ["rev-parse", "--path-format=absolute", "--git-common-dir"], { cwd: top, timeout: 2000 })).trim()
    return out || null
  } catch { return null }
}

// `seen` (a branch seen at an earlier turn end) is used only on positive evidence that the checkout no longer shows a
// branch: the folder is gone (ENOENT) or a successful status read shows a detached head. A PR found through it counts
// only when it was created at or after `bound`, so a reused branch name cannot credit a PR opened before the task.
export async function readFacts({ top, plan, remembered = null, seen = null, bound = null, fallback, run = defaultRun }) {
  const git = (cwd, ...args) => run("git", args, { cwd, timeout: 2000 })
  const where = await stat(top).then((info) => ({ exists: info.isDirectory(), gone: false }),
    (error) => ({ exists: false, gone: error?.code === "ENOENT" }))
  const facts = { exists: where.exists, gone: where.gone, detached: false, branch: null, upstream: false, base: "origin/main",
    head: null, via: null, changed: [], declared: [], pr: null, errors: [] }
  const failed = (step) => (error) => { facts.errors.push({ step, code: codeOf(error) }) }
  if (facts.exists) {
    // A failed read yields no branch header, so it never reads as detached.
    const status = await git(top, "status", "--porcelain=v2", "--branch", "-uall").catch((error) => { failed("status")(error); return "" })
    const header = (name) => status.match(new RegExp(`^# branch\\.${name} (.+)$`, "m"))?.[1] ?? null
    const branch = header("head")
    facts.detached = branch === "(detached)"
    facts.branch = branch === "(detached)" ? null : branch
    facts.upstream = header("upstream") !== null
    facts.base = (await git(top, "rev-parse", "--abbrev-ref", "origin/HEAD").catch(() => "")).trim() || "origin/main"
    try {
      const diff = lines(await git(top, "diff", "--name-only", "--merge-base", facts.base))
      const untracked = lines(status).filter((line) => line.startsWith("? ")).map((line) => line.slice(2))
      facts.changed = [...new Set([...diff, ...untracked])]
    } catch (error) { failed("diff")(error) }
    if (plan) facts.declared = declaredPaths(await readFile(resolve(top, plan), "utf8").catch((error) => { failed("plan")(error); return "" }))
    if (facts.branch && facts.upstream && facts.branch !== facts.base.replace(/^origin\//, "")) Object.assign(facts, { head: facts.branch, via: "branch" })
  }
  if (!facts.head && remembered) Object.assign(facts, { head: remembered, via: "remembered" })
  else if (!facts.head && seen && typeof bound === "number" && (facts.gone || facts.detached)) Object.assign(facts, { head: seen, via: "seen" })
  if (!facts.head) return facts
  try {
    const out = await run("gh", ["pr", "list", "--head", facts.head, "--state", "all", "--json", "state,title,createdAt", "--limit", "1"],
      { cwd: facts.exists ? top : fallback, timeout: 5000 })
    const [first] = JSON.parse(out)
    const dated = facts.via !== "seen" || Date.parse(first?.createdAt) >= bound
    if (first && dated) facts.pr = { state: first.state, title: first.title }
  } catch (error) { failed("pr")(error) }
  return facts
}
