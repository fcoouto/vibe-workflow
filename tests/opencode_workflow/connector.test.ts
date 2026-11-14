import { test } from "node:test"
import assert from "node:assert/strict"
import { createStopForms } from "../../runtime/adapters/opencode_workflow/connector.ts"
import { readFileSync } from "node:fs"

function fixture() {
  let loads = 0; let discoveries = 0; let formCalls = 0; let config: any
  const info = { pid: process.pid, urls: ["http://127.0.0.1:4000"], version: "future", paths: { tmp: "/tmp" } }
  const session = { id: "ses_test", location: { directory: "/project" } }
  const client = { server: { info: async () => info }, session: { get: async () => session,
    form: { create: async (input: any) => { formCalls++; return input.form },
      get: async () => { formCalls++; return { state: { status: "pending" } } },
      cancel: async () => { formCalls++ } } } }
  const endpoint: any = { url: "http://127.0.0.1:4000", auth: { type: "basic", username: "unit", password: "synthetic" } }
  const forms = createStopForms("/project", { load: async () => {
    loads++
    return { Service: { discover: async () => { discoveries++; return endpoint },
      ensure: () => { throw new Error("must never start service") }, headers: () => ({ authorization: "synthetic" }) },
      OpenCode: { make: (value: any) => { config = value; return client } } }
  }, fetch: async (_request, options) => {
    assert.equal(options?.redirect, "error")
    return new Response(null, { status: 204 })
  } })
  return { forms, info, session, client, endpoint, counts: () => ({ loads, discoveries, formCalls }), config: () => config }
}

test("connector stays lazy, discovers without ensure, authenticates PID and accepts void cancellation", async () => {
  const f = fixture()
  assert.deepEqual(f.counts(), { loads: 0, discoveries: 0, formCalls: 0 })
  const signal = new AbortController().signal
  assert.deepEqual(await f.forms.create({ sessionID: "ses_test", form: { id: "frm_one" } }, { signal }), { id: "frm_one" })
  assert.equal(await f.forms.cancel({ sessionID: "ses_test", formID: "frm_one" }, { signal }), undefined)
  assert.equal(f.config().headers.authorization, "synthetic")
  assert.equal(f.counts().formCalls, 2)
})

for (const wrong of ["pid", "location", "endpoint", "auth", "capability"]) test(`connector denies ${wrong} mismatch before forms`, async () => {
  const f = fixture()
  if (wrong === "pid") f.info.pid++
  if (wrong === "location") f.session.location.directory = "/elsewhere"
  if (wrong === "endpoint") f.endpoint.url = "https://outside.invalid"
  if (wrong === "auth") delete f.endpoint.auth
  if (wrong === "capability") (f.client.session.form as any).get = undefined
  await assert.rejects(f.forms.get({ sessionID: "ses_test", formID: "frm_one" }, { signal: new AbortController().signal }))
  assert.equal(f.counts().formCalls, 0)
})

test("aborted discovery never publishes a client or calls forms", async () => {
  const f = fixture(); const controller = new AbortController(); controller.abort()
  await assert.rejects(f.forms.get({ sessionID: "ses_test", formID: "frm_one" }, { signal: controller.signal }))
  assert.deepEqual(f.counts(), { loads: 0, discoveries: 0, formCalls: 0 })
})

test("guarded transport rejects redirected or foreign endpoints and keeps abort signal", async () => {
  const f = fixture(); const signal = new AbortController().signal
  await f.forms.get({ sessionID: "ses_test", formID: "frm_one" }, { signal })
  const transport = f.config().fetch
  await assert.rejects(transport("https://outside.invalid/api/info", { signal }))
  assert.equal((await transport("http://127.0.0.1:4000/api/info", { signal })).status, 204)
})

test("missing dependency denies without a fallback transport", async () => {
  const forms = createStopForms("/project", { load: async () => { throw new Error("missing module") } })
  await assert.rejects(forms.get({ sessionID: "ses_test", formID: "frm_one" }, { signal: new AbortController().signal }))
})

test("the generic adapter directory entry stays importable without prepared native dependencies", async () => {
  const directory = new URL("../../runtime/adapters/opencode_workflow/", import.meta.url)
  const manifest = JSON.parse(readFileSync(new URL("package.json", directory), "utf8"))
  const entry = await import(new URL(manifest.exports["."], directory).href)
  assert.equal(entry.default.id, "vibe-workflow")
  assert.equal(typeof entry.default.setup, "function")
})
