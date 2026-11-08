// Run with `node --test tests/opencode_plugin/plugin.test.ts` (Node strips the types); tests/test_opencode_plugin.py wraps it.
import { test } from "node:test"
import assert from "node:assert/strict"
import { mkdtempSync, writeFileSync, readFileSync, existsSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, dirname } from "node:path"
import { fileURLToPath } from "node:url"
import plugin, {
  createRoutingPlugin, credentialEnvName, routeFacts, spawnModel, CONTRACT_VERSION,
} from "../../runtime/adapters/opencode_plugin.ts"

const HERE = dirname(fileURLToPath(import.meta.url))
const FAKE = join(HERE, "fake_routing.py")
const TIMEOUT = 200
const GRACE = 1500
const MANAGED = "ses_managed1"
const OTHER = "ses_interactive1"
const SECRET = "sk-ant-SECRET-VALUE"
const PROMPT = "PROMPT-TEXT-never-forwarded"
const HAIKU = { providerID: "claude-agent", id: "haiku", variant: "low" }

type Hooks = Record<string, (event: any) => Promise<void> | void>

function child(control: Record<string, unknown> = {}) {
  const dir = mkdtempSync(join(tmpdir(), "routing-plugin-"))
  const controlPath = join(dir, "control.json")
  const logPath = join(dir, "log.jsonl")
  const set = (value: Record<string, unknown>) => {
    writeFileSync(controlPath, JSON.stringify(value))
    rmSync(controlPath + ".counter", { force: true })
  }
  set(control)
  const records = () => existsSync(logPath)
    ? readFileSync(logPath, "utf8").trim().split("\n").filter(Boolean).map((line) => JSON.parse(line)) : []
  return {
    argv: ["python3", "-I", "-B", FAKE, "--serve", "--control", controlPath, "--log", logPath],
    set,
    requests: (op?: string) => records().filter((r) => r.request && (!op || r.request.op === op)).map((r) => r.request),
    spawns: () => records().filter((r) => r.spawn).length,
    cleanup: () => rmSync(dir, { recursive: true, force: true }),
  }
}

function fakeCtx(sessions: Record<string, { parentID?: string }> = {}, options: Record<string, unknown> = {}) {
  const hooks: Hooks = {}
  const lookups: string[] = []
  const state: { rpc?: any, handlers?: any, failLookup?: boolean } = {}
  const domain = (name: string) => ({
    hook: async (hook: string, callback: any) => { hooks[`${name}.${hook}`] = callback; return { dispose: async () => {} } },
  })
  const ctx = {
    options,
    location: { directory: "/tmp/project" },
    session: {
      ...domain("session"),
      get: async ({ sessionID }: { sessionID: string }) => {
        lookups.push(sessionID)
        if (state.failLookup) throw new Error("lookup failed")
        if (!(sessionID in sessions)) throw new Error("not found")
        return { id: sessionID, ...sessions[sessionID] }
      },
    },
    tool: domain("tool"),
    permission: domain("permission"),
    shell: domain("shell"),
    rpc: {
      register: async (definition: any, handlers: any) => {
        state.rpc = definition
        state.handlers = handlers
        return { dispose: async () => {} }
      },
    },
  }
  return { ctx, hooks, lookups, state }
}

async function start(control: Record<string, unknown> = {}, sessions = {}, config: Record<string, unknown> = {}) {
  const fake = child(control)
  const { ctx, hooks, lookups, state } = fakeCtx(sessions)
  const instance = createRoutingPlugin({ argv: fake.argv, timeoutMs: TIMEOUT, spawnGraceMs: GRACE, ...config })
  const cleanup = await instance.setup(ctx)
  const rpc = (method: string, input: unknown = {}) => state.handlers[method](input, { signal: new AbortController().signal })
  const attach = (session = MANAGED, extra: Record<string, unknown> = {}) =>
    rpc("attach", { session_id: session, attempt_id: "att-1", funding_pool: "claude-max", ...extra })
  const stop = () => { if (typeof cleanup === "function") cleanup(); fake.cleanup() }
  return { fake, hooks, lookups, state, rpc, attach, stop }
}

