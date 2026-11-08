// Smart Routing pre-transmission shim for OpenCode 2.2.6 (V2 server plugin). Contract: clients/opencode.md.
//
// Policy-free: it holds no selection or funding logic. For managed sessions it forwards bounded request facts, with
// the attempt's dispatch authority, to one persistent `scripts/routing.py --serve` child and blocks unless the answer
// is a verified `allow`. Interactive sessions (not attached by a managed launcher, directly or through a parent, and
// not a session id the ledger derived) are never checked and never blocked. Nothing is logged; thrown errors carry
// fixed reason codes only, never payload, header, environment, prompt or authority content.
import { spawn } from "node:child_process"
import type { ChildProcess } from "node:child_process"
import { createHash, timingSafeEqual } from "node:crypto"
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
const MAX_LINEAGE_CACHE = 4096  // parents of sessions seen; evicting, used only to answer lookups faster
const MAX_DESCENDANTS_PER_ROOT = 256  // each attached root's own budget of recorded managed descendants
const MAX_DESCENDANTS = 4096  // memory ceiling across roots; recorded descendants are never evicted
const MAX_OVER_LIMIT = 1024  // managed descendants created beyond a limit, recorded only to block them
const FAILED_LOOKUP_MS = 5000
const MAX_FAILED_LOOKUPS = 1024
const MAX_DENY = 16
const MAX_PENDING = 1024  // ledger-known sessions waiting for attach, and their recorded children; never evicted
const MAX_PROBES = 1024  // recent unmanaged or failed probe answers; evicting, used only to avoid asking again
const KINDS = new Set(["primary", "title", "compaction", "generate"])
const HARNESS: Record<string, string> = { "claude-agent": "claude-agent-sdk via opencode" }
const DENY_MESSAGE = "Blocked by Smart Routing: this managed session may not use this action."

// Mirrors of the patterns in runtime/adapters/opencode.py; tests/test_opencode_plugin.py checks parity.
const SESSION = /^ses_[A-Za-z0-9]{1,128}$/
const NAME = /^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$/
const ATTEMPT = /^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255}$/
const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,127}$/
const CREDENTIAL_ENV = new RegExp("(?:^|_)(?:API_?KEY|KEY|TOKEN|SECRETS?|PASSWORD|PASSWD|PASS|PASSPHRASE|CREDENTIALS?|AUTH"
  + "|COOKIE|PRIVATE|JWT|PAT|OAUTH|CREDS|ACCESSKEY|SECRETKEY|DSN|CONNECTION_STRING"
  + "|(?:DATABASE|DB|REDIS|MONGO|MONGODB|POSTGRES|POSTGRESQL|PG|MYSQL|AMQP|RABBITMQ)_UR[LI])(?:_|$)"
  + "|(?:PASSWORD|PASSWD)$|_PWD$", "i")
