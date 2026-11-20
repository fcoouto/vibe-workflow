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
const prefs = (execute, input) => execute({ feature: "git_workflow", ...input })

test("git_workflow preview defaults on with user merge and writes nothing", async (t) => {
  const { execute, path, root } = await fixture(t)
  const preview = await prefs(execute, { op: "preview" })
  assert.deepEqual([preview.client, preview.state, preview.enabled, preview.agent_merge, preview.change],
    ["opencode", "preview", true, false, "create"])
  assert.equal(preview.root, root)
  await assert.rejects(stat(path), { code: "ENOENT" })
})

test("git_workflow apply asks for edit permission and saves only that entry", async (t) => {
  const { execute, write, path, assertCalls } = await fixture(t)
  await write({ version: 1, features: [{ smart_router: { enabled: false } }] })
  const preview = await prefs(execute, { op: "preview", agent_merge: true })
  const saved = await prefs(execute, { op: "apply", enabled: true, agent_merge: true, worktree_cleanup: false, expected_snapshot: preview.snapshot })
  assert.deepEqual([saved.state, saved.enabled, saved.agent_merge], ["saved", true, true])
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")).features,
    [{ smart_router: { enabled: false } }, { git_workflow: { enabled: true, agent_merge: true, worktree_cleanup: false } }])
  assert.deepEqual(assertCalls.map(({ action, resources }) => ({ action, resources })),
    [{ action: "edit", resources: ["../.vibe-workflow/settings.json"] }])
})

test("agent_merge with the default feature is invalid and Smart Router results have no agent_merge", async (t) => {
  const { execute } = await fixture(t)
  assert.equal((await execute({ op: "preview", agent_merge: true })).reason, "invalid_request")
  assert.equal((await execute({ feature: "other", op: "preview" })).reason, "invalid_request")
  assert.ok(!("agent_merge" in await execute({ op: "preview" })))
})

test("git_workflow readiness reports guard_unavailable", async (t) => {
  const { execute } = await fixture(t)
  assert.deepEqual((await prefs(execute, { op: "preview" })).readiness, { state: "setup_needed", reason: "guard_unavailable" })
  assert.deepEqual((await prefs(execute, { op: "preview", enabled: false })).readiness, { state: "off", reason: null })
})

test("skip saves enabled false and agent_merge false with readiness off", async (t) => {
  const { execute, path } = await fixture(t)
  const preview = await prefs(execute, { op: "preview", enabled: false, agent_merge: false, worktree_cleanup: false })
  const saved = await prefs(execute, { op: "apply", enabled: false, agent_merge: false, worktree_cleanup: false, expected_snapshot: preview.snapshot })
  assert.deepEqual([saved.state, saved.readiness], ["saved", { state: "off", reason: null }])
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")).features,
    [{ git_workflow: { enabled: false, agent_merge: false, worktree_cleanup: false } }])
})

test("git_workflow apply without agent_merge or worktree_cleanup is blocked", async (t) => {
  const { execute, path } = await fixture(t)
  const preview = await prefs(execute, { op: "preview" })
  assert.equal((await prefs(execute, { op: "apply", enabled: true, expected_snapshot: preview.snapshot })).reason, "invalid_request")
  assert.equal((await prefs(execute, { op: "apply", enabled: true, agent_merge: false, expected_snapshot: preview.snapshot })).reason,
    "invalid_request")
  await assert.rejects(stat(path), { code: "ENOENT" })
})

test("worktree_cleanup with the default feature is invalid", async (t) => {
  const { execute } = await fixture(t)
  assert.equal((await execute({ op: "preview", worktree_cleanup: true })).reason, "invalid_request")
  assert.equal((await execute({ op: "preview", worktree_cleanup: "yes", feature: "git_workflow" })).reason, "invalid_request")
})

test("preview reports previous_worktree_cleanup null for a file saved before this feature", async (t) => {
  const { execute, write } = await fixture(t)
  await write({ version: 1, features: [{ git_workflow: { enabled: true, agent_merge: true } }] })
  const preview = await prefs(execute, { op: "preview" })
  assert.deepEqual([preview.previous_worktree_cleanup, preview.previous_agent_merge, preview.worktree_cleanup, preview.change],
    [null, true, false, "update"])
})

test("an applied worktree_cleanup true is saved and read back", async (t) => {
  const { execute, path } = await fixture(t)
  const preview = await prefs(execute, { op: "preview", worktree_cleanup: true })
  assert.equal(preview.worktree_cleanup, true)
  const saved = await prefs(execute, { op: "apply", enabled: true, agent_merge: false, worktree_cleanup: true,
    expected_snapshot: preview.snapshot })
  assert.deepEqual([saved.state, saved.worktree_cleanup], ["saved", true])
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")).features,
    [{ git_workflow: { enabled: true, agent_merge: false, worktree_cleanup: true } }])
  const again = await prefs(execute, { op: "preview" })
  assert.deepEqual([again.previous_worktree_cleanup, again.change], [true, "none"])
})

