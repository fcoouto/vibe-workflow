import assert from "node:assert/strict"
import test from "node:test"
import { DEFAULT_TITLE, cellsFor, compose, strip } from "../session-titles.js"

test("compose and strip", () => {
  assert.equal(compose("Fix auth", 2, true), "￻▰▰▱▱▱ WAITING · Fix auth")
  assert.equal(compose("Fix auth", 5, false), "￻▰▰▰▰▰ Fix auth")
  assert.equal(strip("￻▰▰▱▱▱ WAITING · Fix auth"), "Fix auth")
  assert.equal(strip("▰▱▱▱▱ Fix auth"), "Fix auth")
  assert.equal(strip("Fix auth"), "Fix auth")
})

test("cellsFor takes the highest fact that holds", () => {
  const base = { changed: [], declared: ["a.js", "b.js", "c.js"], pr: null, exists: true, cleanupOn: false, primary: false }
  assert.equal(cellsFor(base), 1)
  assert.equal(cellsFor({ ...base, changed: ["docs/x.md"] }), 1)
  assert.equal(cellsFor({ ...base, changed: ["z.js"] }), 2)
  assert.equal(cellsFor({ ...base, changed: ["a.js", "b.js"] }), 3)
  assert.equal(cellsFor({ ...base, changed: ["a.js", "b.js", "c.js"] }), 4)
  assert.equal(cellsFor({ ...base, declared: [], changed: ["z.js"] }), 2)
  assert.equal(cellsFor({ ...base, pr: { state: "OPEN", title: "[BLOCKED] x" } }), 4)
  assert.equal(cellsFor({ ...base, pr: { state: "MERGED", title: "x" } }), 5)
  assert.equal(cellsFor({ ...base, pr: { state: "MERGED", title: "x" }, cleanupOn: true }), 4)
  assert.equal(cellsFor({ ...base, pr: { state: "MERGED", title: "x" }, cleanupOn: true, exists: false }), 5)
  assert.equal(cellsFor({ ...base, pr: { state: "MERGED", title: "x" }, cleanupOn: true, primary: true }), 5)
  assert.equal(cellsFor({ ...base, pr: { state: "CLOSED", title: "[SUPERSEDED] x" }, cleanupOn: true }), 5)
  assert.equal(cellsFor({ ...base, pr: { state: "CLOSED", title: "x" } }), 4)
})

test("DEFAULT_TITLE matches generated titles only", () => {
  assert.match("New session - 2026-11-20T10:11:12.345Z", DEFAULT_TITLE)
  assert.doesNotMatch("New session - later", DEFAULT_TITLE)
})
