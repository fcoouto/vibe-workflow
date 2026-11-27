import { createAdvisor, defaultCheckout } from "./pr-advice.js"
import { createSkillAdvisor } from "./skill-advice.js"

// The host gives the model `result.content` (a string, or text parts) and falls back to `result.output` only when
// `content` is empty, so the note goes into `content`. Any shape not listed here is left alone.
function appendNote(event, note) {
  try {
    const result = event.result
    const text = `\n\n${note}`
    if (typeof result === "string") event.result = `${result}${text}`
    else if (result && typeof result === "object") {
      const { content, output } = result
      if (typeof content === "string") event.result = { ...result, content: `${content}${text}` }
      else if (Array.isArray(content) && content.length) {
        event.result = { ...result, content: [...content, { type: "text", text }] }
      } else if (output !== undefined) {
        const base = typeof output === "string" ? output : JSON.stringify(output)
        if (base !== undefined) event.result = { ...result, content: [{ type: "text", text: `${base}${text}` }] }
      }
    }
  } catch {}
}

// The host's event shape varies by version: every field is read defensively and a missing one keeps the note silent.
export async function registerAdvice(ctx, { settings, onboarded, primary, checkout = defaultCheckout }) {
  const advisor = createAdvisor({ settings, primary, checkout, onboarded })
  const skills = createSkillAdvisor({ settings, onboarded })
  let closed = false
  const close = () => { closed = true; advisor.clear() }
  if (typeof ctx.tool?.hook !== "function") return { registered: false, close }
  try {
    await ctx.tool.hook("execute.after", async (event) => {
      try {
        if (closed) return
        const input = event?.input ?? event?.args
        if (event?.tool === "skill") {
          const note = event.status === "completed" ? await skills.skillNote(input?.id ?? input?.name) : null
          if (note) appendNote(event, note)
          return
        }
        if (typeof input?.command !== "string") return
        const exits = [event.result?.metadata?.exit, event.result?.metadata?.exitCode, event.exitCode, event.metadata?.exit]
        const ok = event.status === "completed" && !exits.some((code) => typeof code === "number" && code !== 0)
        const note = await advisor.advise({ tool: event.tool, command: input.command, ok,
          directory: input.workdir ?? input.cwd ?? ctx.location?.directory }, event.sessionID ?? event.session?.id)
        if (note) appendNote(event, note)
      } catch {}
    })
  } catch { return { registered: false, close } }
  return { registered: true, close }
}
