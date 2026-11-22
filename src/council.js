import { registerSkill } from "./onboarding.js"

const NO_AUTHORITY = { admission: false, reservation: false, spend_permit: false }
const SEAT = /^[A-Za-z][A-Za-z0-9 _-]{0,31}$/
const VARIANT = /^[a-z0-9_-]{1,32}$/
// A lab slug from the public score download: untrusted text, so only a plain slug reaches the agent.
const CREATOR = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/
const STATUSES = new Set(["ok", "billing_unverified", "quota_exhausted"])
const OVERFLOW = new Set(["blocked", "enabled", "unknown"])
const EMPTY = new Set(["no_models", "no_eligible_route", "scores_unavailable", "unscored", "no_recommendation"])
const DEFAULT_COUNT = 3

const plain = (v) => v !== null && typeof v === "object" && !Array.isArray(v)
const exactly = (o, keys) => plain(o) && Object.keys(o).length === keys.length && keys.every((k) => k in o)

// Splits "provider/id" at the first slash; null unless both sides have text.
function split(model) {
  if (typeof model !== "string" || model.length > 256) return null
  const i = model.indexOf("/")
  return i > 0 && i < model.length - 1 ? { provider: model.slice(0, i), id: model.slice(i + 1) } : null
}

function valid(request) {
  if (!exactly(request, ["v", "seats"]) || request.v !== 1 || !Array.isArray(request.seats)) return false
  const { seats } = request
  if (seats.length < 1 || seats.length > 5) return false
  const names = new Set()
  for (const s of seats) {
    if (!exactly(s, ["seat", "model", "variant"]) || typeof s.seat !== "string" || !SEAT.test(s.seat)) return false
    if (!split(s.model) || (s.variant !== null && !(typeof s.variant === "string" && VARIANT.test(s.variant)))) return false
    const key = s.seat.toLowerCase().replaceAll(" ", "-")
    if (names.has(key)) return false
    names.add(key)
  }
  return true
}

// A pick request is `{v: 1}` with an optional seat count; it never carries seats.
function pickCount(request) {
  if (!plain(request) || request.v !== 1 || !Object.keys(request).every((k) => k === "v" || k === "count")) return null
  if (!("count" in request)) return DEFAULT_COUNT
  return Number.isInteger(request.count) && request.count >= 1 && request.count <= 5 ? request.count : null
}

// The last word of the display name ("Opus 5.5" gives "Opus"), or of the id, numbered when taken.
function seatName(entry, taken) {
  const word = [entry.name, entry.id].map((t) => String(t ?? "").match(/[A-Za-z]+/g)?.at(-1)).find(Boolean) ?? "Seat"
  const base = (word[0].toUpperCase() + word.slice(1)).slice(0, 29)
  let name = base
  for (let n = 2; taken.has(name.toLowerCase()); n++) name = `${base} ${n}`
  taken.add(name.toLowerCase())
  return name
}

// A picked answer only counts when every seat is a listed model and the reason matches the seat count.
function pickedRows(answer, list, count) {
  if (answer?.state !== "picked" || !Array.isArray(answer.seats) || answer.seats.length > count
    || typeof answer.attribution !== "string" || answer.attribution.length > 200) return null
  const n = answer.seats.length
  if (!(n === count ? answer.reason === null : n > 0 ? answer.reason === "fewer_seats" : EMPTY.has(answer.reason))) return null
  const taken = new Set(), seen = new Set(), rows = []
  for (const s of answer.seats) {
    const hit = plain(s) ? list.find((m) => m.provider === s.provider && m.id === s.id) : null
    if (!hit || seen.has(`${hit.provider}/${hit.id}`) || !(s.variant === null || hit.variants?.includes(s.variant))
      || !OVERFLOW.has(s.paid_overflow) || typeof s.score !== "number" || !Number.isFinite(s.score)
      || !(s.creator === null || (typeof s.creator === "string" && CREATOR.test(s.creator)))) return null
    seen.add(`${hit.provider}/${hit.id}`)
    rows.push({ seat: seatName(hit, taken), model: `${hit.provider}/${hit.id}`, name: hit.name ?? null, variant: s.variant,
      status: "ok", paid_overflow: s.paid_overflow, candidates: [], score: s.score, creator: s.creator })
  }
  return rows
}

// Same-line suggestions for a model id that is not listed: the last word of the id, newest release first.
function candidates(list, provider, id) {
  const word = id.toLowerCase().match(/[a-z]+/g)?.at(-1)
  if (!word) return []
  return list.filter((m) => m.provider === provider && (m.id.toLowerCase().includes(word) || String(m.name ?? "").toLowerCase().includes(word)))
    .sort((a, b) => (b.released ?? 0) - (a.released ?? 0)).slice(0, 3).map((m) => `${m.provider}/${m.id}`)
}

