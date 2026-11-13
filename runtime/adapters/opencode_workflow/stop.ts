// One-use native stop approval. No transport discovery, credentials, model calls or file writes.
import { createHash, randomBytes, randomUUID } from "node:crypto"
import { closeSync, constants, fstatSync, openSync, readSync } from "node:fs"
import { resolve } from "node:path"
import type { Gate } from "./gate.ts"

export type StopForms = {
  create(input: { sessionID: string; form: Record<string, unknown> }, options: { signal: AbortSignal }): Promise<unknown>
  get(input: { sessionID: string; formID: string }, options: { signal: AbortSignal }): Promise<unknown>
  cancel(input: { sessionID: string; formID: string }, options: { signal: AbortSignal }): Promise<unknown>
}
type Attempt = {
  gate: Gate; session: string; root: string; hold: number; file: string; key: string; target: string
  form: any; binding: string; snapshot: string; start: number; calls: number; sent: boolean
  controller: AbortController; terminal: boolean; wake?: () => void; detail?: any; permit: boolean
}
type RecordEntry = { tool: string; attempt?: Attempt }
type Options = { directory: string; gate: () => Gate | null; forms?: StopForms;
  error: (code: string) => void; now?: () => number }
const CAPACITY = 256
const FILE_BYTES = 1048576
const CHANGE_BYTES = 65536
const ACQUIRE_MS = 110000
const TOTAL_MS = 120000
const OPERATION_MS = 8000
const hash = (value: string | Buffer) => createHash("sha256").update(value).digest("hex")
const key = (session: unknown, message: unknown, id: unknown) =>
  [session, message, id].every((value) => typeof value === "string" && value.length > 0 && value.length <= 256)
    ? JSON.stringify([session, message, id]) : null

function snapshot(file: string): string {
  const fd = openSync(file, constants.O_RDONLY | constants.O_NOFOLLOW)
  try {
    const info = fstatSync(fd)
    if (!info.isFile() || info.size > FILE_BYTES) throw new Error("unsupported file")
    const buffer = Buffer.alloc(FILE_BYTES + 1)
    let size = 0
    while (size < buffer.length) {
      const count = readSync(fd, buffer, size, buffer.length - size, size)
      if (!count) break
      size += count
    }
    if (size > FILE_BYTES || buffer.subarray(0, size).includes(0)) throw new Error("unsupported file")
    new TextDecoder("utf-8", { fatal: true }).decode(buffer.subarray(0, size))
    return `${info.dev}:${info.ino}:${hash(buffer.subarray(0, size))}`
  } finally { closeSync(fd) }
}

const binding = (event: any) => JSON.stringify({ session: event.sessionID, source: event.source,
  agent: event.agent ?? null, action: event.action, resources: event.resources, metadata: event.metadata })
const identity = (form: any) => JSON.stringify({ id: form?.id, sessionID: form?.sessionID,
  title: form?.title, metadata: form?.metadata, fields: form?.fields })

export class StopApprovals {
  private generation = randomUUID()
  private records = new Map<string, RecordEntry>()
  private targets = new Map<string, Attempt>()
  private closed = false
  private now: () => number
  private options: Options

  constructor(options: Options) {
    this.options = options
    this.now = options.now ?? (() => performance.now())
  }

  before(event: any): void {
    const id = key(event?.sessionID, event?.messageID, event?.id)
    if (this.closed || !id || event.tool !== "edit" || !this.options.gate()?.holds(event.sessionID)) return
    if (!this.records.has(id) && this.records.size < CAPACITY) this.records.set(id, { tool: event.tool })
  }

