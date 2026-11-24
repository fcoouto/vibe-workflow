import assert from "node:assert/strict"
import { test } from "node:test"
import { execFileSync } from "node:child_process"
import { createHash } from "node:crypto"
import { mkdtempSync } from "node:fs"
import { mkdtemp, mkdir, writeFile, readFile, copyFile, realpath, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { pathToFileURL } from "node:url"

// Spawned selectors inherit this, so a real CLI can never reach the network.
process.env.VCW_TEST_HOME = mkdtempSync(join(tmpdir(), "vcw-test-home-"))

// Shapes as OpenCode 2.2.6 returns them: Model.Info with ms timestamps, inside a { location, data } envelope.
const info = (over = {}) => ({ id: "gpt-x", modelID: "gpt-x", providerID: "openai", type: "chat", family: "gpt",
  name: "GPT X", package: "@ai-sdk/openai", settings: {}, headers: {}, capabilities: { tools: true },
  variants: [{ id: "high" }], time: { released: 1767225600000 }, cost: { input: 0, output: 0 },
  status: "active", enabled: true, limit: { context: 100000, output: 32000 }, ...over })
const listed = (...data) => ({ location: { directory: "/project" }, data })

async function load() {
  const module = await import("../../index.js").catch(() => null)
  assert.ok(module?.default?.setup, "standalone V2 plugin is missing")
  return module.default
}

function host(directory, options = {}, models, extra = {}) {
  const tools = new Map([["subagent", Object.freeze({ native: true })]])
  const hooks = new Map()
  const hookOptions = new Map()
  const ctx = {
    location: { directory }, options,
    tool: { transform: async (callback) => callback({
      add: (tool) => tools.set(tool.name, tool),
    }) },
    skill: { transform: async (callback) => callback({ add: () => {} }) },
    session: { hook: async (name, callback, opts) => { hooks.set(name, callback); hookOptions.set(name, opts) },
      get: async () => undefined },
    ...(models && { model: { list: typeof models === "function" ? models : async () => models } }),
    ...extra,
  }
  return { ctx, tools, hooks, hookOptions }
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
  assert.deepEqual([...h.tools.keys()], ["subagent", "smart_router_select", "smart_router_feedback",
    "vibe_workflow_onboard", "vibe_council_roster"])
  assert.equal(h.tools.get("subagent"), original)
  assert.deepEqual([...h.hooks.keys()], ["context", "http.response"])
  assert.deepEqual(h.hookOptions.get("http.response"), { providerID: "openai" })
  const system = [{ type: "text", text: "Other plugin instructions" }]
  const permissions = Object.freeze({ edit: "ask" })
  const event = { system, tools: h.tools, permissions, options: {} }
  await h.hooks.get("context")(event)
  assert.equal(system[0].text, "Other plugin instructions")
  assert.equal(system.length, 1)
  assert.ok(system.every((part) => !/smart_router_select/.test(part.text)))
  assert.equal(event.permissions, permissions)
  assert.equal(event.tools, h.tools)
  assert.deepEqual(event.options, {})
})

test("a host without models still reports reviewer_independent and every excluded entry as unmatched", async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), "router-plugin-")))
  try {
    await preference(root, true)
    const h = host(root, { state_dir: join(root, "absent") })
    await (await load()).setup(h.ctx)
    const run = async (extra) => JSON.parse((await h.tools.get("smart_router_select").execute(
      { v: 2, work_kind: "review", task, ...extra }, { signal: new AbortController().signal })).content)
    const answer = await run({ reviewing: "openai/gpt-x", constraints: { excluded_models: ["b", "a", "b"] } })
    assert.equal(answer.reason, "no_models")
    assert.equal(answer.reviewer_independent, false)
    assert.deepEqual(answer.unmatched, ["b", "a"])
    const plain = await run({})
    assert.equal("reviewer_independent" in plain, false)
    assert.equal("unmatched" in plain, false)
    assert.equal("unmatched" in await run({ constraints: { excluded_models: [] } }), false)
  } finally { await rm(root, { recursive: true }) }
})

test("a host without models validates excluded_models like the runtime and bounds the echoed answer", async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), "router-plugin-")))
  try {
    await preference(root, true)
    const h = host(root, { state_dir: join(root, "absent") })
    await (await load()).setup(h.ctx)
    const run = async (entries) => JSON.parse((await h.tools.get("smart_router_select").execute(
      { v: 2, work_kind: "review", task, constraints: { excluded_models: entries } },
      { signal: new AbortController().signal })).content)
    for (const bad of [Array.from({ length: 257 }, (_, n) => `m${n}`), [""], ["x".repeat(257)], ["a\nb"], [7], "no"]) {
      const answer = await run(bad)
      assert.deepEqual([answer.state, answer.reason], ["blocked", "invalid_request"])
      assert.equal("unmatched" in answer, false)
    }
    const entries = Array.from({ length: 256 }, (_, n) => "字".repeat(250) + String(n).padStart(3, "0"))
    const raw = (await h.tools.get("smart_router_select").execute({ v: 2, work_kind: "review", task,
      constraints: { excluded_models: entries } }, { signal: new AbortController().signal })).content
    const answer = JSON.parse(raw)
    assert.equal(answer.reason, "no_models")
    assert.ok(Buffer.byteLength(raw) <= 65536)
    assert.deepEqual(answer.unmatched, entries.slice(0, answer.unmatched.length))
    assert.equal(answer.unmatched.length + answer.unmatched_more, 256)
    assert.ok(answer.unmatched_more > 0)
  } finally { await rm(root, { recursive: true }) }
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

async function fixture(body, models = listed(info()), extra = {}) {
  const root = await realpath(await mkdtemp(join(tmpdir(), "router-transport-")))
  await mkdir(join(root, "scripts"))
  await writeFile(join(root, "package.json"), '{"type":"module"}')
  for (const name of ["config/routes.json", "index.js", "src/project.js", "src/onboarding.js", "src/pr-advice.js", "src/advice.js", "src/skill-advice.js", "src/session-facts.js", "src/session-titles.js", "src/council.js", "skills/vibe-onboard-project/SKILL.md",
    "skills/vibe-prepare-my-workspace/SKILL.md", "skills/vibe-pull-requests/SKILL.md", "skills/vibe-council/SKILL.md", "skills/vibe-project-layout/SKILL.md"]) {
    const target = join(root, name)
    await mkdir(join(target, ".."), { recursive: true })
    await copyFile(new URL(`../../${name}`, import.meta.url), target)
  }
  await writeFile(join(root, "scripts", "select.py"), body)
  await preference(root, true)
  const plugin = (await import(pathToFileURL(join(root, "index.js")))).default
  const h = host(root, { state_dir: join(root, "state") }, models, extra)
  const cleanup = await plugin.setup(h.ctx)
  return { root, h, cleanup, execute: (signal = new AbortController().signal, sessionID = "ses_test", extra = {}) =>
    h.tools.get("smart_router_select").execute({ v: 2, work_kind: "review", task, ...extra }, { signal, sessionID }) }
}

