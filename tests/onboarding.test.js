import assert from "node:assert/strict"
import { execFileSync } from "node:child_process"
import { mkdir, mkdtemp, readFile, realpath, rm, stat, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"

async function fixture(t, permission = async () => {}, prepare) {
  const root = await realpath(await mkdtemp(join(tmpdir(), "vibe-onboard-")))
  t.after(() => rm(root, { recursive: true }))
  const nested = join(root, "nested")
  await mkdir(nested)
  const { execFileSync } = await import("node:child_process")
  execFileSync("git", ["init", "-q", root])
  const directory = prepare ? await prepare(root) : nested
  const tool = new Map([["subagent", Object.freeze({ native: true })]]), skills = new Map()
  const subagent = tool.get("subagent")
  const assertCalls = []
  const ctx = {
    location: { directory }, options: { state_dir: join(root, "private-router") },
    tool: { transform: async (apply) => apply({ add: (entry) => tool.set(entry.name, entry) }) },
    skill: { transform: async (apply) => apply({ add: (entry) => skills.set(entry.name, entry) }) },
    ...(permission && { permission: { assert: async (request) => { assertCalls.push(request); await permission(request) } } }),
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
  assert.deepEqual([...skills.keys()], ["vibe-onboard-project", "vibe-prepare-my-workspace", "vibe-pull-requests"])
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
  await write({ version: 1, features: [{ smart_router: { enabled: false } }] })
  const original = await readFile(path, "utf8"), preview = await execute({ op: "preview", enabled: true })
  await assert.rejects(execute({ op: "apply", enabled: true, expected_snapshot: preview.snapshot }), denial)
  assert.equal(await readFile(path, "utf8"), original)
  assert.equal(tool.get("subagent"), subagent)
  assert.deepEqual(assertCalls.map(({ action, resources }) => ({ action, resources })),
    [{ action: "edit", resources: ["../.vibe-workflow/settings.json"] }])
})

test("apply checks snapshot and keeps existing opt-out and unknown fields", async (t) => {
  const { execute, write, path, assertCalls } = await fixture(t)
  await write({ version: 1, features: [{ smart_router: { enabled: false } }], future: "PRIVATE-SENTINEL" })
  const current = await execute({ op: "preview" })
  assert.equal(current.enabled, false)
  assert.equal(current.readiness.state, "off")
  assert.ok(!JSON.stringify(current).includes("PRIVATE-SENTINEL"))
  assert.equal(assertCalls.length, 0)
  const preview = await execute({ op: "preview", enabled: true })
  await write({ version: 1, features: [{ smart_router: { enabled: false } }], future: "new" })
  assert.equal((await execute({ op: "apply", enabled: true, expected_snapshot: preview.snapshot })).reason,
    "project_settings_changed")
  const fresh = await execute({ op: "preview", enabled: true })
  const saved = await execute({ op: "apply", enabled: true, expected_snapshot: fresh.snapshot })
  assert.equal(saved.state, "saved")
  assert.equal(saved.readiness.state, "setup_needed")
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")), {
    version: 1, features: [{ smart_router: { enabled: true } }], future: "new",
  })
})

const gitIn = (cwd, ...args) => execFileSync("git", ["-C", cwd, "-c", "core.hooksPath=/dev/null", "-c", "user.name=Fixture",
  "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", ...args], { stdio: "pipe" })
const prefs = (execute, input) => execute({ feature: "pull_requests", ...input })

test("pull_requests preview defaults on with user merge and writes nothing", async (t) => {
  const { execute, path, root } = await fixture(t)
  const preview = await prefs(execute, { op: "preview" })
  assert.deepEqual([preview.client, preview.state, preview.enabled, preview.agent_merge, preview.change],
    ["opencode", "preview", true, false, "create"])
  assert.equal(preview.root, root)
  await assert.rejects(stat(path), { code: "ENOENT" })
})

test("pull_requests apply asks for edit permission and saves only that entry", async (t) => {
  const { execute, write, path, assertCalls } = await fixture(t)
  await write({ version: 1, features: [{ smart_router: { enabled: false } }] })
  const preview = await prefs(execute, { op: "preview", agent_merge: true })
  const saved = await prefs(execute, { op: "apply", enabled: true, agent_merge: true, expected_snapshot: preview.snapshot })
  assert.deepEqual([saved.state, saved.enabled, saved.agent_merge], ["saved", true, true])
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")).features,
    [{ smart_router: { enabled: false } }, { pull_requests: { enabled: true, agent_merge: true } }])
  assert.deepEqual(assertCalls.map(({ action, resources }) => ({ action, resources })),
    [{ action: "edit", resources: ["../.vibe-workflow/settings.json"] }])
})