  async evaluate(event: any, files: string[]): Promise<void> {
    const incoming = event.effect
    const message = event.message
    event.effect = "deny"
    event.message = "escalation gate: stopped edit requires a matching explicit form approval; the root user may reset the gate"
    if (!this.options.forms) { this.options.error("stop_form_bridge_unavailable"); return }
    const id = event.source?.type === "tool" ? key(event.sessionID, event.source.messageID, event.source.id) : null
    const record = id && this.records.get(id)
    const gate = this.options.gate()
    const stopped = files.length === 1 && gate?.stopped(event.sessionID, files[0])
    if (this.closed || event.action !== "edit" || !record || !gate || !stopped
      || (incoming !== "allow" && incoming !== "ask")) return
    if (record.attempt) { this.invalidate(record.attempt); return }
    const target = JSON.stringify([stopped.root, files[0]])
    if (this.targets.has(target)) return
    let a: Attempt | undefined
    let timer: ReturnType<typeof setTimeout> | undefined
    try {
      const metadata = JSON.stringify(event.metadata)
      const changes = event.metadata?.files
      const change = changes?.[0]
      if (typeof metadata !== "string" || Buffer.byteLength(metadata) > CHANGE_BYTES || !Array.isArray(changes)
        || changes.length !== 1 || typeof change?.file !== "string"
        || resolve(this.options.directory, change.file) !== files[0] || change.status !== "modified"
        || typeof change.patch !== "string"
        || !/^(?:Index: [^\r\n]+\r?\n={3,}\r?\n)?--- [^\r\n]+\r?\n\+\+\+ [^\r\n]+\r?\n@@ /.test(change.patch)
        || ![change.additions, change.deletions].every((n) => Number.isSafeInteger(n) && n >= 0)) return
      const before = snapshot(files[0])
      const captured = binding(event)
      const nonce = randomBytes(16).toString("hex")
      const form = { id: `frm_${nonce}`, sessionID: event.sessionID,
        title: "Escalation stopped: approve this native edit?",
        metadata: { generation: this.generation, nonce, binding: hash(captured + before) },
        fields: [{ key: "decision", type: "string", title: "Approve this edit?", required: true, custom: false,
          description: `${files[0]}\nEscalation and re-evaluation did not converge.\n${change.patch}`,
          options: [{ value: "approve", label: "Approve this edit" }, { value: "reject", label: "Reject" }] }] }
      a = { gate, session: event.sessionID, ...stopped, file: files[0], key: id!, target, form,
        binding: captured, snapshot: before, start: this.now(), calls: 0, sent: false,
        controller: new AbortController(), terminal: false, permit: false }
      record.attempt = a
      this.targets.set(target, a)
      const attempt = a
      timer = setTimeout(() => attempt.controller.abort(), ACQUIRE_MS)
      const { sessionID: _session, ...payload } = form
      a.sent = true
      const created = await this.call(a, "create", { sessionID: a.session, form: payload })
      if (identity(created) !== identity(form)) throw new Error("form identity mismatch")
      let detail = await this.read(a)
      if (detail.state.status === "pending") {
        await this.wait(a)
        detail = await this.read(a)
        if (detail.state.status === "pending") detail = await this.read(a)
      }
      if (detail.state.status !== "answered" || Object.keys(detail.state.answer ?? {}).length !== 1
        || detail.state.answer.decision !== "approve") return
      const current = this.options.gate()?.stopped(a.session, a.file)
      if (this.closed || a.controller.signal.aborted || this.now() - a.start >= ACQUIRE_MS
        || this.options.gate() !== gate || current?.root !== a.root || current?.hold !== a.hold
        || binding(event) !== a.binding || snapshot(a.file) !== a.snapshot) return
      a.permit = true
      event.effect = incoming
      if (message === undefined) delete event.message
      else event.message = message
    } catch {
      this.options.error("stop_form_failed")
    } finally {
      clearTimeout(timer)
      if (a && !a.permit) {
        a.controller.abort()
        await this.reconcile(a)
        if (this.targets.get(a.target) === a) this.targets.delete(a.target)
      }
    }
  }

