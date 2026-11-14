// Execute the actual GUI lifecycle with JSX replaced by null; no rendering or native host.
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { pathToFileURL } from "node:url"
import { runInNewContext } from "node:vm"
import { StopViewer } from "../../runtime/adapters/opencode_workflow/viewer.ts"

const [artifact, adapter] = process.argv.slice(2)
assert.ok(artifact && adapter, "usage: node gui-lifecycle-check.mjs <artifact> <adapter-source>")
const load = (file) => import(pathToFileURL(resolve(artifact, "node_modules", file)).href)
const { transformSync, types: t } = await load("@babel/core/lib/index.js")
const solid = await load("solid-js/dist/solid.js")
const source = readFileSync(resolve(adapter, "gui.tsx"), "utf8")
const { code } = transformSync(source, { configFile: false, babelrc: false, parserOpts: { plugins: ["typescript", "jsx"] },
  plugins: [() => ({ visitor: {
    ImportDeclaration(path) { path.remove() },
    TSTypeAnnotation(path) { path.remove() },
    TSAsExpression(path) { path.replaceWith(path.node.expression) },
    TSNonNullExpression(path) { path.replaceWith(path.node.expression) },
    CallExpression(path) { path.node.typeParameters = null },
    VariableDeclarator(path) { path.node.definite = false },
    JSXElement(path) { path.replaceWith(path.node.openingElement.name.name === "Review"
      ? t.callExpression(t.identifier("Review"), [t.identifier("props")]) : t.nullLiteral()) },
    ExportDefaultDeclaration(path) { path.replaceWith(t.expressionStatement(t.assignmentExpression("=",
      t.memberExpression(t.identifier("globalThis"), t.identifier("definition")), path.node.declaration))) },
  } })] })
const state = []
const context = { ...solid, StopViewer, AbortController, AbortSignal, queueMicrotask, performance,
  createSignal(value) { const [get, set] = solid.createSignal(value); return [get, (next) => {
    const result = set(next); if (typeof result === "string") state.push(result); return result
  }] },
  document: { hidden: false, activeElement: null, addEventListener() {}, removeEventListener() {} },
  ResizeObserver: class { observe() {} disconnect() {} },
  Plugin: { define: (definition) => definition }, Panel: {}, Command: {}, StopReview: {},
  usePanel: () => ({ visible: () => true }),
}
runInNewContext(code, context)
let event, contribution, dispose
const pending = []
const rpc = { events: { on: (_name, callback) => { event = callback; return () => {} } }, pending: async () => [],
  open: () => new Promise((_done, reject) => pending.push(reject)), abandon: async () => {} }
const session = { id: "ses_one", directory: "/project", location: { directory: "/project" }, visit: {},
  server: { id: "builtin", builtin: true, connected: true, compatible: true, client: {} } }
const ctx = { id: "test", app: { platform: "desktop" }, client: { rpc: () => rpc }, sessions: { current: () => session },
  layout: { open() {}, close() {} }, add(kind, value) { if (kind === context.Panel) contribution = value } }
solid.createRoot((cleanup) => { dispose = cleanup; context.definition.setup(ctx) })
const tick = () => new Promise((done) => setImmediate(done))
await tick()
const notice = (id) => event({ location: { directory: "/project" }, data: { sessionID: "ses_one", rootID: "ses_one",
  formID: `frm_${id}`, attemptID: id, state: "reviewing" } })
notice("old")
let closePanel
solid.createRoot((cleanup) => { closePanel = cleanup; contribution.render({ session }) })
await tick()
notice("new"); await tick()
assert.equal(pending.length, 2)
assert.equal(state.at(-1), "opening")
pending[0](new Error("late old-view rejection")); await tick()
try { assert.equal(state.at(-1), "opening", "the old controller cannot repaint its replacement") }
finally { closePanel(); pending[1](new Error("test cleanup")); await tick(); dispose() }
console.log("PASS: actual GUI lifecycle ignores late old-controller state writes (JSX not rendered)")
