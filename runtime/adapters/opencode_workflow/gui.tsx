import { Command, Panel, Plugin, type PanelProps } from "@opencode/plugin/gui"
import { createEffect, createMemo, createSignal, onCleanup, onMount, Show, untrack } from "solid-js"
import { StopReview } from "./rpc.ts"
import { StopViewer, type ViewerState } from "./viewer.ts"
import type { ReviewNotice, ReviewPayload } from "./stop.ts"

const labels: Record<ViewerState, string> = {
  opening: "Opening complete change…", reviewing: "Review the complete change before deciding.",
  submitting: "Submitting decision…", accepted: "Decision accepted. Native permission or edit execution is still pending.",
  rejected: "Edit rejected. The hold remains.", cancelled: "Review cancelled. The hold remains.",
  expired: "Review expired. Request a fresh stopped edit or reset the gate.",
  failed: "Review unavailable or submission failed. The hold remains unless native completion was already confirmed.",
  completed: "The matching native edit completed.",
}

export default Plugin.define({
  setup(ctx) {
    const [notices, setNotices] = createSignal<ReviewNotice[]>([])
    const tab = { id: "stop-review", title: "Stopped edit", closable: "compact" as const, tabbable: true }
    const own = (session: any) => ctx.app.platform === "desktop" && session?.server.builtin
      && session.server.connected && session.server.compatible && session.location?.directory === session.directory
    const key = `${ctx.id}:stop-review`

    // One scoped snapshot per live routed session; notifications carry identities only, never patch contents.
    createEffect(() => {
      const session = ctx.sessions.current()
      const client = ctx.client
      setNotices([])
      if (!own(session)) return
      const controller = new AbortController()
      const rpc = client.rpc(StopReview)
      let active = true
      const current = () => active && ctx.client === client && ctx.sessions.current() === session && own(session)
      const accept = (notice: ReviewNotice) => {
        if (!current() || (notice.rootID !== session!.id && notice.sessionID !== session!.id)) return
        setNotices((old) => old.some((item) => item.attemptID === notice.attemptID)
          ? old.map((item) => item.attemptID === notice.attemptID ? notice : item) : [...old, notice].slice(-256))
        if (notice.state === "reviewing") ctx.layout.open(key, session!)
      }
      const unsubscribe = rpc.events.on("changed", (event) => {
        if (event.location.directory === session!.directory) accept(event.data as ReviewNotice)
      })
      void rpc.pending({ sessionID: session!.id }, { location: { directory: session!.directory },
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]) })
        .then((items) => { if (current()) for (const notice of items as ReviewNotice[])
          if (!notices().some((item) => item.attemptID === notice.attemptID)) accept(notice) }).catch(() => {})
      onCleanup(() => { active = false; controller.abort(); unsubscribe() })
    })

    function Review(props: PanelProps) {
      const [payload, setPayload] = createSignal<ReviewPayload>()
      const [state, setState] = createSignal<ViewerState>("opening")
      const [usable, setUsable] = createSignal(false)
      let controller: StopViewer | undefined
      let shell!: HTMLDivElement; let header!: HTMLElement; let content!: HTMLPreElement; let actions!: HTMLDivElement
      const previousFocus = document.activeElement as HTMLElement | null
      const selected = createMemo(() => notices().filter((item) => item.sessionID === props.session.id
        || item.rootID === props.session.id).at(-1)?.attemptID)
      const live = () => {
        const session = props.session
        const client = ctx.client
        if (!own(session)) return undefined
        return { client: session.server.client, ownerClient: client, rpc: client.rpc(StopReview), sessionID: session.id, directory: session.directory,
          serverID: session.server.id, visit: session.visit, desktop: ctx.app.platform === "desktop",
          builtin: session.server.builtin, connected: session.server.connected, compatible: session.server.compatible }
      }
      const measure = () => {
        const p = payload()
        const visible = (node: HTMLElement) => node?.isConnected && node.getClientRects().length > 0
          && node.clientWidth > 0 && node.clientHeight > 0
        const bounds = shell?.getBoundingClientRect()
        const contained = (node: HTMLElement) => { const rect = node.getBoundingClientRect(); return bounds
          && rect.top >= bounds.top && rect.bottom <= bounds.bottom && rect.left >= bounds.left && rect.right <= bounds.right }
        const valid = !!p && visible(shell) && visible(header) && visible(content) && visible(actions)
          && content.textContent === p.patch && content.clientHeight >= 32
          && header.scrollHeight <= header.clientHeight && actions.scrollHeight <= actions.clientHeight
          && [header, content, actions].every(contained) && !document.hidden
        setUsable(valid)
        if (p && (!shell?.isConnected || document.hidden || (!valid && state() === "submitting"))) controller?.close()
        return valid
      }
      createEffect(() => {
        const session = props.session
        const client = ctx.client
        const connected = session.server.connected
        const attemptID = selected()
        const notice = untrack(() => notices().find((item) => item.attemptID === attemptID))
        if (!connected || !own(session) || !notice) return
        const viewer = new StopViewer({ current: live, usable: measure, state: setState })
        controller = viewer
        let active = true
        setPayload(undefined)
        void viewer.open(notice.sessionID, notice.formID).then((value) => {
          if (active && client === ctx.client) { setPayload(value); queueMicrotask(() => { measure(); content?.focus() }) }
        }).catch(() => {})
        onCleanup(() => { active = false; viewer.close(); if (controller === viewer) controller = undefined })
      })
      createEffect(() => {
        const p = payload()
        const notice = p && notices().find((item) => item.attemptID === p.attemptID)
        if (notice && notice.state !== "reviewing") {
          if (notice.state === "cancelled" || notice.state === "failed") controller?.close()
          setState(notice.state)
        }
      })
      const observer = new ResizeObserver(measure)
      onMount(() => { observer.observe(shell); observer.observe(header); observer.observe(actions); measure() })
      const visibility = () => { if (document.hidden) controller?.close(); else measure() }
      document.addEventListener("visibilitychange", visibility)
      onCleanup(() => { observer.disconnect(); document.removeEventListener("visibilitychange", visibility);
        controller?.close(); if (previousFocus?.isConnected) previousFocus.focus() })
      const cancel = () => { controller?.close(); ctx.layout.close(key, props.session) }
      const submit = (decision: "approve" | "reject") => { void controller?.submit(decision).catch(() => {}) }
      return <div ref={shell} style={{ display: "flex", "flex-direction": "column", height: "100%", "min-height": "0", padding: "12px", gap: "12px" }}
        onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); cancel() } }}>
        <header ref={header} style={{ "flex-shrink": "0", "overflow-wrap": "anywhere" }}>
          <strong>Review stopped edit</strong>
          <Show when={payload()}>{(p) => <><div>{p().file}</div><div>{p().reason}</div><div>Requesting session: {p().sessionID}</div></>}</Show>
          <p role="status" aria-live="polite">{labels[state()]}</p>
        </header>
        <pre ref={content} tabIndex={0} aria-label="Complete proposed change" dir="ltr"
          style={{ flex: "1 1 0", "min-height": "32px", "min-width": "0", overflow: "auto", "white-space": "pre",
            "tab-size": "4", "unicode-bidi": "isolate", margin: "0", "user-select": "text" }}>{payload()?.patch ?? ""}</pre>
        <div ref={actions} style={{ display: "flex", "flex-wrap": "wrap", gap: "8px", "flex-shrink": "0" }}>
          <ctx.ui.Button disabled={!usable() || state() !== "reviewing"} onClick={() => submit("reject")}>Reject</ctx.ui.Button>
          <ctx.ui.Button disabled={!usable() || state() !== "reviewing"} busy={state() === "submitting"}
            onClick={() => submit("approve")}>Approve this edit</ctx.ui.Button>
          <ctx.ui.Button onClick={cancel}>Cancel / Close</ctx.ui.Button>
        </div>
      </div>
    }

    ctx.add(Panel, { id: "stop-review", region: "side", transient: true,
      list: (input) => own(input.session) && input.open.includes(tab.id) ? [tab] : [],
      render: (props) => <Review {...props} />, closed: (): false => false })
    ctx.add(Command, () => ({ id: "stop-review", title: "Review stopped edit", enabled: !!own(ctx.sessions.current()),
      run: () => { const session = ctx.sessions.current(); if (session && own(session)) ctx.layout.open(key, session) } }))
  },
})
