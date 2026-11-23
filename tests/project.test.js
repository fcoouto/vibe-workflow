import assert from "node:assert/strict"
import { execFileSync } from "node:child_process"
import { chmod, link, mkdir, mkdtemp, readFile, realpath, rm, stat, symlink, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"

async function fixture(t) {
  const root = await realpath(await mkdtemp(join(tmpdir(), "vibe-project-")))
  t.after(() => rm(root, { recursive: true }))
  const module = await import("../src/project.js").catch((error) => {
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
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")), {
    version: 1, features: [{ smart_router: { enabled: true } }],
  })
  const repeat = await project.preview()
  assert.equal(repeat.change, "none")
  assert.equal((await project.apply(true, repeat.snapshot)).state, "unchanged")
})

test("repeat onboarding preserves off, unknown fields and feature order", async (t) => {
  const { path, project, write } = await fixture(t)
  const before = { future_a: { enabled: true, label: "PRIVATE-SENTINEL" } }
  const after = { future_b: { arbitrary: [null, 4] } }
  await write({ version: 1, future: "PRIVATE-SENTINEL",
    features: [before, { smart_router: { enabled: false, future: 3 } }, after] })
  assert.equal((await project.inspect()).state, "off")
  assert.equal((await project.preview()).enabled, false)
  const preview = await project.preview(true)
  assert.ok(!JSON.stringify(preview).includes("PRIVATE-SENTINEL"))
  assert.equal(preview.change, "update")
  assert.equal((await project.apply(true, preview.snapshot)).state, "saved")
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")), {
    version: 1, future: "PRIVATE-SENTINEL",
    features: [before, { smart_router: { enabled: true, future: 3 } }, after],
  })
})

test("an existing feature array without Smart Router gets one appended entry", async (t) => {
  const { path, project, write } = await fixture(t)
  for (const features of [[], [{ another_feature: { enabled: true } }]]) {
    await write({ version: 1, features })
    assert.equal((await project.inspect()).state, "not_onboarded")
    const preview = await project.preview(false)
    assert.equal(preview.change, "update")
    assert.equal((await project.apply(false, preview.snapshot)).state, "saved")
    assert.deepEqual(JSON.parse(await readFile(path, "utf8")), {
      version: 1, features: [...features, { smart_router: { enabled: false } }],
    })
  }
})

test("unsupported settings layouts never read or overwrite a project choice", async (t) => {
  const { path, project, write } = await fixture(t)
  for (const data of [
    { smart_router: { enabled: true } },
    { smart_router: { enabled: false }, features: [{ smart_router: { enabled: true } }] },
    {}, { features: {} }, { features: [null] }, { features: [{}] },
    { features: [{ "": {} }] }, { features: [{ another: true }] },
    { features: [{ first: {}, second: {} }] },
    { features: [{ another: {} }, { another: {} }] },
    { features: [{ smart_router: { enabled: true } }, { smart_router: { enabled: false } }] },
    { features: [{ smart_router: { enabled: 1 } }] },
  ]) {
    const original = JSON.stringify({ version: 1, ...data })
    await write(original)
    for (const answer of [await project.inspect(), await project.preview(),
      await project.apply(true, "0".repeat(64))]) {
      assert.equal(answer.reason, "project_settings_invalid", original)
    }
    assert.equal(await readFile(path, "utf8"), original)
  }
})

test("apply refuses numbers that cannot round-trip without changing existing settings", async (t) => {
  const { path, project, write } = await fixture(t)
  for (const number of ["9007199254740993", "1e400", "0.1234567890123456789", "1e-400", "-0"]) {
    const original = `{"version":1,"features":[{"smart_router":{"enabled":false}},{"future":{"value":${number}}}]}\n`
    await write(original)
    const preview = await project.preview(true)
    assert.equal(await project.apply(true, preview.snapshot).then(result => result.reason),
      "project_settings_lossy", number)
    assert.equal(await readFile(path, "utf8"), original)
  }
  await write('{"version":1,"features":[],"future":[1.2500e+2,0.1000,"escaped \\"1e400\\""]}')
  const preview = await project.preview()
  assert.equal((await project.apply(true, preview.snapshot)).state, "saved")
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")).future, [125, 0.1, 'escaped "1e400"'])
})

