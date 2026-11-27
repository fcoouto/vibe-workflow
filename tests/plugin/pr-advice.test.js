import assert from "node:assert/strict"
import { test } from "node:test"
import { classify, createAdvisor } from "../../src/pr-advice.js"

const on = { state: "on", agent_merge: false }
const SHIP_PR = "vibe-workflow: before this pull request is reviewed or merged, its Security section must cover the " +
  "current head (vibe-security-review ship check). If it is missing or stale, run the check now; if a blocking finding " +
  "is open, keep or return the pull request to draft, titled [BLOCKED] when the git workflow is on."
function advisor(settings = on, extra = {}) {
  const calls = { settings: 0 }
  const value = createAdvisor({
    settings: async () => { calls.settings++; if (settings instanceof Error) throw settings; return settings },
    primary: async () => "/repo", checkout: async () => null, ...extra,
  })
  const advise = (command, sessionID = "ses_a", more = {}) =>
    value.advise({ tool: "bash", command, ok: true, directory: "/repo", ...more }, sessionID)
  return { ...value, advise, calls }
}

test("classify finds commands at command position only", () => {
  const kinds = (command) => classify(command).map(({ kind }) => kind)
  assert.deepEqual(classify('FOO=1 rtk gh pr create --draft --title "[BLOCKED] x"'),
    [{ kind: "pr_create", draft: true, title: "[BLOCKED] x" }])
  assert.deepEqual(classify('cd a && gh pr create -d -t "[BLOCKED] a"'), [{ kind: "pr_create", draft: true, title: "[BLOCKED] a" }])
  assert.deepEqual(classify('gh pr create --title="[BLOCKED] a"'), [{ kind: "pr_create", draft: false, title: "[BLOCKED] a" }])
  assert.deepEqual(classify("gh pr create --fill"), [{ kind: "pr_create", draft: false, title: null }])
  assert.deepEqual(classify("gh pr ready 3"), [{ kind: "pr_ready", undo: false }])
  assert.deepEqual(classify("gh pr ready --undo"), [{ kind: "pr_ready", undo: true }])
  assert.deepEqual(classify('gh pr edit 3 --title "[READY] a"'), [{ kind: "pr_retitle", title: "[READY] a" }])
  assert.deepEqual(classify("gh pr edit 3 --add-label x"), [])
  assert.deepEqual(classify("gh pr merge 3 --auto"), [{ kind: "pr_merge", auto: true }])
  assert.deepEqual(kinds("git push origin x"), ["push"])
  assert.deepEqual(kinds("git push --dry-run"), [])
  assert.deepEqual(kinds("git switch -c x"), ["checkout"])
  assert.deepEqual(kinds("git branch -m new"), ["checkout"])
  assert.deepEqual(kinds('git commit -m "fix gh pr merge"'), [])
  assert.deepEqual(kinds('echo "gh pr create"'), [])
  assert.deepEqual(kinds("echo gh pr create"), [])
})

test("classify handles newlines, heredocs, quoted bodies and prefixes", () => {
  assert.deepEqual(classify("ls\ngh pr create --draft"), [{ kind: "pr_create", draft: true, title: null }])
  const heredoc = `gh pr create --draft --title "[BLOCKED] a" --body "$(cat <<'EOF'\ngh pr merge 1\n) stray paren\nEOF\n)"`
  assert.deepEqual(classify(heredoc), [{ kind: "pr_create", draft: true, title: "[BLOCKED] a" }])
  assert.deepEqual(classify('gh pr create --title "[BLOCKED] a" --body "uses --draft here"'),
    [{ kind: "pr_create", draft: false, title: "[BLOCKED] a" }])
  assert.deepEqual(classify('FOO="a b" gh pr merge 2'), [{ kind: "pr_merge", auto: false }])
  assert.deepEqual(classify("git -C /elsewhere checkout -b x"), [{ kind: "checkout", dir: "/elsewhere", moved: false }])
  assert.deepEqual(classify("cd d && git switch x"), [{ kind: "checkout", dir: null, moved: true }])
  assert.deepEqual(classify("cat <<EOF\ngh pr merge 3\nEOF\ngh pr ready"), [{ kind: "pr_ready", undo: false }])
})

