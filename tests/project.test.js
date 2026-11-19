import assert from "node:assert/strict"
import { execFileSync } from "node:child_process"
import { chmod, link, mkdir, mkdtemp, readFile, realpath, rm, stat, symlink, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"

async function fixture(t) {
  const root = await realpath(await mkdtemp(join(tmpdir(), "vibe-project-")))
  t.after(() => rm(root, { recursive: true }))
  const module = await import("../project.js").catch((error) => {
    if (error.code !== "ERR_MODULE_NOT_FOUND") throw error
  })
  assert.ok(module?.createProjectSettings, "project-settings interface is missing")
  const folder = join(root, ".vibe-workflow"), path = join(folder, "settings.json")
  return { root, folder, path, project: module.createProjectSettings(root), create: module.createProjectSettings,
    async write(value) {
      await mkdir(folder, { recursive: true })
      await writeFile(path, typeof value === "string" ? value : JSON.stringify(value))
    } }
}

test("preview defaults on without writing; confirmed apply is idempotent", async (t) => {
  const { folder, path, project } = await fixture(t)
  const preview = await project.preview()
  assert.equal(preview.enabled, true)
  assert.equal(preview.change, "create")
  await assert.rejects(stat(folder), { code: "ENOENT" })
  assert.equal((await project.apply(true, preview.snapshot)).state, "saved")
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")), { version: 1, smart_router: { enabled: true } })
  const repeat = await project.preview()
  assert.equal(repeat.change, "none")
  assert.equal((await project.apply(true, repeat.snapshot)).state, "unchanged")
})

test("repeat onboarding preserves off and unknown fields", async (t) => {
  const { path, project, write } = await fixture(t)
  await write({ version: 1, future: "PRIVATE-SENTINEL", smart_router: { enabled: false, future: 3 } })
  assert.equal((await project.inspect()).state, "off")
  assert.equal((await project.preview()).enabled, false)
  const preview = await project.preview(true)
  assert.ok(!JSON.stringify(preview).includes("PRIVATE-SENTINEL"))
  assert.equal(preview.change, "update")
  assert.equal((await project.apply(true, preview.snapshot)).state, "saved")
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")), {
    version: 1, future: "PRIVATE-SENTINEL", smart_router: { enabled: true, future: 3 },
  })
})

test("apply refuses numbers that cannot round-trip without changing existing settings", async (t) => {
  const { path, project, write } = await fixture(t)
  for (const number of ["9007199254740993", "1e400", "0.1234567890123456789", "1e-400", "-0"]) {
    const original = `{"version":1,"smart_router":{"enabled":false},"future":{"value":${number}}}\n`
    await write(original)
    const preview = await project.preview(true)
    assert.equal(await project.apply(true, preview.snapshot).then(result => result.reason),
      "project_settings_lossy", number)
    assert.equal(await readFile(path, "utf8"), original)
  }
  await write('{"version":1,"future":[1.2500e+2,0.1000,"escaped \\"1e400\\""]}')
  const preview = await project.preview()
  assert.equal((await project.apply(true, preview.snapshot)).state, "saved")
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")).future, [125, 0.1, 'escaped "1e400"'])
})

test("nested locations, separate repositories and worktrees resolve to their own checkout", async (t) => {
  const { root, path, write, create } = await fixture(t)
  const git = (...args) => execFileSync("git", ["-C", root, "-c", "core.hooksPath=/dev/null", ...args], {
    env: Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_"))),
    stdio: "pipe",
  })
  git("init", "-q")
  git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
    "commit", "--allow-empty", "-qm", "fixture")
  await write({ version: 1, smart_router: { enabled: false } })
  const before = await readFile(path, "utf8")
  const sub = join(root, "sub"), linked = join(root, "linked")
  await mkdir(sub)
  assert.equal((await create(sub).inspect()).root, root)
  git("-C", sub, "init", "-q")
  assert.equal((await create(sub).inspect()).root, sub)
  git("worktree", "add", "--detach", linked)
  const project = create(linked), preview = await project.preview()
  assert.equal(preview.root, linked)
  assert.equal((await project.apply(true, preview.snapshot)).state, "saved")
  assert.equal(await readFile(path, "utf8"), before)
})

test("duplicate settings keys block without overwriting, even when escaped", async (t) => {
  const { path, project, write } = await fixture(t)
  for (const original of [
    '{"version":1,"smart_router":{"enabled":false,"enabled":true}}',
    String.raw`{"version":1,"future":[{"keep":1,"\u006beep":2}]}`,
  ]) {
    await write(original)
    assert.equal((await project.inspect()).reason, "project_settings_invalid")
    assert.equal((await project.apply(true, "0".repeat(64))).reason, "project_settings_invalid")
    assert.equal(await readFile(path, "utf8"), original)
  }
})

test("invalid, oversized and future settings block without overwriting", async (t) => {
  const { path, project, write } = await fixture(t)
  for (const value of ["PRIVATE-SENTINEL", "x".repeat(65537), { version: 2 },
    { version: 1, smart_router: { enabled: 1 } }]) {
    await write(value)
    const original = await readFile(path, "utf8"), preview = await project.preview()
    assert.equal(preview.state, "blocked")
    assert.ok(!JSON.stringify(preview).includes("PRIVATE-SENTINEL"))
    assert.equal((await project.apply(true, "0".repeat(64))).state, "blocked")
    assert.equal(await readFile(path, "utf8"), original)
  }
})

test("link and unsafe-mode targets are refused", async (t) => {
  const { root, folder, path, project, write } = await fixture(t)
  const original = join(root, "original")
  await writeFile(original, '{"version":1}')
  await symlink(root, folder)
  assert.equal((await project.inspect()).state, "blocked")
  await rm(folder)
  await mkdir(folder)
  for (const makeLink of [symlink, link]) {
    await makeLink(original, path)
    assert.equal((await project.inspect()).state, "blocked")
    await rm(path)
  }
  await write({ version: 1 })
  await chmod(path, 0o666)
  assert.equal((await project.inspect()).state, "blocked")
  assert.equal(await readFile(original, "utf8"), '{"version":1}')
})

test("stale previews, held locks and failed replacement preserve existing bytes", async (t) => {
  const { folder, path, project, write } = await fixture(t)
  await write({ version: 1 })
  const stale = await project.preview()
  await write({ version: 1, future: 2 })
  const before = await readFile(path, "utf8"), latest = await project.preview()
  assert.equal((await project.apply(true, stale.snapshot)).reason, "project_settings_changed")
  const lock = join(folder, ".settings.lock")
  await writeFile(lock, "another writer")
  assert.equal((await project.apply(true, latest.snapshot)).reason, "project_settings_busy")
  assert.equal(await readFile(lock, "utf8"), "another writer")
  await rm(lock)
  const fs = await import("node:fs/promises")
  const sync = (await import("node:module")).syncBuiltinESMExports
  const rename = fs.default.rename
  fs.default.rename = async () => { throw new Error("synthetic replacement failure") }
  sync()
  try {
    assert.equal((await project.apply(true, latest.snapshot)).state, "blocked")
  } finally { fs.default.rename = rename; sync() }
  assert.equal(await readFile(path, "utf8"), before)
  await assert.rejects(stat(lock), { code: "ENOENT" })
})