test("nested locations, separate repositories and worktrees keep their own checkout and name the main project", async (t) => {
  const { root, path, write, create } = await fixture(t)
  const git = (...args) => execFileSync("git", ["-C", root, "-c", "core.hooksPath=/dev/null", ...args], {
    env: Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_"))),
    stdio: "pipe",
  })
  git("init", "-q")
  git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
    "commit", "--allow-empty", "-qm", "fixture")
  await write({ version: 1, features: [{ smart_router: { enabled: false } }] })
  const before = await readFile(path, "utf8")
  const sub = join(root, "sub"), linked = join(root, "linked")
  await mkdir(sub)
  assert.deepEqual(await create(sub).inspect().then(({ root, project }) => ({ root, project })), { root, project: root })
  git("-C", sub, "init", "-q")
  assert.deepEqual(await create(sub).inspect().then(({ root, project }) => ({ root, project })), { root: sub, project: sub })
  git("worktree", "add", "--detach", linked)
  await mkdir(join(linked, "deep"))
  assert.equal((await create(join(linked, "deep")).inspect()).project, root)
  const project = create(linked), preview = await project.preview()
  assert.equal(preview.root, linked)
  assert.equal(preview.project, root)
  assert.equal(preview.path, join(linked, ".vibe-workflow", "settings.json"))
  const saved = await project.apply(true, preview.snapshot)
  assert.deepEqual([saved.state, saved.project], ["saved", root])
  assert.equal(await readFile(path, "utf8"), before)
})

test("worktrees of a bare repository keep their own checkout", async (t) => {
  const { root, create } = await fixture(t)
  const bare = join(root, "bare.git"), linked = join(root, "linked")
  const git = (...args) => execFileSync("git", ["-c", "core.hooksPath=/dev/null", ...args], {
    env: Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_"))),
    stdio: "pipe",
  })
  git("init", "-q", "--bare", bare)
  git("-C", bare, "worktree", "add", "-q", "--orphan", "-b", "main", linked)
  const { root: own, project } = await create(linked).inspect()
  assert.deepEqual({ own, project }, { own: linked, project: linked })
})

test("submodules and separate git directories name their own checkout as the project", async (t) => {
  const { root, create } = await fixture(t)
  const git = (...args) => execFileSync("git", ["-c", "core.hooksPath=/dev/null", "-c", "protocol.file.allow=always",
    "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", ...args], {
    env: Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_"))),
    stdio: "pipe",
  })
  const roots = async (directory) => {
    const { state, root, project } = await create(directory).inspect()
    return { state, root, project }
  }
  const source = join(root, "source"), superproject = join(root, "super"), separate = join(root, "separate")
  git("init", "-q", source)
  git("-C", source, "commit", "--allow-empty", "-qm", "fixture")
  git("init", "-q", superproject)
  git("-C", superproject, "submodule", "add", "-q", source, "s")
  const submodule = join(superproject, "s")
  assert.deepEqual(await roots(submodule), { state: "not_onboarded", root: submodule, project: submodule })
  git("init", "-q", "--separate-git-dir", join(root, "store.git"), separate)
  git("-C", separate, "commit", "--allow-empty", "-qm", "fixture")
  assert.deepEqual(await roots(separate), { state: "not_onboarded", root: separate, project: separate })
  const linked = join(root, "linked")
  git("-C", separate, "worktree", "add", "-q", "--detach", linked)
  assert.deepEqual(await roots(linked), { state: "not_onboarded", root: linked, project: linked })
})

test("a failing worktree listing keeps the checkout usable", async (t) => {
  const { root, create } = await fixture(t)
  const bin = join(root, "bin"), real = execFileSync("sh", ["-c", "command -v git"], { encoding: "utf8" }).trim()
  await mkdir(bin)
  await writeFile(join(bin, "git"), `#!/bin/sh\n[ "$1" = worktree ] && exit 129\nexec "${real}" "$@"\n`, { mode: 0o755 })
  const path = process.env.PATH
  process.env.PATH = `${bin}:${path}`
  t.after(() => { process.env.PATH = path })
  const checkout = join(root, "checkout")
  execFileSync(real, ["init", "-q", checkout], { stdio: "pipe" })
  await mkdir(join(checkout, ".vibe-workflow"))
  await writeFile(join(checkout, ".vibe-workflow", "settings.json"), '{"version":1,"features":[{"smart_router":{"enabled":true}}]}')
  const { state, root: own, project } = await create(checkout).inspect()
  assert.deepEqual({ state, own, project }, { state: "on", own: checkout, project: checkout })
})

