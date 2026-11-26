import assert from "node:assert/strict"
import { test } from "node:test"
import { createClaudeLimitHandler } from "../../src/claude-limit-events.js"

const directory = "/project"
const notice = "You've hit your weekly limit · resets 10am (UTC) Resets at 2030-01-02T10:00:00.000Z."
const reset = 1893578400
const frozen = Date.parse("2030-01-01T00:00:00.000Z")
const window = 7 * 86400 + 60
const claude = (id = "msg_1", extra = {}) => ({ type: "assistant", id, model: { providerID: "claude-agent",
  id: "claude-haiku-5-5", variant: "high" }, ...extra })
const rows = [claude()]
const flush = async () => { for (let i = 0; i < 10; i++) await new Promise((resolve) => setImmediate(resolve)) }
const until = async (predicate) => { for (let i = 0; i < 400 && !predicate(); i++) await new Promise((resolve) => setImmediate(resolve)) }
const failure = (over = {}) => ({ id: "evt_1", type: "session.step.failed", durable: true, location: { directory },
  data: { sessionID: "ses_1", assistantMessageID: "msg_1", error: { type: "provider.unknown", message: notice } }, ...over })
const withError = (error) => failure({ data: { sessionID: "ses_1", assistantMessageID: "msg_1", error } })

function harness({ context = async () => rows, enabled = async () => true, probe = async () => ({ state: "stored" }),
  location = { directory }, signal = new AbortController().signal, clock = () => frozen, noContext = false } = {}) {
  const calls = { context: [], probe: [], enabled: 0 }
  const ctx = noContext ? { session: {} } : { session: { context: async (input, options) => {
    calls.context.push({ input, options })
    return context(input, options)
  } } }
  const handler = createClaudeLimitHandler(ctx, { location, signal, now: clock,
    enabled: async () => { calls.enabled += 1; return enabled(calls.enabled) },
    probe: async (request) => { calls.probe.push(request); return probe(request) } })
  return { handler, calls }
}

test("a weekly-limit failure on the same location probes the exact failed Claude step", async () => {
  const h = harness()
  assert.equal(h.handler(failure()), undefined)
  await flush()
  assert.deepEqual(h.calls.probe, [{ reset_at: reset }])
  assert.deepEqual(h.calls.context.map((call) => call.input), [{ sessionID: "ses_1" }])
})

test("fractional and absent timestamp fractions resolve to the same whole second", async () => {
  for (const stamp of ["2030-01-02T10:00:00.000Z", "2030-01-02T10:00:00.500Z", "2030-01-02T10:00:00Z",
    "2030-01-02T10:00:00.5Z", "2030-01-02T10:00:00.999Z"]) {
    const h = harness()
    h.handler(withError({ type: "provider.unknown", message: notice.replace("2030-01-02T10:00:00.000Z", stamp) }))
    await flush()
    assert.deepEqual(h.calls.probe, [{ reset_at: reset }], stamp)
  }
})

test("invalid calendar dates, bad fraction digits and bad separators never probe", async () => {
  for (const stamp of ["2030-02-30T10:00:00.000Z", "2030-01-02T24:00:00.000Z", "2030-13-02T10:00:00.000Z",
    "2030-01-02T10:00:00.1234Z", "2030-01-02 10:00:00.000Z", "2030-01-02t10:00:00.000Z", "2030-01-02T10:00:00.000+00:00"]) {
    const h = harness()
    h.handler(withError({ type: "provider.unknown", message: notice.replace("2030-01-02T10:00:00.000Z", stamp) }))
    await flush()
    assert.deepEqual(h.calls.probe, [], stamp)
  }
})

test("the reset must be after now and within seven days plus the storage margin", async () => {
  const cases = [
    [reset * 1000 - 1000, true], [reset * 1000 - 500, true], [reset * 1000, false], [reset * 1000 + 1000, false],
    [(reset - window) * 1000, true], [(reset - window - 1) * 1000, false],
  ]
  for (const [now, allowed] of cases) {
    const h = harness({ clock: () => now })
    h.handler(failure())
    await flush()
    assert.equal(h.calls.probe.length, allowed ? 1 : 0, String(now))
  }
})

