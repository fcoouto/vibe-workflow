// Real Solid reactivity and controller, synthetic PanelFrame; no DOM or native GUI execution.
import assert from "node:assert/strict"
import { test } from "node:test"
import { resolve } from "node:path"
import { pathToFileURL } from "node:url"
import { StopViewer } from "../../runtime/adapters/opencode_workflow/viewer.ts"

assert.ok(process.argv[2], "usage: node panel-lifecycle-check.mjs <prepared-artifact>")
const { createRoot, createSignal, createComputed } = await import(pathToFileURL(resolve(process.argv[2], "node_modules/solid-js/dist/solid.js")).href)
for (const stage of ["begin", "reply"]) test(`native visibility signal aborts ${stage} with retained nonzero rectangles`, async () => {
  const [visible, setVisible] = createSignal(true)
  const panel = { visible, present: () => true }
  const rect = { x: 0, y: 0, width: 500, height: 500 }
  const payload = { generation: "gen", attemptID: "attempt", sessionID: "ses_one", rootID: "ses_one", formID: "frm_one",
    file: "/project/file", reason: "Stopped", patch: "--- a\n+++ a\n@@ -1 +1 @@\n-a\n+b\n", contentDigest: "a".repeat(64), remainingMS: 10000 }
  let release, signal, dispose, abandons = 0, receipts = 0
  const wait = new Promise((done) => { release = done })
  const pause = async (options) => { signal = options.signal; await wait }
  const rpc = { open: async () => payload, begin: async (_input, options) => { if (stage === "begin") await pause(options); return "ticket" },
    complete: async (input) => { if (input.outcome === "settled") receipts++ }, abandon: async () => { abandons++ } }
  const client = { session: { form: { reply: async (_input, options) => { if (stage === "reply") await pause(options) } } } }
  const visit = {}
  const viewer = new StopViewer({ current: () => ({ client, rpc, sessionID: "ses_one", directory: "/project", serverID: "builtin", visit,
    desktop: true, builtin: true, connected: true, compatible: true, visible: panel.visible() }),
    usable: () => rect.width > 0 && rect.height > 0, state: () => {} })
  await viewer.open("ses_one", "frm_one")
  createRoot((cleanup) => { dispose = cleanup; createComputed(() => viewer.presentationChanged()) })
  const submitting = viewer.submit("approve").then(() => "accepted", () => "denied")
  await new Promise((done) => setImmediate(done))
  setVisible(false)
  await Promise.resolve()
  try {
    assert.equal(panel.present(), true)
    assert.deepEqual(rect, { x: 0, y: 0, width: 500, height: 500 })
    assert.equal(signal.aborted, true)
    assert.equal(abandons, 1)
  } finally { release(); await submitting; viewer.close(); dispose() }
  assert.equal(await submitting, "denied")
  assert.equal(receipts, 0)
  assert.equal(abandons, 1)
})