test("duplicate settings keys block without overwriting, even when escaped", async (t) => {
  const { path, project, write } = await fixture(t)
  for (const original of [
    '{"version":1,"features":[{"smart_router":{"enabled":false,"enabled":true}}]}',
    String.raw`{"version":1,"features":[{"future":{"keep":1,"\u006beep":2}}]}`,
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
     { version: 1, features: [{ smart_router: { enabled: 1 } }] }]) {
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
  await writeFile(original, '{"version":1,"features":[]}')
  await symlink(root, folder)
  assert.equal((await project.inspect()).state, "blocked")
  await rm(folder)
  await mkdir(folder)
  for (const makeLink of [symlink, link]) {
    await makeLink(original, path)
    assert.equal((await project.inspect()).state, "blocked")
    await rm(path)
  }
  await write({ version: 1, features: [] })
  await chmod(path, 0o666)
  assert.equal((await project.inspect()).state, "blocked")
  assert.equal(await readFile(original, "utf8"), '{"version":1,"features":[]}')
})

test("stale previews, held locks and failed replacement preserve existing bytes", async (t) => {
  const { folder, path, project, write } = await fixture(t)
  await write({ version: 1, features: [] })
  const stale = await project.preview()
  await write({ version: 1, features: [], future: 2 })
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

const gitIn = (cwd, ...args) => execFileSync("git", ["-C", cwd, "-c", "core.hooksPath=/dev/null", "-c", "user.name=Fixture",
  "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", ...args], {
  env: Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_"))), stdio: "pipe",
})

test("git_workflow preview defaults on with user merge and writes nothing", async (t) => {
  const { folder, path, create, root } = await fixture(t)
  const project = create(root, "git_workflow")
  const preview = await project.preview()
  assert.equal(preview.enabled, true)
  assert.equal(preview.agent_merge, false)
  assert.equal(preview.previous_agent_merge, null)
  assert.equal(preview.change, "create")
  await assert.rejects(stat(folder), { code: "ENOENT" })
  assert.equal((await project.apply({ enabled: true, agent_merge: false, worktree_cleanup: false }, preview.snapshot)).state, "saved")
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")), {
    version: 1, features: [{ git_workflow: { enabled: true, agent_merge: false, worktree_cleanup: false } }],
  })
  const repeat = await project.preview()
  assert.equal(repeat.change, "none")
  assert.equal((await project.apply({ enabled: true, agent_merge: false, worktree_cleanup: false }, repeat.snapshot)).state, "unchanged")
})

test("git_workflow apply keeps the Smart Router entry, unknown entries and order", async (t) => {
  const { path, create, root, write } = await fixture(t)
  await write({ version: 1, future: "PRIVATE-SENTINEL", features: [{ smart_router: { enabled: false, keep: 1 } },
    { other: { x: 1 } }, { git_workflow: { enabled: true, agent_merge: false, note: "keep" } }] })
  const project = create(root, "git_workflow"), preview = await project.preview({ agent_merge: true })
  assert.equal(preview.change, "update")
  assert.equal((await project.apply({ enabled: true, agent_merge: true, worktree_cleanup: false }, preview.snapshot)).state, "saved")
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")), {
    version: 1, future: "PRIVATE-SENTINEL", features: [{ smart_router: { enabled: false, keep: 1 } },
      { other: { x: 1 } }, { git_workflow: { enabled: true, agent_merge: true, worktree_cleanup: false, note: "keep" } }] })
})