test("the 512 limit counts code points: astral characters at 512 are accepted and at 513 are refused", async () => {
  const head = "You've hit your weekly limit · resets "
  const tail = " Resets at 2030-01-02T10:00:00.000Z."
  const notice512 = (total) => head + "\u{1D518}".repeat(total - [...head].length - [...tail].length) + tail
  const fits = notice512(512)
  const over = notice512(513)
  assert.equal([...fits].length, 512)
  assert.ok(fits.length > 512)
  assert.equal([...over].length, 513)
  assert.ok(over.length <= 1024)
  for (const [message, accepted] of [[fits, true], [over, false]]) {
    const h = harness()
    h.handler(withError({ type: "provider.unknown", message }))
    await flush()
    assert.equal(h.calls.probe.length, accepted ? 1 : 0, String([...message].length))
  }
})

test("counting stops at the limit and a message over 1024 units is refused before any counting", async () => {
  const original = String.prototype[Symbol.iterator]
  const steps = { count: 0 }
  String.prototype[Symbol.iterator] = function () {
    const iterator = original.call(this)
    return { next() { steps.count += 1; return iterator.next() }, [Symbol.iterator]() { return this } }
  }
  try {
    const h = harness()
    const count = (message) => {
      steps.count = 0
      h.handler(withError({ type: "provider.unknown", message }))
      return steps.count
    }
    assert.ok(count("x".repeat(1000)) <= 513)
    assert.equal(count("x".repeat(1025)), 0)
    assert.equal(count("\u{1D518}".repeat(1000)) <= 513, true)
  } finally {
    String.prototype[Symbol.iterator] = original
  }
})

test("the notice needs a nonempty single-line clause and fits in 512 characters", async () => {
  const head = "You've hit your weekly limit · resets "
  const tail = " Resets at 2030-01-02T10:00:00.000Z."
  const fits = head + "x".repeat(512 - head.length - tail.length) + tail
  assert.equal([...fits].length, 512)
  const bad = [head + "x".repeat(513 - head.length - tail.length) + tail, head + "a\nb" + tail, head + "a\rb" + tail,
    head + "a\u2028b" + tail, head + "a\u2029b" + tail,
    "You've hit your weekly limit · resets  Resets at 2030-01-02T10:00:00.000Z.", head.trimEnd() + tail]
  for (const message of bad) {
    const h = harness()
    h.handler(withError({ type: "provider.unknown", message }))
    await flush()
    assert.deepEqual(h.calls.probe, [], message.slice(0, 40))
  }
  const h = harness()
  h.handler(withError({ type: "provider.unknown", message: fits }))
  await flush()
  assert.deepEqual(h.calls.probe, [{ reset_at: reset }])
})

test("a forged or embedded notice, a generic 429 and an auth error never probe", async () => {
  const messages = [`note: ${notice}`, `${notice} trailing`, `prefix\n${notice}`, "Too many requests", undefined]
  for (const message of messages) {
    const h = harness()
    h.handler(withError({ type: "provider.unknown", message }))
    await flush()
    assert.deepEqual(h.calls.probe, [], String(message))
  }
  for (const error of [{ type: "provider.rate-limit", message: notice, status: 429 },
    { type: "provider.auth", message: notice, status: 401 }]) {
    const h = harness()
    h.handler(withError(error))
    await flush()
    assert.deepEqual(h.calls.probe, [], error.type)
  }
  const h = harness()
  h.handler(failure({ data: { sessionID: "ses_1", assistantMessageID: "msg_1" } }))
  await flush()
  assert.deepEqual(h.calls.probe, [])
})

