import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { mkdir, mkdtemp, readFile, realpath, rename, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"

const env = { ...Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_"))),
  GIT_CONFIG_GLOBAL: "/dev/null", GIT_CONFIG_NOSYSTEM: "1" }
const identity = ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
  "-c", "core.hooksPath=/dev/null", "-c", "protocol.file.allow=always"]
const git = (cwd, ...args) => {
  const done = spawnSync("git", [...identity, ...args], { cwd, env, encoding: "utf8" })
  return { ok: done.status === 0, out: `${done.stdout}${done.stderr}`.trim() }
}
const must = (cwd, ...args) => {
  const done = git(cwd, ...args)
  assert.ok(done.ok, `git ${args.join(" ")}: ${done.out}`)
  return done.out
}

async function repository(t) {
  const root = await realpath(await mkdtemp(join(tmpdir(), "vibe-cleanup-")))
  t.after(() => rm(root, { recursive: true, force: true }))
  const origin = join(root, "origin.git"), primary = join(root, "primary")
  must(root, "init", "-q", "--bare", "-b", "main", origin)
  must(root, "init", "-q", "-b", "main", primary)
  must(primary, "remote", "add", "origin", origin)
  must(primary, "commit", "-q", "--allow-empty", "-m", "base")
  must(primary, "push", "-q", "-u", "origin", "main")
  const common = must(primary, "rev-parse", "--absolute-git-dir")
  const worktree = async (name, { push = false, extra = false } = {}) => {
    const path = join(root, name)
    must(primary, "worktree", "add", "-q", "-b", name, path)
    await writeFile(join(path, `${name}.txt`), name)
    must(path, "add", ".")
    must(path, "commit", "-q", "-m", `work ${name}`)
    if (push) must(path, "push", "-q", "-u", "origin", name)
    if (extra) {
      await writeFile(join(path, `${name}-extra.txt`), "extra")
      must(path, "add", ".")
      must(path, "commit", "-q", "-m", `extra ${name}`)
    }
    return path
  }
  // The documented sequence, run from inside the worktree: detach, then the safe delete against the common git dir.
  const safeDelete = (path, name) => {
    must(path, "switch", "-q", "--detach")
    return git(path, `--git-dir=${common}`, "branch", "-d", name)
  }
  return { root, primary, common, origin, worktree, safeDelete }
}

test("the documented sequence refuses commits that exist nowhere else and deletes the rest", async (t) => {
  const { primary, origin, worktree, safeDelete } = await repository(t)
  const unpushed = await worktree("unpushed")
  const pushedUnmerged = await worktree("pushedunmerged", { push: true })
  const viaMerge = await worktree("viamerge", { push: true })
  const squashPushed = await worktree("squashpushed", { push: true })
  const squashPruned = await worktree("squashpruned", { push: true })
  const pushedPlus = await worktree("pushedplus", { push: true, extra: true })
  must(primary, "merge", "-q", "--no-ff", "viamerge", "-m", "merge viamerge")
  must(primary, "merge", "-q", "--squash", "squashpushed")
  must(primary, "commit", "-q", "-m", "squash squashpushed")
  must(primary, "merge", "-q", "--squash", "squashpruned")
  must(primary, "commit", "-q", "-m", "squash squashpruned")
  must(origin, "branch", "-D", "squashpruned")
  must(primary, "fetch", "-q", "--prune")
  const head = () => must(primary, "symbolic-ref", "--short", "HEAD")
  const refused = (done, name) => assert.ok(!done.ok && /not fully merged/.test(done.out), `${name}: ${done.out}`)
  refused(safeDelete(unpushed, "unpushed"), "unpushed")
  assert.equal(head(), "main")
  assert.ok(safeDelete(pushedUnmerged, "pushedunmerged").ok, "pushed unmerged: commits are on the upstream")
  assert.equal(head(), "main")
  assert.ok(safeDelete(viaMerge, "viamerge").ok, "merge-commit merged")
  assert.equal(head(), "main")
  assert.ok(safeDelete(squashPushed, "squashpushed").ok, "squash-merged while its upstream exists")
  assert.equal(head(), "main")
  refused(safeDelete(squashPruned, "squashpruned"), "squashpruned")
  assert.equal(head(), "main")
  refused(safeDelete(pushedPlus, "pushedplus"), "pushedplus")
  assert.equal(head(), "main")
  assert.equal(must(primary, "branch", "--list", "unpushed", "squashpruned", "pushedplus").split("\n").length, 3)
  assert.equal(must(primary, "branch", "--list", "viamerge", "squashpushed", "pushedunmerged"), "")
})

