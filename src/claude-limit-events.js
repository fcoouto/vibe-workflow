import { isAbsolute } from "node:path"

const PROVIDER = "claude-agent"
// The verified OpenCode 2.3.2 weekly notice. The clause is human text and is never parsed.
const NOTICE = /^You've hit your weekly limit · resets (\S.*?) Resets at (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d{1,3})?Z\.$/
const MAX_MESSAGE = 512
const MAX_ID = 256
const MAX_SEEN = 256
const LOOKUP_MS = 2000
// The same window as the probe's check in runtime/storage.py: 7 days plus CLOCK_MARGIN.
const WINDOW = 7 * 86400 + 60

// At most MAX_MESSAGE code points. Each code point is at least one UTF-16 unit and at most two, so longer text is
// refused before counting, and counting stops at the first code point past the limit.
function withinLimit(text) {
  if (text.length > 2 * MAX_MESSAGE) return false
  let count = 0
  for (const _ of text) if (++count > MAX_MESSAGE) return false
  return true
}

// The reset in whole epoch seconds, or null unless the timestamp is a real UTC instant.
function resetOf(message) {
  if (typeof message !== "string" || !withinLimit(message)) return null
  const match = NOTICE.exec(message)
  if (!match) return null
  const [, , stamp, fraction = "."] = match
  const iso = `${stamp}.${fraction.slice(1).padEnd(3, "0")}Z`
  const ms = Date.parse(iso)
  return Number.isFinite(ms) && new Date(ms).toISOString() === iso ? Math.floor(ms / 1000) : null
}

// Watches the one session.step.failed stream for a Claude weekly limit on the failed step's own model. It never
// awaits and never queues: a failure that arrives while one lookup or probe is open is dropped.
export function createClaudeLimitHandler(ctx, { location, signal, enabled, probe, now = Date.now }) {
  const seen = new Set()
  let busy = false
  const inWindow = (reset) => {
    const seconds = Math.floor(now() / 1000)
    return reset > seconds && reset <= seconds + WINDOW
  }
  const sameLocation = (other) => typeof other?.directory === "string" && isAbsolute(other.directory)
    && other.directory === location.directory && other.workspaceID === location.workspaceID
  const remember = (id) => {
    seen.add(id)
    if (seen.size > MAX_SEEN) seen.delete(seen.values().next().value)
  }
  async function lookupAndProbe(sessionID, assistantMessageID, reset) {
    if (!(await enabled()) || signal.aborted) return
    const lookup = AbortSignal.any([signal, AbortSignal.timeout(LOOKUP_MS)])
    const rows = await ctx.session.context({ sessionID }, { signal: lookup })
    if (lookup.aborted) return
    const list = Array.isArray(rows) ? rows : rows?.data
    const found = Array.isArray(list) ? list.filter((row) => row?.type === "assistant" && row.id === assistantMessageID) : []
    const model = found.length === 1 ? found[0].model : null
    if (model?.providerID !== PROVIDER || typeof model.id !== "string" || !model.id) return
    if (!(await enabled()) || signal.aborted || !inWindow(reset)) return
    await probe({ reset_at: reset })
  }
  return (event) => {
    try {
      const id = event?.id
      const data = event?.data
      if (typeof id !== "string" || !id || id.length > MAX_ID || busy || seen.has(id)) return
      if (typeof data?.sessionID !== "string" || !data.sessionID) return
      if (typeof data.assistantMessageID !== "string" || !data.assistantMessageID) return
      if (data.error?.type !== "provider.unknown" || !sameLocation(event.location) || signal.aborted) return
      if (typeof ctx?.session?.context !== "function") return
      const reset = resetOf(data.error.message)
      if (reset === null || !inWindow(reset)) return
      remember(id)
      busy = true
      lookupAndProbe(data.sessionID, data.assistantMessageID, reset).catch(() => {}).finally(() => { busy = false })
    } catch {}
  }
}