test("case 1 fires with a fix for each missing piece", async () => {
  const { advise } = advisor()
  const good = await advise('gh pr create --draft --title "[BLOCKED] a"')
  assert.match(good, /^vibe-workflow:/)
  assert.match(good, /\[BLOCKED\]/)
  assert.doesNotMatch(good, /gh pr ready --undo|gh pr edit --title/)
  assert.match(await advise('gh pr create --title "[BLOCKED] a"'), /gh pr ready --undo/)
  assert.match(await advise('gh pr create --draft --title "feat: a"'), /gh pr edit --title "\[BLOCKED\] <title>"/)
  const fill = await advise("gh pr create --draft --fill")
  assert.doesNotMatch(fill, /gh pr edit --title/)
})

test("onboarded PR ship guidance is independent of git workflow", async () => {
  const onboarded = async () => true
  const command = 'gh pr create --draft --title "[BLOCKED] x"'
  const existing = await advisor().advise(command)
  assert.equal(await advisor(on, { onboarded }).advise(command), `${existing}\n${SHIP_PR}`)
  const { advise } = advisor({ state: "off" }, { onboarded })
  for (const cmd of ["gh pr create", "gh pr ready 3", 'gh pr edit 3 --title "[READY] x"']) {
    assert.equal(await advise(cmd), SHIP_PR)
  }
  for (const cmd of ["gh pr ready 3 --undo", "git push"]) assert.equal(await advise(cmd), null)
  assert.equal(await advise("gh pr create", "ses_a", { ok: false }), null)
  assert.equal(await advisor({ state: "off" }).advise(command), null)
  const throwing = async () => { throw new Error("boom") }
  assert.equal(await advisor(on, { onboarded: throwing }).advise(command), existing)
  assert.equal(await advisor({ state: "off" }, { onboarded: throwing }).advise(command), null)
  assert.equal(await advise("gh pr create && gh pr ready 3"), SHIP_PR)
})

test("case 2 is armed by cases 1 and 3, fires once on the next push, then disarms, per session", async () => {
  const { advise } = advisor()
  assert.equal(await advise("git push origin x"), null)
  await advise('gh pr create --draft --title "[BLOCKED] a"')
  assert.equal(await advise("git push origin x", "ses_b"), null)
  assert.match(await advise("git push origin x"), /\[BLOCKED\] draft/)
  assert.equal(await advise("git push origin x"), null)
  await advise("gh pr ready 3")
  assert.match(await advise("git push"), /gh pr ready --undo/)
  await advise("gh pr ready 3")
  await advise("gh pr ready --undo")
  assert.equal(await advise("git push"), null)
  await advise('gh pr edit 3 --title "[READY] a"')
  await advise('gh pr edit 3 --title "[BLOCKED] a"')
  assert.equal(await advise("git push"), null)
})

test("case 3 wording follows agent_merge", async () => {
  const user = await advisor({ state: "on", agent_merge: false }).advise("gh pr ready 3")
  assert.match(user, /awaits their merge/)
  const agent = await advisor({ state: "on", agent_merge: true }).advise('gh pr edit 3 --title "[READY] a"')
  assert.match(agent, /evidence cover the current head/)
})

test("disabled, not onboarded, unreadable or failing settings add nothing and never throw", async () => {
  for (const settings of [{ state: "off", agent_merge: false }, { state: "not_onboarded", agent_merge: null },
    { state: "blocked", reason: "project_settings_invalid" }, new Error("boom"), null]) {
    assert.equal(await advisor(settings).advise('gh pr create --title "x"'), null)
  }
})

test("settings are never read for a non-matching command, and failed or other-tool calls add nothing", async () => {
  const { advise, calls } = advisor()
  assert.equal(await advise("ls -la"), null)
  assert.equal(await advise('git commit -m "gh pr merge"'), null)
  assert.equal(calls.settings, 0)
  assert.equal(await advise("gh pr create --draft", "ses_a", { ok: false }), null)
  assert.equal(await advise("gh pr create --draft", "ses_a", { tool: "read" }), null)
  assert.equal(calls.settings, 0)
})

test("a missing sessionID skips case 2 only, and per-session state is bounded and clearable", async () => {
  const { advise, clear } = advisor()
  assert.match(await advise("gh pr ready 3", null), /vibe-workflow:/)
  assert.equal(await advise("git push", null), null)
  for (let i = 0; i < 260; i++) await advise("gh pr ready 3", `ses_${i}`)
  assert.equal(await advise("git push", "ses_0"), null)
  assert.match(await advise("git push", "ses_259"), /vibe-workflow:/)
  clear()
  assert.equal(await advise("git push", "ses_259"), null)
})