test("agent_merge with the default feature is invalid and Smart Router results have no agent_merge", async (t) => {
  const { execute } = await fixture(t)
  assert.equal((await execute({ op: "preview", agent_merge: true })).reason, "invalid_request")
  assert.equal((await execute({ feature: "other", op: "preview" })).reason, "invalid_request")
  assert.ok(!("agent_merge" in await execute({ op: "preview" })))
})

test("pull_requests readiness reports guard_unavailable", async (t) => {
  const { execute } = await fixture(t)
  assert.deepEqual((await prefs(execute, { op: "preview" })).readiness, { state: "setup_needed", reason: "guard_unavailable" })
  assert.deepEqual((await prefs(execute, { op: "preview", enabled: false })).readiness, { state: "off", reason: null })
})

test("skip saves enabled false and agent_merge false with readiness off", async (t) => {
  const { execute, path } = await fixture(t)
  const preview = await prefs(execute, { op: "preview", enabled: false, agent_merge: false })
  const saved = await prefs(execute, { op: "apply", enabled: false, agent_merge: false, expected_snapshot: preview.snapshot })
  assert.deepEqual([saved.state, saved.readiness], ["saved", { state: "off", reason: null }])
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")).features,
    [{ pull_requests: { enabled: false, agent_merge: false } }])
})

test("pull_requests apply without agent_merge is blocked", async (t) => {
  const { execute, path } = await fixture(t)
  const preview = await prefs(execute, { op: "preview" })
  assert.equal((await prefs(execute, { op: "apply", enabled: true, expected_snapshot: preview.snapshot })).reason, "invalid_request")
  await assert.rejects(stat(path), { code: "ENOENT" })
})

test("both choices apply in sequence on a primary checkout", async (t) => {
  const { execute, path } = await fixture(t)
  const router = await execute({ op: "preview", enabled: true })
  const pullRequests = await prefs(execute, { op: "preview", enabled: true, agent_merge: false })
  assert.equal(router.snapshot, pullRequests.snapshot)
  assert.equal((await execute({ op: "apply", enabled: true, expected_snapshot: router.snapshot })).state, "saved")
  assert.equal((await prefs(execute, { op: "apply", enabled: true, agent_merge: false,
    expected_snapshot: pullRequests.snapshot })).reason, "project_settings_changed")
  const again = await prefs(execute, { op: "preview", enabled: true, agent_merge: false })
  assert.equal(again.change, "update")
  assert.equal((await prefs(execute, { op: "apply", enabled: true, agent_merge: false, expected_snapshot: again.snapshot })).state, "saved")
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")).features,
    [{ smart_router: { enabled: true } }, { pull_requests: { enabled: true, agent_merge: false } }])
})

test("a missing ctx.permission blocks pull_requests apply", async (t) => {
  const { execute, path } = await fixture(t, null)
  const preview = await prefs(execute, { op: "preview" })
  const blocked = await prefs(execute, { op: "apply", enabled: true, agent_merge: false, expected_snapshot: preview.snapshot })
  assert.equal(blocked.reason, "project_permission_unavailable")
  await assert.rejects(stat(path), { code: "ENOENT" })
})

test("pull_requests apply from a linked worktree writes the main checkout only", async (t) => {
  const denial = new Error("fixture native edit denied")
  let linked
  const prepare = async (root) => {
    gitIn(root, "commit", "--allow-empty", "-qm", "fixture")
    linked = join(root, "linked")
    gitIn(root, "worktree", "add", "--detach", linked)
    return linked
  }
  const { execute, path, assertCalls, root } = await fixture(t, async () => {}, prepare)
  const preview = await prefs(execute, { op: "preview" })
  assert.deepEqual([preview.root, preview.project], [root, root])
  const saved = await prefs(execute, { op: "apply", enabled: true, agent_merge: false, expected_snapshot: preview.snapshot })
  assert.equal(saved.state, "saved")
  assert.equal(JSON.parse(await readFile(path, "utf8")).features[0].pull_requests.enabled, true)
  await assert.rejects(stat(join(linked, ".vibe-workflow")), { code: "ENOENT" })
  assert.deepEqual(assertCalls.map(({ resources }) => resources), [["../.vibe-workflow/settings.json"]])
  const denied = await fixture(t, async () => { throw denial }, prepare)
  const again = await prefs(denied.execute, { op: "preview" })
  await assert.rejects(prefs(denied.execute, { op: "apply", enabled: true, agent_merge: false, expected_snapshot: again.snapshot }), denial)
  await assert.rejects(stat(denied.path), { code: "ENOENT" })
})

test("procedure asks who merges and keeps the Smart Router question", async (t) => {
  const { skills } = await fixture(t)
  const text = await readFile(new URL("./references/procedure.md", `file://${skills.get("vibe-onboard-project").path}`), "utf8")
  for (const phrase of ["Who merges", "Use Smart Router for this project?", "feature", "Reminders registered"]) {
    assert.ok(text.includes(phrase), phrase)
  }
})
