import assert from "node:assert/strict"
import { execFileSync } from "node:child_process"
import { mkdtempSync } from "node:fs"
import { copyFile, mkdir, mkdtemp, readFile, realpath, rm, stat, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"
import { pathToFileURL } from "node:url"
import { createProjectSettings } from "../../src/project.js"

// Spawned selectors inherit this, so a real CLI can never reach the network.
process.env.VCW_TEST_HOME = mkdtempSync(join(tmpdir(), "vcw-test-home-"))

async function fixture(t, permission = async () => {}, prepare, configure) {
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
  configure?.(ctx)
  const cleanup = await (await import("../../index.js")).default.setup(ctx)
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
  assert.deepEqual([...skills.keys()], ["vibe-onboard-project", "vibe-prepare-my-workspace", "vibe-pull-requests", "vibe-council", "vibe-project-layout", "vibe-security-review"])
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

// A session in a subfolder of a linked worktree; main's own file is `path`, the worktree's is `own`.
const inLinked = async (root) => {
  gitIn(root, "commit", "--allow-empty", "-qm", "fixture")
  const linked = join(root, "linked"), nested = join(linked, "nested")
  gitIn(root, "worktree", "add", "--detach", linked)
  await mkdir(nested)
  return nested
}

test("from a linked worktree all three features save only in the worktree", async (t) => {
  const { execute, path, root, assertCalls } = await fixture(t, async () => {}, inLinked)
  const own = join(root, "linked", ".vibe-workflow", "settings.json")
  for (const input of [{ feature: "smart_router", enabled: true },
    { feature: "git_workflow", enabled: true, agent_merge: false, worktree_cleanup: true },
    { feature: "session_titles", enabled: true }]) {
    const preview = await execute({ op: "preview", ...input })
    assert.equal(preview.root, join(root, "linked"))
    assert.equal((await execute({ op: "apply", ...input, expected_snapshot: preview.snapshot })).state, "saved")
  }
  await assert.rejects(stat(path), { code: "ENOENT" })
  assert.deepEqual(JSON.parse(await readFile(own, "utf8")).features, [{ smart_router: { enabled: true } },
    { git_workflow: { enabled: true, agent_merge: false, worktree_cleanup: true } }, { session_titles: { enabled: true } }])
  assert.deepEqual(assertCalls.map(({ resources }) => resources), Array(3).fill(["../.vibe-workflow/settings.json"]))
})

test("keeping every choice in the split case saves once and enables checkout-local titles", async (t) => {
  const { execute, write, path, root, assertCalls } = await fixture(t, async () => {}, inLinked)
  const git = { git_workflow: { enabled: true, agent_merge: true, worktree_cleanup: true } }
  await write({ version: 1, features: [git, { session_titles: { enabled: true } }] })
  const own = join(root, "linked", ".vibe-workflow", "settings.json"), before = await readFile(path, "utf8")
  await mkdir(join(root, "linked", ".vibe-workflow"))
  await writeFile(own, JSON.stringify({ version: 1, features: [{ smart_router: { enabled: true } }] }))
  const current = await prefs(execute, { op: "preview" })
  assert.deepEqual([current.previous_enabled, current.previous_agent_merge, current.previous_worktree_cleanup], [true, true, true])
  const inputs = [{ feature: "smart_router", enabled: true }, { feature: "git_workflow", ...git.git_workflow },
    { feature: "session_titles", enabled: true }]
  const changes = []
  for (const input of inputs) {
    const preview = await execute({ op: "preview", ...input })
    changes.push(preview.change)
    assert.equal((await execute({ op: "apply", ...input, expected_snapshot: preview.snapshot })).state,
      input.feature === "smart_router" ? "saved" : "unchanged")
  }
  assert.deepEqual(changes, ["update", "none", "none"])
  assert.equal(assertCalls.length, 1)
  assert.deepEqual(JSON.parse(await readFile(own, "utf8")).features,
    [git, { session_titles: { enabled: true } }, { smart_router: { enabled: true } }])
  assert.equal((await createProjectSettings(join(root, "linked"), "session_titles").inspect()).state, "on")
  for (const input of inputs) assert.equal((await execute({ op: "preview", ...input })).change, "none")
  assert.equal(await readFile(path, "utf8"), before)
})

test("operational git workflow inspection reads main, never onboarding's worktree preferences", async (t) => {
  const { execute, write, root, assertCalls } = await fixture(t, async () => {}, inLinked)
  const folder = join(root, "linked", ".vibe-workflow")
  await mkdir(folder)
  for (const main of [false, true]) {
    await write({ version: 1, features: [{ git_workflow: { enabled: true, agent_merge: main, worktree_cleanup: main } }] })
    await writeFile(join(folder, "settings.json"), JSON.stringify({
      version: 1, features: [{ git_workflow: { enabled: true, agent_merge: !main, worktree_cleanup: !main } }],
    }))
    const preview = await prefs(execute, { op: "preview" })
    assert.deepEqual([preview.previous_agent_merge, preview.previous_worktree_cleanup], [!main, !main])
    const actual = await prefs(execute, { op: "inspect" })
    assert.deepEqual([actual.state, actual.agent_merge, actual.worktree_cleanup], ["on", main, main])
  }
  assert.equal((await execute({ op: "inspect" })).reason, "invalid_request")
  assert.equal((await prefs(execute, { op: "inspect", agent_merge: true })).reason, "invalid_request")
  assert.equal(assertCalls.length, 0)
})

test("git workflow can save locally but reports unavailable when main cannot be resolved", async (t) => {
  const { execute, root } = await fixture(t, async () => {}, async (root) => {
    const bare = join(root, "bare.git"), linked = join(root, "linked")
    gitIn(root, "init", "-q", "--bare", bare)
    gitIn(bare, "worktree", "add", "-q", "--orphan", "-b", "main", linked)
    const folder = join(linked, ".vibe-workflow")
    await mkdir(folder)
    await writeFile(join(folder, "settings.json"), JSON.stringify({
      version: 1, features: [{ git_workflow: { enabled: true, agent_merge: true, worktree_cleanup: true } }],
    }))
    return linked
  })
  const actual = await prefs(execute, { op: "inspect" })
  assert.deepEqual([actual.state, actual.reason, actual.agent_merge, actual.worktree_cleanup],
    ["blocked", "project_root_unavailable", undefined, undefined])
  const choice = { enabled: true, agent_merge: true, worktree_cleanup: false }
  const preview = await prefs(execute, { op: "preview", ...choice })
  const unavailable = { state: "setup_needed", reason: "project_root_unavailable" }
  assert.deepEqual(preview.readiness, unavailable)
  const applied = await prefs(execute, { op: "apply", ...choice, expected_snapshot: preview.snapshot })
  assert.equal(applied.state, "saved")
  assert.deepEqual(applied.readiness, unavailable)
  const file = JSON.parse(await readFile(join(root, "linked", ".vibe-workflow", "settings.json"), "utf8"))
  assert.deepEqual(file.features, [{ git_workflow: choice }])
  assert.equal((await prefs(execute, { op: "inspect" })).state, "blocked")
})

test("a denied native edit from a linked worktree writes nothing", async (t) => {
  const denial = new Error("fixture native edit denied")
  const { execute, root } = await fixture(t, async () => { throw denial }, inLinked)
  const preview = await prefs(execute, { op: "preview" })
  await assert.rejects(prefs(execute, { op: "apply", enabled: true, agent_merge: false, worktree_cleanup: false, expected_snapshot: preview.snapshot }), denial)
  await assert.rejects(stat(join(root, "linked", ".vibe-workflow")), { code: "ENOENT" })
  await assert.rejects(stat(join(root, ".vibe-workflow")), { code: "ENOENT" })
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
    assert.ok(field("Context"), field("Header"))
    assert.doesNotMatch(field("Context"), /<project path>|^Project:/)
    const groups = block.split(/^- Choices/m).slice(1).map((group) => [...group.matchAll(/^ {2,}- (.+?) — (.+)$/gm)])
    for (const choices of groups) {
      assert.ok(choices.length >= 2 && choices.length <= 3, field("Header"))
      for (const [, label, description] of choices) {
        assert.ok(label.split(/\s+/).length <= 5, label)
        assert.ok(description.length > 0 && !/`|\bgh /.test(`${label}${description}`), label)
      }
    }
    assert.match(groups[0][0][1], /\((Recommended)\)$/, field("Header"))
    for (const saved of groups.slice(1)) assert.match(saved[0][1], /\(Current, Recommended\)$/, field("Header"))
  }
  assert.equal(questions.filter((block) => /^- Choices when it is saved on:/m.test(block)).length, 3)
  assert.equal(questions[3].match(/^- Header: (.+)$/m)[1], "GitHub branch cleanup")
  assert.match(questions[1], /Turn off git guidance — No PR reminders, and no cleanup\./)
  for (const [index, phrase] of [[1, "Reminders are not available in this OpenCode, so this is guidance only."],
    [1, "current for the latest commit"], [1, "[BLOCKED]"],
    [2, "never removes unmerged work, uncommitted work or ignored files"],
    [4, "This OpenCode does not send session events, so titles will not update yet."],
    [3, "not only agents'"], [2, "Keep cleaning up (Current, Recommended)"], [4, "Keep showing it (Current, Recommended)"]]) {
    assert.ok(questions[index].includes(phrase), phrase)
  }
  const flat = text.replace(/\s+/g, " ")
  for (const phrase of ["`previous_agent_merge` `true` is Agents merge their own and `false` is I merge them",
    "In OpenCode, the control's question text is the Context followed by the Question",
    "For `false`, that gives Keep them (Current) first and Clean up (Recommended) second.",
    "For `false`, that gives Leave titles alone (Current) first and Show progress (Recommended) second.",
    "Use each Header, Question and choice label exactly as written, apart from the markers and the saved choice's place.",
    "If blocked, explain the fixed reason and stop. Otherwise, before question 1, write the opening line",
    "**Project: <project>. Client: <client>. Up to five short questions.**",
    "never import or run the plugin's files outside the tool, and never write the settings file yourself",
    "\"feature\":\"git_workflow\",\"enabled\":true,\"agent_merge\":false,\"worktree_cleanup\":false",
    "\"feature\":\"session_titles\",\"enabled\":true"]) {
    assert.ok(flat.includes(phrase), phrase)
  }
  assert.equal(flat.split("the control's question text").length, 2)
  assert.doesNotMatch(text, /Question 1 (leaves|keeps)|Skip git workflow guidance|\*\*Skip\*\*|Remote branch cleanup/)
  const [smartRouter] = questions
  for (const phrase of ["Turn it on (Recommended)", "Leave it off", "Keep it on (Current, Recommended)", "Turn it off",
    "It is on, but not ready yet.", "readiness.state is setup_needed", "readiness.reason is selector_unavailable",
    "Check that Python and the plugin package are available.", "Suggestions start once it is ready."]) {
    assert.ok(smartRouter.includes(phrase), phrase)
  }
  assert.doesNotMatch(smartRouter, /Suggestions continue|not set up/)
  assert.ok(text.includes("Leave it off (Current)"))
  const savedOn = smartRouter.split(/^- Choices/m)[2]
  assert.match(savedOn.match(/^ {2,}- (.+?) — /m)[1], /\(Current, Recommended\)$/)
  assert.doesNotMatch(smartRouter, /Use Smart Router \(Recommended\)|Keep existing model selection/)
  assert.equal(questions[0].match(/^- Question: (.+)$/m)[1], "Use Smart Router for this project?")
  assert.equal(questions[1].match(/^- Question: (.+)$/m)[1], "Who merges ready pull requests in this project?")
  assert.equal(questions[2].match(/^- Question: (.+)$/m)[1], "Clean up after a pull request is merged in this project?")
  assert.equal(questions[3].match(/^- Question: (.+)$/m)[1], "Delete merged branches on GitHub automatically?")
  assert.equal(questions[4].match(/^- Question: (.+)$/m)[1], "Show task progress in the session title?")
  assert.doesNotMatch(text, /Save both\?|Save onboarding choices|one confirmation/)
  assert.doesNotMatch(text, /`\[(READY|BLOCKED)\]`/)
})

test("the procedure fixes the flow: up-front count, one report format, recovery and how to change later", async (t) => {
  const { skills } = await fixture(t)
  const text = await readFile(new URL("./references/procedure.md", `file://${skills.get("vibe-onboard-project").path}`), "utf8")
  for (const phrase of ["Up to five short questions.", "Session titles: not saved.", "Unless question 5 was skipped, apply session titles last",
    "Session titles: Off — titles are left alone.",
    "Session titles: On — updated at the end of each turn.",
    "Session titles: On · Setup needed — this OpenCode does not deliver session events.",
    "Smart Router: Off —", "Smart Router: On · Scores loaded —", "Git workflow: Off —", "Git workflow: On · I merge —",
    "Git workflow: On · Agents merge —", "Git workflow: not saved.", "reminders registered", "Run onboarding again to change them",
    "Nothing to change", "Saving:", "native file-edit permission",
    "Git workflow: On · I merge, cleanup on —", "Git workflow: On · Agents merge, cleanup on —"]) {
    assert.ok(text.includes(phrase), phrase)
  }
  const flat = text.replace(/\s+/g, " ")
  for (const phrase of ["Before step 1, check where the session runs", "`git rev-parse --path-format=absolute --absolute-git-dir --git-common-dir`",
    "`worktree_create` with the name `onboard-vibe-workflow`", "`session.move`", "never save in the main checkout instead",
    "When `root` differs from `project` and Git workflow readiness does not have reason `project_root_unavailable`",
    "those used for the questions, including Smart Router's `previous_enabled` from question 1.",
    "Git workflow: Saved locally — the main checkout could not be resolved; agent merge and cleanup are unavailable.",
    "**Git workflow and cleanup take effect only after this branch is merged into main.**",
    "committing and merging that file is the user's step"]) {
    assert.ok(flat.includes(phrase), phrase)
  }
  for (const phrase of ["saved in the main checkout", "Smart Router is saved only in this worktree", "main checkout's settings file will change",
    "must run from the main checkout", "run onboarding from the main checkout"]) {
    assert.ok(!flat.includes(phrase), phrase)
  }
})

test("the old feature name is not accepted by the tool, only by stored settings", async (t) => {
  const { execute } = await fixture(t)
  assert.equal((await execute({ feature: "pull_requests", op: "preview" })).reason, "invalid_request")
})

test("the procedure pins the cleanup questions, the GitHub conditions, the apply order and cancel", async (t) => {
  const { skills } = await fixture(t)
  const text = await readFile(new URL("./references/procedure.md", `file://${skills.get("vibe-onboard-project").path}`), "utf8")
  for (const phrase of ["Turn off git guidance", "skips questions 3 and 4",
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

test("onboarding off-on fences a title bind waiting on the host", async (t) => {
  const hooks = [], writes = []
  let release, entered
  const blocked = new Promise((done) => { release = done })
  const calling = new Promise((done) => { entered = done })
  const { execute, write, root } = await fixture(t, undefined, undefined, (ctx) => {
    ctx.location.project = { id: ctx.location.directory }
    ctx.tool.hook = async (name, fn) => { if (name === "execute.after") hooks.push(fn) }
    ctx.event = { subscribe: async function* () {} }
    ctx.storage = { get: async () => undefined, set: async (key) => { if (key.startsWith("session-titles/")) writes.push(key) },
      scan: async () => ({ entries: [] }) }
    ctx.session.get = async ({ sessionID }) => {
      entered()
      await blocked
      return { id: sessionID, title: "Untouched title", location: { directory: ctx.location.directory } }
    }
  })
  await write({ version: 1, features: [{ session_titles: { enabled: true } }] })
  const emit = async () => {
    for (const hook of hooks) await hook({ tool: "write", status: "completed", sessionID: "ses_fixture",
      input: { path: join(root, "docs/superpowers/specs/probe.md") } })
  }
  const pending = emit()
  try {
    await calling
    for (const enabled of [false, true]) {
      const preview = await titles(execute, { op: "preview", enabled })
      const saved = await titles(execute, { op: "apply", enabled, expected_snapshot: preview.snapshot })
      assert.equal(saved.state, "saved")
    }
  } finally { release() }
  await pending
  assert.deepEqual(writes, [])
  await emit()
  assert.deepEqual(writes, ["session-titles/ses_fixture"])
})

test("session_titles rejects git workflow booleans", async (t) => {
  const { execute } = await fixture(t)
  assert.equal((await titles(execute, { op: "preview", agent_merge: true })).reason, "invalid_request")
  assert.equal((await titles(execute, { op: "preview", worktree_cleanup: true })).reason, "invalid_request")
})

test("session_titles never runs the Smart Router setup check", async (t) => {
  const { mkdtemp } = await import("node:fs/promises")
  const bin = await mkdtemp(join(tmpdir(), "vibe-spy-")), log = join(bin, "calls")
  t.after(() => rm(bin, { recursive: true }))
  await writeFile(join(bin, "python3"), `#!/bin/sh\necho "$@" >> "${log}"\nexit 1\n`, { mode: 0o755 })
  const path = process.env.PATH
  process.env.PATH = `${bin}:${path}`
  t.after(() => { process.env.PATH = path })
  const { execute } = await fixture(t)
  const calls = async () => (await readFile(log, "utf8").catch(() => "")).split("\n").filter(Boolean)
  const preview = await titles(execute, { op: "preview" })
  await titles(execute, { op: "apply", enabled: true, expected_snapshot: preview.snapshot })
  assert.deepEqual(await calls(), [])
  await execute({ op: "preview" })
  assert.equal((await calls()).length, 1, "the spy sees the Smart Router check")
})

test("only apply with enabled true lets the setup check fetch scores", async (t) => {
  const root = await realpath(await mkdtemp(join(tmpdir(), "vibe-setup-argv-")))
  t.after(() => rm(root, { recursive: true }))
  await mkdir(join(root, "scripts"))
  await writeFile(join(root, "package.json"), '{"type":"module"}')
  for (const name of ["index.js", "src/project.js", "src/onboarding.js", "src/pr-advice.js", "src/advice.js", "src/skill-advice.js", "src/session-facts.js", "src/session-titles.js", "src/council.js", "src/claude-limit-events.js", "skills/vibe-onboard-project/SKILL.md",
    "skills/vibe-prepare-my-workspace/SKILL.md", "skills/vibe-pull-requests/SKILL.md", "skills/vibe-council/SKILL.md", "skills/vibe-project-layout/SKILL.md", "skills/vibe-security-review/SKILL.md"]) {
    await mkdir(join(root, name, ".."), { recursive: true })
    await copyFile(new URL(`../../${name}`, import.meta.url), join(root, name))
  }
  await writeFile(join(root, "scripts", "select.py"), `import json, sys
from pathlib import Path
with open(Path(__file__).parent.parent / "argv.log", "a") as log:
    log.write(" ".join(sys.argv[3:]) + "\\n")
print(json.dumps(dict(state="ready", reason=None)))
`)
  execFileSync("git", ["init", "-q", root])
  const tool = new Map()
  const ctx = { location: { directory: root }, options: { state_dir: join(root, "state") },
    tool: { transform: async (apply) => apply({ add: (entry) => tool.set(entry.name, entry) }) },
    skill: { transform: async (apply) => apply({ add: () => {} }) },
    permission: { assert: async () => {} }, session: { hook: async () => {} } }
  const cleanup = await (await import(pathToFileURL(join(root, "index.js")))).default.setup(ctx)
  t.after(() => cleanup?.())
  const run = async (input) => JSON.parse((await tool.get("vibe_workflow_onboard").execute({ v: 1, ...input },
    { sessionID: "s", messageID: "m", id: "c", agent: "build", signal: new AbortController().signal })).content)
  const preview = await run({ op: "preview" })
  assert.equal(preview.readiness.state, "ready")
  await run({ op: "apply", enabled: true, expected_snapshot: preview.snapshot })
  const lines = (await readFile(join(root, "argv.log"), "utf8")).trim().split("\n")
  assert.deepEqual(lines, ["--check-setup", "--check-setup --allow-fetch"])
})
