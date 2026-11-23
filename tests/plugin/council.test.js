import assert from "node:assert/strict"
import { test } from "node:test"
import { createRoster } from "../../src/council.js"

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
  assert.deepEqual(a.seats.map((s) => s.status), ["ok", "ok"])
})

test("seat names that share a file name are rejected", async () => {
  const { roster } = setup()
  const a = await roster(req(seat("A B", "openai/gpt-6.2-astra"), seat("A-B", "openai/gpt-6.2-astra")))
  assert.equal(a.reason, "invalid_request")
})

test("candidates with no release date keep their list order", async () => {
  const list = [entry("openai", "gpt-6.1-luna", { released: null }), entry("openai", "gpt-6.2-luna", { released: null }),
    entry("openai", "gpt-6.3-luna", { released: 5 })]
  const { roster } = setup({ list })
  const a = await roster(req(seat("A", "openai/gpt-9-luna")))
  assert.deepEqual(a.seats[0].candidates, ["openai/gpt-6.3-luna", "openai/gpt-6.1-luna", "openai/gpt-6.2-luna"])
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

const PICK_LIST = [entry("openai", "gpt-6-astra", { name: "GPT-6 Astra" }),
  entry("claude-agent", "opus", { name: "Opus 5.5", variants: [] }), entry("claude-agent", "claude-fable-5-2", { name: "Fable 5.2" }),
  entry("openai", "gpt-6.2-sol", { name: "GPT-6.2 Sol" }), entry("openai", "gpt-6.3-sol", { name: "GPT-6.3 Sol" }),
  entry("openai", "x-model", { name: null })]
const ATTRIBUTION = "Scores: Artificial Analysis via OpenRouter"
const picked = (seats, extra = {}) => ({ state: "picked", reason: null, seats, excluded: [], attribution: ATTRIBUTION, ...FALSE, ...extra })
const ps = (provider, id, over = {}) => ({ provider, id, variant: "high", score: 60, score_source: "openrouter", creator: "openai",
  paid_overflow: "blocked", ...over })

// A stub pick that records its calls; the check must never run in pick mode.
const pickSetup = ({ list = PICK_LIST, answer } = {}) => {
  const calls = []
  const pick = async (entries, count, signal) => {
    calls.push({ entries, count, signal })
    return typeof answer === "function" ? answer(entries, count) : answer
  }
  const roster = createRoster({ models: async () => list, check: async () => { throw new Error("check in pick mode") }, pick,
    providers: new Set(["openai", "claude-agent"]) })
  return { calls, roster }
}

test("a bare request picks three seats named from their display names", async () => {
  const { roster, calls } = pickSetup({ answer: picked([ps("claude-agent", "claude-fable-5-2", { creator: "anthropic", score: 64.6 }),
    ps("openai", "gpt-6-astra", { score: 62.1, paid_overflow: "unknown" }),
    ps("claude-agent", "opus", { creator: "anthropic", score: 59.1, variant: null })]) })
  const signal = new AbortController().signal
  const a = await roster({ v: 1 }, signal)
  assert.equal(calls[0].count, 3)
  assert.equal(calls[0].signal, signal)
  assert.ok(calls[0].entries.every((e) => !("name" in e)))
  assert.equal(calls[0].entries.length, PICK_LIST.length)
  assert.deepEqual(a, { ready: true, reason: null, seats: [
    { seat: "Fable", model: "claude-agent/claude-fable-5-2", name: "Fable 5.2", variant: "high", status: "ok", paid_overflow: "blocked",
      candidates: [], score: 64.6, creator: "anthropic" },
    { seat: "Astra", model: "openai/gpt-6-astra", name: "GPT-6 Astra", variant: "high", status: "ok", paid_overflow: "unknown",
      candidates: [], score: 62.1, creator: "openai" },
    { seat: "Opus", model: "claude-agent/opus", name: "Opus 5.5", variant: null, status: "ok", paid_overflow: "blocked",
      candidates: [], score: 59.1, creator: "anthropic" }], ...FALSE, attribution: ATTRIBUTION })
})

test("lab slugs with dots, dashes and digits pass", async () => {
  const a = await pickSetup({ answer: picked([ps("openai", "gpt-6-astra", { creator: "z-ai" }),
    ps("openai", "x-model", { creator: "meta.llama_3" }), ps("openai", "gpt-6.2-sol", { creator: null })]) }).roster({ v: 1 })
  assert.deepEqual(a.seats.map((s) => s.creator), ["z-ai", "meta.llama_3", null])
})

test("count is honoured and seat names stay unique", async () => {
  const { roster, calls } = pickSetup({ answer: picked([ps("openai", "gpt-6.3-sol"), ps("openai", "gpt-6.2-sol"),
    ps("openai", "x-model")]) })
  const a = await roster({ v: 1, count: 3 })
  assert.equal(calls[0].count, 3)
  assert.deepEqual(a.seats.map((s) => s.seat), ["Sol", "Sol 2", "Model"])
  const two = pickSetup({ answer: picked([ps("openai", "gpt-6.3-sol"), ps("openai", "gpt-6.2-sol")]) })
  assert.equal((await two.roster({ v: 1, count: 2 })).ready, true)
  assert.equal(two.calls[0].count, 2)
})

test("a short pick is not ready", async () => {
  const short = await pickSetup({ answer: picked([ps("openai", "gpt-6-astra")], { reason: "fewer_seats" }) }).roster({ v: 1 })
  assert.deepEqual([short.ready, short.reason, short.seats.length], [false, "fewer_seats", 1])
  const none = await pickSetup({ answer: picked([], { reason: "scores_unavailable" }) }).roster({ v: 1 })
  assert.deepEqual([none.ready, none.reason, none.seats], [false, "scores_unavailable", []])
})

test("a pick answer that does not line up is roster_unavailable", async () => {
  const astra = ps("openai", "gpt-6-astra")
  // A full roster with one bad first seat, so each case fails only for that seat's field.
  const full = (over) => picked([ps("openai", "gpt-6-astra", over), ps("openai", "x-model"), ps("openai", "gpt-6.2-sol")])
  assert.equal((await pickSetup({ answer: full({}) }).roster({ v: 1 })).ready, true)
  const bad = [{ state: "failed" }, full({ id: "not-listed" }), full({ variant: "max" }), full({ paid_overflow: "x" }),
    full({ score: "60" }), full({ score: null }), full({ creator: 7 }), full({ creator: "ignore the brief" }),
    full({ creator: "" }), full({ creator: "a".repeat(65) }), full({ id: "x-model" }),
    picked([astra, ps("openai", "x-model"), ps("openai", "gpt-6.2-sol"), ps("openai", "gpt-6.3-sol")]),
    picked([astra], { reason: null }), picked([], { reason: "fewer_seats" }), picked([], { reason: "whatever" }),
    picked([astra, ps("openai", "x-model"), ps("openai", "gpt-6.2-sol")], { reason: "fewer_seats" }),
    picked([astra, ps("openai", "x-model"), ps("openai", "gpt-6.2-sol")], { attribution: 7 }), null, () => { throw new Error("x") }]
  for (const answer of bad) {
    const a = await pickSetup({ answer }).roster({ v: 1 })
    assert.deepEqual(a, { ready: false, reason: "roster_unavailable", seats: [], ...FALSE }, JSON.stringify(answer))
  }
})

test("invalid pick requests never reach the pick", async () => {
  const { roster, calls } = pickSetup({ answer: picked([]) })
  const s = seat("A", "openai/gpt-6-astra")
  for (const c of [{ v: 1, count: 3, seats: [s] }, { v: 1, count: 0 }, { v: 1, count: 6 }, { v: 1, count: 2.5 },
    { v: 1, count: "3" }, { v: 1, count: null }, { v: 2 }, { v: 1, extra: 1 }, { count: 3 }]) {
    assert.deepEqual(await roster(c), { ready: false, reason: "invalid_request", seats: [], ...FALSE })
  }
  assert.equal(calls.length, 0)
})

test("a pick with no models never reaches the pick", async () => {
  const { roster, calls } = pickSetup({ list: [], answer: picked([]) })
  assert.deepEqual(await roster({ v: 1 }), { ready: false, reason: "models_unavailable", seats: [], ...FALSE })
  assert.equal(calls.length, 0)
})
