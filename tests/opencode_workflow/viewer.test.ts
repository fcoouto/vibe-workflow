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
    ownerClient: {}, desktop: true, builtin: true, connected: true, compatible: true }
  const states: string[] = []
  let usable = true
  const viewer = new StopViewer({ current: () => live, usable: () => usable, state: (value: string) => states.push(value) })
  return { viewer, rpc, client, calls, states, hide: () => { usable = false },
    switch: () => { live = { ...live, client: {} } }, remote: () => { live.builtin = false },
    switchOwner: () => { live = { ...live, ownerClient: {} } } }
}
test("complete exact safe text stays unmodified; unsupported controls and bidi deny", () => {
  assert.equal(faithfulText("<script>not HTML</script>\n" + "x".repeat(60000) + "\t終"), true)
  for (const text of ["a\u0000b", "a\u001bb", "a\u202eb", "a\rhidden", "a\ud800b", "a\u200bb"])
    assert.equal(faithfulText(text), false)
})
test("one explicit submission commits fulfilled void reply and never claims edit completion", async () => {
  const f = fixture(); const view = await f.viewer.open("ses_one", "frm_one")
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
