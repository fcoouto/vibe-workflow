// Smart Routing pre-transmission shim for OpenCode 2.2.6 (V2 server plugin). Contract: clients/opencode.md.
//
// Policy-free: it holds no selection or funding logic. For managed sessions it forwards bounded request facts to one
// persistent `scripts/routing.py --serve` child and blocks unless the answer is `allow`. Interactive sessions (not
// attached by a managed launcher, directly or through a parent) are never checked and never blocked. Nothing is
// logged; thrown errors carry fixed reason codes only, never payload, header, environment or prompt content.
import { spawn } from "node:child_process"
import type { ChildProcess } from "node:child_process"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

export const CONTRACT_VERSION = 1
const PROTOCOL = 1
const DEFAULT_TIMEOUT_MS = 500
const DEFAULT_SPAWN_GRACE_MS = 1000  // added to the first request after a spawn: interpreter and import start-up
const LOOKUP_TIMEOUT_MS = 300
const BACKOFF_MS = 1000  // after two failed respawns in a row, fail fast for this long before starting a child again
const MAX_REQUEST = 4096
const MAX_RESPONSE = 65536
const MAX_ATTACHED = 256  // attached roots; children found through them are not counted
const MAX_LOOKUPS = 8  // session.get calls per lineage question
const MAX_CHAIN = 64  // sessions walked per lineage question
const MAX_LINEAGE_CACHE = 4096
const MAX_DENY = 16
const KINDS = new Set(["primary", "title", "compaction", "generate"])
const HARNESS: Record<string, string> = { "claude-agent": "claude-agent-sdk via opencode" }
const DENY_MESSAGE = "Blocked by Smart Routing: this managed session may not use this action."

