import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { test } from "node:test"

const root = new URL("../../skills/vibe-council/", import.meta.url)
const read = (name) => readFile(new URL(name, root), "utf8")
const hasAll = (text, phrases) => {
  for (const phrase of phrases) assert.ok(text.includes(phrase), `missing: ${phrase}`)
}

test("skill frontmatter matches the registration pattern and links the procedure", async () => {
  const text = await read("SKILL.md")
  assert.match(text, /^---\nname: vibe-council\ndescription: [^\n]+\n---\n[\s\S]+/)
  assert.ok(text.includes("references/procedure.md"))
})

test("procedure carries the process, roster and failure rules", async () => {
  hasAll(await read("references/procedure.md"), [
    "vibe_council_roster", 'agent: "explore"', "#<variant>", "docs/vibe-council/sessions/",
    "shasum -a 256", '{"v":1}', '"count"', "fewer_seats", "Scores: Artificial Analysis via OpenRouter",
    "no vote", "did not vote", "Round 2", "worktree", "paid overflow", "call `vibe_council_roster` again",
    "| Seat | Model ID | Name | Variant | Paid overflow | Lab | Score |",
  ])
})

test("the README says councils keep the scores fresh", async () => {
  const readme = await readFile(new URL("../../README.md", import.meta.url), "utf8")
  const section = readme.slice(readme.indexOf("## Ask the council"), readme.indexOf("## Scores"))
  assert.match(section.replace(/\s+/g, " "), /refresh(es)? them in the background about weekly/)
})

test("no council file hard-sets a seat", async () => {
  for (const name of ["SKILL.md", "references/procedure.md", "references/brief-template.md", "references/record-template.md"]) {
    const text = await read(name)
    for (const id of ["gpt-6-astra", "claude-agent/opus", "claude-fable-5-2"]) assert.ok(!text.includes(id), `${name}: ${id}`)
  }
  const readme = await readFile(new URL("../../README.md", import.meta.url), "utf8")
  for (const id of ["gpt-6-astra", "claude-agent/opus", "claude-fable-5-2"]) assert.ok(!readme.includes(id), `README.md: ${id}`)
})

test("brief template carries the member rules", async () => {
  hasAll(await read("references/brief-template.md"), [
    "Answer independently", "optimistic", "KEEP | CHANGE", "Compounding", "Top 3",
    "500 words", "Forbidden paths", "15 tool calls",
  ])
})

test("record template carries the record fields", async () => {
  hasAll(await read("references/record-template.md"), ["SHA-256", "paid overflow", "User decision",
    "| Seat | Model ID | Name | Variant | Paid overflow | Lab | Score |", "Scores: Artificial Analysis via OpenRouter"])
})
