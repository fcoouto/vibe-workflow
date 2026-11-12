// Run with `node --test tests/opencode_plugin/plugin.test.ts` (Node strips the types); tests/test_opencode_plugin.py wraps it.
import { test } from "node:test"
import assert from "node:assert/strict"
import { mkdtempSync, writeFileSync, readFileSync, existsSync, rmSync, mkdirSync, chmodSync } from "node:fs"
import { spawnSync } from "node:child_process"
import { createHash } from "node:crypto"
import { tmpdir } from "node:os"
import { join, dirname } from "node:path"
import { fileURLToPath } from "node:url"
import plugin, {
  configRoots, createRoutingPlugin, credentialEnvName, derivedSession, extraPaths, managedDenial, protectedPath, routeFacts,
  spawnModel,
  CONTRACT_VERSION,
} from "../../runtime/adapters/opencode_plugin.ts"

const HERE = dirname(fileURLToPath(import.meta.url))
const FAKE = join(HERE, "fake_routing.py")
const TIMEOUT = 200
const GRACE = 1500
/** A ledger-derived-shape session id (`ses_` + 32 lowercase hex) for a name; only these can be attached. */
const IMAGE = "experimental_image_generate"
const D = (name: string) => "ses_" + createHash("md5").update(name).digest("hex")
const MANAGED = D("managed1")
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
/** Emitting this makes the fake event stream throw, as a lost subscription would. */
const STREAM_LOST = Symbol("stream lost")

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
            const value = queue.shift()
            if (value === STREAM_LOST) throw new Error("event stream lost")
            return { done: false, value }
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
  const { noEvents, options, location, ...settings } = config
  const { ctx, hooks, lookups, state, emit } = fakeCtx(sessions, (options ?? {}) as Record<string, unknown>)
  if (noEvents) delete (ctx as any).event  // a host without an event stream
  if (location === null) delete (ctx as any).location  // a host that does not say where the instance is
  else if (location !== undefined) (ctx as any).location = { directory: location }
  const refusals: string[] = []  // attach's internal refusal reasons, seen only through the test hook
  // A fixed environment, so the host's own OPENCODE_CONFIG_DIR or XDG_CONFIG_HOME never changes a result.
  const instance = createRoutingPlugin({ argv: fake.argv, timeoutMs: TIMEOUT, spawnGraceMs: GRACE,
    onRefusal: (reason: string) => refusals.push(reason), environment: { env: {}, home: "/home/test" }, ...settings })
  const cleanup = await instance.setup(ctx)
  const rpc = (method: string, input: unknown = {}) => state.handlers[method](input, { signal: new AbortController().signal })
  // The launcher's attach: the child is told to answer the verification normally, then the test's modes come back.
  const attach = async (session = MANAGED, extra: Record<string, unknown> = {}) => {
    const attempt = (extra.attempt_id ?? "att-1") as string
    const saved = fake.control()
    const before = new Set(fake.all().map((r) => r.id))
    fake.set({ ...saved, check: ["allow"], probe: ["allow"], owners: { ...(saved.owners as object ?? {}), [session]: attempt } })
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
  const descendants = (session = MANAGED, attempt = "att-1", authority: unknown = TOKENS[attempt]) =>
    rpc("descendants", { session_id: session, attempt_id: attempt, authority })
  const stop = () => { if (typeof cleanup === "function") cleanup(); fake.cleanup() }
  return { fake, hooks, lookups, state, rpc, attach, detach, descendants, stop, emit, ctx, refusals }
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
    await t.emit(created(D("root1")))
    await t.attach()
    await t.emit(created("ses_kid1", MANAGED))
    await t.emit(created("ses_grandkid1", "ses_kid1"))
    await t.hooks["session.model.request"](modelRequest(D("root1")))  // known root: interactive
    await assert.rejects(t.hooks["session.model.request"](modelRequest("ses_grandkid1")), blocked)
    assert.deepEqual(t.lookups, [])
    await t.detach()
    await t.attach(D("other9"))
    await t.hooks["session.model.request"](modelRequest("ses_grandkid1"))  // its root was detached
    assert.deepEqual(t.lookups, [])
  } finally { t.stop() }
})

// Every hook an interactive session reaches; none may block, deny or throw.
async function assertUntouched(t: Awaited<ReturnType<typeof start>>, sessionID: string) {
  await t.hooks["session.model.request"](modelRequest(sessionID))
  await t.hooks["tool.execute.before"](spawnCall(sessionID, "other/model"))
  await t.hooks["tool.execute.before"](spawnCall(sessionID))
  await t.hooks["tool.execute.before"](toolCall(sessionID, IMAGE, { prompt: PROMPT, model: "openai/gpt-image-1" }))
  for (const [action, resources] of [["webfetch", []], ["read", []], ["execute", []], [IMAGE, ["openai/gpt-image-1"]],
    ["edit", [".opencode/command/x.md"]], ["edit", ["opencode.json"]], ["edit", []], ["edit", null]] as [string, unknown][]) {
    const event = { sessionID, action, resources, effect: "ask" } as any
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
    await t.attach(D("other9"))
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
  const B = D("rootb1")
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
      await t.attach(D(`full${root}`))
      for (let index = 0; index < 256; index++) await t.emit(created(`ses_full${root}x${index}`, D(`full${root}`)))
    }
    await t.attach(D("rootc1"))
    await assert.rejects(t.hooks["tool.execute.before"](spawnCall(D("rootc1"), "claude-agent/haiku#low")),
      (error: Error) => blocked(error) && /descendant_limit/.test(error.message))
    await t.emit(created("ses_ckid1", D("rootc1")))
    await assert.rejects(t.hooks["session.model.request"](modelRequest("ses_ckid1")),
      (error: Error) => blocked(error) && /descendant_limit/.test(error.message))
    await t.hooks["session.model.request"](modelRequest(D("rootc1")))
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
  for (let index = 0; index < 250; index++) sessions[`ses_kid${index}`] = { parentID: D("root0") }  // within one root's budget
  const t = await start({}, sessions)
  try {
    await t.attach(D("root0"))
    for (let index = 0; index < 250; index++) await t.hooks["session.model.request"](modelRequest(`ses_kid${index}`))
    for (let index = 1; index < 256; index++) await t.attach(D(`root${index}`))
    await t.attach(D("root5"))  // re-attaching an attached root is allowed at the limit
    await assert.rejects(Promise.resolve().then(() => t.attach(D("root999"))), blocked)
    await t.detach(D("root1"))
    await t.attach(D("root999"))
  } finally { t.stop() }
})

const refused = (reason: string | RegExp) => (error: unknown) => blocked(error)
  && (typeof reason === "string" ? (error as Error).message === `routing: blocked (${reason})` : reason.test((error as Error).message))
const attachInput = (extra: Record<string, unknown> = {}) => ({ session_id: MANAGED, attempt_id: "att-1", authority: TOKEN,
  funding_pool: "claude-max", model: "claude-agent/haiku#low", ...extra })

test("attach verifies the authority with a strict check, then that the ledger owns the session, before recording", async () => {
  const t = await start({ owners: { [MANAGED]: "att-1" } })
  try {
    assert.deepEqual(await t.rpc("attach", attachInput()), { state: "attached", session_id: MANAGED })
    const [verify, owned] = t.fake.all().filter((r) => r.op === "check")
    assert.deepEqual(verify, { v: 1, op: "check", id: verify.id, attempt_id: "att-1", session_id: MANAGED, authority: TOKEN,
      request: { provider: "claude-agent", model: "haiku", variant: "low", harness: "claude-agent-sdk via opencode",
        kind: "primary", funding_pool: "claude-max" } })
    assert.deepEqual(Object.keys(owned).sort(), ["id", "op", "request", "session_id", "v"])
    assert.equal(owned.session_id, MANAGED)
  } finally { t.stop() }
})

