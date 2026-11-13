// Deterministic adapter regressions, not native tool, form rendering or service qualification.
import { test } from "node:test"
import assert from "node:assert/strict"
import { mkdtempSync, readFileSync, rmSync, writeFileSync, symlinkSync } from "node:fs"
import { join } from "node:path"
import { tmpdir } from "node:os"
import { createWorkflowPlugin } from "../../runtime/adapters/opencode_workflow/index.ts"

const ROOT = "ses_root"
const MAX = { providerID: "p", id: "m", variant: "max" }
const tick = () => new Promise<void>((resolve) => setImmediate(resolve))
const settle = async () => { for (let i = 0; i < 8; i++) await tick() }
const copy = (value: any) => structuredClone(value)

async function harness(t: any, options: { bridge?: boolean; answer?: string } = {}) {
  const directory = mkdtempSync(join(tmpdir(), "stop-form-unit-"))
  const file = join(directory, "canary.txt")
  writeFileSync(file, "VALUE = 0\n")
  const hooks: Record<string, any> = {}
  const tools: any[] = []
  const queue: any[] = []
  let wake = () => {}
  let now = 0
  let serial = 0
  const forms = new Map<string, any>()
  const operations: string[] = []
  const driver: any = {
    async create({ sessionID, form }: any, { signal }: any) {
      assert.ok(signal instanceof AbortSignal)
      operations.push("create")
      const info = { ...copy(form), sessionID }
      forms.set(info.id, { ...info, state: options.answer
        ? { status: "answered", answer: { decision: options.answer } } : { status: "pending" } })
      return info
    },
    async get({ sessionID, formID }: any) {
      operations.push("get")
      const detail = forms.get(formID)
      assert.equal(sessionID, detail?.sessionID)
      return copy(detail)
    },
    async cancel({ sessionID, formID }: any) {
      operations.push("cancel")
      const detail = forms.get(formID)
      assert.equal(sessionID, detail?.sessionID)
      detail.state = { status: "cancelled" }
    },
  }
  const domain = (name: string) => ({ hook: async (kind: string, fn: any) => {
    hooks[`${name}.${kind}`] = fn
    return { dispose: async () => {} }
  } })
  const ctx = {
    location: { directory }, session: { ...domain("session"), get: async ({ sessionID }: any) => ({ id: sessionID }) },
    permission: domain("permission"), tool: { ...domain("tool"), transform: async (fn: any) => {
      fn({ add: (tool: any) => tools.push(tool) })
      return { dispose: async () => {} }
    } },
    event: { subscribe: ({ signal }: any) => ({ async *[Symbol.asyncIterator]() {
      const abort = () => wake()
      signal.addEventListener("abort", abort)
      try {
        while (!signal.aborted) {
          if (!queue.length) await new Promise<void>((resolve) => { wake = resolve })
          while (queue.length) {
            const event = queue.shift()
            if (event === "lost") throw new Error("synthetic stream loss")
            yield event
          }
        }
      } finally { signal.removeEventListener("abort", abort) }
    } }) },
  }
  const cleanup = await createWorkflowPlugin({
    featureStatus: async () => ({ features: { escalation_gate: { effective: true, values: {} } } }),
    stopForms: options.bridge === false ? undefined : driver,
    stopNow: () => now,
  } as any).setup(ctx)
  t.after(async () => { cleanup(); await settle(); rmSync(directory, { recursive: true, force: true }) })
  const emit = async (event: any) => { queue.push(event); wake(); await settle() }
  await settle()
  await emit({ type: "session.created", data: { sessionID: ROOT } })
  await hooks["session.model.request"]({ sessionID: ROOT, kind: "primary", model: MAX })
  const makeCall = (changes: any = {}) => {
    const id = `call_${++serial}`
    return { sessionID: ROOT, tool: "edit", messageID: "msg_1", id, input: { path: file, oldString: "0", newString: "1" },
      action: "edit", resources: [file], effect: "allow",
      source: { type: "tool", messageID: "msg_1", id },
      metadata: { files: [{ file, patch: "--- canary.txt\n+++ canary.txt\n@@ -1 +1 @@\n-VALUE = 0\n+VALUE = 1\n",
        status: "modified", additions: 1, deletions: 1 }] }, ...changes }
  }
  const evaluate = async (call: any) => {
    await hooks["tool.execute.before"]?.(call)
    await hooks["permission.evaluate"](call)
  }
  const complete = (call: any, changes: any = {}) => hooks["tool.execute.after"]({
    sessionID: call.sessionID, messageID: call.messageID, id: call.id, tool: call.tool,
    input: call.input, status: "completed", result: { content: "ok" }, ...changes,
  })
  const fail = () => hooks["tool.execute.after"]({ sessionID: ROOT, tool: "shell", id: `check_${++serial}`,
    input: { command: "pytest" }, status: "completed", result: { metadata: { exit: 1 } } })
  const stop = async () => {
    for (let i = 0; i < 3; i++) { const c = makeCall(); await evaluate(c); await complete(c); await fail() }
    await emit({ type: "session.created", data: { sessionID: `ses_diag${serial}`, parentID: ROOT } })
    await hooks["session.model.request"]({ sessionID: `ses_diag${serial}`, kind: "primary", model: MAX })
    await emit({ type: "session.execution.succeeded", data: { sessionID: `ses_diag${serial}` } })
    for (let i = 0; i < 2; i++) { const c = makeCall(); await evaluate(c); await complete(c); await fail() }
  }
  const status = async () => JSON.parse((await tools[0].execute({}, { sessionID: ROOT })).content)
  const reply = async (answer = "approve") => {
    const detail: any = [...forms.values()].at(-1)
    assert.ok(detail, "the adapter must create the native form before waiting")
    detail.state = { status: "answered", answer: { decision: answer } }
    await emit({ type: "form.replied", data: { id: detail.id, sessionID: detail.sessionID, answer: { decision: answer } } })
  }
  return { hooks, driver, forms, operations, file, directory, makeCall, evaluate, complete, fail, stop, status, emit, reply,
    cleanup, advance: async (ms: number) => { now += ms; t.mock.timers.tick(ms); await settle() } }
}