const modelRequest = (sessionID: string, kind = "primary", model: unknown = HAIKU) => ({
  sessionID, agent: "build", kind, model, baseURL: "https://api.example.com/v1?key=" + SECRET,
  headers: { authorization: "Bearer " + SECRET }, messages: [{ text: PROMPT }],
})
const toolCall = (sessionID: string, tool: string, input: Record<string, unknown>) =>
  ({ sessionID, messageID: "msg_1", id: "call_1", agent: "build", tool, input })
const spawnCall = (sessionID: string, model?: string) =>
  toolCall(sessionID, "subagent", { agent: "general", description: "d", prompt: PROMPT, ...(model ? { model } : {}) })

const blocked = (error: unknown) => {
  assert.ok(error instanceof Error)
  assert.match(error.message, /^routing: /)
  for (const text of [SECRET, PROMPT, "Bearer"]) assert.ok(!error.message.includes(text), error.message)
  return true
}

test("plugin is a V2 server plugin with an id and setup", () => {
  assert.equal(typeof plugin.id, "string")
  assert.equal(typeof plugin.setup, "function")
  assert.equal(CONTRACT_VERSION, 1)
})

test("model requests of an attached session pass on allow and forward only bounded facts", async () => {
  const t = await start()
  try {
    await t.attach()
    for (const kind of ["primary", "title", "compaction", "generate"]) await t.hooks["session.model.request"](modelRequest(MANAGED, kind))
    const checks = t.fake.requests("check")
    assert.equal(checks.length, 4)
    assert.deepEqual(checks.map((r) => r.request.kind), ["primary", "title", "compaction", "generate"])
    assert.deepEqual(checks[0].request, {
      provider: "claude-agent", model: "haiku", variant: "low", harness: "claude-agent-sdk via opencode",
      kind: "primary", funding_pool: "claude-max",
    })
    assert.equal(checks[0].attempt_id, "att-1")
    assert.equal(checks[0].v, 1)
    const line = JSON.stringify(checks)
    for (const text of [SECRET, PROMPT, "Bearer", "api.example.com"]) assert.ok(!line.includes(text))
    assert.equal(new Set(checks.map((r) => r.id)).size, 4)
  } finally { t.stop() }
})

for (const mode of ["blocked", "garbage", "wrong_id", "wrong_version", "no_state", "exit", "stall", "leak_reason", "unmanaged"]) {
  test(`attached session: every checked hook throws on ${mode}`, async () => {
    const t = await start({ check: [mode] })
    try {
      await t.attach()
      await assert.rejects(t.hooks["session.model.request"](modelRequest(MANAGED)), blocked)
      await assert.rejects(t.hooks["tool.execute.before"](spawnCall(MANAGED, "claude-agent/haiku#low")), blocked)
    } finally { t.stop() }
  })
}

test("unknown request kind and malformed model are blocked for attached sessions without a check", async () => {
  const t = await start()
  try {
    await t.attach()
    await assert.rejects(t.hooks["session.model.request"](modelRequest(MANAGED, "mystery")), blocked)
    await assert.rejects(t.hooks["session.model.request"](modelRequest(MANAGED, "primary", { providerID: "x".repeat(300), id: "a" })), blocked)
    await assert.rejects(t.hooks["session.model.request"](modelRequest(MANAGED, "primary", null)), blocked)
    assert.equal(t.fake.requests("check").length, 0)
  } finally { t.stop() }
})

test("interactive sessions pass without a child, even when the command is broken", async () => {
  const t = await start({ check: ["exit"], status: ["exit"] }, {}, { argv: ["python3", "-I", "-B", "/nonexistent/routing.py", "--serve"] })
  try {
    await t.hooks["session.model.request"](modelRequest(OTHER))
    await t.hooks["tool.execute.before"](spawnCall(OTHER, "other/model"))
    const event = { sessionID: OTHER, action: "webfetch", resources: ["https://x"], effect: "allow" }
    await t.hooks["permission.evaluate"](event)
    assert.equal(event.effect, "allow")
    assert.equal(t.fake.spawns(), 0)
    assert.deepEqual(t.lookups, [])
  } finally { t.stop() }
})