test("only the same directory and an equal workspace (absent equals absent) can trigger", async () => {
  const cases = [
    [{ directory: "/other" }, undefined, false],
    [{ directory: "/project/" }, undefined, false],
    [undefined, undefined, false],
    [{ directory: "project" }, undefined, false],
    [{ directory, workspaceID: "wsp_1" }, undefined, false],
    [{ directory }, "wsp_1", false],
    [{ directory, workspaceID: "wsp_2" }, "wsp_1", false],
    [{ directory, workspaceID: "wsp_1" }, "wsp_1", true],
    [{ directory }, undefined, true],
  ]
  for (const [eventLocation, workspaceID, allowed] of cases) {
    const h = harness({ location: { directory, ...(workspaceID && { workspaceID }) } })
    h.handler(failure({ location: eventLocation }))
    await flush()
    assert.equal(h.calls.probe.length, allowed ? 1 : 0, JSON.stringify(eventLocation))
  }
})

test("an explicit null workspaceID is not an absent one, in either direction", async () => {
  const place = (workspaceID) => workspaceID === undefined ? { directory } : { directory, workspaceID }
  const cases = [
    [place(undefined), { directory, workspaceID: null }, false],
    [place(null), { directory }, false],
    [place(null), { directory, workspaceID: null }, true],
    [place("wsp_1"), { directory, workspaceID: null }, false],
    [place(null), { directory, workspaceID: "wsp_1" }, false],
  ]
  for (const [plugin, eventLocation, allowed] of cases) {
    const h = harness({ location: plugin })
    h.handler(failure({ location: eventLocation }))
    await flush()
    assert.equal(h.calls.probe.length, allowed ? 1 : 0, JSON.stringify([plugin, eventLocation]))
  }
})

test("a directory with a different symlink spelling never probes", async () => {
  const h = harness({ location: { directory: "/private/var/project" } })
  h.handler(failure({ location: { directory: "/var/project" } }))
  await flush()
  assert.deepEqual(h.calls.probe, [])
})

test("only a claude-agent assistant with a model ID triggers", async () => {
  const cases = [
    [[claude("msg_1", { model: { providerID: "anthropic", id: "x" } })]],
    [[claude("msg_1", { model: { providerID: "opencode-go", id: "x" } })]],
    [[claude("msg_1", { model: { providerID: "claude-agent" } })]],
    [[claude("msg_1", { model: { providerID: "claude-agent", id: "" } })]],
    [[claude("msg_1", { model: { providerID: "claude-agent", modelID: "claude-haiku-5-5" } })]],
    [[claude("msg_2")]],
    [[claude("msg_1"), claude("msg_1")]],
    [[{ ...claude("msg_1"), type: "user" }]],
    [[]],
    [null],
  ]
  for (const [answer] of cases) {
    const h = harness({ context: async () => answer })
    h.handler(failure())
    await flush()
    assert.deepEqual(h.calls.probe, [], JSON.stringify(answer))
  }
})

test("the envelope with data is accepted and the current session model is never read", async () => {
  const h = harness({ context: async () => ({ data: rows }) })
  h.handler(failure())
  await flush()
  assert.deepEqual(h.calls.probe, [{ reset_at: reset }])
})

test("a missing context API never probes", async () => {
  const h = harness({ noContext: true })
  h.handler(failure())
  await flush()
  assert.deepEqual(h.calls.probe, [])
})

test("a nested agent parent is never consulted, even when it names a different model", async () => {
  const h = harness({ context: async ({ sessionID }) => sessionID === "ses_child"
    ? [claude("msg_1", { model: { providerID: "opencode-go", id: "claude-haiku-5-5" } })]
    : [claude("msg_1")] })
  h.handler(failure({ data: { sessionID: "ses_child", assistantMessageID: "msg_1",
    error: { type: "provider.unknown", message: notice } } }))
  await flush()
  assert.deepEqual(h.calls.context.map((call) => call.input.sessionID), ["ses_child"])
  assert.deepEqual(h.calls.probe, [])
})

test("a disabled project never probes, including when it is switched off during the lookup", async () => {
  const off = harness({ enabled: async () => false })
  off.handler(failure())
  await flush()
  assert.deepEqual([off.calls.context.length, off.calls.probe.length], [0, 0])

  const late = harness({ enabled: (count) => count === 1 })
  late.handler(failure())
  await flush()
  assert.deepEqual([late.calls.context.length, late.calls.probe.length], [1, 0])
})

