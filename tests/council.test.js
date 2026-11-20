import assert from "node:assert/strict"
import { test } from "node:test"
import { createRoster } from "../council.js"

const entry = (provider, id, over = {}) => ({ provider, id, model: id, family: "gpt", variants: ["high"], context: 1000,
  released: 100, name: id.toUpperCase(), ...over })
const LIST = [entry("openai", "gpt-6.2-astra", { released: 200 }), entry("openai", "gpt-6.1-sol"),
  entry("openai", "gpt-6.0-astra", { released: 50 }), entry("anthropic", "claude-opus", { variants: [] })]
const FALSE = { admission: false, reservation: false, spend_permit: false }

// A stub check that records its calls and answers ok for every entry it is sent.
const setup = ({ list = LIST, answer } = {}) => {
  const calls = []
  const check = async (entries, signal) => {
    calls.push({ entries, signal })
    if (answer) return typeof answer === "function" ? answer(entries) : answer
    return { state: "checked", seats: entries.map((e) => ({ provider: e.provider, id: e.id, status: "ok", paid_overflow: "blocked" })), ...FALSE }
  }
  const roster = createRoster({ models: async () => list, check, providers: new Set(["openai", "anthropic"]) })
  return { calls, roster }
}
const seat = (name, model, variant = null) => ({ seat: name, model, variant })
const req = (...seats) => ({ v: 1, seats })

test("all ok seats are ready and keep their model ids", async () => {
  const { roster } = setup()
  const a = await roster(req(seat("Astra", "openai/gpt-6.2-astra", "high"), seat("Opus", "anthropic/claude-opus")))
  assert.equal(a.ready, true)
  assert.equal(a.reason, null)
  assert.deepEqual(a.seats, [
    { seat: "Astra", model: "openai/gpt-6.2-astra", name: "GPT-6.2-ASTRA", variant: "high", status: "ok", paid_overflow: "blocked", candidates: [] },
    { seat: "Opus", model: "anthropic/claude-opus", name: "CLAUDE-OPUS", variant: null, status: "ok", paid_overflow: "blocked", candidates: [] }])
  assert.deepEqual({ ...FALSE }, { admission: a.admission, reservation: a.reservation, spend_permit: a.spend_permit })
})

test("paid duplicates are provider_not_on_plan", async () => {
  const { roster, calls } = setup()
  const a = await roster(req(seat("Paid", "openrouter/openai/gpt-6.2-astra"), seat("Free", "openai/gpt-6.2-astra")))
  assert.equal(a.ready, false)
  assert.equal(a.seats[0].status, "provider_not_on_plan")
  assert.equal(a.seats[0].model, "openrouter/openai/gpt-6.2-astra")
  assert.equal(a.seats[0].name, null)
  assert.equal(a.seats[1].status, "ok")
  assert.deepEqual(calls[0].entries.map((e) => `${e.provider}/${e.id}`), ["openai/gpt-6.2-astra"])
})

test("unknown model lists same-line candidates newest first", async () => {
  const { roster } = setup()
  const a = await roster(req(seat("X", "openai/gpt-6.3-astra")))
  assert.equal(a.seats[0].status, "unknown_model")
  assert.deepEqual(a.seats[0].candidates, ["openai/gpt-6.2-astra", "openai/gpt-6.0-astra"])
  const b = await setup({ list: [LIST[0], LIST[1]] }).roster(req(seat("X", "openai/gpt-6.3-astra")))
  assert.deepEqual(b.seats[0].candidates, ["openai/gpt-6.2-astra"])
  assert.equal(b.ready, false)
})

test("unknown variant is not sent to check", async () => {
  const { roster, calls } = setup()
  const a = await roster(req(seat("A", "openai/gpt-6.2-astra", "max"), seat("B", "openai/gpt-6.1-sol", "high")))
  assert.equal(a.seats[0].status, "unknown_variant")
  assert.equal(a.seats[0].name, "GPT-6.2-ASTRA")
  assert.equal(a.seats[1].status, "ok")
  assert.equal(calls[0].entries.length, 1)
  assert.equal(calls[0].entries[0].id, "gpt-6.1-sol")
})

