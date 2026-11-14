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

async function harness(t: any, options: { bridge?: boolean; answer?: string; early?: boolean } = {}) {
  const directory = mkdtempSync(join(tmpdir(), "stop-form-unit-"))
  const file = join(directory, "canary.txt")
  writeFileSync(file, "VALUE = 0\n")
  const hooks: Record<string, any> = {}
  const tools: any[] = []
  const queue: any[] = []
  let wake = () => {}
  let now = 0
  let serial = 0
  let owner: any
  const forms = new Map<string, any>()
  const operations: string[] = []
  const driver: any = {
    async create({ sessionID, form }: any, { signal }: any) {
      assert.ok(signal instanceof AbortSignal)
      operations.push("create")
      const info = { ...copy(form), sessionID }
      forms.set(info.id, { ...info, state: options.early && options.answer
        ? { status: "answered", answer: { decision: options.answer } } : { status: "pending" } })
      if (options.answer) setImmediate(() => { void reply(options.answer).catch(() => {}) })
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
    registerStopOwner: (value: any) => { owner = value },
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
    let ticket: any
    if (owner?.pending(ROOT).some((item: any) => item.formID === detail.id)) {
      const view = owner.open(ROOT, detail.id, "view_test")
      ticket = await owner.begin({ attemptID: view.attemptID, viewID: "view_test", submissionID: "submit_test",
        contentDigest: view.contentDigest, decision: answer === "reject" ? "reject" : "approve" })
    }
    detail.state = { status: "answered", answer: { decision: answer } }
    await emit({ type: "form.replied", data: { id: detail.id, sessionID: detail.sessionID, answer: { decision: answer } } })
    if (ticket) owner.complete(ticket, "settled")
  }
  return { hooks, driver, forms, operations, file, directory, makeCall, evaluate, complete, fail, stop, status, emit, reply,
    cleanup, owner: () => owner, advance: async (ms: number) => { now += ms; t.mock.timers.tick(ms); await settle() } }
}

test("default composer approval without viewer receipt never authorizes", async (t) => {
  const h = await harness(t)
  await h.stop()
  const c = h.makeCall(); const pending = h.evaluate(c)
  await settle()
  const form = [...h.forms.values()][0]
  form.state = { status: "answered", answer: { decision: "approve" } }
  await h.emit({ type: "form.replied", data: { id: form.id, sessionID: ROOT } })
  await pending
  assert.equal(c.effect, "deny")
})

test("pending read belongs to explicit begin, and duplicate submissions share its ticket", async (t) => {
  const h = await harness(t)
  await h.stop()
  const c = h.makeCall(); const pending = h.evaluate(c)
  await settle()
  assert.deepEqual(h.operations, ["create"])
  const owner = h.owner(); const form = [...h.forms.values()][0]
  const view = owner.open(ROOT, form.id, "view_one")
  assert.equal(view.patch, c.metadata.files[0].patch)
  assert.throws(() => owner.open(ROOT, form.id, "view_two"))
  const input = { attemptID: view.attemptID, viewID: "view_one", submissionID: "submission_one",
    contentDigest: view.contentDigest, decision: "approve" }
  const first = owner.begin(input); const second = owner.begin(input)
  assert.equal(first, second)
  const ticket = await first
  assert.deepEqual(h.operations, ["create", "get"])
  form.state = { status: "answered", answer: { decision: "approve" } }
  await h.emit({ type: "form.replied", data: { id: form.id, sessionID: ROOT } })
  assert.equal(c.effect, "deny", "native event cannot replace the committed receipt")
  owner.complete(ticket, "settled"); owner.complete(ticket, "settled")
  await pending
  assert.equal(c.effect, "allow")
  assert.equal(await owner.begin(input), ticket, "an identical completed begin replay returns its result")
  assert.deepEqual(h.operations, ["create", "get", "get"])
})

test("an already answered form fails the selected pending read", async (t) => {
  const h = await harness(t, { answer: "approve", early: true })
  await h.stop()
  const c = h.makeCall(); await h.evaluate(c)
  assert.equal(c.effect, "deny")
  assert.deepEqual(h.operations, ["create", "get"])
})

for (const outcome of ["settled", "failed"] as const) test(`receipt ${outcome} without event requires exact native state`, async (t) => {
  const h = await harness(t)
  await h.stop()
  const c = h.makeCall(); const pending = h.evaluate(c)
  await settle()
  const owner = h.owner(); const form = [...h.forms.values()][0]
  assert.deepEqual(owner.pending("ses_foreign"), [])
  const view = owner.open(ROOT, form.id, "view_receipt")
  const input = { attemptID: view.attemptID, viewID: "view_receipt", submissionID: "submit_receipt",
    contentDigest: view.contentDigest, decision: "approve" }
  const ticket = await owner.begin(input)
  await assert.rejects(owner.begin({ ...input, submissionID: "submit_other" }))
  form.state = { status: "answered", answer: { decision: "approve" } }
  owner.complete(ticket, outcome)
  await pending
  assert.equal(c.effect, outcome === "settled" ? "allow" : "deny")
  if (outcome === "settled") {
    owner.abandon(view.attemptID, "view_receipt")
    await h.complete(c)
    assert.deepEqual((await h.status()).held, [], "abandon cannot undo consumed permission")
  }
})

test("abandon during pending GET invalidates its late result and retains hold", async (t) => {
  const h = await harness(t)
  await h.stop()
  const c = h.makeCall(); const pending = h.evaluate(c)
  await settle()
  const owner = h.owner(); const form = [...h.forms.values()][0]
  const view = owner.open(ROOT, form.id, "view_close")
  const original = h.driver.get
  let release!: (value: any) => void
  let first = true
  h.driver.get = (...args: any[]) => { if (!first) return original(...args); first = false; return new Promise((done) => { release = done }) }
  const begin = owner.begin({ attemptID: view.attemptID, viewID: "view_close", submissionID: "submit_close",
    contentDigest: view.contentDigest, decision: "approve" })
  const denied = assert.rejects(begin)
  await settle(); owner.abandon(view.attemptID, "view_close")
  await denied; await pending
  release(copy(form)); await settle()
  assert.equal(c.effect, "deny")
  assert.equal((await h.status()).held[0].stage, "stop")
  assert.equal(h.operations.filter((op) => op === "cancel").length, 1)
})

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
  assert.deepEqual(h.operations, ["create", "get", "get"])
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
  assert.deepEqual(h.operations, ["create", "get", "get"])
  await h.complete(c, { status: "error", error: { message: "native reject" } })
  assert.equal((await h.status()).held[0].stage, "stop")
  await h.complete(c)
  assert.equal((await h.status()).held[0].stage, "stop")
})

