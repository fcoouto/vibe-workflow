import assert from "node:assert/strict"
import { execFileSync } from "node:child_process"
import { chmodSync, mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import test from "node:test"
import { artifact, branchOf, commonDir, declaredPaths, defaultRun, readFacts } from "../../src/session-facts.js"

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

test("declaredPaths also reads paths on the Files line itself", () => {
  assert.deepEqual(declaredPaths("**Files:** Modify `a/b.md` (`FEATURES`), `tests/c.js`\n\nText `x.js`\n"), ["a/b.md", "tests/c.js"])
  assert.deepEqual(declaredPaths("**Files:** Modify `a.js:10-20`\n- Test: `t.js`\n\nText `x.js`\n"), ["a.js", "t.js"])
  assert.deepEqual(declaredPaths("**Files:** none here\n\nText `x.js`\n"), [])
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
  assert.deepEqual(calls.at(-1).args, ["pr", "list", "--head", "feat/x", "--state", "all", "--json", "state,title,createdAt", "--limit", "1"])
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

test("local-only facts read a pushed checkout, declared paths and untracked files without gh", async (t) => {
  const { top, main } = setup(t)
  const { calls, run } = recorder()
  git(top, "switch", "-qc", "feat/x")
  commit(top, "src/app.js")
  git(top, "push", "-qu", "origin", "feat/x")
  writeFileSync(join(top, "src/new.js"), "untracked\n")
  writeFileSync(join(top, "README.md"), "**Files:** `src/app.js`, `src/new.js`\n")
  const facts = await readFacts({ top, fallback: main, plan: "README.md", localOnly: true, run })
  assert.equal(facts.pr, null)
  assert.deepEqual(facts.declared, ["src/app.js", "src/new.js"])
  assert.ok(facts.changed.includes("src/app.js"))
  assert.ok(facts.changed.includes("src/new.js"))
  assert.deepEqual(calls, [])
})

test("Git facts keep non-ASCII and newline filenames under docs", async (t) => {
  const { top, main } = setup(t)
  commit(top, "docs/café.md")
  writeFileSync(join(top, "docs/first\nsecond.md"), "untracked\n")
  const got = await readFacts({ top, fallback: main, localOnly: true })
  assert.deepEqual(got.changed.sort(), ["docs/café.md", "docs/first\nsecond.md"])
})

const dated = { state: "MERGED", title: "Add x", createdAt: "2026-11-25T10:00:00Z" }
const at = Date.parse(dated.createdAt)

test("seen is used only when the checkout is gone or detached", async (t) => {
  const { top, main } = setup(t)
  const { calls, run } = recorder({ "feat/x": [dated] })
  const facts = () => readFacts({ top, fallback: main, run, seen: "feat/x", bound: at })
  git(top, "switch", "-q", "-c", "feat/x")
  let got = await facts()
  assert.deepEqual([got.branch, got.via, got.head, got.detached, got.gone], ["feat/x", null, null, false, false])
  assert.equal(calls.length, 0, "an existing checkout on its unpushed branch makes no gh call")
  git(top, "switch", "-q", "--detach")
  got = await facts()
  assert.deepEqual([got.detached, got.via, got.head, got.pr], [true, "seen", "feat/x", { state: "MERGED", title: "Add x" }])
  rmSync(top, { recursive: true, force: true })
  got = await facts()
  assert.deepEqual([got.exists, got.gone, got.via, got.pr?.state], [false, true, "seen", "MERGED"])
  assert.equal(calls.at(-1).cwd, main)
})

test("a failed read holds the cells", async (t) => {
  const { top, main, root } = setup(t)
  const { calls, run } = recorder({ "feat/x": [dated] })
  const failing = async (command, args, options) => {
    if (command === "git" && args[0] === "status") throw Object.assign(Error("x"), { code: 128 })
    return run(command, args, options)
  }
  let got = await readFacts({ top, fallback: main, run: failing, seen: "feat/x", bound: at })
  assert.deepEqual([got.exists, got.detached, got.via, got.pr], [true, false, null, null])
  const file = join(root, "not-a-folder")
  writeFileSync(file, "x\n")
  got = await readFacts({ top: file, fallback: main, run, seen: "feat/x", bound: at })
  assert.deepEqual([got.exists, got.gone, got.via, got.pr], [false, false, null, null])
  const locked = join(root, "locked")
  mkdirSync(join(locked, "child"), { recursive: true })
  chmodSync(locked, 0o000)
  try {
    got = await readFacts({ top: join(locked, "child"), fallback: main, run, seen: "feat/x", bound: at })
  } finally { chmodSync(locked, 0o755) }
  assert.deepEqual([got.exists, got.gone, got.via, got.pr], [false, false, null, null], "a stat error other than ENOENT is not removal")
  assert.equal(calls.length, 0)
})

test("a PR created before the bind does not count through seen", async (t) => {
  const { top, main } = setup(t)
  const { run } = recorder({ "feat/x": [dated] })
  git(top, "switch", "-q", "--detach")
  const pr = async (bound) => (await readFacts({ top, fallback: main, run, seen: "feat/x", bound })).pr
  assert.equal(await pr(at + 30000), null)
  assert.equal((await pr(at))?.state, "MERGED")
  const unbound = await readFacts({ top, fallback: main, run, seen: "feat/x" })
  assert.deepEqual([unbound.via, unbound.pr], [null, null])
})

test("remembered still wins over seen and needs no date", async (t) => {
  const { top, main } = setup(t)
  const { calls, run } = recorder({ "feat/r": [{ state: "OPEN", title: "r" }], "feat/x": [dated] })
  git(top, "switch", "-q", "--detach")
  const got = await readFacts({ top, fallback: main, run, remembered: "feat/r", seen: "feat/x", bound: at + 99999 })
  assert.deepEqual([got.via, got.head, got.pr], ["remembered", "feat/r", { state: "OPEN", title: "r" }])
  assert.equal(calls.length, 1)
})

test("commonDir is equal for a checkout and its linked worktree and null on failure", async (t) => {
  const { top, main, root } = setup(t)
  const shared = await commonDir({ top: main })
  assert.ok(shared && shared.endsWith(".git"))
  assert.equal(await commonDir({ top }), shared)
  assert.equal(await commonDir({ top: join(root, "missing") }), null)
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

test("branchOf is the checked-out branch, never the default branch, a detached head or a failed read", async (t) => {
  const { top, main, root } = setup(t)
  assert.equal(await branchOf({ top }), null)
  git(top, "switch", "-q", "-c", "feat/x")
  assert.equal(await branchOf({ top }), "feat/x")
  git(top, "switch", "-q", "--detach")
  git(main, "switch", "-q", "main")
  assert.equal(await branchOf({ top: main }), null)
  assert.equal(await branchOf({ top: join(root, "missing") }), null)
  assert.equal(await branchOf({ top, run: async () => { throw new Error("x") } }), null)
})

test("readFacts reports which read failed, as codes without messages", async (t) => {
  const { top, main } = setup(t)
  const failing = (status, gh) => async (command, args, options) => {
    if (command === "gh") throw gh
    if (args[0] === "status") throw status
    return defaultRun(command, args, options)
  }
  const missing = Object.assign(new Error("spawn git ENOENT /secret/path"), { code: "ENOENT" })
  const slow = Object.assign(new Error("Command failed: gh https://token@example.invalid"), { killed: true })
  let got = await readFacts({ top, remembered: "feat/x", fallback: main, run: failing(missing, slow) })
  assert.deepEqual(got.errors, [{ step: "status", code: "ENOENT" }, { step: "pr", code: "timeout" }])
  assert.doesNotMatch(JSON.stringify(got.errors), /secret|token|spawn/)
  got = await readFacts({ top, remembered: "feat/x", fallback: main,
    run: failing(Object.assign(new Error("x"), { code: 128 }), new SyntaxError("Unexpected token")) })
  assert.deepEqual(got.errors, [{ step: "status", code: "exit:128" }, { step: "pr", code: "error" }])
  got = await readFacts({ top, remembered: "feat/x", fallback: main,
    run: failing(Object.assign(new Error("x"), { code: "has space /secret" }), Object.assign(new Error("y"), { code: "ERR_X", killed: true })) })
  assert.deepEqual(got.errors, [{ step: "status", code: "error" }, { step: "pr", code: "timeout" }])
  assert.deepEqual((await readFacts({ top, fallback: main, run: recorder().run })).errors, [])
})

test("readFacts reports a failed diff and a missing plan by step and code", async (t) => {
  const { top, main } = setup(t)
  const run = async (command, args, options) => {
    if (args[0] === "diff") throw Object.assign(new Error("fatal: bad revision /secret/path"), { code: 129 })
    return defaultRun(command, args, options)
  }
  const got = await readFacts({ top, plan: "docs/superpowers/plans/missing.md", fallback: main, run })
  assert.deepEqual(got.errors, [{ step: "diff", code: "exit:129" }, { step: "plan", code: "ENOENT" }])
  assert.doesNotMatch(JSON.stringify(got.errors), /secret|revision/)
})

test("git is told not to take optional locks, so a tool-time read never writes the index", async () => {
  const env = await defaultRun(process.execPath, ["-e", "process.stdout.write(String(process.env.GIT_OPTIONAL_LOCKS))"],
    { cwd: tmpdir(), timeout: 5000 })
  assert.equal(env, "0")
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
