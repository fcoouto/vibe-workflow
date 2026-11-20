import { registerSkill } from "./onboarding.js"

const NO_AUTHORITY = { admission: false, reservation: false, spend_permit: false }
const SEAT = /^[A-Za-z][A-Za-z0-9 _-]{0,31}$/
const VARIANT = /^[a-z0-9_-]{1,32}$/
const STATUSES = new Set(["ok", "billing_unverified", "quota_exhausted"])
const OVERFLOW = new Set(["blocked", "enabled", "unknown"])

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
    const key = s.seat.toLowerCase()
    if (names.has(key)) return false
    names.add(key)
  }
  return true
}

// Same-line suggestions for a model id that is not listed: the last word of the id, newest release first.
function candidates(list, provider, id) {
  const word = id.toLowerCase().match(/[a-z]+/g)?.at(-1)
  if (!word) return []
  return list.filter((m) => m.provider === provider && (m.id.toLowerCase().includes(word) || String(m.name ?? "").toLowerCase().includes(word)))
    .sort((a, b) => (b.released ?? -Infinity) - (a.released ?? -Infinity)).slice(0, 3).map((m) => `${m.provider}/${m.id}`)
}

// A checked answer only counts when it lines up seat for seat with what was sent.
function lined(answer, sent) {
  return answer?.state === "checked" && Array.isArray(answer.seats) && answer.seats.length === sent.length
    && answer.seats.every((s, i) => plain(s) && s.provider === sent[i].provider && s.id === sent[i].id && STATUSES.has(s.status)
      && (s.status === "ok" ? OVERFLOW.has(s.paid_overflow) : s.paid_overflow === null))
}

export function createRoster({ models, check, providers }) {
  return async function roster(request, signal) {
    const answer = (ready, reason, seats) => ({ ready, reason, seats, ...NO_AUTHORITY })
    if (!valid(request)) return answer(false, "invalid_request", [])
    let list
    try { list = await models() } catch { list = [] }
    if (!Array.isArray(list)) list = []
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
    description: "Check council seats before dispatch: exact model, plan provider, subscription billing, quota and paid overflow. " +
      "Never substitutes a model and launches nothing. Follow the vibe-council skill.",
    input: {
      type: "object", additionalProperties: false, required: ["v", "seats"],
      properties: {
        v: { type: "integer", const: 1 },
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
        text: extra ? `Use the vibe-council skill. ${prompt.text}` : "Use the vibe-council skill.",
        files: withoutMentions(prompt?.files), agents: withoutMentions(prompt?.agents),
        skills: withoutMentions(prompt?.skills), delivery,
      })
    },
  }))
}
