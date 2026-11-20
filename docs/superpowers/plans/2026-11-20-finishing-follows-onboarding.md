# Finishing Follows Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the `skill` tool loads `finishing-a-development-branch` in a project whose git workflow is on, append a `vibe-workflow:` note to that tool result stating the user's onboarding decision, so the agent does not present the integration menu.

**Architecture:** `createAdvisor` in `pr-advice.js` gains `finish(skill)`, which builds the note from the `git_workflow` settings it already reads. `registerPrAdvice`'s existing `execute.after` handler routes completed `skill` events to it and appends the note with the existing `appendNote`. The `vibe-pull-requests` procedure gains a matching section.

**Tech Stack:** Node ES modules, `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-20-finishing-follows-onboarding-design.md`

## Global Constraints

- Note text, exactly: `vibe-workflow: your human partner already decided during onboarding how finished work is integrated in this project, so do not present the integration options or ask. Push the branch and open a pull request, following the vibe-pull-requests skill ([BLOCKED] draft, then [READY]). ` + merge + ` ` + cleanup.
  - merge: `agent_merge === true` → `Agents merge their own [READY] pull requests once the evidence is current.`; otherwise `Stop at [READY] and tell your human partner it awaits their merge.`
  - cleanup: `worktree_cleanup === true` → `After a confirmed merge, follow the worktree cleanup steps.`; otherwise `Keep the worktree after the merge.`
- Trigger: `event.tool === "skill"`, `event.status === "completed"`, and the skill id (`input.id`, else `input.name`) after its last `:` or `/` equals `finishing-a-development-branch`. Settings are read only for that trigger; only `state: "on"` adds a note.
- The note goes into the result's `content` through the existing `appendNote`; nothing else on the event changes. The hook never throws.
- Skill files (`skills/vibe-pull-requests/**`) never contain "superpowers" (any case) or "enforce" outside "not enforced" (pinned by `tests/pull-request-skill.test.js`).
- No new setting, question, dependency or exported file.

## Review Focus

- A `superpowers:`-prefixed id and a bare id both trigger; `finishing-a-development-branch-old` and `brainstorming` do not (Task 1).
- Off, not onboarded, blocked, throwing or null settings add nothing and never throw (Task 1).
- A failed (`status: "error"`) skill load adds nothing (Task 1).
- A skill event never reaches the shell-command path, and shell events keep their current notes (Task 1: existing tests unchanged).
- A string `result` and a `content` array both receive the note (Task 1).

---

### Task 1: The finishing note

**Files:**
- Modify: `pr-advice.js` (`createAdvisor`, `registerPrAdvice`), `skills/vibe-pull-requests/references/procedure.md`
- Test: `tests/pr-advice.test.js`, `tests/pr-hook.test.js`, `tests/pull-request-skill.test.js`

**Interfaces:**
- Produces: `createAdvisor(...).finish(skill: unknown) -> Promise<string | null>`.

- [ ] **Step 1: Write failing tests.**

  `tests/pr-advice.test.js`:

```js
const LEAD = "vibe-workflow: your human partner already decided during onboarding how finished work is integrated " +
  "in this project, so do not present the integration options or ask. Push the branch and open a pull request, " +
  "following the vibe-pull-requests skill ([BLOCKED] draft, then [READY]). "
test("finish states the onboarding decision for the finishing skill only", async () => {
  const merges = advisor({ state: "on", agent_merge: true, worktree_cleanup: true })
  assert.equal(await merges.finish("superpowers:finishing-a-development-branch"), LEAD +
    "Agents merge their own [READY] pull requests once the evidence is current. " +
    "After a confirmed merge, follow the worktree cleanup steps.")
  const user = advisor({ state: "on", agent_merge: false, worktree_cleanup: null })
  assert.equal(await user.finish("finishing-a-development-branch"), LEAD +
    "Stop at [READY] and tell your human partner it awaits their merge. Keep the worktree after the merge.")
  const { finish, calls } = advisor()
  for (const skill of ["brainstorming", "finishing-a-development-branch-old", undefined, 7]) assert.equal(await finish(skill), null)
  assert.equal(calls.settings, 0)
})
```

  Extend the existing "disabled, not onboarded, unreadable or failing settings" test with `finish("finishing-a-development-branch")` returning `null` for each settings value.

  `tests/pr-hook.test.js`, calling the registered handler directly with `{ tool: "skill", status, sessionID: "ses_1", input: { id }, result }`: a completed `superpowers:finishing-a-development-branch` load gets the note appended to a `content` array and to a string `result`; `status: "error"`, another skill id, and settings `{ enabled: false, agent_merge: false }` leave `result` deep-equal to before.

  `tests/pull-request-skill.test.js`: the procedure-phrases test also requires `## Finishing a branch` and `do not offer other ways to integrate it`.
- [ ] **Step 2: Run** `node --test tests/pr-advice.test.js tests/pr-hook.test.js tests/pull-request-skill.test.js`. Expected: the new assertions fail.
- [ ] **Step 3: Implement.** Add `finish` to `createAdvisor` (check the id first, then read settings; any error gives `null`). In `registerPrAdvice`'s handler, before the `input.command` check, send completed `skill` events to `advisor.finish(input?.id ?? input?.name)`, append a non-null note, and return. Add to the procedure, after "Merging":

```markdown
## Finishing a branch

When the git workflow is on, the user already chose during onboarding how finished
work is integrated: push the branch, open a pull request through the lifecycle above,
merge as "Who may merge" says, and clean up only if the project chose it. When the
work is done, follow that and do not offer other ways to integrate it or ask again.
An explicit request from the user in the session still wins.
```

- [ ] **Step 4: Run** `node --test tests/*.test.js`, `python3 -B -m unittest discover -s tests`, `python3 -B scripts/validate.py`. Expected: all pass. Commit.

### Task 2: Live check

Load the packaged plugin into a throwaway project (a local clone under `/private/tmp` with a copy of the plugin under `.opencode/plugins/` and its id changed in that copy only, as in the session-titles live check), with `git_workflow` on. In one API-created root session, ask the agent to load the `finishing-a-development-branch` skill with the skill tool and then say what it will do next.

- [ ] Observe whether the reply follows the note (no integration menu) and record the outcome, the OpenCode version and the plugin commit in `docs/development.md` under "The pull request hook: what is known"; state anything not observed as unverified.
- [ ] Remove the throwaway session, clone and package. Commit the docs change.
