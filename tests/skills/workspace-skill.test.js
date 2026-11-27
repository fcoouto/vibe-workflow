import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { test } from "node:test"
import plugin from "../../index.js"

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

test("registers the workspace skill after onboarding with its bundled files", async () => {
  const skills = await registered()
  assert.deepEqual(skills.map(({ id, name }) => [id, name]), [
    ["vibe-onboard-project", "vibe-onboard-project"],
    ["vibe-prepare-my-workspace", "vibe-prepare-my-workspace"],
    ["vibe-pull-requests", "vibe-pull-requests"],
    ["vibe-council", "vibe-council"],
    ["vibe-project-layout", "vibe-project-layout"],
    ["vibe-security-review", "vibe-security-review"],
  ])
  const skill = skills[1]
  assert.match(skill.path, /skills\/vibe-prepare-my-workspace\/SKILL\.md$/)
  assert.ok(skill.description.length > 0 && !/\n/.test(skill.description))
  assert.ok(skill.content.includes("references/procedure.md"))
  assert.equal((await readFile(skill.path, "utf8")).includes(skill.content), true)
})

test("procedure keeps the approval, Python-first and backup rules", async () => {
  const [, skill] = await registered()
  const procedure = await readFile(new URL("./references/procedure.md", `file://${skill.path}`), "utf8")
  for (const text of ["python3 --version", "scripts/check.py", "approve", "workspace-backups", "needs-user"]) {
    assert.ok(procedure.includes(text), text)
  }
})
