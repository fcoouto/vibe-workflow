// Run with `node --test tests/opencode_workflow/plugin.test.ts` (Node strips the types); tests/test_opencode_workflow.py wraps it.
import { test } from "node:test"
import assert from "node:assert/strict"
import plugin, { checkFailed, createWorkflowPlugin, exitStatus, routeOf } from "../../runtime/adapters/opencode_workflow/index.ts"

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

for (const mode of ["throws", "ends", "missing"] as const) {
  test(`event-channel lifecycle: ${mode} before feature resolution stays fail-open`, async () => {
    const fake = fakeCtx("/w", { [ROOT]: {} })
    const ctx: any = fake.ctx
    let subscriptions = 0
    if (mode === "missing") delete ctx.event.subscribe
    else ctx.event.subscribe = () => {
      subscriptions++
      return { async *[Symbol.asyncIterator]() {
        if (mode === "throws") throw new Error("synthetic event stream failure")
      } }
    }
    let resolveFeature!: (value: unknown) => void
    const pending = new Promise<unknown>((resolve) => { resolveFeature = resolve })
    const instance = createWorkflowPlugin({ featureStatus: () => pending })
    const cleanup = await instance.setup(ctx)
    const t = { ...fake, cleanup }
    try {
      await settle()
      assert.equal(t.tools.length, 0)
      resolveFeature(report(true))
      await settle()
      await trip(t)
      assert.equal((await edit(t, ["a.ts"])).effect, "allow")
      assert.deepEqual(await status(t), { held: [], lastError: "event_stream_lost" })
      assert.equal(subscriptions, mode === "missing" ? 0 : 1)
    } finally { cleanup() }
  })
}

test("event-channel lifecycle: cleanup before feature resolution prevents late activation", async () => {
  const fake = fakeCtx("/w", { [ROOT]: {} })
  let resolveFeature!: (value: unknown) => void
  const pending = new Promise<unknown>((resolve) => { resolveFeature = resolve })
  const instance = createWorkflowPlugin({ featureStatus: () => pending })
  const cleanup = await instance.setup(fake.ctx)
  const t = { ...fake, cleanup }
  try {
    await settle()
    cleanup()
    resolveFeature(report(true))
    await settle()
    await trip(t)
    assert.equal((await edit(t, ["a.ts"])).effect, "allow")
    assert.equal(t.tools.length, 0)
  } finally { cleanup() }
})

test("event-channel lifecycle: cleanup disables an active hold without reporting stream loss", async () => {
  const t = await start()
  try {
    await trip(t)
    assert.equal((await edit(t, ["a.ts"])).effect, "deny")
    t.cleanup()
    await settle()
    await trip(t)
    assert.equal((await edit(t, ["a.ts"])).effect, "allow")
    assert.deepEqual(await status(t), { held: [], lastError: null })
  } finally { t.cleanup() }
})

test("event-channel lifecycle: a healthy channel permits deferred activation and normal release", async () => {
  let resolveFeature!: (value: unknown) => void
  const pending = new Promise<unknown>((resolve) => { resolveFeature = resolve })
  const t = await start({ featureStatus: () => pending })
  try {
    await trip(t)
    assert.equal((await edit(t, ["a.ts"])).effect, "allow")
    assert.equal(t.tools.length, 0)
    resolveFeature(report(true))
    await settle()
    await t.emit({ type: "session.created", data: { sessionID: ROOT } })
    await trip(t)
    assert.equal((await edit(t, ["a.ts"])).effect, "deny")
    await diagnose(t)
    assert.equal((await edit(t, ["a.ts"])).effect, "allow")
    assert.deepEqual(await status(t), { held: [], lastError: null })
  } finally { t.cleanup() }
})

