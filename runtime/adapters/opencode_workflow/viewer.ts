import type { ReviewNotice, ReviewPayload } from "./stop.ts"

// Plain text cannot faithfully expose control/bidi/zero-width formatting. Refuse, never strip or interpret it.
export function faithfulText(text: string): boolean {
  return typeof text === "string" && text.isWellFormed()
    && !/[\u0000-\u0008\u000b-\u001f\u007f-\u009f\p{Cf}\u2028\u2029]/u.test(text)
}
export type ViewerState = "opening" | "reviewing" | "submitting" | "accepted" | "rejected" | "cancelled" | "expired" | "failed" | "completed"
type Live = { client: any; ownerClient?: any; rpc: any; sessionID: string; directory: string; serverID: string; visit: object;
  desktop: boolean; builtin: boolean; connected: boolean; compatible: boolean; visible: boolean }
type Options = { current: () => Live | undefined; usable: () => boolean; state: (state: ViewerState) => void }

export class StopViewer {
  private options: Options
  private pinned?: Live
  private payload?: ReviewPayload
  private controller = new AbortController()
  private viewID = crypto.randomUUID()
  private used = false
  private abandoned = false
  private outcome?: Exclude<ReviewNotice["state"], "reviewing">
  private timer?: ReturnType<typeof setTimeout>

  constructor(options: Options) { this.options = options }

  private bound(live: Live | undefined): live is Live {
    return !!live?.desktop && live.builtin && live.connected && live.compatible
      && (!this.pinned || (live.client === this.pinned.client && live.sessionID === this.pinned.sessionID
        && live.ownerClient === this.pinned.ownerClient && live.directory === this.pinned.directory
        && live.serverID === this.pinned.serverID && live.visit === this.pinned.visit))
  }

  private current(): Live {
    const live = this.options.current()
    if (this.controller.signal.aborted || !this.bound(live) || !live.visible) throw new Error("review connection changed")
    return live
  }

  private publish(state: ViewerState): void {
    if (!this.outcome) this.options.state(state)
  }

  notice(notice: ReviewNotice): void {
    const p = this.payload
    if (!p || !this.bound(this.options.current()) || notice.attemptID !== p.attemptID || notice.formID !== p.formID
      || notice.sessionID !== p.sessionID || notice.rootID !== p.rootID || notice.state === "reviewing"
      || (this.outcome && this.outcome !== "accepted") || notice.state === this.outcome) return
    this.outcome = notice.state
    clearTimeout(this.timer)
    if (notice.state !== "accepted") this.controller.abort()
    this.options.state(notice.state)
  }

  private requestOptions() {
    return { location: { directory: this.pinned!.directory },
      signal: AbortSignal.any([this.controller.signal, AbortSignal.timeout(8000)]) }
  }

  presentationChanged(): void {
    try { this.current() } catch { this.close() }
  }

  async open(sessionID: string, formID: string): Promise<ReviewPayload> {
    if (this.pinned) throw new Error("view already opened")
    this.pinned = this.current()
    this.publish("opening")
    const start = performance.now()
    try {
      this.payload = await this.current().rpc.open({ sessionID, formID, viewID: this.viewID }, this.requestOptions())
      this.current()
      const p = this.payload!
      if (p.sessionID !== sessionID || p.formID !== formID || (p.sessionID !== this.pinned.sessionID && p.rootID !== this.pinned.sessionID)
        || ![p.patch, p.file, p.reason, p.sessionID].every(faithfulText) || p.patch.length > 65536
        || !/^[a-f0-9]{64}$/.test(p.contentDigest) || !Number.isFinite(p.remainingMS) || p.remainingMS <= 0 || p.remainingMS > 110000) {
        throw new Error("unsupported review payload")
      }
      this.payload = Object.freeze({ ...p })
      this.timer = setTimeout(() => { this.close(); this.publish("expired") }, Math.max(0, p.remainingMS - (performance.now() - start)))
      this.publish("reviewing")
      return this.payload
    } catch (error) { this.close(); this.publish("failed"); throw error }
  }

  async submit(decision: "approve" | "reject"): Promise<void> {
    if (this.used || !this.payload || !["approve", "reject"].includes(decision)) throw new Error("submission unavailable")
    this.used = true
    let ticket: string | undefined
    try {
      this.current()
      if (!this.options.usable()) throw new Error("presentation unavailable")
      this.publish("submitting")
      const p = this.payload
      ticket = await this.current().rpc.begin({ attemptID: p.attemptID, viewID: this.viewID,
        submissionID: crypto.randomUUID(), contentDigest: p.contentDigest, decision }, this.requestOptions())
      this.current()
      if (!this.options.usable() || typeof ticket !== "string" || !ticket) throw new Error("submission unavailable")
      const result = await this.current().client.session.form.reply({ sessionID: p.sessionID, formID: p.formID,
        answer: { decision } }, this.requestOptions())
      this.current()
      if (result !== undefined || !this.options.usable()) throw new Error("native reply not confirmed")
      await this.current().rpc.complete({ ticket, outcome: "settled" }, this.requestOptions())
      if (this.outcome) return
      this.current()
      this.publish(decision === "approve" ? "accepted" : "rejected")
    } catch (error) {
      if (this.outcome) return
      // Only a currently bound connection may acknowledge failure; abandon uses the original exact RPC owner.
      try { if (ticket) await this.current().rpc.complete({ ticket, outcome: "failed" }, this.requestOptions()) } catch {}
      this.close(); this.publish("failed"); throw error
    }
  }

  close(): void {
    this.controller.abort()
    clearTimeout(this.timer)
    if (this.payload && this.pinned && !this.abandoned && !this.outcome) {
      this.abandoned = true
      void Promise.resolve().then(() => this.pinned!.rpc.abandon({ attemptID: this.payload!.attemptID, viewID: this.viewID },
        { location: { directory: this.pinned!.directory }, signal: AbortSignal.timeout(8000) })).catch(() => {})
    }
    this.publish("cancelled")
  }
}
