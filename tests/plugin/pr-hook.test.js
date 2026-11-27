import assert from "node:assert/strict"
import { execFileSync } from "node:child_process"
import { mkdir, mkdtemp, realpath, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"
import plugin from "../../index.js"

const git = (cwd, ...args) => execFileSync("git", ["-C", cwd, "-c", "core.hooksPath=/dev/null", "-c", "user.name=Fixture",
  "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", ...args], { stdio: "pipe", encoding: "utf8" })

async function fixture(t, { hook = true, settings = { enabled: true, agent_merge: false } } = {}) {
  const root = await realpath(await mkdtemp(join(tmpdir(), "vibe-pr-")))
  t.after(() => rm(root, { recursive: true }))
  git(root, "init", "-q", "-b", "main")
  git(root, "commit", "--allow-empty", "-qm", "fixture")
  if (settings) {
    await mkdir(join(root, ".vibe-workflow"))
    await writeFile(join(root, ".vibe-workflow", "settings.json"),
      typeof settings === "string" ? settings : JSON.stringify({ version: 1, features: [{ git_workflow: settings }] }))
  }
  const hooks = new Map(), registrations = [], contexts = [], tools = new Map()
  const ctx = {
    location: { directory: root }, options: { state_dir: join(root, "state") },
    tool: { transform: async (apply) => apply({ add: (entry) => tools.set(entry.name, entry) }),
      ...(hook && { hook: async (name, handler) => { registrations.push(name); hooks.set(name, handler) } }) },
    skill: { transform: async (apply) => apply({ add: () => {} }) },
    permission: { assert: async () => {} },
    session: { hook: async (name, handler) => { if (name === "context") contexts.push(handler) } },
  }
  const cleanup = await plugin.setup(ctx)
  t.after(() => cleanup?.())
  const fire = async (command, more = {}) => {
    const event = { tool: "bash", status: "completed", sessionID: "ses_1", input: { command, workdir: root }, result: "done", ...more }
    await hooks.get("execute.after")?.(event)
    return event
  }
  const context = async () => { const event = { system: [] }; for (const handler of contexts) await handler(event); return event.system.map((entry) => entry.text) }
  return { root, hooks, registrations, fire, context, cleanup, tools }
}

const create = 'gh pr create --draft --title "[BLOCKED] a"'

test("registers only when the host provides tool.hook", async (t) => {
  const { hooks, registrations } = await fixture(t)
  assert.deepEqual([...hooks.keys()], ["execute.after"])
  assert.deepEqual(registrations, ["execute.after"], "skill and shell advice share one registration")
  const without = await fixture(t, { hook: false })
  assert.equal(without.hooks.size, 0)
})

test("appends the note where the model reads it, following the host's result shape", async (t) => {
  const { fire } = await fixture(t)
  const note = /vibe-workflow:/
  // Real shape (OpenCode 2.2.6): the model is given `content`; `output` is only a fallback for empty content.
  const real = (await fire(create, { result: { output: "x", content: [{ type: "text", text: "x" }], metadata: { a: 1 } } })).result
  assert.equal(real.content.length, 2)
  assert.deepEqual(real.content[0], { type: "text", text: "x" })
  assert.equal(real.content[1].type, "text")
  assert.match(real.content[1].text, /^\n\nvibe-workflow:/)
  assert.equal(real.output, "x")
  assert.deepEqual(real.metadata, { a: 1 })
  const objectOutput = (await fire(create, { result: { output: { stdout: "u" }, content: [{ type: "text", text: "u" }] } })).result
  assert.match(objectOutput.content.at(-1).text, note)
  assert.deepEqual(objectOutput.output, { stdout: "u" })
  assert.match((await fire(create, { result: { content: "c" } })).result.content, /^c\n\nvibe-workflow:/)
  const empty = (await fire(create, { result: { output: "out", content: [] } })).result
  assert.deepEqual([empty.content.length, empty.content[0].type], [1, "text"])
  assert.match(empty.content[0].text, /^out\n\nvibe-workflow:/)
  assert.match((await fire(create, { result: { output: { k: 1 } } })).result.content[0].text, /^\{"k":1\}\n\nvibe-workflow:/)
  assert.match((await fire(create)).result, /^done\n\nvibe-workflow:/)
})

test("leaves failed calls, other tools, missing commands and unknown shapes unchanged", async (t) => {
  const { fire } = await fixture(t)
  const unchanged = async (command, more) => assert.equal((await fire(command, more)).result, "done", JSON.stringify(more))
  await unchanged(create, { status: "error" })
  await unchanged(create, { status: undefined })
  await unchanged(create, { tool: "read" })
  await unchanged(create, { result: "done", input: {} })
  await unchanged(create, { result: "done", input: { command: 5 } })
  for (const more of [{ result: { metadata: { exit: 1 } } }, { result: { metadata: { exitCode: 2 }, output: "x" } },
    { exitCode: 1 }, { metadata: { exit: 3 } }]) {
    const event = await fire(create, more)
    assert.doesNotMatch(JSON.stringify(event), /vibe-workflow/)
  }
  for (const result of [42, null, { other: "x" }, ["a"]]) {
    assert.deepEqual((await fire(create, { result })).result, result)
  }
})

test("a frozen event is left unchanged without throwing", async (t) => {
  const { hooks } = await fixture(t)
  const event = Object.freeze({ tool: "bash", status: "completed", sessionID: "s", input: Object.freeze({ command: create }),
    result: Object.freeze({ output: "x" }) })
  await hooks.get("execute.after")(event)
  const text = Object.freeze({ tool: "bash", status: "completed", input: { command: create }, result: "x" })
  await hooks.get("execute.after")(text)
  assert.equal(event.result.output, "x")
})

test("failing, invalid or disabled settings add nothing and never throw", async (t) => {
  for (const settings of ["not json", { enabled: false, agent_merge: false }, null, { enabled: "yes" }]) {
    const { fire } = await fixture(t, { settings })
    const note = "vibe-workflow: before this pull request is reviewed or merged, its Security section must cover the " +
      "current head (vibe-security-review ship check). If it is missing or stale, run the check now; if a blocking finding " +
      "is open, keep or return the pull request to draft, titled [BLOCKED] when the git workflow is on."
    assert.equal((await fire(create)).result, settings?.enabled === false ? `done\n\n${note}` : "done", JSON.stringify(settings))
  }
})

test("a missing sessionID keeps cases 1, 3 and 4 working", async (t) => {
  const { fire } = await fixture(t)
  for (const command of [create, "gh pr ready 3", "gh pr merge 3"]) {
    assert.match((await fire(command, { sessionID: undefined })).result, /vibe-workflow:/, command)
  }
})

test("case 4 names the main checkout, and case 5 follows the real checkout", async (t) => {
  const { fire, root } = await fixture(t)
  const merged = (await fire("gh pr merge 3")).result
  assert.ok(merged.includes(root))
  assert.equal((await fire("git checkout main")).result, "done")
  git(root, "checkout", "-q", "-b", "feat/x")
  const moved = (await fire("git checkout -b feat/x")).result
  assert.match(moved, /feat\/x/)
  assert.equal((await fire("git checkout -b feat/x")).result, "done")
  const linked = join(root, "linked")
  git(root, "worktree", "add", "-q", "--detach", linked)
  assert.equal((await fire("git switch -c y", { input: { command: "git switch -c y", workdir: linked } })).result, "done")
})

test("no pull request line is pushed into the context, with or without the hook", async (t) => {
  const line = /open pull requests as \[BLOCKED\] drafts/
  assert.ok(!(await (await fixture(t, { hook: false })).context()).some((text) => line.test(text)))
  assert.ok(!(await (await fixture(t)).context()).some((text) => line.test(text)))
  assert.ok(!(await (await fixture(t, { hook: false, settings: { enabled: false, agent_merge: false } })).context())
    .some((text) => line.test(text)))
})

test("onboarding readiness follows hook availability", async (t) => {
  const ready = async (options) => {
    const { tools } = await fixture(t, options)
    const result = await tools.get("vibe_workflow_onboard").execute({ v: 1, op: "preview", feature: "git_workflow" },
      { sessionID: "s", messageID: "m", id: "c", signal: new AbortController().signal })
    return JSON.parse(result.content).readiness
  }
  assert.deepEqual(await ready({ hook: true, settings: null }), { state: "ready", reason: null })
  assert.deepEqual(await ready({ hook: false, settings: null }), { state: "setup_needed", reason: "guard_unavailable" })
})

test("cleanup clears state and later events do nothing", async (t) => {
  const { fire, cleanup } = await fixture(t)
  assert.match((await fire(create)).result, /vibe-workflow:/)
  await cleanup()
  assert.equal((await fire(create)).result, "done")
})

test("odd results never throw and are left unchanged", async (t) => {
  const { fire } = await fixture(t)
  const circular = {}
  circular.self = circular
  for (const result of [{ output: circular }, { output: () => 1 }, { output: Symbol("s") }, { content: { length: 5 } }]) {
    const event = await fire(create, { result })
    assert.strictEqual(event.result, result)
  }
  const objectContent = (await fire(create, { result: { content: {}, output: "o" } })).result
  assert.match(objectContent.content[0].text, /^o\n\nvibe-workflow:/)
})

test("an old pull_requests entry no longer drives the hook", async (t) => {
  const { fire } = await fixture(t, { settings: JSON.stringify({ version: 1, features: [{ pull_requests: { enabled: true, agent_merge: false } }] }) })
  assert.equal((await fire(create)).result, "done")
})

test("a settings file with worktree_cleanup true adds the cleanup sentence to a gh pr merge result", async (t) => {
  const { fire } = await fixture(t, { settings: { enabled: true, agent_merge: true, worktree_cleanup: true } })
  assert.match((await fire("gh pr merge 3")).result, /Cleanup is on: once the merge is confirmed/)
})

test("a settings file without the worktree_cleanup key adds no cleanup sentence", async (t) => {
  const { fire } = await fixture(t, { settings: { enabled: true, agent_merge: true } })
  const result = (await fire("gh pr merge 3")).result
  assert.match(result, /vibe-workflow:/)
  assert.doesNotMatch(result, /Cleanup is on/)
})

test("a finishing-skill load gets the onboarding decision and ship check; failed loads and other skills do not", async (t) => {
  const skill = async (hooks, id, result, status = "completed") => {
    const event = { tool: "skill", status, sessionID: "ses_1", input: { id }, result }
    await hooks.get("execute.after")(event)
    return event.result
  }
  const { hooks } = await fixture(t, { settings: { enabled: true, agent_merge: true } })
  const array = await skill(hooks, "superpowers:finishing-a-development-branch", { content: [{ type: "text", text: "menu" }] })
  assert.equal(array.content.length, 2)
  assert.match(array.content[1].text, /^\n\nvibe-workflow: your human partner's declared integration choice/)
  assert.match(array.content[1].text, /You may merge it yourself once it is \[READY\]/)
  assert.match(await skill(hooks, "finishing-a-development-branch", "menu"), /^menu\n\nvibe-workflow: your human partner's declared integration choice/)
  const untouched = () => ({ content: [{ type: "text", text: "menu" }] })
  assert.deepEqual(await skill(hooks, "superpowers:finishing-a-development-branch", untouched(), "error"), untouched())
  assert.deepEqual(await skill(hooks, "superpowers:systematic-debugging", untouched()), untouched())
  const design = await skill(hooks, "superpowers:brainstorming", untouched())
  assert.equal(design.content.length, 2)
  assert.match(design.content[1].text, /^\n\nvibe-workflow: your human partner asks that the design check match the stakes/)
  const off = await fixture(t, { settings: { enabled: false, agent_merge: false } })
  const offResult = await skill(off.hooks, "superpowers:finishing-a-development-branch", untouched())
  assert.deepEqual(offResult.content[0], { type: "text", text: "menu" })
  assert.equal(offResult.content[1].text, "\n\nvibe-workflow: before you open or ready the pull request, confirm the vibe-security-review ship " +
    "check covers the current head and its Security section is in the body; run it if not. " +
    "An explicit request from your human partner in this session overrides this.")
})

test("a worktree-skill load gets the declared worktree preference only when the git workflow is on", async (t) => {
  const load = async (hooks, status = "completed") => {
    const event = { tool: "skill", status, sessionID: "ses_1", input: { id: "superpowers:using-git-worktrees" },
      result: { content: [{ type: "text", text: "consent?" }] } }
    await hooks.get("execute.after")(event)
    return event.result
  }
  const { hooks } = await fixture(t, { settings: { enabled: true, agent_merge: false } })
  const result = await load(hooks)
  assert.match(result.content[1].text, /^\n\nvibe-workflow: your human partner's declared worktree preference/)
  assert.match(result.content[1].text, /Do not ask for consent\./)
  assert.equal((await load(hooks, "error")).content.length, 1)
  const off = await fixture(t, { settings: { enabled: false, agent_merge: false } })
  assert.equal((await load(off.hooks)).content.length, 1)
})

test("a writing-plans load gets the method pre-approval when any feature is onboarded, never in a fresh project", async (t) => {
  const load = async (hooks) => {
    const event = { tool: "skill", status: "completed", sessionID: "ses_1", input: { id: "superpowers:writing-plans" },
      result: { content: [{ type: "text", text: "which approach?" }] } }
    await hooks.get("execute.after")(event)
    return event.result
  }
  const router = await fixture(t, { settings: JSON.stringify({ version: 1, features: [{ smart_router: { enabled: false } }] }) })
  assert.match((await load(router.hooks)).content[1].text, /^\n\nvibe-workflow: your human partner pre-approved the execution method/)
  assert.match((await load(router.hooks)).content[1].text, /follow the vibe-project-layout skill/)
  const fresh = await fixture(t, { settings: null })
  assert.equal((await load(fresh.hooks)).content.length, 1)
})