test("unqualified default bridge denies stop without native discovery or a form", async (t) => {
  const h = await harness(t, { bridge: false })
  await h.stop()
  const c = h.makeCall()
  await h.evaluate(c)
  assert.equal(c.effect, "deny")
  assert.match((await h.status()).lastError, /bridge_unavailable/)
  await h.complete(c)
  assert.equal((await h.status()).held[0].stage, "stop")
  assert.deepEqual(h.operations, [])
})

test("exact authoritative form approval preserves allow and releases only matching completed call", async (t) => {
  const h = await harness(t, { answer: "approve" })
  await h.stop()
  const c = h.makeCall()
  await h.evaluate(c)
  assert.equal(c.effect, "allow")
  assert.equal((await h.status()).held[0].stage, "stop")
  await h.complete(c, { sessionID: "ses_foreign" })
  assert.equal((await h.status()).held[0].stage, "stop")
  await h.complete(c, { id: "call_other" })
  assert.equal((await h.status()).held[0].stage, "stop")
  await h.complete(c)
  assert.deepEqual((await h.status()).held, [])
  await h.complete(c)
  assert.deepEqual((await h.status()).held, [])
  assert.deepEqual(h.operations, ["create", "get"])
})

test("pending evaluation stays denied and never edits the file before authoritative settlement", async (t) => {
  const h = await harness(t)
  await h.stop()
  const c = h.makeCall()
  let finished = false
  const pending = h.evaluate(c).then(() => { finished = true })
  await settle()
  assert.equal(c.effect, "deny")
  assert.equal(finished, false)
  assert.equal(readFileSync(h.file, "utf8"), "VALUE = 0\n")
  const form: any = [...h.forms.values()][0]
  assert.equal(form.fields[0].required, true)
  assert.equal(form.fields[0].custom, false)
  assert.equal(form.fields[0].default, undefined)
  assert.match(form.fields[0].description, /VALUE = 1/)
  await h.reply()
  await pending
  assert.equal(c.effect, "allow")
})

for (const answer of ["reject", "something-else"]) {
  test(`${answer} does not authorize a stopped edit`, async (t) => {
    const h = await harness(t, { answer })
    await h.stop()
    const c = h.makeCall()
    await h.evaluate(c)
    assert.equal(c.effect, "deny")
    await h.complete(c)
    assert.equal((await h.status()).held[0].stage, "stop")
  })
}

test("a cancelled form settles denial without release", async (t) => {
  const h = await harness(t)
  await h.stop()
  const c = h.makeCall(); const pending = h.evaluate(c)
  await settle()
  const form: any = [...h.forms.values()][0]
  assert.ok(form)
  form.state = { status: "cancelled" }
  await h.emit({ type: "form.cancelled", data: { id: form.id, sessionID: ROOT } })
  await pending
  assert.equal(c.effect, "deny")
  assert.equal((await h.status()).held[0].stage, "stop")
})

test("native ask survives form approval and native rejection keeps the hold", async (t) => {
  const h = await harness(t, { answer: "approve" })
  await h.stop()
  const c = h.makeCall({ effect: "ask" })
  await h.evaluate(c)
  assert.equal(c.effect, "ask")
  assert.deepEqual(h.operations, ["create", "get"])
  await h.complete(c, { status: "error", error: { message: "native reject" } })
  assert.equal((await h.status()).held[0].stage, "stop")
  await h.complete(c)
  assert.equal((await h.status()).held[0].stage, "stop")
})

test("configured deny never creates a form or broadens the decision", async (t) => {
  const h = await harness(t)
  await h.stop()
  const c = h.makeCall({ effect: "deny" })
  await h.evaluate(c)
  assert.equal(c.effect, "deny")
  assert.deepEqual(h.operations, [])
})

test("concurrent stopped requests cannot borrow one approval", async (t) => {
  const h = await harness(t)
  await h.stop()
  const first = h.makeCall(); const pending = h.evaluate(first)
  await settle()
  const second = h.makeCall(); await h.evaluate(second)
  assert.equal(second.effect, "deny")
  await h.reply(); await pending
  assert.equal(first.effect, "allow")
  await h.complete(second)
  assert.equal((await h.status()).held[0].stage, "stop")
  await h.complete(first)
  assert.deepEqual((await h.status()).held, [])
  assert.equal(h.operations.filter((op) => op === "create").length, 1)
})

