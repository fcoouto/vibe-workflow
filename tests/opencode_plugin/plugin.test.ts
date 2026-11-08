// Run with `node --test tests/opencode_plugin/plugin.test.ts` (Node strips the types); tests/test_opencode_plugin.py wraps it.
import { test } from "node:test"
import assert from "node:assert/strict"
import { mkdtempSync, writeFileSync, readFileSync, existsSync, rmSync, mkdirSync, chmodSync } from "node:fs"
import { spawnSync } from "node:child_process"
import { tmpdir } from "node:os"
import { join, dirname } from "node:path"
import { fileURLToPath } from "node:url"
import plugin, {
  createRoutingPlugin, credentialEnvName, derivedSession, routeFacts, spawnModel, CONTRACT_VERSION,
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
// Lowercase tokens also match the reason pattern, so a child that echoes one exercises the shim's scrubbing.
const TOKEN = "zz_token_value_" + "q".repeat(28)
const TOKEN_B = "zz_other_token_" + "r".repeat(28)
const TOKENS: Record<string, string> = { "att-1": TOKEN, "att-b": TOKEN_B }
const DERIVED = "ses_" + "0123456789abcdef".repeat(2)
const DERIVED_B = "ses_" + "fedcba9876543210".repeat(2)

type Hooks = Record<string, (event: any) => Promise<void> | void>

function child(control: Record<string, unknown> = {}) {
  const dir = mkdtempSync(join(tmpdir(), "routing-plugin-"))
  const controlPath = join(dir, "control.json")
  const logPath = join(dir, "log.jsonl")
  let current: Record<string, unknown> = {}
  const set = (value: Record<string, unknown>) => {
    current = value
    writeFileSync(controlPath, JSON.stringify({ tokens: TOKENS, ...value }))
    rmSync(controlPath + ".counter", { force: true })
  }
  set(control)
  const records = () => existsSync(logPath)
    ? readFileSync(logPath, "utf8").trim().split("\n").filter(Boolean).map((line) => JSON.parse(line)) : []
  const hidden = new Set<string>()  // ids of the checks attach sends to verify a token
  return {
    argv: ["python3", "-I", "-B", FAKE, "--serve", "--control", controlPath, "--log", logPath],
    set,
    control: () => current,
    hide: (ids: string[]) => { for (const id of ids) hidden.add(id) },
    /** Requests the shim sent, without the verification checks of the harness's own attach calls. */
    requests: (op?: string) => records().filter((r) => r.request && !hidden.has(r.request.id) && (!op || r.request.op === op))
      .map((r) => r.request),
    all: () => records().filter((r) => r.request).map((r) => r.request),
    spawns: () => records().filter((r) => r.spawn).length,
    cleanup: () => rmSync(dir, { recursive: true, force: true }),
  }
}

function fakeCtx(sessions: Record<string, { parentID?: string }> = {}, options: Record<string, unknown> = {}) {
  const hooks: Hooks = {}
  const lookups: string[] = []
  const state: { rpc?: any, handlers?: any, failLookup?: boolean, hangLookup?: boolean, errorLookup?: unknown,
    subscribed?: string[] } = {}
  const listeners: ((event: unknown) => void)[] = []
  // A live-only event stream like ctx.event.subscribe: an async iterable that ends when the signal aborts.
  const subscribe = ({ types, signal }: { types?: string[], signal?: AbortSignal } = {}) => {
    state.subscribed = types
    const queue: unknown[] = []
    let wake: (() => void) | null = null
    listeners.push((event) => { queue.push(event); wake?.() })
    return {
      [Symbol.asyncIterator]() {
        return {
          async next() {
            while (!queue.length) {
              if (signal?.aborted) return { done: true, value: undefined }
              await new Promise<void>((resolve) => { wake = resolve; signal?.addEventListener("abort", () => resolve(), { once: true }) })
            }
            return { done: false, value: queue.shift() }
          },
          async return() { return { done: true, value: undefined } },
        }
      },
    }
  }
  const emit = async (event: unknown) => {
    for (const listener of listeners) listener(event)
    await new Promise((resolve) => setImmediate(resolve))
  }
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
        if (state.hangLookup) await new Promise(() => {})
        if (state.failLookup) throw new Error("lookup failed")
        if (state.errorLookup !== undefined) return state.errorLookup  // an error object instead of a throw
        if (!(sessionID in sessions)) throw new Error("not found")
        return { id: sessionID, ...sessions[sessionID] }
      },
    },
    tool: domain("tool"),
    permission: domain("permission"),
    shell: domain("shell"),
    event: { subscribe },
    rpc: {
      register: async (definition: any, handlers: any) => {
        state.rpc = definition
        state.handlers = handlers
        return { dispose: async () => {} }
      },
    },
  }
  return { ctx, hooks, lookups, state, emit }
}

const created = (sessionID: string, parentID?: string) =>
  ({ id: "evt_1", type: "session.created", data: { sessionID, agent: "build", ...(parentID ? { parentID } : {}) } })

async function start(control: Record<string, unknown> = {}, sessions = {}, config: Record<string, unknown> = {}) {
  const fake = child(control)
  const { ctx, hooks, lookups, state, emit } = fakeCtx(sessions)
  const instance = createRoutingPlugin({ argv: fake.argv, timeoutMs: TIMEOUT, spawnGraceMs: GRACE, ...config })
  const cleanup = await instance.setup(ctx)
  const rpc = (method: string, input: unknown = {}) => state.handlers[method](input, { signal: new AbortController().signal })
  // The launcher's attach: the child is told to answer the verification normally, then the test's modes come back.
  const attach = async (session = MANAGED, extra: Record<string, unknown> = {}) => {
    const attempt = (extra.attempt_id ?? "att-1") as string
    const saved = fake.control()
    const before = new Set(fake.all().map((r) => r.id))
    fake.set({ ...saved, check: ["allow"], probe: ["allow"] })
    try {
      return await rpc("attach", { session_id: session, attempt_id: attempt, authority: TOKENS[attempt],
        funding_pool: "claude-max", model: "claude-agent/haiku#low", ...extra })
    } finally {
      fake.hide(fake.all().filter((r) => !before.has(r.id)).map((r) => r.id))
      fake.set(saved)
    }
  }
  const detach = (session = MANAGED, attempt = "att-1", authority: unknown = TOKENS[attempt]) =>
    rpc("detach", { session_id: session, attempt_id: attempt, authority })
  const stop = () => { if (typeof cleanup === "function") cleanup(); fake.cleanup() }
  return { fake, hooks, lookups, state, rpc, attach, detach, stop, emit, ctx }
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
    assert.deepEqual(Object.keys(checks[0]).sort(), ["attempt_id", "authority", "id", "op", "request", "session_id", "v"])
    assert.ok(checks.every((r) => r.session_id === MANAGED && r.authority === TOKEN))
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
    await t.hooks["tool.execute.before"](spawnCall("ses_child1", "claude-agent/haiku#low"))
    // A child sends its own session id with its root's attempt and token.
    for (const check of t.fake.requests("check")) {
      assert.deepEqual([check.attempt_id, check.session_id, check.authority], ["att-1", "ses_child1", TOKEN])
    }
    t.fake.set({ check: ["blocked"] })
    await assert.rejects(t.hooks["session.model.request"](modelRequest("ses_child1")), blocked)
    await t.detach()
    await t.hooks["session.model.request"](modelRequest("ses_child1"))  // detaching the root releases its children
  } finally { t.stop() }
})