test("interactive sessions still pass while another session is attached and the child is dead", async () => {
  const t = await start({ check: ["exit"] }, { [OTHER]: {} })
  try {
    await t.attach()
    await t.hooks["session.model.request"](modelRequest(OTHER))
    await t.hooks["tool.execute.before"](spawnCall(OTHER))
    await assert.rejects(t.hooks["session.model.request"](modelRequest(MANAGED)), blocked)
    assert.equal(t.fake.requests("check").length, 2)  // the managed request and its single retry
  } finally { t.stop() }
})

test("a child session inherits its attached ancestor's attempt", async () => {
  const sessions = { ses_child1: { parentID: "ses_mid1" }, ses_mid1: { parentID: MANAGED }, [MANAGED]: {} }
  const t = await start({}, sessions)
  try {
    await t.attach()
    await t.hooks["session.model.request"](modelRequest("ses_child1"))
    const [check] = t.fake.requests("check")
    assert.equal(check.attempt_id, "att-1")
    t.fake.set({ check: ["blocked"] })
    await assert.rejects(t.hooks["session.model.request"](modelRequest("ses_child1")), blocked)
    await t.rpc("detach", { session_id: MANAGED })
    await t.hooks["session.model.request"](modelRequest("ses_child1"))  // detaching the root releases its children
  } finally { t.stop() }
})

test("a failed lineage lookup blocks only while a session is attached", async () => {
  const t = await start({}, {})
  try {
    t.state.failLookup = true
    await t.hooks["session.model.request"](modelRequest(OTHER))
    await t.attach()
    await assert.rejects(t.hooks["session.model.request"](modelRequest(OTHER)), blocked)
  } finally { t.stop() }
})

test("a stalled child is killed and respawned once; the retry answers", async () => {
  const t = await start({ check: ["stall", "allow"] })
  try {
    await t.attach()
    const began = performance.now()
    await t.hooks["session.model.request"](modelRequest(MANAGED))
    assert.ok(performance.now() - began < TIMEOUT + GRACE + 500)
    assert.equal(t.fake.spawns(), 2)
    const health = await t.rpc("health")
    assert.equal(health.state, "ok")
    assert.equal(typeof health.respawn_ms, "number")
  } finally { t.stop() }
})

test("a child that stalls again after the respawn makes the hook throw within the bound", async () => {
  const t = await start({ check: ["stall"] })
  try {
    await t.attach()
    const began = performance.now()
    await assert.rejects(t.hooks["session.model.request"](modelRequest(MANAGED)), blocked)
    assert.ok(performance.now() - began < TIMEOUT * 2 + GRACE * 2 + 500)
    assert.equal(t.fake.spawns(), 2)
  } finally { t.stop() }
})

test("health answers ok only when the child answers a ping", async () => {
  const t = await start({ status: ["allow", "exit", "allow"] })
  try {
    const first = await t.rpc("health")
    assert.equal(first.state, "ok")
    assert.equal(first.child, "up")
    assert.equal(first.version, 1)
    const second = await t.rpc("health")  // the child exits on this ping; one respawn answers
    assert.equal(second.child, "respawned")
    t.fake.set({ status: ["garbage"] })
    await assert.rejects(Promise.resolve().then(() => t.rpc("health")), blocked)
    t.fake.set({ status: ["blocked"] })
    await assert.rejects(Promise.resolve().then(() => t.rpc("health")), blocked)
    assert.ok(t.fake.requests("status").every((r) => Object.keys(r).sort().join() === "id,op,v"))
  } finally { t.stop() }
})

test("health fails when the routing command cannot start", async () => {
  const t = await start({}, {}, { argv: ["python3", "-I", "-B", "/nonexistent/routing.py", "--serve"] })
  try { await assert.rejects(Promise.resolve().then(() => t.rpc("health")), blocked) } finally { t.stop() }
})

