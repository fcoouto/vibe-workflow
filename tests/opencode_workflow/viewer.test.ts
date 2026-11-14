import { test } from "node:test"
import assert from "node:assert/strict"
import { faithfulText, StopViewer } from "../../runtime/adapters/opencode_workflow/viewer.ts"

const payload = { generation: "gen", attemptID: "attempt", sessionID: "ses_one", rootID: "ses_one",
  formID: "frm_one", file: "/project/file", reason: "Stopped", patch: "--- a\n+++ a\n@@ -1 +1 @@\n-a\n+b\n",
  contentDigest: "a".repeat(64), remainingMS: 10000 }
function fixture() {
  const calls: any[] = []
  const rpc: any = { open: async () => ({ ...payload }), begin: async () => "ticket",
    complete: async (input: any) => { calls.push(["complete", input.outcome]) },
    abandon: async () => { calls.push(["abandon"]) } }
  const client = { session: { form: { reply: async (input: any) => { calls.push(["reply", input]) } } } }
  let live: any = { client, rpc, sessionID: "ses_one", directory: "/project", serverID: "sidecar", visit: {},
    ownerClient: {}, desktop: true, builtin: true, connected: true, compatible: true, visible: true }
  const states: string[] = []
  let usable = true
  const viewer = new StopViewer({ current: () => live, usable: () => usable, state: (value: string) => states.push(value) })
  return { viewer, rpc, client, calls, states, hide: () => { usable = false },
    switch: () => { live = { ...live, client: {} } }, remote: () => { live.builtin = false },
    switchOwner: () => { live = { ...live, ownerClient: {} } },
    panel: (visible: boolean) => { live.visible = visible } }
}
test("complete exact safe text stays unmodified; unsupported controls and bidi deny", () => {
  assert.equal(faithfulText("<script>not HTML</script>\n" + "x".repeat(60000) + "\t終"), true)
  for (const text of ["a\u0000b", "a\u001bb", "a\u202eb", "a\rhidden", "a\ud800b", "a\u200bb"])
    assert.equal(faithfulText(text), false)
})
test("one explicit submission commits fulfilled void reply and never claims edit completion", async (t) => {
  const f = fixture(); const view = await f.viewer.open("ses_one", "frm_one")
  t.after(() => f.viewer.close())
  assert.equal(view.patch, payload.patch)
  await f.viewer.submit("approve")
  await assert.rejects(f.viewer.submit("approve"))
  assert.deepEqual(f.calls.map((call) => call[0]), ["reply", "complete"])
  assert.equal(f.calls[1][1], "settled")
  assert.equal(f.states.at(-1), "accepted")
  assert.ok(!f.states.includes("completed"))
})
for (const stage of ["open", "begin", "reply"]) test(`server switch during ${stage} prevents success receipt`, async () => {
  const f = fixture()
  if (stage === "open") f.rpc.open = async () => { f.switch(); return payload }
  if (stage === "begin") f.rpc.begin = async () => { f.switch(); return "ticket" }
  if (stage === "reply") f.client.session.form.reply = async () => { f.switch() }
  if (stage === "open") await assert.rejects(f.viewer.open("ses_one", "frm_one"))
  else { await f.viewer.open("ses_one", "frm_one"); await assert.rejects(f.viewer.submit("approve")) }
  assert.ok(!f.calls.some((call) => call[0] === "complete" && call[1] === "settled"))
  assert.ok(f.calls.some((call) => call[0] === "abandon"))
})
for (const result of ["error", "envelope"]) test(`failed or unknown native reply (${result}) never commits or retries`, async () => {
  const f = fixture(); let replies = 0
  f.client.session.form.reply = async () => { replies++; if (result === "error") throw new Error("conflict"); return { error: "failed" } as any }
  await f.viewer.open("ses_one", "frm_one")
  await assert.rejects(f.viewer.submit("approve")); await assert.rejects(f.viewer.submit("approve"))
  assert.equal(replies, 1)
  assert.ok(!f.calls.some((call) => call[0] === "complete" && call[1] === "settled"))
})

test("remote presentation cannot claim a view or submit a reply", async () => {
  const f = fixture(); f.remote()
  await assert.rejects(f.viewer.open("ses_one", "frm_one"))
  assert.deepEqual(f.calls, [])
})

for (const stage of ["before", "begin", "reply"]) test(`lost presentation ${stage} prevents committed success`, async () => {
  const f = fixture()
  await f.viewer.open("ses_one", "frm_one")
  if (stage === "before") f.hide()
  if (stage === "begin") f.rpc.begin = async () => { f.hide(); return "ticket" }
  if (stage === "reply") f.client.session.form.reply = async () => { f.hide() }
  await assert.rejects(f.viewer.submit("approve"))
  assert.ok(!f.calls.some((call) => call[0] === "complete" && call[1] === "settled"))
})

test("closing during open abandons the late returned exact view", async () => {
  const f = fixture()
  f.rpc.open = async () => { f.viewer.close(); return payload }
  await assert.rejects(f.viewer.open("ses_one", "frm_one"))
  assert.deepEqual(f.calls, [["abandon"]])
})