test("an interactive root passes while a session is attached even when its lookup throws, hangs or cycles", async () => {
  const t = await start({ check: ["blocked"] }, { ses_loopa: { parentID: "ses_loopb" }, ses_loopb: { parentID: "ses_loopa" } })
  try {
    await t.attach()
    t.state.failLookup = true
    await t.hooks["session.model.request"](modelRequest(OTHER))
    await t.hooks["tool.execute.before"](spawnCall(OTHER, "other/model"))
    t.state.failLookup = false
    t.state.hangLookup = true
    const began = performance.now()
    await t.hooks["session.model.request"](modelRequest("ses_hang1"))
    assert.ok(performance.now() - began < 1000)
    t.state.hangLookup = false
    await t.hooks["session.model.request"](modelRequest("ses_loopa"))  // cycle without an attached session
    assert.equal(t.fake.requests("check").length, 0)
  } finally { t.stop() }
})

test("a missing or invalid session ID is interactive", async () => {
  const t = await start({ check: ["blocked"] })
  try {
    await t.attach()
    for (const sessionID of [undefined, "", "not-a-session", 42]) {
      await t.hooks["session.model.request"](modelRequest(sessionID as any))
      await t.hooks["tool.execute.before"](spawnCall(sessionID as any, "other/model"))
      const event = { sessionID, action: "webfetch", resources: [], effect: "allow" } as any
      await t.hooks["permission.evaluate"](event)
      assert.equal(event.effect, "allow")
      assert.equal(event.message, undefined)
    }
    assert.deepEqual(t.lookups, [])
    assert.equal(t.fake.requests("check").length, 0)
  } finally { t.stop() }
})

test("session.created events answer lineage without lookups", async () => {
  const t = await start({ check: ["blocked"] })
  try {
    assert.deepEqual(t.state.subscribed, ["session.created"])
    await t.emit(created("ses_root1"))
    await t.attach()
    await t.emit(created("ses_kid1", MANAGED))
    await t.emit(created("ses_grandkid1", "ses_kid1"))
    await t.hooks["session.model.request"](modelRequest("ses_root1"))  // known root: interactive
    await assert.rejects(t.hooks["session.model.request"](modelRequest("ses_grandkid1")), blocked)
    assert.deepEqual(t.lookups, [])
    await t.detach()
    await t.attach("ses_other9")
    await t.hooks["session.model.request"](modelRequest("ses_grandkid1"))  // its root was detached
    assert.deepEqual(t.lookups, [])
  } finally { t.stop() }
})

// Every hook an interactive session reaches; none may block, deny or throw.
async function assertUntouched(t: Awaited<ReturnType<typeof start>>, sessionID: string) {
  await t.hooks["session.model.request"](modelRequest(sessionID))
  await t.hooks["tool.execute.before"](spawnCall(sessionID, "other/model"))
  await t.hooks["tool.execute.before"](spawnCall(sessionID))
  for (const action of ["webfetch", "read", "execute"]) {
    const event = { sessionID, action, resources: [], effect: "ask" } as any
    await t.hooks["permission.evaluate"](event)
    assert.equal(event.effect, "ask")
    assert.equal(event.message, undefined)
  }
}

const flood = async (t: Awaited<ReturnType<typeof start>>, count: number) => {
  for (let index = 0; index < count; index++) await t.emit(created(`ses_flood${index}`))
}

test("probe (i): an interactive subagent of a pre-existing interactive parent is untouched when lookups fail", async () => {
  const t = await start({ check: ["blocked"] })
  try {
    await t.attach(MANAGED, { deny_actions: ["read", "execute"] })
    await t.emit(created("ses_isub1", "ses_preexisting1"))  // the parent was created before the plugin loaded
    t.state.failLookup = true
    await assertUntouched(t, "ses_isub1")
    t.state.failLookup = false
    t.state.hangLookup = true
    await assertUntouched(t, "ses_isub1")
    assert.equal(t.fake.requests("check").length, 0)
  } finally { t.stop() }
})

test("probe (ii): an interactive grandchild stays interactive after its root is flooded out of the cache", async () => {
  const t = await start({ check: ["blocked"] })
  try {
    await t.attach()
    await t.emit(created("ses_iroot1"))
    await flood(t, 4100)  // evicts the interactive root's record
    await t.emit(created("ses_ikid1", "ses_iroot1"))
    await t.emit(created("ses_igrand1", "ses_ikid1"))
    t.state.failLookup = true
    await assertUntouched(t, "ses_igrand1")
    assert.equal(t.fake.requests("check").length, 0)
  } finally { t.stop() }
})

test("probe (iii): failed lookups are remembered briefly, so repeated requests neither block nor wait", async () => {
  const t = await start({ check: ["blocked"] })
  try {
    await t.attach()
    t.state.hangLookup = true
    await t.hooks["session.model.request"](modelRequest("ses_slow1"))
    const lookups = t.lookups.length
    const began = performance.now()
    for (let index = 0; index < 5; index++) await assertUntouched(t, "ses_slow1")
    assert.ok(performance.now() - began < 200, "a remembered failure must not wait again")
    assert.equal(t.lookups.length, lookups)
    assert.equal(t.fake.requests("check").length, 0)
  } finally { t.stop() }
})

test("a session created while something is attached, with an unknown parent, is interactive", async () => {
  const t = await start({ check: ["blocked"] })
  try {
    await t.attach()
    await t.emit(created("ses_late1", "ses_lost2"))
    t.state.failLookup = true
    await assertUntouched(t, "ses_late1")
  } finally { t.stop() }
})

test("managed descendants recorded from events survive a cache flood, at any depth, without lookups", async () => {
  const t = await start({ check: ["blocked"] })
  try {
    await t.attach()
    await t.emit(created("ses_mkid1", MANAGED))
    await t.emit(created("ses_mgrand1", "ses_mkid1"))
    await t.emit(created("ses_mgreat1", "ses_mgrand1"))
    await flood(t, 4100)
    t.state.failLookup = true
    for (const session of ["ses_mkid1", "ses_mgrand1", "ses_mgreat1"]) {
      await assert.rejects(t.hooks["session.model.request"](modelRequest(session)), blocked)
      const event = { sessionID: session, action: "webfetch", resources: [], effect: "allow" } as any
      await t.hooks["permission.evaluate"](event)
      assert.equal(event.effect, "deny")
    }
    assert.deepEqual(t.lookups, [])
  } finally { t.stop() }
})

