// Stuck-pattern escalation gate: the pure state machine behind the OpenCode plugin. Contract:
// clients/opencode-escalation-gate.md. It decides from observed facts only (edits, failed checks, finished child
// sessions and the routes sessions sent); it never calls a model, reads files or logs.

export type Route = { provider: string; model: string; variant: string | null }
export type GateSettings = { checkPatterns: string[]; ladder: string[] }
export type Decision = { effect: "allow" } | { effect: "deny" | "ask"; message: string }
export type Stage = "escalating" | "reevaluation" | "stop"
export type GateStatus = {
  held: { file: string; rounds: number; stage: Stage; escalations: number; levels: Route[] }[]
  lastError: string | null
}

export const DEFAULT_SETTINGS: GateSettings = {
  checkPatterns: ["scripts/validate.py", "scripts/test_sharded.py", "pytest", "unittest", "node --test", "npm test",
    "pnpm test", "yarn test", "go test", "cargo test", "make test", "make check"],
  ladder: [],
}
export const REPAIR_ROUNDS = 2
export const MAX_ESCALATIONS = 3

const VARIANTS = ["low", "medium", "high", "xhigh", "max"]
const POLICY = "policies/03-agent-delegation.md#stuck-pattern-escalation"
const MAX_TREES = 64
const MAX_FILES = 512
const MAX_SESSIONS = 4096
const MAX_LISTED = 5
const DERIVED = /^ses_[0-9a-f]{32}$/
const LADDER_ENTRY = /^[A-Za-z0-9][\w.:@+-]*\/[A-Za-z0-9][\w.:@+-]*(?:\/[A-Za-z0-9][\w.:@+-]*)?$/
const WRAPPERS = new Set(["env", "time", "nice", "nohup", "command", "exec", "npx"])
const RUNNERS = new Set(["uv", "poetry", "pipenv", "pnpm"])  // followed by `run` or `exec`
const INTERPRETER = /^(?:python[0-9.]*|node|bash|sh|zsh)$/
const ASSIGNMENT = /^[A-Za-z_][A-Za-z0-9_]*=/
const NEXT: Record<Stage, string> = {
  escalating: "Before another repair, dispatch a new read-only diagnosis on a model or effort stronger than every "
    + "earlier level on these files; same-level reviews do not count",
  reevaluation: "The escalation ladder is used up: run the architecture re-evaluation, a new read-only assessment by "
    + "the most capable authorized option against every agreed requirement",
  stop: "Escalation and re-evaluation did not converge: the user decides how to continue",
}

type Session = {
  root: string; parent: string | null; created: number; route: Route | null; edited: boolean; fresh: boolean
}
type Entry = {
  lastEdit: number; pending: boolean; rounds: number; levels: Route[]; escalations: number; resetUsed: boolean
  heldAt: number | null
}

export function derivedSession(id: unknown): boolean {
  return typeof id === "string" && DERIVED.test(id)
}

export function parseSettings(values: unknown): GateSettings | null {
  if (values === null || typeof values !== "object" || Array.isArray(values)) return null
  const raw = values as Record<string, unknown>
  const patterns = raw.check_patterns ?? DEFAULT_SETTINGS.checkPatterns
  const ladder = raw.ladder ?? DEFAULT_SETTINGS.ladder
  if (!Array.isArray(patterns) || !patterns.every((p) => typeof p === "string" && p.trim() !== "")) return null
  if (!Array.isArray(ladder) || !ladder.every((entry) => typeof entry === "string" && LADDER_ENTRY.test(entry))) return null
  return { checkPatterns: [...patterns], ladder: [...ladder] }
}

const key = (route: Route) => `${route.provider}/${route.model}`
const sign = (value: number): 1 | 0 | -1 => value > 0 ? 1 : value < 0 ? -1 : 0
const same = (a: Route, b: Route) => a.provider === b.provider && a.model === b.model && a.variant === b.variant

/** 1 when `a` is stronger, -1 when weaker, 0 when equal, null when the two cannot be ranked. */
export function compareRoutes(a: Route, b: Route, ladder: string[]): 1 | 0 | -1 | null {
  if (key(a) === key(b)) {
    const left = a.variant === null ? -1 : VARIANTS.indexOf(a.variant)
    const right = b.variant === null ? -1 : VARIANTS.indexOf(b.variant)
    return left < 0 || right < 0 ? null : sign(left - right)
  }
  const left = ladder.indexOf(key(a))
  const right = ladder.indexOf(key(b))
  return left < 0 || right < 0 ? null : sign(left - right)
}

