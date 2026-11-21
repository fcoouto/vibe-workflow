import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { test } from "node:test"

const root = new URL("../skills/vibe-council/", import.meta.url)
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
    "shasum -a 256", "openai/gpt-6.2-astra", "claude-agent/opus", "claude-agent/claude-fable-5-2",
    "no vote", "did not vote", "Round 2", "worktree", "paid overflow", "call `vibe_council_roster` again",
  ])
})

test("brief template carries the member rules", async () => {
  hasAll(await read("references/brief-template.md"), [
    "Answer independently", "optimistic", "KEEP | CHANGE", "Compounding", "Top 3",
    "500 words", "Forbidden paths", "15 tool calls",
  ])
})

test("record template carries the record fields", async () => {
  hasAll(await read("references/record-template.md"), ["SHA-256", "paid overflow", "User decision"])
})