test("a parent ID naming an attached session makes the new session managed (fails closed)", async () => {
  const t = await start({ admitted: { provider: "claude-agent", model: "haiku", variant: "low" } })
  try {
    await t.attach()
    await t.emit(created("ses_spoof1", MANAGED))  // e.g. created through the API with that parentID
    await t.hooks["session.model.request"](modelRequest("ses_spoof1"))
    assert.equal(t.fake.requests("check").at(-1).attempt_id, "att-1")
    await assert.rejects(t.hooks["session.model.request"](modelRequest("ses_spoof1", "primary", { ...HAIKU, id: "sonnet" })), blocked)
  } finally { t.stop() }
})

test("detaching the root releases its recorded descendants", async () => {
  const t = await start({ check: ["blocked"] })
  try {
    await t.attach()
    await t.emit(created("ses_mkid1", MANAGED))
    await t.emit(created("ses_mgrand1", "ses_mkid1"))
    await t.detach()
    await t.attach("ses_other9")
    t.state.failLookup = true
    await assertUntouched(t, "ses_mgrand1")
  } finally { t.stop() }
})

test("a root over its own descendant limit blocks only its own tree", async () => {
  const t = await start({})
  try {
    await t.attach()
    for (let index = 0; index < 256; index++) await t.emit(created(`ses_many${index}`, MANAGED))
    await t.emit(created("ses_iroot1"))
    await t.emit(created("ses_ikid1", "ses_iroot1"))
    await assert.rejects(t.hooks["tool.execute.before"](spawnCall(MANAGED, "claude-agent/haiku#low")),
      (error: Error) => blocked(error) && /descendant_limit/.test(error.message))
    await t.hooks["session.model.request"](modelRequest(MANAGED))  // reaching the limit alone does not stop the root
    await t.emit(created("ses_many256", MANAGED))  // one more managed descendant despite the limit
    for (const session of [MANAGED, "ses_many3", "ses_many256"]) {
      await assert.rejects(t.hooks["session.model.request"](modelRequest(session)),
        (error: Error) => blocked(error) && /descendant_limit/.test(error.message))
    }
    t.state.failLookup = true
    await assertUntouched(t, "ses_ikid1")
    await assertUntouched(t, "ses_iroot1")
  } finally { t.stop() }
})

test("one root's descendants never limit another root, and detaching clears its state", async () => {
  const B = "ses_rootb1"
  const t = await start({})
  try {
    await t.attach()
    await t.attach(B, { attempt_id: "att-b" })
    await t.emit(created("ses_bkid1", B))
    for (let index = 0; index < 1023; index++) await t.emit(created(`ses_akid${index}`, MANAGED))
    await assert.rejects(t.hooks["session.model.request"](modelRequest(MANAGED)), blocked)  // A is over its own limit
    await t.hooks["tool.execute.before"](spawnCall(B, "claude-agent/haiku#low"))
    await t.emit(created("ses_bkid2", B))
    for (const session of [B, "ses_bkid1", "ses_bkid2"]) await t.hooks["session.model.request"](modelRequest(session))
    await t.detach()
    t.state.failLookup = true
    await assertUntouched(t, "ses_akid5")
    t.state.failLookup = false
    await t.attach()
    await t.hooks["session.model.request"](modelRequest(MANAGED))
    await t.hooks["tool.execute.before"](spawnCall(MANAGED, "claude-agent/haiku#low"))
    await t.hooks["session.model.request"](modelRequest("ses_bkid2"))
  } finally { t.stop() }
})

test("the instance-wide ceiling blocks only the new descendants of the root creating them", async () => {
  const t = await start({})
  try {
    for (let root = 0; root < 16; root++) {
      await t.attach(`ses_full${root}`)
      for (let index = 0; index < 256; index++) await t.emit(created(`ses_full${root}x${index}`, `ses_full${root}`))
    }
    await t.attach("ses_rootc1")
    await assert.rejects(t.hooks["tool.execute.before"](spawnCall("ses_rootc1", "claude-agent/haiku#low")),
      (error: Error) => blocked(error) && /descendant_limit/.test(error.message))
    await t.emit(created("ses_ckid1", "ses_rootc1"))
    await assert.rejects(t.hooks["session.model.request"](modelRequest("ses_ckid1")),
      (error: Error) => blocked(error) && /descendant_limit/.test(error.message))
    await t.hooks["session.model.request"](modelRequest("ses_rootc1"))
    await t.hooks["session.model.request"](modelRequest("ses_full3x7"))
    await t.emit(created("ses_iroot1"))
    await assertUntouched(t, "ses_iroot1")
  } finally { t.stop() }
})

test("a lookup that returns an error object is a failed lookup, not a root", async () => {
  const t = await start({ check: ["blocked"] }, { ses_missed1: { parentID: MANAGED } }, { failedLookupMs: 100 })
  try {
    await t.attach()
    t.state.errorLookup = { name: "NotFoundError", data: { message: "not found" } }
    await assertUntouched(t, "ses_missed1")  // undeterminable for now: interactive
    t.state.errorLookup = { id: "ses_someoneelse1" }  // an answer about another session is not an answer
    await new Promise((resolve) => setTimeout(resolve, 150))
    await assertUntouched(t, "ses_missed1")
    t.state.errorLookup = undefined  // lookups are honest again
    await new Promise((resolve) => setTimeout(resolve, 150))
    await assert.rejects(t.hooks["session.model.request"](modelRequest("ses_missed1")), blocked)
  } finally { t.stop() }
})

test("a long or cyclic recorded chain is managed only when it reaches an attached session", async () => {
  const t = await start({ check: ["blocked"] })
  try {
    await t.attach()
    let parent = MANAGED
    for (let index = 0; index < 12; index++) {
      await t.emit(created(`ses_deep${index}`, parent))
      parent = `ses_deep${index}`
    }
    await assert.rejects(t.hooks["session.model.request"](modelRequest("ses_deep11")), blocked)
    await t.emit(created("ses_cyca", "ses_cycb"))
    await t.emit(created("ses_cycb", "ses_cyca"))
    await t.hooks["session.model.request"](modelRequest("ses_cyca"))
    assert.deepEqual(t.lookups, [])
  } finally { t.stop() }
})

test("the attach limit counts attached roots, not their children", async () => {
  const sessions: Record<string, { parentID?: string }> = {}
  for (let index = 0; index < 250; index++) sessions[`ses_kid${index}`] = { parentID: "ses_root0" }  // within one root's budget
  const t = await start({}, sessions)
  try {
    await t.attach("ses_root0")
    for (let index = 0; index < 250; index++) await t.hooks["session.model.request"](modelRequest(`ses_kid${index}`))
    for (let index = 1; index < 256; index++) await t.attach(`ses_root${index}`)
    await t.attach("ses_root5")  // re-attaching an attached root is allowed at the limit
    await assert.rejects(Promise.resolve().then(() => t.attach("ses_root999")), blocked)
    await t.detach("ses_root1")
    await t.attach("ses_root999")
  } finally { t.stop() }
})

const refused = (reason: string | RegExp) => (error: unknown) => blocked(error)
  && (typeof reason === "string" ? (error as Error).message === `routing: blocked (${reason})` : reason.test((error as Error).message))