test("owner RPC client restart also invalidates a native session client that stayed stable", async () => {
  const f = fixture()
  await f.viewer.open("ses_one", "frm_one")
  f.rpc.begin = async () => { f.switchOwner(); return "ticket" }
  await assert.rejects(f.viewer.submit("approve"))
  assert.ok(!f.calls.some((call) => call[0] === "reply"))
})

for (const stage of ["begin", "reply"]) test(`native panel hide during ${stage} denies despite retained usable geometry`, async () => {
  const f = fixture()
  await f.viewer.open("ses_one", "frm_one")
  if (stage === "begin") f.rpc.begin = async () => { f.panel(false); return "ticket" }
  else f.client.session.form.reply = async () => { f.panel(false) }
  await assert.rejects(f.viewer.submit("approve"))
  assert.ok(!f.calls.some((call) => call[0] === "complete" && call[1] === "settled"))
  assert.equal(f.calls.filter((call) => call[0] === "abandon").length, 1)
})

for (const stage of ["begin", "reply"]) test(`reactive retained-panel loss aborts pending ${stage} and abandons once`, async () => {
  const f = fixture()
  let release!: () => void
  let signal!: AbortSignal
  const pending = new Promise<void>((done) => { release = done })
  const pause = async (_input: any, options: any) => { signal = options.signal; await pending }
  if (stage === "begin") f.rpc.begin = async (...args: any[]) => { await pause(...args); return "ticket" }
  else f.client.session.form.reply = pause
  await f.viewer.open("ses_one", "frm_one")
  const submitting = f.viewer.submit("approve")
  const denied = assert.rejects(submitting)
  await new Promise((done) => setImmediate(done))
  f.panel(false)
  // Mirrors the host visibility effect, without changing the still-usable geometry seam.
  f.viewer.presentationChanged()
  try {
    await Promise.resolve()
    assert.equal(signal.aborted, true)
    assert.equal(f.calls.filter((call) => call[0] === "abandon").length, 1)
  } finally { release(); await denied; f.viewer.close() }
  assert.ok(!f.calls.some((call) => call[0] === "complete" && call[1] === "settled"))
  assert.equal(f.calls.filter((call) => call[0] === "abandon").length, 1)
})

for (const outcome of ["accepted", "completed", "failed", "cancelled"] as const)
  for (const rejection of [false, true]) test(`authoritative ${outcome} survives receipt ${rejection ? "rejection" : "settlement"} without compensation`, async (t) => {
    const f = fixture(); t.after(() => f.viewer.close())
    await f.viewer.open("ses_one", "frm_one")
    f.rpc.complete = async (input: any) => {
      f.calls.push(["complete", input.outcome])
      f.viewer.notice({ ...payload, state: outcome })
      if (rejection) throw new Error("late transport rejection")
    }
    await f.viewer.submit("approve")
    f.viewer.close(); f.viewer.notice({ ...payload, state: "accepted" })
    assert.equal(f.states.at(-1), outcome)
    assert.deepEqual(f.calls.filter((call) => call[0] === "complete"), [["complete", "settled"]])
  })

test("late matching acceptance replaces local close; first owner terminal wins", async () => {
  const f = fixture(); await f.viewer.open("ses_one", "frm_one")
  f.viewer.close(); await Promise.resolve()
  assert.equal(f.states.at(-1), "cancelled")
  f.viewer.notice({ ...payload, state: "accepted" })
  assert.equal(f.states.at(-1), "accepted")
  f.viewer.close()
  f.viewer.notice({ ...payload, state: "completed" })
  f.viewer.notice({ ...payload, state: "failed" }); f.viewer.notice({ ...payload, state: "accepted" })
  assert.equal(f.states.at(-1), "completed")
  assert.equal(f.calls.filter((call) => call[0] === "abandon").length, 1)
})

for (const field of ["attemptID", "formID", "sessionID", "rootID"]) test(`foreign owner notice ${field} cannot repaint current view`, async (t) => {
  const f = fixture(); t.after(() => f.viewer.close()); await f.viewer.open("ses_one", "frm_one")
  f.viewer.notice({ ...payload, [field]: "foreign", state: "completed" })
  assert.equal(f.states.at(-1), "reviewing")
})

test("old owner-client notice cannot paint a switched connection", async (t) => {
  const f = fixture(); t.after(() => f.viewer.close()); await f.viewer.open("ses_one", "frm_one")
  f.switchOwner(); f.viewer.notice({ ...payload, state: "completed" })
  assert.equal(f.states.at(-1), "reviewing")
})

test("receipt success is not owner consumption; only owner acceptance cancels acquisition expiry", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] })
  const f = fixture(); await f.viewer.open("ses_one", "frm_one"); await f.viewer.submit("approve")
  t.mock.timers.tick(10000)
  assert.equal(f.states.at(-1), "expired")
  const accepted = fixture(); await accepted.viewer.open("ses_one", "frm_one")
  accepted.viewer.notice({ ...payload, state: "accepted" }); t.mock.timers.tick(10000)
  accepted.viewer.close(); assert.equal(accepted.states.at(-1), "accepted")
  assert.equal(accepted.calls.filter((call) => call[0] === "abandon").length, 0)
})