test("attach accepts only the attempt's own derived session (S2)", async () => {
  const ROOT_B = D("rootb2")
  const t = await start({ owners: { [MANAGED]: "att-1", [ROOT_B]: "att-b" } })
  try {
    await t.rpc("attach", attachInput({ session_id: ROOT_B, attempt_id: "att-b", authority: TOKEN_B }))
    await t.emit(created("ses_bchild1", ROOT_B))
    t.refusals.length = 0
    // A's token with B's child, an interactive id, B's derived root, or a derived-shape id the ledger does not know.
    await assert.rejects(Promise.resolve().then(() => t.rpc("attach", attachInput({ session_id: "ses_bchild1" }))), refused("invalid_attach"))
    await assert.rejects(Promise.resolve().then(() => t.rpc("attach", attachInput({ session_id: OTHER }))), refused("invalid_attach"))
    await assert.rejects(Promise.resolve().then(() => t.rpc("attach", attachInput({ session_id: ROOT_B }))), refused("attach_refused"))
    await assert.rejects(Promise.resolve().then(() => t.rpc("attach", attachInput({ session_id: D("unknown7") }))), refused("attach_refused"))
    assert.deepEqual(t.refusals, ["session_mismatch", "not_own_session"])
    assert.deepEqual(await t.rpc("attach", attachInput()), { state: "attached", session_id: MANAGED })
  } finally { t.stop() }
})