test("case 4 names the primary path and the fast-forward commands", async () => {
  const note = await advisor().advise("gh pr merge 3 --squash")
  assert.match(note, /\/repo/)
  assert.match(note, /git fetch origin main/)
  assert.match(note, /git merge --ff-only origin\/main/)
  assert.match(note, /chose to merge pull requests themselves/)
  assert.doesNotMatch(await advisor({ state: "on", agent_merge: true }).advise("gh pr merge 3"), /chose to merge/)
})

test("case 4 with auto-merge waits for completion, and a missing primary still advises", async () => {
  assert.match(await advisor().advise("gh pr merge 3 --auto"), /auto-merge completes/)
  const note = await advisor(on, { primary: async () => null }).advise("gh pr merge 3")
  assert.match(note, /git merge --ff-only origin\/main/)
})

test("case 5 names the branch once, stays silent on main, another repository, failure and cd", async () => {
  const seen = []
  let info = { top: "/repo", branch: "feat/x" }
  const { advise } = advisor(on, { checkout: async (dir) => { seen.push(dir); return info } })
  const note = await advise("git switch -c feat/x")
  assert.match(note, /feat\/x/)
  assert.match(note, /worktree/)
  assert.match(note, /Never touch the primary checkout; tell the user/)
  assert.equal(await advise("git checkout feat/x"), null)
  info = { top: "/repo", branch: "other" }
  assert.match(await advise("git checkout other"), /other/)
  info = { top: "/repo", branch: "main" }
  assert.equal(await advise("git checkout main"), null)
  info = { top: "/elsewhere", branch: "feat/y" }
  assert.equal(await advise("git checkout -b feat/y"), null)
  info = null
  assert.equal(await advise("git checkout -b feat/z"), null)
  info = { top: "/repo", branch: "feat/q" }
  assert.equal(await advise("cd /repo && git checkout -b feat/q"), null)
  assert.equal(await advise("git checkout -b feat/q", null), null)
  assert.deepEqual(seen.slice(0, 2), ["/repo", "/repo"])
  assert.equal(await advise("git -C /repo/sub checkout -b feat/q", "ses_c") === null, false)
  assert.equal(seen.at(-1), "/repo/sub")
})

test("a detached primary checkout is reported", async () => {
  const { advise } = advisor(on, { checkout: async () => ({ top: "/repo", branch: "HEAD" }) })
  assert.match(await advise("git checkout abc123"), /detached/)
})

test("case 4 adds the cleanup sentence only when worktree_cleanup is true", async () => {
  const sentence = /Cleanup is on: once the merge is confirmed, follow the worktree cleanup steps in the vibe-pull-requests skill \(detach, safe-delete the branch, remove the worktree last\)\./
  assert.match(await advisor({ state: "on", agent_merge: true, worktree_cleanup: true }).advise("gh pr merge 3"), sentence)
  for (const worktree_cleanup of [false, null, undefined]) {
    assert.doesNotMatch(await advisor({ state: "on", agent_merge: true, worktree_cleanup }).advise("gh pr merge 3"), /Cleanup is on/)
  }
  assert.equal(await advisor({ state: "off", agent_merge: true, worktree_cleanup: true }).advise("gh pr merge 3"), null)
  assert.equal(await advisor({ state: "not_onboarded", agent_merge: null, worktree_cleanup: true }).advise("gh pr merge 3"), null)
})

test("case 4 with auto waits for the auto-merge to complete before cleanup", async () => {
  const note = await advisor({ state: "on", agent_merge: true, worktree_cleanup: true }).advise("gh pr merge 3 --auto")
  assert.match(note, /Cleanup is on: once the auto-merge completes, follow the worktree cleanup steps/)
  assert.doesNotMatch(note, /once the merge is confirmed, follow/)
})

test("the user-merges and cleanup sentences appear together", async () => {
  const note = await advisor({ state: "on", agent_merge: false, worktree_cleanup: true }).advise("gh pr merge 3")
  assert.match(note, /chose to merge pull requests themselves/)
  assert.match(note, /Cleanup is on/)
})
