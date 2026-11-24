import assert from "node:assert/strict"
import { test } from "node:test"
import { createSkillAdvisor } from "../../src/skill-advice.js"

const on = { state: "on", agent_merge: false }
function advisor(settings = on, extra = {}) {
  const calls = { settings: 0 }
  const value = createSkillAdvisor({
    settings: async () => { calls.settings++; if (settings instanceof Error) throw settings; return settings },
    onboarded: async () => { calls.onboarded = (calls.onboarded ?? 0) + 1; return extra.isOnboarded ?? true }, ...extra,
  })
  return { ...value, calls }
}

test("disabled, not onboarded, unreadable or failing settings add no skill note and never throw", async () => {
  for (const settings of [{ state: "off", agent_merge: false }, { state: "not_onboarded", agent_merge: null },
    { state: "blocked", reason: "project_settings_invalid" }, new Error("boom"), null]) {
    assert.equal(await advisor(settings).skillNote("finishing-a-development-branch"), null)
    assert.equal(await advisor(settings).skillNote("using-git-worktrees"), null)
  }
})

const LEAD = "vibe-workflow: your human partner's declared integration choice, set during onboarding: push the branch " +
  "and open a pull request, following the vibe-pull-requests skill ([BLOCKED] draft, then [READY]). Skip the options " +
  "menu and do not ask; the skill's other steps still apply. "
const LAST = "An explicit request from your human partner in this session overrides this."
const WORKTREE = "vibe-workflow: your human partner's declared worktree preference, set during onboarding: always work " +
  "in a worktree, never in the primary checkout. Do not ask for consent. " + LAST
test("skillNote states the onboarding decision for the finishing and worktree skills only", async () => {
  const merges = advisor({ state: "on", agent_merge: true, worktree_cleanup: true })
  assert.equal(await merges.skillNote("superpowers:finishing-a-development-branch"), LEAD +
    "You may merge it yourself once it is [READY] and its evidence is current. " +
    "After a confirmed merge, follow the worktree cleanup steps in vibe-pull-requests. " + LAST)
  const user = advisor({ state: "on", agent_merge: false, worktree_cleanup: null })
  assert.equal(await user.skillNote("finishing-a-development-branch"), LEAD +
    "Stop at [READY] and tell your human partner it awaits their merge. Keep the worktree after the merge. " + LAST)
  assert.equal(await merges.skillNote("superpowers:using-git-worktrees"), WORKTREE)
  assert.equal(await user.skillNote("using-git-worktrees"), WORKTREE)
  const { skillNote, calls } = advisor()
  for (const skill of ["brainstorming-old", "finishing-a-development-branch-old", "using-git-worktrees-x", undefined, 7]) {
    assert.equal(await skillNote(skill), null)
  }
  assert.equal(calls.settings, 0)
})

const PLAN_STEP = "After your self-review and before you ask for theirs, unless the plan's risk is reversible, dispatch one " +
  "fresh reviewer, once, with the spec and plan paths only. Ask it for, ranked: spec requirements with no task, misordered " +
  "tasks, failure modes no test covers (for statistics, numerics or calibration, a missing known-answer or reference-value test), and steps a cheap implementer could misread; \"nothing important\" is a valid " +
  "answer. Set its model as planning work (level 3); without Smart Router, the top tier. Fix what it finds yourself, " +
  "without a second review; bring your human partner only what they must decide."
const PLAN = "vibe-workflow: your human partner pre-approved the execution method you recommend; treat it as already " +
  "supplied, name it, and do not ask which one to use. Still ask them to review the plan, and wait for that review before " +
  "implementing. Before you fill in each task's Files block, follow the vibe-project-layout skill: put every new file " +
  "and test where the project's recorded layout says. If a file belongs elsewhere or a folder needs splitting, propose it " +
  "as its own task for your human partner to approve, never as a silent move. " + PLAN_STEP + " " + LAST
const DESIGN = "vibe-workflow: your human partner asks that the design check match the stakes. Run the builder check for a " +
  "project, and also for a small change where a mistake could lose real money, expose credentials, break production or " +
  "users' data, or cannot be undone. Other small changes skip it, as the skill says. Add one request to its prompt: " +
  "\"Then list up to five flaws in the approach that would cost most: a wrong choice, a gap, an unhandled risk. No " +
  "rewrites.\" Give the reviewer the document path only, for one pass, and sort its flaws into the same three piles. " +
  "Set its model as planning work (level 3); without Smart Router, the top tier. " + LAST
const MODEL_NAME = /haiku|opus|sonnet|fable|astra|terra|luna|gemini/i

test("skillNote pre-approves the recommended execution method in any onboarded project", async () => {
  assert.doesNotMatch(PLAN, MODEL_NAME)
  for (const settings of [on, { state: "off" }, { state: "not_onboarded" }, null]) {
    const { skillNote } = advisor(settings)
    assert.equal(await skillNote("superpowers:writing-plans"), PLAN)
    assert.equal(await skillNote("writing-plans"), PLAN)
  }
  const fresh = advisor(on, { isOnboarded: false })
  assert.equal(await fresh.skillNote("writing-plans"), null)
  const failing = advisor(on, { onboarded: async () => { throw new Error("boom") } })
  assert.equal(await failing.skillNote("writing-plans"), null)
  const { skillNote, calls } = advisor(on, { isOnboarded: false })
  for (const skill of ["writing-plans-old", "brainstorming-old", "toString", "__proto__"]) assert.equal(await skillNote(skill), null)
  assert.equal(calls.settings, 0)
  assert.equal(calls.onboarded, undefined)
})

test("skillNote sends the stakes-sized design review in any onboarded project", async () => {
  assert.doesNotMatch(DESIGN, MODEL_NAME)
  for (const settings of [on, { state: "off" }, { state: "not_onboarded" }, null]) {
    const { skillNote } = advisor(settings)
    assert.equal(await skillNote("superpowers:brainstorming"), DESIGN)
    assert.equal(await skillNote("brainstorming"), DESIGN)
  }
  const fresh = advisor(on, { isOnboarded: false })
  assert.equal(await fresh.skillNote("brainstorming"), null)
  const failing = advisor(on, { onboarded: async () => { throw new Error("boom") } })
  assert.equal(await failing.skillNote("brainstorming"), null)
})
