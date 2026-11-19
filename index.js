// OpenCode V2's structural server-plugin contract; no SDK dependency is needed.
import { spawn } from "node:child_process"
import { homedir } from "node:os"
import { dirname, isAbsolute, join } from "node:path"
import { fileURLToPath } from "node:url"
import { registerOnboarding } from "./onboarding.js"
import { createProjectSettings } from "./project.js"

const ROOT = dirname(fileURLToPath(import.meta.url))
const MAX_BYTES = 65536
const MAX_NOTICE_SESSIONS = 128
const blocked = () => ({
  state: "blocked", reason: "selector_unavailable", route: null, excluded: [],
  admission: false, reservation: false, spend_permit: false,
})
const actions = new Map([
  ["unconfigured", "Check Smart Router configuration."],
  ["disabled", "Check whether your Smart Router configuration is enabled."],
  ["table_empty", "Add eligible entries to the Smart Router strategy table."],
  ["no_recommendation", "Check the strategy table for this kind of work and its constraints."],
  ["billing_unverified", "Check your native sign-in and selected account."],
  ["overflow_not_blocked", "Review paid-overflow settings for the selected account."],
  ["quota_exhausted", "Check usage limits or try again later."],
  ["account_changed", "Check the selected account before requesting another recommendation."],
  ["selector_unavailable", "Check that Python and the plugin package are available, then try again."],
])

function failureAdvice(answer) {
  const exclusions = Array.isArray(answer.excluded) ? answer.excluded : []
  const excluded = exclusions[0]?.reason
  const cause = actions.has(answer.reason) ? answer.reason :
    answer.reason === "no_eligible_route" && actions.has(excluded)
      && exclusions.every((entry) => entry?.reason === excluded) ? excluded : "generic"
  const action = actions.get(cause) ?? "Check routing settings or account availability."
  return [cause, `vibe-workflow: Smart Router found no eligible route. ${action} Continuing with your client's existing selection.`]
}
const input = {
  type: "object", additionalProperties: false, required: ["v", "work_kind"],
  properties: {
    v: { type: "integer", const: 1 },
    work_kind: { type: "string", enum: ["implementation", "review", "investigation", "planning", "testing", "writing"] },
    profile: { type: "object" },
    constraints: { type: "object" },
  },
}

