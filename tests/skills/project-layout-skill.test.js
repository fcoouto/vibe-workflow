import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { test } from "node:test"

const root = new URL("../../skills/vibe-project-layout/", import.meta.url)
const read = (name) => readFile(new URL(name, root), "utf8")
const files = ["SKILL.md", "references/procedure.md", "references/layouts.md"]
const hasAll = (text, phrases) => {
  for (const phrase of phrases) assert.ok(text.includes(phrase), `missing: ${phrase}`)
}

test("skill frontmatter matches the registration pattern and links both references", async () => {
  const text = await read("SKILL.md")
  assert.match(text, /^---\nname: vibe-project-layout\ndescription: [^\n]+\n---\n[\s\S]+/)
  hasAll(text, ["references/procedure.md", "references/layouts.md"])
})

test("procedure carries the placement, restructure and recording rules", async () => {
  hasAll(await read("references/procedure.md"), [
    "Placement order", "recorded layout", "nearest comparable", "has its own layout", "git mv", "safe-refactor",
    "source-to-destination map", "approved", "AGENTS.md", "about ten lines", "never a file listing", "dead files",
  ])
})

test("procedure offers to record a missing layout once", async () => {
  hasAll(await read("references/procedure.md"), ["offer once, at the end of the task, to record"])
})

test("procedure treats a direct request for one move as its own approval", async () => {
  hasAll(await read("references/procedure.md"), ["asks for directly needs no further approval"])
})

test("procedure moves files itself when safe-refactor is not installed", async () => {
  hasAll(await read("references/procedure.md"), ["`safe-refactor` skill when it is installed;", "otherwise make them yourself"])
})

test("procedure uses the examples only when the earlier steps are silent", async () => {
  hasAll(await read("references/procedure.md"), ["only when 1 to 3 are silent: there, group by module"])
})

test("procedure records the layout under a fixed heading and asks before creating a file", async () => {
  hasAll(await read("references/procedure.md"), ["`## Project layout` heading", "has no agent instructions file, ask before creating one."])
})

test("procedure raises folder hints as one proposal at the end of the task", async () => {
  hasAll(await read("references/procedure.md"), ["proposal at the end of the task"])
})

test("every example tree has its own heading marked as an example", async () => {
  const text = await read("references/layouts.md")
  const trees = (text.match(/^```/gm) || []).length / 2
  assert.equal((text.match(/^## .*\(example, not rule\)$/gm) || []).length, trees)
})

test("the monorepo and plugin examples carry their placement decisions", async () => {
  hasAll(await read("references/layouts.md"), [
    "A new test goes in its package's `tests/`", "A shipped asset goes inside the package that ships it",
    "assets/         # shipped non-code files",
  ])
})

test("no skill file names writing-plans or carries a machine path", async () => {
  for (const name of files) {
    const text = await read(name)
    assert.ok(!text.includes("writing-plans"), `${name}: writing-plans`)
    assert.ok(!/\/Users\/|\/home\//.test(text), `${name}: machine path`)
  }
})

test("layouts has at most four example trees and the convention-owned folders", async () => {
  const text = await read("references/layouts.md")
  const trees = (text.match(/^```/gm) || []).length / 2
  assert.ok(trees >= 1 && trees <= 4, `trees: ${trees}`)
  hasAll(text, ["app/", "tests/", "skills/<id>/SKILL.md"])
})