test("an RPC caller without the session's token can neither attach nor detach", async () => {
  const t = await start({ check: ["blocked"] })
  const enforcedChecks = () => t.fake.requests("check").filter((r) => "attempt_id" in r).length
  const managedNow = async (session: string) => {
    const before = enforcedChecks()
    await t.hooks["session.model.request"](modelRequest(session)).then(() => false, () => true)
    return enforcedChecks() > before
  }
  try {
    t.fake.set({ owners: { [MANAGED]: "att-1" } })
    // A wrong token, another attempt's token, and a token for no attempt are all refused with one reason (S1).
    for (const extra of [{ authority: "x".repeat(43) }, { authority: TOKEN_B }, { attempt_id: "att-b", authority: TOKEN },
      { attempt_id: "att-none", authority: TOKEN }]) {
      await assert.rejects(Promise.resolve().then(() => t.rpc("attach", attachInput(extra))), refused("attach_refused"))
    }
    assert.deepEqual(t.refusals, ["authority_invalid", "authority_invalid", "session_mismatch", "session_mismatch"])
    for (const mode of ["blocked", "unmanaged", "allow_unverified", "allow_other_attempt", "garbage", "exit", "no_state", "echo_authority"]) {
      t.fake.set({ check: [mode], owners: { [MANAGED]: "att-1" } })
      await assert.rejects(Promise.resolve().then(() => t.rpc("attach", attachInput())), refused("attach_refused"), mode)
    }
    await new Promise((resolve) => setTimeout(resolve, 1100))  // the failing modes left the child in its back-off
    t.fake.set({ check: ["blocked"] })
    assert.equal(await managedNow(MANAGED), false)  // still interactive: no check was sent for its request
    // Missing or malformed fields never reach the child.
    const sent = t.fake.all().length
    for (const bad of [null, [], "x", {}, attachInput({ authority: undefined }), attachInput({ authority: "" }),
      attachInput({ authority: 42 }), attachInput({ authority: "has space " + TOKEN }), attachInput({ authority: "a".repeat(129) }),
      attachInput({ model: undefined }), attachInput({ model: "haiku" }), attachInput({ model: "a/b#c#d" }),
      attachInput({ attempt_id: undefined }), attachInput({ session_id: "nope" }), attachInput({ session_id: OTHER }),
      attachInput({ session_id: MANAGED.toUpperCase() }), attachInput({ funding_pool: "" }),
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
    t.refusals.length = 0
    // Even a verified owner of another attempt is refused, and the conflict is only found after verification.
    await assert.rejects(t.attach(MANAGED, { attempt_id: "att-b" }), refused("attach_refused"))
    await assert.rejects(t.attach("ses_kid1", { attempt_id: "att-b" }), refused("invalid_attach"))
    assert.deepEqual(t.refusals, ["attach_conflict"])
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
    t.fake.set({ check: ["blocked"] })  // the ledger now refuses the attempt
    await assert.rejects(Promise.resolve().then(() => t.rpc("attach", attachInput())), refused("attach_refused"))
    await t.hooks["session.model.request"](modelRequest(MANAGED))  // nothing was recorded, and the ledger does not know it
    assert.equal(t.fake.requests("check").filter((r) => "attempt_id" in r).length, 1)  // only the refused verification
  } finally { t.stop() }
})

test("B1: a finished attempt's session blocks only itself and never turns on stripping or lookups", async () => {
  const t = await start({ check: ["blocked"] }, { ses_ichild1: { parentID: "ses_iparent1" } })
  try {
    await t.attach()
    await t.detach()
    t.fake.set({ check: ["blocked"], closed_sessions: [MANAGED] })  // `record complete`
    await assert.rejects(t.hooks["session.model.request"](modelRequest(MANAGED)), refused("attempt_closed"))
    await assert.rejects(t.hooks["session.model.request"](modelRequest(MANAGED)), refused("attempt_closed"))
    t.refusals.length = 0
    await assert.rejects(t.attach(MANAGED), refused("attach_refused"))  // `attempt_closed` from the ledger
    const shell = { env: { ...shellEnv(), SSH_AUTH_SOCK: "/s" } }
    await t.hooks["shell.create.before"](shell)
    assert.deepEqual(shell.env, { ...shellEnv(), SSH_AUTH_SOCK: "/s" })
    const began = performance.now()
    await assertUntouched(t, "ses_ichild1")
    assert.ok(performance.now() - began < 100)
    assert.deepEqual(t.lookups, [])
    // A session recorded under the closed one is blocked too (S-a), still without stripping or lookups.
    await t.emit(created("ses_closedkid1", MANAGED))
    await assert.rejects(t.hooks["session.model.request"](modelRequest("ses_closedkid1")), refused("attempt_closed"))
    assert.deepEqual(t.lookups, [])
    assert.equal(t.fake.requests("check").filter((r) => r.session_id === MANAGED && !("attempt_id" in r)).length, 1)
  } finally { t.stop() }
})

test("B1: pending sessions alone never strip shells or start lookups, and expire to be asked again", async () => {
  const t = await start({ check: ["blocked"], known_sessions: [DERIVED] }, { ses_ichild1: { parentID: "ses_iparent1" } }, { pendingMs: 300 })
  try {
    await t.emit(created(DERIVED))
    await assert.rejects(t.hooks["session.model.request"](modelRequest(DERIVED)), refused("attach_required"))
    const shell = { env: shellEnv() }
    await t.hooks["shell.create.before"](shell)
    assert.deepEqual(shell.env, shellEnv())
    await assertUntouched(t, "ses_ichild1")
    assert.deepEqual(t.lookups, [])
    const asked = () => t.fake.requests("check").filter((r) => r.session_id === DERIVED).length
    assert.equal(asked(), 1)
    await new Promise((resolve) => setTimeout(resolve, 350))
    t.fake.set({ check: ["blocked"] })  // the ledger forgot it
    await t.hooks["session.model.request"](modelRequest(DERIVED))
    assert.equal(asked(), 2)
  } finally { t.stop() }
})

test("S4: at most 8 event-started questions are in flight, and an overflowing one is not remembered as unmanaged", async () => {
  const ids = Array.from({ length: 20 }, (_, index) => D(`flood${index}`))
  const t = await start({ probe: ["stall"], threads: true })
  try {
    for (const id of ids) await t.emit(created(id))
    await new Promise((resolve) => setTimeout(resolve, 50))
    assert.equal(t.fake.requests("check").length, 8)
    // The hook asks its own question within its budget, so a session the ledger owns is still found.
    t.fake.set({ probe: ["allow"], threads: true, known_sessions: [ids[15]] })
    await assert.rejects(t.hooks["session.model.request"](modelRequest(ids[15])), refused("attach_required"))
  } finally { t.stop() }
})

test("S-b: a burst of derived-looking creations never lets a ledger-owned session through", async () => {
  const t = await start({ probe: ["shuffle"], threads: true, known_sessions: [DERIVED] })
  try {
    for (let index = 0; index < 40; index++) await t.emit(created(D(`burst${index}`)))
    await t.emit(created(DERIVED))
    await assert.rejects(t.hooks["session.model.request"](modelRequest(DERIVED)), refused("attach_required"))
    await assert.rejects(t.hooks["tool.execute.before"](spawnCall(DERIVED, "claude-agent/haiku#low")), refused("attach_required"))
  } finally { t.stop() }
})

test("S-b: hook questions have their own limit; past it a hook does not wait", async () => {
  const ids = Array.from({ length: 9 }, (_, index) => D(`hookq${index}`))
  const t = await start({ probe: ["stall"], threads: true })
  try {
    const waiting = ids.slice(0, 8).map((id) => t.hooks["session.model.request"](modelRequest(id)))
    await new Promise((resolve) => setTimeout(resolve, 50))
    const began = performance.now()
    await t.hooks["session.model.request"](modelRequest(ids[8]))
    assert.ok(performance.now() - began < 100)
    await Promise.all(waiting)  // each resolves within its budget as interactive
  } finally { t.stop() }
})

test("S-a: sessions under a finished attempt stay blocked after their root is detached", async () => {
  const t = await start({ check: ["blocked"] }, { ses_ichild1: { parentID: "ses_iparent1" } })
  try {
    await t.attach()
    await t.emit(created("ses_fkid1", MANAGED))
    await t.emit(created("ses_fgrand1", "ses_fkid1"))
    t.fake.set({ check: ["blocked"], closed_sessions: [MANAGED] })  // `record complete`, then the launcher detaches
    await t.detach()
    await flood(t, 4100)  // the lineage cache forgets them; the detached root's record does not
    for (const session of ["ses_fkid1", "ses_fgrand1"]) {
      await assert.rejects(t.hooks["session.model.request"](modelRequest(session)), refused("attempt_closed"))
      await assert.rejects(t.hooks["tool.execute.before"](spawnCall(session, "claude-agent/haiku#low")), refused("attempt_closed"))
    }
    const shell = { env: shellEnv() }
    await t.hooks["shell.create.before"](shell)
    assert.deepEqual(shell.env, shellEnv())
    await assertUntouched(t, "ses_ichild1")
    assert.deepEqual(t.lookups, [])
    assert.equal(t.fake.requests("check").filter((r) => "attempt_id" in r).length, 0)
  } finally { t.stop() }
})

test("LOW-1: a session created after detach under a retired child stays blocked after a cache flood", async () => {
  const t = await start({ check: ["blocked"] }, {}, { pendingMs: 60000 })
  try {
    await t.attach()
    await t.emit(created("ses_rkid1", MANAGED))
    await t.attach(D("other9"))  // another root stays attached, so lookups are allowed and must not decide this
    t.fake.set({ check: ["blocked"], closed_sessions: [MANAGED] })
    await t.detach()
    await flood(t, 4100)  // evicts ses_rkid1 -> MANAGED from the parent cache
    await t.emit(created("ses_rgrand1", "ses_rkid1"))  // created through the API after detach
    await t.emit(created("ses_rgreat1", "ses_rgrand1"))
    t.state.failLookup = true
    for (const session of ["ses_rgrand1", "ses_rgreat1"]) {
      await assert.rejects(t.hooks["session.model.request"](modelRequest(session)), refused("attempt_closed"))
      await assert.rejects(t.hooks["tool.execute.before"](spawnCall(session, "claude-agent/haiku#low")), refused("attempt_closed"))
    }
  } finally { t.stop() }
})

test("LOW-1: the same under a detached attempt that is still open blocks with attach_required", async () => {
  const t = await start({ check: ["blocked"] })
  try {
    await t.attach()
    await t.emit(created("ses_okid2", MANAGED))
    t.fake.set({ check: ["blocked"], known_sessions: [MANAGED] })
    await t.detach()
    await flood(t, 4100)
    await t.emit(created("ses_ograndkid2", "ses_okid2"))
    await assert.rejects(t.hooks["session.model.request"](modelRequest("ses_ograndkid2")), refused("attach_required"))
    const shell = { env: shellEnv() }
    await t.hooks["shell.create.before"](shell)
    assert.deepEqual(shell.env, shellEnv())
    assert.deepEqual(t.lookups, [])
  } finally { t.stop() }
})

test("S-a: sessions under a detached open attempt are blocked until attached, then closed when it finishes", async () => {
  const t = await start({ check: ["blocked"] }, {}, { pendingMs: 200 })
  try {
    await t.attach()
    await t.emit(created("ses_okid1", MANAGED))
    t.fake.set({ check: ["blocked"], known_sessions: [MANAGED] })
    await t.detach()
    await assert.rejects(t.hooks["session.model.request"](modelRequest("ses_okid1")), refused("attach_required"))
    t.fake.set({ check: ["blocked"], closed_sessions: [MANAGED] })
    await new Promise((resolve) => setTimeout(resolve, 250))
    await assert.rejects(t.hooks["session.model.request"](modelRequest("ses_okid1")), refused("attempt_closed"))
  } finally { t.stop() }
})

test("S-c: shutting the plugin down with a request in flight leaves no routing child running", async () => {
  const fake = child({ check: ["stall"] })
  const { ctx, hooks, state } = fakeCtx()
  const instance = createRoutingPlugin({ argv: fake.argv, timeoutMs: TIMEOUT, spawnGraceMs: GRACE })
  const cleanup: any = await instance.setup(ctx)
  try {
    fake.set({ check: ["allow"], probe: ["allow"], owners: { [MANAGED]: "att-1" } })
    await state.handlers.attach({ session_id: MANAGED, attempt_id: "att-1", authority: TOKEN, funding_pool: "claude-max",
      model: "claude-agent/haiku#low" })
    fake.set({ check: ["stall"] })
    const pending = hooks["session.model.request"](modelRequest(MANAGED)).then(() => "pass", (error: Error) => error.message)
    await new Promise((resolve) => setTimeout(resolve, 50))
    const spawned = fake.spawns()
    cleanup()
    assert.match(await pending, /^routing: blocked \(/)
    await new Promise((resolve) => setTimeout(resolve, 500))
    assert.equal(fake.spawns(), spawned)
    const pids = readFileSync(join(dirname(fake.argv[fake.argv.indexOf("--log") + 1]), "log.jsonl"), "utf8").trim().split("\n")
      .map((line) => JSON.parse(line)).filter((r) => r.spawn).map((r) => r.spawn)
    for (const pid of pids) assert.throws(() => process.kill(pid, 0), undefined, `child ${pid} still running`)
    // After shutdown nothing starts a child again (attachments are gone, so the session is interactive).
    await hooks["session.model.request"](modelRequest(MANAGED))
    await assert.rejects(Promise.resolve().then(() => state.handlers.health({})), blocked)
    assert.equal(fake.spawns(), spawned)
  } finally { fake.cleanup() }
})

test("re-attaching with the same token never loosens deny_actions", async () => {
  const t = await start()
  try {
    await t.attach(MANAGED, { deny_actions: ["read"] })
    await t.attach(MANAGED, { deny_actions: ["execute"] })
    await t.attach(MANAGED)
    for (const action of ["read", "execute", "webfetch"]) {
      const event = { sessionID: MANAGED, action, resources: [], effect: "allow" } as any
      await t.hooks["permission.evaluate"](event)
      assert.equal(event.effect, "deny", action)
    }
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
    assert.deepEqual(shell.env, shellEnv())  // only attached roots strip shells (B1)
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
    await t.hooks["session.model.request"](modelRequest("ses_late2"))  // nothing attached: no lookups, interactive
    assert.deepEqual(t.lookups, [])
    await t.attach(D("other9"))
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
        await settle(() => t.rpc("attach", attachInput({ session_id: D("another1") })))
        await settle(() => t.rpc("attach", attachInput({ session_id: D("another2"), authority: WRONG })))
        await settle(() => t.rpc("attach", attachInput({ authority: WRONG, model: "bad" })))
        await settle(() => t.rpc("attach", attachInput({ funding_pool: "p".repeat(5000) })))
        await settle(() => t.detach(MANAGED, "att-1", WRONG))
        await settle(() => t.detach(MANAGED, "att-b", TOKEN))
        await settle(() => t.rpc("detach", { session_id: MANAGED, attempt_id: "att-1", authority: TOKEN + " " }))
        await settle(() => t.descendants())
        await settle(() => t.descendants(MANAGED, "att-1", WRONG))
        await settle(() => t.descendants(MANAGED, "att-b", TOKEN))
        await settle(() => t.descendants("ses_kid1"))
        await settle(() => t.rpc("descendants", { session_id: MANAGED, attempt_id: "att-1", authority: TOKEN + " " }))
        await settle(() => t.rpc("descendants", { session_id: MANAGED, attempt_id: TOKEN, authority: TOKEN }))
        await settle(() => t.detach())
        await settle(() => t.descendants())
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
  test(`seeded lineage fuzz ${seed}: interactive sessions are never blocked and wait for a ledger question at most once`, async () => {
    const random = seeded(seed)
    const pick = <T>(items: T[]) => items[Math.floor(random() * items.length)]
    const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))
    const known: string[] = []  // derived ids the ledger knows (open attempts)
    const closed: string[] = []  // derived ids of finished attempts
    let mode = "allow"  // how the ledger answers questions: allow (normal), blocked, garbage, exit or stall
    let stalls = 0
    const control = () => ({ check: ["blocked"], probe: [mode], known_sessions: known, closed_sessions: closed })
    const t = await start(control(), {}, { failedLookupMs: 60000 })
    const settle = async () => {  // a healthy ledger again, after any back-off or stalled question has run out
      if (mode === "allow") return
      const wait = mode === "stall" ? 4000 : 1100
      mode = "allow"
      t.fake.set(control())
      await sleep(wait)
    }
    const roots = new Set<string>()
    const interactive: string[] = []  // native ids, and derived-shape ids the ledger does not know
    const managed = new Map<string, string>()  // session -> attached root
    const pending: string[] = []
    const finished: string[] = []
    const hooked = new Set<string>()
    const questions = () => t.fake.requests("check").filter((r) => !("attempt_id" in r)).length
    let next = 0
    try {
      for (let step = 0; step < 120; step++) {
        const roll = random()
        if (roll < 0.15) {
          const id = `ses_i${next++}`
          const parent = interactive.length && random() < 0.6 ? pick(interactive) : random() < 0.3 ? `ses_ghost${next}` : undefined
          await t.emit(created(id, parent))
          interactive.push(id)
        } else if (roll < 0.22 && roots.size < 5) {
          await settle()
          const root = D(`r${next++}`)
          await t.emit(created(root))
          await t.attach(root)
          roots.add(root)
          managed.set(root, root)
        } else if (roll < 0.34 && managed.size) {
          const parent = pick([...managed.keys()])
          const id = `ses_m${next++}`
          await t.emit(created(id, parent))
          managed.set(id, managed.get(parent)!)
        } else if (roll < 0.38 && roots.size) {
          const root = pick([...roots])
          const finish = random() < 0.5  // the attempt completes before the launcher detaches
          if (finish) {
            await settle()
            closed.push(root)
            t.fake.set(control())
          }
          await t.detach(root)
          roots.delete(root)
          if (finish) await assert.rejects(t.hooks["session.model.request"](modelRequest(root)), refused("attempt_closed"))
          for (const [session, owner] of [...managed]) {
            if (owner === root) { managed.delete(session); (finish ? finished : interactive).push(session) }
          }
        } else if (roll < 0.48) {
          const id = D(`n${next++}`)  // derived-looking, unknown to the ledger
          await t.emit(created(id))
          interactive.push(id)
        } else if (roll < 0.52) {
          await settle()
          const id = D(`k${next++}`)
          known.push(id)
          t.fake.set(control())
          await t.emit(created(id))
          await assert.rejects(t.hooks["session.model.request"](modelRequest(id)), refused("attach_required"))
          pending.push(id)
        } else if (roll < 0.55 && pending.length) {
          const id = `ses_p${next++}`
          await t.emit(created(id, pick(pending)))
          pending.push(id)
        } else if (roll < 0.58) {
          await settle()
          const id = D(`c${next++}`)
          closed.push(id)
          t.fake.set(control())
          await t.emit(created(id))
          finished.push(id)
        } else if (roll < 0.61) {
          const choice = pick(["allow", "blocked", "garbage", "exit", "stall"])
          if (choice === "stall" && stalls >= 1) continue
          if (choice === "stall") stalls += 1
          mode = choice
          t.fake.set(control())
        } else if (roll < 0.64) {
          t.state.failLookup = !t.state.failLookup
        } else {
          const population = pick([interactive, [...managed.keys()], pending, finished].filter((items) => items.length))
          if (!population) continue
          const session = pick(population)
          const where = `seed ${seed} step ${step} ${session}`
          const enforced = () => t.fake.requests("check").filter((r) => "attempt_id" in r && r.session_id === session)
          const before = enforced().length
          const asked = questions()
          const lookups = t.lookups.length
          const began = performance.now()
          const outcome = await t.hooks["session.model.request"](modelRequest(session)).then(() => "pass", (error: Error) => error.message)
          if (population === interactive) {
            assert.equal(outcome, "pass", `${where}: interactive was blocked`)
            await assertUntouched(t, session)
            const took = performance.now() - began
            assert.equal(enforced().length, before, `${where}: interactive was checked`)
            assert.ok(took < TIMEOUT + GRACE + 400, `${where}: waited ${took} ms`)
            if (hooked.has(session) && questions() === asked) assert.ok(took < 250, `${where}: waited ${took} ms again`)
            if (!roots.size) assert.equal(t.lookups.length, lookups, `${where}: lookups with nothing attached`)
            hooked.add(session)
          } else if (population === pending) {
            assert.equal(outcome, "routing: blocked (attach_required)", where)
          } else if (population === finished) {
            assert.equal(outcome, "routing: blocked (attempt_closed)", where)
          } else {
            assert.match(outcome, /^routing: blocked \(/, where)
            for (const check of enforced().slice(before)) assert.deepEqual([check.attempt_id, check.authority], ["att-1", TOKEN])
            // The descendants RPC answers only with the asking root's own recorded sessions, and asks nothing.
            const root = managed.get(session)!
            const sent = t.fake.all().length
            const answer = await t.descendants(root)
            assert.equal(t.fake.all().length, sent, `${where}: descendants called the child`)
            assert.equal(answer.root, root)
            for (const entry of answer.descendants) {
              assert.equal(managed.get(entry.session_id), root, `${where}: ${entry.session_id} is not under ${root}`)
              assert.notEqual(entry.session_id, root)
            }
          }
          const shell = { env: shellEnv() }
          await t.hooks["shell.create.before"](shell)
          assert.deepEqual(Object.keys(shell.env), roots.size ? ["PATH"] : ["PATH", "GITHUB_TOKEN"], `${where}: shell`)
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

const CONFIG_PATHS = JSON.parse(readFileSync(join(HERE, "config_paths.json"), "utf8"))
/** The live-recorded `permission.evaluate` payload (s3-3-shim-live.json key set) with another action and resources. */
const permissionEvent = (sessionID: string, action: string, resources: unknown, effect = "allow") => ({
  action, agent: "build", effect, metadata: {}, resources, sessionID,
  source: { id: "toolu_live1", messageID: "msg_live1", type: "tool" },
}) as any
const evaluated = async (t: Awaited<ReturnType<typeof start>>, event: any) => {
  await t.hooks["permission.evaluate"](event)
  return event
}

test("S3.3d: image generation is denied for managed sessions by action and by tool; interactive is untouched", async () => {
  const t = await start({}, { [OTHER]: {} })
  try {
    await t.attach()
    for (const effect of ["allow", "ask"]) {
      const event = await evaluated(t, permissionEvent(MANAGED, IMAGE, ["openai/gpt-image-1"], effect))
      assert.equal(event.effect, "deny")
      assert.equal(event.message, "Blocked by Smart Routing: this managed session may not use this action.")
    }
    await assert.rejects(t.hooks["tool.execute.before"](toolCall(MANAGED, IMAGE, { prompt: PROMPT, model: "openai/x" })),
      refused("image_generation_denied"))
    await assertUntouched(t, OTHER)
    await assertUntouched(t, "ses_unknown1")
    assert.equal(t.fake.requests().length, 0)  // no check is sent for either
  } finally { t.stop() }
})

const TABLE_ENVIRONMENT = { env: CONFIG_PATHS.env, home: CONFIG_PATHS.home }

test("S3.3d: managed edits of every config path spelling are denied; ordinary edits pass (table, both directory modes)", async () => {
  for (const location of [CONFIG_PATHS.directory, null, "relative/dir"]) {
    const known = location === CONFIG_PATHS.directory
    const t = await start({}, {}, { location, environment: TABLE_ENVIRONMENT })
    try {
      await t.attach()
      for (const action of ["edit", "write", "patch"]) {
        for (const resource of CONFIG_PATHS.deny) {
          assert.equal((await evaluated(t, permissionEvent(MANAGED, action, [resource]))).effect, "deny", `${location} ${action} ${resource}`)
          // A patch lists every file it touches; one config path among ordinary ones denies the whole patch.
          assert.equal((await evaluated(t, permissionEvent(MANAGED, action, ["src/x.ts", resource]))).effect, "deny", resource)
        }
        for (const resource of CONFIG_PATHS.deny_without_directory) {
          const event = await evaluated(t, permissionEvent(MANAGED, action, [resource]))
          assert.equal(event.effect, known ? "allow" : "deny", `${location} ${action} ${resource}`)
        }
        for (const resource of CONFIG_PATHS.allow) {
          const event = await evaluated(t, permissionEvent(MANAGED, action, [resource]))
          assert.equal(event.effect, "allow", `${location} ${action} ${resource}`)
          assert.equal(event.message, undefined)
        }
        assert.equal((await evaluated(t, permissionEvent(MANAGED, action, CONFIG_PATHS.allow))).effect, "allow")
      }
      // Reading config and other actions on config paths are not this rule's business.
      for (const action of ["read", "external_directory", "glob"]) {
        assert.equal((await evaluated(t, permissionEvent(MANAGED, action, [".opencode/command/x.md"]))).effect, "allow")
      }
      assert.equal(t.fake.requests().length, 0)
    } finally { t.stop() }
  }
})

test("S3.3d: an instance whose directory is a config directory denies its agent, command and mode edits", async () => {
  for (const item of CONFIG_PATHS.config_directories) {
    const t = await start({}, {}, { location: item.directory, environment: TABLE_ENVIRONMENT })
    try {
      await t.attach()
      for (const resource of item.deny ?? []) {
        assert.equal((await evaluated(t, permissionEvent(MANAGED, "edit", [resource]))).effect, "deny", `${item.directory} ${resource}`)
      }
      for (const resource of item.allow ?? []) {
        assert.equal((await evaluated(t, permissionEvent(MANAGED, "edit", [resource]))).effect, "allow", `${item.directory} ${resource}`)
      }
    } finally { t.stop() }
  }
})

test("S3.3d: missing or odd edit resources deny for managed sessions only", async () => {
  const t = await start({}, { [OTHER]: {} })
  try {
    await t.attach()
    const odd: unknown[] = [undefined, null, [], "src/x.ts", { path: "src/x.ts" }, [null], [5], ["src/x.ts", 7],
      Array(257).fill("src/x.ts"), ...CONFIG_PATHS.unparseable.map((value: string) => [value]), ["x".repeat(5000)]]
    for (const resources of odd) {
      assert.equal((await evaluated(t, permissionEvent(MANAGED, "edit", resources))).effect, "deny", JSON.stringify(resources)?.slice(0, 40))
      const interactive = await evaluated(t, permissionEvent(OTHER, "edit", resources, "ask"))
      assert.equal(interactive.effect, "ask")
      assert.equal(interactive.message, undefined)
    }
  } finally { t.stop() }
})

test("S3.3d: path matching stays linear on long runs of dots and spaces", async () => {
  const t = await start()
  try {
    await t.attach()
    const resources = Array.from({ length: 256 }, (_, index) => ". ".repeat(2000) + "x" + index)
    const began = performance.now()
    const event = await evaluated(t, permissionEvent(MANAGED, "patch", resources))
    assert.equal(event.effect, "allow")
    assert.ok(performance.now() - began < 300, `took ${performance.now() - began} ms`)
  } finally { t.stop() }
})

test("S3.3d: an existing denial keeps its message and secrets in resources never reach the message", async () => {
  const t = await start()
  try {
    await t.attach()
    for (const [action, resources] of [[IMAGE, ["openai/x"]], ["edit", [".opencode/command/x.md"]], ["edit", null]] as [string, unknown][]) {
      const earlier = { ...permissionEvent(MANAGED, action, resources, "deny"), message: "earlier reason" }
      assert.equal((await evaluated(t, earlier)).message, "earlier reason")
    }
    const leaking = await evaluated(t, permissionEvent(MANAGED, "edit", [`.opencode/command/${SECRET}.md`]))
    assert.equal(leaking.effect, "deny")
    assert.ok(!String(leaking.message).includes(SECRET))
    await assert.rejects(t.hooks["tool.execute.before"](toolCall(MANAGED, IMAGE, { prompt: SECRET + PROMPT })), blocked)
  } finally { t.stop() }
})

test("S3.3d: pending and closed sessions deny image generation and config edits too", async () => {
  const t = await start({ known_sessions: [DERIVED], closed_sessions: [DERIVED_B] })
  try {
    for (const session of [DERIVED, DERIVED_B]) {
      assert.equal((await evaluated(t, permissionEvent(session, IMAGE, ["openai/x"]))).effect, "deny", session)
      assert.equal((await evaluated(t, permissionEvent(session, "edit", ["opencode.json"]))).effect, "deny", session)
      assert.equal((await evaluated(t, permissionEvent(session, "edit", ["src/x.ts"]))).effect, "allow", session)
      await assert.rejects(t.hooks["tool.execute.before"](toolCall(session, IMAGE, { prompt: PROMPT })), refused("image_generation_denied"))
    }
  } finally { t.stop() }
})

test("S3.3d: protected_paths adds paths and never loosens; an invalid option denies every managed edit", async () => {
  const t = await start({}, { [OTHER]: {} }, { options: { protected_paths: [".cursor", "tools/agents"] } })
  try {
    await t.attach()
    for (const resource of [".cursor/rules.md", "x/.CURSOR/y", "tools/agents/a.md", ".opencode/command/x.md", "opencode.json"]) {
      assert.equal((await evaluated(t, permissionEvent(MANAGED, "edit", [resource]))).effect, "deny", resource)
    }
    assert.equal((await evaluated(t, permissionEvent(MANAGED, "edit", ["tools/x.md"]))).effect, "allow")
    assert.equal((await evaluated(t, permissionEvent(OTHER, "edit", [".cursor/rules.md"]))).effect, "allow")
  } finally { t.stop() }
  for (const bad of [[".."], "a", [5], [""]]) {
    const u = await start({}, { [OTHER]: {} }, { options: { protected_paths: bad } })
    try {
      await u.attach()
      assert.equal((await evaluated(u, permissionEvent(MANAGED, "edit", ["src/x.ts"]))).effect, "deny", JSON.stringify(bad))
      assert.equal((await evaluated(u, permissionEvent(MANAGED, "read", ["src/x.ts"]))).effect, "allow")
      assert.equal((await evaluated(u, permissionEvent(OTHER, "edit", ["src/x.ts"]))).effect, "allow")
    } finally { u.stop() }
  }
})

test("S3.3d: health lists the denials the shim applies to managed sessions", async () => {
  const t = await start()
  try {
    assert.deepEqual((await t.rpc("health")).managed_denies, ["config_edit", "image_generation"])
  } finally { t.stop() }
})

test("S3.3d: an invalid protected_paths option makes health not ready, without asking the child, and still denies edits", async () => {
  for (const bad of [[".."], "a", Array(33).fill("a"), ["a/b/c/d/e/f/g/h/i/j/k/l/m/n/o/p/q"]]) {
    const t = await start({}, {}, { options: { protected_paths: bad } })
    try {
      assert.deepEqual(await t.rpc("health"), { state: "not_ready", reason: "protected_paths_invalid", version: 1 })
      assert.equal(t.fake.requests("status").length, 0)
      await t.attach()
      assert.equal((await evaluated(t, permissionEvent(MANAGED, "edit", ["src/x.ts"]))).effect, "deny")
    } finally { t.stop() }
  }
  const ok = await start({}, {}, { options: { protected_paths: Array.from({ length: 32 }, (_, i) => `a/b/c/d/e/f/g/h/i/j/k/l/m/n/o/p${i}`) } })
  try { assert.equal((await ok.rpc("health")).state, "ok") } finally { ok.stop() }
})

test("S3.3d: the largest protected_paths option and a 256-file patch of deep paths stay fast", async () => {
  const options = { protected_paths: Array.from({ length: 32 }, (_, i) => [...Array(15).fill("a"), `z${i}`].join("/")) }
  const t = await start({}, {}, { options, location: CONFIG_PATHS.directory, environment: TABLE_ENVIRONMENT })
  try {
    await t.attach()
    const resources = Array(256).fill([...Array(2047).fill("a"), "b"].join("/"))
    const began = performance.now()
    assert.equal((await evaluated(t, permissionEvent(MANAGED, "patch", resources))).effect, "allow")
    assert.equal((await evaluated(t, permissionEvent(MANAGED, "patch", [...resources, "x"]))).effect, "deny")  // over 256 files
    assert.ok(performance.now() - began < 500, `took ${performance.now() - began} ms`)
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
  const real = (value: unknown): any => JSON.parse(JSON.stringify(value).replace(/<(ses|msg|toolu)#(\d+)>/g,
    (_: string, kind: string, n: string) => kind === "ses" ? D(`live${n}`) : `${kind}_live${n}`))
  const payload = (hook: string, tool?: string) => real(live.hooks.find((h: any) => h.hook === hook && (!tool || h.payload.tool === tool)).payload)
  const t = await start()
  try {
    await t.attach(D("live1"))
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
  assert.deepEqual(routeFacts({ providerID: "openrouter", id: "x/y" })?.model, "x/y")
  for (const id of ["a/b/c", "../x", "a//b", "a/", "/b", "a/..", "a/" + "b".repeat(128)]) {
    assert.equal(routeFacts({ providerID: "openrouter", id }), null, id)
  }
  assert.deepEqual(spawnModel("openrouter/z-ai/glm-5#high"), { provider: "openrouter", model: "z-ai/glm-5", variant: "high" })
  assert.equal(spawnModel("openrouter/a/b/c"), null)
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
  writeFileSync(table, JSON.stringify({ schema: 2, version: "t1", rows: { "implementation|medium|medium": [
    { strategy_id: "s-sub", model: ADMITTED.model, effort: ADMITTED.effort, harness: ADMITTED.harness, evidence_refs: ["g1"] }] } }))
  chmodSync(table, 0o600)
  if (admit) {
    const request = {
      v: 1, op: "prepare", assignment: { attempt_id: "real-a1", task: "task-real", role: "software-engineer", scope: "scope1" },
      job: { id: "job-1", role: "software-engineer", scope_ref: "scope1", objective: "o", context: "c", profile: {
        complexity_band: "medium", risk_band: "medium", review_policy: "independent",
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
  const refusals: string[] = []
  const cleanup = await createRoutingPlugin({ argv: stores.argv, spawnGraceMs: 3000,
    onRefusal: (reason: string) => refusals.push(reason) }).setup(ctx)
  const rpc = (method: string, input: unknown = {}) => state.handlers[method](input, {})
  return { hooks, rpc, emit, refusals, stop: () => { if (typeof cleanup === "function") cleanup(); stores.cleanup() } }
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
    assert.deepEqual(raw({}), { v: 1, state: "blocked", reason: "authority_required", settings_warnings: [] })
    assert.deepEqual(raw({ authority: "x".repeat(43) }), { v: 1, state: "blocked", reason: "authority_invalid", settings_warnings: [] })
    assert.equal(raw({ authority }).authority, "verified")
    const input = { session_id: session, attempt_id: "real-a1", authority, funding_pool: "sub", model: "claude-agent/haiku#low" }
    for (const bad of [{ authority: "x".repeat(43) }, { authority: authority.slice(0, -1) + (authority.endsWith("A") ? "B" : "A") },
      { attempt_id: "real-unknown" }, { model: "claude-agent/haiku#high" }, { funding_pool: "other" },
      { session_id: "ses_" + "9".repeat(32) }]) {  // the token holder's attempt does not own this derived-shape id
      await assert.rejects(Promise.resolve().then(() => t.rpc("attach", { ...input, ...bad })), refused("attach_refused"),
        JSON.stringify(Object.keys(bad)))
    }
    assert.deepEqual(t.refusals, ["authority_invalid", "authority_invalid", "session_mismatch", "route_mismatch", "route_mismatch",
      "not_own_session"])
    await assert.rejects(Promise.resolve().then(() => t.rpc("attach", { ...input, session_id: OTHER })), refused("invalid_attach"))
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
    await assert.rejects(Promise.resolve().then(() => t.rpc("attach", { ...input, session_id: D("unsent") })), refused("attach_refused"))
    assert.deepEqual(t.refusals, ["dispatch_required"])
    const { authority, session_id: session } = stores.command("dispatch", { attempt_id: "real-a1", request: REAL_REQUEST })
    assert.equal(stores.command("dispatch", { attempt_id: "real-a1", request: REAL_REQUEST }).authority, undefined)
    await t.rpc("attach", { ...input, session_id: session, authority })
    await t.emit(created("ses_realkid2", session))
    await t.hooks["session.model.request"](modelRequest("ses_realkid2"))  // the attempt is open: allowed
    assert.equal(stores.command("record", { event: { event_id: "e1", attempt_id: "real-a1", kind: "complete" } }).state, "recorded")
    await assert.rejects(t.hooks["session.model.request"](modelRequest("ses_realkid2")), refused("attempt_closed"))  // still attached
    await t.rpc("detach", { session_id: session, attempt_id: "real-a1", authority })
    // S-a: after detach the finished attempt's child stays blocked.
    await assert.rejects(t.hooks["session.model.request"](modelRequest("ses_realkid2")), refused("attempt_closed"))
    await assert.rejects(t.hooks["tool.execute.before"](spawnCall("ses_realkid2", "claude-agent/haiku#low")), refused("attempt_closed"))
    await assert.rejects(Promise.resolve().then(() => t.rpc("attach", { ...input, session_id: session, authority })), refused("attach_refused"))
    assert.deepEqual(t.refusals.at(-1), "attempt_closed")
    // B1 with the real command: the finished session blocks only itself; nothing else is stripped or looked up.
    await assert.rejects(t.hooks["session.model.request"](modelRequest(session)), refused("attempt_closed"))
    const shell = { env: shellEnv() }
    await t.hooks["shell.create.before"](shell)
    assert.deepEqual(shell.env, shellEnv())
  } finally { t.stop() }
})

test("the real child without a ledger is alive but not ready, and attach is refused", async () => {
  const stores = realStores(false)
  const t = await realPlugin(stores)
  try {
    assert.deepEqual(await t.rpc("health"), { state: "not_ready", reason: "ledger_absent", version: 1 })
    await assert.rejects(Promise.resolve().then(() => t.rpc("attach", { session_id: MANAGED, attempt_id: "real-a1",
      authority: "x".repeat(43), funding_pool: "sub", model: "claude-agent/haiku#low" })), refused("attach_refused"))
    assert.deepEqual(t.refusals, ["ledger_absent"])
    await t.emit(created(DERIVED))
    await t.hooks["session.model.request"](modelRequest(DERIVED))  // no ledger: an unattached session is interactive
  } finally { t.stop() }
})

// S3.3c: `routing.descendants` lets the launcher cross-check its own session listing against the shim's record.
const link = (session_id: string, parent_session_id: string | null) => ({ session_id, parent_session_id })
const byId = (entries: { session_id: string }[]) => [...entries].sort((a, b) => a.session_id < b.session_id ? -1 : 1)

test("descendants: the attached root's recorded tree at any depth, also after a cache flood, without lookups", async () => {
  const B = D("rootb1")
  const t = await start({ check: ["blocked"] })
  try {
    await t.attach()
    await t.emit(created(MANAGED))  // the launcher creates the root after attaching it
    await t.emit(created("ses_kid1", MANAGED))
    await t.emit(created("ses_grand1", "ses_kid1"))
    await t.emit(created("ses_cmd1", MANAGED))  // a `subagent: true` command child: known only from its creation event
    await t.attach(B, { attempt_id: "att-b" })
    await t.emit(created(B))
    await t.emit(created("ses_bkid1", B))
    await t.emit(created("ses_iroot1"))
    await t.emit(created("ses_ikid1", "ses_iroot1"))
    await flood(t, 4100)
    await t.emit(created("ses_great1", "ses_grand1"))
    const sent = t.fake.all().length
    assert.deepEqual(await t.descendants(), {
      state: "ok", root: MANAGED,
      descendants: byId([link("ses_kid1", MANAGED), link("ses_grand1", "ses_kid1"), link("ses_cmd1", MANAGED),
        link("ses_great1", "ses_grand1")]),
      truncated: false, complete: true, reason: null, cache: { recorded: 4, retired: 0 },
    })
    assert.deepEqual(await t.descendants(B, "att-b"), { state: "ok", root: B, descendants: [link("ses_bkid1", B)],
      truncated: false, complete: true, reason: null, cache: { recorded: 1, retired: 0 } })
    assert.deepEqual(await t.descendants(), await t.descendants())  // a read: repeating it changes nothing
    assert.equal(t.fake.all().length, sent)  // answered from the shim's own record: no routing child question
    assert.deepEqual(t.lookups, [])
  } finally { t.stop() }
})

test("descendants: a child found by a lookup is listed with its parent", async () => {
  const t = await start({ check: ["blocked"] }, { ses_missed1: { parentID: MANAGED } })
  try {
    await t.attach()
    await t.emit(created(MANAGED))
    await assert.rejects(t.hooks["session.model.request"](modelRequest("ses_missed1")), blocked)
    const answer = await t.descendants()
    assert.deepEqual(answer.descendants, [link("ses_missed1", MANAGED)])
    assert.equal(answer.complete, true)
  } finally { t.stop() }
})

test("descendants: one refusal for every caller without the attached root's attempt and token", async () => {
  const B = D("rootb1")
  const t = await start({ check: ["blocked"] })
  const refusal = (error: unknown) => blocked(error) && (error as Error).message === "routing: blocked (descendants_refused)"
  const invalid = (error: unknown) => blocked(error) && (error as Error).message === "routing: blocked (invalid_descendants)"
  try {
    await t.attach()
    await t.emit(created(MANAGED))
    await t.emit(created("ses_kid1", MANAGED))
    await t.attach(B, { attempt_id: "att-b" })
    await t.emit(created("ses_bkid1", B))
    const sent = t.fake.all().length
    for (const [session, attempt, authority] of [[MANAGED, "att-1", TOKEN_B], [MANAGED, "att-b", TOKEN_B],
      [MANAGED, "att-b", TOKEN], [B, "att-1", TOKEN], [MANAGED, "att-1", TOKEN.slice(0, -1) + "x"], [MANAGED, "att-1", TOKEN + "x"],
      ["ses_kid1", "att-1", TOKEN], [OTHER, "att-1", TOKEN], [D("never1"), "att-1", TOKEN], [D("never1"), "att-none", "x".repeat(43)]]) {
      await assert.rejects(Promise.resolve().then(() => t.descendants(session, attempt, authority)), refusal, `${session} ${attempt}`)
    }
    const hostile = JSON.parse(`{"__proto__": {"authority": "${TOKEN}"}, "session_id": "${MANAGED}", "attempt_id": "att-1"}`)
    for (const bad of [null, [], "x", 7, {}, { session_id: MANAGED }, { session_id: MANAGED, attempt_id: "att-1" }, hostile,
      { session_id: MANAGED, attempt_id: "att-1", authority: 7 }, { session_id: MANAGED, attempt_id: "att-1", authority: "" },
      { session_id: MANAGED, attempt_id: "att-1", authority: "a".repeat(129) },
      { session_id: MANAGED, attempt_id: "att-1", authority: "has space " + TOKEN },
      { session_id: "nope", attempt_id: "att-1", authority: TOKEN }, { session_id: "ses_" + "x".repeat(200), attempt_id: "att-1", authority: TOKEN },
      { session_id: MANAGED + "\n", attempt_id: "att-1", authority: TOKEN }, { session_id: [MANAGED], attempt_id: "att-1", authority: TOKEN },
      { session_id: MANAGED, attempt_id: "a".repeat(300), authority: TOKEN }, { session_id: MANAGED, attempt_id: "", authority: TOKEN }]) {
      await assert.rejects(Promise.resolve().then(() => t.rpc("descendants", bad)), invalid, JSON.stringify(bad)?.slice(0, 80))
    }
    assert.equal(t.fake.all().length, sent)
    // The refusals never revealed anything, and the rightful callers still get only their own trees.
    assert.deepEqual((await t.descendants()).descendants, [link("ses_kid1", MANAGED)])
    assert.deepEqual((await t.descendants(B, "att-b")).descendants, [link("ses_bkid1", B)])
    // After detach (and for a replayed call) the same refusal: the launcher reads the set before it detaches.
    await t.detach()
    await assert.rejects(Promise.resolve().then(() => t.descendants()), refusal)
    await assert.rejects(Promise.resolve().then(() => t.descendants()), refusal)
    assert.deepEqual((await t.descendants(B, "att-b")).descendants, [link("ses_bkid1", B)])
  } finally { t.stop() }
})

test("descendants: the response is bounded and says when it was cut", async () => {
  const t = await start({ check: ["blocked"] }, {}, { maxDescendantsResponse: 5 })
  try {
    await t.attach()
    await t.emit(created(MANAGED))
    for (let index = 0; index < 8; index++) await t.emit(created(`ses_many${index}`, MANAGED))
    const answer = await t.descendants()
    assert.equal(answer.descendants.length, 5)
    assert.deepEqual([answer.truncated, answer.complete, answer.reason], [true, false, "response_truncated"])
    assert.deepEqual(answer.cache, { recorded: 8, retired: 0 })
    assert.deepEqual(answer.descendants, byId(answer.descendants))
  } finally { t.stop() }
  const plain = await start({ check: ["blocked"] })
  try {
    await plain.attach()
    await plain.emit(created(MANAGED))
    for (let index = 0; index < 256; index++) await plain.emit(created(`ses_many${index}`, MANAGED))
    const answer = await plain.descendants()  // a full budget fits the default bound
    assert.deepEqual([answer.descendants.length, answer.truncated, answer.complete], [256, false, true])
    assert.ok(JSON.stringify(answer).length < 65536)
  } finally { plain.stop() }
})

test("descendants: complete is false, with one fixed reason, whenever the shim cannot vouch for the set", async () => {
  const cases: [string, Record<string, unknown>, (t: Awaited<ReturnType<typeof start>>) => Promise<void>][] = [
    ["event_stream_unavailable", {}, async (t) => { await t.emit(STREAM_LOST) }],
    ["event_stream_unavailable", { noEvents: true }, async () => {}],
    ["descendant_limit", {}, async (t) => {
      for (let index = 0; index < 257; index++) await t.emit(created(`ses_many${index}`, MANAGED))
    }],
    ["lineage_lookup_failed", {}, async (t) => {
      t.state.failLookup = true
      await assertUntouched(t, "ses_unknown1")  // an undeterminable chain: it may have been this root's child
    }],
    ["lineage_lookup_failed", {}, async (t) => {
      // Every lookup answers, but the chain is longer than one question's 8 lookups: undetermined, so interactive.
      const ctxSessions = t.ctx.session as any
      const original = ctxSessions.get
      ctxSessions.get = async ({ sessionID }: { sessionID: string }) => {
        const depth = Number(sessionID.slice("ses_deep".length))
        return { id: sessionID, ...(depth < 20 ? { parentID: `ses_deep${depth + 1}` } : {}) }
      }
      try { await assertUntouched(t, "ses_deep0") } finally { ctxSessions.get = original }
    }],
  ]
  for (const [reason, config, act] of cases) {
    const t = await start({ check: ["blocked"] }, {}, config)
    try {
      await t.attach()
      await t.emit(created(MANAGED))
      await t.emit(created("ses_kid1", MANAGED))
      await act(t)
      const answer = await t.descendants()
      assert.equal(answer.state, "ok", reason)
      assert.deepEqual([answer.complete, answer.reason], [false, reason], reason)
      if (!config.noEvents) assert.ok(answer.descendants.some((entry: any) => entry.session_id === "ses_kid1"), reason)
    } finally { t.stop() }
  }
})

// Moved out of the table above: it re-sent the root's creation event after a re-attach to reach a `watched` root,
// which a repeated creation event may no longer do (S3). Here the later root is created for the first time instead.
test("descendants: a remembered failed lookup also leaves a root attached after the failure unvouched", async () => {
  const t = await start({ check: ["blocked"] }, {}, { failedLookupMs: 60000 })
  try {
    await t.attach(D("first1"))
    await t.emit(created(D("first1")))
    t.state.failLookup = true
    await assertUntouched(t, "ses_unknown2")  // fails while only another root is attached...
    t.state.failLookup = false
    await t.attach()
    await t.emit(created(MANAGED))
    assert.equal((await t.descendants()).complete, true)
    await assertUntouched(t, "ses_unknown2")  // ...and is answered from the failure memory, still undetermined
    assert.deepEqual(Object.values(pick(await t.descendants())), [false, "lineage_lookup_failed"])
  } finally { t.stop() }
})

const pick = (answer: any) => ({ complete: answer.complete, reason: answer.reason })

test("descendants: a repeated creation event for the root never vouches for it again (S3)", async () => {
  const t = await start({ check: ["blocked"] })
  try {
    await t.attach()
    await t.emit(created(MANAGED))
    await t.emit(created("ses_kid1", MANAGED))
    await t.detach()
    await t.emit(created("ses_gap1", MANAGED))  // created while detached: nobody recorded it
    await t.attach()
    await t.emit(created(MANAGED))  // the same creation event again (replayed or forged)
    const answer = await t.descendants()
    assert.deepEqual(pick(answer), { complete: false, reason: "root_creation_unobserved" })
    assert.ok(!answer.descendants.some((entry: any) => entry.session_id === "ses_gap1"))
  } finally { t.stop() }
})

test("descendants: only spend paths mark roots for an undetermined chain, never permission.evaluate (S2)", async () => {
  const sessions: Record<string, { parentID?: string }> = {}
  for (let depth = 0; depth < 30; depth++) sessions[`ses_deep${depth}`] = { parentID: `ses_deep${depth + 1}` }
  const t = await start({ check: ["blocked"] }, sessions)
  try {
    await t.attach()
    await t.emit(created(MANAGED))
    for (const action of ["read", "webfetch", "execute"]) {
      const event = { sessionID: "ses_deep0", action, resources: [], effect: "ask" } as any
      await t.hooks["permission.evaluate"](event)
      assert.equal(event.effect, "ask")
    }
    assert.ok(t.lookups.length > 0)  // the chain was walked and stopped at its limit
    assert.deepEqual(pick(await t.descendants()), { complete: true, reason: null })
    await t.hooks["session.model.request"](modelRequest("ses_deep0"))  // a spend path: interactive, but unvouched
    assert.deepEqual(pick(await t.descendants()), { complete: false, reason: "lineage_lookup_failed" })
  } finally { t.stop() }
})

test("descendants: a subagent spawn on an undetermined chain marks roots too (S2)", async () => {
  const t = await start({ check: ["blocked"] })
  try {
    await t.attach()
    await t.emit(created(MANAGED))
    t.state.failLookup = true
    const event = { sessionID: "ses_unknown3", action: "read", resources: [], effect: "ask" } as any
    await t.hooks["permission.evaluate"](event)
    assert.deepEqual(pick(await t.descendants()), { complete: true, reason: null })
    await t.hooks["tool.execute.before"](spawnCall("ses_unknown3", "claude-agent/haiku#low"))
    assert.deepEqual(pick(await t.descendants()), { complete: false, reason: "lineage_lookup_failed" })
  } finally { t.stop() }
})

test("descendants: a root whose creation the shim did not see after attach is not vouched for", async () => {
  const t = await start({ check: ["blocked"], known_sessions: [MANAGED] }, {}, { pendingMs: 60000 })
  try {
    await t.emit(created(MANAGED))  // created before attach: children may have come and gone unrecorded
    await assert.rejects(t.hooks["session.model.request"](modelRequest(MANAGED)), refused("attach_required"))
    await t.emit(created("ses_early1", MANAGED))  // recorded while pending, so attach makes it a descendant
    await t.attach()
    const early = await t.descendants()
    assert.deepEqual([early.complete, early.reason], [false, "root_creation_unobserved"])
    assert.deepEqual(early.descendants, [link("ses_early1", MANAGED)])
  } finally { t.stop() }
  const again = await start({ check: ["blocked"] })
  try {
    await again.attach()
    await again.emit(created(MANAGED))
    await again.emit(created("ses_kid1", MANAGED))
    assert.equal((await again.descendants()).complete, true)
    await again.attach()  // the same attempt attaching again keeps what it saw
    assert.equal((await again.descendants()).complete, true)
    await again.detach()
    await again.attach()  // after detach (or a plugin restart) the root's creation is in the past
    await again.emit(created("ses_kid2", "ses_kid1"))
    const answer = await again.descendants()
    assert.deepEqual([answer.complete, answer.reason], [false, "root_creation_unobserved"])
    assert.deepEqual(answer.descendants, byId([link("ses_kid1", MANAGED), link("ses_kid2", "ses_kid1")]))
    assert.deepEqual(answer.cache, { recorded: 1, retired: 1 })
  } finally { again.stop() }
})

test("descendants: interactive sessions are untouched by the RPC and never listed", async () => {
  const t = await start({ check: ["blocked"] })
  try {
    await t.attach()
    await t.emit(created(MANAGED))
    await t.emit(created("ses_iroot1"))
    await t.emit(created("ses_ikid1", "ses_iroot1"))
    await t.emit(created("ses_orphan1", "ses_ghost1"))
    await t.descendants()
    for (const session of ["ses_iroot1", "ses_ikid1", "ses_orphan1"]) await assertUntouched(t, session)
    assert.deepEqual((await t.descendants()).descendants, [])
    assert.equal(t.fake.requests("check").length, 0)
  } finally { t.stop() }
})

test("the real child: descendants before record complete and detach, refused afterwards", async () => {
  const stores = realStores(true)
  const t = await realPlugin(stores)
  try {
    const { authority, session_id: session } = stores.command("dispatch", { attempt_id: "real-a1", request: REAL_REQUEST })
    const input = { session_id: session, attempt_id: "real-a1", authority }
    await t.rpc("attach", { ...input, funding_pool: "sub", model: "claude-agent/haiku#low" })
    await t.emit(created(session))
    await t.emit(created("ses_realkid1", session))
    await t.emit(created("ses_realcmd1", "ses_realkid1"))
    await t.hooks["session.model.request"](modelRequest("ses_realkid1"))
    const expected = { state: "ok", root: session, descendants: byId([link("ses_realkid1", session),
      link("ses_realcmd1", "ses_realkid1")]), truncated: false, complete: true, reason: null, cache: { recorded: 2, retired: 0 } }
    assert.deepEqual(await t.rpc("descendants", input), expected)
    await assert.rejects(Promise.resolve().then(() => t.rpc("descendants", { ...input, authority: "x".repeat(43) })),
      refused("descendants_refused"))
    assert.equal(stores.command("record", { event: { event_id: "e1", attempt_id: "real-a1", kind: "complete" } }).state, "recorded")
    assert.deepEqual(await t.rpc("descendants", input), expected)  // still attached: the order is account, record, detach
    await t.rpc("detach", input)
    await assert.rejects(Promise.resolve().then(() => t.rpc("descendants", input)), refused("descendants_refused"))
  } finally { t.stop() }
})

test("TS extraction agrees with the Python adapter on the parity cases", { skip: !process.env.OPENCODE_PLUGIN_PARITY }, () => {
  const cases = JSON.parse(readFileSync(process.env.OPENCODE_PLUGIN_PARITY!, "utf8"))
  for (const item of cases.env) assert.equal(credentialEnvName(item.name), item.credential, item.name)
  for (const item of cases.models) assert.deepEqual(routeFacts(item.model), item.facts, JSON.stringify(item.model))
  for (const item of cases.spawns) assert.deepEqual(spawnModel(item.model), item.requested, item.model)
  for (const item of cases.sessions) assert.equal(derivedSession(item.id), item.derived, item.id)
  const configured = extraPaths([".cursor", "Tools\\Agents"])
  for (const item of cases.roots) assert.deepEqual(configRoots(item.env, item.home), item.roots, JSON.stringify(item.env))
  for (const item of cases.paths) {
    const where = `${JSON.stringify(item.resource)?.slice(0, 60)} in ${item.directory}`
    assert.equal(protectedPath(item.resource, [], item.directory, cases.table_roots), item.protected, where)
    assert.equal(protectedPath(item.resource, configured, item.directory, cases.table_roots), item.configured, where)
  }
  for (const item of cases.extras) assert.deepEqual(extraPaths(item.option), item.paths, JSON.stringify(item.option))
  for (const item of cases.denials) {
    assert.equal(managedDenial(item.action, item.resources), item.deny, `${item.action} ${JSON.stringify(item.resources)}`)
  }
})
