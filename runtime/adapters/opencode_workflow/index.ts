// Stuck-pattern escalation gate for OpenCode 2.2.6 (V2 server plugin). Contract: clients/opencode-escalation-gate.md.
//
// Feeds observed edits, failed checks, finished child sessions and primary routes to the pure state machine in
// gate.ts, and applies its holds through `permission.evaluate`. Interactive trees only: ledger-derived sessions
// belong to the Smart Routing shim. State lives in memory; a reload starts afresh. Nothing is logged. Ordinary internal
// errors fail open; a captured stop-stage edit stays denied when its explicit form prerequisite fails.
import { execFile } from "node:child_process"
import { dirname, isAbsolute, join, resolve, sep } from "node:path"
import { fileURLToPath } from "node:url"
import { DEFAULT_SETTINGS, Gate, derivedSession, isCheck, parseSettings, resetRequest } from "./gate.ts"
import type { GateSettings, Route } from "./gate.ts"
import { StopApprovals } from "./stop.ts"
import type { StopForms } from "./stop.ts"

const PACKAGE = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..")
const FEATURE = "escalation_gate"
const FEATURE_TIMEOUT_MS = 5000
const LOOKUP_TIMEOUT_MS = 300
const MAX_LOOKUPS = 8
const FAILED_LOOKUP_MS = 5000
const MAX_REMEMBERED = 256
const EDIT_ACTIONS = new Set(["edit", "write", "patch"])
const SHELL_TOOLS = new Set(["shell", "bash"])
const FINISHED = "session.execution.succeeded"
const SESSION = /^ses_[A-Za-z0-9]{1,128}$/
const NAME = /^[A-Za-z0-9][A-Za-z0-9._:@+\/-]{0,127}$/

type Config = { featureStatus?: (directory: string) => Promise<unknown>;
  stopForms?: StopForms; stopNow?: () => number;
  registerStopOwner?: (owner: StopApprovals) => void | (() => void) | Promise<void | (() => void)> }

/** The route a `model.request` names, or null when it is not one. */
export function routeOf(model: unknown): Route | null {
  const value = model as { providerID?: unknown; id?: unknown; variant?: unknown } | null
  if (!value || typeof value.providerID !== "string" || !NAME.test(value.providerID)) return null
  if (typeof value.id !== "string" || !NAME.test(value.id)) return null
  const variant = typeof value.variant === "string" && NAME.test(value.variant) ? value.variant : null
  return { provider: value.providerID, model: value.id, variant }
}

/** A shell call's exit status from its result metadata, or null when the result does not carry one. */
export function exitStatus(event: unknown): number | null {
  const metadata = (event as { result?: { metadata?: Record<string, unknown> } } | null)?.result?.metadata
  for (const field of ["exit", "exitCode"]) {
    const value = metadata?.[field]
    if (typeof value === "number" && Number.isInteger(value)) return value
  }
  return null
}

/** Infer masked unittest failures from complete footers consistent with literal checks, not authenticated output. */
export function checkFailed(event: any, patterns = DEFAULT_SETTINGS.checkPatterns): boolean {
  const status = exitStatus(event)
  if (event?.status === "error" || (status !== null && status !== 0)) return true
  const command = event?.input?.command
  // Unsupported shell syntax stays unknown; the splits below apply only to this literal subset.
  if (event?.status !== "completed" || typeof command !== "string" || !/^[\w./:=+@%, \t&|;>-]+$/.test(command)) return false
  const checks = command.split(/&&|\|\||;/)
  for (const check of checks) {
    const [source, ...filters] = check.split("|").map((part) => part.trim())
    const invocation = source.replace(/[ \t]+2>&1$/, "")
    // Only option-free wrapper prefixes are eligible; isCheck cannot assign wrapper option values to their owners.
    const program = invocation.replace(/^(?:(?:[A-Za-z_]\w*=[^ \t]*|(?:\.\/)?(?:env|time|nice|nohup|command|exec|npx|(?:uv|poetry|pipenv|pnpm)[ \t]+(?:run|exec)))[ \t]+)+/, "")
    if (program.startsWith("-")) return false
    if (/[&>]/.test(invocation) || !isCheck(invocation, patterns)) return false
    if (filters.length > 1 || filters.some((filter) => !/^tail[ \t]+(?:-[1-9]\d*|-n[ \t]+[1-9]\d*)$/.test(filter))) return false
  }
  const content = event?.result?.content
  const texts = typeof content === "string" ? [content] : Array.isArray(content)
    ? content.filter((part) => part?.type === "text" && typeof part.text === "string").map((part) => part.text) : []
  const text = texts.join("\n").replace(/\r\n/g, "\n")
  const footers = [...text.matchAll(/^Ran (?:1 test|(?:0|[2-9]|[1-9]\d+) tests) in (?:0|[1-9]\d*)\.\d{3}s\n\n([^\n]+)/gm)]
  if (footers.length !== checks.length) return false
  const last = footers[footers.length - 1]
  if (text.slice(last.index! + last[0].length).trim() !== "") return false
  let failed = false
  for (const footer of footers) {
    const result = /^(OK|FAILED)(?: \((.+)\))?$/.exec(footer[1])
    if (!result) return false
    const seen = new Set<string>()
    let unsuccessful = false
    for (const count of result[2]?.split(", ") ?? []) {
      const field = /^(failures|errors|skipped|expected failures|unexpected successes)=(0|[1-9]\d*)$/.exec(count)
      if (!field || seen.has(field[1])) return false
      seen.add(field[1])
      if (field[2] !== "0" && ["failures", "errors", "unexpected successes"].includes(field[1])) unsuccessful = true
    }
    if ((result[1] === "FAILED") !== unsuccessful) return false
    failed ||= unsuccessful
  }
  return failed
}