test("a branch checked out in a worktree cannot be deleted, and a dirty worktree cannot be removed", async (t) => {
  const { primary, common, worktree } = await repository(t)
  const path = await worktree("dirty", { push: true })
  const checkedOut = git(path, `--git-dir=${common}`, "branch", "-d", "dirty")
  assert.ok(!checkedOut.ok && /used by worktree/.test(checkedOut.out), checkedOut.out)
  must(path, "switch", "-q", "--detach")
  await writeFile(join(path, "untracked.txt"), "x")
  const refused = git(primary, "worktree", "remove", path)
  assert.ok(!refused.ok && /modified or untracked/.test(refused.out), refused.out)
  await rm(join(path, "untracked.txt"))
  assert.ok(git(primary, "worktree", "remove", path).ok)
  assert.equal(must(primary, "symbolic-ref", "--short", "HEAD"), "main")
})

test("git worktree remove deletes ignored files, and status --porcelain --ignored is what shows them", async (t) => {
  const { primary, worktree } = await repository(t)
  const path = await worktree("ignored", { push: true })
  await writeFile(join(primary, ".gitignore"), "/.private/\n.env\n")
  must(primary, "add", ".gitignore")
  must(primary, "commit", "-q", "-m", "ignore")
  must(path, "merge", "-q", "main")
  await mkdir(join(path, ".private"))
  await writeFile(join(path, ".private", "ledger.md"), "evidence")
  await writeFile(join(path, ".env"), "SECRET=1")
  assert.equal(must(path, "status", "--porcelain"), "", "plain status hides ignored files")
  assert.match(must(path, "status", "--porcelain", "--ignored"), /!! \.env[\s\S]*!! \.private\//)
  must(path, "switch", "-q", "--detach")
  assert.ok(git(primary, "worktree", "remove", path).ok, "git removes it without force, ignored files included")
})

test("a folder that holds only a repository is invisible to status, and git removes it with the worktree", async (t) => {
  const { primary, worktree } = await repository(t)
  const path = await worktree("nested", { push: true })
  await mkdir(join(path, "saved", ".git"), { recursive: true })
  await writeFile(join(path, "saved", ".git", "config"), "[core]\n")
  assert.equal(must(path, "status", "--porcelain", "--ignored"), "", "git lists nothing for it")
  assert.ok(git(primary, "worktree", "remove", path).ok, "git removes it without force")
  await assert.rejects(readFile(join(path, "saved", ".git", "config")), "the nested files are gone")
})

test("git reads a worktree pointer file that ends in CR LF", async (t) => {
  const { worktree } = await repository(t)
  const path = await worktree("crlf")
  const pointer = await readFile(join(path, ".git"), "utf8")
  assert.match(pointer, /^gitdir: [^\r\n]+\n$/)
  await writeFile(join(path, ".git"), pointer.replace("\n", "\r\n"))
  assert.equal(must(path, "rev-parse", "--abbrev-ref", "HEAD"), "crlf")
})

// The reference's rule: status `!!`, and the last path component, as git prints it, equals a listed name (`*.pyc`: ends in `.pyc`).
const LISTED = ["node_modules/", "__pycache__/", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/", ".DS_Store"]
const rebuildable = (line) => {
  if (!line.startsWith("!! ")) return false
  const last = line.slice(3).match(/[^/]*\/?$/)[0]
  return LISTED.includes(last) || last.endsWith(".pyc")
}
const leftovers = (path) => must(path, "status", "--porcelain", "--ignored").split("\n").filter(Boolean)
const ignore = async (path, rules) => {
  await writeFile(join(path, ".gitignore"), rules)
  must(path, "add", ".gitignore")
  must(path, "commit", "-q", "-m", "ignore")
}

test("listed caches can be deleted after the merge, then the worktree is removable", async (t) => {
  const { primary, worktree } = await repository(t)
  const path = await worktree("caches", { push: true })
  // A tracked file keeps `src/` visible: a folder holding only ignored entries collapses into one `!! src/` line.
  await mkdir(join(path, "src"))
  await writeFile(join(path, "src", "module.py"), "")
  must(path, "add", "src")
  await ignore(path, "node_modules/\n__pycache__/\n.DS_Store\n")
  must(primary, "merge", "-q", "--no-ff", "caches", "-m", "merge caches")
  await mkdir(join(path, "node_modules", "pkg"), { recursive: true })
  await writeFile(join(path, "node_modules", "pkg", "index.js"), "")
  await mkdir(join(path, "src", "__pycache__"), { recursive: true })
  await writeFile(join(path, "src", "__pycache__", "module.cpython-311.pyc"), "")
  await writeFile(join(path, ".DS_Store"), "")
  const lines = leftovers(path)
  assert.deepEqual(lines, ["!! .DS_Store", "!! node_modules/", "!! src/__pycache__/"])
  for (const line of lines) assert.ok(rebuildable(line), line)
  for (const line of lines) await rm(join(path, line.slice(3).replace(/\/$/, "")), { recursive: true })
  assert.deepEqual(leftovers(path), [])
  must(path, "switch", "-q", "--detach")
  assert.ok(git(primary, "worktree", "remove", path).ok, "removable without force")
})

test("look-alike and unignored caches are not rebuildable", async (t) => {
  const { worktree } = await repository(t)
  const path = await worktree("lookalike", { push: true })
  await mkdir(join(path, "node_modules"))
  await writeFile(join(path, "node_modules", "index.js"), "")
  assert.deepEqual(leftovers(path), ["?? node_modules/"])
  await rm(join(path, "node_modules"), { recursive: true })
  await ignore(path, "mynode_modules/\n")
  await mkdir(join(path, "mynode_modules"))
  await writeFile(join(path, "mynode_modules", "index.js"), "")
  assert.deepEqual(leftovers(path), ["!! mynode_modules/"])
  for (const line of ["?? node_modules/", "!! mynode_modules/"]) assert.ok(!rebuildable(line), line)
})

test("unlisted ignored folders, .env and untracked files stay real work", async (t) => {
  const { worktree } = await repository(t)
  const path = await worktree("realwork", { push: true })
  await ignore(path, "/build/\n.env\n")
  await mkdir(join(path, "build"))
  await writeFile(join(path, "build", ".env"), "SECRET=1")
  await writeFile(join(path, ".env"), "SECRET=1")
  await writeFile(join(path, "notes.md"), "notes")
  const lines = leftovers(path)
  assert.deepEqual([...lines].sort(), ["!! .env", "!! build/", "?? notes.md"], "an ignored folder collapses into one line")
  for (const line of lines) assert.ok(!rebuildable(line), line)
})

test("worktree repair fixes a worktree after the repository moved", async (t) => {
  const { root, primary, worktree } = await repository(t)
  const path = await worktree("moved", { push: true })
  const moved = join(root, "primary-moved")
  await rename(primary, moved)
  assert.ok(!git(path, "status").ok, "the worktree's .git file names the old path")
  must(moved, "worktree", "repair", path)
  must(path, "status")
  assert.equal(must(path, ...commonDir), must(moved, ...commonDir), "repaired into this repository")
})

test("the documented switch and branch delete run no hook when hooks are pointed at nothing", async (t) => {
  const { root, common, worktree } = await repository(t)
  const marker = join(root, "hook-ran")
  await mkdir(join(common, "hooks"), { recursive: true })
  for (const name of ["post-checkout", "reference-transaction"]) {
    await writeFile(join(common, "hooks", name), `#!/bin/sh\necho ${name} >> '${marker}'\n`, { mode: 0o755 })
  }
  // No `core.hooksPath` from the fixture here: only what the command itself passes.
  const raw = (cwd, ...args) => spawnSync("git", args, { cwd, env, encoding: "utf8" })
  const quiet = await worktree("quiet", { push: true })
  const loud = await worktree("loud", { push: true })
  await assert.rejects(readFile(marker), "the fixture itself ran no hook")
  const detached = raw(quiet, "-c", "core.hooksPath=/dev/null", "switch", "-q", "--detach")
  assert.equal(detached.status, 0, detached.stderr)
  const deleted = raw(quiet, "-c", "core.hooksPath=/dev/null", `--git-dir=${common}`, "branch", "-d", "quiet")
  assert.equal(deleted.status, 0, deleted.stderr)
  await assert.rejects(readFile(marker), "the documented commands ran no hook")
  assert.equal(raw(loud, "switch", "-q", "--detach").status, 0)
  assert.equal(raw(loud, `--git-dir=${common}`, "branch", "-d", "loud").status, 0)
  const ran = await readFile(marker, "utf8")
  assert.match(ran, /post-checkout/, "without the setting, the switch runs the hook")
  assert.match(ran, /reference-transaction/, "without the setting, the branch delete runs the hook")
})

const commonDir = ["rev-parse", "--path-format=absolute", "--git-common-dir"]
// Raw stdout: `-z` output is NUL-separated and must not be trimmed or mixed with stderr.
const entries = (cwd, ...args) => {
  const done = spawnSync("git", [...identity, ...args], { cwd, env, encoding: "utf8" })
  assert.equal(done.status, 0, done.stderr)
  return done.stdout.split("\0").slice(0, -1)
}

test("the -z listing gives raw names where the line form quotes and escapes them", async (t) => {
  const { worktree } = await repository(t)
  const path = await worktree("names", { push: true })
  const names = ['say "hi".md', "caf\u00e9.md", "line\nbreak.md"]
  for (const name of names) await writeFile(join(path, name), "")
  const lines = leftovers(path)
  assert.ok(lines.every((line) => line.startsWith('?? "')), "the line form wraps each of these names in quotes")
  assert.ok(!lines.some((line) => names.includes(line.slice(3))), "and none of them is the stored name")
  assert.deepEqual(entries(path, "status", "--porcelain=v1", "-z", "--ignored").sort(), names.map((name) => `?? ${name}`).sort())
})

test("an untracked folder is one line until --untracked-files=all expands it; an ignored folder never expands", async (t) => {
  const { worktree } = await repository(t)
  const path = await worktree("collapsed", { push: true })
  await ignore(path, "/build/\n")
  for (const name of ["docs/a/result.md", "docs/b/member.md", "build/out.js"]) {
    await mkdir(join(path, name, ".."), { recursive: true })
    await writeFile(join(path, name), "")
  }
  assert.deepEqual(entries(path, "status", "--porcelain=v1", "-z", "--ignored").sort(), ["!! build/", "?? docs/"])
  assert.deepEqual(entries(path, "status", "--porcelain=v1", "-z", "--untracked-files=all").sort(),
    ["?? docs/a/result.md", "?? docs/b/member.md"])
})

test("a named worktree is known by its live branch, top folder and common git dir", async (t) => {
  const { root, primary, worktree } = await repository(t)
  const path = await worktree("named", { push: true })
  assert.equal(must(path, "rev-parse", "--show-toplevel"), path)
  assert.equal(must(path, ...commonDir), must(primary, ...commonDir))
  assert.equal(must(path, "symbolic-ref", "--short", "-q", "HEAD"), "named")
  must(path, "switch", "-q", "-c", "reused")
  assert.equal(must(path, "symbolic-ref", "--short", "-q", "HEAD"), "reused", "a stale note names another branch")
  must(path, "switch", "-q", "--detach")
  assert.ok(!git(path, "symbolic-ref", "--short", "-q", "HEAD").ok, "detached: no branch")
  const other = join(root, "other")
  must(root, "init", "-q", "-b", "main", other)
  assert.notEqual(must(other, ...commonDir), must(primary, ...commonDir), "a folder of another repository")
})