/** Command segments split on unquoted `&&`, `||`, `;`, `|` and `&`; null when the quoting is unbalanced. */
function segments(command: string): string[][] | null {
  const result: string[][] = [[]]
  let word = ""
  let started = false
  let quote: string | null = null
  const flush = () => {
    if (started) result[result.length - 1].push(word)
    word = ""
    started = false
  }
  for (let i = 0; i < command.length; i++) {
    const c = command[i]
    if (quote) {
      if (c === quote) quote = null
      else word += c
    } else if (c === "'" || c === '"') {
      quote = c
      started = true
    } else if (c === "\\") {
      word += command[++i] ?? ""
      started = true
    } else if (/\s/.test(c)) {
      flush()
    } else if (c === "&" || c === "|" || c === ";") {
      flush()
      if ((c === "&" || c === "|") && command[i + 1] === c) i++
      result.push([])
    } else {
      word += c
      started = true
    }
  }
  if (quote) return null
  flush()
  return result
}

/** True when a command segment runs one of the check patterns as its program, or as an interpreter's script. */
export function isCheck(command: string, patterns: string[]): boolean {
  for (const raw of segments(command) ?? []) {
    const tokens = raw.map((token) => token.replace(/^\.\//, ""))
    let at = 0
    for (;;) {
      while (at < tokens.length && ASSIGNMENT.test(tokens[at])) at++
      if (WRAPPERS.has(tokens[at])) {
        at++
        while (at < tokens.length && tokens[at].startsWith("-")) at++
      } else if (RUNNERS.has(tokens[at]) && (tokens[at + 1] === "run" || tokens[at + 1] === "exec")) {
        at += 2
      } else {
        break
      }
    }
    if (at >= tokens.length) continue
    const starts = [at]
    if (INTERPRETER.test(tokens[at].split("/").pop()!)) {
      let next = at + 1
      while (next < tokens.length && tokens[next].startsWith("-")) next++
      starts.push(next)
    }
    for (const pattern of patterns) {
      const wanted = pattern.trim().split(/\s+/)
      if (starts.some((start) => wanted.every((token, offset) => tokens[start + offset] === token))) return true
    }
  }
  return false
}

/** The files a user message asks to reset (empty: every held file), or null when it asks for no reset. */
export function resetRequest(text: string): { files: string[] } | null {
  let asked = false
  const files: string[] = []
  for (const sentence of text.split(/[.!?](?=\s|$)|\n/)) {
    const lower = sentence.toLowerCase().replace(/’/g, "'")
    const match = /\breset\b/.exec(lower)
    if (!match || !/\b(?:gate|counters?|holds?|escalation)\b/.test(lower)) continue
    if (/\b(?:don't|do not|never|no)\b/.test(lower.slice(0, match.index))) return null
    asked = true
    for (const file of sentence.match(/[\w./-]+\.[A-Za-z0-9]+/g) ?? []) files.push(file)
  }
  return asked ? { files } : null
}

export class Gate {
  private settings: GateSettings
  private seq = 0
  private sessions = new Map<string, Session>()
  private trees = new Map<string, Map<string, Entry>>()

  constructor(settings: GateSettings) {
    this.settings = settings
  }

  /** `recovered`: first seen through a lineage lookup, so its creation time and earlier edits are unknown. */
  created(session: string, parent: string | null, recovered = false): void {
    const existing = this.sessions.get(session)
    if (existing) {
      if (!recovered && !existing.fresh) {
        existing.fresh = true  // its creation event proves it is new
        existing.created = ++this.seq
      }
      return
    }
    if (derivedSession(session) || this.sessions.size >= MAX_SESSIONS) return
    let root = session
    if (parent !== null) {
      const above = this.session(parent)
      if (!above) return
      root = above.root
    } else {
      if (this.trees.size >= MAX_TREES) this.trees.delete(this.trees.keys().next().value!)
      this.trees.set(session, new Map())
    }
    this.sessions.set(session, { root, parent, created: ++this.seq, route: null, edited: false, fresh: !recovered })
  }

  knows(session: string): boolean {
    return this.session(session) !== undefined
  }

  isRoot(session: string): boolean {
    return this.session(session)?.parent === null
  }

  routed(session: string, route: Route): void {
    const known = this.session(session)
    if (known) known.route = route
  }

  edited(session: string, files: string[]): void {
    const known = this.session(session)
    if (!known) return
    known.edited = true
    const tree = this.trees.get(known.root)!
    const now = ++this.seq
    for (const file of files) {
      let entry = tree.get(file)
      if (entry && entry.heldAt !== null && this.stage(entry) === "stop") {
        tree.delete(file)  // the user approved the stop-stage ask: start this file afresh
        entry = undefined
      }
      if (!entry) {
        if (tree.size < MAX_FILES) tree.set(file, fresh(now))
        continue
      }
      if (entry.pending) {
        entry.rounds += 1
        if (known.route) entry.levels.push(known.route)
        entry.pending = false
      }
      entry.lastEdit = now
    }
  }

  failed(session: string): void {
    const known = this.session(session)
    if (!known) return
    this.failure(known.root, ++this.seq, null)
  }

  finished(session: string): void {
    const known = this.session(session)
    if (!known || known.parent === null || known.edited || !known.fresh) return
    const now = ++this.seq
    const tree = this.trees.get(known.root)!
    if (known.route) {
      for (const entry of tree.values()) {
        if (entry.heldAt !== null && entry.heldAt < known.created) this.release(entry, known.route)
      }
    }
    this.failure(known.root, now, known.created)
  }

  editDecision(session: string, files: string[]): Decision {
    const known = this.session(session)
    if (!known) return { effect: "allow" }
    const tree = this.trees.get(known.root)!
    const held = files.flatMap((file) => {
      const entry = tree.get(file)
      return entry && entry.heldAt !== null ? [[file, entry] as const] : []
    })
    if (!held.length) return { effect: "allow" }
    const stages = held.map(([, entry]) => this.stage(entry))
    // Ask only when every held file is at the stop stage; any other held file still needs its diagnosis.
    const stage: Stage = stages.includes("reevaluation") ? "reevaluation" : stages.includes("escalating") ? "escalating"
      : "stop"
    const names = held.slice(0, MAX_LISTED).map(([file]) => file).join(", ")
    const more = held.length > MAX_LISTED ? ` and ${held.length - MAX_LISTED} more` : ""
    const rounds = Math.max(...held.map(([, entry]) => entry.rounds))
    const message = `escalation gate: ${names}${more} had ${rounds} repair rounds and failed again (stage: ${stage}). `
      + `${NEXT[stage]}. See ${POLICY}.`
    return { effect: stage === "stop" ? "ask" : "deny", message }
  }

  holds(session: string): boolean {
    const known = this.session(session)
    return known !== undefined && [...this.trees.get(known.root)!.values()].some((entry) => entry.heldAt !== null)
  }

  reset(session: string, request: { files: string[] }): string[] {
    const known = this.session(session)
    if (!known) return []
    const tree = this.trees.get(known.root)!
    const cleared = [...tree.entries()].filter(([file, entry]) => request.files.length
      ? request.files.some((name) => file === name || file.endsWith("/" + name.replace(/^\.?\//, "")))
      : entry.heldAt !== null).map(([file]) => file)
    for (const file of cleared) tree.delete(file)
    return cleared
  }

  status(session: string): GateStatus {
    const known = this.session(session)
    const tree = known ? this.trees.get(known.root)! : new Map<string, Entry>()
    const held = [...tree.entries()].filter(([, entry]) => entry.heldAt !== null).map(([file, entry]) => (
      { file, rounds: entry.rounds, stage: this.stage(entry), escalations: entry.escalations, levels: [...entry.levels] }))
    return { held, lastError: null }
  }

  private session(id: string): Session | undefined {
    const known = this.sessions.get(id)
    return known && this.trees.has(known.root) ? known : undefined
  }

  /** A failure observed at `now`; a finished reviewer counts only for files edited before it was created. */
  private failure(root: string, now: number, createdAt: number | null) {
    for (const entry of this.trees.get(root)!.values()) {
      if (entry.heldAt !== null || entry.lastEdit >= (createdAt ?? now)) continue
      if (entry.pending) {
        entry.rounds = 0  // failed twice without a repair in between: not a repair loop on this file
        continue
      }
      entry.pending = true
      if (entry.rounds >= REPAIR_ROUNDS) entry.heldAt = now
    }
  }

  private release(entry: Entry, route: Route) {
    const ladder = this.settings.ladder
    const stage = this.stage(entry)
    if (stage === "escalating" && entry.levels.every((level) => {
      const order = compareRoutes(route, level, ladder)
      return order === null ? !same(route, level) : order === 1
    })) {
      entry.escalations += 1
    } else if (stage === "reevaluation" && entry.levels.every((level) => compareRoutes(route, level, ladder) !== -1)) {
      entry.escalations = 0
      entry.resetUsed = true
    } else {
      return
    }
    entry.rounds = 0
    entry.pending = false
    entry.heldAt = null
    entry.levels.push(route)
  }

  private stage(entry: Entry): Stage {
    const exhausted = entry.escalations >= MAX_ESCALATIONS || this.nothingAbove(entry.levels)
    if (!exhausted) return "escalating"
    return entry.resetUsed ? "stop" : "reevaluation"
  }

  /** Every level is comparable, the strongest is at `max`, and no ladder entry ranks above its model. */
  private nothingAbove(levels: Route[]): boolean {
    if (!levels.length) return false
    const ladder = this.settings.ladder
    let strongest = levels[0]
    for (const level of levels.slice(1)) {
      const order = compareRoutes(level, strongest, ladder)
      if (order === null) return false
      if (order === 1) strongest = level
    }
    if (levels.some((level) => compareRoutes(level, strongest, ladder) === null)) return false
    const position = ladder.indexOf(key(strongest))
    return strongest.variant === "max" && (position < 0 || position === ladder.length - 1)
  }
}

function fresh(now: number): Entry {
  return { lastEdit: now, pending: false, rounds: 0, levels: [], escalations: 0, resetUsed: false, heldAt: null }
}
