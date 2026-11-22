# Subagent model note cleanup

Follow-up to `2026-11-22-subagent-model-note-design.md` (PR #366). Approved by the user
2026-11-22. A small change: text and one test, no behavior change.

## Intent

PR #366 made agents follow the subagent model note when Smart Router has no
recommendation. Two lines still promise the old fallback ("the client's existing
selection"), and the final review left two minors.

## Changes

- `skills/vibe-onboard-project/references/procedure.md:186`: "show it before continuing
  with the client's existing selection and permissions" becomes "show it, then continue
  without a recommendation, within the client's permissions". The file is client-neutral,
  so it does not name OpenCode's note. Not question 1, and no new question.
- `PRODUCT.md:89`: "then continue with the client's existing selection" becomes "then
  continue without a recommendation". Edited through the `impeccable` skill.
- `tests/plugin.test.js`: a context event with no `tools` key reaches the hook, which
  leaves it without one and still pushes the router instruction. The misleading
  `undefined` case leaves the odd-shapes list (`step(undefined)` substituted a schema).
- `README.md`, through `impeccable`: "agents also pick each subagent's model: one you
  name, else …" becomes "agents set each subagent's model when one applies: a model you
  or their skill name, else Smart Router's pick when it is on, else their workflow's
  tier, on the same provider."

## Approaches

One sensible way: edit the four places. Leaving the lines was rejected because they
contradict shipped behavior.

## Not in scope

Moving the router instruction onto the subagent tool (its own design); historical specs
and plans, which record what was true when written.