test("form approval preserves a pre-existing native permission explanation", async (t) => {
  const h = await harness(t, { answer: "approve" })
  await h.stop()
  const c = h.makeCall({ effect: "ask", message: "existing native permission reason" })
  await h.evaluate(c)
  assert.equal(c.effect, "ask")
  assert.equal(c.message, "existing native permission reason")
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
  const view = h.owner().open(ROOT, form.id, "view_event")
  const ticket = await h.owner().begin({ attemptID: view.attemptID, viewID: "view_event",
    submissionID: "submit_event", contentDigest: view.contentDigest, decision: "approve" })
  await h.emit({ type: "form.replied", data: { id: form.id, sessionID: ROOT, answer: { decision: "approve" } } })
  await h.emit({ type: "form.replied", data: { id: form.id, sessionID: ROOT, answer: { decision: "approve" } } })
  h.owner().complete(ticket, "settled")
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
  const view = h.owner().open(ROOT, form.id, "view_unconfirmed")
  const ticket = await h.owner().begin({ attemptID: view.attemptID, viewID: "view_unconfirmed",
    submissionID: "submit_unconfirmed", contentDigest: view.contentDigest, decision: "approve" })
  await h.emit({ type: "form.replied", data: { id: form.id, sessionID: ROOT, answer: { decision: "approve" } } })
  h.owner().complete(ticket, "settled")
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

for (const action of ["write", "patch"]) {
  test(`an edit tool cannot use its form permit for permission action ${action}`, async (t) => {
    const h = await harness(t, { answer: "approve" })
    await h.stop()
    const c = h.makeCall({ action })
    await h.evaluate(c)
    assert.equal(c.effect, "deny")
    assert.deepEqual(h.operations, [])
  })
}

test("a malformed proposed native diff cannot acquire approval", async (t) => {
  const h = await harness(t, { answer: "approve" })
  await h.stop()
  const c = h.makeCall({ metadata: { files: [
    { file: h.file, patch: "not a native proposed diff", status: "modified", additions: 1, deletions: 1 },
  ] } })
  await h.evaluate(c)
  assert.equal(c.effect, "deny")
  assert.deepEqual(h.operations, [])
})

test("an old successful completion cannot clear a reset-and-reheld stop", async (t) => {
  const h = await harness(t, { answer: "approve" })
  await h.stop()
  const old = h.makeCall(); await h.evaluate(old)
  assert.equal(old.effect, "allow")
  await h.hooks["session.prompt"]({ sessionID: ROOT, prompt: { text: "reset the gate" } })
  await h.stop()
  await h.complete(old)
  assert.equal((await h.status()).held[0].stage, "stop")
  const current = h.makeCall(); await h.evaluate(current); await h.complete(current)
  assert.deepEqual((await h.status()).held, [])
})

test("reset of a pending form invalidates its answer before the same path is held again", async (t) => {
  const h = await harness(t)
  await h.stop()
  const c = h.makeCall(); const pending = h.evaluate(c)
  await settle()
  await h.hooks["session.prompt"]({ sessionID: ROOT, prompt: { text: "reset the gate" } })
  await pending
  await h.stop()
  await h.reply(); await h.complete(c)
  assert.equal(c.effect, "deny")
  assert.equal((await h.status()).held[0].stage, "stop")
})

test("an approved native ask retains target occupancy until terminal tool result", async (t) => {
  const h = await harness(t, { answer: "approve" })
  await h.stop()
  const c = h.makeCall({ effect: "ask" }); await h.evaluate(c)
  const other = h.makeCall(); await h.evaluate(other)
  assert.equal(other.effect, "deny")
  assert.equal(h.forms.size, 1)
  await h.complete(c, { status: "error" })
  const next = h.makeCall(); await h.evaluate(next)
  assert.equal(next.effect, "allow")
  assert.equal(h.forms.size, 2)
})

test("foreign session, form ID, answer field and binding changes cannot authorize", async (t) => {
  for (const mutation of ["session", "id", "binding", "generation", "fields", "extra-answer"]) {
    const h = await harness(t, { answer: "approve" })
    await h.stop()
    const original = h.driver.get
    h.driver.get = async (...args: any[]) => {
      const value = await original(...args)
      if (mutation === "session") value.sessionID = "ses_foreign"
      if (mutation === "id") value.id = "frm_old"
      if (mutation === "binding") value.metadata.binding = "old"
      if (mutation === "generation") value.metadata.generation = "old"
      if (mutation === "fields") value.fields[0].description = "different change"
      if (mutation === "extra-answer" && value.state.answer) value.state.answer.extra = "approve"
      return value
    }
    const c = h.makeCall(); await h.evaluate(c)
    assert.equal(c.effect, "deny", mutation)
    await h.complete(c)
    assert.equal((await h.status()).held[0].stage, "stop")
    assert.ok(!h.operations.includes("cancel"), "never cancel an unverified foreign form")
  }
})

test("unknown native call or changed message ID cannot acquire a form", async (t) => {
  const h = await harness(t, { answer: "approve" })
  await h.stop()
  const unknown = h.makeCall(); await h.hooks["permission.evaluate"](unknown)
  assert.equal(unknown.effect, "deny")
  const c = h.makeCall(); await h.hooks["tool.execute.before"](c)
  c.source.messageID = "msg_foreign"
  await h.hooks["permission.evaluate"](c)
  assert.equal(c.effect, "deny")
  assert.deepEqual(h.operations, [])
})

test("a reply before create response or before pending read cannot become retroactive viewer approval", async (t) => {
  for (const stage of ["create", "get"]) {
    const h = await harness(t, { answer: "approve" })
    await h.stop()
    const original = h.driver[stage]
    let once = false
    h.driver[stage] = async (...args: any[]) => {
      const value = await original(...args)
      if (!once) {
        once = true
        const form = [...h.forms.values()][0]
        form.state = { status: "answered", answer: { decision: "approve" } }
        await h.emit({ type: "form.replied", data: { id: form.id, sessionID: ROOT } })
        if (stage === "get") return copy(form)
      }
      return value
    }
    const c = h.makeCall(); await h.evaluate(c)
    assert.equal(c.effect, "deny", stage)
    await h.complete(c)
    assert.equal((await h.status()).held[0].stage, "stop")
    assert.ok(h.operations.length <= 4)
  }
})

test("repeated authorization under the same native call invalidates its permit", async (t) => {
  const h = await harness(t, { answer: "approve" })
  await h.stop()
  const c = h.makeCall(); await h.evaluate(c)
  assert.equal(c.effect, "allow")
  await h.hooks["permission.evaluate"](c)
  assert.equal(c.effect, "deny")
  await h.complete(c)
  assert.equal((await h.status()).held[0].stage, "stop")
  assert.equal(h.forms.size, 1)
})

test("event hints with a foreign session cannot release a pending native call", async (t) => {
  const h = await harness(t)
  await h.stop()
  const c = h.makeCall(); let done = false
  const pending = h.evaluate(c).then(() => { done = true })
  await settle()
  const form: any = [...h.forms.values()][0]
  await h.emit({ type: "form.replied", data: { id: form.id, sessionID: "ses_foreign", answer: { decision: "approve" } } })
  assert.equal(done, false)
  assert.equal(c.effect, "deny")
  await h.reply(); await pending
  assert.equal(c.effect, "allow")
})

test("read operation timeout denies and late authoritative success cannot re-activate it", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] })
  const h = await harness(t, { answer: "approve" })
  await h.stop()
  const original = h.driver.get
  let release!: (value: any) => void
  let first = true
  h.driver.get = async (...args: any[]) => {
    if (!first) return original(...args)
    first = false
    return new Promise((done) => { release = done })
  }
  const c = h.makeCall(); const pending = h.evaluate(c)
  await settle(); await h.advance(8000); await pending
  assert.equal(c.effect, "deny")
  release(copy([...h.forms.values()][0])); await settle(); await h.complete(c)
  assert.equal((await h.status()).held[0].stage, "stop")
  assert.ok(h.operations.length <= 6)
})