test("attach validates its input and detach returns the session to interactive", async () => {
  const t = await start({ check: ["blocked"] })
  try {
    for (const bad of [{ session_id: "nope" }, { session_id: MANAGED, attempt_id: "" }, { session_id: MANAGED, attempt_id: "a".repeat(300) },
      { session_id: MANAGED, attempt_id: "a", funding_pool: "p", deny_actions: ["bad action!"] }, null]) {
      await assert.rejects(Promise.resolve().then(() => t.rpc("attach", bad)), blocked)
    }
    assert.deepEqual(await t.attach(), { state: "attached", session_id: MANAGED })
    await assert.rejects(t.hooks["session.model.request"](modelRequest(MANAGED)), blocked)
    assert.deepEqual(await t.rpc("detach", { session_id: MANAGED }), { state: "detached", session_id: MANAGED, was_attached: true })
    await t.hooks["session.model.request"](modelRequest(MANAGED))
  } finally { t.stop() }
})

test("subagent spawns are checked with the requested model; another route is blocked", async () => {
  const t = await start({ admitted: { provider: "claude-agent", model: "haiku", variant: "low" } })
  try {
    await t.attach()
    await t.hooks["tool.execute.before"](spawnCall(MANAGED, "claude-agent/haiku#low"))
    await assert.rejects(t.hooks["tool.execute.before"](spawnCall(MANAGED, "claude-agent/sonnet")), blocked)
    const checks = t.fake.requests("check")
    assert.deepEqual(checks.map((r) => [r.request.kind, r.request.model, r.request.variant]),
      [["subagent", "haiku", "low"], ["subagent", "sonnet", null]])
    await assert.rejects(t.hooks["tool.execute.before"](spawnCall(MANAGED)), blocked)  // no explicit model
    await assert.rejects(t.hooks["tool.execute.before"](spawnCall(MANAGED, "not a model")), blocked)
    assert.equal(t.fake.requests("check").length, 2)
  } finally { t.stop() }
})

test("other tools never call check", async () => {
  const t = await start({ check: ["blocked"] })
  try {
    await t.attach()
    for (const tool of ["read", "shell", "execute", "webfetch", "mkd_touch_marker"]) {
      await t.hooks["tool.execute.before"](toolCall(MANAGED, tool, { command: "curl " + SECRET }))
    }
    assert.equal(t.fake.requests().length, 0)
  } finally { t.stop() }
})

test("webfetch and the profile's denied actions are tightened to deny for managed sessions only", async () => {
  const t = await start({}, { [OTHER]: {} })
  try {
    await t.attach(MANAGED, { deny_actions: ["execute"] })
    const evaluate = (sessionID: string, action: string, effect = "allow") => {
      const event = { sessionID, action, resources: ["https://example.com/?token=" + SECRET], effect } as any
      return Promise.resolve(t.hooks["permission.evaluate"](event)).then(() => event)
    }
    const fetched = await evaluate(MANAGED, "webfetch", "ask")
    assert.equal(fetched.effect, "deny")
    assert.ok(!String(fetched.message).includes(SECRET))
    assert.equal((await evaluate(MANAGED, "execute")).effect, "deny")
    assert.equal((await evaluate(MANAGED, "read")).effect, "allow")
    assert.equal((await evaluate(OTHER, "webfetch")).effect, "allow")
    assert.equal((await evaluate("ses_unknown1", "read")).effect, "deny")  // lineage unknown while attached: fail closed
    assert.equal(t.fake.requests().length, 0)
  } finally { t.stop() }
})

test("shell credentials are stripped only while this instance has a managed session", async () => {
  const t = await start()
  try {
    const env = () => ({ PATH: "/bin", HOME: "/h", GITHUB_TOKEN: SECRET, ANTHROPIC_API_KEY: SECRET, AWS_SECRET_ACCESS_KEY: SECRET,
      "npm_config_//registry/:_authToken": SECRET, MY_PASSWORD: SECRET, LANG: "C" })
    const before = { command: "echo", cwd: "/tmp", timeout: 1, shell: "/bin/sh", env: env() }
    await t.hooks["shell.create.before"](before)
    assert.deepEqual(before.env, env())
    await t.attach()
    const during = { command: "echo", cwd: "/tmp", timeout: 1, shell: "/bin/sh", env: env() }
    await t.hooks["shell.create.before"](during)
    assert.deepEqual(Object.keys(during.env).sort(), ["HOME", "LANG", "PATH"])
    assert.equal(t.fake.requests().length, 0)
  } finally { t.stop() }
})