test("git_workflow apply requires all three booleans and unchanged compares all three", async (t) => {
  const { create, root, write } = await fixture(t)
  await write({ version: 1, features: [{ git_workflow: { enabled: true, agent_merge: false, worktree_cleanup: false } }] })
  const project = create(root, "git_workflow"), { snapshot } = await project.preview()
  for (const values of [{ enabled: true }, { agent_merge: true }, { worktree_cleanup: true }, true, undefined,
    { enabled: true, agent_merge: false }, { enabled: 1, agent_merge: false, worktree_cleanup: false },
    { enabled: true, agent_merge: false, worktree_cleanup: 1 },
    { enabled: true, agent_merge: false, worktree_cleanup: false, extra: true }]) {
    assert.deepEqual(await project.apply(values, snapshot).then(({ state, reason }) => ({ state, reason })),
      { state: "blocked", reason: "invalid_request" })
  }
  assert.equal((await project.preview({ enabled: true, extra: 1 })).reason, "invalid_request")
  assert.equal((await project.apply({ enabled: true, agent_merge: false, worktree_cleanup: false }, snapshot)).state, "unchanged")
  assert.equal((await project.apply({ enabled: true, agent_merge: false, worktree_cleanup: true }, snapshot)).state, "saved")
})

test("invalid entries block only their own feature", async (t) => {
  const { create, root, write } = await fixture(t)
  for (const entry of [{ enabled: 1 }, { enabled: true, agent_merge: "yes" }, { enabled: true, worktree_cleanup: "yes" }]) {
    await write({ version: 1, features: [{ git_workflow: entry }] })
    assert.equal((await create(root, "git_workflow").inspect()).reason, "project_settings_invalid")
    assert.equal((await create(root).inspect()).state, "not_onboarded")
  }
  await write({ version: 1, features: [{ smart_router: { enabled: "no" } }] })
  assert.equal((await create(root).inspect()).reason, "project_settings_invalid")
  assert.equal((await create(root, "git_workflow").inspect()).state, "not_onboarded")
})

test("default-feature results have no agent_merge key and ignore git_workflow entries", async (t) => {
  const { create, root, write } = await fixture(t)
  await write({ version: 1, features: [{ git_workflow: { enabled: true, agent_merge: true } }] })
  const project = create(root), inspected = await project.inspect(), preview = await project.preview()
  assert.equal(inspected.state, "not_onboarded")
  for (const result of [inspected, preview, await project.apply(true, preview.snapshot)]) {
    assert.ok(!("agent_merge" in result), JSON.stringify(result))
  }
})

test("a worktree copy never grants merge when read at the project path", async (t) => {
  const { root, create, write } = await fixture(t)
  gitIn(root, "init", "-q")
  gitIn(root, "commit", "--allow-empty", "-qm", "fixture")
  await write({ version: 1, features: [{ git_workflow: { enabled: true, agent_merge: false } }] })
  const linked = join(root, "linked")
  gitIn(root, "worktree", "add", "--detach", linked)
  await mkdir(join(linked, ".vibe-workflow"))
  await writeFile(join(linked, ".vibe-workflow", "settings.json"),
    JSON.stringify({ version: 1, features: [{ git_workflow: { enabled: true, agent_merge: true } }] }))
  const main = await create(linked, "git_workflow", { atProject: true }).inspect()
  assert.equal(main.root, root)
  assert.equal(main.agent_merge, false)
  assert.equal((await create(linked, "git_workflow").inspect()).agent_merge, true)
})

test("unsupported feature names throw", async (t) => {
  const { create, root } = await fixture(t)
  assert.throws(() => create(root, "other"), /unsupported feature/)
})

test("atProject is blocked in a linked worktree of a bare repository, and nothing is written", async (t) => {
  const { root, create } = await fixture(t)
  const bare = join(root, "bare.git"), linked = join(root, "linked")
  execFileSync("git", ["init", "-q", "--bare", bare], { stdio: "pipe" })
  execFileSync("git", ["-C", bare, "worktree", "add", "-q", "--orphan", "-b", "main", linked], { stdio: "pipe" })
  const project = create(linked, "git_workflow", { atProject: true })
  const state = await project.inspect()
  assert.deepEqual([state.state, state.reason, state.project], ["blocked", "project_root_unavailable", null])
  assert.equal((await project.preview()).state, "blocked")
  assert.equal((await project.apply({ enabled: true, agent_merge: true, worktree_cleanup: true }, "0".repeat(64))).state, "blocked")
  await assert.rejects(stat(join(linked, ".vibe-workflow")), { code: "ENOENT" })
  assert.equal((await create(linked).inspect()).project, linked)
})