test("uncertain create is never retried; late creation is reported unconfirmed, not authorized", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] })
  const h = await harness(t)
  await h.stop()
  const create = h.driver.create
  let release!: () => void
  h.driver.create = (...args: any[]) => new Promise((done) => { release = () => { void create(...args).then(done) } })
  const c = h.makeCall(); const pending = h.evaluate(c)
  await settle(); await h.advance(8000); await pending
  assert.equal(c.effect, "deny")
  assert.match((await h.status()).lastError, /cleanup_unconfirmed/)
  release(); await settle(); await h.complete(c)
  assert.equal((await h.status()).held[0].stage, "stop")
  assert.equal(h.operations.filter((op) => op === "create").length, 1)
})

test("cleanup timeout preserves denial and reports unconfirmed owned cancellation", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] })
  const h = await harness(t)
  await h.stop()
  h.driver.cancel = () => new Promise(() => {})
  const c = h.makeCall(); const pending = h.evaluate(c)
  await settle(); await h.advance(110000); await h.advance(8000); await pending
  assert.equal(c.effect, "deny")
  assert.match((await h.status()).lastError, /cleanup_unconfirmed/)
  assert.equal((await h.status()).held[0].stage, "stop")
})

test("pre-hook external changes remain a stated assumption rather than a fabricated native before-image check", async (t) => {
  const h = await harness(t, { answer: "approve" })
  await h.stop()
  const c = h.makeCall()
  writeFileSync(h.file, "VALUE = 9\n") // Outside approved writer scope, before the hook's first snapshot.
  await h.evaluate(c)
  assert.equal(c.effect, "allow", "stable hook bytes cannot authenticate the native executor's earlier read")
  assert.equal(readFileSync(h.file, "utf8"), "VALUE = 9\n", "the plugin itself never applies the native diff")
})

