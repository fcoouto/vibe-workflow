import assert from "node:assert/strict"
import { test } from "node:test"
import { mkdtempSync } from "node:fs"
import { mkdtemp, mkdir, writeFile, readFile, copyFile, realpath, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { pathToFileURL } from "node:url"

// Spawned selectors inherit this, so a real CLI can never reach the network.
process.env.VCW_TEST_HOME = mkdtempSync(join(tmpdir(), "vcw-test-home-"))

const info = (over = {}) => ({ id: "openai/gpt-x", modelID: "gpt-x", providerID: "openai", family: "gpt",
  variants: [{ id: "high" }], limit: { context: 100000 }, time: { released: "2026-01-01" },
  capabilities: { tools: true }, enabled: true, status: "active", ...over })

async function load() {
  const module = await import("../index.js").catch(() => null)
  assert.ok(module?.default?.setup, "standalone V2 plugin is missing")
  return module.default
}

function host(directory, options = {}, models) {
  const tools = new Map([["subagent", Object.freeze({ native: true })]])
  const hooks = new Map()
  const ctx = {
    location: { directory }, options,
    tool: { transform: async (callback) => callback({
      add: (tool) => tools.set(tool.name, tool),
    }) },
    skill: { transform: async (callback) => callback({ add: () => {} }) },
    session: { hook: async (name, callback) => hooks.set(name, callback) },
    ...(models && { model: { list: typeof models === "function" ? models : async () => models } }),
  }
  return { ctx, tools, hooks }
}

async function preference(root, enabled) {
  await mkdir(join(root, ".vibe-workflow"), { recursive: true })
  await writeFile(join(root, ".vibe-workflow", "settings.json"),
    typeof enabled === "boolean" ? JSON.stringify({ version: 1, features: [{ smart_router: { enabled } }] }) : enabled)
}

test("registers onboarding and keeps the native tool and existing context untouched", async (t) => {
  const plugin = await load()
  const root = await realpath(await mkdtemp(join(tmpdir(), "router-context-")))
  t.after(() => rm(root, { recursive: true }))
  await preference(root, true)
  const h = host(root)
  const original = h.tools.get("subagent")
  await plugin.setup(h.ctx)
  assert.equal(plugin.id, "vibe-workflow")
  assert.deepEqual([...h.tools.keys()], ["subagent", "smart_router_select", "vibe_workflow_onboard"])
  assert.equal(h.tools.get("subagent"), original)
  assert.deepEqual([...h.hooks.keys()], ["context"])
  const system = [{ type: "text", text: "Other plugin instructions" }]
  const permissions = Object.freeze({ edit: "ask" })
  const event = { system, tools: h.tools, permissions, options: {} }
  await h.hooks.get("context")(event)
  assert.equal(system[0].text, "Other plugin instructions")
  assert.equal(system.length, 2)
  assert.match(system[1].text, /advisory/i)
  assert.match(system[1].text, /before delegating/i)
  assert.match(system[1].text, /show .*notice.*chat/i)
  assert.match(system[1].text, /files.*spec.*risk/i)
  assert.equal(event.permissions, permissions)
  assert.equal(event.tools, h.tools)
  assert.deepEqual(event.options, {})
})

test("a host without models returns no authority without creating state", async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), "router-plugin-")))
  try {
    await preference(root, true)
    const h = host(root, { state_dir: join(root, "absent") })
    await (await load()).setup(h.ctx)
    const answer = JSON.parse((await h.tools.get("smart_router_select").execute(
      { v: 2, work_kind: "review", task }, { signal: new AbortController().signal })).content)
    assert.equal(answer.state, "no_selection")
    assert.equal(answer.reason, "no_models")
    assert.equal(answer.route, null)
    for (const key of ["admission", "reservation", "spend_permit"]) assert.equal(answer[key], false)
    await assert.rejects(readFile(join(root, "absent", "router.json")))
  } finally { await rm(root, { recursive: true }) }
})

const task = { files: "1-2", spec: "exact", risk: "reversible" }

