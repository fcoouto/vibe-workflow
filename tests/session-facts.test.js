import assert from "node:assert/strict"
import { execFileSync } from "node:child_process"
import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import test from "node:test"
import { artifact, declaredPaths, defaultRun, readFacts } from "../session-facts.js"

const git = (cwd, ...args) => execFileSync("git", ["-C", cwd, "-c", "core.hooksPath=/dev/null", ...args], { encoding: "utf8" })
const tmp = (t) => {
  const dir = realpathSync(mkdtempSync(join(tmpdir(), "session-facts-")))
  t.after(() => rmSync(dir, { recursive: true, force: true }))
  return dir
}
const commit = (cwd, file, text = "x\n") => {
  mkdirSync(join(cwd, file, ".."), { recursive: true })
  writeFileSync(join(cwd, file), text)
  git(cwd, "add", file)
  git(cwd, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", file)
}
const pr = { state: "OPEN", title: "Add x" }
function recorder(table = { "feat/x": [pr] }) {
  const calls = []
  const run = async (command, args, options) => {
    if (command !== "gh") return defaultRun(command, args, options)
    calls.push({ args, cwd: options.cwd })
    return JSON.stringify(table[args[args.indexOf("--head") + 1]] ?? [])
  }
  return { calls, run }
}
function setup(t) {
  const root = tmp(t)
  const origin = join(root, "origin.git"), main = join(root, "main"), top = join(root, "work")
  git(root, "init", "-q", "--bare", "-b", "main", origin)
  git(root, "init", "-q", "-b", "main", main)
  commit(main, "README.md")
  git(main, "remote", "add", "origin", origin)
  git(main, "push", "-q", "-u", "origin", "main")
  git(main, "switch", "-q", "--detach")
  git(main, "worktree", "add", "-q", "--detach", top)
  return { root, main, top }
}

test("artifact recognizes specs and plans and derives top and slug", () => {
  assert.deepEqual(artifact("/w/a/docs/superpowers/specs/2026-11-20-session-titles-design.md"),
    { kind: "spec", top: "/w/a", slug: "session titles" })
  assert.equal(artifact("/w/a/docs/superpowers/plans/2026-11-20-x.md").kind, "plan")
  for (const path of ["/w/notes-docs/superpowers/specs/a.md", "/w/docs/superpowers/other/a.md",
    "/w/docs/superpowers/specs/a/b.md", "/w/docs/superpowers/specs/a.txt"]) assert.equal(artifact(path), null)
})

test("declaredPaths reads path-like backticked tokens inside Files blocks only", () => {
  const plan = "Intro `not/this.js`\n\n**Files:**\n- Create: `a.js`\n- Modify: `b/c.js:10-20` (`FEATURES`), through `impeccable`\n- Test: `tests/a.test.js`\n\nText `x.js`\n\n**Files:**\n- Modify: `a.js`, `README.md`\n"
  assert.deepEqual(declaredPaths(plan), ["a.js", "b/c.js", "tests/a.test.js", "README.md"])
  assert.deepEqual(declaredPaths("no files here"), [])
})

test("readFacts follows the checkout through detached, branch, pushed and default states", async (t) => {
  const { top, main } = setup(t)
  const { calls, run } = recorder()
  const facts = (extra = {}) => readFacts({ top, fallback: main, run, ...extra })

  let got = await facts()
  assert.deepEqual([got.exists, got.branch, got.upstream, got.head, got.pr], [true, null, false, null, null])
  assert.equal(calls.length, 0)

  commit(top, "docs/superpowers/specs/s.md")
  commit(top, "src/app.js")
  writeFileSync(join(top, "README.md"), "edited\n")
  writeFileSync(join(top, "new.js"), "n\n")
  got = await facts()
  assert.deepEqual([...got.changed].sort(), ["README.md", "docs/superpowers/specs/s.md", "new.js", "src/app.js"])
  assert.equal(got.base, "origin/main")

  git(top, "switch", "-q", "-c", "feat/x")
  got = await facts()
  assert.deepEqual([got.branch, got.upstream, got.head], ["feat/x", false, null])
  assert.equal(calls.length, 0)

  git(top, "push", "-q", "-u", "origin", "feat/x")
  got = await facts()
  assert.deepEqual([got.branch, got.upstream, got.head, got.pr], ["feat/x", true, "feat/x", pr])
  assert.deepEqual(calls.at(-1).args, ["pr", "list", "--head", "feat/x", "--state", "all", "--json", "state,title", "--limit", "1"])
  assert.equal(calls.at(-1).cwd, top)

  git(top, "switch", "-q", "--detach")
  assert.equal((await facts()).head, null)
  got = await facts({ remembered: "feat/x" })
  assert.deepEqual([got.branch, got.head, got.pr], [null, "feat/x", pr])

  git(top, "stash", "-u", "-q")
  git(top, "switch", "-q", "main")
  git(top, "branch", "-q", "-u", "origin/main")
  const before = calls.length
  got = await facts()
  assert.deepEqual([got.branch, got.upstream, got.head], ["main", true, null])
  assert.equal(calls.length, before)
  got = await facts({ remembered: "feat/x" })
  assert.deepEqual([got.branch, got.head, got.pr], ["main", "feat/x", pr])
  assert.deepEqual(calls.at(-1).args.slice(0, 4), ["pr", "list", "--head", "feat/x"])
})

test("artifact finds the docs folder last when the checkout path contains one", () => {
  assert.deepEqual(artifact("/w/docs/superpowers/app/docs/superpowers/specs/2026-11-20-x-design.md"),
    { kind: "spec", top: "/w/docs/superpowers/app", slug: "x" })
})

test("readFacts reads declared paths from the plan under top", async (t) => {
  const { top, main } = setup(t)
  commit(top, "docs/superpowers/plans/p.md", "**Files:**\n- Create: `a.js`\n")
  const got = await readFacts({ top, plan: "docs/superpowers/plans/p.md", fallback: main, run: recorder().run })
  assert.deepEqual(got.declared, ["a.js"])
})

test("readFacts uses the remembered branch and fallback when top is gone", async (t) => {
  const { top, main } = setup(t)
  rmSync(top, { recursive: true, force: true })
  const { calls, run } = recorder()
  let got = await readFacts({ top, remembered: "feat/x", fallback: main, run })
  assert.deepEqual([got.exists, got.head, got.pr, got.changed], [false, "feat/x", pr, []])
  assert.equal(calls[0].cwd, main)
  got = await readFacts({ top, remembered: null, fallback: main, run })
  assert.equal(got.head, null)
  assert.equal(calls.length, 1)
})

test("readFacts resolves without rejecting outside a usable repository", async (t) => {
  const root = tmp(t)
  const { calls, run } = recorder()
  const plain = join(root, "plain")
  mkdirSync(plain)
  let got = await readFacts({ top: plain, fallback: root, run })
  assert.deepEqual([got.changed, got.pr, got.branch, got.head], [[], null, null, null])

  const lone = join(root, "lone")
  git(root, "init", "-q", "-b", "main", lone)
  commit(lone, "a.txt")
  writeFileSync(join(lone, "b.txt"), "b\n")
  got = await readFacts({ top: lone, fallback: root, run })
  assert.deepEqual([got.changed, got.pr], [[], null])
  assert.equal(calls.length, 0)
})