test("event-channel lifecycle: stream loss keeps a disabled feature quiet", async () => {
  let resolveFeature!: (value: unknown) => void
  const pending = new Promise<unknown>((resolve) => { resolveFeature = resolve })
  const t = await start({ featureStatus: () => pending })
  try {
    await t.emit(STREAM_LOST)
    resolveFeature(report(false))
    await settle()
    await trip(t)
    assert.equal((await edit(t, ["a.ts"])).effect, "allow")
    assert.equal(t.tools.length, 0)
  } finally { t.cleanup() }
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

const unittestCommand = "python3 -m unittest -q"
const pipedUnittest = `${unittestCommand} 2>&1 | tail -5`
const unittestRun = "Ran 4 tests in 0.001s\n\n"
const unittestFailure = `${unittestRun}FAILED (failures=1)\n`
const unittestSuccess = `${unittestRun}OK\n`
const unexpectedSuccess = "Ran 1 test in 0.001s\n\nFAILED (unexpected successes=1)\n"
const historicalSample = `Captured expected runner output:\nFAILED (failures=1)\n${unittestSuccess}`
const checkEvent = (content: unknown, command = pipedUnittest) => ({ sessionID: ROOT, id: `check_${++calls}`,
  tool: "shell", input: { command }, status: "completed", result: { content, metadata: { exit: 0 } } })

test("qualification regression: unittest failure survives a successful output pipeline", async () => {
  const t = await start()
  await t.hooks["session.model.request"]({ sessionID: ROOT, kind: "primary", model: MODEL })
  for (let i = 0; i < 3; i++) {
    await edit(t, ["a.ts"])
    await t.hooks["tool.execute.after"]({ sessionID: ROOT, id: `piped_${i}`, tool: "shell",
      input: { command: "python3 -m unittest -q 2>&1 | tail -5" }, status: "completed",
      result: { content: [{ type: "text", text: "Ran 4 tests in 0.001s\n\nFAILED (failures=1)\n" }],
        metadata: { exit: 0 } } })
  }
  assert.equal((await edit(t, ["a.ts"])).effect, "deny")
  t.cleanup()
})

test("qualification regression: only an actual unittest failure footer supplements exit status", () => {
  assert.equal(checkFailed(checkEvent(unittestFailure)), true)
  assert.equal(checkFailed(checkEvent(`${unittestRun}FAILED (errors=2, skipped=1)\n`)), true)
  assert.equal(checkFailed(checkEvent(`${unittestRun}FAILED (failures=0, errors=1)\n`)), true)
  assert.equal(checkFailed(checkEvent(`${unittestRun}FAILED (failures=0)\n`)), false)
  assert.equal(checkFailed(checkEvent(unittestSuccess)), false)
  assert.equal(checkFailed(checkEvent("Expected example: FAILED (failures=1)")), false)
  assert.equal(checkFailed(checkEvent("test_failed_detection passed")), false)
  assert.equal(checkFailed(checkEvent("FAILED (errors=1)\n")), false)
})

test("masked-exit escalation: historical and sample output is not a current failure", () => {
  for (const text of [historicalSample, unittestFailure + unittestSuccess,
    `Expected example: FAILED (failures=1)\n${unittestSuccess}`, "FAILED (failures=1)\n"]) {
    assert.equal(checkFailed(checkEvent(text)), false)
  }
})

test("masked-exit escalation: mixed and unsupported commands have no output-only attribution", () => {
  for (const command of [`cat old-unittest.log; ${unittestCommand}`, `${unittestCommand}; cat old-unittest.log`,
    "cat old-unittest.log", `${unittestCommand} | tail -5 old-unittest.log`, `${unittestCommand} | grep FAILED`,
    `${unittestCommand} | tail -5 | tail -5`, `${unittestCommand}; echo done`, `${unittestCommand} || true`,
    `${unittestCommand} &`, `${unittestCommand};`, `${unittestCommand} > output.log`,
    `${unittestCommand} 2>&1 2>&1 | tail -5`, `${unittestCommand} 'test_a'`, `${unittestCommand} "test_a"`,
    `${unittestCommand} $(cat args)`, `${unittestCommand} \`cat args\``, `${unittestCommand} $ARGS`,
    `${unittestCommand} test_*`, `${unittestCommand} <<EOF\nEOF`, `${unittestCommand}\n${unittestCommand}`]) {
    assert.equal(checkFailed(checkEvent(unittestFailure, command)), false, command)
  }
  assert.equal(checkFailed(checkEvent(unittestFailure + unittestSuccess, `cat old-unittest.log; ${unittestCommand}`)), false)
  assert.equal(checkFailed(checkEvent(unittestSuccess + unittestFailure, `${unittestCommand}; cat old-unittest.log`)), false)
})

for (const command of ["env -C unittest cat old-unittest.log", "npx -p unittest cat old-unittest.log"]) {
  test(`masked-exit escalation: wrapper option values do not identify checks: ${command}`, () => {
    const event = checkEvent(unittestFailure, command)
    assert.equal(checkFailed(event), false)
    event.result.metadata.exit = 1
    assert.equal(checkFailed(event), true)
    assert.equal(checkFailed({ ...event, status: "error", result: null }), true)
  })
}

test("masked-exit escalation: wrapper options remain ambiguous through literal prefixes", () => {
  for (const command of ["CI=1 env -C unittest cat old-unittest.log", "env CI=1 -C unittest cat old-unittest.log",
    "env CI=1 npx -p unittest cat old-unittest.log", "time env -C unittest cat old-unittest.log",
    "uv run env -C unittest cat old-unittest.log", "./env -C unittest cat old-unittest.log",
    "env --chdir unittest cat old-unittest.log", "command -p unittest cat old-unittest.log"]) {
    assert.equal(checkFailed(checkEvent(unittestFailure, command)), false, command)
  }
  assert.equal(checkFailed(checkEvent(unittestFailure + unittestFailure,
    `${pipedUnittest}; env -C unittest cat old-unittest.log`)), false)
})

test("masked-exit escalation: repeated wrapper log views never create a hold", async () => {
  for (const command of ["env -C unittest cat old-unittest.log", "npx -p unittest cat old-unittest.log"]) {
    const t = await start()
    try {
      await t.hooks["session.model.request"]({ sessionID: ROOT, kind: "primary", model: MODEL })
      for (let i = 0; i < 3; i++) {
        assert.equal((await edit(t, ["a.ts"])).effect, "allow")
        await t.hooks["tool.execute.after"](checkEvent(unittestFailure, command))
      }
      assert.equal((await edit(t, ["a.ts"])).effect, "allow", command)
      assert.deepEqual(await status(t), { held: [], lastError: null })
    } finally { t.cleanup() }
  }
})

test("masked-exit escalation: literal checks and stdin-only tail preserve genuine failures", () => {
  for (const command of [unittestCommand, pipedUnittest, `${unittestCommand} | tail -n 5`,
    `env CI=1 ${unittestCommand} 2>&1 | tail -5`, `CI=1 ${pipedUnittest}`,
    `env CI=1 PYTHONHASHSEED=0 ${pipedUnittest}`, `time env CI=1 ${pipedUnittest}`]) {
    assert.equal(checkFailed(checkEvent(unittestFailure, command)), true, command)
  }
  assert.equal(checkFailed(checkEvent(unittestFailure.replaceAll("\n", "\r\n"))), true)
  const noExit: any = checkEvent(unittestFailure)
  delete noExit.result.metadata
  assert.equal(checkFailed(noExit), true)
})

test("masked-exit escalation: all complete runs must correspond to check invocations", () => {
  for (const separator of [";", "&&", "||"]) {
    const command = `${pipedUnittest} ${separator} ${unittestCommand} test_b`
    for (const [text, expected] of [[unittestFailure + unittestSuccess, true], [unittestSuccess + unittestFailure, true],
      [unittestFailure + unittestFailure, true], [unittestSuccess + unittestSuccess, false], [unittestFailure, false],
      [unittestFailure + unittestSuccess + unittestSuccess, false],
      [unittestFailure + `${unittestRun}FAILED (errors=-1)\n`, false]] as const) {
      assert.equal(checkFailed(checkEvent(text, command)), expected, `${separator}: ${text}`)
    }
  }
})

test("masked-exit escalation: unexpected successes fail but expected failures do not", () => {
  assert.equal(checkFailed(checkEvent(unexpectedSuccess)), true)
  assert.equal(checkFailed(checkEvent("Ran 1 test in 0.001s\n\nOK (expected failures=1)\n")), false)
  assert.equal(checkFailed(checkEvent(`${unittestRun}FAILED (expected failures=1)\n`)), false)
  assert.equal(checkFailed(checkEvent(`${unittestRun}OK (unexpected successes=1)\n`)), false)
})

test("masked-exit escalation: the entire count list must be well formed", () => {
  for (const counts of ["failures=0, errors=0, unexpected successes=0", "skipped=1", "expected failures=1",
    "garbage, failures=1", "failures=1, unknown=1", "failures=1, failures=2", "errors=-1", "errors=1.5",
    "failures=01", "failures=+1", "failures=1,errors=1", "failures=1, ", "", "failures=1, errors=NaN"]) {
    assert.equal(checkFailed(checkEvent(`${unittestRun}FAILED (${counts})\n`)), false, counts)
  }
  for (const counts of ["errors=2, skipped=1", "failures=0, errors=1", "unexpected successes=1",
    "failures=1, errors=2, skipped=1, expected failures=1, unexpected successes=1"]) {
    assert.equal(checkFailed(checkEvent(`${unittestRun}FAILED (${counts})\n`)), true, counts)
  }
})

test("masked-exit escalation: malformed and truncated footers remain unknown", () => {
  for (const text of ["Ran 4 tests\n\nFAILED (failures=1)\n", "FAILED (errors=1)\n",
    `${unittestRun}FAILED (failures=1`, `${unittestRun}FAILED\n`, `${unittestRun}FAILED (failures=1) extra\n`,
    `${unittestFailure}unrelated trailing text\n`, "Ran four tests in 0.001s\n\nFAILED (failures=1)\n",
    `${unittestRun}FAILED (errors=-1)\n${unittestFailure}`]) {
    assert.equal(checkFailed(checkEvent(text)), false, text)
  }
})

test("masked-exit escalation: text parts preserve boundaries and reject malformed content", () => {
  assert.equal(checkFailed(checkEvent([{ type: "text", text: unittestFailure }])), true)
  assert.equal(checkFailed(checkEvent([null, {}, { type: "image", text: unittestFailure },
    { type: "text", text: 1 }, { type: "text", text: unittestFailure } ])), true)
  assert.equal(checkFailed(checkEvent([{ type: "text", text: unittestFailure },
    { type: "text", text: unittestSuccess }])), false)
  assert.equal(checkFailed(checkEvent([{ type: "text", text: unittestFailure },
    { type: "text", text: unittestSuccess }], `${unittestCommand}; ${unittestCommand}`)), true)
  for (const content of [null, undefined, 1, {}, [null, {}, { type: "text", text: 1 }],
    [{ type: "image", text: unittestFailure }],
    [{ type: "text", text: `${unittestRun}FAILED (fail` }, { type: "text", text: "ures=1)\n" }],
    [{ type: "text", text: "Ra" }, { type: "text", text: "n 4 tests in 0.001s\n\nFAILED (failures=1)\n" }]]) {
    assert.equal(checkFailed(checkEvent(content)), false)
  }
})

test("masked-exit escalation: status and tool errors bypass output inference unchanged", () => {
  assert.equal(checkFailed({ status: "error" }), true)
  assert.equal(checkFailed({ result: { metadata: { exit: 1 } } }), true)
  assert.equal(checkFailed({ result: { metadata: { exitCode: 2 } } }), true)
  const mixed = checkEvent({}, `cat old-unittest.log; ${unittestCommand}`)
  mixed.result.metadata.exit = 1
  assert.equal(checkFailed(mixed), true)
  assert.equal(checkFailed({ ...mixed, status: "error", result: null }), true)
  assert.equal(checkFailed({ result: { metadata: { exit: "1" } } }), false)
  assert.equal(checkFailed(null), false)
  assert.equal(checkFailed({ ...checkEvent(unittestFailure), input: {} }), false)
  assert.equal(checkFailed({ ...checkEvent(unittestFailure), status: "pending" }), false)
})

for (const [name, content, command] of [
  ["unexpected-success failures", unexpectedSuccess, pipedUnittest],
  ["environment-assigned unexpected-success failures", unexpectedSuccess, `env CI=1 ${pipedUnittest}`],
  ["multiple failed runs once per shell call", unittestFailure + unittestFailure, `${pipedUnittest}; ${pipedUnittest}`],
] as const) {
  test(`masked-exit escalation: hooks hold after ${name}`, async () => {
    const t = await start()
    try {
      await t.hooks["session.model.request"]({ sessionID: ROOT, kind: "primary", model: MODEL })
      for (let i = 0; i < 3; i++) {
        assert.equal((await edit(t, ["a.ts"])).effect, "allow")
        await t.hooks["tool.execute.after"](checkEvent(content, command))
      }
      assert.equal((await edit(t, ["a.ts"])).effect, "deny")
      assert.equal((await status(t)).held[0].rounds, 2)
      assert.equal((await status(t)).lastError, null)
    } finally { t.cleanup() }
  })
}

test("masked-exit escalation: negative-output controls never create a hold", async () => {
  for (const [content, command] of [[historicalSample, unittestCommand], [unittestFailure + unittestSuccess, unittestCommand],
    [unittestFailure + unittestSuccess, `cat old-unittest.log; ${unittestCommand}`],
    [unittestSuccess + unittestFailure, `${unittestCommand}; cat old-unittest.log`],
    [`${unittestRun}OK (expected failures=1)\n`, pipedUnittest],
    [`${unittestRun}FAILED (failures=0, errors=0, unexpected successes=0)\n`, pipedUnittest],
    [`${unittestRun}FAILED (garbage, failures=1)\n`, pipedUnittest],
    [unittestFailure, `${unittestCommand} | tail -5 old-unittest.log`]]) {
    const t = await start()
    try {
      for (let i = 0; i < 3; i++) {
        assert.equal((await edit(t, ["a.ts"])).effect, "allow")
        await t.hooks["tool.execute.after"](checkEvent(content, command))
      }
      assert.equal((await edit(t, ["a.ts"])).effect, "allow")
      assert.deepEqual(await status(t), { held: [], lastError: null })
    } finally { t.cleanup() }
  }
})

test("masked-exit escalation: successful and ambiguous output never clear an existing hold", async () => {
  const t = await start()
  try {
    await trip(t)
    const before = await status(t)
    for (const event of [checkEvent(unittestSuccess), checkEvent(historicalSample),
      checkEvent(unittestFailure + unittestSuccess, `cat old-unittest.log; ${unittestCommand}`)]) {
      await t.hooks["tool.execute.after"](event)
      assert.deepEqual(await status(t), before)
      assert.equal((await edit(t, ["a.ts"])).effect, "deny")
    }
  } finally { t.cleanup() }
})

test("masked-exit escalation: hooks use configured check patterns", async () => {
  const t = await start({ feature: report(true, { check_patterns: ["custom-check"] }) })
  try {
    for (let i = 0; i < 3; i++) {
      await edit(t, ["a.ts"])
      await t.hooks["tool.execute.after"](checkEvent(unittestFailure, "custom-check | tail -5"))
    }
    assert.equal((await edit(t, ["a.ts"])).effect, "deny")
  } finally { t.cleanup() }
})

test("masked-exit escalation: internal output errors remain fail-open", async () => {
  const t = await start()
  try {
    for (let i = 0; i < 3; i++) {
      await edit(t, ["a.ts"])
      const event = checkEvent(null)
      Object.defineProperty(event.result, "content", { get() { throw new Error("synthetic output error") } })
      await t.hooks["tool.execute.after"](event)
    }
    assert.equal((await edit(t, ["a.ts"])).effect, "allow")
    assert.deepEqual(await status(t), { held: [], lastError: "tool_execute_after_failed" })
  } finally { t.cleanup() }
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

test("probe order: no root creation event, child prompt before its creation event, then release", async () => {
  // Observed on OpenCode 2.2.6 (tests/fixtures/opencode_workflow/probes.json): the root's `session.created` never
  // arrives, and a subagent's prompt hook fires before the child's creation event.
  const fake = fakeCtx("/w", { ses_run: {}, ses_kid2: { parentID: "ses_run" } })
  const instance = createWorkflowPlugin({ featureStatus: async () => report(true) })
  const cleanup = await instance.setup(fake.ctx)
  await settle()
  const t = { ...fake, cleanup }
  await t.hooks["session.prompt"]({ sessionID: "ses_run", prompt: { text: "work" } })
  await t.hooks["session.model.request"]({ sessionID: "ses_run", kind: "primary", model: MODEL })
  for (let i = 0; i < 3; i++) {
    await edit(t as any, ["a.ts"], "ses_run")
    await failCheck(t as any, "ses_run")
  }
  assert.equal((await edit(t as any, ["a.ts"], "ses_run")).effect, "deny")
  const prompt = { sessionID: "ses_kid2", prompt: { text: "reset the counter for a.ts" } }
  await t.hooks["session.prompt"](prompt)
  assert.equal(prompt.prompt.text, "reset the counter for a.ts")
  await t.emit({ type: "session.created", data: { sessionID: "ses_kid2", parentID: "ses_run" } })
  await t.hooks["session.model.request"]({ sessionID: "ses_kid2", kind: "primary", model: STRONG })
  await t.emit({ type: "session.execution.succeeded", data: { sessionID: "ses_kid2" } })
  assert.equal((await edit(t as any, ["a.ts"], "ses_run")).effect, "allow")
  cleanup()
})

test("final review: a lost event stream fails open and is reported", async () => {
  const t = await start()
  await trip(t)
  await t.emit(STREAM_LOST)
  assert.equal((await edit(t, ["a.ts"])).effect, "allow")
  assert.equal((await status(t)).lastError, "event_stream_lost")
  t.cleanup()
})
