// OpenCode V2's structural server-plugin contract; no SDK dependency is needed.
import { spawn } from "node:child_process"
import { readFile } from "node:fs/promises"
import { homedir } from "node:os"
import { dirname, isAbsolute, join } from "node:path"
import { fileURLToPath } from "node:url"
import { registerOnboarding, registerSkill } from "./onboarding.js"
import { registerPrAdvice } from "./pr-advice.js"
import { createProjectSettings } from "./project.js"

const ROOT = dirname(fileURLToPath(import.meta.url))
const MAX_BYTES = 65536
const MAX_NOTICE_SESSIONS = 128
const blocked = () => ({
  state: "blocked", reason: "selector_unavailable", route: null, excluded: [],
  admission: false, reservation: false, spend_permit: false,
})
const actions = new Map([
  ["scores_unavailable", "Model scores are not available yet; Smart Router retries automatically."],
  ["no_models", "No runnable models were found on your connected plans."],
  ["unscored", "No scored model fits this task yet."],
  ["invalid_request", "Send v: 2 with task facts files, spec, risk."],
  ["no_recommendation", "No model clears this task's level within its constraints."],
  ["billing_unverified", "Check your native sign-in and selected account."],
  ["quota_exhausted", "Check usage limits or try again later."],
  ["account_changed", "Check the selected account before requesting another recommendation."],
  ["selector_unavailable", "Check that Python and the plugin package are available, then try again."],
])
const disclosure = "vibe-workflow: Smart Router downloads public model scores from openrouter.ai about weekly and sends no " +
  "credentials. Scores: Artificial Analysis via OpenRouter."
const noModels = () => ({ ...blocked(), state: "no_selection", reason: "no_models" })
const rubric = { files: ["1-2", "3+", "cross-system"], spec: ["exact", "prose", "open"],
  risk: ["reversible", "shared", "critical"] }

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
  type: "object", additionalProperties: false, required: ["v", "work_kind", "task"],
  properties: {
    v: { type: "integer", const: 2 },
    work_kind: { type: "string", enum: ["implementation", "review", "investigation", "planning", "testing", "writing"] },
    task: {
      type: "object", additionalProperties: false, required: Object.keys(rubric),
      properties: { ...Object.fromEntries(Object.entries(rubric).map(([key, values]) => [key, { enum: values }])),
        context_tokens: { type: "integer", minimum: 0 } },
    },
    constraints: { type: "object" },
  },
}
const description = "Get an advisory provider/model/effort choice. Does not launch or authorize execution. " +
  "Describe the task by its observable facts, levels in order: " +
  Object.entries(rubric).map(([key, values]) => `${key}: ${values.join(" | ")}`).join("; ") + "."

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
    // Only models on a path the router knows can be routed; this also keeps the request under MAX_BYTES.
    const providers = new Set(JSON.parse(await readFile(join(ROOT, "config", "routes.json"), "utf8").catch(() => "{}"))
      .paths?.map((path) => path.provider))
    let refreshing = false
    async function models() {
      try {
        const list = await ctx.model.list()
        return list.filter((m) => m.enabled && m.status !== "deprecated" && m.capabilities?.tools !== false
          && providers.has(m.providerID)).slice(0, 512).map((m) => ({ provider: m.providerID, id: m.id,
          model: m.modelID ?? null, family: m.family ?? null, variants: (m.variants ?? []).map((v) => v.id),
          context: m.limit?.context ?? null, released: m.time?.released ?? null }))
      } catch { return [] }
    }
    async function runRouter(request, { directory, stateDir, signal, operation, allowFetch }) {
      const setup = operation === "check_setup", refresh = operation === "refresh"
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
          if (setup) args.push("--check-setup", ...allowFetch ? ["--allow-fetch"] : [])
          if (refresh) args.push("--refresh")
          child = spawn("python3", args, { cwd: directory, stdio: ["pipe", "pipe", "ignore"] })
        } catch { resolve(fallback()); return }
        active.add(stop)
        signal?.addEventListener("abort", stop, { once: true })
        timer = setTimeout(stop, refresh ? 15000 : setup ? (allowFetch ? 10000 : 5000) : 60000)
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
            if (refresh) return resolve(null)
            const value = JSON.parse(Buffer.concat(chunks).toString("utf8"))
            if (setup) {
              if (value.state === "ready" && value.reason === null) return resolve({ state: "ready", reason: null })
              if (value.state !== "setup_needed" || !["scores_unavailable", "selector_unavailable"].includes(value.reason)) throw Error()
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
      description,
      input,
      execute: async (request, { signal, sessionID }) => {
        const preference = await project.inspect()
        if (preference.state !== "on") {
          if (preference.state === "off") notices.clear()
          const reason = preference.state === "off" ? "project_disabled" :
            preference.state === "not_onboarded" ? "project_not_onboarded" : "project_settings_invalid"
          return { content: JSON.stringify({ ...blocked(), state: "no_selection", reason, notice: null }) }
        }
        const { models: _ignored, ...own } = request ?? {}
        const list = ctx.model ? await models() : []
        let answer = noModels(), firstFetch = false
        if (list.length) {
          const { refresh, first_fetch, ...rest } = await runRouter({ ...own, models: list },
            { directory, stateDir: state, signal, operation: "select" })
          answer = rest
          firstFetch = first_fetch === true
          if (Array.isArray(refresh?.wanted) && !refreshing && !closed) {
            refreshing = true
            runRouter({ wanted: refresh.wanted }, { directory, stateDir: state, operation: "refresh" })
              .finally(() => { refreshing = false })
          }
        }
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
        if (firstFetch && !closed) notice = disclosure
        return { content: JSON.stringify({ ...answer, notice }) }
      },
    }))
    const gitWorkflow = createProjectSettings(directory, "git_workflow", { atProject: true })
    const prAdvice = await registerPrAdvice(ctx, { settings: () => gitWorkflow.inspect(),
      primary: async () => (await gitWorkflow.inspect()).project ?? null })
    await registerOnboarding(ctx, { smart_router: { ...project, async apply(...args) {
      const result = await project.apply(...args)
      if (result.enabled === false) notices.clear()
      return result
    } }, git_workflow: gitWorkflow }, (signal, allowFetch) =>
      runRouter(null, { directory, stateDir: state, signal, operation: "check_setup", allowFetch }),
    () => prAdvice.registered ? { state: "ready", reason: null } : { state: "setup_needed", reason: "guard_unavailable" })
    await registerSkill(ctx, "vibe-prepare-my-workspace")
    await registerSkill(ctx, "vibe-pull-requests")
    await ctx.session.hook("context", async (event) => {
      if (closed) return
      if (!prAdvice.registered && (await gitWorkflow.inspect()).state === "on") {
        event.system.push({ type: "text", text:
          "vibe-workflow: open pull requests as [BLOCKED] drafts and follow the vibe-pull-requests skill." })
      }
      const preference = await project.inspect()
      if (preference.state === "off") notices.clear()
      if (preference.state !== "on" || closed) return
      event.system.push({ type: "text", text:
        "Before delegating work, consult smart_router_select for an advisory provider/model/effort recommendation, " +
        "describing the task by its facts: files, spec, risk. " +
        "Use a recommendation only when compatible with the user's choices and the client's permitted native controls. " +
        "If no recommendation is available, show its notice in chat when present, then keep the client's existing selection. " +
        "Do not retry automatically or override a native denial. Routing grants no launch or spending authority. " +
        "The caller retains task ownership, native execution, reviews, permissions, continuation, and results." })
    })
    return () => {
      closed = true
      prAdvice.close()
      notices.clear()
      for (const stop of active) stop()
    }
  },
}