test("changed native metadata or file after initial snapshot invalidates approval", async (t) => {
  for (const change of ["file", "metadata"]) {
    const h = await harness(t)
    await h.stop()
    const c = h.makeCall(); const pending = h.evaluate(c)
    await settle()
    if (change === "file") writeFileSync(h.file, "VALUE = 9\n")
    else c.metadata.files[0].patch += "another change"
    await h.reply(); await pending
    assert.equal(c.effect, "deny", change)
    await h.complete(c)
    assert.equal((await h.status()).held[0].stage, "stop")
  }
})

test("one extra authoritative confirmation handles event before state and coalesces duplicates", async (t) => {
  const h = await harness(t)
  await h.stop()
  const original = h.driver.get
  let reads = 0
  h.driver.get = async (...args: any[]) => {
    const detail = await original(...args)
    if (++reads === 2) {
      const form: any = [...h.forms.values()][0]
      form.state = { status: "answered", answer: { decision: "approve" } }
    }
    return detail
  }
  const c = h.makeCall(); const pending = h.evaluate(c)
  await settle()
  const form: any = [...h.forms.values()][0]; assert.ok(form)
  await h.emit({ type: "form.replied", data: { id: form.id, sessionID: ROOT, answer: { decision: "approve" } } })
  await h.emit({ type: "form.replied", data: { id: form.id, sessionID: ROOT, answer: { decision: "approve" } } })
  await pending
  assert.equal(c.effect, "allow")
  assert.deepEqual(h.operations, ["create", "get", "get", "get"])
})

test("unconfirmed terminal event denies and cancels within six operations", async (t) => {
  const h = await harness(t)
  await h.stop()
  const c = h.makeCall(); const pending = h.evaluate(c)
  await settle()
  const form: any = [...h.forms.values()][0]; assert.ok(form)
  await h.emit({ type: "form.replied", data: { id: form.id, sessionID: ROOT, answer: { decision: "approve" } } })
  await pending
  assert.equal(c.effect, "deny")
  assert.deepEqual(h.operations, ["create", "get", "get", "get", "cancel", "get"])
  assert.equal(form.state.status, "cancelled")
})

test("interruption, reset and disposal settle pending operations denied", async (t) => {
  for (const cause of ["interrupt", "reset", "dispose", "stream"]) {
    const h = await harness(t)
    await h.stop()
    const c = h.makeCall(); const pending = h.evaluate(c)
    await settle()
    if (cause === "interrupt") await h.emit({ type: "session.execution.interrupted", data: { sessionID: ROOT } })
    if (cause === "reset") await h.hooks["session.prompt"]({ sessionID: ROOT, prompt: { text: "reset the gate" } })
    if (cause === "dispose") h.cleanup()
    if (cause === "stream") await h.emit("lost")
    await pending
    assert.equal(c.effect, "deny", cause)
    assert.equal([...h.forms.values()][0].state.status, "cancelled", cause)
  }
})

test("approval acquisition timeout reserves cleanup and late replies cannot allow", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] })
  const h = await harness(t)
  await h.stop()
  const c = h.makeCall(); const pending = h.evaluate(c)
  await settle()
  await h.advance(110000)
  await pending
  assert.equal(c.effect, "deny")
  assert.equal([...h.forms.values()][0].state.status, "cancelled")
  await h.reply()
  await h.complete(c)
  assert.equal((await h.status()).held[0].stage, "stop")
  assert.ok(h.operations.length <= 6)
})

test("unsupported stopped tools, resources and native changes fail closed before a form", async (t) => {
  const h = await harness(t, { answer: "approve" })
  await h.stop()
  for (const changes of [{ tool: "write" }, { resources: [h.file, join(h.directory, "other.txt")] },
    { metadata: {} }, { metadata: { files: [{ file: h.file, patch: "x".repeat(65537), status: "modified", additions: 1, deletions: 1 }] } },
    { metadata: { files: [{ file: h.file, patch: "x", status: "added", additions: 1, deletions: 0 }] } }]) {
    const c = h.makeCall(changes); await h.evaluate(c)
    assert.equal(c.effect, "deny")
  }
  assert.deepEqual(h.operations, [])
})

test("symlink, binary and oversized stopped files are not admitted", async (t) => {
  const h = await harness(t, { answer: "approve" })
  await h.stop()
  for (const kind of ["symlink", "binary", "large"]) {
    rmSync(h.file)
    if (kind === "symlink") symlinkSync(join(h.directory, "other.txt"), h.file)
    if (kind === "binary") writeFileSync(h.file, Buffer.from([0, 255]))
    if (kind === "large") writeFileSync(h.file, Buffer.alloc(1048577, 65))
    const c = h.makeCall(); await h.evaluate(c)
    assert.equal(c.effect, "deny", kind)
  }
  assert.deepEqual(h.operations, [])
})
