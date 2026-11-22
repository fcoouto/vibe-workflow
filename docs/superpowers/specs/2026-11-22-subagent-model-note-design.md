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

- The OpenCode plugin (`index.js`) appends this, after one space, to the description of
  the `subagent` tool's `model` parameter, right after OpenCode's "NEVER set this …":

  > vibe-workflow: your human partner asks you to set the subagent tool's `model` and
  > variant in this project; this is the explicit request that tool requires. Use the
  > first that applies: a model they or your active skill name exactly; Smart Router's
  > recommendation, when on; the tier your skill or plan calls for, on this session's
  > provider. Otherwise leave it unset.

- Gate: the project is onboarded (the existing `onboarded` check: any feature has a
  saved choice, on or off). A project nobody onboarded keeps stock behavior.
- Channel: the existing `context` session hook appends the note to
  `event.tools.subagent.input.properties.model.description` on every step, once per
  step. `input` is a plain JSON Schema object there. OpenCode's own shell and subagent
  tools edit their descriptions in the same hook. No system-prompt push and no session
  instruction entry: the note travels with the parameter, so it reaches every provider
  from the first model call, and it disappears on the next step once the gate is false.
  If any part of that path is missing or the description is not a string, nothing
  happens.
- Why the parameter and not the tool description: the `claude-agent` bridge cuts the
  subagent tool description to 2048 characters. When it is longer, the bridge moves the
  "Available subagents:" list to the front and cuts the tail (OpenCode 2.3.2, bundled
  function `OZ`, constant `Y0=2048`). A note on the tool description lands right after
  that list, so it is lost once the user's agent list grows. The bridge passes
  `inputSchema` through unchanged.
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
- Editing the tool-level description (see "Why the parameter" above).

## Evidence (2026-11-22)

Probes on OpenCode 2.3.2: a throwaway repository with only a project-local probe plugin,
`opencode run --standalone`, one prompt per model and no tools, all on the first model
call. Markers were text the prompt did not contain.

- Tool description, appended marker: `claude-agent/sonnet` and `openai/gpt-6.1-terra`
  quoted it. With markers at both ends of a 2239-character description, Sonnet's first
  60 characters began "Available subagents:" and its last 60 ended mid-sentence in
  OpenCode's own text, which matches the bridge code above.
- `model` parameter, appended marker: `claude-agent/sonnet`, `openai/gpt-6.1-terra` and
  `opencode-go/claude-haiku-5-5` each quoted the full parameter description with the
  marker at its end. For all three the hook saw `input` with keys `type`, `properties`,
  `required`, `additionalProperties`, and `properties.model` with `type` and
  `description`.

Code mode, read from the 2.3.2 binary: the subagent tool is registered with
`codemode: false` unconditionally, so it is never moved behind `execute`, even with the
experimental `full_codemode` flag (off by default, opt-in via `OPENCODE_FLAGS` or
`experimental.full_codemode`). This user's config does not enable it.

## Known gaps

- One model per provider was probed: the three subscription paths Smart Router routes
  (`claude-agent`, `openai`, `opencode-go`). Every model receives the same schema, so
  what varies by model is only whether it follows the note, not whether it sees it.
- OpenCode versions other than 2.3.2 are unverified. The `event.tools` shape is
  undocumented; a version that changes it makes the hook a no-op (stock behavior), not
  an error. The live record in `docs/development.md` names the version.
- Projects onboarded before this change get the note without having been asked; the
  user accepted that as their own approval.