function defaultFeatureStatus(directory: string): Promise<unknown> {
  const script = join(PACKAGE, "scripts", "workflow.py")
  return new Promise((done) => {
    execFile("python3", ["-I", "-B", script, "features", "status", FEATURE, "--cwd", directory],
      { timeout: FEATURE_TIMEOUT_MS, maxBuffer: 1024 * 1024 }, (_error, stdout) => {
        try { done(JSON.parse(String(stdout))) } catch { done(null) }
      })
  })
}

/** The resolved settings when the feature is effective for this project, otherwise null. */
function activeSettings(report: unknown): GateSettings | null {
  const feature = (report as { features?: Record<string, { effective?: unknown; values?: unknown }> } | null)
    ?.features?.[FEATURE]
  return feature?.effective === true ? parseSettings(feature.values ?? {}) : null
}

const within = <T>(promise: Promise<T>, ms: number) => new Promise<T>((done, fail) => {
  const timer = setTimeout(() => fail(new Error("timeout")), ms)
  promise.then((value) => { clearTimeout(timer); done(value) }, (error) => { clearTimeout(timer); fail(error) })
})

export function createWorkflowPlugin(config: Config = {}) {
  const featureStatus = config.featureStatus ?? defaultFeatureStatus
  return {
    id: "vibe-workflow" as const,
    async setup(ctx: any): Promise<() => void> {
      const directory: string = typeof ctx?.location?.directory === "string" ? ctx.location.directory : process.cwd()
      let gate: Gate | null = null
      let settings: GateSettings | null = null
      let lastError: string | null = null
      const remembered = new Map<string, { session: string; files: string[] }>()
      const failedLookups = new Map<string, number>()
      // No default native transport is qualified; the dependency seam never performs discovery or copies credentials.
      const stops = new StopApprovals({ directory, gate: () => gate, forms: config.stopForms, now: config.stopNow,
        error: (code) => { if (lastError !== "event_stream_lost") lastError = code } })
      const disposeReview = await config.registerStopOwner?.(stops)

      const guard = (name: string, body: (event: any) => Promise<void> | void) => async (event: any) => {
        if (!gate) return
        try {
          await body(event)
        } catch {
          lastError = `${name}_failed`
        }
      }

      const display = (text: string) => text.split(directory + sep).join("")
      const absolute = (resource: unknown) => {
        if (typeof resource !== "string" || resource === "") throw new Error("invalid resource")
        return isAbsolute(resource) ? resolve(resource) : resolve(directory, resource)
      }

      /** Make a session known: walk its parents through `session.get` until a known session or a root. */
      async function known(session: unknown): Promise<string | null> {
        if (typeof session !== "string" || !SESSION.test(session) || derivedSession(session)) return null
        if (gate!.knows(session)) return session
        if ((failedLookups.get(session) ?? 0) > Date.now()) return null
        const chain: [string, string | null][] = []
        let current = session
        for (let lookups = 0; lookups < MAX_LOOKUPS; lookups++) {
          let info: any
          try {
            info = await within(Promise.resolve().then(() => ctx.session.get({ sessionID: current })), LOOKUP_TIMEOUT_MS)
          } catch {
            break
          }
          const record = info?.id === current ? info : info?.data?.id === current ? info.data : null
          if (!record) break
          const parent = record.parentID == null ? null : String(record.parentID)
          chain.push([current, parent])
          if (parent === null || gate!.knows(parent)) {
            for (let i = chain.length - 1; i >= 0; i--) gate!.created(chain[i][0], chain[i][1], true)
            return gate!.knows(session) ? session : null
          }
          current = parent
        }
        failedLookups.set(session, Date.now() + FAILED_LOOKUP_MS)
        return null
      }

      // Lineage and finished runs from the live event stream.
      const events = new AbortController()
      let eventChannelAvailable = typeof ctx?.event?.subscribe === "function"
      if (eventChannelAvailable) {
        const consumeEvents = async () => {
          try {
            for await (const event of ctx.event.subscribe({ types: ["session.created", FINISHED, "form.replied",
              "form.cancelled", "session.execution.failed", "session.execution.interrupted"], signal: events.signal })) {
              if (!gate) continue
              stops.event(event)
              const data = (event as any)?.data
              if (typeof data?.sessionID !== "string") continue
              if ((event as any)?.type === "session.created") {
                if (data.parentID == null) gate.created(data.sessionID, null)
                else if (await known(data.parentID)) gate.created(data.sessionID, String(data.parentID))
              } else if ((event as any)?.type === FINISHED && gate.knows(data.sessionID)) {
                gate.finished(data.sessionID)
              }
            }
          } catch { /* handled below */ }
          // Releases depend on finished-run events: without them a hold could never end, so fail open.
          eventChannelAvailable = false
          stops.close()
          gate = null
          if (!events.signal.aborted) {
            lastError = "event_stream_lost"
          }
        }
        void consumeEvents()
      } else lastError = "event_stream_lost"

      await ctx.session.hook("model.request", guard("model_request", async (event) => {
        if (event?.kind !== "primary") return
        const route = routeOf(event?.model)
        const session = await known(event?.sessionID)
        if (route && session) gate!.routed(session, route)
      }))

      await ctx.tool.hook("execute.before", guard("tool_execute_before", async (event) => {
        if (event?.tool !== "edit") return
        if (await known(event?.sessionID)) stops.before(event)
      }))

      await ctx.permission.hook("evaluate", guard("permission_evaluate", async (event) => {
        if (!EDIT_ACTIONS.has(event?.action) || event.effect === "deny") return
        const resources = event.resources
        if (!Array.isArray(resources)) return
        const files = resources.map(absolute)
        const session = await known(event.sessionID)
        if (!session) return
        const decision = gate!.editDecision(session, files)
        if (decision.effect === "ask") {
          // A native ask/reply cannot substitute for the distinct form prerequisite.
          await stops.evaluate(event, files)
          return
        }
        if (decision.effect === "deny") {
          event.effect = "deny"
          event.message = display(decision.message)
          return
        }
        // Remember ordinary allowed and asked edits; only the separate approval owner may clear a stop.
        const call = event.source?.id
        if (typeof call !== "string") return
        if (remembered.size >= MAX_REMEMBERED) remembered.delete(remembered.keys().next().value!)
        remembered.set(call, { session, files })
      }))

      await ctx.tool.hook("execute.after", guard("tool_execute_after", async (event) => {
        if (stops.after(event)) return
        const edit = typeof event?.id === "string" ? remembered.get(event.id) : undefined
        if (edit && edit.session === event.sessionID) {
          remembered.delete(event.id)
          if (event.status === "completed") gate!.edited(edit.session, edit.files)
          return
        }
        if (!SHELL_TOOLS.has(event?.tool) || typeof event?.input?.command !== "string") return
        if (!isCheck(event.input.command, settings!.checkPatterns)) return
        if (!checkFailed(event, settings!.checkPatterns)) return
        const session = await known(event.sessionID)
        if (session) gate!.failed(session)
      }))

      await ctx.session.hook("prompt", guard("prompt", async (event) => {
        const session = await known(event?.sessionID)
        if (!session || !gate!.isRoot(session) || !gate!.holds(session)) return
        const text = event?.prompt?.text
        if (typeof text !== "string") return
        const request = resetRequest(text)
        if (!request) return
        const cleared = gate!.reset(session, request)
        stops.reset(session, cleared)
        if (cleared.length) {
          event.prompt.text = `${text}\n\n[escalation gate: counter reset by user for ${display(cleared.join(", "))}]`
        }
      }))

      // The feature is read in the background; until it resolves as effective, every hook returns at once.
      void Promise.resolve().then(() => featureStatus(directory)).then(async (report) => {
        settings = activeSettings(report)
        if (!settings || events.signal.aborted) return
        if (eventChannelAvailable) gate = new Gate(settings)
        await ctx.tool.transform((editor: any) => {
          editor.add({
            name: "escalation_gate_status",
            description: "Show the stuck-pattern escalation gate: held files, repair rounds, stage, escalations "
              + "and the last internal error. Read-only.",
            input: { type: "object", properties: {}, additionalProperties: false },
            options: { codemode: false },
            execute: async (_input: unknown, context: any) => {
              const state = gate ? gate.status(String(context?.sessionID ?? "")) : { held: [], lastError: null }
              return { content: JSON.stringify({ ...state, lastError }) }
            },
          })
        })
      }).catch(() => { gate = null })

      return () => {
        eventChannelAvailable = false
        stops.close()
        gate = null
        events.abort()
        disposeReview?.()
      }
    },
  }
}

export default createWorkflowPlugin()
