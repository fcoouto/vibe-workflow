import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { mkdir, mkdtemp, realpath, rm, writeFile } from "node:fs/promises"
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
