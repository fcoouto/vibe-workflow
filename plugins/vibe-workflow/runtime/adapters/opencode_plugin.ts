// Smart Routing pre-transmission shim for OpenCode 2.2.6 (V2 server plugin). Contract: clients/opencode.md.
//
// Policy-free: it holds no selection or funding logic. For managed sessions it forwards bounded request facts, with
// the attempt's dispatch authority, to one persistent `scripts/routing.py --serve` child and blocks unless the answer
// is a verified `allow`. Interactive sessions (not attached by a managed launcher, directly or through a parent, and
// not a session id the ledger derived) are never checked and never blocked. Nothing is logged; thrown errors carry
// fixed reason codes only, never payload, header, environment, prompt or authority content.
import { spawn } from "node:child_process"
import type { ChildProcess } from "node:child_process"
import { createHash, randomBytes, timingSafeEqual } from "node:crypto"
import { homedir } from "node:os"
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
const MAX_PENDING = 1024  // ledger-known sessions waiting for attach, and their recorded children
const PENDING_MS = 5 * 60 * 1000  // a pending entry is dropped after this and the ledger is asked again
const MAX_CLOSED = 1024  // sessions of finished attempts; evicting, an evicted one is asked again
const MAX_PROBES = 1024  // recent unmanaged or failed answers; evicting, used only to avoid asking again
const MAX_PROBING = 8  // ledger questions in flight, for creation events and for hooks each; beyond it, no answer for now
const MAX_RETIRED = 4096  // managed descendants of detached roots; evicting, an evicted one is asked about no more
const MAX_CREATED = 4096  // derived-shape sessions whose creation event was seen; evicting (see `routing.descendants`)
const MAX_DESCENDANTS_RESPONSE = 512  // entries in one `routing.descendants` answer; beyond it the answer says truncated
const KINDS = new Set(["primary", "title", "compaction", "generate"])
const HARNESS: Record<string, string> = { "claude-agent": "claude-agent-sdk via opencode" }
const DENY_MESSAGE = "Blocked by Smart Routing: this managed session may not use this action."
// S3.3d, mirrored from runtime/adapters/opencode.py (see the comments there; tests/test_opencode_plugin.py checks parity).
const IMAGE_ACTION = "experimental_image_generate"
const EDIT_ACTIONS = new Set(["edit", "write", "patch"])
const CONFIG_NAMES = new Set([".opencode", ".claude", ".agents", "opencode.json", "opencode.jsonc"])
const CONFIG_DIRS = new Set(["agent", "agents", "command", "commands", "mode", "modes"])
const SYSTEM_CONFIG = ["/etc/opencode", "/Library/Application Support/opencode"]
const MANAGED_DENIES = ["config_edit", "image_generation"]  // what `routing.health` reports the shim applies
const MAX_PATH = 4096
const MAX_RESOURCES = 256
const MAX_EXTRA = 32
const MAX_EXTRA_SEGMENTS = 16
const MAX_EXTRA_TEXT = 256
const PATH_CONTROL = /[\x00-\x1f\x7f]/
const DRIVE = /^[a-z]:/

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
// Exactly the shape of `runtime.routing.derive_ids`. Assumed (not on record for 2.2.6) that OpenCode's own ids never
// match it; P0.7 re-qualification checks this.
const DERIVED_SESSION = /^ses_[0-9a-f]{32}$/
// Answers to a session-only question that mean an open attempt of the ledger owns the session: the only ones the
// command can give for a session it resolves without an attempt or token. `attempt_closed` is handled on its own.
// Anything else (`unmanaged`, or a store or request error such as `ledger_absent`) leaves the session interactive.
const LEDGER_KNOWS = new Set(["authority_required", "dispatch_required", "attempt_uncertain", "hold_state_unsupported"])
// The command validates a request before it resolves the session; for a session-only probe these facts are never used.
const PROBE_REQUEST = { provider: "opencode", model: "session-probe", variant: null, harness: "opencode session probe",
  kind: "primary", funding_pool: "none" }