const attachInput = (extra: Record<string, unknown> = {}) => ({ session_id: MANAGED, attempt_id: "att-1", authority: TOKEN,
  funding_pool: "claude-max", model: "claude-agent/haiku#low", ...extra })

test("attach verifies the authority with a strict check through the child before recording anything", async () => {
  const t = await start()
  try {
    assert.deepEqual(await t.rpc("attach", attachInput()), { state: "attached", session_id: MANAGED })
    const [verify] = t.fake.all().filter((r) => r.op === "check")
    assert.deepEqual(verify, { v: 1, op: "check", id: verify.id, attempt_id: "att-1", session_id: MANAGED, authority: TOKEN,
      request: { provider: "claude-agent", model: "haiku", variant: "low", harness: "claude-agent-sdk via opencode",
        kind: "primary", funding_pool: "claude-max" } })
  } finally { t.stop() }
})

test("an RPC caller without the session's token can neither attach nor detach", async () => {
  const t = await start({ check: ["blocked"] })
  const managedNow = async (session: string) => {
    const before = t.fake.requests("check").length
    await t.hooks["session.model.request"](modelRequest(session)).then(() => false, () => true)
    return t.fake.requests("check").length > before
  }
  try {
    t.fake.set({})
    // A wrong token, another attempt's token, and a token for no attempt are all refused, and nothing is recorded.
    for (const extra of [{ authority: "x".repeat(43) }, { authority: TOKEN_B }, { attempt_id: "att-b", authority: TOKEN },
      { attempt_id: "att-none", authority: TOKEN }]) {
      await assert.rejects(Promise.resolve().then(() => t.rpc("attach", attachInput(extra))), refused(/authority_invalid|attach_refused/))
    }
    for (const mode of ["blocked", "unmanaged", "allow_unverified", "allow_other_attempt", "garbage", "exit", "no_state", "echo_authority"]) {
      t.fake.set({ check: [mode] })
      await assert.rejects(Promise.resolve().then(() => t.rpc("attach", attachInput())), blocked, mode)
    }
    await new Promise((resolve) => setTimeout(resolve, 1100))  // the failing modes left the child in its back-off
    t.fake.set({ check: ["blocked"] })
    assert.equal(await managedNow(MANAGED), false)  // still interactive: no check was sent for its request
    // Missing or malformed fields never reach the child.
    const sent = t.fake.all().length
    for (const bad of [null, [], "x", {}, attachInput({ authority: undefined }), attachInput({ authority: "" }),
      attachInput({ authority: 42 }), attachInput({ authority: "has space " + TOKEN }), attachInput({ authority: "a".repeat(129) }),
      attachInput({ model: undefined }), attachInput({ model: "haiku" }), attachInput({ model: "a/b#c#d" }),
      attachInput({ attempt_id: undefined }), attachInput({ session_id: "nope" }), attachInput({ funding_pool: "" }),
      attachInput({ deny_actions: ["bad action!"] }), attachInput({ deny_actions: "webfetch" }),
      JSON.parse(`{"__proto__": {"authority": "${TOKEN}"}, "session_id": "${MANAGED}", "attempt_id": "att-1"}`)]) {
      await assert.rejects(Promise.resolve().then(() => t.rpc("attach", bad)), refused("invalid_attach"))
    }
    assert.equal(t.fake.all().length, sent)

    await t.attach()
    assert.equal(await managedNow(MANAGED), true)
    for (const bad of [null, {}, { session_id: MANAGED }, { session_id: MANAGED, attempt_id: "att-1" },
      { session_id: MANAGED, attempt_id: "att-1", authority: 7 }, { session_id: "nope", attempt_id: "att-1", authority: TOKEN }]) {
      await assert.rejects(Promise.resolve().then(() => t.rpc("detach", bad)), refused("invalid_detach"))
    }
    for (const [attempt, authority] of [["att-1", TOKEN_B], ["att-b", TOKEN_B], ["att-1", TOKEN.slice(0, -1) + "x"],
      ["att-1", TOKEN + "x"], ["att-b", TOKEN]]) {
      await assert.rejects(Promise.resolve().then(() => t.detach(MANAGED, attempt, authority)), refused("detach_refused"))
    }
    assert.equal(await managedNow(MANAGED), true)
    assert.deepEqual(await t.detach(), { state: "detached", session_id: MANAGED })
    assert.equal(await managedNow(MANAGED), false)
    // Replaying the detach is refused, and so is detaching a session that was never attached.
    await assert.rejects(Promise.resolve().then(() => t.detach()), refused("detach_refused"))
    await assert.rejects(Promise.resolve().then(() => t.detach(OTHER)), refused("detach_refused"))
  } finally { t.stop() }
})

test("a managed descendant cannot be detached, and an attached session cannot be taken over by another attempt", async () => {
  const t = await start({ check: ["blocked"] })
  try {
    await t.attach()
    await t.emit(created("ses_kid1", MANAGED))
    await assert.rejects(Promise.resolve().then(() => t.detach("ses_kid1")), refused("detach_refused"))
    await assert.rejects(t.attach(MANAGED, { attempt_id: "att-b" }), refused("attach_conflict"))
    await assert.rejects(t.attach("ses_kid1", { attempt_id: "att-b" }), refused("attach_conflict"))
    await t.attach()  // the same attempt may attach again
    const [check] = await t.hooks["session.model.request"](modelRequest("ses_kid1")).then(() => [], () => t.fake.requests("check"))
    assert.equal(check.attempt_id, "att-1")
    assert.equal(check.authority, TOKEN)
  } finally { t.stop() }
})

test("a replayed attach after the attempt closed is refused", async () => {
  const t = await start()
  try {
    await t.attach()
    await t.detach()
    t.fake.set({ check: ["blocked"] })  // the ledger now refuses the attempt (closed)
    await assert.rejects(Promise.resolve().then(() => t.rpc("attach", attachInput())), blocked)
    await t.hooks["session.model.request"](modelRequest(MANAGED))  // nothing was recorded: interactive
    assert.equal(t.fake.requests("check").length, 1)  // only the refused attach's verification
  } finally { t.stop() }
})

const shellEnv = () => ({ PATH: "/bin", GITHUB_TOKEN: SECRET })

