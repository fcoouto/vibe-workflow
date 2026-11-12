// Run with `node --test tests/opencode_workflow/plugin.test.ts` (Node strips the types); tests/test_opencode_workflow.py wraps it.
import { test } from "node:test"
import assert from "node:assert/strict"
import plugin, { createWorkflowPlugin, exitStatus, routeOf } from "../../runtime/adapters/opencode_workflow/index.ts"

type Hooks = Record<string, (event: any) => Promise<void> | void>
const ROOT = "ses_root"
const CHILD = "ses_child"
const MODEL = { providerID: "p", id: "m", variant: "low" }
const STRONG = { providerID: "p", id: "m", variant: "high" }
const STREAM_LOST = Symbol("stream lost")
const tick = () => new Promise((resolve) => setImmediate(resolve))
const settle = async () => { for (let i = 0; i < 5; i++) await tick() }

function fakeCtx(directory: string, sessions: Record<string, { parentID?: string }> = {}) {
  const hooks: Hooks = {}
  const tools: any[] = []
  const lookups: string[] = []
  const listeners: ((event: unknown) => void)[] = []
  const subscribe = ({ signal }: { signal?: AbortSignal } = {}) => {
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
  const domain = (name: string) => ({
    hook: async (hook: string, callback: any) => { hooks[`${name}.${hook}`] = callback; return { dispose: async () => {} } },
  })
  const ctx = {
    location: { directory },
    session: {
      ...domain("session"),
      get: async ({ sessionID }: { sessionID: string }) => {
        lookups.push(sessionID)
        if (!(sessionID in sessions)) throw new Error("not found")
        return { id: sessionID, ...sessions[sessionID] }
      },
    },
    tool: {
      ...domain("tool"),
      transform: async (callback: any) => {
        callback({ add: (tool: any) => tools.push(tool) })
        return { dispose: async () => {} }
      },
    },
    permission: domain("permission"),
    event: { subscribe },
  }
  const emit = async (event: unknown) => {
    for (const listener of listeners) listener(event)
    await settle()
  }
  return { ctx, hooks, tools, lookups, emit }
}

const report = (effective: boolean, values: Record<string, unknown> = {}) =>
  ({ ok: true, features: { escalation_gate: { effective, values } } })

async function start(options: { feature?: unknown; directory?: string; sessions?: Record<string, { parentID?: string }>,
  featureStatus?: (directory: string) => Promise<unknown> } = {}) {
  const fake = fakeCtx(options.directory ?? "/w", options.sessions)
  const instance = createWorkflowPlugin({
    featureStatus: options.featureStatus ?? (async () => options.feature ?? report(true)),
  })
  const cleanup = await instance.setup(fake.ctx)
  await settle()
  await fake.emit({ type: "session.created", data: { sessionID: ROOT } })
  return { ...fake, cleanup }
}

let calls = 0
const permissionEvent = (sessionID: string, resources: unknown, call: string, effect = "allow", action = "edit") =>
  ({ sessionID, action, resources, source: { type: "tool", messageID: "msg_1", id: call }, effect } as any)

async function edit(t: Awaited<ReturnType<typeof start>>, resources: unknown, session = ROOT, tool = "edit") {
  const call = `call_${++calls}`
  const event = permissionEvent(session, resources, call)
  await t.hooks["permission.evaluate"](event)
  if (event.effect === "allow") {
    await t.hooks["tool.execute.after"]({ sessionID: session, id: call, tool, input: {}, status: "completed",
      result: { content: "ok" } })
  }
  return event
}

async function failCheck(t: Awaited<ReturnType<typeof start>>, session = ROOT) {
  await t.hooks["tool.execute.after"]({ sessionID: session, id: `sh_${++calls}`, tool: "shell",
    input: { command: "pytest -q" }, status: "completed", result: { content: "1 failed", metadata: { exit: 1 } } })
}

async function trip(t: Awaited<ReturnType<typeof start>>, resources: unknown = ["a.ts"]) {
  await t.hooks["session.model.request"]({ sessionID: ROOT, kind: "primary", model: MODEL })
  for (let i = 0; i < 3; i++) {
    await edit(t, resources)
    await failCheck(t)
  }
}

async function diagnose(t: Awaited<ReturnType<typeof start>>, model = STRONG, outcome = "succeeded", id = CHILD) {
  await t.emit({ type: "session.created", data: { sessionID: id, parentID: ROOT } })
  await t.hooks["session.model.request"]({ sessionID: id, kind: "primary", model })
  await t.emit({ type: `session.execution.${outcome}`, data: { sessionID: id } })
}

const status = async (t: Awaited<ReturnType<typeof start>>, session = ROOT) =>
  JSON.parse((await t.tools[0].execute({}, { sessionID: session })).content)

test("inactive when the feature is off, missing, unreadable or still loading", async () => {
  for (const feature of [report(false), { ok: true, features: {} }, { ok: false }, report(true, { ladder: "bad" })]) {
    const t = await start({ feature })
    await trip(t)
    const event = await edit(t, ["a.ts"])
    assert.equal(event.effect, "allow")
    assert.equal(t.tools.length, 0)
    t.cleanup()
  }
})

test("setup returns before feature status resolves", async () => {
  const t = await start({ featureStatus: () => new Promise(() => {}) })
  await trip(t)
  assert.equal((await edit(t, ["a.ts"])).effect, "allow")
  assert.equal(t.tools.length, 0)
  t.cleanup()
})

test("status tool is registered only once active", async () => {
  const t = await start()
  assert.equal(t.tools.length, 1)
  assert.equal(t.tools[0].name, "escalation_gate_status")
  assert.deepEqual(await status(t), { held: [], lastError: null })
  t.cleanup()
})

test("trip and hold end to end through hooks", async () => {
  const t = await start()
  await trip(t)
  const event = await edit(t, ["a.ts"])
  assert.equal(event.effect, "deny")
  assert.match(event.message, /^escalation gate: a\.ts had 2 repair rounds/)
  assert.doesNotMatch(event.message, /\/w\//)
  assert.equal((await status(t)).held[0].file, "/w/a.ts")
  t.cleanup()
})

test("a configured deny is never changed", async () => {
  const t = await start()
  await trip(t)
  const event = permissionEvent(ROOT, ["a.ts"], "call_deny", "deny")
  event.message = "configured"
  await t.hooks["permission.evaluate"](event)
  assert.equal(event.effect, "deny")
  assert.equal(event.message, "configured")
  t.cleanup()
})

test("denied edits are not counted; any completed call with remembered files is", async () => {
  const t = await start()
  await t.hooks["session.model.request"]({ sessionID: ROOT, kind: "primary", model: MODEL })
  for (let i = 0; i < 3; i++) {
    const call = `call_err_${i}`
    await t.hooks["permission.evaluate"](permissionEvent(ROOT, ["a.ts"], call))
    await t.hooks["tool.execute.after"]({ sessionID: ROOT, id: call, tool: "edit", input: {}, status: "error",
      error: { message: "rejected" } })
    await failCheck(t)
  }
  assert.equal((await edit(t, ["a.ts"])).effect, "allow")
  for (let i = 0; i < 3; i++) {
    await edit(t, ["b.ts"], ROOT, "apply_patch")
    await failCheck(t)
  }
  assert.equal((await edit(t, ["b.ts"])).effect, "deny")
  t.cleanup()
})

test("resources normalize to one absolute file", async () => {
  const t = await start({ directory: "/w/sub" })
  await t.hooks["session.model.request"]({ sessionID: ROOT, kind: "primary", model: MODEL })
  for (const resource of ["../a.ts", "/w/a.ts", "./../a.ts"]) {
    await edit(t, [resource])
    await failCheck(t)
  }
  assert.equal((await edit(t, ["../a.ts"])).effect, "deny")
  assert.deepEqual((await status(t)).held.map((entry: any) => entry.file), ["/w/a.ts"])
  t.cleanup()
})

test("unknown sessions resolve through session.get lineage", async () => {
  const t = await start({ sessions: { ses_old: {}, ses_kid: { parentID: "ses_old" } } })
  // A resumed root and a child whose hooks arrive before any creation event.
  await t.hooks["session.model.request"]({ sessionID: "ses_old", kind: "primary", model: MODEL })
  for (let i = 0; i < 3; i++) {
    await edit(t, ["a.ts"], "ses_old")
    await failCheck(t, "ses_old")
  }
  assert.equal((await edit(t, ["a.ts"], "ses_kid")).effect, "deny")
  assert.ok(t.lookups.includes("ses_kid"))
  t.cleanup()
})

test("only succeeded executions release; failed and interrupted do not", async () => {
  const t = await start()
  await trip(t)
  await diagnose(t, STRONG, "failed", "ses_c1")
  await diagnose(t, STRONG, "interrupted", "ses_c2")
  assert.equal((await edit(t, ["a.ts"])).effect, "deny")
  await diagnose(t, STRONG, "succeeded", "ses_c3")
  assert.equal((await edit(t, ["a.ts"])).effect, "allow")
  t.cleanup()
})

test("only primary model requests set levels", async () => {
  const t = await start()
  await trip(t)
  await t.emit({ type: "session.created", data: { sessionID: CHILD, parentID: ROOT } })
  await t.hooks["session.model.request"]({ sessionID: CHILD, kind: "title", model: STRONG })
  await t.hooks["session.model.request"]({ sessionID: CHILD, kind: "primary", model: MODEL })
  await t.hooks["session.model.request"]({ sessionID: CHILD, kind: "compaction", model: STRONG })
  await t.emit({ type: "session.execution.succeeded", data: { sessionID: CHILD } })
  assert.equal((await edit(t, ["a.ts"])).effect, "deny")
  t.cleanup()
})

test("root prompt resets and appends the note only while held; child prompt does not", async () => {
  const t = await start()
  const idle = { sessionID: ROOT, prompt: { text: "reset the gate please" } }
  await t.hooks["session.prompt"](idle)
  assert.equal(idle.prompt.text, "reset the gate please")
  await trip(t)
  await t.emit({ type: "session.created", data: { sessionID: CHILD, parentID: ROOT } })
  const child = { sessionID: CHILD, prompt: { text: "reset the counter for a.ts" } }
  await t.hooks["session.prompt"](child)
  assert.equal(child.prompt.text, "reset the counter for a.ts")
  assert.equal((await edit(t, ["a.ts"])).effect, "deny")
  const root = { sessionID: ROOT, prompt: { text: "that one is unrelated, reset the counter for a.ts" } }
  await t.hooks["session.prompt"](root)
  assert.match(root.prompt.text, /\n\n\[escalation gate: counter reset by user for a\.ts\]$/)
  assert.equal((await edit(t, ["a.ts"])).effect, "allow")
  t.cleanup()
})

test("hook errors allow and record lastError", async () => {
  const t = await start()
  await trip(t)
  const hostile = permissionEvent(ROOT, ["a.ts"], "call_x")
  Object.defineProperty(hostile, "resources", { get() { throw new Error("boom SECRET") } })
  await t.hooks["permission.evaluate"](hostile)
  assert.equal(hostile.effect, "allow")
  const { lastError } = await status(t)
  assert.equal(lastError, "permission_evaluate_failed")
  t.cleanup()
})

test("exitStatus reads metadata.exit and metadata.exitCode only", () => {
  assert.equal(exitStatus({ result: { metadata: { exit: 2 } } }), 2)
  assert.equal(exitStatus({ result: { metadata: { exitCode: 0 } } }), 0)
  assert.equal(exitStatus({ result: { content: "exit code 1" } }), null)
  assert.equal(exitStatus({ result: { metadata: { exit: "1" } } }), null)
  assert.equal(exitStatus(null), null)
  assert.deepEqual(routeOf(MODEL), { provider: "p", model: "m", variant: "low" })
  assert.deepEqual(routeOf({ providerID: "p", id: "m" }), { provider: "p", model: "m", variant: null })
  assert.equal(routeOf({ id: "m" }), null)
})

test("nothing is written to prompts or messages while nothing is held", async () => {
  const t = await start()
  const event = await edit(t, ["a.ts"])
  assert.equal(event.effect, "allow")
  assert.equal(event.message, undefined)
  const after = { sessionID: ROOT, id: "sh_quiet", tool: "shell", input: { command: "pytest" }, status: "completed",
    result: { content: "ok", metadata: { exit: 0 } } }
  await t.hooks["tool.execute.after"](after)
  assert.deepEqual(after.result, { content: "ok", metadata: { exit: 0 } })
  assert.equal(typeof plugin.setup, "function")
  t.cleanup()
})

test("final review: an approved stop-stage edit clears the hold; a rejected one does not", async () => {
  const t = await start()
  const MAX = { providerID: "p", id: "m", variant: "max" }
  await t.hooks["session.model.request"]({ sessionID: ROOT, kind: "primary", model: MAX })
  for (let i = 0; i < 3; i++) { await edit(t, ["a.ts"]); await failCheck(t) }
  await diagnose(t, MAX, "succeeded", "ses_reeval")  // re-evaluation accepted
  for (let i = 0; i < 2; i++) { await edit(t, ["a.ts"]); await failCheck(t) }
  const rejected = permissionEvent(ROOT, ["a.ts"], "call_rejected")
  await t.hooks["permission.evaluate"](rejected)
  assert.equal(rejected.effect, "ask")
  await t.hooks["tool.execute.after"]({ sessionID: ROOT, id: "call_rejected", tool: "edit", input: {}, status: "error",
    error: { message: "rejected" } })
  const approved = permissionEvent(ROOT, ["a.ts"], "call_approved")
  await t.hooks["permission.evaluate"](approved)
  assert.equal(approved.effect, "ask")
  await t.hooks["tool.execute.after"]({ sessionID: ROOT, id: "call_approved", tool: "edit", input: {}, status: "completed",
    result: { content: "ok" } })
  assert.equal((await edit(t, ["a.ts"])).effect, "allow")
  t.cleanup()
})

test("final review: a follow-up to a child first seen through a lookup does not release", async () => {
  const t = await start({ sessions: { ses_oldchild: { parentID: ROOT } } })
  await trip(t)
  await t.hooks["session.model.request"]({ sessionID: "ses_oldchild", kind: "primary", model: STRONG })
  await t.emit({ type: "session.execution.succeeded", data: { sessionID: "ses_oldchild" } })
  assert.equal((await edit(t, ["a.ts"])).effect, "deny")
  t.cleanup()
})

test("final review: a lost event stream fails open and is reported", async () => {
  const t = await start()
  await trip(t)
  await t.emit(STREAM_LOST)
  assert.equal((await edit(t, ["a.ts"])).effect, "allow")
  assert.equal((await status(t)).lastError, "event_stream_lost")
  t.cleanup()
})