  after(event: any): boolean {
    const id = key(event?.sessionID, event?.messageID, event?.id)
    const record = id && this.records.get(id)
    if (!record) return false
    this.records.delete(id!)
    const a = record.attempt
    if (!a) return false
    const current = this.options.gate()?.stopped(a.session, a.file)
    const approved = !this.closed && a.permit && event.tool === "edit" && event.status === "completed"
      && this.options.gate() === a.gate && current?.root === a.root && current?.hold === a.hold
    this.invalidate(a)
    if (approved) a.gate.edited(a.session, [a.file], a.hold)
    return true
  }

  event(event: any): void {
    for (const a of this.targets.values()) {
      const data = event?.data
      if ((event.type === "session.execution.interrupted" || event.type === "session.execution.failed")
        && (data?.sessionID === a.session || data?.sessionID === a.root)) this.invalidate(a)
      if ((event.type === "form.replied" || event.type === "form.cancelled")
        && data?.id === a.form.id && data?.sessionID === a.session && !a.terminal) {
        a.terminal = true
        a.wake?.()
      }
    }
  }

  reset(root: string, files: string[]): void {
    for (const a of this.targets.values()) if (a.root === root && files.includes(a.file)) this.invalidate(a)
  }

  close(): void {
    this.closed = true
    for (const a of this.targets.values()) this.invalidate(a)
    this.records.clear()
  }

  private invalidate(a: Attempt): void {
    a.permit = false
    a.controller.abort()
    if (this.targets.get(a.target) === a) this.targets.delete(a.target)
  }

  private wait(a: Attempt): Promise<void> {
    if (a.controller.signal.aborted) return Promise.reject(new Error("inactive approval"))
    if (a.terminal) return Promise.resolve()
    return new Promise<void>((done, fail) => {
      const abort = () => { a.wake = undefined; fail(new Error("inactive approval")) }
      a.controller.signal.addEventListener("abort", abort, { once: true })
      a.wake = () => { a.controller.signal.removeEventListener("abort", abort); a.wake = undefined; done() }
    })
  }

  private async call(a: Attempt, method: keyof StopForms, input: any, cleanup = false): Promise<any> {
    const remaining = (cleanup ? TOTAL_MS : ACQUIRE_MS) - (this.now() - a.start)
    if (remaining <= 0 || a.calls >= (cleanup ? 6 : 4) || (!cleanup && a.controller.signal.aborted)) {
      throw new Error("approval allocation exhausted")
    }
    a.calls++
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    let abort: (() => void) | undefined
    try {
      const expired = new Promise<never>((_done, fail) => {
        abort = () => { controller.abort(); fail(new Error("approval operation stopped")) }
        if (!cleanup) a.controller.signal.addEventListener("abort", abort, { once: true })
        timer = setTimeout(abort, Math.min(OPERATION_MS, remaining))
      })
      return await Promise.race([Promise.resolve().then(() => this.options.forms![method](input,
        { signal: controller.signal })), expired])
    } finally {
      clearTimeout(timer)
      if (abort) a.controller.signal.removeEventListener("abort", abort)
      controller.abort()
    }
  }

  private async read(a: Attempt, cleanup = false): Promise<any> {
    const detail = await this.call(a, "get", { sessionID: a.session, formID: a.form.id }, cleanup)
    if (identity(detail) !== identity(a.form) || !["pending", "answered", "cancelled"].includes(detail?.state?.status)) {
      throw new Error("invalid authoritative form")
    }
    a.detail = detail
    return detail
  }

  private async reconcile(a: Attempt): Promise<void> {
    if (!a.sent) return
    try {
      const detail = a.detail ?? await this.read(a, true)
      if (detail.state.status !== "pending") return
      await this.call(a, "cancel", { sessionID: a.session, formID: a.form.id }, true)
      if ((await this.read(a, true)).state.status === "pending") throw new Error("cancellation unconfirmed")
    } catch { this.options.error("stop_form_cleanup_unconfirmed") }
  }
}