// A checked answer only counts when it lines up seat for seat with what was sent.
function lined(answer, sent) {
  return answer?.state === "checked" && Array.isArray(answer.seats) && answer.seats.length === sent.length
    && answer.seats.every((s, i) => plain(s) && s.provider === sent[i].provider && s.id === sent[i].id && STATUSES.has(s.status)
      && (s.status === "ok" ? OVERFLOW.has(s.paid_overflow) : s.paid_overflow === null))
}

export function createRoster({ models, check, pick, providers }) {
  const answer = (ready, reason, seats) => ({ ready, reason, seats, ...NO_AUTHORITY })
  async function listed() {
    try {
      const list = await models()
      return Array.isArray(list) ? list : []
    } catch { return [] }
  }
  async function picked(count, signal) {
    const list = await listed()
    if (list.length === 0) return answer(false, "models_unavailable", [])
    let got
    try { got = await pick(list.map(({ name, ...entry }) => entry), count, signal) } catch { got = null }
    const rows = pickedRows(got, list, count)
    if (!rows) return answer(false, "roster_unavailable", [])
    return { ...answer(rows.length === count, got.reason, rows), attribution: got.attribution }
  }
  return async function roster(request, signal) {
    const count = pickCount(request)
    if (count !== null) return picked(count, signal)
    if (!valid(request)) return answer(false, "invalid_request", [])
    const list = await listed()
    if (list.length === 0) return answer(false, "models_unavailable", request.seats.map((s) => ({ seat: s.seat, model: s.model,
      name: null, variant: s.variant, status: "unchecked", paid_overflow: null, candidates: [] })))
    const rows = request.seats.map((s) => {
      const { provider, id } = split(s.model)
      const row = { seat: s.seat, model: s.model, name: null, variant: s.variant, status: "unchecked", paid_overflow: null, candidates: [] }
      if (!providers.has(provider)) return { row: { ...row, status: "provider_not_on_plan" } }
      const hit = list.find((m) => m.provider === provider && m.id === id)
      if (!hit) return { row: { ...row, status: "unknown_model", candidates: candidates(list, provider, id) } }
      row.name = hit.name ?? null
      if (s.variant !== null && !hit.variants?.includes(s.variant)) return { row: { ...row, status: "unknown_variant" } }
      const { name, ...sent } = hit
      return { row, sent }
    })
    const sent = rows.filter((r) => r.sent).map((r) => r.sent)
    const rowsOnly = rows.map((r) => r.row)
    if (sent.length === 0) return answer(rowsOnly.every((r) => r.status === "ok"), null, rowsOnly)
    let checked
    try { checked = await check(sent, signal) } catch { checked = null }
    if (!lined(checked, sent)) return answer(false, "roster_unavailable", rowsOnly)
    let at = 0
    for (const r of rows) if (r.sent) {
      const c = checked.seats[at++]
      r.row.status = c.status
      r.row.paid_overflow = c.paid_overflow
    }
    return answer(rowsOnly.every((r) => r.status === "ok"), null, rowsOnly)
  }
}

const SEAT_PATTERN = SEAT.source
const VARIANT_PATTERN = VARIANT.source

// Registers the skill, the roster tool and, when the host has a command API, the command that starts the skill.
export async function registerCouncil(ctx, { roster }) {
  await registerSkill(ctx, "vibe-council")
  await ctx.tool.transform((editor) => editor.add({
    name: "vibe_council_roster",
    description: "Pick council seats by the seat rule with {v: 1} and an optional count (frontier models, one per lab first), " +
      "or check named seats with {v: 1, seats}: exact model, plan provider, subscription billing, quota and paid overflow. " +
      "Never substitutes a named model and launches nothing. Follow the vibe-council skill.",
    input: {
      type: "object", additionalProperties: false, required: ["v"],
      properties: {
        v: { type: "integer", const: 1 },
        count: { type: "integer", minimum: 1, maximum: 5 },
        seats: {
          type: "array", minItems: 1, maxItems: 5,
          items: {
            type: "object", additionalProperties: false, required: ["seat", "model", "variant"],
            properties: {
              seat: { type: "string", pattern: SEAT_PATTERN },
              model: { type: "string", minLength: 3, maxLength: 256 },
              variant: { anyOf: [{ type: "null" }, { type: "string", pattern: VARIANT_PATTERN }] },
            },
          },
        },
      },
    },
    execute: async (request, { signal } = {}) => ({ content: JSON.stringify(await roster(request, signal)) }),
  }))
  if (typeof ctx.command?.transform !== "function") return
  const withoutMentions = (items) => items?.map(({ mention, ...item }) => item)
  await ctx.command.transform((editor) => editor.add({
    name: "vibe-council",
    description: "Run the council on a set of decisions.",
    execute: async ({ sessionID, prompt, delivery }) => {
      const extra = typeof prompt?.text === "string" ? prompt.text.trim() : ""
      await ctx.session.prompt({
        ...prompt, sessionID,
        text: extra ? `Use the vibe-council skill. ${extra}` : "Use the vibe-council skill.",
        files: withoutMentions(prompt?.files), agents: withoutMentions(prompt?.agents),
        skills: withoutMentions(prompt?.skills), delivery,
      })
    },
  }))
}