test("a derived session the ledger knows is managed from its creation, before any attach", async () => {
  const t = await start({ check: ["blocked"], known_sessions: [DERIVED] })
  try {
    await t.emit(created(DERIVED))
    await t.emit(created("ses_dkid1", DERIVED))
    for (const session of [DERIVED, "ses_dkid1"]) {
      await assert.rejects(t.hooks["session.model.request"](modelRequest(session)), refused("attach_required"))
      await assert.rejects(t.hooks["tool.execute.before"](spawnCall(session, "claude-agent/haiku#low")), refused("attach_required"))
      const event = { sessionID: session, action: "webfetch", resources: [], effect: "allow" } as any
      await t.hooks["permission.evaluate"](event)
      assert.equal(event.effect, "deny")
    }
    const shell = { env: shellEnv() }
    await t.hooks["shell.create.before"](shell)
    assert.deepEqual(Object.keys(shell.env), ["PATH"])
    const probes = t.fake.requests("check")
    assert.equal(probes.length, 1)  // one read-only probe, cached; the blocked requests sent no check
    assert.deepEqual(Object.keys(probes[0]).sort(), ["id", "op", "request", "session_id", "v"])
    assert.equal(probes[0].session_id, DERIVED)
    // The launcher's attach turns it into an ordinary attached root; its recorded child follows it.
    await t.attach(DERIVED)
    t.fake.set({ known_sessions: [DERIVED] })
    await t.hooks["session.model.request"](modelRequest(DERIVED))
    await t.hooks["session.model.request"](modelRequest("ses_dkid1"))
    assert.deepEqual(t.fake.requests("check").slice(-2).map((r) => [r.session_id, r.attempt_id, r.authority]),
      [[DERIVED, "att-1", TOKEN], ["ses_dkid1", "att-1", TOKEN]])
    // After detach the ledger still knows the derived id, so it is blocked again until attached.
    await t.detach(DERIVED)
    await assert.rejects(t.hooks["session.model.request"](modelRequest(DERIVED)), refused("attach_required"))
  } finally { t.stop() }
})

test("a derived session found only by a hook, or through its parent, is managed too", async () => {
  const t = await start({ check: ["blocked"], known_sessions: [DERIVED] }, { ses_late2: { parentID: DERIVED } })
  try {
    await assert.rejects(t.hooks["session.model.request"](modelRequest(DERIVED)), refused("attach_required"))  // no event seen
    await assert.rejects(t.hooks["session.model.request"](modelRequest("ses_late2")), refused("attach_required"))  // via lookup
  } finally { t.stop() }
})

test("an unknown or unanswerable derived session stays interactive, within the bound, and is asked once", async () => {
  const ids = ["a", "b", "c", "d", "e", "f"].map((c) => "ses_" + c.repeat(32))
  const t = await start({ check: ["blocked"] })
  try {
    for (const [index, mode] of ["allow", "blocked", "exit", "garbage", "wrong_version", "stall"].entries()) {
      t.fake.set({ check: ["blocked"], probe: [mode] })
      await t.emit(created(ids[index]))
      const began = performance.now()
      await assertUntouched(t, ids[index])
      await assertUntouched(t, ids[index])
      assert.ok(performance.now() - began < TIMEOUT + GRACE + 400, mode)
    }
    const probes = t.fake.requests("check")
    for (const id of ids.slice(0, 2)) assert.equal(probes.filter((r) => r.session_id === id).length, 1, id)
    assert.ok(probes.every((r) => !("attempt_id" in r) && !("authority" in r)))
  } finally { t.stop() }
})

test("native session ids are never probed, so interactive use starts no child", async () => {
  const t = await start({ check: ["exit"] }, {}, { argv: ["python3", "-I", "-B", "/nonexistent/routing.py", "--serve"] })
  try {
    for (const session of [OTHER, "ses_01JABCDEFGHJKMNPQRSTVWXYZ", "ses_" + "0".repeat(31), "ses_" + "A".repeat(32)]) {
      await t.emit(created(session))
      await assertUntouched(t, session)
    }
    assert.equal(t.fake.spawns(), 0)
    assert.deepEqual(t.lookups, [])
  } finally { t.stop() }
})

test("the token never leaves the shim except on the routing child's stdin", async () => {
  const WRONG = "zz_wrong_token_" + "w".repeat(28)
  const seen: string[] = []
  const text = (value: unknown) => { try { return typeof value === "string" ? value : JSON.stringify(value) ?? String(value) } catch { return String(value) } }
  const keep = (value: unknown) => { seen.push(text(value)) }
  const errorText = (error: any) => [error?.message, error?.stack, error?.name, String(error), text(error),
    text(Object.getOwnPropertyNames(error ?? {}).map((key) => error[key]))].join("\n")
  const settle = async (promise: () => unknown) => {
    try { keep(await promise()) } catch (error) { keep(errorText(error)) }
  }
  const original = { log: console.log, error: console.error, warn: console.warn, info: console.info, debug: console.debug }
  const writes = { out: process.stdout.write, err: process.stderr.write }
  for (const name of Object.keys(original) as (keyof typeof original)[]) console[name] = (...args: unknown[]) => keep(args)
  process.stdout.write = ((chunk: unknown, ...rest: unknown[]) => { keep(chunk); return (writes.out as any).call(process.stdout, chunk, ...rest) }) as any
  process.stderr.write = ((chunk: unknown, ...rest: unknown[]) => { keep(chunk); return (writes.err as any).call(process.stderr, chunk, ...rest) }) as any
  try {
    for (const mode of ["allow", "blocked", "echo_authority", "leak_reason", "garbage", "wrong_id", "wrong_version", "no_state",
      "exit", "unmanaged", "allow_unverified", "allow_other_attempt", "stall"]) {
      const t = await start({ known_sessions: [DERIVED] })
      try {
        await settle(() => t.attach())
        await t.emit(created("ses_kid1", MANAGED))
        await t.emit(created(DERIVED))
        t.fake.set({ check: [mode], probe: [mode], status: [mode], known_sessions: [DERIVED] })
        const sessions = mode === "stall" ? [MANAGED] : [MANAGED, "ses_kid1", DERIVED, OTHER]
        for (const session of sessions) {
          const request = modelRequest(session)
          await settle(() => t.hooks["session.model.request"](request))
          keep(request)
          const spawn = spawnCall(session, "claude-agent/haiku#low")
          await settle(() => t.hooks["tool.execute.before"](spawn))
          keep(spawn)
          const permission = { sessionID: session, action: "webfetch", resources: [], effect: "allow" }
          await settle(() => t.hooks["permission.evaluate"](permission))
          keep(permission)
        }
        const shell = { env: shellEnv() }
        await settle(() => t.hooks["shell.create.before"](shell))
        keep(shell)
        await settle(() => t.rpc("health"))
        await settle(() => t.rpc("attach", attachInput({ session_id: "ses_another1" })))
        await settle(() => t.rpc("attach", attachInput({ session_id: "ses_another2", authority: WRONG })))
        await settle(() => t.rpc("attach", attachInput({ authority: WRONG, model: "bad" })))
        await settle(() => t.rpc("attach", attachInput({ funding_pool: "p".repeat(5000) })))
        await settle(() => t.detach(MANAGED, "att-1", WRONG))
        await settle(() => t.detach(MANAGED, "att-b", TOKEN))
        await settle(() => t.rpc("detach", { session_id: MANAGED, attempt_id: "att-1", authority: TOKEN + " " }))
        await settle(() => t.detach())
        keep(t.lookups)
        keep(t.state.subscribed)
        keep(t.state.rpc)
      } finally { t.stop() }
    }
  } finally {
    Object.assign(console, original)
    process.stdout.write = writes.out
    process.stderr.write = writes.err
  }
  assert.ok(seen.length > 100)
  for (const [index, output] of seen.entries()) {
    for (const secret of [TOKEN, WRONG, TOKEN.slice(0, 20), WRONG.slice(0, 20)]) assert.ok(!output.includes(secret), `output ${index}: ${output.slice(0, 200)}`)
  }
})