test("the cleanup reserve bounds a final read by remaining time, not a new eight-second window", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] })
  const h = await harness(t)
  await h.stop()
  let cancel!: () => void
  h.driver.cancel = () => new Promise<void>((done) => { cancel = done })
  const original = h.driver.get
  let reads = 0
  h.driver.get = (...args: any[]) => ++reads === 1 ? original(...args) : new Promise(() => {})
  const c = h.makeCall(); const pending = h.evaluate(c)
  await settle(); await h.advance(110000); await h.advance(7999)
  cancel(); await settle(); await h.advance(2001)
  await pending
  assert.equal(c.effect, "deny")
  assert.match((await h.status()).lastError, /cleanup_unconfirmed/)
  await h.complete(c)
  assert.equal((await h.status()).held[0].stage, "stop")
})

test("a child shares the stopped target but cannot borrow its parent's pending approval", async (t) => {
  const h = await harness(t)
  await h.stop()
  await h.emit({ type: "session.created", data: { sessionID: "ses_child", parentID: ROOT } })
  const parent = h.makeCall(); const pending = h.evaluate(parent)
  await settle()
  const child = h.makeCall({ sessionID: "ses_child" }); await h.evaluate(child)
  assert.equal(child.effect, "deny")
  await h.reply(); await pending
  await h.complete(parent)
  assert.deepEqual((await h.status()).held, [])
  assert.equal(h.forms.size, 1)
})