// Mirrors of the patterns in runtime/adapters/opencode.py; tests/test_opencode_plugin.py checks parity.
const SESSION = /^ses_[A-Za-z0-9]{1,128}$/
const NAME = /^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$/
const ATTEMPT = /^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255}$/
const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,127}$/
const CREDENTIAL_ENV = /(?:^|_)(?:API_?KEY|KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|AUTH|COOKIE|PRIVATE|DSN|CONNECTION_STRING|DATABASE_URL)(?:_|$)/i
const SPAWN_MODEL = /^([A-Za-z0-9][A-Za-z0-9._-]{0,127})\/([A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}?)(?:#([A-Za-z0-9][A-Za-z0-9._-]{0,63}))?$/
const REASON = /^[a-z][a-z0-9_]{0,63}$/

type Facts = { provider: string; model: string; variant: string | null; harness: string }
type Attachment = { attempt: string; pool: string; deny: Set<string> }
type Lineage = { parent: string | null; whileAttached: boolean }
type Config = { argv?: string[]; timeoutMs?: number; spawnGraceMs?: number }

class RoutingBlocked extends Error {
  constructor(reason: string) {
    super(`routing: blocked (${REASON.test(reason) ? reason : "blocked"})`)
    this.name = "RoutingBlocked"
  }
}

class ChildFault extends Error {}

const text = (value: unknown, pattern: RegExp) => typeof value === "string" && pattern.test(value)

export function credentialEnvName(name: string): boolean {
  return ENV_NAME.test(name) && CREDENTIAL_ENV.test(name)
}

/** Shell-only rule for names outside ENV_NAME: split camelCase and punctuation, then apply the same rule. */
export function credentialLikeName(name: string): boolean {
  if (ENV_NAME.test(name)) return CREDENTIAL_ENV.test(name)
  return CREDENTIAL_ENV.test(name.replace(/([a-z0-9])([A-Z])/g, "$1_$2").replace(/[^A-Za-z0-9]+/g, "_"))
}

export function harnessFor(provider: string): string {
  return HARNESS[provider] ?? `opencode provider ${provider}`
}

/** Requested route facts of a hook's `model`, or null when absent or malformed. Never the resolved model. */
export function routeFacts(model: unknown): Facts | null {
  if (model === null || typeof model !== "object" || Array.isArray(model)) return null
  const { providerID, id, variant } = model as Record<string, unknown>
  if (!text(providerID, NAME) || !text(id, NAME) || (variant != null && !text(variant, NAME))) return null
  const provider = providerID as string
  return { provider, model: id as string, variant: (variant as string | undefined) ?? null, harness: harnessFor(provider) }
}

/** A subagent's `provider/model#variant` override, or null when malformed. */
export function spawnModel(value: unknown): { provider: string; model: string; variant: string | null } | null {
  const match = typeof value === "string" ? SPAWN_MODEL.exec(value) : null
  return match ? { provider: match[1], model: match[2], variant: match[3] ?? null } : null
}

/** One persistent routing child speaking JSON lines; requests are multiplexed by id and each is bounded in time. */
class RoutingChild {
  argv: string[]
  timeoutMs: number
  graceMs: number
  proc: ChildProcess | null = null
  fresh = false
  spawned = 0
  respawnMs: number | null = null
  seq = 0
  failures = 0
  retryAt = 0
  buffer = ""
  pending = new Map<string, { resolve: (value: Record<string, unknown>) => void; reject: (error: Error) => void }>()

  constructor(argv: string[], timeoutMs: number, graceMs: number) {
    this.argv = argv
    this.timeoutMs = timeoutMs
    this.graceMs = graceMs
  }

  start() {
    const env: Record<string, string> = {}
    for (const [key, value] of Object.entries(process.env)) {
      if (value !== undefined && ENV_NAME.test(key) && !CREDENTIAL_ENV.test(key)) env[key] = value
    }
    const proc = spawn(this.argv[0], this.argv.slice(1), { stdio: ["pipe", "pipe", "ignore"], env })
    this.proc = proc
    this.fresh = true
    this.buffer = ""
    this.spawned += 1
    proc.on("error", () => this.fault(proc))
    proc.on("exit", () => this.fault(proc))
    proc.stdin?.on("error", () => this.fault(proc))
    proc.stdout?.setEncoding("utf8")
    proc.stdout?.on("data", (chunk: string) => this.read(proc, chunk))
    return proc
  }

  read(proc: ChildProcess, chunk: string) {
    if (proc !== this.proc) return
    this.buffer += chunk
    let index
    while ((index = this.buffer.indexOf("\n")) >= 0) {
      const line = this.buffer.slice(0, index)
      this.buffer = this.buffer.slice(index + 1)
      let response: unknown
      try { response = JSON.parse(line) } catch { return this.fault(proc) }
      const id = (response as Record<string, unknown> | null)?.id
      const waiter = typeof id === "string" ? this.pending.get(id) : undefined
      if (!waiter || typeof response !== "object" || Array.isArray(response)) return this.fault(proc)
      this.fresh = false
      this.pending.delete(id as string)
      waiter.resolve(response as Record<string, unknown>)
    }
    if (this.buffer.length > MAX_RESPONSE) this.fault(proc)
  }

  /** A stall, exit, unreadable line or unknown id: kill this child and fail every request waiting on it. */
  fault(proc: ChildProcess) {
    if (proc !== this.proc) return
    this.proc = null
    try { proc.kill("SIGKILL") } catch { /* already gone */ }
    const waiting = [...this.pending.values()]
    this.pending.clear()
    for (const waiter of waiting) waiter.reject(new ChildFault())
  }

  send(op: string, fields: Record<string, unknown>): Promise<Record<string, unknown>> {
    const id = `r${++this.seq}`
    const line = JSON.stringify({ v: PROTOCOL, op, id, ...fields })
    if (line.length > MAX_REQUEST) return Promise.reject(new RoutingBlocked("request_too_large"))
    const proc = this.proc ?? this.start()
    const limit = this.timeoutMs + (this.fresh ? this.graceMs : 0)
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => this.fault(proc), limit)
      this.pending.set(id, {
        resolve: (value) => { clearTimeout(timer); resolve(value) },
        reject: (error) => { clearTimeout(timer); reject(error) },
      })
      try { proc.stdin?.write(line + "\n") } catch { this.fault(proc) }
    })
  }

  /** Send once; on a child fault respawn once and retry, then fail closed. Never waits beyond two bounded tries. */
  async call(op: string, fields: Record<string, unknown>) {
    if (this.failures >= 2 && performance.now() < this.retryAt) throw new RoutingBlocked("routing_unavailable")
    let respawned = false
    for (let attempt = 0; ; attempt++) {
      const replacing = this.proc === null && this.spawned > 0
      const began = performance.now()
      try {
        const response = await this.send(op, fields)
        this.failures = 0
        if (replacing) {
          respawned = true
          this.respawnMs = Math.round((performance.now() - began) * 100) / 100
        }
        if (response.v !== PROTOCOL) throw new RoutingBlocked("protocol_version")
        if (typeof response.state !== "string") throw new RoutingBlocked("protocol_invalid")
        return { response, respawned }
      } catch (error) {
        if (!(error instanceof ChildFault)) throw error
        if (attempt >= 1) {
          this.failures += 1
          if (this.failures >= 2) this.retryAt = performance.now() + BACKOFF_MS
          throw new RoutingBlocked("routing_unavailable")
        }
      }
    }
  }

  stop() {
    if (this.proc) this.fault(this.proc)
  }
}