async function fixture(body, models = [info()]) {
  const root = await realpath(await mkdtemp(join(tmpdir(), "router-transport-")))
  await mkdir(join(root, "scripts"))
  await writeFile(join(root, "package.json"), '{"type":"module"}')
  for (const name of ["config/routes.json", "index.js", "project.js", "onboarding.js", "pr-advice.js", "skills/vibe-onboard-project/SKILL.md",
    "skills/vibe-prepare-my-workspace/SKILL.md", "skills/vibe-pull-requests/SKILL.md"]) {
    const target = join(root, name)
    await mkdir(join(target, ".."), { recursive: true })
    await copyFile(new URL(`../${name}`, import.meta.url), target)
  }
  await writeFile(join(root, "scripts", "select.py"), body)
  await preference(root, true)
  const plugin = (await import(pathToFileURL(join(root, "index.js")))).default
  const h = host(root, { state_dir: join(root, "state") }, models)
  const cleanup = await plugin.setup(h.ctx)
  return { root, h, cleanup, execute: (signal = new AbortController().signal, sessionID = "ses_test", extra = {}) =>
    h.tools.get("smart_router_select").execute({ v: 2, work_kind: "review", task, ...extra }, { signal, sessionID }) }
}

test("passes actual location and exact provider/model/effort, without native dispatch", async () => {
  await load()
  const f = await fixture(`import json, os, sys
req = json.load(sys.stdin)
assert req.pop("models")[0]["id"] == "openai/gpt-x"
assert req == dict(v=2, work_kind="review", task=dict(files="1-2", spec="exact", risk="reversible"))
assert os.getcwd() == os.path.dirname(os.path.dirname(__file__))
print(json.dumps(dict(state="selected", route=dict(provider="invented", model="invented-model", effort="high"),
    admission=False, reservation=False, spend_permit=False)))
`)
  try {
    const answer = JSON.parse((await f.execute()).content)
    assert.deepEqual(answer.route, { provider: "invented", model: "invented-model", effort: "high" })
    for (const key of ["admission", "reservation", "spend_permit"]) assert.equal(answer[key], false)
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("malformed or oversized reader output never escapes as tool content", async () => {
  await load()
  for (const body of ['print("PRIVATE-SENTINEL")', 'print("PRIVATE-SENTINEL" * 10000)']) {
    const f = await fixture(body)
    try {
      const result = await f.execute()
      assert.equal(JSON.parse(result.content).state, "blocked")
      assert.match(JSON.parse(result.content).notice, /^vibe-workflow:.*Try.*again/i)
      assert.ok(!result.content.includes("PRIVATE-SENTINEL"))
    } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
  }
})

test("unonboarded, opted-out or malformed settings never spawn a selector or add routing context", async () => {
  const f = await fixture(`from pathlib import Path
Path("spawned").touch()
print("{}")
`)
  try {
    await rm(join(f.root, ".vibe-workflow", "settings.json"))
    for (const [enabled, reason] of [[null, "project_not_onboarded"], [false, "project_disabled"],
      ["malformed", "project_settings_invalid"],
      ['{"version":1,"smart_router":{"enabled":true}}', "project_settings_invalid"],
      ['{"version":1,"smart_router":{"enabled":false},"features":[{"smart_router":{"enabled":true}}]}',
        "project_settings_invalid"]]) {
      if (enabled !== null) await preference(f.root, enabled)
      const answer = JSON.parse((await f.execute()).content)
      assert.equal(answer.reason, reason)
      assert.equal(answer.route, null)
      assert.equal(answer.notice ?? null, null)
      await assert.rejects(readFile(join(f.root, "spawned")), { code: "ENOENT" })
      const system = [{ type: "text", text: "existing" }]
      await f.h.hooks.get("context")({ system })
      assert.deepEqual(system, [{ type: "text", text: "existing" }])
      assert.ok(f.h.tools.has("vibe_workflow_onboard"))
    }
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("enabled failures explain known reasons without exposing untrusted exclusion data", async () => {
  const generic = "vibe-workflow: Smart Router found no eligible route. Check routing settings or account availability. Continuing with your client's existing selection."
  const f = await fixture(`from pathlib import Path
print(Path("answer.json").read_text())
`)
  try {
    for (const [reason, exclusion, action] of [
      ["scores_unavailable", null, /scores/i], ["no_models", null, /models/i],
      ["no_eligible_route", "unscored", /scored model/i],
      ["no_recommendation", null, /clears this task's level/i],
      ["no_eligible_route", "billing_unverified", /sign-in/i],
      ["no_eligible_route", "quota_exhausted", /usage limits/i],
      ["no_eligible_route", "account_changed", /selected account/i],
      ["no_eligible_route", null, generic],
    ]) {
      await writeFile(join(f.root, "answer.json"), JSON.stringify({
        state: "no_selection", route: null, reason,
        excluded: exclusion ? [{ reason: exclusion, route: "PRIVATE-SENTINEL" }] : [],
        admission: false, reservation: false, spend_permit: false,
      }))
      const answer = JSON.parse((await f.execute()).content)
      assert.equal(answer.reason, reason)
      assert.equal(answer.route, null)
      assert.match(answer.notice, /^vibe-workflow:.*Continuing with your client's existing selection\.$/)
      if (action instanceof RegExp) assert.match(answer.notice, action)
      else assert.equal(answer.notice, action)
      assert.ok(!answer.notice.includes("PRIVATE-SENTINEL"))
    }
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("notices deduplicate per session and reset after success or opt-out", async () => {
  const f = await fixture(`from pathlib import Path
print(Path("answer.json").read_text())
`)
  const answerFile = join(f.root, "answer.json")
  const setResult = (selected) => writeFile(answerFile, JSON.stringify({
    state: selected ? "selected" : "no_selection", reason: selected ? null : "no_recommendation",
    route: selected ? { provider: "invented", model: "invented", effort: "high" } : null,
    admission: false, reservation: false, spend_permit: false,
  }))
  const execute = async (id) => JSON.parse((await f.execute(undefined, id)).content)
  try {
    await setResult(false)
    assert.ok((await execute("one")).notice)
    assert.equal((await execute("one")).notice, null)
    assert.ok((await execute("two")).notice)
    await setResult(true)
    assert.equal((await execute("one")).state, "selected")
    await setResult(false)
    assert.ok((await execute("one")).notice)
    await preference(f.root, false)
    assert.equal((await execute("one")).notice ?? null, null)
    await preference(f.root, true)
    assert.ok((await execute("one")).notice)
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("cancelled calls and unload stop owned selector processes without retry", async () => {
  await load()
  const f = await fixture("import sys\nsys.stdin.read()\nsys.stdin.read()\n")
  try {
    const controller = new AbortController()
    controller.abort()
    assert.equal(JSON.parse((await f.execute(controller.signal)).content).state, "blocked")
    await f.cleanup?.()
    assert.equal(JSON.parse((await f.execute()).content).state, "blocked")
  } finally { await rm(f.root, { recursive: true }) }
})

const answerWith = (extra) => JSON.stringify({ state: "no_selection", route: null, reason: "no_recommendation",
  excluded: [], admission: false, reservation: false, spend_permit: false, ...extra })
const echo = `from pathlib import Path
print(Path("answer.json").read_text())
`

test("passes the reduced model list and drops a caller-supplied one", async () => {
  const f = await fixture(`import json, sys
req = json.load(sys.stdin)
assert req["models"] == [dict(provider="openai", id="openai/gpt-x", model="gpt-x", family="gpt", variants=["high"],
    context=100000, released="2026-01-01"), dict(provider="claude-agent", id="bare", model=None, family=None,
    variants=[], context=None, released=None)], req["models"]
print(json.dumps(dict(state="no_selection", route=None, reason="no_recommendation", excluded=[],
    admission=False, reservation=False, spend_permit=False)))
`, [info(), info({ id: "bare", modelID: undefined, providerID: "claude-agent", family: undefined, variants: [],
    limit: undefined, time: undefined }), info({ id: "off", enabled: false }), info({ id: "old", status: "deprecated" }),
    info({ id: "notools", capabilities: { tools: false } }), info({ id: "or/x", providerID: "openrouter" })])
  try {
    const answer = JSON.parse((await f.execute(undefined, "ses_a", { models: [{ id: "evil" }] })).content)
    assert.equal(answer.reason, "no_recommendation")
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("strips internal fields and starts one bounded refresh", async () => {
  const f = await fixture(`import json, sys
from pathlib import Path
if "--refresh" in sys.argv:
    Path("refresh-stdin").write_text(sys.stdin.read())
    Path("refresh-argv").write_text(" ".join(sys.argv))
    sys.stdin.close()
    import time; time.sleep(30)
else:
    sys.stdin.read()
    print(Path("answer.json").read_text())
`)
  try {
    await writeFile(join(f.root, "answer.json"), answerWith({ refresh: { wanted: ["x"] }, first_fetch: true }))
    const result = await f.execute()
    assert.ok(!result.content.includes("refresh") && !result.content.includes("first_fetch"))
    for (let i = 0; i < 50; i++) {
      try { await readFile(join(f.root, "refresh-stdin")); break } catch { await new Promise((r) => setTimeout(r, 100)) }
    }
    assert.equal(await readFile(join(f.root, "refresh-stdin"), "utf8"), '{"wanted":["x"]}')
    assert.match(await readFile(join(f.root, "refresh-argv"), "utf8"), /--state-dir .* --refresh/)
    const started = Date.now()
    await f.cleanup()
    assert.ok(Date.now() - started < 2000)
  } finally { await rm(f.root, { recursive: true }) }
})

test("first fetch shows the disclosure once", async () => {
  const f = await fixture(echo)
  try {
    await writeFile(join(f.root, "answer.json"), answerWith({ first_fetch: true }))
    const first = JSON.parse((await f.execute()).content)
    assert.match(first.notice, /openrouter\.ai/)
    assert.match(first.notice, /sends no credentials/)
    assert.ok(!("first_fetch" in first))
    await writeFile(join(f.root, "answer.json"), answerWith({}))
    assert.doesNotMatch(JSON.parse((await f.execute()).content).notice ?? "", /openrouter/)
    await writeFile(join(f.root, "answer.json"), JSON.stringify({ state: "selected", reason: null, excluded: [],
      route: { provider: "p", model: "m", effort: "high" }, admission: false, reservation: false, spend_permit: false,
      first_fetch: true }))
    assert.match(JSON.parse((await f.execute()).content).notice, /openrouter\.ai/)
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("empty or failing model list never spawns", async () => {
  for (const models of [[], async () => { throw new Error("boom") }, [info({ providerID: "openrouter" })]]) {
    const f = await fixture(`from pathlib import Path
Path("spawned").touch()
print("{}")
`, models)
    try {
      const answer = JSON.parse((await f.execute()).content)
      assert.equal(answer.state, "no_selection")
      assert.equal(answer.reason, "no_models")
      assert.match(answer.notice, /models/i)
      await assert.rejects(readFile(join(f.root, "spawned")), { code: "ENOENT" })
    } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
  }
})

test("v1 callers learn what to send", async () => {
  const f = await fixture(echo)
  try {
    await writeFile(join(f.root, "answer.json"), answerWith({ state: "blocked", reason: "invalid_request" }))
    const answer = JSON.parse((await f.execute()).content)
    assert.match(answer.notice, /v: 2/)
    assert.match(answer.notice, /files, spec, risk/)
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("the tool describes its input and rubric", async () => {
  const h = host(await realpath(await mkdtemp(join(tmpdir(), "router-schema-"))))
  await (await load()).setup(h.ctx)
  const tool = h.tools.get("smart_router_select")
  assert.equal(tool.input.properties.v.const, 2)
  assert.deepEqual(tool.input.properties.task.required, ["files", "spec", "risk"])
  assert.equal(tool.input.properties.task.additionalProperties, false)
  assert.equal(tool.input.properties.task.properties.context_tokens.minimum, 0)
  assert.match(tool.description, /files: 1-2 \| 3\+ \| cross-system/)
})
