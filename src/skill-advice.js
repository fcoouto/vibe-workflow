// Notes sent when a skill loads in an onboarded project. The hook that delivers them is in advice.js.
const OVERRIDE = "An explicit request from your human partner in this session overrides this."
const PLAN_REVIEW = "After your self-review and before you ask for theirs, unless the plan's risk is reversible, dispatch one " +
  "fresh reviewer, once, with the spec and plan paths only. Ask it for, ranked: spec requirements with no task, misordered " +
  "tasks, failure modes no test covers (for statistics, numerics or calibration, a missing known-answer or reference-value test), and steps a cheap implementer could misread; \"nothing important\" is a valid " +
  "answer. Set its model as planning work (level 3); without Smart Router, the top tier. Fix what it finds yourself, " +
  "without a second review; bring your human partner only what they must decide."
const DESIGN_REVIEW = "vibe-workflow: your human partner asks that the design check match the stakes. Run the builder check " +
  "for a project, and also for a small change where a mistake could lose real money, expose credentials, break production " +
  "or users' data, or cannot be undone. Other small changes skip it, as the skill says. Add one request to its prompt: " +
  "\"Then list up to five flaws in the approach that would cost most: a wrong choice, a gap, an unhandled risk. No " +
  "rewrites.\" Give the reviewer the document path only, for one pass, and sort its flaws into the same three piles. " +
  "Set its model as planning work (level 3); without Smart Router, the top tier. " + OVERRIDE
const SKILL_NOTES = {
  brainstorming: { gate: "onboarded", note: () => DESIGN_REVIEW },
  "writing-plans": { gate: "onboarded", note: () => "vibe-workflow: your human partner pre-approved the execution " +
    "method you recommend; treat it as already supplied, name it, and do not ask which one to use. Still ask them to " +
    "review the plan, and wait for that review before implementing. Before you fill in each task's Files block, " +
    "follow the vibe-project-layout skill: put every new file and test where the project's recorded layout says. If a " +
    "file belongs elsewhere or a folder needs splitting, propose it as its own task for your human partner to " +
    "approve, never as a silent move. " + PLAN_REVIEW + " " + OVERRIDE },
  "finishing-a-development-branch": { gate: "git_workflow", note: (state) => "vibe-workflow: your human partner's declared integration choice, set " +
    "during onboarding: push the branch and open a pull request, following the vibe-pull-requests skill ([BLOCKED] " +
    "draft, then [READY]). Skip the options menu and do not ask; the skill's other steps still apply. " +
    (state.agent_merge === true ? "You may merge it yourself once it is [READY] and its evidence is current. "
      : "Stop at [READY] and tell your human partner it awaits their merge. ") +
    (state.worktree_cleanup === true ? "After a confirmed merge, follow the worktree cleanup steps in vibe-pull-requests. "
      : "Keep the worktree after the merge. ") + OVERRIDE },
  "using-git-worktrees": { gate: "git_workflow", note: () => "vibe-workflow: your human partner's declared worktree preference, set during " +
    "onboarding: always work in a worktree, never in the primary checkout. Do not ask for consent. " + OVERRIDE },
}
const SHIP_NOTES = {
  "requesting-code-review": "vibe-workflow: your human partner asks for a security check sized to the change. Before you " +
    "dispatch the code reviewer, follow the vibe-security-review skill's ship check: pick the change's tier, add its " +
    "security section to the reviewer's brief at tier 2, and dispatch its security reviewer at tier 3. " + OVERRIDE,
  "subagent-driven-development": "vibe-workflow: your human partner asks for a security check sized to the change. At the final " +
    "whole-branch review, follow the vibe-security-review skill's ship check once for the whole branch, not per task. " + OVERRIDE,
  "finishing-a-development-branch": "vibe-workflow: before you open or ready the pull request, confirm the vibe-security-review ship " +
    "check covers the current head and its Security section is in the body; run it if not. " + OVERRIDE,
}

export function createSkillAdvisor({ settings, onboarded = async () => false, sweep = null }) {
  return {
    async skillNote(skill) {
      if (typeof skill !== "string") return null
      const key = skill.split(/[:/]/).pop()
      const entry = Object.hasOwn(SKILL_NOTES, key) && SKILL_NOTES[key]
      let note = null, ship = null
      try {
        if (entry?.gate === "onboarded") {
          if (await onboarded() === true) note = entry.note()
        } else if (entry) {
          const state = await settings()
          if (state?.state === "on") note = entry.note(state)
          if (key === "using-git-worktrees" && state?.state === "on" && state.worktree_cleanup === true && sweep) {
            try { const line = await sweep.note({ exclude: [] }); if (line) note += "\n" + line } catch {}
          }
        }
      } catch {}
      try {
        if (Object.hasOwn(SHIP_NOTES, key) && await onboarded() === true) ship = SHIP_NOTES[key]
      } catch {}
      return [note, ship].filter(Boolean).join("\n") || null
    },
  }
}