test("concurrent hooks multiplex over one child by request id", async () => {
  const t = await start({ threads: true, check: ["shuffle"], admitted: { provider: "claude-agent", model: "haiku", variant: "low" } })
  try {
    await t.attach()
    const calls = Array.from({ length: 24 }, (_, index) => index % 3 === 0
      ? t.hooks["tool.execute.before"](spawnCall(MANAGED, "claude-agent/sonnet")).then(() => "allow", () => "blocked")
      : t.hooks["session.model.request"](modelRequest(MANAGED)).then(() => "allow", () => "blocked"))
    const results = await Promise.all(calls)
    assert.deepEqual(results, results.map((_, index) => index % 3 === 0 ? "blocked" : "allow"))
    assert.equal(t.fake.spawns(), 1)
    assert.equal(new Set(t.fake.requests("check").map((r) => r.id)).size, 24)
  } finally { t.stop() }
})

test("the shim writes nothing to the console and bounds its requests", async () => {
  const seen: unknown[] = []
  const original = { log: console.log, error: console.error, warn: console.warn, info: console.info, debug: console.debug }
  for (const name of Object.keys(original) as (keyof typeof original)[]) console[name] = (...args: unknown[]) => { seen.push(args) }
  const t = await start({ check: ["leak_reason"] })
  try {
    await t.attach()
    await assert.rejects(t.hooks["session.model.request"](modelRequest(MANAGED)), blocked)
    await assert.rejects(t.hooks["session.model.request"](modelRequest(MANAGED, "primary", { providerID: "p", id: "m".repeat(5000) })), blocked)
    await assert.rejects(Promise.resolve().then(() => t.attach(MANAGED, { funding_pool: "p".repeat(5000) })), blocked)
    for (const request of t.fake.requests()) assert.ok(JSON.stringify(request).length < 2048)
  } finally {
    Object.assign(console, original)
    t.stop()
  }
  assert.deepEqual(seen, [])
})

test("helper extraction matches the documented adapter shapes", () => {
  assert.deepEqual(routeFacts(HAIKU), { provider: "claude-agent", model: "haiku", variant: "low", harness: "claude-agent-sdk via opencode" })
  assert.deepEqual(routeFacts({ providerID: "openrouter", id: "x/y" }), null)
  assert.deepEqual(routeFacts({ providerID: "openrouter", id: "glm-5" })?.harness, "opencode provider openrouter")
  assert.deepEqual(spawnModel("claude-agent/haiku#low"), { provider: "claude-agent", model: "haiku", variant: "low" })
  assert.equal(spawnModel("haiku"), null)
  for (const name of ["GITHUB_TOKEN", "OPENAI_API_KEY", "APIKEY", "SSH_AUTH_SOCK", "DB_PASSWORD"]) assert.ok(credentialEnvName(name), name)
  for (const name of ["PATH", "HOME", "KEYBOARD", "TOKENIZER_PATH", "MONKEY"]) assert.ok(!credentialEnvName(name), name)
})

test("TS extraction agrees with the Python adapter on the parity cases", { skip: !process.env.OPENCODE_PLUGIN_PARITY }, () => {
  const cases = JSON.parse(readFileSync(process.env.OPENCODE_PLUGIN_PARITY!, "utf8"))
  for (const item of cases.env) assert.equal(credentialEnvName(item.name), item.credential, item.name)
  for (const item of cases.models) assert.deepEqual(routeFacts(item.model), item.facts, JSON.stringify(item.model))
  for (const item of cases.spawns) assert.deepEqual(spawnModel(item.model), item.requested, item.model)
})