test("both choices apply in sequence on a primary checkout", async (t) => {
  const { execute, path } = await fixture(t)
  const router = await execute({ op: "preview", enabled: true })
  const pullRequests = await prefs(execute, { op: "preview", enabled: true, agent_merge: false, worktree_cleanup: false })
  assert.equal(router.snapshot, pullRequests.snapshot)
  assert.equal((await execute({ op: "apply", enabled: true, expected_snapshot: router.snapshot })).state, "saved")
  assert.equal((await prefs(execute, { op: "apply", enabled: true, agent_merge: false, worktree_cleanup: false,
    expected_snapshot: pullRequests.snapshot })).reason, "project_settings_changed")
  const again = await prefs(execute, { op: "preview", enabled: true, agent_merge: false, worktree_cleanup: false })
  assert.equal(again.change, "update")
  assert.equal((await prefs(execute, { op: "apply", enabled: true, agent_merge: false, worktree_cleanup: false, expected_snapshot: again.snapshot })).state, "saved")
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")).features,
    [{ smart_router: { enabled: true } }, { git_workflow: { enabled: true, agent_merge: false, worktree_cleanup: false } }])
})

test("a missing ctx.permission blocks git_workflow apply", async (t) => {
  const { execute, path } = await fixture(t, null)
  const preview = await prefs(execute, { op: "preview" })
  const blocked = await prefs(execute, { op: "apply", enabled: true, agent_merge: false, worktree_cleanup: false, expected_snapshot: preview.snapshot })
  assert.equal(blocked.reason, "project_permission_unavailable")
  await assert.rejects(stat(path), { code: "ENOENT" })
})

test("git_workflow apply from a linked worktree writes the main checkout only", async (t) => {
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
  const saved = await prefs(execute, { op: "apply", enabled: true, agent_merge: false, worktree_cleanup: false, expected_snapshot: preview.snapshot })
  assert.equal(saved.state, "saved")
  assert.equal(JSON.parse(await readFile(path, "utf8")).features[0].git_workflow.enabled, true)
  await assert.rejects(stat(join(linked, ".vibe-workflow")), { code: "ENOENT" })
  assert.deepEqual(assertCalls.map(({ resources }) => resources), [["../.vibe-workflow/settings.json"]])
  const denied = await fixture(t, async () => { throw denial }, prepare)
  const again = await prefs(denied.execute, { op: "preview" })
  await assert.rejects(prefs(denied.execute, { op: "apply", enabled: true, agent_merge: false, worktree_cleanup: false, expected_snapshot: again.snapshot }), denial)
  await assert.rejects(stat(denied.path), { code: "ENOENT" })
})

