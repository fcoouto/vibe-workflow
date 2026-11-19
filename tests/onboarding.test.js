import assert from "node:assert/strict"
import { mkdir, mkdtemp, readFile, realpath, rm, stat, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"

async function fixture(t, permission = async () => {}) {
  const root = await realpath(await mkdtemp(join(tmpdir(), "vibe-onboard-")))
  t.after(() => rm(root, { recursive: true }))
  const nested = join(root, "nested")
  await mkdir(nested)
  const { execFileSync } = await import("node:child_process")
  execFileSync("git", ["init", "-q", root])
  const tool = new Map([["subagent", Object.freeze({ native: true })]]), skills = new Map()
  const subagent = tool.get("subagent")
  const assertCalls = []
  const ctx = {
    location: { directory: nested }, options: { state_dir: join(root, "private-router") },
    tool: { transform: async (apply) => apply({ add: (entry) => tool.set(entry.name, entry) }) },
    skill: { transform: async (apply) => apply({ add: (entry) => skills.set(entry.name, entry) }) },
    permission: { assert: async (request) => { assertCalls.push(request); await permission(request) } },
    session: { hook: async () => {} },
  }
  const cleanup = await (await import("../index.js")).default.setup(ctx)
  t.after(() => cleanup?.())
  assert.ok(tool.has("vibe_workflow_onboard"), "onboarding tool is missing")
  const invocation = { sessionID: "ses_fixture", messageID: "msg_fixture", id: "call_fixture", agent: "build",
    signal: new AbortController().signal }
  const path = join(root, ".vibe-workflow", "settings.json")
  return { root, path, tool, skills, ctx, subagent, assertCalls, invocation,
    async execute(input, context = invocation) {
      return JSON.parse((await tool.get("vibe_workflow_onboard").execute({ v: 1, ...input }, context)).content)
    },
    async write(value) {
      await mkdir(join(root, ".vibe-workflow"), { recursive: true })
      await writeFile(path, JSON.stringify(value))
    } }
}

test("preview registers bundled onboarding, defaults on and writes nothing", async (t) => {
  const { execute, root, path, skills, tool, subagent } = await fixture(t)
  const preview = await execute({ op: "preview" })
  assert.equal(preview.root, root)
  assert.equal(preview.client, "opencode")
  assert.equal(preview.enabled, true)
  assert.equal(preview.readiness.state, "setup_needed")
  assert.deepEqual([...skills.keys()], ["vibe-onboard-project"])
  const skill = skills.get("vibe-onboard-project")
  assert.equal(skill.id, "vibe-onboard-project")
  assert.match(tool.get("vibe_workflow_onboard").description, /\bvibe-onboard-project\b/)
  assert.ok((await readFile(skill.path, "utf8")).includes("references/procedure.md"))
  assert.equal(tool.get("subagent"), subagent)
  await assert.rejects(stat(path), { code: "ENOENT" })
  const controller = new AbortController()
  controller.abort()
  const cancelled = await execute({ op: "apply", enabled: true, expected_snapshot: preview.snapshot },
    { sessionID: "ses_fixture", messageID: "msg_fixture", id: "apply", signal: controller.signal })
  assert.equal(cancelled.state, "blocked")
  await assert.rejects(stat(join(root, ".vibe-workflow")), { code: "ENOENT" })
})

test("native edit denial never applies", async (t) => {
  const denial = new Error("fixture native edit denied")
  const { execute, write, path, tool, subagent, assertCalls } = await fixture(t, async () => { throw denial })
  await write({ version: 1, smart_router: { enabled: false } })
  const original = await readFile(path, "utf8"), preview = await execute({ op: "preview", enabled: true })
  await assert.rejects(execute({ op: "apply", enabled: true, expected_snapshot: preview.snapshot }), denial)
  assert.equal(await readFile(path, "utf8"), original)
  assert.equal(tool.get("subagent"), subagent)
  assert.deepEqual(assertCalls.map(({ action, resources }) => ({ action, resources })),
    [{ action: "edit", resources: ["../.vibe-workflow/settings.json"] }])
})

test("apply checks snapshot and keeps existing opt-out and unknown fields", async (t) => {
  const { execute, write, path, assertCalls } = await fixture(t)
  await write({ version: 1, smart_router: { enabled: false }, future: "PRIVATE-SENTINEL" })
  const current = await execute({ op: "preview" })
  assert.equal(current.enabled, false)
  assert.equal(current.readiness.state, "off")
  assert.ok(!JSON.stringify(current).includes("PRIVATE-SENTINEL"))
  assert.equal(assertCalls.length, 0)
  const preview = await execute({ op: "preview", enabled: true })
  await write({ version: 1, smart_router: { enabled: false }, future: "new" })
  assert.equal((await execute({ op: "apply", enabled: true, expected_snapshot: preview.snapshot })).reason,
    "project_settings_changed")
  const fresh = await execute({ op: "preview", enabled: true })
  const saved = await execute({ op: "apply", enabled: true, expected_snapshot: fresh.snapshot })
  assert.equal(saved.state, "saved")
  assert.equal(saved.readiness.state, "setup_needed")
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")), {
    version: 1, smart_router: { enabled: true }, future: "new",
  })
})