test("correlation capacity refuses new calls without evicting an active permit", async (t) => {
  const h = await harness(t, { answer: "approve" })
  await h.stop()
  const first = h.makeCall(); await h.evaluate(first)
  for (let i = 0; i < 300; i++) await h.hooks["tool.execute.before"](h.makeCall())
  const last = h.makeCall(); await h.evaluate(last)
  assert.equal(last.effect, "deny")
  await h.complete(first)
  assert.deepEqual((await h.status()).held, [])
  assert.equal(h.forms.size, 1)
})

test("ordinary editing stays quiet and a native post-format result still completes its exact permit", async (t) => {
  const h = await harness(t, { answer: "approve" })
  const ordinary = h.makeCall(); await h.evaluate(ordinary); await h.complete(ordinary)
  assert.equal(ordinary.effect, "allow")
  assert.deepEqual(h.operations, [])
  await h.stop()
  const stopped = h.makeCall(); await h.evaluate(stopped)
  await h.complete(stopped, { messageID: "msg_other" })
  assert.equal((await h.status()).held[0].stage, "stop")
  await h.complete(stopped, { result: { metadata: { files: [{ patch: "native formatter changed spacing" }] } } })
  assert.deepEqual((await h.status()).held, [])
})

test("native JSON field ordering does not turn an exact form approval into a denial", async (t) => {
  const h = await harness(t, { answer: "approve" })
  await h.stop()
  const nativeOrder = (value: any) => ({ ...value,
    fields: value.fields.map((field: any) => ({ description: field.description, key: field.key,
      title: field.title, required: field.required, type: field.type, custom: field.custom, options: field.options })),
    metadata: { nonce: value.metadata.nonce, binding: value.metadata.binding, generation: value.metadata.generation },
  })
  const create = h.driver.create; const get = h.driver.get
  h.driver.create = async (...args: any[]) => nativeOrder(await create(...args))
  h.driver.get = async (...args: any[]) => nativeOrder(await get(...args))
  const c = h.makeCall(); await h.evaluate(c)
  assert.equal(c.effect, "allow")
  await h.complete(c)
  assert.deepEqual((await h.status()).held, [])
})