export default {
  id: "vibe-workflow",
  async setup(ctx) {
    const directory = ctx.location.directory
    const state = ctx.options?.state_dir ?? join(
      process.env.XDG_STATE_HOME || join(homedir(), ".local", "state"), "vibe-workflow", "smart-router")
    const active = new Set()
    const notices = new Map()
    let closed = false
    const project = createProjectSettings(directory)
    async function runRouter(request, { directory, stateDir, signal, operation }) {
      const setup = operation === "check_setup"
      const fallback = () => setup ? { state: "setup_needed", reason: "selector_unavailable" } : blocked()
      let raw
      try { raw = setup ? "" : JSON.stringify(request) } catch { return fallback() }
      if (closed || signal?.aborted || typeof directory !== "string" || !isAbsolute(directory)
        || typeof stateDir !== "string" || !isAbsolute(stateDir) || typeof raw !== "string"
        || Buffer.byteLength(raw) > MAX_BYTES) return fallback()
      return new Promise((resolve) => {
        let child, timer, killTimer, size = 0, chunks = [], failed = false
        const stop = () => {
          failed = true
          if (!child || child.exitCode !== null || child.signalCode !== null) return
          child.kill("SIGTERM")
          killTimer ??= setTimeout(() => child.kill("SIGKILL"), 3000)
        }
        try {
          const args = ["-I", "-B", join(ROOT, "scripts", "select.py"), "--state-dir", stateDir]
          if (setup) args.push("--check-setup")
          child = spawn("python3", args, { cwd: directory, stdio: ["pipe", "pipe", "ignore"] })
        } catch { resolve(fallback()); return }
        active.add(stop)
        signal?.addEventListener("abort", stop, { once: true })
        timer = setTimeout(stop, setup ? 5000 : 60000)
        child.stdin.on("error", stop)
        child.stdout.on("data", (chunk) => {
          size += chunk.length
          if (size > MAX_BYTES) stop()
          else chunks.push(chunk)
        })
        child.on("error", () => { failed = true })
        child.on("close", (code) => {
          clearTimeout(timer)
          clearTimeout(killTimer)
          active.delete(stop)
          signal?.removeEventListener("abort", stop)
          try {
            if (failed || code !== 0) throw Error()
            const value = JSON.parse(Buffer.concat(chunks).toString("utf8"))
            if (setup) {
              if (value.state === "ready" && value.reason === null) return resolve({ state: "ready", reason: null })
              if (value.state !== "setup_needed" || !["unconfigured", "disabled", "table_empty", "no_recommendation",
                "configuration_invalid", "selector_unavailable"].includes(value.reason)) throw Error()
              return resolve({ state: value.state, reason: value.reason })
            }
            if (!["selected", "no_selection", "blocked"].includes(value.state)
              || ["admission", "reservation", "spend_permit"].some((key) => value[key] !== false)
              || (value.state === "selected" ? !value.route : value.route !== null)) throw Error()
            resolve(value)
          } catch { resolve(fallback()) }
        })
        // Setup does not consume stdin and may already have closed it.
        child.stdin.end(raw)
        if (signal?.aborted) stop()
      })
    }
    await ctx.tool.transform((editor) => editor.add({
      name: "smart_router_select",
      description: "Get an advisory provider/model/effort choice. Does not launch or authorize execution.",
      input,
      execute: async (request, { signal, sessionID }) => {
        const preference = await project.inspect()
        if (preference.state !== "on") {
          if (preference.state === "off") notices.clear()
          const reason = preference.state === "off" ? "project_disabled" :
            preference.state === "not_onboarded" ? "project_not_onboarded" : "project_settings_invalid"
          return { content: JSON.stringify({ ...blocked(), state: "no_selection", reason, notice: null }) }
        }
        const answer = await runRouter(request, { directory, stateDir: state, signal, operation: "select" })
        let notice = null
        if (answer.state === "selected") notices.delete(sessionID)
        else if (!closed && !signal?.aborted) {
          const [cause, message] = failureAdvice(answer)
          const identified = typeof sessionID === "string" && sessionID.length > 0 && sessionID.length <= 256
          const seen = identified ? notices.get(sessionID) ?? new Set() : new Set()
          if (!seen.has(cause)) notice = message
          if (identified) {
            seen.add(cause)
            notices.delete(sessionID)
            notices.set(sessionID, seen)
            if (notices.size > MAX_NOTICE_SESSIONS) notices.delete(notices.keys().next().value)
          }
        }
        return { content: JSON.stringify({ ...answer, notice }) }
      },
    }))
    await registerOnboarding(ctx, { ...project, async apply(...args) {
      const result = await project.apply(...args)
      if (result.enabled === false) notices.clear()
      return result
    } }, (signal) =>
      runRouter(null, { directory, stateDir: state, signal, operation: "check_setup" }))
    await ctx.session.hook("context", async (event) => {
      if (closed) return
      const preference = await project.inspect()
      if (preference.state === "off") notices.clear()
      if (preference.state !== "on" || closed) return
      event.system.push({ type: "text", text:
        "Before delegating work, consult smart_router_select for an advisory provider/model/effort recommendation. " +
        "Use a recommendation only when compatible with the user's choices and the client's permitted native controls. " +
        "If no recommendation is available, show its notice in chat when present, then keep the client's existing selection. " +
        "Do not retry automatically or override a native denial. Routing grants no launch or spending authority. " +
        "The caller retains task ownership, native execution, reviews, permissions, continuation, and results." })
    })
    return () => {
      closed = true
      notices.clear()
      for (const stop of active) stop()
    }
  },
}