/** mulberry32: a small seeded generator, so a failing sequence can be replayed from its seed. */
function seeded(seed: number) {
  return () => {
    seed = (seed + 0x6D2B79F5) | 0
    let value = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    value = (value + Math.imul(value ^ (value >>> 7), 61 | value)) ^ value
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296
  }
}

for (const seed of [1, 7, 42]) {
  test(`seeded lineage fuzz ${seed}: interactive sessions are never checked or blocked; managed and ledger-known ones always are`, async () => {
    const random = seeded(seed)
    const pick = <T>(items: T[]) => items[Math.floor(random() * items.length)]
    const known = ["1", "2", "3"].map((c) => "ses_" + c.repeat(32))
    const t = await start({ check: ["blocked"], known_sessions: known })
    const interactive: string[] = []
    const managed = new Map<string, string>()  // session -> attached root
    const pending: string[] = []  // ledger-known derived sessions and their recorded children, never attached here
    let next = 0
    try {
      for (let step = 0; step < 160; step++) {
        const roll = random()
        if (roll < 0.2) {
          const id = `ses_i${next++}`
          const parent = interactive.length && random() < 0.6 ? pick(interactive) : random() < 0.3 ? `ses_ghost${next}` : undefined
          await t.emit(created(id, parent))
          interactive.push(id)
        } else if (roll < 0.28 && new Set(managed.values()).size < 6) {
          const root = `ses_r${next++}`
          await t.emit(created(root))
          await t.attach(root)
          managed.set(root, root)
        } else if (roll < 0.42 && managed.size) {
          const parent = pick([...managed.keys()])
          const id = `ses_m${next++}`
          await t.emit(created(id, parent))
          managed.set(id, managed.get(parent)!)
        } else if (roll < 0.47 && managed.size) {
          const root = managed.get(pick([...managed.keys()]))!
          await t.detach(root)
          for (const [session, owner] of [...managed]) if (owner === root) { managed.delete(session); interactive.push(session) }
        } else if (roll < 0.53) {
          const fresh = known.filter((id) => !pending.includes(id))
          const id = fresh.length && random() < 0.5 ? fresh[0] : pending.length ? `ses_p${next++}` : fresh[0]
          if (id === undefined) continue
          await t.emit(created(id, id.startsWith("ses_p") ? pick(pending) : undefined))
          pending.push(id)
        } else if (roll < 0.58) {
          t.state.failLookup = !t.state.failLookup
        } else {
          const population = pick([interactive, [...managed.keys()], pending].filter((items) => items.length))
          if (!population) continue
          const session = pick(population)
          const before = t.fake.requests("check").filter((r) => r.session_id === session).length
          const began = performance.now()
          const outcome = await t.hooks["session.model.request"](modelRequest(session)).then(() => "pass", (error: Error) => error.message)
          const checks = t.fake.requests("check").filter((r) => r.session_id === session)
          if (population === interactive) {
            assert.equal(outcome, "pass", `seed ${seed} step ${step}: interactive ${session} was blocked`)
            await assertUntouched(t, session)
            assert.equal(checks.length, before, `seed ${seed} step ${step}: interactive ${session} was checked`)
            assert.ok(performance.now() - began < 250, `seed ${seed} step ${step}: interactive ${session} waited`)
          } else if (population === pending) {
            assert.equal(outcome, "routing: blocked (attach_required)", `seed ${seed} step ${step}: ${session}`)
          } else {
            assert.equal(outcome, "routing: blocked (route_mismatch)", `seed ${seed} step ${step}: ${session}`)
            assert.deepEqual([checks.length, checks.at(-1).attempt_id, checks.at(-1).authority], [before + 1, "att-1", TOKEN])
          }
        }
      }
      assert.ok(interactive.length && managed.size + pending.length > 0, `seed ${seed} exercised too little`)
    } finally { t.stop() }
  })
}

test("after two failed respawns the shim backs off for about a second, then tries again", async () => {
  const t = await start({ check: ["exit"] })
  try {
    await t.attach()
    await assert.rejects(t.hooks["session.model.request"](modelRequest(MANAGED)), blocked)
    await assert.rejects(t.hooks["session.model.request"](modelRequest(MANAGED)), blocked)
    const spawned = t.fake.spawns()
    const began = performance.now()
    await assert.rejects(t.hooks["session.model.request"](modelRequest(MANAGED)), blocked)
    assert.ok(performance.now() - began < 100)
    assert.equal(t.fake.spawns(), spawned)  // no spawn during the back-off
    await new Promise((resolve) => setTimeout(resolve, 1100))
    t.fake.set({ check: ["allow"] })
    await t.hooks["session.model.request"](modelRequest(MANAGED))
    assert.ok(t.fake.spawns() > spawned)
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
    t.fake.set({ status: ["ledger_absent"] })
    assert.deepEqual(await t.rpc("health"), { state: "not_ready", reason: "ledger_absent", version: 1 })
    t.fake.set({ status: ["settings_unavailable"] })
    assert.deepEqual(await t.rpc("health"), { state: "not_ready", reason: "settings_unavailable", version: 1 })
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
      { session_id: MANAGED, attempt_id: "a", funding_pool: "p", deny_actions: ["bad action!"] }, null,
      { session_id: MANAGED, attempt_id: "att-1", funding_pool: "claude-max" }]) {  // the S3.3 attempt-only form is gone
      await assert.rejects(Promise.resolve().then(() => t.rpc("attach", bad)), blocked)
    }
    assert.deepEqual(await t.attach(), { state: "attached", session_id: MANAGED })
    await assert.rejects(t.hooks["session.model.request"](modelRequest(MANAGED)), blocked)
    assert.deepEqual(await t.detach(), { state: "detached", session_id: MANAGED })
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
    assert.equal((await evaluate("ses_unknown1", "read")).effect, "allow")  // unresolvable, no evidence: interactive
    const earlier = { sessionID: MANAGED, action: "webfetch", resources: [], effect: "deny", message: "earlier reason" } as any
    await t.hooks["permission.evaluate"](earlier)
    assert.equal(earlier.message, "earlier reason")  // an existing denial keeps its message
    const interactive = { sessionID: OTHER, action: "read", resources: [], effect: "deny", message: "other plugin" } as any
    await t.hooks["permission.evaluate"](interactive)
    assert.equal(interactive.message, "other plugin")
    assert.equal(t.fake.requests().length, 0)
  } finally { t.stop() }
})