test("same model on two seats", async () => {
  const { roster, calls } = setup()
  const a = await roster(req(seat("One", "openai/gpt-6.2-astra"), seat("Two", "openai/gpt-6.2-astra", "high")))
  assert.equal(a.ready, true)
  assert.equal(calls[0].entries.length, 2)
  assert.deepEqual(a.seats.map((s) => s.seat), ["One", "Two"])
})

test("empty model list", async () => {
  const { roster, calls } = setup({ list: [] })
  const a = await roster(req(seat("A", "openai/gpt-6.2-astra")))
  assert.equal(a.ready, false)
  assert.equal(a.reason, "models_unavailable")
  assert.equal(a.seats[0].status, "unchecked")
  assert.equal(a.seats[0].paid_overflow, null)
  assert.equal(calls.length, 0)
})

test("check failure leaves seats unchecked", async () => {
  const bad = [{ state: "failed" }, { state: "checked", seats: [] },
    { state: "checked", seats: [{ provider: "openai", id: "other", status: "ok", paid_overflow: "blocked" }] },
    { state: "checked", seats: [{ provider: "openai", id: "gpt-6.2-astra", status: "great", paid_overflow: "blocked" }] }, () => null]
  for (const answer of bad) {
    const { roster } = setup({ answer })
    const a = await roster(req(seat("A", "openai/gpt-6.2-astra"), seat("Z", "openai/gpt-9")))
    assert.equal(a.ready, false)
    assert.equal(a.reason, "roster_unavailable")
    assert.equal(a.seats[0].status, "unchecked")
    assert.equal(a.seats[1].status, "unknown_model")
  }
  const thrown = createRoster({ models: async () => LIST, check: async () => { throw new Error("x") }, providers: new Set(["openai"]) })
  assert.equal((await thrown(req(seat("A", "openai/gpt-6.2-astra")))).reason, "roster_unavailable")
})

test("billing and quota statuses pass through", async () => {
  const { roster } = setup({ answer: (es) => ({ state: "checked", ...FALSE, seats: [
    { provider: es[0].provider, id: es[0].id, status: "quota_exhausted", paid_overflow: null },
    { provider: es[1].provider, id: es[1].id, status: "billing_unverified", paid_overflow: null }] }) })
  const a = await roster(req(seat("A", "openai/gpt-6.2-astra"), seat("B", "openai/gpt-6.1-sol")))
  assert.deepEqual(a.seats.map((s) => s.status), ["quota_exhausted", "billing_unverified"])
  assert.equal(a.ready, false)
  assert.equal(a.reason, null)
})

test("invalid requests", async () => {
  const { roster, calls } = setup()
  const s = seat("A", "openai/gpt-6.2-astra")
  const six = Array.from({ length: 6 }, (_, i) => seat(`S${i}`, "openai/gpt-6.2-astra"))
  const cases = [req(), req(...six), req(seat("Opus", "openai/x"), seat("opus", "openai/y")), req(seat("A", "openai")),
    req(seat("A", "/x")), req(seat("A", "openai/")), req(seat("A", `openai/${"x".repeat(260)}`)), req(seat("1A", "openai/x")),
    req(seat("A", "openai/x", "Bad Variant")), { ...req(s), extra: 1 }, { v: 2, seats: [s] }, { seats: [s] },
    req({ ...s, extra: 1 }), req({ seat: "A", model: "openai/x" }), null, "x", { v: 1, seats: "no" }]
  for (const c of cases) {
    const a = await roster(c)
    assert.deepEqual(a, { ready: false, reason: "invalid_request", seats: [], ...FALSE })
  }
  assert.equal(calls.length, 0)
})

test("check receives entries without name", async () => {
  const { roster, calls } = setup()
  const signal = new AbortController().signal
  await roster(req(seat("A", "openai/gpt-6.2-astra")), signal)
  assert.equal(calls[0].signal, signal)
  assert.deepEqual(calls[0].entries, [{ provider: "openai", id: "gpt-6.2-astra", model: "gpt-6.2-astra", family: "gpt",
    variants: ["high"], context: 1000, released: 200 }])
})