type Facts = { provider: string; model: string; variant: string | null; harness: string }
// `count` is the root's recorded descendants; `saturated` means its own tree exceeded its own budget.
// `authority` is the attempt's dispatch token: kept only here and sent only to the routing child.
// `watched`: the root's own creation event arrived while it was attached, so every descendant was created while the
// shim was listening. `lookupFailed`: a lineage lookup failed while it was attached. Both feed `routing.descendants`.
type Attachment = { attempt: string; authority: string; pool: string; deny: Set<string>; count: number; saturated: boolean;
  watched: boolean; lookupFailed: boolean }
// `parent` is the direct parent the shim knew when it recorded the session, or null when it did not know it.
type Descendant = { root: string; overLimit: boolean; parent: string | null }
// `onRefusal` is a code-only hook (never a plugin option) that sees attach's internal refusal reason; the RPC caller
// only ever sees `attach_refused`.
type Config = { argv?: string[]; timeoutMs?: number; spawnGraceMs?: number; failedLookupMs?: number; pendingMs?: number;
  maxDescendantsResponse?: number; onRefusal?: (reason: string) => void
  environment?: { env: Record<string, string | undefined>; home: string | null } }  // code-only: config roots' source
type Answer = "known" | "closed" | "no"

class RoutingBlocked extends Error {
  reason: string
  constructor(reason: string) {
    const fixed = REASON.test(reason) ? reason : "blocked"
    super(`routing: blocked (${fixed})`)
    this.name = "RoutingBlocked"
    this.reason = fixed
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

type ParsedPath = { absolute: boolean; raw: string[]; resolved: string[] }

/** Python's `rstrip(". ")`, linear in the segment length (a `[. ]+$` regex backtracks on long runs of dots). */
function trimTrailing(segment: string): string {
  let end = segment.length
  while (end > 0 && (segment[end - 1] === "." || segment[end - 1] === " ")) end--
  return segment.slice(0, end)
}

/** The adapter's `_path`: NFKC, lower case, `\` as `/`, names before `:` with trailing dots and spaces ignored. */
function parsePath(value: unknown, limit = MAX_PATH): ParsedPath | null {
  if (typeof value !== "string" || !value || value.length > 2 * limit || [...value].length > limit) return null
  const text = value.normalize("NFKC").toLowerCase().replaceAll("\\", "/")
  if (PATH_CONTROL.test(text)) return null
  const absolute = text.startsWith("/") || text.startsWith("~") || DRIVE.test(text)
  const raw: string[] = []
  for (const segment of text.split("/")) {
    if (segment === "" || segment === ".") continue
    if (segment === "..") { raw.push(segment); continue }
    if (!trimTrailing(segment)) return null
    raw.push(trimTrailing(segment.split(":")[0]) || trimTrailing(segment))
  }
  return { absolute, raw, resolved: resolveSegments(raw, absolute) }
}

function resolveSegments(raw: string[], absolute: boolean): string[] {
  const resolved: string[] = []
  for (const segment of raw) {
    if (segment !== "..") resolved.push(segment)
    else if (resolved.length && resolved[resolved.length - 1] !== "..") resolved.pop()
    else if (!absolute) resolved.push(segment)
  }
  return resolved
}

/** The adapter's `config_roots`: system, `<home>/.config/opencode`, `$XDG_CONFIG_HOME/opencode`, `$OPENCODE_CONFIG_DIR`. */
export function configRoots(env: Record<string, unknown>, home: unknown): string[][] {
  const programData = typeof env?.ProgramData === "string" ? env.ProgramData : "C:\\ProgramData"
  const candidates: unknown[] = [...SYSTEM_CONFIG, programData + "/opencode"]
  if (typeof home === "string" && home) candidates.push(home + "/.config/opencode")
  if (typeof env?.XDG_CONFIG_HOME === "string" && env.XDG_CONFIG_HOME) candidates.push(env.XDG_CONFIG_HOME + "/opencode")
  candidates.push(env?.OPENCODE_CONFIG_DIR)
  const roots: string[][] = []
  for (const candidate of candidates) {
    const parsed = parsePath(candidate)
    if (parsed?.absolute && parsed.resolved.length && !roots.some((root) => root.join("/") === parsed.resolved.join("/"))) {
      roots.push(parsed.resolved)
    }
  }
  return roots
}

const DEFAULT_ROOTS = configRoots({}, null)

function configHit(segments: string[], absolute: boolean, extra: string[][], roots: string[][]): boolean {
  if (segments.some((segment) => CONFIG_NAMES.has(segment))) return true
  if (segments.some((segment, index) => segment === ".config" && segments[index + 1] === "opencode")) return true
  if (absolute) {
    if (roots.some((root) => root.length <= segments.length && root.every((part, index) => segments[index] === part))) return true
  } else {
    const first = segments.find((segment) => segment !== "..")
    if (first !== undefined && CONFIG_DIRS.has(first)) return true
  }
  const joined = "/" + segments.join("/") + "/"
  return extra.some((pattern) => joined.includes("/" + pattern.join("/") + "/"))
}

/** The `protected_paths` plugin option as segment lists: [] when absent, null when invalid (at most 32 x 16 segments). */
export function extraPaths(value: unknown): string[][] | null {
  if (value == null) return []
  if (!Array.isArray(value) || value.length > MAX_EXTRA) return null
  const paths: string[][] = []
  for (const item of value) {
    const parsed = parsePath(item, MAX_EXTRA_TEXT)
    if (!parsed || !parsed.raw.length || parsed.raw.includes("..") || parsed.raw.length > MAX_EXTRA_SEGMENTS) return null
    paths.push(parsed.raw)
  }
  return paths
}

/**
 * Whether an edit resource may change OpenCode configuration; an unreadable one counts as protected. Relative
 * resources resolve against `directory` (the instance's `ctx.location.directory`); when it is unknown the fail-closed
 * relative rule applies.
 */
export function protectedPath(resource: unknown, extra: string[][] | null = [], directory: unknown = null,
  roots: string[][] = DEFAULT_ROOTS): boolean {
  const parsed = parsePath(resource)
  if (!parsed || !extra) return true
  let { absolute, raw, resolved } = parsed
  const base = parsePath(directory)
  if (!absolute && base?.absolute) {
    absolute = true
    raw = [...base.resolved, ...raw]
    resolved = resolveSegments(raw, true)
  }
  return configHit(raw, absolute, extra, roots) || configHit(resolved, absolute, extra, roots)
}

/** The S3.3d denial for a managed session's permission request: "image_generation", "config_edit" or null. */
export function managedDenial(action: unknown, resources: unknown, extra: string[][] | null = [], directory: unknown = null,
  roots: string[][] = DEFAULT_ROOTS): string | null {
  if (action === IMAGE_ACTION) return "image_generation"
  if (typeof action !== "string" || !EDIT_ACTIONS.has(action)) return null
  if (!Array.isArray(resources) || !resources.length || resources.length > MAX_RESOURCES) return "config_edit"
  return resources.some((resource) => protectedPath(resource, extra, directory, roots)) ? "config_edit" : null
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
  stopped = false  // after shutdown nothing starts a child again
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
    if (this.stopped) return Promise.reject(new RoutingBlocked("routing_stopped"))
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
    if (this.stopped) throw new RoutingBlocked("routing_stopped")
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
        if (this.stopped) throw new RoutingBlocked("routing_stopped")
        if (attempt >= 1) {
          this.failures += 1
          if (this.failures >= 2) this.retryAt = performance.now() + BACKOFF_MS
          throw new RoutingBlocked("routing_unavailable")
        }
      }
    }
  }

  stop() {
    this.stopped = true
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
const RPC = { id: "routing", methods: { health: METHOD, attach: METHOD, detach: METHOD, descendants: METHOD }, events: {} }

export function createRoutingPlugin(config: Config = {}) {
  return {
    id: "vibe-workflow.routing",
    async setup(ctx: any) {
      const options = (ctx?.options ?? {}) as Record<string, unknown>
      const configured = Number(options.timeout_ms)
      const child = new RoutingChild(config.argv ?? defaultArgv(options),
        config.timeoutMs ?? (configured > 0 && configured <= 5000 ? configured : DEFAULT_TIMEOUT_MS),
        config.spawnGraceMs ?? DEFAULT_SPAWN_GRACE_MS)
      // Extra protected paths only add to the built-in set; an invalid option denies every managed edit.
      const extra = extraPaths(options.protected_paths)
      // Where relative edit resources resolve (the same location FileAccess uses), and the global and system config
      // directories; an unknown directory keeps the fail-closed relative rule.
      const directory = ctx?.location?.directory
      const environment = config.environment ?? { env: process.env, home: homedir() }
      const roots = configRoots(environment.env, environment.home)
      // Instance-local state. Roots are the sessions a managed launcher attached here. Managed descendants are sessions
      // whose creation event named a parent that was already managed; they are never evicted. The lineage cache and
      // the failed-lookup cache only speed up questions about other sessions and may forget entries.
      // Pending sessions are derived ids an open attempt of the ledger owns that nobody attached here yet, and children
      // recorded under them; they are blocked until the launcher attaches, and expire to be asked again. Closed
      // sessions belong to finished attempts and block themselves and the sessions recorded under them. Neither turns on shell stripping or lookups:
      // only attached roots do. Probes are the ledger questions about derived ids.
      const attached = new Map<string, Attachment>()
      const descendants = new Map<string, Descendant>()
      const pending = new Map<string, { holder: string; until: number }>()  // holder: the ledger-known session above it
      const closed = new Map<string, true>()
      const lineage = new Map<string, string | null>()
      const failed = new Map<string, number>()
      const probes = new Map<string, number>()  // session -> until when an unmanaged or failed answer stands
      const probing = new Map<string, Promise<Answer>>()
      const asking = { event: 0, hook: 0 }  // questions in flight by who started them
      // Managed descendants of detached roots -> that root (and their parent), so a finished attempt's sessions stay
      // blocked after detach.
      const retired = new Map<string, { root: string; parent: string | null }>()
      const unattached = randomBytes(32).toString("base64url")  // compared against when there is no attachment
      /** The attachment of an attached root with this attempt and token, or undefined. The token is always compared. */
      const authorized = (session: string, attempt: string, authority: string): Attachment | undefined => {
        const attachment = attached.get(session)
        const matches = sameToken(attachment?.authority ?? unattached, authority)
        return attachment && attachment.attempt === attempt && matches ? attachment : undefined
      }
      const bounded = <V>(map: Map<string, V>, limit: number, key: string, value: V) => {
        map.delete(key)
        if (map.size >= limit) map.delete(map.keys().next().value as string)
        map.set(key, value)
      }
      const failedLookupMs = config.failedLookupMs ?? FAILED_LOOKUP_MS
      const pendingMs = config.pendingMs ?? PENDING_MS
      let recorded = 0  // regular descendants across roots
      let overRecorded = 0
      let streaming = false  // the creation-event stream is subscribed and has not ended or failed
      // Derived-shape sessions (the only attachable ones) whose creation event was already seen, so a repeated event
      // never vouches for a root again after a detach and re-attach.
      const createdSeen = new Map<string, true>()

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
        const parent = lineage.get(session) ?? null
        if (attachment.count < MAX_DESCENDANTS_PER_ROOT && recorded < MAX_DESCENDANTS) {
          descendants.set(session, { root, overLimit: false, parent })
          attachment.count += 1
          recorded += 1
          return
        }
        // Over the root's own budget, its whole tree is blocked. At the instance ceiling only the new session is.
        if (attachment.count >= MAX_DESCENDANTS_PER_ROOT) attachment.saturated = true
        if (overRecorded < MAX_OVER_LIMIT) {
          descendants.set(session, { root, overLimit: true, parent })
          overRecorded += 1
        } else {
          attachment.saturated = true  // cannot be recorded, so fail closed for this root
        }
      }

      /** The ledger-known session a pending session was found under, or undefined; expired entries are dropped. */
      function holderOf(session: string): string | undefined {
        const entry = pending.get(session)
        if (entry === undefined) return undefined
        if (entry.until > performance.now()) return entry.holder
        pending.delete(session)
        return undefined
      }

      /** Record a session found under a ledger-known one, unless it is (or meanwhile became) managed by an attachment. */
      function markPending(session: string, holder: string): Attachment | "pending" {
        const root = rootOf(session) ?? rootOf(holder)
        if (root !== undefined) {
          if (root !== session) recordDescendant(session, root)
          return attached.get(root)!
        }
        if (!pending.has(session) && pending.size >= MAX_PENDING) {
          for (const key of [...pending.keys()]) holderOf(key)  // make room from expired entries
        }
        if (pending.has(session) || pending.size < MAX_PENDING) {
          pending.set(session, { holder, until: performance.now() + pendingMs })
        }
        return "pending"  // past the limit it is still blocked now, and asked again next time
      }

      const negative = (session: string) => bounded(probes, MAX_PROBES, session, performance.now() + failedLookupMs)

      /**
       * Ask the ledger, read-only, about a derived session id: "known" (an open attempt owns it), "closed" (a finished
       * attempt did) or "no". Shared while in flight and at most MAX_PROBING at once; errors and overflow mean no.
       */
      function probe(session: string, origin: "event" | "hook"): Promise<Answer> {
        if (holderOf(session) !== undefined) return Promise.resolve("known")
        if (closed.has(session)) return Promise.resolve("closed")
        if ((probes.get(session) ?? 0) > performance.now()) return Promise.resolve("no")
        let running = probing.get(session)
        if (running === undefined) {
          // Creation events and hooks each have their own limit, so a burst of creations never uses up a hook's
          // question. Past a limit there is no answer for now, and it is not remembered: a hook asks again.
          if (asking[origin] >= MAX_PROBING) return Promise.resolve("no")
          asking[origin] += 1
          running = child.call("check", { session_id: session, request: PROBE_REQUEST }).then(({ response }): Answer => {
            const answer = response.state === "blocked" ? response.reason : response.state
            return answer === "attempt_closed" ? "closed" : typeof answer === "string" && LEDGER_KNOWS.has(answer) ? "known" : "no"
          }, (): Answer => "no").then((answer) => {
            probing.delete(session)
            asking[origin] -= 1
            // A late answer still counts: it only adds protection.
            if (answer === "known") markPending(session, session)
            else if (answer === "closed") bounded(closed, MAX_CLOSED, session, true)
            else negative(session)
            return answer
          })
          probing.set(session, running)
        }
        return running
      }

      /** The probe's answer within one request budget; a hook never waits longer, and a timeout counts as no. */
      async function ledgerAnswer(session: string): Promise<Answer> {
        try {
          return await within(probe(session, "hook"), child.timeoutMs + child.graceMs)
        } catch {
          negative(session)
          return "no"
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
        streaming = true
        void (async () => {
          try {
            for await (const event of ctx.event.subscribe({ types: ["session.created"], signal: events.signal })) {
              const data = (event as any)?.data
              if ((event as any)?.type !== "session.created" || !text(data?.sessionID, SESSION)) continue
              if (data.parentID != null && !text(data.parentID, SESSION)) continue
              const parent: string | null = data.parentID ?? null
              bounded(lineage, MAX_LINEAGE_CACHE, data.sessionID, parent)
              if (derivedSession(data.sessionID) && !createdSeen.has(data.sessionID)) {
                bounded(createdSeen, MAX_CREATED, data.sessionID, true)
                const own = attached.get(data.sessionID)
                if (own) own.watched = true
              }
              // A parent recorded under a root that was detached and attached again counts under that root.
              const former = parent === null ? undefined : retired.get(parent)?.root
              const root = parent === null ? undefined : rootOf(parent) ?? (former !== undefined ? rootOf(former) : undefined)
              if (root !== undefined) recordDescendant(data.sessionID, root)
              else if (parent !== null && holderOf(parent) !== undefined) markPending(data.sessionID, holderOf(parent)!)
              else if (derivedSession(data.sessionID) && rootOf(data.sessionID) === undefined) void probe(data.sessionID, "event")
            }
          } catch { /* live-only stream: lookups cover what it misses */ }
          streaming = false  // ended or failed: no descendant set can be vouched for from now on
        })()
      }

      /**
       * An undetermined chain may belong to any attached root's tree, so none of them can vouch for its descendants.
       * Only spend paths (`model.request` and subagent spawns) mark them; other hooks only answer interactive.
       */
      const undetermined = (spend: boolean): null => {
        if (spend) for (const attachment of attached.values()) attachment.lookupFailed = true
        return null
      }

      /** The session's parent: a session ID, null for a root, or undefined when it cannot be determined. */
      async function lookup(session: string, spend: boolean): Promise<string | null | undefined> {
        if ((failed.get(session) ?? 0) > performance.now()) {
          undetermined(spend)
          return undefined
        }
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
        undetermined(spend)
        return undefined
      }

      /**
       * For a session recorded under a root that has since been detached: the root's attachment when it was attached
       * again (recording `session` under it), "closed" when its attempt finished, "pending" when it is still open, or
       * undefined when the session is not retired or the ledger gives no answer.
       */
      async function underRetired(retiredSession: string, session: string)
        : Promise<Attachment | "pending" | "closed" | undefined> {
        const former = retired.get(retiredSession)?.root
        if (former === undefined) return undefined
        const again = rootOf(former)
        if (again !== undefined) {
          recordDescendant(session, again)
          return attached.get(again)!
        }
        const answer = await ledgerAnswer(former)
        if (answer === "closed") return "closed"
        if (answer === "known") return markPending(session, former)
        return undefined
      }

      /**
       * The attachment governing a session, "pending" for a session an open attempt owns that nobody attached here,
       * "closed" for a finished attempt's own session, or null when it is interactive. A session is managed when it is
       * attached, a recorded managed descendant, or its parent chain (from events, and bounded lookups only while a
       * root is attached) reaches one of those. It is pending when it, or a session on that chain, is a
       * derived id an open attempt owns, and closed when it, or a session on that chain, is a finished attempt's
       * derived id or was recorded under a root that has since been detached and whose attempt finished (the retired
       * record survives the parent cache). A chain that cannot be determined,
       * a cycle, a root and a chain past the limits are interactive. Only derived ids are ever asked about, each at
       * most once per answer window, so other sessions never wait for the child.
       */
      async function managed(sessionID: unknown, spend = false): Promise<Attachment | "pending" | "closed" | null> {
        if (!text(sessionID, SESSION)) return null
        const session = sessionID as string
        const known = rootOf(session)
        if (known !== undefined) return attached.get(known) ?? null
        if (holderOf(session) !== undefined) return "pending"
        if (closed.has(session)) return "closed"
        const retiredAnswer = await underRetired(session, session)
        if (retiredAnswer !== undefined) return retiredAnswer
        if (derivedSession(session)) {
          const answer = await ledgerAnswer(session)
          if (answer === "known") return markPending(session, session)
          if (answer === "closed") return rootOf(session) !== undefined ? attached.get(rootOf(session)!)! : "closed"
        }
        // With no root attached the walk uses only what creation events recorded: no `session.get` lookups.
        const mayLookUp = attached.size > 0
        const seen = new Set<string>()
        let current = sessionID as string
        let lookups = 0
        for (;;) {
          if (seen.has(current)) return null
          if (seen.size >= MAX_CHAIN) return undetermined(spend)
          seen.add(current)
          let parent = lineage.get(current)
          if (parent === undefined) {
            if (!mayLookUp) return null
            if (lookups >= MAX_LOOKUPS) return undetermined(spend)
            lookups += 1
            parent = await lookup(current, spend)
            if (parent === undefined) return null
            bounded(lineage, MAX_LINEAGE_CACHE, current, parent)
          }
          if (parent === null) return null
          const root = rootOf(parent)
          if (root !== undefined) {
            recordDescendant(session, root)
            return attached.get(root) ?? null
          }
          const holder = holderOf(parent)
          if (holder !== undefined) return markPending(session, holder)
          // A finished attempt's derived session blocks the sessions recorded under it too (never shells or lookups).
          if (closed.has(parent)) return "closed"
          const above = await underRetired(parent, session)
          if (above !== undefined) return above
          if (derivedSession(parent)) {
            if (lookups >= MAX_LOOKUPS) return undetermined(spend && attached.size > 0)
            lookups += 1
            const answer = await ledgerAnswer(parent)
            if (answer === "known") return markPending(session, parent)
            if (answer === "closed") return "closed"
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

      /** The attachment a checked hook enforces, or null for an interactive session; pending and closed ones block. */
      async function enforced(sessionID: unknown): Promise<Attachment | null> {
        const governing = await managed(sessionID, true)
        if (governing === "pending") throw new RoutingBlocked("attach_required")
        if (governing === "closed") throw new RoutingBlocked("attempt_closed")
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

      /** False when the session cannot be managed here, so interactive hooks return without a lineage question. */
      const mayBeManaged = (sessionID: unknown) => attached.size > 0 || derivedSession(sessionID)
        || (text(sessionID, SESSION) && holderOf(sessionID as string) !== undefined)

      // Subagent spawns are checked and image generation (a paid model call that never reaches `model.request`) is
      // refused for any managed session; other built-in, MCP and Code Mode tools are governed by policy deny rules.
      await ctx.tool.hook("execute.before", async (event: any) => {
        if (event?.tool === IMAGE_ACTION) {
          if (mayBeManaged(event?.sessionID) && await managed(event.sessionID) !== null) {
            throw new RoutingBlocked("image_generation_denied")
          }
          return
        }
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

      // Tightens only; a configured deny is final and never reaches this hook. Image generation and edits that may
      // change OpenCode configuration are denied for attached, pending and closed sessions alike (S3.3d).
      await ctx.permission.hook("evaluate", async (event: any) => {
        if (typeof event?.action !== "string") return
        if (!mayBeManaged(event.sessionID)) return
        let tighten: boolean
        try {
          const governing = await managed(event.sessionID)
          tighten = governing !== null && (managedDenial(event.action, event.resources, extra, directory, roots) !== null
            || (governing === "pending" ? event.action === "webfetch"
              : governing !== "closed" && (event.action === "webfetch" || governing.deny.has(event.action))))
        } catch {
          tighten = true
        }
        if (tighten && event.effect !== "deny") {
          event.effect = "deny"
          event.message = DENY_MESSAGE
        }
      })

      // The hook carries no session ID, so while any root is attached here every shell loses credentials. Pending and
      // closed sessions alone never strip: they cannot send model requests, and a stale entry must not break shells.
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
          // The option fails closed (every managed edit is denied), so a launcher must not dispatch workers here.
          if (extra === null) return { state: "not_ready", reason: "protected_paths_invalid", version: CONTRACT_VERSION }
          const { response, respawned } = await child.call("status", {})
          if (response.state !== "ok") throw new RoutingBlocked("routing_unhealthy")
          // Ready means a check can be answered: readable settings and an existing ledger (prepare creates it).
          const ledger = (response.ledger as Record<string, unknown> | null)?.state
          const reason = response.settings_state !== "ok" ? "settings_unavailable"
            : ledger === "ready" ? null : ledger === "absent" ? "ledger_absent" : "ledger_unavailable"
          if (reason) return { state: "not_ready", reason, version: CONTRACT_VERSION }
          return { state: "ok", child: respawned ? "respawned" : "up", version: CONTRACT_VERSION, respawn_ms: child.respawnMs,
            managed_denies: [...MANAGED_DENIES] }
        },
        // Gated by the dispatch authority: attach records nothing until the child verified the token for this attempt
        // and session; detach needs the attached root's attempt and token. Neither ever returns or echoes the token.
        attach: async (input: unknown) => {
          const body = object(input, "invalid_attach")
          const deny = body.deny_actions ?? []
          const route = spawnModel(body.model)
          // Only the attempt's own derived session is attached; children are learned from creation events.
          if (!text(body.session_id, DERIVED_SESSION) || !text(body.attempt_id, ATTEMPT) || !text(body.authority, AUTHORITY)
              || !text(body.funding_pool, NAME) || !route
              || !Array.isArray(deny) || deny.length > MAX_DENY || !deny.every((action) => text(action, NAME))) {
            throw new RoutingBlocked("invalid_attach")
          }
          const session = body.session_id as string
          const attempt = body.attempt_id as string
          const authority = body.authority as string
          // Every refusal past the shape check looks the same to the caller, so a caller without the token learns
          // nothing about the ledger or this instance; the reason goes only to the code-only test hook.
          const refuse = (reason: string) => {
            config.onRefusal?.(reason)
            return new RoutingBlocked("attach_refused")
          }
          const ask = async (fields: Record<string, unknown>) => {
            try {
              return (await child.call("check", fields)).response
            } catch (error) {
              throw refuse(error instanceof RoutingBlocked ? error.reason : "routing_unavailable")
            }
          }
          // 1. The token: a strict check for this attempt, session, route and pool. Another attempt's session is
          //    `session_mismatch`; a session the ledger does not know is checked as a child of the attempt.
          const verified = await ask({ attempt_id: attempt, session_id: session, authority,
            request: { ...route, harness: harnessFor(route.provider), kind: "primary", funding_pool: body.funding_pool } })
          if (verified.state !== "allow") {
            throw refuse(verified.state === "unmanaged" ? "unmanaged" : reasonFrom(verified.reason, authority, "blocked"))
          }
          if (verified.authority !== "verified" || verified.attempt_id !== attempt) throw refuse("authority_unverified")
          // 2. Ownership: the ledger must already own this exact session as a dispatched hold's. With step 1 (which
          //    refuses another attempt's session) that makes it this attempt's own derived session.
          const owned = await ask({ session_id: session, request: PROBE_REQUEST })
          if (owned.state !== "blocked" || owned.reason !== "authority_required") throw refuse("not_own_session")
          // 3. Local limits, only after the token was verified, and again after the awaits above.
          const root = rootOf(session)
          if (root !== undefined && attached.get(root)!.attempt !== attempt) throw refuse("attach_conflict")
          if (!attached.has(session) && attached.size >= MAX_ATTACHED) throw refuse("attach_limit")
          forget(session)  // a session attached as a root is no longer another root's descendant
          const previous = attached.get(session)
          // Re-attaching never loosens: the denied actions only grow.
          attached.set(session, { attempt, authority, pool: body.funding_pool as string,
            deny: new Set([...(previous?.deny ?? []), ...(deny as string[])]),
            count: previous?.count ?? 0, saturated: previous?.saturated ?? false,
            watched: previous?.watched ?? false, lookupFailed: previous?.lookupFailed ?? false })
          // Sessions found under it while it waited for this attach become its managed descendants.
          pending.delete(session)
          closed.delete(session)
          probes.delete(session)
          for (const [key, entry] of [...pending]) {
            if (entry.holder === session) {
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
          // One reason for every refusal, and the token is always compared, so a caller learns nothing about which
          // part was wrong or whether the session is attached here.
          if (!authorized(session, body.attempt_id as string, body.authority as string)) throw new RoutingBlocked("detach_refused")
          for (const [key, entry] of descendants) {
            if (entry.root === session) {
              forget(key)
              bounded(retired, MAX_RETIRED, key, { root: session, parent: entry.parent })
            }
          }
          attached.delete(session)  // with it go its count, saturation and token
          probes.delete(session)  // a derived id is asked about again, so a ledger-known one is pending, not interactive
          return { state: "detached", session_id: session }
        },
        // The recorded managed descendants of an attached root, for the launcher to cross-check its own session
        // listing before it accounts, records and detaches. Same authority rules as detach; ids and parent links only.
        descendants: async (input: unknown) => {
          const body = object(input, "invalid_descendants")
          if (!text(body.session_id, SESSION) || !text(body.attempt_id, ATTEMPT) || !text(body.authority, AUTHORITY)) {
            throw new RoutingBlocked("invalid_descendants")
          }
          const root = body.session_id as string
          const attachment = authorized(root, body.attempt_id as string, body.authority as string)
          if (!attachment) throw new RoutingBlocked("descendants_refused")
          const live = [...descendants].filter(([, entry]) => entry.root === root)
          const old = [...retired].filter(([key, entry]) => entry.root === root && !descendants.has(key))
          const all = [...live, ...old].map(([session, entry]) => ({ session_id: session, parent_session_id: entry.parent }))
            .sort((left, right) => left.session_id < right.session_id ? -1 : left.session_id > right.session_id ? 1 : 0)
          const limit = config.maxDescendantsResponse ?? MAX_DESCENDANTS_RESPONSE
          const truncated = all.length > limit
          // Why the set may be missing a descendant, first match wins; null when the shim can vouch for it.
          const reason = !streaming ? "event_stream_unavailable"
            : !attachment.watched ? "root_creation_unobserved"
            : attachment.saturated ? "descendant_limit"
            : attachment.lookupFailed ? "lineage_lookup_failed"
            : truncated ? "response_truncated" : null
          return { state: "ok", root, descendants: all.slice(0, limit), truncated, complete: reason === null, reason,
            cache: { recorded: live.length, retired: old.length } }
        },
      })

      return () => {
        events.abort()
        child.stop()
        attached.clear()
        descendants.clear()
        pending.clear()
        closed.clear()
        retired.clear()
        createdSeen.clear()
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
