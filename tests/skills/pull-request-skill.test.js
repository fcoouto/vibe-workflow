import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { test } from "node:test"
import plugin from "../../index.js"

const root = new URL("../../skills/vibe-pull-requests/", import.meta.url)
const read = (name) => readFile(new URL(name, root), "utf8")
// The `-D` flag itself, not the `-D` inside the `<YYYY-MM-DD>` placeholder.
const FLAG_D = /(?<![\w-])-D\b/

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
  assert.deepEqual(skills.map(({ id }) => id), ["vibe-onboard-project", "vibe-prepare-my-workspace", "vibe-pull-requests", "vibe-council", "vibe-project-layout", "vibe-security-review"])
  const skill = skills[2]
  assert.match(skill.path, /skills\/vibe-pull-requests\/SKILL\.md$/)
  assert.ok(skill.description.length > 0 && !/\n/.test(skill.description))
  assert.ok(skill.content.includes("references/procedure.md"))
})

test("procedure states the lifecycle, readiness, merge and checkout rules", async () => {
  const text = await read("references/procedure.md")
  for (const phrase of ["[BLOCKED]", "[READY]", "[SUPERSEDED]", "--undo", "git fetch origin main", "--ff-only",
    "agent_merge", ".github/pull_request_template.md", ".vibe-workflow/settings.json", "worktree",
    '{"v":1,"op":"inspect","feature":"git_workflow"}', "agent_merge", "observed on this head", "Never touch the primary checkout", "## Finishing a branch",
    "do not offer other ways to integrate it", "declared choice from onboarding", ".vibe-workflow/security-reports/",
    "vibe-security-review", "Security section"]) {
    assert.ok(text.includes(phrase), phrase)
  }
})

test("template has the six sections", async () => {
  const text = await read("references/pull-request-template.md")
  for (const heading of ["Why", "What changes", "Checks", "Review", "Security", "Readiness"]) {
    assert.match(text, new RegExp(`^## ${heading}$`, "m"), heading)
  }
  assert.ok(text.indexOf("## Review") < text.indexOf("## Security"))
  assert.ok(text.indexOf("## Security") < text.indexOf("## Readiness"))
})

test("skill files never point to Superpowers or claim enforcement", async () => {
  for (const name of ["SKILL.md", "references/procedure.md", "references/pull-request-template.md",
    "references/worktree-cleanup.md"]) {
    const text = await read(name)
    assert.doesNotMatch(text, /superpowers/i, name)
    assert.doesNotMatch(text.replaceAll("not enforced", ""), /enforce/i, name)
  }
})

test("the cleanup reference moves the session before it removes the worktree", async () => {
  const text = await read("references/worktree-cleanup.md")
  for (const phrase of ["Move the session", "session_move", "ask before removing", "Remove the worktree", "separate, later tool call", "anything else counts as not confirmed", "before step 3 changes anything", "the removal tool itself makes the move"]) {
    assert.ok(text.includes(phrase), phrase)
  }
  assert.ok(text.indexOf("Move the session") < text.indexOf("Remove the worktree"))
})

test("the cleanup reference states the checks, the order, the mapping and the no-force rules", async () => {
  const text = await read("references/worktree-cleanup.md")
  for (const phrase of ['{"v":1,"op":"inspect","feature":"git_workflow"}', "worktree_cleanup", "gh pr view", "defaultBranchRef",
    "merge-base --is-ancestor", "git switch --detach", "--git-dir", "branch -d", "last action that changes anything",
    "git -C", "status --porcelain --ignored", "--path-format=absolute", "worktree list --porcelain", ".gitmodules", "origin/<default branch>", "worktree_remove", "ExitWorktree", "git worktree remove", "--delete-branch", "backstop"]) {
    assert.ok(text.includes(phrase), phrase)
  }
  for (const line of text.split("\n").filter((line) => FLAG_D.test(line))) assert.match(line, /never/i, line)
  const procedure = await read("references/procedure.md")
  assert.ok(procedure.includes("worktree-cleanup.md"))
  assert.match(procedure, /--delete-branch/)
})

test("the cleanup reference sorts leftovers, waits for the merge and sweeps older worktrees", async () => {
  const text = await read("references/worktree-cleanup.md")
  for (const phrase of ["node_modules/", "__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/", ".DS_Store",
    "!! build/", "last path component", "Nothing is deleted, moved or archived before the merge is confirmed",
    "Commit to this PR", "Archive", "Discard", "Keep the worktree", "list them again", "docs/vibe-workflow/council-sessions/",
    "result.md", "(Recommended)", ".vibe-workflow/archives/<YYYY-MM-DD>_<branch>/", "replaced by `-`", "scripts/archive.py",
    "python3 -I -B", "never by hand", "refused:", "Cleanup verified", "Cleanup blocked", "## Sweeping older merged worktrees",
    "gh pr list --head <branch> --state merged", "git -C <worktree> switch --detach", "worktree repair", "prunable"]) {
    assert.ok(text.includes(phrase), phrase)
  }
  for (const line of text.split("\n").filter((line) => /force/i.test(line))) assert.match(line, /never|without/i, line)
  for (const line of text.split("\n").filter((line) => FLAG_D.test(line))) assert.match(line, /never/i, line)
  const procedure = await read("references/procedure.md")
  for (const phrase of [".vibe-workflow/archives/", "leftover files"]) assert.ok(procedure.includes(phrase), phrase)
})
