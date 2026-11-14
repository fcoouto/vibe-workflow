// Run with an already prepared artifact; this resolves entries but never loads a native plugin or service.
import assert from "node:assert/strict"
import { resolve } from "node:path"
import { pathToFileURL } from "node:url"

const [sourceDirectory, artifactDirectory] = process.argv.slice(2)
assert.ok(sourceDirectory && artifactDirectory, "usage: node loader-check.mjs <source-adapter> <prepared-artifact>")
const source = resolve(sourceDirectory)
const artifact = resolve(artifactDirectory)
const { Host } = await import(pathToFileURL(resolve(artifact, "node_modules/@opencode/plugin/dist/host.js")).href)
const actual = { source: Host.resolve({ directory: source }), artifact: Host.resolve({ directory: artifact }) }
console.log(JSON.stringify(actual, null, 2))
assert.equal(actual.source.server, pathToFileURL(resolve(source, "index.ts")).href,
  "the actual host must select the dependency-free generic entry")
assert.equal(actual.artifact.server, pathToFileURL(resolve(artifact, "runtime/adapters/opencode_workflow/server.ts")).href,
  "the actual host must select the native server only in the prepared artifact")
assert.equal(actual.source.gui, undefined, "the generic source must not advertise an unbuilt GUI")
assert.equal(actual.artifact.gui, pathToFileURL(resolve(artifact, "runtime/adapters/opencode_workflow/dist/gui-experimental.cjs")).href)
