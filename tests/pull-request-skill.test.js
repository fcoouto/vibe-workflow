import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { test } from "node:test"
import plugin from "../index.js"

const root = new URL("../skills/vibe-pull-requests/", import.meta.url)
const read = (name) => readFile(new URL(name, root), "utf8")

async function registered() {
  const skills = []
  await plugin.setup({
    location: { directory: process.cwd() }, options: {},
    tool: { transform: async (apply) => apply({ add: () => {} }) },
    skill: { transform: async (apply) => apply({ add: (entry) => skills.push(entry) }) },
    session: { hook: async () => {} },
  })
  return skills
}

test("registers the pull request skill third, with its procedure linked", async () => {
  const skills = await registered()
  assert.deepEqual(skills.map(({ id }) => id), ["vibe-onboard-project", "vibe-prepare-my-workspace", "vibe-pull-requests"])
  const skill = skills[2]
  assert.match(skill.path, /skills\/vibe-pull-requests\/SKILL\.md$/)
  assert.ok(skill.description.length > 0 && !/\n/.test(skill.description))
  assert.ok(skill.content.includes("references/procedure.md"))
})

test("procedure states the lifecycle, readiness, merge and checkout rules", async () => {
  const text = await read("references/procedure.md")
  for (const phrase of ["[BLOCKED]", "[READY]", "[SUPERSEDED]", "--undo", "git fetch origin main", "--ff-only",
    "agent_merge", ".github/pull_request_template.md", ".vibe-workflow/settings.json", "worktree",
    '"feature":"pull_requests"', "previous_agent_merge", "observed on this head"]) {
    assert.ok(text.includes(phrase), phrase)
  }
})

test("template has the five sections", async () => {
  const text = await read("references/pull-request-template.md")
  for (const heading of ["Why", "What changes", "Checks", "Review", "Readiness"]) {
    assert.match(text, new RegExp(`^## ${heading}$`, "m"), heading)
  }
})

test("skill files never point to Superpowers or claim enforcement", async () => {
  for (const name of ["SKILL.md", "references/procedure.md", "references/pull-request-template.md"]) {
    const text = await read(name)
    assert.doesNotMatch(text, /superpowers/i, name)
    assert.doesNotMatch(text.replaceAll("not enforced", ""), /enforce/i, name)
  }
})