const SPAWN_MODEL = /^([A-Za-z0-9][A-Za-z0-9._-]{0,127})\/([A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}?)(?:#([A-Za-z0-9][A-Za-z0-9._-]{0,63}))?$/
const REASON = /^[a-z][a-z0-9_]{0,63}$/
const AUTHORITY = /^[A-Za-z0-9_-]{16,128}$/  // `dispatch` issues 43 URL-safe characters
// Exactly the shape of `runtime.routing.derive_ids`; OpenCode's own ids are shorter, so interactive ones never match.
const DERIVED_SESSION = /^ses_[0-9a-f]{32}$/
// Answers to a session-only probe that mean the ledger owns the session. Anything else (`unmanaged`, or a store or
// request error such as `ledger_absent`) leaves an unattached session interactive.
const LEDGER_KNOWS = new Set(["allow", "authority_required", "authority_invalid", "dispatch_required", "attempt_closed",
  "attempt_uncertain", "hold_state_unsupported", "session_mismatch", "unknown_attempt"])
// The command validates a request before it resolves the session; for a session-only probe these facts are never used.
const PROBE_REQUEST = { provider: "opencode", model: "session-probe", variant: null, harness: "opencode session probe",
  kind: "primary", funding_pool: "none" }

type Facts = { provider: string; model: string; variant: string | null; harness: string }
// `count` is the root's recorded descendants; `saturated` means its own tree exceeded its own budget.
// `authority` is the attempt's dispatch token: kept only here and sent only to the routing child.
type Attachment = { attempt: string; authority: string; pool: string; deny: Set<string>; count: number; saturated: boolean }
type Descendant = { root: string; overLimit: boolean }
type Config = { argv?: string[]; timeoutMs?: number; spawnGraceMs?: number; failedLookupMs?: number }

class RoutingBlocked extends Error {
  constructor(reason: string) {
    super(`routing: blocked (${REASON.test(reason) ? reason : "blocked"})`)
    this.name = "RoutingBlocked"
  }
}

class ChildFault extends Error {}

const text = (value: unknown, pattern: RegExp) => typeof value === "string" && pattern.test(value)

/** A reason code from the child, unless it is malformed or shares any 8 characters with the secret. */
function reasonFrom(value: unknown, secret: string, fallback: string): string {
  if (typeof value !== "string" || !REASON.test(value)) return fallback
  for (let index = 0; index + 8 <= Math.max(value.length, 8); index++) {
    if (secret.includes(value.slice(index, index + 8))) return fallback
  }
  return value
}

const digest = (value: string) => createHash("sha256").update(value).digest()
const sameToken = (left: string, right: string) => timingSafeEqual(digest(left), digest(right))

/** Whether a session id has the shape of a ledger-derived one, so the shim may ask the ledger about it. */
export function derivedSession(value: unknown): boolean {
  return text(value, DERIVED_SESSION)
}

/** The adapter's `credential_name` rule: camelCase is split before matching. */
export function credentialEnvName(name: string): boolean {
  return ENV_NAME.test(name) && credentialLikeName(name)
}

/** The shell rule for any name: split camelCase and punctuation, then match; for ENV_NAME names it is the adapter rule. */
export function credentialLikeName(name: string): boolean {
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
      if (value !== undefined && ENV_NAME.test(key) && !credentialLikeName(key)) env[key] = value
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
      // Instance-local state. Roots are the sessions a managed launcher attached here. Managed descendants are sessions
      // whose creation event named a parent that was already managed; they are never evicted. The lineage cache and
      // the failed-lookup cache only speed up questions about other sessions and may forget entries.
      // Pending sessions are derived ids the ledger knows that nobody attached here yet, and children recorded under
      // them; they are blocked until the launcher attaches. Probes are the ledger questions about derived ids.
      const attached = new Map<string, Attachment>()
      const descendants = new Map<string, Descendant>()
      const pending = new Map<string, string>()  // session -> the ledger-known session it was found under
      const lineage = new Map<string, string | null>()
      const failed = new Map<string, number>()
      const probes = new Map<string, number>()  // session -> until when an unmanaged or failed answer stands
      const probing = new Map<string, Promise<boolean>>()
      const bounded = <V>(map: Map<string, V>, limit: number, key: string, value: V) => {
        map.delete(key)
        if (map.size >= limit) map.delete(map.keys().next().value as string)
        map.set(key, value)
      }
      const failedLookupMs = config.failedLookupMs ?? FAILED_LOOKUP_MS
      let recorded = 0  // regular descendants across roots
      let overRecorded = 0

      /** The root a session is managed under when that is already known, without lookups. */
      const rootOf = (session: string): string | undefined => {
        if (attached.has(session)) return session
        const root = descendants.get(session)?.root
        return root !== undefined && attached.has(root) ? root : undefined
      }

      /** Record a managed descendant against its root's own budget; limits fail closed for that root only. */
      function recordDescendant(session: string, root: string) {
        const attachment = attached.get(root)
        if (descendants.has(session) || !attachment) return
        if (attachment.count < MAX_DESCENDANTS_PER_ROOT && recorded < MAX_DESCENDANTS) {
          descendants.set(session, { root, overLimit: false })
          attachment.count += 1
          recorded += 1
          return
        }
        // Over the root's own budget, its whole tree is blocked. At the instance ceiling only the new session is.
        if (attachment.count >= MAX_DESCENDANTS_PER_ROOT) attachment.saturated = true
        if (overRecorded < MAX_OVER_LIMIT) {
          descendants.set(session, { root, overLimit: true })
          overRecorded += 1
        } else {
          attachment.saturated = true  // cannot be recorded, so fail closed for this root
        }
      }

      /** Record a session found under a ledger-known one, unless it is (or meanwhile became) managed by an attachment. */
      function markPending(session: string, holder: string): Attachment | "pending" {
        const root = rootOf(session) ?? rootOf(holder)
        if (root !== undefined) {
          if (root !== session) recordDescendant(session, root)
          return attached.get(root)!
        }
        if (!pending.has(session) && pending.size < MAX_PENDING) pending.set(session, holder)
        return "pending"  // past the limit it is still blocked now, and asked again next time
      }

      /** Ask the ledger, read-only, whether it owns a derived session id. Shared while in flight; errors mean no. */
      function probe(session: string): Promise<boolean> {
        if (pending.has(session)) return Promise.resolve(true)
        if ((probes.get(session) ?? 0) > performance.now()) return Promise.resolve(false)
        let running = probing.get(session)
        if (running === undefined) {
          running = child.call("check", { session_id: session, request: PROBE_REQUEST }).then(({ response }) => {
            const answer = response.state === "blocked" ? response.reason : response.state
            return typeof answer === "string" && LEDGER_KNOWS.has(answer)
          }, () => false).then((known) => {
            probing.delete(session)
            if (known) markPending(session, session)  // a late answer still protects (strictly additive)
            else bounded(probes, MAX_PROBES, session, performance.now() + failedLookupMs)
            return known
          })
          probing.set(session, running)
        }
        return running
      }

      /** The probe's answer within one request budget; a hook never waits longer, and a timeout counts as no. */
      async function ledgerKnows(session: string): Promise<boolean> {
        try {
          return await within(probe(session), child.timeoutMs + child.graceMs)
        } catch {
          bounded(probes, MAX_PROBES, session, performance.now() + failedLookupMs)
          return false
        }
      }

      function forget(session: string) {
        const entry = descendants.get(session)
        if (!entry) return
        descendants.delete(session)
        if (entry.overLimit) overRecorded -= 1
        else {
          recorded -= 1
          const attachment = attached.get(entry.root)
          if (attachment) attachment.count -= 1
        }
      }

      // Lineage from the live event stream. Only a creation whose parent is already managed is evidence of a managed
      // session; any other creation, whatever its timing, is interactive unless its chain later reaches a root.
      const events = new AbortController()
      if (typeof ctx?.event?.subscribe === "function") {
        void (async () => {
          try {
            for await (const event of ctx.event.subscribe({ types: ["session.created"], signal: events.signal })) {
              const data = (event as any)?.data
              if ((event as any)?.type !== "session.created" || !text(data?.sessionID, SESSION)) continue
              if (data.parentID != null && !text(data.parentID, SESSION)) continue
              const parent: string | null = data.parentID ?? null
              bounded(lineage, MAX_LINEAGE_CACHE, data.sessionID, parent)
              const root = parent === null ? undefined : rootOf(parent)
              if (root !== undefined) recordDescendant(data.sessionID, root)
              else if (parent !== null && pending.has(parent)) markPending(data.sessionID, pending.get(parent)!)
              else if (derivedSession(data.sessionID) && rootOf(data.sessionID) === undefined) void probe(data.sessionID)
            }
          } catch { /* live-only stream: lookups cover what it misses */ }
        })()
      }

      /** The session's parent: a session ID, null for a root, or undefined when it cannot be determined. */
      async function lookup(session: string): Promise<string | null | undefined> {
        if ((failed.get(session) ?? 0) > performance.now()) return undefined
        if (typeof ctx?.session?.get === "function") {
          try {
            const answer: any = await within(Promise.resolve().then(() => ctx.session.get({ sessionID: session })), LOOKUP_TIMEOUT_MS)
            // Only an answer about this session counts; an error object or another session is a failed lookup.
            const info = answer?.id === session ? answer : answer?.data?.id === session ? answer.data : undefined
            if (info !== undefined) {
              if (info.parentID == null) return null
              if (text(info.parentID, SESSION)) return info.parentID
            }
          } catch { /* fall through: undetermined */ }
        }
        bounded(failed, MAX_FAILED_LOOKUPS, session, performance.now() + failedLookupMs)
        return undefined
      }

      /**
       * The attachment governing a session, "pending" for a ledger-known session nobody attached here, or null when
       * it is interactive. A session is managed when it is attached, a recorded managed descendant, or its parent
       * chain (from events and bounded lookups) reaches one of those. It is pending when it, or a session on that
       * chain, is a derived id the ledger knows. A chain that cannot be determined, a cycle, a root and a chain past
       * the limits are interactive. Only derived ids are ever asked about, so other sessions never wait for the child.
       */
      async function managed(sessionID: unknown): Promise<Attachment | "pending" | null> {
        if (!text(sessionID, SESSION)) return null
        const session = sessionID as string
        const known = rootOf(session)
        if (known !== undefined) return attached.get(known) ?? null
        if (pending.has(session)) return "pending"
        if (derivedSession(session) && await ledgerKnows(session)) return markPending(session, session)
        if (attached.size === 0 && pending.size === 0) return null
        const seen = new Set<string>()
        let current = sessionID as string
        let lookups = 0
        for (;;) {
          if (seen.has(current) || seen.size >= MAX_CHAIN) return null
          seen.add(current)
          let parent = lineage.get(current)
          if (parent === undefined) {
            if (lookups >= MAX_LOOKUPS) return null
            lookups += 1
            parent = await lookup(current)
            if (parent === undefined) return null
            bounded(lineage, MAX_LINEAGE_CACHE, current, parent)
          }
          if (parent === null) return null
          const root = rootOf(parent)
          if (root !== undefined) {
            recordDescendant(session, root)
            return attached.get(root) ?? null
          }
          const holder = pending.get(parent)
          if (holder !== undefined) return markPending(session, holder)
          if (derivedSession(parent)) {
            if (lookups >= MAX_LOOKUPS) return null
            lookups += 1
            if (await ledgerKnows(parent)) return markPending(session, parent)
          }
          current = parent
        }
      }

      async function check(session: string, attachment: Attachment, facts: Facts, kind: string) {
        if (attachment.saturated || descendants.get(session)?.overLimit) throw new RoutingBlocked("descendant_limit")
        // Strict check: the hook's own session id (a child's too) with its root's attempt and token.
        const { response } = await child.call("check", {
          attempt_id: attachment.attempt,
          session_id: session,
          authority: attachment.authority,
          request: { ...facts, kind, funding_pool: attachment.pool },
        })
        if (response.state === "allow") {
          if (response.authority === "verified" && response.attempt_id === attachment.attempt) return
          throw new RoutingBlocked("authority_unverified")
        }
        // `unmanaged` means the ledger does not know the session; for an attached session that contradicts the launcher.
        throw new RoutingBlocked(response.state === "unmanaged" ? "unexpected_unmanaged"
          : reasonFrom(response.reason, attachment.authority, "blocked"))
      }

      /** The attachment a checked hook enforces, or null for an interactive session; pending sessions are blocked. */
      async function enforced(sessionID: unknown): Promise<Attachment | null> {
        const governing = await managed(sessionID)
        if (governing === "pending") throw new RoutingBlocked("attach_required")
        return governing
      }

      await ctx.session.hook("model.request", async (event: any) => {
        const attachment = await enforced(event?.sessionID)
        if (!attachment) return
        if (!KINDS.has(event?.kind)) throw new RoutingBlocked("invalid_kind")
        const facts = routeFacts(event?.model)
        if (!facts) throw new RoutingBlocked("invalid_model")
        await check(event.sessionID, attachment, facts, event.kind)
      })

      // Only subagent spawns are checked; built-in, MCP and Code Mode tools are governed by policy deny rules.
      await ctx.tool.hook("execute.before", async (event: any) => {
        if (event?.tool !== "subagent") return
        const attachment = await enforced(event?.sessionID)
        if (!attachment) return
        const model = event?.input?.model
        if (model == null) throw new RoutingBlocked("subagent_model_required")
        const requested = spawnModel(model)
        if (!requested) throw new RoutingBlocked("invalid_subagent_model")
        if (attachment.count >= MAX_DESCENDANTS_PER_ROOT || recorded >= MAX_DESCENDANTS) {
          throw new RoutingBlocked("descendant_limit")
        }
        await check(event.sessionID, attachment, { ...requested, harness: harnessFor(requested.provider) }, "subagent")
      })

      // Tightens only; a configured deny is final and never reaches this hook.
      await ctx.permission.hook("evaluate", async (event: any) => {
        if (typeof event?.action !== "string") return
        if (attached.size === 0 && pending.size === 0 && !derivedSession(event.sessionID)) return
        let tighten: boolean
        try {
          const governing = await managed(event.sessionID)
          tighten = governing === "pending" ? event.action === "webfetch"
            : governing !== null && (event.action === "webfetch" || governing.deny.has(event.action))
        } catch {
          tighten = true
        }
        if (tighten && event.effect !== "deny") {
          event.effect = "deny"
          event.message = DENY_MESSAGE
        }
      })

      // The hook carries no session ID, so while any managed or pending session is known here every shell loses credentials.
      await ctx.shell.hook("create.before", (event: any) => {
        const env = event?.env
        if ((attached.size === 0 && pending.size === 0) || env === null || typeof env !== "object") return
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
        // Gated by the dispatch authority: attach records nothing until the child verified the token for this attempt
        // and session; detach needs the attached root's attempt and token. Neither ever returns or echoes the token.
        attach: async (input: unknown) => {
          const body = object(input, "invalid_attach")
          const deny = body.deny_actions ?? []
          const route = spawnModel(body.model)
          if (!text(body.session_id, SESSION) || !text(body.attempt_id, ATTEMPT) || !text(body.authority, AUTHORITY)
              || !text(body.funding_pool, NAME) || !route
              || !Array.isArray(deny) || deny.length > MAX_DENY || !deny.every((action) => text(action, NAME))) {
            throw new RoutingBlocked("invalid_attach")
          }
          const session = body.session_id as string
          const attempt = body.attempt_id as string
          const authority = body.authority as string
          const admissible = () => {
            const root = rootOf(session)
            if (root !== undefined && attached.get(root)!.attempt !== attempt) throw new RoutingBlocked("attach_conflict")
            if (!attached.has(session) && attached.size >= MAX_ATTACHED) throw new RoutingBlocked("attach_limit")
          }
          admissible()
          const { response } = await child.call("check", {
            attempt_id: attempt,
            session_id: session,
            authority,
            request: { ...route, harness: harnessFor(route.provider), kind: "primary", funding_pool: body.funding_pool },
          })
          if (response.state !== "allow") {
            throw new RoutingBlocked(response.state === "unmanaged" ? "attach_refused"
              : reasonFrom(response.reason, authority, "attach_refused"))
          }
          if (response.authority !== "verified" || response.attempt_id !== attempt) throw new RoutingBlocked("attach_refused")
          admissible()  // another attach may have landed while the child answered
          forget(session)  // a session attached as a root is no longer another root's descendant
          const previous = attached.get(session)
          attached.set(session, { attempt, authority, pool: body.funding_pool as string, deny: new Set(deny as string[]),
            count: previous?.count ?? 0, saturated: previous?.saturated ?? false })
          // Sessions found under it while it waited for this attach become its managed descendants.
          pending.delete(session)
          for (const [key, holder] of [...pending]) {
            if (holder === session) {
              pending.delete(key)
              recordDescendant(key, session)
            }
          }
          // A launcher attaches the sessions it created, which are roots; after detach its children resolve to it.
          if (!lineage.has(session)) bounded(lineage, MAX_LINEAGE_CACHE, session, null)
          return { state: "attached", session_id: session }
        },
        detach: async (input: unknown) => {
          const body = object(input, "invalid_detach")
          if (!text(body.session_id, SESSION) || !text(body.attempt_id, ATTEMPT) || !text(body.authority, AUTHORITY)) {
            throw new RoutingBlocked("invalid_detach")
          }
          const session = body.session_id as string
          const attachment = attached.get(session)
          // One reason for every refusal, so a caller learns nothing about which part was wrong.
          if (!attachment || attachment.attempt !== body.attempt_id || !sameToken(attachment.authority, body.authority as string)) {
            throw new RoutingBlocked("detach_refused")
          }
          for (const [key, entry] of descendants) if (entry.root === session) forget(key)
          attached.delete(session)  // with it go its count, saturation and token
          probes.delete(session)  // a derived id is asked about again, so a ledger-known one is pending, not interactive
          return { state: "detached", session_id: session }
        },
      })

      return () => {
        events.abort()
        child.stop()
        attached.clear()
        descendants.clear()
        pending.clear()
        probes.clear()
        probing.clear()
        recorded = 0
        overRecorded = 0
        lineage.clear()
        failed.clear()
      }
    },
  }
}

export default createRoutingPlugin()