test("atProject is blocked when the worktree listing fails in a linked worktree", async (t) => {
  const { root, create } = await fixture(t)
  const real = execFileSync("sh", ["-c", "command -v git"], { encoding: "utf8" }).trim()
  gitIn(root, "init", "-q")
  gitIn(root, "commit", "--allow-empty", "-qm", "fixture")
  const linked = join(root, "linked")
  gitIn(root, "worktree", "add", "-q", "--detach", linked)
  const bin = join(root, "bin")
  await mkdir(bin)
  await writeFile(join(bin, "git"), `#!/bin/sh\n[ "$1" = worktree ] && exit 129\nexec "${real}" "$@"\n`, { mode: 0o755 })
  const path = process.env.PATH
  process.env.PATH = `${bin}:${path}`
  t.after(() => { process.env.PATH = path })
  const state = await create(linked, "git_workflow", { atProject: true }).inspect()
  assert.deepEqual([state.state, state.reason, state.project], ["blocked", "project_root_unavailable", null])
  assert.equal((await create(linked).inspect()).project, linked)
  assert.equal((await create(root, "git_workflow", { atProject: true }).inspect()).state, "not_onboarded")
})

test("an old pull_requests entry is only an unknown entry: ignored, and kept when git_workflow is saved", async (t) => {
  const { path, create, root, write } = await fixture(t)
  await write({ version: 1, features: [{ pull_requests: { enabled: true, agent_merge: true } }] })
  const project = create(root, "git_workflow"), preview = await project.preview()
  assert.deepEqual([preview.state, preview.previous_enabled, preview.agent_merge, preview.change], ["preview", null, false, "update"])
  assert.equal((await project.apply({ enabled: true, agent_merge: false, worktree_cleanup: false }, preview.snapshot)).state, "saved")
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")).features, [{ pull_requests: { enabled: true, agent_merge: true } },
    { git_workflow: { enabled: true, agent_merge: false, worktree_cleanup: false } }])
})

test("a missing worktree_cleanup key reads null and previews false with an update", async (t) => {
  const { path, create, root, write } = await fixture(t)
  await write({ version: 1, features: [{ git_workflow: { enabled: true, agent_merge: true } }] })
  const project = create(root, "git_workflow")
  assert.equal((await project.inspect()).worktree_cleanup, null)
  const preview = await project.preview()
  assert.deepEqual([preview.previous_worktree_cleanup, preview.worktree_cleanup, preview.change], [null, false, "update"])
  assert.equal((await project.apply({ enabled: true, agent_merge: true, worktree_cleanup: false }, preview.snapshot)).state, "saved")
  assert.equal(JSON.parse(await readFile(path, "utf8")).features[0].git_workflow.worktree_cleanup, false)
  assert.equal((await project.preview()).change, "none")
  assert.equal((await create(root, "git_workflow").inspect()).worktree_cleanup, false)
})

test("no file and no entry read worktree_cleanup as null", async (t) => {
  const { create, root, write } = await fixture(t)
  assert.equal((await create(root, "git_workflow").inspect()).worktree_cleanup, null)
  await write({ version: 1, features: [{ smart_router: { enabled: true } }] })
  assert.equal((await create(root, "git_workflow").inspect()).worktree_cleanup, null)
})

test("worktree_cleanup round-trips true and false and keeps unknown keys", async (t) => {
  const { path, create, root, write } = await fixture(t)
  await write({ version: 1, features: [{ git_workflow: { enabled: true, agent_merge: false, worktree_cleanup: false, note: "keep" } }] })
  const project = create(root, "git_workflow"), preview = await project.preview({ worktree_cleanup: true })
  assert.deepEqual([preview.previous_worktree_cleanup, preview.worktree_cleanup, preview.change], [false, true, "update"])
  assert.equal((await project.apply({ enabled: true, agent_merge: false, worktree_cleanup: true }, preview.snapshot)).worktree_cleanup, true)
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")).features,
    [{ git_workflow: { enabled: true, agent_merge: false, worktree_cleanup: true, note: "keep" } }])
  assert.equal((await project.inspect()).worktree_cleanup, true)
})