test("shell credentials are stripped only while this instance has a managed session", async () => {
  const t = await start()
  try {
    const env = () => ({ PATH: "/bin", HOME: "/h", GITHUB_TOKEN: SECRET, ANTHROPIC_API_KEY: SECRET, AWS_SECRET_ACCESS_KEY: SECRET,
      "npm_config_//registry/:_authToken": SECRET, MY_PASSWORD: SECRET, LANG: "C", SSH_AUTH_SOCK: "/s", DATABASE_URL: SECRET,
      SENTRY_DSN: SECRET, "x-api-key": SECRET, "my-app-config": "1", AWS_PROFILE: "p", KUBECONFIG: "/k" })
    const before = { command: "echo", cwd: "/tmp", timeout: 1, shell: "/bin/sh", env: env() }
    await t.hooks["shell.create.before"](before)
    assert.deepEqual(before.env, env())
    await t.attach()
    const during = { command: "echo", cwd: "/tmp", timeout: 1, shell: "/bin/sh", env: env() }
    await t.hooks["shell.create.before"](during)
    // Known limits: AWS_PROFILE and KUBECONFIG name credential files and are kept.
    assert.deepEqual(Object.keys(during.env).sort(), ["AWS_PROFILE", "HOME", "KUBECONFIG", "LANG", "PATH", "my-app-config"])
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

test("the live-recorded hook payloads drive the shim for an attached session", async () => {
  const live = JSON.parse(readFileSync(join(HERE, "../fixtures/smart-routing/probes/s3-3-shim-live.json"), "utf8"))
  const real = (value: unknown): any => JSON.parse(JSON.stringify(value).replace(/<(ses|msg|toolu)#(\d+)>/g, "$1_live$2"))
  const payload = (hook: string, tool?: string) => real(live.hooks.find((h: any) => h.hook === hook && (!tool || h.payload.tool === tool)).payload)
  const t = await start()
  try {
    await t.attach("ses_live1")
    await t.hooks["session.model.request"](payload("model.request"))
    await t.hooks["tool.execute.before"](payload("tool.execute.before", "subagent"))
    await t.hooks["tool.execute.before"](payload("tool.execute.before", "webfetch"))
    assert.deepEqual(t.fake.requests("check").map((r) => [r.request.kind, r.request.provider, r.request.model, r.request.variant]),
      [["title", "claude-agent", "haiku", "low"], ["subagent", "claude-agent", "haiku", "low"]])
    const permission = payload("permission.evaluate")
    await t.hooks["permission.evaluate"](permission)
    assert.equal(permission.effect, "deny")
  } finally { t.stop() }
})

test("helper extraction matches the documented adapter shapes", () => {
  assert.deepEqual(routeFacts(HAIKU), { provider: "claude-agent", model: "haiku", variant: "low", harness: "claude-agent-sdk via opencode" })
  assert.deepEqual(routeFacts({ providerID: "openrouter", id: "x/y" }), null)
  assert.deepEqual(routeFacts({ providerID: "openrouter", id: "glm-5" })?.harness, "opencode provider openrouter")
  assert.deepEqual(spawnModel("claude-agent/haiku#low"), { provider: "claude-agent", model: "haiku", variant: "low" })
  assert.equal(spawnModel("haiku"), null)
  for (const name of ["GITHUB_TOKEN", "OPENAI_API_KEY", "APIKEY", "SSH_AUTH_SOCK", "DB_PASSWORD", "DATABASE_URL", "SENTRY_DSN",
    "PG_CONNECTION_STRING", "PASS", "DB_PASS", "PASSPHRASE", "SSH_PASSPHRASE", "SECRETS", "APP_SECRETS", "JWT", "JWT_SIGNING",
    "PGPASSWORD", "MYSQL_PWD", "GITHUB_PAT", "PAT", "REDIS_URL", "MONGODB_URI", "MONGO_URL", "POSTGRES_URL", "githubToken",
    "dbPassword", "apiKey", "GITHUB_OAUTH", "OAUTH", "AWS_CREDS", "DB_PWD", "AWS_ACCESSKEY", "MY_SECRETKEY", "accessKey",
    "SECRETKEY"]) assert.ok(credentialEnvName(name), name)
  for (const name of ["PATH", "HOME", "KEYBOARD", "TOKENIZER_PATH", "MONKEY", "AWS_PROFILE", "KUBECONFIG", "DATABASE_HOST",
    "LANG", "EDITOR", "TERM", "PWD", "npm_config_cache", "GIT_AUTHOR_NAME", "GIT_DIR", "TOKENIZERS_PARALLELISM", "AUTHOR",
    "PASSENGER", "NODE_PATH", "REDIS_HOST", "OLDPWD", "OAUTHLIB_INSECURE_TRANSPORT", "HTTP_PROXY", "PIP_INDEX_URL"]) {
    assert.ok(!credentialEnvName(name), name)
  }
})

const ROOT = join(HERE, "..", "..")
const ADMITTED = { provider: "claude-agent", model: "haiku", effort: "low", harness: "claude-agent-sdk via opencode" }

/** Pinned temp stores with one live admission, created by the real `routing.py prepare`. */
function realStores(admit: boolean) {
  const dir = mkdtempSync(join(tmpdir(), "routing-real-"))
  const settings = join(dir, "routing", "settings.json")
  const ledger = join(dir, "usage", "organizations.sqlite3")
  mkdirSync(dirname(settings), { recursive: true, mode: 0o700 })
  const table = join(dirname(settings), "strategy-table.json")
  writeFileSync(table, JSON.stringify({ schema: 1, version: "t1", rows: { "worker|code|medium|medium": [
    { strategy_id: "s-sub", model: ADMITTED.model, effort: ADMITTED.effort, harness: ADMITTED.harness, evidence_refs: ["g1"] }] } }))
  chmodSync(table, 0o600)
  if (admit) {
    const request = {
      v: 1, op: "prepare", assignment: { attempt_id: "real-a1", task: "task-real", role: "implementer", scope: "scope1" },
      job: { id: "job-1", role: "worker", scope_ref: "scope1", objective: "o", context: "c", profile: {
        task_family: "code", complexity_band: "medium", risk_band: "medium", review_policy: "independent",
        context_tokens: 100, tools: ["edit"], uncertainty: "low" } },
      observations: { now: Math.floor(Date.now() / 1000), routes: [{ route: "sub", strategy_id: "s-sub", ...ADMITTED,
        account: "anthropic:acct", billing_class: "included_subscription", pool: "sub", demand: { "5h": 10 },
        capable: true, permitted: true, binding_enforceable: true }],
        pools: { sub: { windows: [{ id: "5h", limit: 1000, used: 100, length: 18000, reset_at: null, rolling: true, fresh: true }] } } },
    }
    const done = spawnSync("python3", ["-I", "-B", join(ROOT, "scripts/routing.py"), "prepare", "--settings", settings, "--ledger", ledger],
      { input: JSON.stringify(request), encoding: "utf8", timeout: 30000 })
    assert.equal(JSON.parse(done.stdout).state, "admitted", done.stdout)
  }
  const command = (op: string, input: Record<string, unknown>) => {
    const done = spawnSync("python3", ["-I", "-B", join(ROOT, "scripts/routing.py"), op, "--settings", settings, "--ledger", ledger],
      { input: JSON.stringify({ v: 1, op, ...input }), encoding: "utf8", timeout: 30000 })
    return JSON.parse(done.stdout)
  }
  const argv = ["python3", "-I", "-B", join(ROOT, "scripts/routing.py"), "--serve", "--settings", settings, "--ledger", ledger]
  return { argv, command, cleanup: () => rmSync(dir, { recursive: true, force: true }) }
}

const REAL_REQUEST = { provider: "claude-agent", model: "haiku", variant: "low", harness: "claude-agent-sdk via opencode",
  kind: "primary", funding_pool: "sub" }

async function realPlugin(stores: ReturnType<typeof realStores>) {
  const { ctx, hooks, state, emit } = fakeCtx()
  const cleanup = await createRoutingPlugin({ argv: stores.argv, spawnGraceMs: 3000 }).setup(ctx)
  const rpc = (method: string, input: unknown = {}) => state.handlers[method](input, {})
  return { hooks, rpc, emit, stop: () => { if (typeof cleanup === "function") cleanup(); stores.cleanup() } }
}

test("the real child: prepare, health, dispatch, attach with the token, then checks carry it", async () => {
  const stores = realStores(true)
  const t = await realPlugin(stores)
  try {
    const health = await t.rpc("health")
    assert.equal(health.state, "ok", JSON.stringify(health))
    const dispatched = stores.command("dispatch", { attempt_id: "real-a1", request: REAL_REQUEST })
    assert.equal(dispatched.state, "dispatched", JSON.stringify(dispatched))
    const { authority, session_id: session } = dispatched
    // Before attach the ledger already knows the derived session: it is blocked from its creation.
    await t.emit(created(session))
    await assert.rejects(t.hooks["session.model.request"](modelRequest(session)), refused("attach_required"))
    // A token-less check and a wrong token are refused by the command itself.
    const raw = (extra: Record<string, unknown>) => stores.command("check", { attempt_id: "real-a1", session_id: session, request: REAL_REQUEST, ...extra })
    assert.deepEqual(raw({}), { v: 1, state: "blocked", reason: "authority_required" })
    assert.deepEqual(raw({ authority: "x".repeat(43) }), { v: 1, state: "blocked", reason: "authority_invalid" })
    assert.equal(raw({ authority }).authority, "verified")
    const input = { session_id: session, attempt_id: "real-a1", authority, funding_pool: "sub", model: "claude-agent/haiku#low" }
    for (const bad of [{ authority: "x".repeat(43) }, { authority: authority.slice(0, -1) + (authority.endsWith("A") ? "B" : "A") },
      { attempt_id: "real-unknown" }, { model: "claude-agent/haiku#high" }, { funding_pool: "other" }]) {
      await assert.rejects(Promise.resolve().then(() => t.rpc("attach", { ...input, ...bad })), blocked, JSON.stringify(Object.keys(bad)))
    }
    await assert.rejects(t.hooks["session.model.request"](modelRequest(session)), refused("attach_required"))
    assert.deepEqual(await t.rpc("attach", input), { state: "attached", session_id: session })
    await t.hooks["session.model.request"](modelRequest(session))
    await t.hooks["tool.execute.before"](spawnCall(session, "claude-agent/haiku#low"))
    await t.emit(created("ses_realkid1", session))
    await t.hooks["session.model.request"](modelRequest("ses_realkid1"))  // a child session, checked under the attempt
    await assert.rejects(t.hooks["session.model.request"](modelRequest(session, "primary", { ...HAIKU, variant: "high" })),
      refused("route_mismatch"))
    // Detach needs the same attempt and token.
    await assert.rejects(Promise.resolve().then(() => t.rpc("detach", { session_id: session, attempt_id: "real-a1", authority: "x".repeat(43) })),
      refused("detach_refused"))
    await t.hooks["session.model.request"](modelRequest(session))
    assert.deepEqual(await t.rpc("detach", { session_id: session, attempt_id: "real-a1", authority }), { state: "detached", session_id: session })
    await assert.rejects(Promise.resolve().then(() => t.rpc("detach", { session_id: session, attempt_id: "real-a1", authority })),
      refused("detach_refused"))
    await assert.rejects(t.hooks["session.model.request"](modelRequest(session)), refused("attach_required"))
    // A derived-looking id this ledger never issued is unmanaged, so interactive.
    await t.hooks["session.model.request"](modelRequest("ses_" + "9".repeat(32)))
  } finally { t.stop() }
})

test("the real child: an unsent admission cannot be attached, and a closed attempt cannot be re-attached", async () => {
  const stores = realStores(true)
  const t = await realPlugin(stores)
  try {
    const input = { session_id: MANAGED, attempt_id: "real-a1", authority: "x".repeat(43), funding_pool: "sub", model: "claude-agent/haiku#low" }
    await assert.rejects(Promise.resolve().then(() => t.rpc("attach", input)), refused("dispatch_required"))
    const { authority, session_id: session } = stores.command("dispatch", { attempt_id: "real-a1", request: REAL_REQUEST })
    assert.equal(stores.command("dispatch", { attempt_id: "real-a1", request: REAL_REQUEST }).authority, undefined)
    await t.rpc("attach", { ...input, session_id: session, authority })
    await t.rpc("detach", { session_id: session, attempt_id: "real-a1", authority })
    assert.equal(stores.command("record", { event: { event_id: "e1", attempt_id: "real-a1", kind: "complete" } }).state, "recorded")
    await assert.rejects(Promise.resolve().then(() => t.rpc("attach", { ...input, session_id: session, authority })), refused("attempt_closed"))
  } finally { t.stop() }
})

test("the real child without a ledger is alive but not ready, and attach is refused", async () => {
  const stores = realStores(false)
  const t = await realPlugin(stores)
  try {
    assert.deepEqual(await t.rpc("health"), { state: "not_ready", reason: "ledger_absent", version: 1 })
    await assert.rejects(Promise.resolve().then(() => t.rpc("attach", { session_id: MANAGED, attempt_id: "real-a1",
      authority: "x".repeat(43), funding_pool: "sub", model: "claude-agent/haiku#low" })), refused("ledger_absent"))
    await t.emit(created(DERIVED))
    await t.hooks["session.model.request"](modelRequest(DERIVED))  // no ledger: an unattached session is interactive
  } finally { t.stop() }
})

test("TS extraction agrees with the Python adapter on the parity cases", { skip: !process.env.OPENCODE_PLUGIN_PARITY }, () => {
  const cases = JSON.parse(readFileSync(process.env.OPENCODE_PLUGIN_PARITY!, "utf8"))
  for (const item of cases.env) assert.equal(credentialEnvName(item.name), item.credential, item.name)
  for (const item of cases.models) assert.deepEqual(routeFacts(item.model), item.facts, JSON.stringify(item.model))
  for (const item of cases.spawns) assert.deepEqual(spawnModel(item.model), item.requested, item.model)
  for (const item of cases.sessions) assert.equal(derivedSession(item.id), item.derived, item.id)
})