function defaultArgv(options: Record<string, unknown>): string[] {
  const script = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "scripts", "routing.py")
  const python = typeof options.python === "string" && options.python ? options.python : "python3"
  const argv = [python, "-I", "-B", script, "--serve"]
  for (const pin of ["settings", "ledger"]) {
    const value = options[pin]
    if (typeof value === "string" && value && !value.startsWith("-")) argv.push(`--${pin}`, value)
  }
  return argv
}

const within = <T>(promise: Promise<T>, ms: number) => new Promise<T>((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error("timeout")), ms)
  promise.then((value) => { clearTimeout(timer); resolve(value) }, (error) => { clearTimeout(timer); reject(error) })
})

const object = (value: unknown, reason: string) => {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new RoutingBlocked(reason)
  return value as Record<string, unknown>
}

const METHOD = { input: { type: "object" }, output: { type: "object" } }
const RPC = { id: "routing", methods: { health: METHOD, attach: METHOD, detach: METHOD }, events: {} }

export function createRoutingPlugin(config: Config = {}) {
  return {
    id: "vibe-workflow.routing",
    async setup(ctx: any) {
      const options = (ctx?.options ?? {}) as Record<string, unknown>
      const configured = Number(options.timeout_ms)
      const child = new RoutingChild(config.argv ?? defaultArgv(options),
        config.timeoutMs ?? (configured > 0 && configured <= 5000 ? configured : DEFAULT_TIMEOUT_MS),
        config.spawnGraceMs ?? DEFAULT_SPAWN_GRACE_MS)
      // Instance-local: sessions a managed launcher attached here (roots), and the parent of each session seen.
      const attached = new Map<string, Attachment>()
      const lineage = new Map<string, Lineage>()
      const remember = (session: string, entry: Lineage) => {
        lineage.delete(session)
        if (lineage.size >= MAX_LINEAGE_CACHE) lineage.delete(lineage.keys().next().value as string)
        lineage.set(session, entry)
      }

      // Lineage from the live event stream, so session.get is needed only for sessions created before this instance
      // subscribed or whose event was missed. Sessions created while any session is attached are flagged.
      const events = new AbortController()
      if (typeof ctx?.event?.subscribe === "function") {
        void (async () => {
          try {
            for await (const event of ctx.event.subscribe({ types: ["session.created"], signal: events.signal })) {
              const data = (event as any)?.data
              if ((event as any)?.type !== "session.created" || !text(data?.sessionID, SESSION)) continue
              if (data.parentID != null && !text(data.parentID, SESSION)) continue
              remember(data.sessionID, { parent: data.parentID ?? null, whileAttached: attached.size > 0 })
            }
          } catch { /* live-only stream: lookups cover what it misses */ }
        })()
      }

      /** The session's parent: a session ID, null for a root, or undefined when it cannot be determined. */
      async function lookup(session: string): Promise<string | null | undefined> {
        if (typeof ctx?.session?.get !== "function") return undefined
        try {
          const info: any = await within(Promise.resolve().then(() => ctx.session.get({ sessionID: session })), LOOKUP_TIMEOUT_MS)
          const parent = info?.parentID ?? info?.data?.parentID
          if (parent == null) return null
          return text(parent, SESSION) ? parent : undefined
        } catch {
          return undefined
        }
      }

      /**
       * The attachment governing a session, or null when it is interactive. A session is managed only when its parent
       * chain reaches an attached session. When the chain cannot be determined it blocks only on positive evidence: a
       * session on the walked chain was created while a session was attached. Invalid IDs, roots, cycles and chains
       * longer than the limits are interactive.
       */
      async function managed(sessionID: unknown): Promise<Attachment | null> {
        if (attached.size === 0 || !text(sessionID, SESSION)) return null
        const seen = new Set<string>()
        let current = sessionID as string
        let lookups = 0
        let evidence = false
        for (;;) {
          const root = attached.get(current)
          if (root) return root
          if (seen.has(current) || seen.size >= MAX_CHAIN) return null
          seen.add(current)
          let entry = lineage.get(current)
          if (!entry) {
            if (lookups >= MAX_LOOKUPS) return null
            lookups += 1
            const parent = await lookup(current)
            if (parent === undefined) {
              if (evidence) throw new RoutingBlocked("lineage_unavailable")
              return null
            }
            entry = { parent, whileAttached: false }
            remember(current, entry)
          }
          if (entry.whileAttached) evidence = true
          if (entry.parent === null) return null
          current = entry.parent
        }
      }

      async function check(attachment: Attachment, facts: Facts, kind: string) {
        const { response } = await child.call("check", {
          attempt_id: attachment.attempt,
          request: { ...facts, kind, funding_pool: attachment.pool },
        })
        if (response.state === "allow") return
        // `unmanaged` means the ledger does not know the session; for an attached session that contradicts the launcher.
        throw new RoutingBlocked(response.state === "unmanaged" ? "unexpected_unmanaged"
          : typeof response.reason === "string" ? response.reason : String(response.state))
      }

      await ctx.session.hook("model.request", async (event: any) => {
        const attachment = await managed(event?.sessionID)
        if (!attachment) return
        if (!KINDS.has(event?.kind)) throw new RoutingBlocked("invalid_kind")
        const facts = routeFacts(event?.model)
        if (!facts) throw new RoutingBlocked("invalid_model")
        await check(attachment, facts, event.kind)
      })

      // Only subagent spawns are checked; built-in, MCP and Code Mode tools are governed by policy deny rules.
      await ctx.tool.hook("execute.before", async (event: any) => {
        if (event?.tool !== "subagent") return
        const attachment = await managed(event?.sessionID)
        if (!attachment) return
        const model = event?.input?.model
        if (model == null) throw new RoutingBlocked("subagent_model_required")
        const requested = spawnModel(model)
        if (!requested) throw new RoutingBlocked("invalid_subagent_model")
        await check(attachment, { ...requested, harness: harnessFor(requested.provider) }, "subagent")
      })

      // Tightens only; a configured deny is final and never reaches this hook.
      await ctx.permission.hook("evaluate", async (event: any) => {
        if (attached.size === 0 || typeof event?.action !== "string") return
        let tighten: boolean
        try {
          const attachment = await managed(event.sessionID)
          tighten = attachment !== null && (event.action === "webfetch" || attachment.deny.has(event.action))
        } catch {
          tighten = true
        }
        if (tighten && event.effect !== "deny") {
          event.effect = "deny"
          event.message = DENY_MESSAGE
        }
      })

      // The hook carries no session ID, so while any managed session is attached here every shell loses credentials.
      await ctx.shell.hook("create.before", (event: any) => {
        const env = event?.env
        if (attached.size === 0 || env === null || typeof env !== "object") return
        for (const key of Object.keys(env)) if (credentialLikeName(key)) delete env[key]
      })

      let definition: unknown = RPC
      try {
        const module: any = await import("@opencode/plugin/rpc")
        if (typeof module?.Rpc?.define === "function") definition = module.Rpc.define(RPC)
      } catch { /* outside OpenCode the plain definition is used */ }
      await ctx.rpc.register(definition, {
        health: async () => {
          const { response, respawned } = await child.call("status", {})
          if (response.state !== "ok") throw new RoutingBlocked("routing_unhealthy")
          // Ready means a check can be answered: readable settings and an existing ledger (prepare creates it).
          const ledger = (response.ledger as Record<string, unknown> | null)?.state
          const reason = response.settings_state !== "ok" ? "settings_unavailable"
            : ledger === "ready" ? null : ledger === "absent" ? "ledger_absent" : "ledger_unavailable"
          if (reason) return { state: "not_ready", reason, version: CONTRACT_VERSION }
          return { state: "ok", child: respawned ? "respawned" : "up", version: CONTRACT_VERSION, respawn_ms: child.respawnMs }
        },
        attach: async (input: unknown) => {
          const body = object(input, "invalid_attach")
          const deny = body.deny_actions ?? []
          if (!text(body.session_id, SESSION) || !text(body.attempt_id, ATTEMPT) || !text(body.funding_pool, NAME)
              || !Array.isArray(deny) || deny.length > MAX_DENY || !deny.every((action) => text(action, NAME))) {
            throw new RoutingBlocked("invalid_attach")
          }
          const session = body.session_id as string
          if (!attached.has(session) && attached.size >= MAX_ATTACHED) throw new RoutingBlocked("attach_limit")
          attached.set(session, { attempt: body.attempt_id as string, pool: body.funding_pool as string,
            deny: new Set(deny as string[]) })
          // A launcher attaches the sessions it created, which are roots; after detach its children resolve to it.
          if (!lineage.has(session)) remember(session, { parent: null, whileAttached: false })
          return { state: "attached", session_id: session }
        },
        detach: async (input: unknown) => {
          const body = object(input, "invalid_detach")
          if (!text(body.session_id, SESSION)) throw new RoutingBlocked("invalid_detach")
          const session = body.session_id as string
          const was = attached.has(session)
          attached.delete(session)  // children found through it become interactive again
          return { state: "detached", session_id: session, was_attached: was }
        },
      })

      return () => {
        events.abort()
        child.stop()
        attached.clear()
        lineage.clear()
      }
    },
  }
}

export default createRoutingPlugin()
