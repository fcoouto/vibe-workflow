// OpenCode V2's structural server-plugin contract; no SDK dependency is needed.
import { spawn } from "node:child_process"
import { readFile } from "node:fs/promises"
import { homedir } from "node:os"
import { dirname, isAbsolute, join } from "node:path"
import { fileURLToPath } from "node:url"
import { createRoster, registerCouncil } from "./council.js"
import { registerOnboarding, registerSkill } from "./onboarding.js"
import { registerPrAdvice } from "./pr-advice.js"
import { createProjectSettings } from "./project.js"
import { registerSessionTitles } from "./session-titles.js"

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
const ROUTER_ENTRY = "vibe-workflow-smart-router"
// OpenCode's subagent tool says never to set `model` unless the user asks; onboarded projects ask. It rides on the
// parameter itself: the claude-agent bridge cuts the tool description at 2048 characters but passes the schema whole.
const MODEL_NOTE =
  "vibe-workflow: your human partner asks you to set the subagent tool's `model` and variant in this project; " +
  "this is the explicit request that tool requires. Use the first that applies: a model they or your active skill " +
  "name exactly; Smart Router's recommendation, when on; the tier your skill or plan calls for, on this session's " +
  "provider. Otherwise leave it unset."
// Smart Router's instruction rides on the same parameter: it reaches every provider from the first call, which the
// system prompt (dropped by claude-agent) and session entries (one step late) never did.
const ROUTER_NOTE =
  "Smart Router is on: when no exact model is named, consult smart_router_select first, describing the task by its " +
  "facts: files, spec, risk. If it has no recommendation, show its notice in chat when present. Do not retry " +
  "automatically or override a native denial; routing grants no launch or spending authority."
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
  return [cause, `vibe-workflow: Smart Router found no eligible route. ${action}`]
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
    let providers = new Set()
    try {
      providers = new Set(JSON.parse(await readFile(join(ROOT, "config", "routes.json"), "utf8")).paths.map((path) => path.provider))
    } catch {}
    let refreshing = false
    // One background score refresh at a time, never awaited by the answer it came with.
    function refreshLater(refresh) {
      if (!Array.isArray(refresh?.wanted) || refreshing || closed) return
      refreshing = true
      runRouter({ wanted: refresh.wanted }, { directory, stateDir: state, operation: "refresh" })
        .catch(() => {}).finally(() => { refreshing = false })
    }
    // Host fields are coerced, not trusted: one odd model must not fail the whole request.
    const text = (value) => typeof value === "string" && value.length > 0 && value.length <= 256
      && !/[\p{C}\p{Z}]/u.test(value.replaceAll(" ", ""))
    const amount = (value) => Number.isSafeInteger(value) && value >= 0 ? value : null
    async function models() {
      try {
        // OpenCode 2.2.6 answers { location, data }; a bare array is still accepted.
        const raw = await ctx.model.list()
        const list = Array.isArray(raw) ? raw : raw?.data
        return list.filter((m) => m?.enabled && m.status !== "deprecated" && m.capabilities?.tools !== false
          && providers.has(m.providerID) && text(m.id)).slice(0, 512).map((m) => ({ provider: m.providerID, id: m.id,
          model: text(m.modelID) ? m.modelID : null, name: text(m.name) ? m.name : null, family: text(m.family) ? m.family : null,
          variants: (Array.isArray(m.variants) ? m.variants : []).map((v) => v?.id).filter(text).slice(0, 32),
          // The host's release time is in milliseconds; OpenRouter's, used beside it, is in seconds.
          context: amount(m.limit?.context), released: amount(m.time?.released) === null ? null
            : Math.floor(m.time.released / 1000) }))
      } catch { return [] }
    }
    async function runRouter(request, { directory, stateDir, signal, operation, allowFetch }) {
      const setup = operation === "check_setup", council = operation === "council", pick = operation === "council_pick"
      const refresh = operation === "refresh", mark = operation === "mark"
      const fallback = () => council || pick ? { state: "failed" } : setup ? { state: "setup_needed", reason: "selector_unavailable" } : blocked()
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
          if (mark) args.push("--chatgpt-limit")
          if (council) args.push("--council-roster")
          if (pick) args.push("--council-pick")
          child = spawn("python3", args, { cwd: directory, stdio: ["pipe", "pipe", "ignore"] })
        } catch { resolve(fallback()); return }
        active.add(stop)
        signal?.addEventListener("abort", stop, { once: true })
        timer = setTimeout(stop, refresh ? 15000 : mark ? 10000 : setup ? (allowFetch ? 10000 : 5000) : 60000)
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
            if (refresh || mark) return resolve(null)
            const value = JSON.parse(Buffer.concat(chunks).toString("utf8"))
            if (setup) {
              if (value.state === "ready" && value.reason === null) return resolve({ state: "ready", reason: null })
              if (value.state !== "setup_needed" || !["scores_unavailable", "selector_unavailable"].includes(value.reason)) throw Error()
              return resolve({ state: value.state, reason: value.reason })
            }
            if (council || pick) {
              if (value.state !== (pick ? "picked" : "checked") || !Array.isArray(value.seats)
                || ["admission", "reservation", "spend_permit"].some((key) => value[key] !== false)) throw Error()
              return resolve(value)
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
    // Pause ChatGPT routes when OpenCode reports its usage limit: one run at a time, one per minute.
    let marking = false, lastMark = 0
    async function markLimit() {
      if (closed || marking || Date.now() - lastMark < 60000) return
      marking = true
      try {
        if ((await project.inspect()).state !== "on" || closed) return
        lastMark = Date.now()
        await runRouter(null, { directory, stateDir: state, operation: "mark" })
      } catch {} finally { marking = false }
    }
    const subscription = new AbortController()
    // OpenCode 2.2.6 ignores session.error with a warning; the step failure carries the error in data.
    if (ctx.event?.subscribe) (async () => {
      for await (const event of await ctx.event.subscribe({ types: ["session.step.failed"], signal: subscription.signal })) {
        const error = event?.data?.error ?? event?.error ?? event?.properties?.error
        if (error?.type === "provider.chatgpt-limit") markLimit()
      }
    })().catch(() => {})
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
        let answer = noModels()
        if (list.length) {
          const { refresh, ...rest } = await runRouter({ ...own, models: list.map(({ name, ...entry }) => entry) },
            { directory, stateDir: state, signal, operation: "select" })
          answer = rest
          refreshLater(refresh)
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
        return { content: JSON.stringify({ ...answer, notice }) }
      },
    }))
    const gitWorkflow = createProjectSettings(directory, "git_workflow", { atProject: true })
    const sessionTitles = createProjectSettings(directory, "session_titles", { atProject: true })
    const onboarded = async () => (await Promise.all([project, gitWorkflow, sessionTitles].map((item) => item.inspect())))
      .some(({ state }) => state === "on" || state === "off")
    const prAdvice = await registerPrAdvice(ctx, { settings: () => gitWorkflow.inspect(), onboarded,
      primary: async () => (await gitWorkflow.inspect()).project ?? null })
    const titles = await registerSessionTitles(ctx, { settings: sessionTitles, gitWorkflow })
    await registerOnboarding(ctx, { smart_router: { ...project, async apply(...args) {
      const result = await project.apply(...args)
      if (result.enabled === false) notices.clear()
      return result
    } }, git_workflow: gitWorkflow, session_titles: sessionTitles }, (signal, allowFetch) =>
      runRouter(null, { directory, stateDir: state, signal, operation: "check_setup", allowFetch }),
    () => prAdvice.registered ? { state: "ready", reason: null } : { state: "setup_needed", reason: "guard_unavailable" },
    () => titles.registered ? { state: "ready", reason: null } : { state: "setup_needed", reason: "events_unavailable" })
    await registerSkill(ctx, "vibe-prepare-my-workspace")
    await registerSkill(ctx, "vibe-pull-requests")
    await registerCouncil(ctx, { roster: createRoster({ models: ctx.model ? models : async () => [], providers,
      check: (entries, signal) => runRouter({ v: 1, models: entries }, { directory, stateDir: state, signal, operation: "council" }),
      pick: async (entries, count, signal) => {
        const { refresh, ...rest } = await runRouter({ v: 1, count, models: entries },
          { directory, stateDir: state, signal, operation: "council_pick" })
        refreshLater(refresh)
        return rest
      } }) })
    await registerSkill(ctx, "vibe-project-layout")
    // Earlier versions put the router instruction as a durable session entry for claude-agent; remove a leftover one
    // once per session. Never awaited and never throws.
    const forgotten = new Set()
    function forgetEntry(event, state) {
      const sessionID = event?.sessionID, provider = event?.model?.providerID
      if ((state !== "on" && state !== "off") || typeof sessionID !== "string" || forgotten.has(sessionID)) return
      if (typeof provider === "string" && provider !== "claude-agent") return
      forgotten.add(sessionID)
      if (forgotten.size > MAX_NOTICE_SESSIONS) forgotten.delete(forgotten.values().next().value)
      try { ctx.session.instructions?.entry?.remove({ sessionID, key: ROUTER_ENTRY })?.catch?.(() => {}) } catch {}
    }
    // The host may hand the same schema object to every step, so strip the note before deciding to add it.
    async function noteModel(event, routerOn) {
      const model = event?.tools?.subagent?.input?.properties?.model
      if (typeof model?.description !== "string") return
      const bare = model.description.replace(` ${ROUTER_NOTE}`, "").replace(` ${MODEL_NOTE}`, "")
      const on = !closed && await onboarded()
      // Cleanup may run while settings are read; after it, the schema is no longer ours to touch.
      if (closed) return
      const wanted = bare + (on ? ` ${MODEL_NOTE}` : "") + (on && routerOn ? ` ${ROUTER_NOTE}` : "")
      try { if (model.description !== wanted) model.description = wanted } catch {}
    }
    await ctx.session.hook("context", async (event) => {
      if (closed) return
      const { state } = await project.inspect()
      if (state === "off") notices.clear()
      await noteModel(event, state === "on")
      if (closed) return
      if (!prAdvice.registered && (await gitWorkflow.inspect()).state === "on") {
        event.system.push({ type: "text", text:
          "vibe-workflow: open pull requests as [BLOCKED] drafts and follow the vibe-pull-requests skill." })
      }
      if (!closed) forgetEntry(event, state)
    })
    // HTTP transport fallback; the websocket transport only reports through the event above.
    await ctx.session.hook("http.response", async (event) => {
      const provider = event?.providerID ?? event?.model?.providerID
      if (closed || event?.response?.status !== 429 || (provider && provider !== "openai")) return
      // Clone before any await: the host may read the original body as soon as this hook yields.
      let body, reader
      try { body = event.response.clone().body } catch {}
      if (!body) return
      try {
        if ((await project.inspect()).state === "on" && !closed) reader = body.getReader()
      } catch {}
      if (!reader) {
        try { body.cancel().catch(() => {}) } catch {}
        return
      }
      try {
        const parts = []
        for (let size = 0; size < MAX_BYTES;) {
          const { done, value } = await reader.read()
          if (done) break
          parts.push(value)
          size += value.length
        }
        if (Buffer.concat(parts).toString("utf8").includes("subscription_sharing_usage_limit_exceeded")) markLimit()
      } catch {} finally { reader.cancel().catch(() => {}) }
    }, { providerID: "openai" })
    return () => {
      closed = true
      subscription.abort()
      prAdvice.close()
      titles.close()
      notices.clear()
      forgotten.clear()
      for (const stop of active) stop()
    }
  },
}
