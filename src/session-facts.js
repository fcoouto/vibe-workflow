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
  for (const line of planText.split("\n")) {
    if (line.trim() === "") inside = false
    else if (line.startsWith("**Files:**")) inside = true
    else if (inside) {
      for (const [, token] of line.matchAll(/`([^`]+)`/g)) {
        if (/[./]/.test(token)) found.add(token.replace(/:\d+(?:-\d+)?$/, ""))
      }
    }
  }
  return [...found]
}

export async function defaultRun(command, args, { cwd, timeout }) {
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_")))
  const { stdout } = await exec(command, args, { cwd, env: { ...env, LC_ALL: "C" }, timeout, maxBuffer: 1 << 20 })
  return stdout
}

const lines = (text) => text.split("\n").filter(Boolean)

export async function readFacts({ top, plan, remembered = null, fallback, run = defaultRun }) {
  const git = (cwd, ...args) => run("git", args, { cwd, timeout: 2000 })
  const exists = await stat(top).then((info) => info.isDirectory(), () => false)
  const facts = { exists, branch: null, upstream: false, base: "origin/main", head: null, changed: [], declared: [], pr: null }
  if (exists) {
    const status = await git(top, "status", "--porcelain=v2", "--branch", "-uall").catch(() => "")
    const header = (name) => status.match(new RegExp(`^# branch\\.${name} (.+)$`, "m"))?.[1] ?? null
    const branch = header("head")
    facts.branch = branch === "(detached)" ? null : branch
    facts.upstream = header("upstream") !== null
    facts.base = (await git(top, "rev-parse", "--abbrev-ref", "origin/HEAD").catch(() => "")).trim() || "origin/main"
    try {
      const diff = lines(await git(top, "diff", "--name-only", "--merge-base", facts.base))
      const untracked = lines(status).filter((line) => line.startsWith("? ")).map((line) => line.slice(2))
      facts.changed = [...new Set([...diff, ...untracked])]
    } catch {}
    if (plan) facts.declared = declaredPaths(await readFile(resolve(top, plan), "utf8").catch(() => ""))
    if (facts.branch && facts.upstream && facts.branch !== facts.base.replace(/^origin\//, "")) facts.head = facts.branch
    else facts.head = remembered
  } else facts.head = remembered
  if (!facts.head) return facts
  try {
    const out = await run("gh", ["pr", "list", "--head", facts.head, "--state", "all", "--json", "state,title", "--limit", "1"],
      { cwd: exists ? top : fallback, timeout: 5000 })
    const [first] = JSON.parse(out)
    if (first) facts.pr = { state: first.state, title: first.title }
  } catch {}
  return facts
}