test("every onboarding question has the same control-shaped form", async (t) => {
  const { skills } = await fixture(t)
  const text = await readFile(new URL("./references/procedure.md", `file://${skills.get("vibe-onboard-project").path}`), "utf8")
  const questions = text.split(/^### \d+\. /m).slice(1).map((block) => block.split(/^## /m)[0])
  assert.equal(questions.length, 5)
  for (const block of questions) {
    const field = (name) => block.match(new RegExp(`^- ${name}: (.+)$`, "m"))?.[1]
    assert.ok(field("Header") && field("Header").length <= 30, field("Header"))
    assert.match(field("Question"), /\?$/)
    assert.ok(field("Context")?.startsWith("Project: <project path>."), field("Header"))
    const choices = [...block.matchAll(/^ {2,}- (.+?) — (.+)$/gm)]
    assert.ok(choices.length >= 2 && choices.length <= 3, field("Header"))
    for (const [, label, description] of choices) {
      assert.ok(label.split(/\s+/).length <= 5, label)
      assert.ok(description.length > 0 && !/`/.test(`${label}${description}`), label)
    }
    assert.match(choices[0][1], /\((Recommended)\)$/, field("Header"))
  }
  assert.equal(questions[0].match(/^- Question: (.+)$/m)[1], "Use Smart Router for this project?")
  assert.equal(questions[1].match(/^- Question: (.+)$/m)[1], "Who merges ready pull requests in this project?")
  assert.equal(questions[2].match(/^- Question: (.+)$/m)[1], "Clean up after a pull request is merged in this project?")
  assert.equal(questions[3].match(/^- Question: (.+)$/m)[1], "Delete merged branches on GitHub automatically?")
  assert.equal(questions[4].match(/^- Question: (.+)$/m)[1], "Show task progress in the session title?")
  assert.match(questions[1], /Skip git workflow guidance — Saves all of this as off, including cleanup\./)
  assert.doesNotMatch(text, /Save both\?|Save onboarding choices|one confirmation/)
  assert.doesNotMatch(text, /`\[(READY|BLOCKED)\]`/)
})

test("the procedure fixes the flow: up-front count, one report format, recovery and how to change later", async (t) => {
  const { skills } = await fixture(t)
  const text = await readFile(new URL("./references/procedure.md", `file://${skills.get("vibe-onboard-project").path}`), "utf8")
  for (const phrase of ["Up to five short questions.", "Session titles: not saved.", "Session titles: Off — titles are left alone.",
    "Session titles: On — updated at the end of each turn.",
    "Session titles: On · Setup needed — this OpenCode does not deliver session events.", "Saved in the main checkout", "project_root_unavailable",
    "Smart Router: Off —", "Smart Router: On · Configuration checked —", "Git workflow: Off —", "Git workflow: On · I merge —",
    "Git workflow: On · Agents merge —", "Git workflow: not saved.", "reminders registered", "Run onboarding again to change them",
    "Nothing to change", "Saving:", "native file-edit permission",
    "Git workflow: On · I merge, cleanup on —", "Git workflow: On · Agents merge, cleanup on —"]) {
    assert.ok(text.includes(phrase), phrase)
  }
})

test("the old feature name is not accepted by the tool, only by stored settings", async (t) => {
  const { execute } = await fixture(t)
  assert.equal((await execute({ feature: "pull_requests", op: "preview" })).reason, "invalid_request")
})

test("the procedure pins the cleanup questions, the GitHub conditions, the apply order and cancel", async (t) => {
  const { skills } = await fixture(t)
  const text = await readFile(new URL("./references/procedure.md", `file://${skills.get("vibe-onboard-project").path}`), "utf8")
  for (const phrase of ["Skip git workflow guidance", "skips questions 3 and 4",
    "gh repo view --json nameWithOwner,deleteBranchOnMerge,viewerPermission", "`deleteBranchOnMerge` is `false`",
    "`viewerPermission` is `ADMIN`", "gh repo edit", "--delete-branch-on-merge", "the same `gh repo view` call",
    "GitHub auto-delete: already on.", "GitHub auto-delete: not changed (admin access is needed).",
    "GitHub auto-delete: not available (gh is missing or not logged in).",
    "GitHub auto-delete: not available (this remote is not on GitHub).", "GitHub auto-delete: not available (could not be read).", "ignored files", "GitHub auto-delete: turned on.",
    "GitHub: turn on auto-delete of merged branches", "previous_worktree_cleanup", '"worktree_cleanup"',
    "A yes to question 4 counts as a change", "the Saving line comes before anything is applied",
    "a cancel at any question or a denied native file-edit permission means no GitHub change",
    "runs after the settings apply", "independent of a technical failure"]) {
    assert.ok(text.includes(phrase), phrase)
  }
  assert.doesNotMatch(text, /Two short questions\.|Up to four/)
})

const titles = (execute, input) => execute({ feature: "session_titles", ...input })

test("session_titles preview defaults on, writes nothing and takes readiness from titlesGuard", async (t) => {
  const { execute, path } = await fixture(t)
  const preview = await titles(execute, { op: "preview" })
  assert.deepEqual([preview.state, preview.enabled, preview.change], ["preview", true, "create"])
  assert.ok(!("agent_merge" in preview))
  assert.deepEqual(preview.readiness, { state: "setup_needed", reason: "events_unavailable" })
  assert.deepEqual((await titles(execute, { op: "preview", enabled: false })).readiness, { state: "off", reason: null })
  await assert.rejects(stat(path), { code: "ENOENT" })
})

test("session_titles apply saves only its entry after edit permission", async (t) => {
  const { execute, write, path, assertCalls } = await fixture(t)
  await write({ version: 1, features: [{ smart_router: { enabled: false } }] })
  const preview = await titles(execute, { op: "preview" })
  const saved = await titles(execute, { op: "apply", enabled: true, expected_snapshot: preview.snapshot })
  assert.deepEqual([saved.state, saved.enabled], ["saved", true])
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")).features,
    [{ smart_router: { enabled: false } }, { session_titles: { enabled: true } }])
  assert.deepEqual(assertCalls.map(({ action, resources }) => ({ action, resources })),
    [{ action: "edit", resources: ["../.vibe-workflow/settings.json"] }])
})

test("session_titles rejects git workflow booleans", async (t) => {
  const { execute } = await fixture(t)
  assert.equal((await titles(execute, { op: "preview", agent_merge: true })).reason, "invalid_request")
  assert.equal((await titles(execute, { op: "preview", worktree_cleanup: true })).reason, "invalid_request")
})