test("a worktree copy never turns cleanup on when read at the project path", async (t) => {
  const { root, create, write } = await fixture(t)
  gitIn(root, "init", "-q")
  gitIn(root, "commit", "--allow-empty", "-qm", "fixture")
  await write({ version: 1, features: [{ git_workflow: { enabled: true, agent_merge: false, worktree_cleanup: false } }] })
  const linked = join(root, "linked")
  gitIn(root, "worktree", "add", "--detach", linked)
  await mkdir(join(linked, ".vibe-workflow"))
  await writeFile(join(linked, ".vibe-workflow", "settings.json"),
    JSON.stringify({ version: 1, features: [{ git_workflow: { enabled: true, agent_merge: true, worktree_cleanup: true } }] }))
  const main = await create(linked, "git_workflow", { atProject: true }).inspect()
  assert.deepEqual([main.root, main.agent_merge, main.worktree_cleanup], [root, false, false])
})

test("Smart Router results have no worktree_cleanup key", async (t) => {
  const { create, root, write } = await fixture(t)
  await write({ version: 1, features: [{ git_workflow: { enabled: true, agent_merge: true, worktree_cleanup: true } }] })
  const project = create(root), inspected = await project.inspect(), preview = await project.preview()
  for (const result of [inspected, preview, await project.apply(true, preview.snapshot)]) {
    assert.ok(!("worktree_cleanup" in result) && !("previous_worktree_cleanup" in result), JSON.stringify(result))
  }
})

test("session_titles previews, applies and reports unchanged like Smart Router", async (t) => {
  const { path, create, root } = await fixture(t)
  const project = create(root, "session_titles", { atProject: true })
  const preview = await project.preview()
  assert.deepEqual([preview.enabled, preview.change, "agent_merge" in preview], [true, "create", false])
  assert.equal((await project.apply(true, preview.snapshot)).state, "saved")
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")), { version: 1, features: [{ session_titles: { enabled: true } }] })
  const repeat = await project.preview({ enabled: true })
  assert.equal(repeat.change, "none")
  assert.equal((await project.apply(true, repeat.snapshot)).state, "unchanged")
  const off = await project.preview(false)
  assert.equal((await project.apply(false, off.snapshot)).state, "saved")
  assert.equal((await project.inspect()).state, "off")
})

test("session_titles coexists with smart_router, git_workflow and unknown entries", async (t) => {
  const { path, create, root, write } = await fixture(t)
  await write({ version: 1, features: [{ smart_router: { enabled: false } }, { other: { x: 1 } },
    { git_workflow: { enabled: true, agent_merge: false, worktree_cleanup: true } }] })
  const project = create(root, "session_titles", { atProject: true }), preview = await project.preview()
  assert.equal(preview.state, "preview")
  assert.equal((await project.apply(true, preview.snapshot)).state, "saved")
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")).features, [{ smart_router: { enabled: false } }, { other: { x: 1 } },
    { git_workflow: { enabled: true, agent_merge: false, worktree_cleanup: true } }, { session_titles: { enabled: true } }])
  assert.equal((await create(root, "git_workflow").inspect()).worktree_cleanup, true)
  assert.equal((await create(root).inspect()).enabled, false)
})

test("a worktree copy never turns session_titles on at the project path", async (t) => {
  const { root, create } = await fixture(t)
  gitIn(root, "init", "-q")
  gitIn(root, "commit", "--allow-empty", "-qm", "fixture")
  const linked = join(root, "linked")
  gitIn(root, "worktree", "add", "-q", "--detach", linked)
  await mkdir(join(linked, ".vibe-workflow"))
  await writeFile(join(linked, ".vibe-workflow", "settings.json"),
    JSON.stringify({ version: 1, features: [{ session_titles: { enabled: true } }] }))
  const main = await create(linked, "session_titles", { atProject: true }).inspect()
  assert.deepEqual([main.root, main.state], [root, "not_onboarded"])
})
