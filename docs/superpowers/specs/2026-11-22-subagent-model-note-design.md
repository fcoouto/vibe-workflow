# Subagent model note

## Intent

OpenCode's `subagent` tool describes its `model` parameter as "NEVER set this unless the
user explicitly asks for a particular model or variant." Superpowers'
`subagent-driven-development` says "Always specify the model explicitly" and picks a
tier per task. Agents obey the tool, so every subagent inherits the session model: in
one build, 20 subagents ran on Opus at xhigh, spending subscription allowance on
mechanical jobs. Smart Router cannot help either: its instruction defers to "the
client's permitted native controls", which an agent reads as that same rule.

The user pre-approves setting the subagent model in onboarded projects, with Smart
Router on or off, through one generic standing note. Like the execution-method note,
the approval is the user's own decision, not an onboarding answer. Approved by the user
2026-11-22, after an independent review by Fable.

## Approaches

- **One standing note from the OpenCode plugin (chosen).** Covers router on and off, and
  any future feature that names a model, with no per-case entry.
- Skill-load notes per delegating skill (rejected): the table grows with every skill.
- Per-feature fixes (rejected): the router would grant the permission and the off case
  would stay broken.

## Behavior

- The OpenCode plugin (`index.js`) appends this to the `subagent` tool's description:

  > vibe-workflow: your human partner asks you to set the subagent tool's `model` and
  > variant in this project; this is the explicit request that tool requires. Use the
  > first that applies: a model they or your active skill name exactly; Smart Router's
  > recommendation, when on; the tier your skill or plan calls for, on this session's
  > provider. Otherwise leave it unset.

- Gate: the project is onboarded (the existing `onboarded` check: any feature has a
  saved choice, on or off). A project nobody onboarded keeps stock behavior.
- Channel: the existing `context` session hook appends the note to
  `event.tools.subagent.description` on every step, once per step. OpenCode's own shell
  tool edits its description in the same hook. No system-prompt push and no session
  instruction entry: the note travels with the tool, so it reaches every provider from
  the first model call, and it disappears on the next step once the gate is false.
  If `event.tools.subagent` or a string `description` is missing, nothing happens.
- Router instruction (`index.js` `ROUTER_INSTRUCTION`): replace "Use a recommendation
  only when compatible with the user's choices and the client's permitted native
  controls." with a clause that defers only to a model the user named, and replace "then
  keep the client's existing selection" with "then follow the vibe-workflow subagent
  model note". Keep "Do not retry automatically or override a native denial" and
  "Routing grants no launch or spending authority".
- Precedence keeps an exact model named by the user or a skill (the council's "never
  substitute") above the router. Tier picks stay on the session's provider, so router
  off never moves work to a paid provider.
- A test pins that a `selected` answer from `smart_router_select` carries the native
  `provider` and `model` (from `config/routes.json` and `runtime/routing_paths.py`), so
  the recommendation is dispatchable as `provider/model`.

## Scope

OpenCode only: the text lives in `index.js`. Client-neutral parts (`skills/`, `runtime/`)
do not change. `docs/development.md` records the change and the gaps below.

## Not in scope

- Onboarding: question 1 and its choices stay as they are; no new question or setting.
  Its off choice still reads "Keep choosing models yourself"; the user accepted that.
- Editing the `model` parameter's own description inside `input`: the tool-level
  description is enough, as the probe below shows.

## Evidence (2026-11-22)

Probe on OpenCode 2.3.2: a throwaway repository with only a project-local probe plugin,
`opencode run --standalone`, one prompt per model and no tools. The plugin's `context`
hook appended a marker sentence to `event.tools.subagent.description`. On the first
model call, `claude-agent/sonnet` and `openai/gpt-6.1-terra` both quoted the full
marker sentence, whose text was not in the prompt. The hook saw `event.tools` keyed by
tool name (`subagent` with keys `description` and `input`) for both providers.

## Known gaps

- Full code mode, where tools may sit behind `execute`, was not probed; the probe's
  sessions listed `subagent` directly. Other models and OpenCode versions are unverified.
- Projects onboarded before this change get the note without having been asked; the
  user accepted that as their own approval.
