import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { test } from "node:test"

const root = new URL("../../skills/vibe-security-review/", import.meta.url)
const read = (name) => readFile(new URL(name, root), "utf8")
const hasAll = (text, phrases) => {
  for (const phrase of phrases) assert.ok(text.includes(phrase), `missing: ${phrase}`)
}

test("skill frontmatter matches the registration pattern and links every reference", async () => {
  const text = await read("SKILL.md")
  assert.match(text, /^---\nname: vibe-security-review\ndescription: [^\n]+\n---\n[\s\S]+/)
  for (const ref of ["ship-check.md", "full-review.md", "reviewer-brief.md", "report-storage.md"]) assert.ok(text.includes(`references/${ref}`), ref)
})
test("ship check carries the tiers, rules and PR section", async () => {
  const text = await read("references/ship-check.md")
  hasAll(text, ["Tier 0", "Tier 1", "Tier 2", "Tier 3", "merge-base", "untracked", "never replaces",
    "take the higher one", "Removing a check", "AGENTS.md", "SKILL.md", "## Security", ".github/pull_request_template.md",
    "git for-each-ref", "--all", "--name-only", "gitleaks", "approves that exact run", "reviewer-brief.md", "report-storage.md"])
  hasAll(text.replace(/\s+/g, " "), [
    "A confirmed high or critical finding that this change introduced or made worse keeps the pull request [BLOCKED] until it is fixed or the user explicitly accepts it.",
    "A pre-existing finding is reported to the user and does not block an unrelated pull request.",
    "If a required tier-3 review could not run, the pull request stays [BLOCKED] and says so."])
})
test("reviewer brief carries the read-only, routing and redaction rules", async () => {
  hasAll(await read("references/reviewer-brief.md"), ['agent: "explore"', "smart_router_select", "reviewing", "top tier",
    "counterevidence", "confirmed", "likely", "hardening", "pre-existing", "file, line and type", "tool output",
    "real credentials", "live services", "differential-review", "adversarial.md", "fp-check", "exploit code",
    "available skills", "approved that exact run", "installed is not approval", "cannot load skills",
    "Base directory for this skill:", "{baseDir}", "read tool"])
})
test("full review states its bound, areas, verification and output", async () => {
  hasAll(await read("references/full-review.md"), ["bound", "supply chain", "AI-agent", "refute", "sibling",
    "report-storage.md", "reviewer-brief.md", "No fixes", "supply-chain-risk-auditor", "agentic-actions-auditor", "sharp-edges",
    "Semgrep", "CodeQL", "approves that exact run"])
})
test("report storage keeps reports private and outside the task worktree", async () => {
  hasAll(await read("references/report-storage.md"), [".vibe-workflow/security-reports/", "git worktree list --porcelain",
    "check-ignore -q", "exactly `*`", "rewrite", "chat", "0700", "0600", "git add -f", "never overwritten", "git clean -x",
    "not a Git repository", "symlink", "git ls-files", "-2"])
})
test("no skill file copies Trail of Bits text or claims enforcement", async () => {
  for (const name of ["SKILL.md", ...["ship-check", "full-review", "reviewer-brief", "report-storage"].map((n) => `references/${n}.md`)]) {
    const text = await read(name)
    for (const copied of ["Heartbleed was 2 lines", "pit of success", "Rationalizations"]) assert.ok(!text.includes(copied), `${name}: ${copied}`)
    assert.doesNotMatch(text.replaceAll("not enforced", ""), /enforce/i, name)
    assert.doesNotMatch(text, /superpowers/i, name)
  }
})