test("a reset that passes during the lookup is not launched", async () => {
  let clock = frozen
  const h = harness({ clock: () => clock, context: async () => { clock = reset * 1000; return rows } })
  h.handler(failure())
  await flush()
  assert.deepEqual([h.calls.context.length, h.calls.probe.length], [1, 0])
})

test("a failure without a durable event ID or with an oversized one never probes", async () => {
  for (const id of [undefined, "", 7, "x".repeat(257)]) {
    const h = harness()
    h.handler(failure({ id }))
    await flush()
    assert.deepEqual(h.calls.probe, [], String(id))
  }
})

test("a duplicate of a completed failure never probes again", async () => {
  const h = harness()
  h.handler(failure())
  await flush()
  h.handler(failure())
  await flush()
  assert.equal(h.calls.probe.length, 1)
})

test("abort during a delayed settings read or context lookup never probes", async () => {
  const controller = new AbortController()
  let resolveSettings
  const settings = harness({ signal: controller.signal, enabled: () => new Promise((resolve) => { resolveSettings = resolve }) })
  settings.handler(failure())
  await until(() => resolveSettings)
  controller.abort()
  resolveSettings(true)
  await flush()
  assert.deepEqual([settings.calls.context.length, settings.calls.probe.length], [0, 0])

  const lookup = new AbortController()
  let resolveRows
  const context = harness({ signal: lookup.signal, context: () => new Promise((resolve) => { resolveRows = resolve }) })
  context.handler(failure())
  await until(() => resolveRows)
  lookup.abort()
  resolveRows(rows)
  await flush()
  assert.equal(context.calls.probe.length, 0)
})

test("a lookup past its two-second deadline never probes and holds the slot until it settles", async () => {
  let lookupSignal, release
  const h = harness({ context: (input, options) => {
    if (lookupSignal) return rows
    lookupSignal = options.signal
    return new Promise((resolve) => { release = () => resolve(rows) })
  } })
  h.handler(failure())
  await until(() => lookupSignal)
  await new Promise((resolve) => lookupSignal.addEventListener("abort", resolve, { once: true }))
  h.handler(failure({ id: "evt_2" }))
  await flush()
  assert.equal(h.calls.context.length, 1)
  release()
  await flush()
  assert.equal(h.calls.probe.length, 0)
  h.handler(failure({ id: "evt_3" }))
  await flush()
  assert.equal(h.calls.context.length, 2)
  assert.equal(h.calls.probe.length, 1)
})

test("a duplicate or distinct failure during lookup or probe starts nothing", async () => {
  let resolveRows
  const lookup = harness({ context: () => new Promise((resolve) => { resolveRows = resolve }) })
  lookup.handler(failure())
  await until(() => resolveRows)
  lookup.handler(failure())
  lookup.handler(failure({ id: "evt_2" }))
  await flush()
  assert.equal(lookup.calls.context.length, 1)
  resolveRows(rows)
  await flush()
  assert.equal(lookup.calls.probe.length, 1)

  let finish
  const active = harness({ probe: () => new Promise((resolve) => { finish = resolve }) })
  active.handler(failure())
  await until(() => finish)
  active.handler(failure())
  active.handler(failure({ id: "evt_2" }))
  await flush()
  assert.equal(active.calls.context.length, 1)
  assert.equal(active.calls.probe.length, 1)
  finish({ state: "stored" })
  await flush()
  active.handler(failure({ id: "evt_3" }))
  await flush()
  assert.equal(active.calls.probe.length, 2)
})

test("a probe that rejects releases the slot", async () => {
  let attempts = 0
  const h = harness({ probe: async () => { attempts += 1; if (attempts === 1) throw Error("PRIVATE-SENTINEL") } })
  h.handler(failure())
  await flush()
  h.handler(failure({ id: "evt_2" }))
  await flush()
  assert.equal(h.calls.probe.length, 2)
})

test("the handler never throws on malformed events", () => {
  const h = harness()
  for (const event of [null, undefined, {}, { data: null }, { id: "evt_9", data: { error: null } }, "text"]) {
    assert.doesNotThrow(() => h.handler(event))
  }
})
