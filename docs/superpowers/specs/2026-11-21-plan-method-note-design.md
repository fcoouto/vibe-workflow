# Execution method pre-approved

## Intent

Superpowers' `writing-plans` skill ends every plan with "Which execution approach would
you prefer? Subagent-driven / Native … For this plan I recommend …" and waits. The user
pre-approves whatever the agent recommends, automatically, without an onboarding
question. Only the method is pre-approved: the skill's request to review the plan, and
its wait for that review, stay. Approved by the user 2026-11-21.

## Behavior

- A third entry in the skill-load note table (`pr-advice.js`, `SKILL_NOTES`): when the
  `skill` tool loads `writing-plans` (bare or prefixed), append exactly:

  > vibe-workflow: your human partner pre-approved the execution method you recommend;
  > treat it as already supplied, name it, and do not ask which one to use. Still ask them to review
  > the plan, and wait for that review before implementing. An explicit request from your
  > human partner in this session overrides this.

- Gate: the project is onboarded, meaning any vibe-workflow feature (Smart Router, git
  workflow, session titles) has a saved choice, on or off. A project nobody onboarded
  keeps stock behavior. The git-workflow entries keep their own gate (git workflow on).
- "already supplied" matches the skill's own branch for a supplied method, which still
  asks for the plan review.
- Same rules as the other notes: id checked before any settings read, completed loads
  only, errors give no note, never throws, shell notes unchanged.

## Not in scope

A setting or onboarding question; skipping the plan review; notes for other skills.

"name it" was added on 2026-11-21 after the live check showed the handoff did not say which
method would be used, so the user could not object to it.