test("passes actual location and exact provider/model/effort, without native dispatch", async () => {
  await load()
  const f = await fixture(`import json, os, sys
req = json.load(sys.stdin)
assert req.pop("models")[0]["id"] == "gpt-x"
assert req == dict(v=2, work_kind="review", task=dict(files="1-2", spec="exact", risk="reversible"))
assert os.getcwd() == os.path.dirname(os.path.dirname(__file__))
print(json.dumps(dict(state="selected", route=dict(provider="invented", model="invented-model", effort="high"),
    admission=False, reservation=False, spend_permit=False)))
`)
  try {
    const answer = JSON.parse((await f.execute()).content)
    assert.deepEqual(answer.route, { provider: "invented", model: "invented-model", effort: "high",
      model_ref: "invented/invented-model#high" })
    for (const key of ["admission", "reservation", "spend_permit"]) assert.equal(answer[key], false)
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("each selected pick carries a model_ref for the subagent tool's model", async () => {
  const f = await fixture(`from pathlib import Path
print(Path("answer.json").read_text())
`)
  try {
    await writeFile(join(f.root, "answer.json"), JSON.stringify({
      state: "selected", reason: null, route: { provider: "p", model: "m", effort: "high" },
      alternatives: [{ provider: "q", model: "n" }, { provider: "", model: "x" }, null],
      admission: false, reservation: false, spend_permit: false,
    }))
    const answer = JSON.parse((await f.execute()).content)
    assert.equal(answer.route.model_ref, "p/m#high")
    assert.equal(answer.alternatives[0].model_ref, "q/n")
    assert.ok(!("model_ref" in answer.alternatives[1]))
    assert.equal(answer.alternatives[2], null)
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("model_ref skips a null route and leaves no-selection answers unchanged", async () => {
  const f = await fixture(`from pathlib import Path
print(Path("answer.json").read_text())
`)
  try {
    const selectorAnswer = { state: "no_selection", reason: "no_recommendation", route: null,
      admission: false, reservation: false, spend_permit: false }
    await writeFile(join(f.root, "answer.json"), JSON.stringify(selectorAnswer))
    const answer = JSON.parse((await f.execute(undefined, "ses_null_route")).content)
    assert.equal(answer.route, null)
    assert.equal(answer.state, "no_selection")
    assert.equal(answer.reason, "no_recommendation")
    assert.ok(!("model_ref" in answer))
    assert.equal(answer.alternatives, undefined)
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
  const generic = "vibe-workflow: Smart Router found no eligible route. Check routing settings or account availability."
  const f = await fixture(`from pathlib import Path
print(Path("answer.json").read_text())
`)
  try {
    for (const [reason, exclusion, action] of [
      ["scores_unavailable", null, /scores/i], ["no_models", null, /models/i],
      ["no_eligible_route", "unscored", /scored model/i],
      ["unscored", "unscored", /No scored model fits this task yet/],
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
      const answer = JSON.parse((await f.execute(undefined, `ses_${reason}_${exclusion}`)).content)
      assert.equal(answer.reason, reason)
      assert.equal(answer.route, null)
      assert.match(answer.notice, /^vibe-workflow: Smart Router found no eligible route\. .*\.$/)
      assert.doesNotMatch(answer.notice, /existing selection/)
      if (action instanceof RegExp) assert.match(answer.notice, action)
      else assert.equal(answer.notice, action)
      assert.ok(!answer.notice.includes("PRIVATE-SENTINEL"))
    }
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("below-level answers get one notice per session and level", async () => {
  const f = await fixture(echo)
  const answerFile = join(f.root, "answer.json")
  const below = (level) => JSON.stringify({ state: "selected", reason: null, below_level: true, level,
    route: { provider: "invented", model: "invented", effort: "high" }, admission: false, reservation: false, spend_permit: false })
  const normal = JSON.stringify({ state: "selected", reason: null, route: { provider: "invented", model: "invented", effort: "high" },
    admission: false, reservation: false, spend_permit: false })
  const message = (level) => `vibe-workflow: Smart Router could not confirm a level ${level} match for this request and recommended the strongest available; excluded models may be the cause. Tell your human partner.`
  const execute = async (id) => JSON.parse((await f.execute(undefined, id)).content)
  try {
    await writeFile(answerFile, below(3))
    assert.equal((await execute("one")).notice, message(3))
    assert.equal((await execute("one")).notice, null)
    await writeFile(answerFile, normal)
    assert.equal((await execute("one")).notice, null)
    await writeFile(answerFile, below(3))
    assert.equal((await execute("one")).notice, null)
    await writeFile(answerFile, below(2))
    assert.equal((await execute("one")).notice, message(2))
    assert.equal((await execute("two")).notice, message(2))
    await writeFile(answerFile, below(3))
    assert.equal((await execute("two")).notice, message(3))
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

const SAME_LAB = "vibe-workflow: No reviewer from a different lab than the reviewed work reaches this task's level; Smart Router recommended the usual pick."
// A rateable below-level pick: the picked fixture plus the below-level fields.
const belowPick = (level) => ({ ...picked, below_level: true, level })

test("a same-lab review gets one notice per session, kept through an ordinary pick", async () => {
  const f = await fixture(echo)
  const answerFile = join(f.root, "answer.json")
  const execute = async (id) => JSON.parse((await f.execute(undefined, id, { reviewing: "openai/gpt-x#high" })).content)
  try {
    await writeFile(answerFile, JSON.stringify({ ...picked, reviewer_independent: false }))
    assert.equal((await execute("one")).notice, SAME_LAB)
    assert.equal((await execute("one")).notice, null)
    await writeFile(answerFile, JSON.stringify(picked))
    assert.equal((await execute("one")).notice, null)
    await writeFile(answerFile, JSON.stringify({ ...picked, reviewer_independent: false }))
    assert.equal((await execute("one")).notice, null)
    assert.equal((await execute("two")).notice, SAME_LAB)
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("no same-lab notice when the reviewer is independent or the field is absent", async () => {
  const f = await fixture(echo)
  const answerFile = join(f.root, "answer.json")
  const execute = async (id) => JSON.parse((await f.execute(undefined, id, { reviewing: "openai/gpt-x#high" })).content)
  try {
    await writeFile(answerFile, JSON.stringify({ ...picked, reviewer_independent: true }))
    assert.equal((await execute("one")).notice, null)
    await writeFile(answerFile, JSON.stringify(picked))
    assert.equal((await execute("one")).notice, null)
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a below-level answer shows only the below-level notice even when the reviewer is not independent", async () => {
  const f = await fixture(echo)
  const answerFile = join(f.root, "answer.json")
  try {
    await writeFile(answerFile, JSON.stringify({ ...belowPick(2), reviewer_independent: false }))
    const answer = JSON.parse((await f.execute(undefined, "one", { reviewing: "openai/gpt-x#high" })).content)
    assert.match(answer.notice, /^vibe-workflow: Smart Router could not confirm a level 2 match/)
    assert.doesNotMatch(answer.notice, /No reviewer from a different lab/)
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a below-level pick keeps its pick_id and hint but its card is ignored", async () => {
  const f = await pickFixture()
  try {
    await writeFile(join(f.root, "answer.json"), JSON.stringify(belowPick(2)))
    const answer = JSON.parse((await f.execute()).content)
    assert.match(answer.pick_id, /^[0-9a-f]{16}$/)
    assert.equal(answer.feedback_hint, FEEDBACK_HINT)
    assert.deepEqual(await rate(f, { pick_id: answer.pick_id, model_ref: "openai/gpt-x#high", rating: 4 }), { state: "ignored", reason: "not_rateable" })
    await assert.rejects(readFile(join(f.root, "feedback-stdin")), { code: "ENOENT" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("reviewing reaches the selector unchanged and reviewer_independent reaches the answer", async () => {
  const f = await fixture(`import json, sys
from pathlib import Path
Path("request.json").write_text(json.dumps(json.load(sys.stdin)))
print(Path("answer.json").read_text())
`)
  try {
    await writeFile(join(f.root, "answer.json"), JSON.stringify({ ...picked, reviewer_independent: true }))
    const answer = JSON.parse((await f.execute(undefined, "ses_a", { reviewing: "openai/gpt-x#high" })).content)
    assert.equal(answer.reviewer_independent, true)
    const request = JSON.parse(await readFile(join(f.root, "request.json"), "utf8"))
    assert.equal(request.reviewing, "openai/gpt-x#high")
    assert.equal("models" in request, true)
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
assert req["models"] == [dict(provider="openai", id="gpt-x", model="gpt-x", family="gpt", variants=["high"],
    context=100000, released=1767225600), dict(provider="claude-agent", id="bare", model=None, family=None,
    variants=[], context=None, released=None)], req["models"]
print(json.dumps(dict(state="no_selection", route=None, reason="no_recommendation", excluded=[],
    admission=False, reservation=False, spend_permit=False)))
`, listed(info(), info({ id: "bare", modelID: undefined, providerID: "claude-agent", family: undefined, variants: [],
    limit: undefined, time: undefined }), info({ id: "off", enabled: false }), info({ id: "old", status: "deprecated" }),
    info({ id: "notools", capabilities: { tools: false } }), info({ id: "or/x", providerID: "openrouter" })))
  try {
    const answer = JSON.parse((await f.execute(undefined, "ses_a", { models: [{ id: "evil" }] })).content)
    assert.equal(answer.reason, "no_recommendation")
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

const selected = `import json, sys
from pathlib import Path
Path("models.json").write_text(json.dumps(json.load(sys.stdin)["models"]))
print(json.dumps(dict(state="selected", route=dict(provider="openai", model="gpt-x", effort="high"),
    admission=False, reservation=False, spend_permit=False)))
`

test("live 2.2.6: the { location, data } model-list envelope reaches the selector", async () => {
  const f = await fixture(selected, async () => listed(info(), info({ id: "qwen4-max", modelID: "qwen4-max",
    family: "qwen4-max", variants: [], time: { released: 1794787200000 } })))
  try {
    assert.equal(JSON.parse((await f.execute()).content).state, "selected")
    const models = JSON.parse(await readFile(join(f.root, "models.json"), "utf8"))
    assert.deepEqual(models.map((m) => [m.id, m.variants, m.released]),
      [["gpt-x", ["high"], 1767225600], ["qwen4-max", [], 1794787200]])
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a bare model array still reaches the selector", async () => {
  const f = await fixture(selected, [info()])
  try {
    assert.equal(JSON.parse((await f.execute()).content).state, "selected")
    assert.equal(JSON.parse(await readFile(join(f.root, "models.json"), "utf8"))[0].id, "gpt-x")
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("coerces unexpected model fields and skips unsafe ids", async () => {
  const f = await fixture(`import json, sys
req = json.load(sys.stdin)
expected = [dict(provider="openai", id="float", model="gpt-x", family="gpt", variants=["v%d" % n for n in range(32)],
    context=None, released=1700000000), dict(provider="openai", id="mixed", model="gpt-x", family="gpt",
    variants=["high"], context=None, released=None), dict(provider="openai", id="text", model="gpt-x", family="gpt",
    variants=["high"], context=100000, released=None)]
assert req["models"] == expected, req["models"]
print(json.dumps(dict(state="no_selection", route=None, reason="no_recommendation", excluded=[],
    admission=False, reservation=False, spend_permit=False)))
`, listed(info({ id: "float", limit: { context: 1.5 }, time: { released: 1700000000999 },
    variants: Array.from({ length: 40 }, (_, n) => ({ id: `v${n}` })) }),
    info({ id: "mixed", limit: { context: -1 }, time: { released: 2 ** 60 }, variants: [{ id: 7 }, { id: "high" }, null] }),
    info({ id: "text", time: { released: "2026-01-01" } }),
    info({ id: "x".repeat(257) }), info({ id: "" }), info({ id: "bad\nid" }), info({ id: 7 }),
    info({ id: "noprovider", providerID: { evil: true } })))
  try {
    const answer = JSON.parse((await f.execute()).content)
    assert.equal(answer.reason, "no_recommendation")
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("strips internal fields and starts one bounded refresh", async () => {
  const f = await fixture(`import json, sys
from pathlib import Path
if "--refresh" in sys.argv:
    Path("refresh-stdin").write_text(sys.stdin.read())
    Path("refresh-argv").write_text(" ".join(sys.argv))
    import os, time
    Path("refresh-pid").write_text(str(os.getpid()))
    time.sleep(30)
else:
    sys.stdin.read()
    print(Path("answer.json").read_text())
`)
  try {
    await writeFile(join(f.root, "answer.json"), answerWith({ refresh: { wanted: ["x"] } }))
    const result = await f.execute()
    assert.ok(!result.content.includes("refresh"))
    for (let i = 0; i < 50; i++) {
      try { await readFile(join(f.root, "refresh-stdin")); break } catch { await new Promise((r) => setTimeout(r, 100)) }
    }
    assert.equal(await readFile(join(f.root, "refresh-stdin"), "utf8"), '{"wanted":["x"]}')
    assert.match(await readFile(join(f.root, "refresh-argv"), "utf8"), /--state-dir .* --refresh/)
    const pid = Number(await readFile(join(f.root, "refresh-pid"), "utf8"))
    await f.cleanup()
    let alive = true
    for (let i = 0; i < 40 && alive; i++) {
      try { process.kill(pid, 0); await new Promise((r) => setTimeout(r, 50)) } catch { alive = false }
    }
    assert.equal(alive, false, "cleanup left the refresh child running")
  } finally { await rm(f.root, { recursive: true }) }
})

test("a first score download adds no notice", async () => {
  const f = await fixture(echo)
  try {
    await writeFile(join(f.root, "answer.json"), answerWith({ first_fetch: true }))
    const failure = JSON.parse((await f.execute()).content)
    assert.match(failure.notice, /No model clears this task's level/)
    assert.doesNotMatch(failure.notice, /openrouter\.ai/)
    await writeFile(join(f.root, "answer.json"), JSON.stringify({ state: "selected", reason: null, excluded: [],
      route: { provider: "p", model: "m", effort: "high" }, admission: false, reservation: false, spend_permit: false,
      first_fetch: true }))
    assert.equal(JSON.parse((await f.execute()).content).notice, null)
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("empty or failing model list never spawns", async () => {
  for (const models of [listed(), [], { location: {} }, async () => { throw new Error("boom") },
    listed(info({ providerID: "openrouter" }))]) {
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
  assert.ok(tool.description.length < 2048)
  assert.match(tool.description, /Pass a pick's model_ref as the subagent tool's model\./)
  assert.match(tool.description, /critical = a mistake could lose real money/)
  assert.match(tool.description, /cross-system = changes in more than one repository or deployed service that must stay compatible with each other/)
  assert.match(tool.description, /To review a design or plan, use work_kind planning/)
  assert.doesNotMatch(tool.description, /haiku|opus|sonnet|fable|astra|terra|luna|gemini/i)
})

test("a malformed routes.json leaves the plugin loaded and routing empty", async () => {
  const f = await fixture(`from pathlib import Path
Path("spawned").touch()
`)
  try {
    await f.cleanup()
    for (const body of ["not json", '{"paths":"x"}']) {
      await writeFile(join(f.root, "config", "routes.json"), body)
      const plugin = (await import(pathToFileURL(join(f.root, "index.js")) + "?" + encodeURIComponent(body))).default
      const h = host(f.root, { state_dir: join(f.root, "state") }, listed(info()))
      const cleanup = await plugin.setup(h.ctx)
      assert.ok(h.tools.has("vibe_workflow_onboard"))
      const answer = JSON.parse((await h.tools.get("smart_router_select").execute(
        { v: 2, work_kind: "review", task }, { signal: new AbortController().signal, sessionID: "s" })).content)
      assert.equal(answer.reason, "no_models")
      await assert.rejects(readFile(join(f.root, "spawned")), { code: "ENOENT" })
      await cleanup()
    }
  } finally { await rm(f.root, { recursive: true }) }
})

const mark = `import sys
open("argv.log", "a").write(" ".join(sys.argv[1:]) + "\\n")
print('{"state":"marked"}')
`
const pause = (ms) => new Promise((r) => setTimeout(r, ms))
const runs = async (root) => (await readFile(join(root, "argv.log"), "utf8").catch(() => "")).split("\n").filter(Boolean)

function feed(reject = []) {
  const subscribers = []
  const out = { calls: [], push(event) {
    for (const s of subscribers) if (s.types.includes(event.type)) { s.queue.push(event); s.waiters.splice(0).forEach((r) => r()) }
  } }
  out.subscribe = (opts) => {
    out.calls.push(opts)
    if (opts.types.some((type) => reject.includes(type))) throw Error("unknown event type")
    const s = { types: opts.types, queue: [], waiters: [], finished: false }
    subscribers.push(s)
    out.finished = () => subscribers.every((each) => each.finished)
    return (async function* () {
      try {
        while (!opts.signal.aborted) {
          if (!s.queue.length) await new Promise((r) => { s.waiters.push(r); opts.signal.addEventListener("abort", r, { once: true }) })
          while (s.queue.length) yield s.queue.shift()
        }
      } finally { s.finished = true }
    })()
  }
  return out
}
const limit = { type: "provider.chatgpt-limit", message: "limit" }
// The session.step.failed envelope OpenCode 2.2.6 publishes.
const failed = (error) => ({ id: "evt_1", created: 1795132800000, type: "session.step.failed", durable: true,
  location: { directory: "/project" }, data: { sessionID: "ses_1", assistantMessageID: "msg_1", ...error && { error } } })

test("a chatgpt-limit step failure marks once", async () => {
  const events = feed()
  const f = await fixture(mark, listed(info()), { event: { subscribe: events.subscribe } })
  try {
    assert.deepEqual(events.calls.map((opts) => opts.types), [["session.step.failed"]])
    events.push(failed(limit))
    for (let i = 0; i < 50 && !(await runs(f.root)).length; i++) await pause(50)
    await pause(300) // the first run has finished, so only the 60 s window can stop the next one
    events.push(failed(limit))
    await pause(300)
    const log = await runs(f.root)
    assert.equal(log.length, 1)
    assert.match(log[0], /--state-dir .* --chatgpt-limit/)
    // After the window it re-arms.
    const real = Date.now
    Date.now = () => real() + 61000
    try {
      events.push(failed(limit))
      for (let i = 0; i < 50 && (await runs(f.root)).length < 2; i++) await pause(50)
    } finally { Date.now = real }
    assert.equal((await runs(f.root)).length, 2)
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("the error is also found at the top level or in properties", async () => {
  for (const envelope of [{ error: limit }, { properties: { error: limit } }]) {
    const events = feed()
    const f = await fixture(mark, listed(info()), { event: { subscribe: events.subscribe } })
    try {
      events.push({ type: "session.step.failed", ...envelope })
      for (let i = 0; i < 50 && !(await runs(f.root)).length; i++) await pause(50)
      assert.equal((await runs(f.root)).length, 1)
    } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
  }
})

test("other failures and other providers never mark", async () => {
  const events = feed()
  const f = await fixture(mark, listed(info()), { event: { subscribe: events.subscribe } })
  try {
    events.push(failed({ type: "provider.rate-limit", message: "x" }))
    events.push(failed({ type: "provider.auth", message: "x", status: 401 }))
    events.push(failed())
    await f.h.hooks.get("http.response")({ providerID: "anthropic", response: new Response("subscription_sharing_usage_limit_exceeded", { status: 429 }) })
    await f.h.hooks.get("http.response")({ response: new Response("subscription_sharing_usage_limit_exceeded", { status: 500 }) })
    await pause(400)
    assert.deepEqual(await runs(f.root), [])
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("http 429 fallback reads a clone", async () => {
  const f = await fixture(mark)
  try {
    const body = '{"error":{"code":"subscription_sharing_usage_limit_exceeded"}}'
    const response = new Response(body, { status: 429 })
    await f.h.hooks.get("http.response")({ providerID: "openai", response })
    for (let i = 0; i < 50 && !(await runs(f.root)).length; i++) await pause(50)
    assert.equal((await runs(f.root)).length, 1)
    assert.equal(await response.text(), body)
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("opted-out projects never mark", async () => {
  const events = feed()
  const f = await fixture(mark, listed(info()), { event: { subscribe: events.subscribe } })
  try {
    await preference(f.root, false)
    events.push(failed(limit))
    let pulled = false, cancelled = false
    const body = new ReadableStream({
      pull(controller) { pulled = true; controller.enqueue(Buffer.from("subscription_sharing_usage_limit_exceeded")); controller.close() },
      cancel() { cancelled = true },
    }, { highWaterMark: 0 })
    await f.h.hooks.get("http.response")({ response: { status: 429, clone: () => ({ body }) } })
    await pause(400)
    assert.deepEqual(await runs(f.root), [])
    assert.equal(pulled, false)
    assert.equal(cancelled, true)
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("http 429 fallback still marks when the host reads the body at once", async () => {
  const f = await fixture(mark)
  try {
    const body = '{"error":{"code":"subscription_sharing_usage_limit_exceeded"}}'
    const response = new Response(body, { status: 429 })
    const pending = f.h.hooks.get("http.response")({ providerID: "openai", response })
    assert.equal(await response.text(), body)
    await pending
    for (let i = 0; i < 50 && !(await runs(f.root)).length; i++) await pause(50)
    await pause(200)
    assert.equal((await runs(f.root)).length, 1)
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a rejected subscription leaves the plugin loaded", async () => {
  const events = feed(["session.step.failed"])
  const f = await fixture(mark, listed(info()), { event: { subscribe: events.subscribe } })
  try {
    assert.deepEqual(events.calls.map((opts) => opts.types), [["session.step.failed"]])
    assert.ok(f.h.tools.has("smart_router_select"))
    await f.cleanup()
    assert.ok(events.calls.every((opts) => opts.signal.aborted))
  } finally { await rm(f.root, { recursive: true }) }
})

test("cleanup aborts the subscription", async () => {
  const events = feed()
  const f = await fixture(mark, listed(info()), { event: { subscribe: events.subscribe } })
  try {
    assert.equal(events.calls.length, 1)
    assert.ok(events.calls.every((opts) => !opts.signal.aborted))
    await f.cleanup()
    assert.ok(events.calls.every((opts) => opts.signal.aborted))
    for (let i = 0; i < 20 && !events.finished(); i++) await pause(25)
    assert.equal(events.finished(), true)
  } finally { await rm(f.root, { recursive: true }) }
})

const seatsRequest = { v: 1, seats: [{ seat: "Ada", model: "openai/gpt-x", variant: null }] }
const checkedSelect = `import json, sys
req = json.load(sys.stdin)
assert "--council-roster" in sys.argv
assert all("name" not in m for m in req["models"])
print(json.dumps(dict(state="checked", seats=[dict(provider="openai", id="gpt-x", status="ok", paid_overflow="blocked")],
    admission=False, reservation=False, spend_permit=False)))
`
const roster = async (f, request = seatsRequest) =>
  JSON.parse((await f.h.tools.get("vibe_council_roster").execute(request, { signal: new AbortController().signal })).content)

test("council roster spawns select.py with --council-roster and returns its seats", async () => {
  const f = await fixture(checkedSelect)
  try {
    const answer = await roster(f)
    assert.equal(answer.ready, true)
    assert.equal(answer.seats[0].status, "ok")
    assert.equal(answer.seats[0].paid_overflow, "blocked")
    assert.equal(answer.seats[0].name, "GPT X")
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("council roster hides malformed Python output", async () => {
  const f = await fixture('print("PRIVATE-SENTINEL")')
  try {
    const answer = await roster(f)
    assert.equal(answer.reason, "roster_unavailable")
    assert.ok(!JSON.stringify(answer).includes("PRIVATE-SENTINEL"))
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("council roster ignores the Smart Router preference", async () => {
  const f = await fixture(checkedSelect)
  try {
    await preference(f.root, false)
    assert.equal((await roster(f)).seats[0].status, "ok")
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("smart_router_select request carries no name", async () => {
  const f = await fixture(`import json, sys
req = json.load(sys.stdin)
assert req["models"] and all("name" not in m for m in req["models"])
print(json.dumps(dict(state="selected", route=dict(provider="openai", model="gpt-x", effort="high"),
    admission=False, reservation=False, spend_permit=False)))
`)
  try {
    assert.equal(JSON.parse((await f.execute()).content).state, "selected")
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("council command forwards the skill line and strips mentions", async () => {
  const commands = new Map(), prompts = []
  const f = await fixture(checkedSelect, listed(info()), {
    command: { transform: async (cb) => cb({ add: (c) => commands.set(c.name, c) }) },
    session: { hook: async () => {}, prompt: async (p) => { prompts.push(p) } },
  })
  try {
    const command = commands.get("vibe-council")
    assert.equal(command.description, "Run the council on a set of decisions.")
    await command.execute({ sessionID: "s1", delivery: "d", prompt: { text: "  ", files: [{ path: "a", mention: "m" }] } })
    await command.execute({ sessionID: "s1", delivery: "d", prompt: { text: "  x " } })
    await command.execute({ sessionID: "s1", delivery: "d", prompt: { text: "on R1-R9", agents: [{ name: "x", mention: "m" }],
      skills: [{ name: "y", mention: "m" }] } })
    assert.equal(prompts[0].text, "Use the vibe-council skill.")
    assert.equal(prompts[1].text, "Use the vibe-council skill. x")
    assert.equal(prompts[2].text, "Use the vibe-council skill. on R1-R9")
    assert.deepEqual(prompts[0].files, [{ path: "a" }])
    assert.deepEqual(prompts[2].agents, [{ name: "x" }])
    assert.deepEqual(prompts[2].skills, [{ name: "y" }])
    assert.equal(prompts[2].sessionID, "s1")
    assert.equal(prompts[2].delivery, "d")
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("setup succeeds without a command API", async () => {
  const f = await fixture(checkedSelect)
  try {
    assert.ok(f.h.tools.has("vibe_council_roster"))
    assert.ok(f.h.tools.has("smart_router_select"))
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("council roster reports a crashed reader as roster_unavailable", async () => {
  const f = await fixture("import sys\nsys.exit(3)\n")
  try {
    const answer = await roster(f)
    assert.equal(answer.ready, false)
    assert.equal(answer.reason, "roster_unavailable")
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

const pickSelect = `import json, sys
from pathlib import Path
if "--refresh" in sys.argv:
    Path("refresh-stdin").write_text(sys.stdin.read())
    sys.exit(0)
req = json.load(sys.stdin)
assert "--council-pick" in sys.argv
assert set(req) == {"v", "count", "models"} and req["v"] == 1 and req["count"] == 3, req
assert req["models"] and all("name" not in m for m in req["models"])
print(json.dumps(dict(state="picked", reason="fewer_seats", seats=[dict(provider="openai", id="gpt-x", variant="high", score=60,
    score_source="openrouter", creator="openai", paid_overflow="blocked")], excluded=[],
    attribution="Scores: Artificial Analysis via OpenRouter", refresh=dict(wanted=["x"]),
    admission=False, reservation=False, spend_permit=False)))
`

test("council pick spawns select.py with --council-pick and starts one refresh", async () => {
  const f = await fixture(pickSelect)
  try {
    const answer = await roster(f, { v: 1 })
    assert.deepEqual([answer.ready, answer.reason], [false, "fewer_seats"])
    assert.deepEqual(answer.seats.map((s) => [s.seat, s.model, s.name, s.variant, s.score]), [["X", "openai/gpt-x", "GPT X", "high", 60]])
    assert.ok(!JSON.stringify(answer).includes("refresh"))
    for (let i = 0; i < 50; i++) {
      try { await readFile(join(f.root, "refresh-stdin")); break } catch { await new Promise((r) => setTimeout(r, 100)) }
    }
    assert.equal(await readFile(join(f.root, "refresh-stdin"), "utf8"), '{"wanted":["x"]}')
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("council pick ignores the Smart Router preference", async () => {
  const f = await fixture(pickSelect)
  try {
    await preference(f.root, false)
    assert.equal((await roster(f, { v: 1 })).seats[0].status, "ok")
    await rm(join(f.root, ".vibe-workflow"), { recursive: true })
    assert.equal((await roster(f, { v: 1 })).seats[0].status, "ok")
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("council pick hides malformed Python output", async () => {
  const f = await fixture('print("PRIVATE-SENTINEL")')
  try {
    const answer = await roster(f, { v: 1 })
    assert.equal(answer.reason, "roster_unavailable")
    assert.ok(!JSON.stringify(answer).includes("PRIVATE-SENTINEL"))
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

// The subagent tool's `model` parameter says never to set it unless the user asks; onboarded projects ask.
const MODEL_NOTE = "vibe-workflow: your human partner asks you to set the subagent tool's `model` and variant in " +
  "this project; this is the explicit request that tool requires. Use the first that applies: a model they or your " +
  "active skill name exactly; Smart Router's recommendation, when on; the tier your skill or plan calls for, on this " +
  "session's provider. Otherwise leave it unset."
const ROUTER_NOTE = "Smart Router is on: when no exact model is named, consult smart_router_select first, describing " +
  "the task by its facts: files, spec, risk. If it has no recommendation, show its notice in chat when present. Do not " +
  "retry automatically or override a native denial; routing grants no launch or spending authority."
const FULL = `NEVER set this unless the user explicitly asks for a particular model or variant. ${MODEL_NOTE} ${ROUTER_NOTE}`
const NEVER = "NEVER set this unless the user explicitly asks for a particular model or variant."
const schema = () => ({ subagent: { description: "Spawns an agent in a child session.", input: { type: "object",
  properties: { model: { type: "string", description: NEVER } } } } })

async function noted(settings, { git = false, entries } = {}) {
  const root = await realpath(await mkdtemp(join(tmpdir(), "model-note-")))
  if (git) execFileSync("git", ["init", "-q"], { cwd: root })
  if (settings !== null) await preference(root, settings)
  const h = host(root)
  const calls = []
  if (entries) h.ctx.session.instructions = { entry: {
    put: async (input) => { calls.push(["put", input]) },
    remove: (input) => {
      calls.push(["remove", input])
      if (entries === "throwing") throw Error("host torn down")
      return entries === "rejecting" ? Promise.reject(Error("refused")) : Promise.resolve()
    },
  } }
  const cleanup = await (await load()).setup(h.ctx)
  // OpenCode 2.3.2 passes the model as { id, providerID, variant }; no provider stands for an older host.
  const step = async (tools = schema(), { sessionID = "ses_a", providerID } = {}) => {
    const system = []
    const model = providerID ? { id: "model", providerID, variant: null } : undefined
    await h.hooks.get("context")({ system, tools, sessionID, options: {}, ...(model && { model }) })
    return { tools, system }
  }
  return { root, step, cleanup, calls, hook: h.hooks.get("context"),
    done: async () => { await cleanup?.(); await rm(root, { recursive: true }) } }
}

test("onboarded projects get the model note once on the subagent model parameter", async (t) => {
  for (const [enabled, expected] of [[false, `${NEVER} ${MODEL_NOTE}`], [true, FULL]]) {
    const n = await noted(enabled)
    t.after(n.done)
    const { tools } = await n.step()
    assert.equal(tools.subagent.input.properties.model.description, expected)
    assert.equal(tools.subagent.description, "Spawns an agent in a child session.")
  }
})

test("onboarded by git workflow alone still gets the note", async (t) => {
  const n = await noted(JSON.stringify({ version: 1, features: [{ git_workflow: { enabled: false } }] }), { git: true })
  t.after(n.done)
  const { tools } = await n.step()
  assert.equal(tools.subagent.input.properties.model.description, `${NEVER} ${MODEL_NOTE}`)
})

test("unonboarded projects keep the model description untouched", async (t) => {
  const n = await noted(null)
  t.after(n.done)
  const { tools } = await n.step()
  assert.equal(tools.subagent.input.properties.model.description, NEVER)
})

test("appends the note once when the host reuses the schema", async (t) => {
  const n = await noted(true)
  t.after(n.done)
  const tools = schema()
  for (let i = 0; i < 3; i++) await n.step(tools)
  assert.equal(tools.subagent.input.properties.model.description, FULL)
})

test("removes the note when the project is no longer onboarded", async (t) => {
  const n = await noted(false)
  t.after(n.done)
  const tools = schema()
  await n.step(tools)
  assert.equal(tools.subagent.input.properties.model.description, `${NEVER} ${MODEL_NOTE}`)
  await rm(join(n.root, ".vibe-workflow", "settings.json"))
  await n.step(tools)
  assert.equal(tools.subagent.input.properties.model.description, NEVER)
})

test("a frozen model schema is left alone and the hook still runs", async (t) => {
  const n = await noted(true)
  t.after(n.done)
  const tools = schema()
  Object.freeze(tools.subagent.input.properties.model)
  const { system } = await n.step(tools)
  assert.equal(tools.subagent.input.properties.model.description, NEVER)
  assert.deepEqual(system, [])
})

test("missing or odd tool shapes are a no-op", async (t) => {
  const n = await noted(true)
  t.after(n.done)
  const odd = [new Map([["subagent", schema().subagent]]), {}, { subagent: { input: null } },
    { subagent: { input: { properties: { model: { description: 7 } } } } }]
  for (const tools of odd) {
    const before = JSON.stringify(tools instanceof Map ? [...tools] : tools)
    await n.step(tools)
    assert.equal(JSON.stringify(tools instanceof Map ? [...tools] : tools), before)
  }
})

test("an event without tools stays without tools", async (t) => {
  const n = await noted(true)
  t.after(n.done)
  const event = { system: [], sessionID: "ses_a", options: {} }
  await n.hook(event)
  assert.equal("tools" in event, false)
  assert.deepEqual(event.system, [])
})

test("no note after unload", async (t) => {
  const n = await noted(true)
  t.after(async () => rm(n.root, { recursive: true }))
  await n.cleanup?.()
  const { tools } = await n.step()
  assert.equal(tools.subagent.input.properties.model.description, NEVER)
})

test("cleanup during an in-flight step leaves an annotated schema alone", async (t) => {
  const n = await noted(true)
  t.after(async () => rm(n.root, { recursive: true }))
  const tools = schema()
  tools.subagent.input.properties.model.description = `${NEVER} ${MODEL_NOTE}`
  const running = n.step(tools)
  await n.cleanup?.()
  await running
  assert.equal(tools.subagent.input.properties.model.description, `${NEVER} ${MODEL_NOTE}`)
})

test("router on adds the router note after the model note", async (t) => {
  const n = await noted(true)
  t.after(n.done)
  const { tools } = await n.step()
  assert.equal(tools.subagent.input.properties.model.description, FULL)
})

test("router off keeps only the model note", async (t) => {
  const n = await noted(false)
  t.after(n.done)
  const { tools } = await n.step()
  assert.equal(tools.subagent.input.properties.model.description, `${NEVER} ${MODEL_NOTE}`)
})

test("turning the router off removes its note from a reused schema", async (t) => {
  const n = await noted(true)
  t.after(n.done)
  const tools = schema()
  await n.step(tools)
  assert.equal(tools.subagent.input.properties.model.description, FULL)
  await preference(n.root, false)
  await n.step(tools)
  assert.equal(tools.subagent.input.properties.model.description, `${NEVER} ${MODEL_NOTE}`)
})

test("the router instruction is never pushed", async (t) => {
  const n = await noted(true)
  t.after(n.done)
  for (const providerID of ["claude-agent", "openai", "opencode-go", undefined]) {
    const { system } = await n.step(schema(), { providerID })
    assert.deepEqual(system, [])
  }
})

test("no session instruction entry call is made for any provider", async (t) => {
  for (const settings of [true, false, null]) {
    const n = await noted(settings, { entries: "working" })
    t.after(n.done)
    for (const providerID of ["claude-agent", "openai", "opencode-go", undefined]) {
      await n.step(schema(), { sessionID: `ses_${providerID}`, providerID })
    }
    assert.deepEqual(n.calls, [])
  }
})

test("a host without the tool hook gets no pull request push", async (t) => {
  const n = await noted(JSON.stringify({ version: 1, features: [{ git_workflow: { enabled: true } }] }), { git: true })
  t.after(n.done)
  const { system } = await n.step()
  assert.deepEqual(system, [])
})

const FEEDBACK_HINT = "After the subagent reports, you may rate its work with smart_router_feedback and this pick_id."
const otherModel = info({ id: "other", modelID: "other", providerID: "claude-agent", family: "other", variants: [] })
const picked = { state: "selected", reason: null, size_group: "short", level: 1,
  route: { provider: "openai", model: "gpt-x", effort: "high", catalog_id: { key: "gpt-x", creator: "openai", family: "gpt" },
    feedback: { sessions: 0, adjustment: 0 } },
  alternatives: [{ provider: "claude-agent", model: "other", catalog_id: { key: "other", creator: "anthropic", family: "other" },
    feedback: { sessions: 0, adjustment: 0 } }],
  admission: false, reservation: false, spend_permit: false }
// One stub: a selection answer from answer.json, and a feedback reply it chooses (default recorded).
const picking = (reply = '{"state":"recorded"}') => `import json, sys
from pathlib import Path
if "--feedback" in sys.argv:
    Path("feedback-stdin").write_text(sys.stdin.read())
    Path("feedback-argv").write_text(" ".join(sys.argv))
    print(${JSON.stringify(reply)})
else:
    sys.stdin.read()
    print(Path("answer.json").read_text())
`
async function pickFixture(reply, extra = {}) {
  const f = await fixture(picking(reply), listed(info(), otherModel), extra)
  await writeFile(join(f.root, "answer.json"), JSON.stringify(picked))
  return f
}
const rate = (f, input, sessionID = "ses_test") =>
  f.h.tools.get("smart_router_feedback").execute(input, { signal: new AbortController().signal, sessionID }).then((r) => JSON.parse(r.content))
const pickOf = async (f) => JSON.parse((await f.execute()).content).pick_id
const sentFeedback = async (f) => JSON.parse(await readFile(join(f.root, "feedback-stdin"), "utf8"))

test("a selected pick carries a pick_id and the feedback hint; no selection carries neither", async () => {
  const f = await pickFixture()
  try {
    const answer = JSON.parse((await f.execute()).content)
    assert.match(answer.pick_id, /^[0-9a-f]{16}$/)
    assert.equal(answer.feedback_hint, FEEDBACK_HINT)
    await writeFile(join(f.root, "answer.json"), answerWith({}))
    const none = JSON.parse((await f.execute()).content)
    assert.ok(!("pick_id" in none))
    assert.ok(!("feedback_hint" in none))
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("feedback is ignored for an unknown pick, another session, or Smart Router off", async () => {
  const f = await pickFixture()
  try {
    const pick_id = await pickOf(f)
    const input = { pick_id, model_ref: "openai/gpt-x#high", rating: 4 }
    assert.deepEqual(await rate(f, { ...input, pick_id: "0123456789abcdef" }), { state: "ignored", reason: "unknown_pick" })
    assert.deepEqual(await rate(f, input, "ses_other"), { state: "ignored", reason: "unknown_pick" })
    await preference(f.root, false)
    assert.deepEqual(await rate(f, input), { state: "ignored", reason: "router_off" })
    await assert.rejects(readFile(join(f.root, "feedback-stdin")), { code: "ENOENT" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a model ref that was not offered is refused before the selector runs", async () => {
  const f = await pickFixture()
  try {
    const pick_id = await pickOf(f)
    assert.deepEqual(await rate(f, { pick_id, model_ref: "openai/other#high", rating: 4 }),
      { state: "refused", reason: "not_offered" })
    await assert.rejects(readFile(join(f.root, "feedback-stdin")), { code: "ENOENT" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a valid rating reaches the selector with the card, session hash and rater, never the comment", async () => {
  const f = await pickFixture()
  try {
    const pick_id = await pickOf(f)
    f.h.ctx.session.get = async () => ({ model: { providerID: "claude-agent", id: "other" } })
    const answer = await rate(f, { pick_id, model_ref: "openai/gpt-x#high", rating: 4, comment: "PRIVATE-COMMENT" })
    assert.deepEqual(answer, { state: "recorded" })
    assert.ok(!JSON.stringify(answer).includes("PRIVATE-COMMENT"))
    const request = await sentFeedback(f)
    assert.deepEqual(Object.keys(request).sort(),
      ["comment", "rated", "rater_entry", "rater_model", "rating", "session", "size_group", "v", "work_kind"])
    assert.equal(request.v, 1)
    assert.deepEqual(request.rated, { key: "gpt-x", creator: "openai", family: "gpt" })
    assert.equal(request.work_kind, "review")
    assert.equal(request.size_group, "short")
    assert.equal(request.rating, 4)
    assert.equal(request.comment, "PRIVATE-COMMENT")
    assert.equal(request.session, createHash("sha256").update("ses_test").digest("hex").slice(0, 32))
    assert.equal(request.rater_entry.provider, "claude-agent")
    assert.equal(request.rater_entry.id, "other")
    assert.equal("name" in request.rater_entry, false)
    assert.deepEqual(request.rater_model, { provider: "claude-agent", id: "other" })
    assert.match(await readFile(join(f.root, "feedback-argv"), "utf8"), /--feedback/)
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a rater the host cannot name sends rater_entry null", async () => {
  const f = await pickFixture()
  try {
    const pick_id = await pickOf(f)
    const input = { pick_id, model_ref: "openai/gpt-x#high", rating: 3 }
    const cases = [
      () => { delete f.h.ctx.session.get },
      () => { f.h.ctx.session.get = async () => { throw Error("gone") } },
      () => { f.h.ctx.session.get = async () => ({}) },
      () => { f.h.ctx.session.get = async () => ({ model: { providerID: "claude-agent", id: "unlisted" } }) },
    ]
    for (const arrange of cases) {
      arrange()
      assert.deepEqual(await rate(f, input), { state: "recorded" })
      assert.equal((await sentFeedback(f)).rater_entry, null)
    }
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("an unlisted rater still sends its raw session model, so Python can refuse a cross-provider self-rating", async () => {
  const f = await pickFixture()
  try {
    const pick_id = await pickOf(f)
    f.h.ctx.session.get = async () => ({ model: { providerID: "other-host", id: "gpt-x" } })
    assert.deepEqual(await rate(f, { pick_id, model_ref: "openai/gpt-x#high", rating: 3 }), { state: "recorded" })
    const request = await sentFeedback(f)
    assert.equal(request.rater_entry, null)
    assert.deepEqual(request.rater_model, { provider: "other-host", id: "gpt-x" })
    f.h.ctx.session.get = async () => ({})
    await rate(f, { pick_id, model_ref: "openai/gpt-x#high", rating: 3 })
    assert.equal((await sentFeedback(f)).rater_model, null)
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("turning Smart Router off while a rating looks up the session leaves it ignored and unsent", async () => {
  const f = await pickFixture()
  try {
    const pick_id = await pickOf(f)
    let release
    f.h.ctx.session.get = () => new Promise((resolve) => { release = () => resolve({ model: { providerID: "claude-agent", id: "other" } }) })
    const pending = rate(f, { pick_id, model_ref: "openai/gpt-x#high", rating: 4 })
    while (!release) await new Promise((resolve) => setImmediate(resolve))
    await preference(f.root, false)
    assert.equal(JSON.parse((await f.execute()).content).state, "no_selection")
    release()
    assert.deepEqual(await pending, { state: "ignored", reason: "router_off" })
    await assert.rejects(readFile(join(f.root, "feedback-stdin")), { code: "ENOENT" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a pick whose refs have no usable catalog_id is not remembered, so its card is ignored", async () => {
  const f = await pickFixture()
  try {
    const bad = (id) => ({ provider: "openai", model: "gpt-x", catalog_id: id })
    for (const id of [undefined, { key: "", creator: "openai", family: "gpt" }, { key: "gpt-x", creator: 1, family: "gpt" },
      { key: "gpt-x", creator: "openai" }]) {
      await writeFile(join(f.root, "answer.json"), JSON.stringify({ ...picked, route: bad(id), alternatives: [] }))
      const pick_id = await pickOf(f)
      assert.deepEqual(await rate(f, { pick_id, model_ref: "openai/gpt-x#high", rating: 4 }), { state: "ignored", reason: "not_rateable" })
    }
    await assert.rejects(readFile(join(f.root, "feedback-stdin")), { code: "ENOENT" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a pick whose family key is empty (a numeric model) is still rated", async () => {
  const f = await pickFixture()
  try {
    const route = { ...picked.route, catalog_id: { key: "123", creator: "openai", family: "" } }
    await writeFile(join(f.root, "answer.json"), JSON.stringify({ ...picked, route, alternatives: [] }))
    const pick_id = await pickOf(f)
    assert.deepEqual(await rate(f, { pick_id, model_ref: "openai/gpt-x#high", rating: 4 }), { state: "recorded" })
    assert.deepEqual((await sentFeedback(f)).rated, { key: "123", creator: "openai", family: "" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("rating a pick with the model that made it is refused before the selector runs", async () => {
  const f = await pickFixture()
  try {
    const pick_id = await pickOf(f)
    f.h.ctx.session.get = async () => ({ model: { providerID: "openai", id: "gpt-x" } })
    assert.deepEqual(await rate(f, { pick_id, model_ref: "openai/gpt-x#high", rating: 5 }),
      { state: "refused", reason: "self_rating" })
    await assert.rejects(readFile(join(f.root, "feedback-stdin")), { code: "ENOENT" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a dispatch at another effort still counts as the offered model", async () => {
  const f = await pickFixture()
  try {
    const pick_id = await pickOf(f)
    assert.deepEqual(await rate(f, { pick_id, model_ref: "openai/gpt-x#low", rating: 2 }), { state: "recorded" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a selector that cannot answer feedback reports failed", async () => {
  const f = await pickFixture("garbage")
  try {
    const pick_id = await pickOf(f)
    assert.deepEqual(await rate(f, { pick_id, model_ref: "openai/gpt-x#high", rating: 4 }), { state: "failed" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("the select and feedback descriptions teach the plan rule, the hours and the rating scale", async () => {
  const h = host(await realpath(await mkdtemp(join(tmpdir(), "router-text-"))))
  await (await load()).setup(h.ctx)
  const select = h.tools.get("smart_router_select")
  assert.ok(select.description.length < 2048)
  assert.match(select.description, /one task of a written plan/)
  assert.match(select.description, /estimate_agentic_effort_hours/)
  assert.match(select.description, /One request per delegated task\. Rate risk by what a mistake would cost/)
  assert.doesNotMatch(select.description, /Rate the task, not the project/)
  assert.doesNotMatch(select.description, /haiku|opus|sonnet|fable|astra|terra|luna|gemini/i)
  const feedback = h.tools.get("smart_router_feedback")
  assert.equal(feedback.description, "Optional. Rate a subagent's finished work for a Smart Router pick: 0 = failed, " +
    "3 = met the stated checks, 5 = met them with no rework needed. Skip it when the brief was at fault or the provider " +
    "failed. One card per session, model and kind of work. Ratings adjust later picks on this machine; Smart Router sends nothing.")
  assert.doesNotMatch(feedback.description, /haiku|opus|sonnet|fable|astra|terra|luna|gemini/i)
})

test("the select schema bounds the hours, lists only excluded_models and takes reviewing; feedback bounds its input", async () => {
  const h = host(await realpath(await mkdtemp(join(tmpdir(), "router-schema2-"))))
  await (await load()).setup(h.ctx)
  const select = h.tools.get("smart_router_select").input
  const { properties } = select
  assert.deepEqual(properties.task.properties.estimate_agentic_effort_hours, { type: "number", minimum: 0, maximum: 100 })
  assert.equal(properties.constraints.type, "object")
  assert.equal(properties.constraints.additionalProperties, false)
  assert.deepEqual(Object.keys(properties.constraints.properties), ["excluded_models"])
  assert.deepEqual(properties.constraints.properties.excluded_models, { type: "array", items: { type: "string" },
    description: "Models to leave out for this request only: a pick's model_ref, a catalog key or a model name." })
  assert.deepEqual(properties.reviewing, { type: "string",
    description: "For a review: the model_ref of the model whose work is reviewed (provider/model, or provider/creator/model for a routed model); the router prefers a reviewer from another lab, never at a lower level." })
  assert.deepEqual(Object.keys(properties).sort(), ["constraints", "reviewing", "task", "v", "work_kind"])
  assert.doesNotMatch(JSON.stringify(properties), /haiku|opus|sonnet|fable|astra|terra|luna|gemini/i)
  const feedback = h.tools.get("smart_router_feedback").input
  assert.equal(feedback.additionalProperties, false)
  assert.deepEqual(feedback.required, ["pick_id", "model_ref", "rating"])
  assert.deepEqual(feedback.properties.rating, { type: "integer", minimum: 0, maximum: 5 })
  assert.deepEqual(feedback.properties.comment, { type: "string", maxLength: 280 })
})

const selectWithoutSession = (f) =>
  f.h.tools.get("smart_router_select").execute({ v: 2, work_kind: "review", task }, { signal: new AbortController().signal })
    .then((r) => JSON.parse(r.content))
const cardFor = (f, input, sessionID) =>
  f.h.tools.get("smart_router_feedback").execute(input, { signal: new AbortController().signal, sessionID })
    .then((r) => JSON.parse(r.content))

test("a selection without a session still gets a pick_id and the hint, and its card is ignored", async () => {
  const f = await pickFixture()
  try {
    const answer = await selectWithoutSession(f)
    assert.match(answer.pick_id, /^[0-9a-f]{16}$/)
    assert.equal(answer.feedback_hint, FEEDBACK_HINT)
    assert.deepEqual(await cardFor(f, { pick_id: answer.pick_id, model_ref: "openai/gpt-x#high", rating: 4 }, undefined),
      { state: "ignored", reason: "unknown_pick" })
    await assert.rejects(readFile(join(f.root, "feedback-stdin")), { code: "ENOENT" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a ref without catalog_id cannot be rated", async () => {
  const f = await pickFixture()
  try {
    const alternatives = [{ provider: "claude-agent", model: "other", feedback: { sessions: 0, adjustment: 0 } }]
    await writeFile(join(f.root, "answer.json"), JSON.stringify({ ...picked, alternatives }))
    const answer = JSON.parse((await f.execute()).content)
    assert.deepEqual(await rate(f, { pick_id: answer.pick_id, model_ref: "claude-agent/other#high", rating: 4 }),
      { state: "refused", reason: "not_offered" })
    assert.deepEqual(await rate(f, { pick_id: answer.pick_id, model_ref: "openai/gpt-x#high", rating: 4 }), { state: "recorded" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a size group the selector does not name is never guessed: the pick is issued but cannot be rated", async () => {
  const f = await pickFixture()
  try {
    const { size_group: _size, ...unsized } = picked
    for (const answer of [unsized, { ...picked, size_group: "medium" }]) {
      await writeFile(join(f.root, "answer.json"), JSON.stringify(answer))
      const issued = JSON.parse((await f.execute()).content)
      assert.match(issued.pick_id, /^[0-9a-f]{16}$/)
      assert.equal(issued.feedback_hint, FEEDBACK_HINT)
      assert.deepEqual(await rate(f, { pick_id: issued.pick_id, model_ref: "openai/gpt-x#high", rating: 4 }), { state: "ignored", reason: "not_rateable" })
    }
    await assert.rejects(readFile(join(f.root, "feedback-stdin")), { code: "ENOENT" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a long pick is rated in the long size group", async () => {
  const f = await pickFixture()
  try {
    await writeFile(join(f.root, "answer.json"), JSON.stringify({ ...picked, size_group: "long" }))
    const pick_id = await pickOf(f)
    assert.deepEqual(await rate(f, { pick_id, model_ref: "openai/gpt-x#high", rating: 4 }), { state: "recorded" })
    assert.equal((await sentFeedback(f)).size_group, "long")
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a selection that observes Smart Router off forgets the picks, so the old pick_id stays ignored after re-enabling", async () => {
  const f = await pickFixture()
  try {
    const pick_id = await pickOf(f)
    await preference(f.root, false)
    assert.equal(JSON.parse((await f.execute()).content).state, "no_selection")
    await preference(f.root, true)
    assert.deepEqual(await rate(f, { pick_id, model_ref: "openai/gpt-x#high", rating: 4 }), { state: "ignored", reason: "unknown_pick" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("onboarding turning Smart Router off forgets the picks, so the old pick_id stays ignored after re-enabling", async () => {
  const f = await pickFixture(undefined, { permission: { assert: async () => {} } })
  try {
    const pick_id = await pickOf(f)
    const invocation = { sessionID: "ses_test", messageID: "m", id: "c", agent: "build", signal: new AbortController().signal }
    const onboard = (input) => f.h.tools.get("vibe_workflow_onboard").execute({ v: 1, ...input }, invocation)
      .then((r) => JSON.parse(r.content))
    const preview = await onboard({ op: "preview", enabled: false })
    const applied = await onboard({ op: "apply", enabled: false, expected_snapshot: preview.snapshot })
    assert.notEqual(applied.state, "blocked")
    assert.match(await readFile(join(f.root, ".vibe-workflow", "settings.json"), "utf8"), /"enabled": false/)
    await preference(f.root, true)
    assert.deepEqual(await rate(f, { pick_id, model_ref: "openai/gpt-x#high", rating: 4 }), { state: "ignored", reason: "unknown_pick" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a card seen while Smart Router is off forgets the picks, so the old pick_id stays ignored after re-enabling", async () => {
  const f = await pickFixture()
  try {
    const pick_id = await pickOf(f)
    await preference(f.root, false)
    assert.deepEqual(await rate(f, { pick_id, model_ref: "openai/gpt-x#high", rating: 4 }), { state: "ignored", reason: "router_off" })
    await preference(f.root, true)
    assert.deepEqual(await rate(f, { pick_id, model_ref: "openai/gpt-x#high", rating: 4 }), { state: "ignored", reason: "unknown_pick" })
    await assert.rejects(readFile(join(f.root, "feedback-stdin")), { code: "ENOENT" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("the 429 fallback seeing Smart Router off forgets the picks, so the old pick_id stays ignored after re-enabling", async () => {
  const f = await pickFixture()
  try {
    const pick_id = await pickOf(f)
    await preference(f.root, false)
    const body = '{"error":{"code":"subscription_sharing_usage_limit_exceeded"}}'
    await f.h.hooks.get("http.response")({ providerID: "openai", response: new Response(body, { status: 429 }) })
    await preference(f.root, true)
    assert.deepEqual(await rate(f, { pick_id, model_ref: "openai/gpt-x#high", rating: 4 }), { state: "ignored", reason: "unknown_pick" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("an onboarding preview that sees the router off forgets picks, so re-enabling keeps the old pick_id ignored", async () => {
  const f = await pickFixture(undefined, { permission: { assert: async () => {} } })
  try {
    const pick_id = await pickOf(f)
    await preference(f.root, false)
    const invocation = { sessionID: "ses_test", messageID: "m", id: "c", agent: "build", signal: new AbortController().signal }
    const onboard = (input) => f.h.tools.get("vibe_workflow_onboard").execute({ v: 1, ...input }, invocation)
      .then((r) => JSON.parse(r.content))
    const preview = await onboard({ op: "preview", enabled: true })
    assert.equal(preview.previous_enabled, false)
    const applied = await onboard({ op: "apply", enabled: true, expected_snapshot: preview.snapshot })
    assert.notEqual(applied.state, "blocked")
    assert.deepEqual(await rate(f, { pick_id, model_ref: "openai/gpt-x#high", rating: 4 }), { state: "ignored", reason: "unknown_pick" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("the context hook seeing Smart Router off also forgets the picks", async () => {
  const f = await pickFixture()
  try {
    const pick_id = await pickOf(f)
    await preference(f.root, false)
    await f.h.hooks.get("context")({ system: [] })
    await preference(f.root, true)
    assert.deepEqual(await rate(f, { pick_id, model_ref: "openai/gpt-x#high", rating: 4 }), { state: "ignored", reason: "unknown_pick" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a selection that resolves after Smart Router is turned off stores no pick", async () => {
  const turnsOff = `import json, sys
from pathlib import Path
if "--feedback" in sys.argv:
    print('{"state":"recorded"}')
else:
    sys.stdin.read()
    Path(".vibe-workflow", "settings.json").write_text('{"version":1,"features":[{"smart_router":{"enabled":false}}]}')
    print(Path("answer.json").read_text())
`
  const f = await fixture(turnsOff, listed(info(), otherModel))
  try {
    await writeFile(join(f.root, "answer.json"), JSON.stringify(picked))
    const answer = JSON.parse((await f.execute()).content)
    assert.match(answer.pick_id, /^[0-9a-f]{16}$/)
    await preference(f.root, true)
    assert.deepEqual(await rate(f, { pick_id: answer.pick_id, model_ref: "openai/gpt-x#high", rating: 4 }), { state: "ignored", reason: "unknown_pick" })
    await assert.rejects(readFile(join(f.root, "feedback-stdin")), { code: "ENOENT" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

// A Map-backed ctx.storage like the host's: JSON values, scan by prefix with an `after` cursor and a `limit`.
function memoryStorage(entries = new Map()) {
  return {
    entries,
    get: async (key) => entries.has(key) ? JSON.parse(entries.get(key)) : undefined,
    set: async (key, json) => { entries.set(key, JSON.stringify(json)) },
    remove: async (key) => { entries.delete(key) },
    scan: async ({ prefix = "", after, limit = 100 } = {}) => {
      const keys = [...entries.keys()].filter((key) => key.startsWith(prefix) && (after === undefined || key > after)).sort()
      const page = keys.slice(0, limit)
      return { entries: page.map((key) => ({ key, value: JSON.parse(entries.get(key)) })),
        ...(keys.length > limit && { next: page.at(-1) }) }
    },
  }
}
const flush = () => new Promise((resolve) => setImmediate(resolve))
const WINDOW_MS = 48 * 3600 * 1000
const storedKeys = (storage) => [...storage.entries.keys()].filter((key) => key.startsWith("pick/"))
const sessionHash = (sessionID) => createHash("sha256").update(sessionID).digest("hex").slice(0, 32)
// A stored pick as this version writes it, for the session ses_test.
const record = (over = {}) => ({ v: 1, s: sessionHash("ses_test"), t: Date.now(), work_kind: "review", size_group: "short",
  rateable: true, refs: [["openai/gpt-x", { provider: "openai", model: "gpt-x",
    catalog_id: { key: "gpt-x", creator: "openai", family: "gpt" } }]], ...over })
// A second plugin instance on the same project and storage, as after a restart: it shares no memory with the first.
async function restarted(f, storage) {
  const plugin = (await import(pathToFileURL(join(f.root, "index.js")))).default
  const h = host(f.root, { state_dir: join(f.root, "state") }, listed(info(), otherModel), { storage })
  const cleanup = await plugin.setup(h.ctx)
  return { root: f.root, h, cleanup }
}
const cardInput = { model_ref: "openai/gpt-x#high", rating: 4 }

test("a rateable pick survives a restart: the second instance accepts its card for the same session", async () => {
  const storage = memoryStorage()
  const f = await pickFixture(undefined, { storage })
  let b
  try {
    const pick_id = await pickOf(f)
    const stored = JSON.parse(storage.entries.get(`pick/${pick_id}`))
    assert.deepEqual(Object.keys(stored).sort(), ["rateable", "refs", "s", "size_group", "t", "v", "work_kind"])
    assert.equal(stored.s, sessionHash("ses_test"))
    assert.equal(stored.rateable, true)
    b = await restarted(f, storage)
    assert.deepEqual(await rate(b, { pick_id, ...cardInput, rating: 4 }), { state: "recorded" })
    assert.equal((await sentFeedback(b)).size_group, "short")
  } finally { await b?.cleanup?.(); await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a stored pick is not accepted from another session", async () => {
  const storage = memoryStorage()
  const f = await pickFixture(undefined, { storage })
  let b
  try {
    const pick_id = await pickOf(f)
    b = await restarted(f, storage)
    assert.deepEqual(await rate(b, { pick_id, ...cardInput }, "ses_other"), { state: "ignored", reason: "unknown_pick" })
    await assert.rejects(readFile(join(f.root, "feedback-stdin")), { code: "ENOENT" })
  } finally { await b?.cleanup?.(); await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a stored record of another shape is unknown_pick, not a card", async () => {
  const storage = memoryStorage()
  const f = await pickFixture(undefined, { storage })
  let b
  try {
    storage.entries.set("pick/0123456789abcdef", JSON.stringify({ v: 1, s: sessionHash("ses_test"), t: Date.now() }))
    b = await restarted(f, storage)
    assert.deepEqual(await rate(b, { pick_id: "0123456789abcdef", ...cardInput }), { state: "ignored", reason: "unknown_pick" })
  } finally { await b?.cleanup?.(); await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a stored pick older than 48 hours is ignored as expired and its entry is removed", async () => {
  const storage = memoryStorage()
  const f = await pickFixture(undefined, { storage })
  let b
  try {
    const pick_id = await pickOf(f)
    b = await restarted(f, storage)
    await flush() // the startup prune keeps this fresh entry; the expiry is then seen at lookup time
    storage.entries.set(`pick/${pick_id}`, JSON.stringify(record({ t: Date.now() - WINDOW_MS - 1000 })))
    assert.deepEqual(await rate(b, { pick_id, ...cardInput }), { state: "ignored", reason: "expired" })
    await flush()
    assert.deepEqual(storedKeys(storage), [])
    await assert.rejects(readFile(join(f.root, "feedback-stdin")), { code: "ENOENT" })
  } finally { await b?.cleanup?.(); await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a pick remembered in this instance expires after 48 hours too", async (t) => {
  const f = await pickFixture()
  try {
    const pick_id = await pickOf(f)
    const now = Date.now()
    t.mock.method(Date, "now", () => now + WINDOW_MS + 1000)
    assert.deepEqual(await rate(f, { pick_id, ...cardInput }), { state: "ignored", reason: "expired" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("turning Smart Router off removes the stored picks too", async () => {
  const storage = memoryStorage()
  const f = await pickFixture(undefined, { storage })
  try {
    const pick_id = await pickOf(f)
    assert.equal(storedKeys(storage).length, 1)
    await preference(f.root, false)
    assert.deepEqual(await rate(f, { pick_id, ...cardInput }), { state: "ignored", reason: "router_off" })
    await flush()
    assert.deepEqual(storedKeys(storage), [])
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("startup prunes expired and malformed stored picks, then keeps only the newest MAX_STORED_PICKS (2000)", async () => {
  const storage = memoryStorage()
  const now = Date.now()
  storage.entries.set("pick/expired", JSON.stringify(record({ t: now - WINDOW_MS - 1000 })))
  storage.entries.set("pick/malformed", JSON.stringify({ v: 1, s: "x" }))
  for (let n = 0; n <= 2000; n += 1) storage.entries.set(`pick/fresh-${String(n).padStart(4, "0")}`, JSON.stringify(record({ t: now - n })))
  const f = await pickFixture(undefined, { storage })
  try {
    await flush()
    const keys = storedKeys(storage)
    assert.equal(keys.length, 2000)
    assert.ok(!keys.includes("pick/expired"))
    assert.ok(!keys.includes("pick/malformed"))
    assert.ok(!keys.includes("pick/fresh-2000"), "the oldest rateable pick goes first")
    assert.ok(keys.includes("pick/fresh-1999"))
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a storage without set leaves picks in memory only", async () => {
  const f = await pickFixture(undefined, { storage: { get: async () => { throw Error("unused") } } })
  try {
    const pick_id = await pickOf(f)
    assert.deepEqual(await rate(f, { pick_id, ...cardInput }), { state: "recorded" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("the select answer does not wait for a storage write that never settles", { timeout: 5000 }, async () => {
  const storage = { ...memoryStorage(), set: () => new Promise(() => {}) }
  const f = await pickFixture(undefined, { storage })
  try {
    const answer = JSON.parse((await f.execute()).content)
    assert.match(answer.pick_id, /^[0-9a-f]{16}$/)
    assert.deepEqual(await rate(f, { pick_id: answer.pick_id, ...cardInput }), { state: "recorded" })
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

// Storage whose named method waits until the release function returned by hold(name) is called.
function holdable(storage) {
  const gates = new Map()
  const arrivals = new Map()
  for (const name of ["get", "set", "remove", "scan"]) {
    const call = storage[name]
    storage[name] = async (...args) => {
      arrivals.set(name, (arrivals.get(name) ?? 0) + 1)
      await gates.get(name)
      return call(...args)
    }
  }
  storage.arrived = (name) => arrivals.get(name) ?? 0
  storage.hold = (name) => {
    let release
    gates.set(name, new Promise((resolve) => { release = resolve }))
    return release
  }
  return storage
}
// Counts calls to storage.scan.
function countScans(storage) {
  const counter = { count: 0 }
  const scan = storage.scan
  storage.scan = (...args) => { counter.count += 1; return scan(...args) }
  return counter
}

test("a write still pending when Smart Router turns off leaves no pick entry behind", async () => {
  const storage = holdable(memoryStorage())
  const f = await pickFixture(undefined, { storage })
  try {
    const release = storage.hold("set")
    await pickOf(f)
    await preference(f.root, false)
    assert.equal(JSON.parse((await f.execute()).content).state, "no_selection")
    release()
    await flush()
    assert.deepEqual(storedKeys(storage), [])
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a clear still pending when Smart Router turns back on does not delete a pick written after it", async () => {
  const storage = holdable(memoryStorage())
  const f = await pickFixture(undefined, { storage })
  try {
    const old = await pickOf(f)
    await flush()
    const release = storage.hold("scan")
    await preference(f.root, false)
    assert.equal(JSON.parse((await f.execute()).content).state, "no_selection")
    await preference(f.root, true)
    const fresh = await pickOf(f)
    release()
    await flush()
    assert.notEqual(fresh, old)
    assert.deepEqual(storedKeys(storage), [`pick/${fresh}`])
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("with Smart Router off, repeated context-hook steps scan storage at most once", async () => {
  const storage = memoryStorage()
  const f = await pickFixture(undefined, { storage })
  try {
    await pickOf(f)
    await flush()
    await preference(f.root, false)
    const scans = countScans(storage)
    for (let step = 0; step < 5; step += 1) await f.h.hooks.get("context")({ system: [] })
    await flush()
    assert.ok(scans.count <= 1, `storage scans: ${scans.count}`)
  } finally { await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})

test("a card after the plugin closed is ignored with router_off", async () => {
  const f = await pickFixture()
  try {
    const pick_id = await pickOf(f)
    await f.cleanup()
    assert.deepEqual(await rate(f, { pick_id, ...cardInput }), { state: "ignored", reason: "router_off" })
  } finally { await rm(f.root, { recursive: true }) }
})

test("two cards for one stored pick looked up together are both answered, neither as router_off", async () => {
  const storage = holdable(memoryStorage())
  const f = await pickFixture(undefined, { storage })
  let b
  try {
    const pick_id = await pickOf(f)
    await flush()
    b = await restarted(f, storage)
    await flush()
    const release = storage.hold("get")
    const cards = Promise.all([rate(b, { pick_id, ...cardInput }), rate(b, { pick_id, ...cardInput })])
    while (storage.arrived("get") < 2) await flush()
    release()
    const results = await cards
    assert.ok(results.every((result) => result.reason !== "router_off"), JSON.stringify(results))
    assert.ok(results.some((result) => result.state === "recorded"), JSON.stringify(results))
  } finally { await b?.cleanup?.(); await f.cleanup?.(); await rm(f.root, { recursive: true }) }
})
