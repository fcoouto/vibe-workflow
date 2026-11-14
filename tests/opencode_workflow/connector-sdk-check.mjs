// Uses the installed serializer with an in-memory transport; never discovers or starts a real service.
import assert from "node:assert/strict"
import { test } from "node:test"
import { resolve } from "node:path"
import { pathToFileURL } from "node:url"
import { createStopForms } from "../../runtime/adapters/opencode_workflow/connector.ts"

assert.ok(process.argv[2], "usage: node connector-sdk-check.mjs <prepared-artifact>")
const artifact = resolve(process.argv[2])
const { OpenCode } = await import(pathToFileURL(resolve(artifact, "node_modules/@opencode/client/dist/promise/index.js")).href)
const form = { id: "frm_synthetic", title: "Review exact edit", metadata: { generation: "gen", nonce: "nonce", binding: "binding" },
  fields: [{ key: "decision", type: "string", title: "Approve?", required: true, custom: false,
    description: "/project/canary.txt\nStopped\n--- a\n+++ a\n@@ -1 +1 @@\n-<old>\n+終\tnew\n",
    options: [{ value: "approve", label: "Approve" }, { value: "reject", label: "Reject" }] }] }

for (const status of [200, 400, 409]) test(`official serializer preserves exact create body; HTTP ${status} is not retried`, async () => {
  const requests = []
  const forms = createStopForms("/project", { load: async () => ({ OpenCode, Service: {
    discover: async () => ({ url: "http://127.0.0.1:4000", auth: { type: "basic", username: "unit", password: "synthetic" } }),
    headers: () => ({ authorization: "Basic synthetic" }),
  } }), fetch: async (request, init) => {
    const url = new URL(request instanceof Request ? request.url : String(request))
    assert.equal(url.origin, "http://127.0.0.1:4000")
    assert.equal(init.redirect, "error")
    assert.ok(init.signal instanceof AbortSignal)
    requests.push({ path: url.pathname, method: init.method, body: init.body && JSON.parse(init.body) })
    if (url.pathname === "/api/info") return Response.json({ pid: process.pid, urls: [url.origin] })
    if (url.pathname === "/api/session/ses_synthetic") return Response.json({ data: { id: "ses_synthetic", location: { directory: "/project" } } })
    assert.equal(url.pathname, "/api/session/ses_synthetic/form")
    return Response.json(status === 200 ? { data: { ...form, sessionID: "ses_synthetic" } } : { message: "synthetic denial" }, { status })
  } })
  const pending = forms.create({ sessionID: "ses_synthetic", form }, { signal: new AbortController().signal })
  if (status === 200) assert.deepEqual(await pending, { ...form, sessionID: "ses_synthetic" })
  else await assert.rejects(pending)
  const posts = requests.filter((item) => item.method === "POST")
  assert.equal(posts.length, 1)
  assert.deepEqual(posts[0].body, form)
  assert.equal(posts[0].body.fields[0].description, form.fields[0].description)
  assert.deepEqual(requests.map((item) => item.path), ["/api/info", "/api/session/ses_synthetic", "/api/session/ses_synthetic/form"])
})
