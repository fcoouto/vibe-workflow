# Thin chat-based project onboarding

## Status and intent

The user approved the five-step conversational flow, non-blocking fallback and
project-settings location, then requested an implementation plan.
It does not implement onboarding or authorize activating a real project.

Make connecting a project quick and understandable. Start on OpenCode, keep the
conversation client-neutral, and leave room for Codex, Claude and other clients.
Use existing capabilities and partner workflows rather than restore the earlier
plugin's full onboarding framework.

## Approaches considered

- **Shared chat Q&A — selected by the user.** Works inside the coding client
  where the user already operates; client-specific setup remains separate.
- **Standalone terminal command.** Potentially useful as an internal helper,
  but not the selected user-facing onboarding experience.
- **Dedicated setup screen.** Adds a separate interface to build and maintain;
  the user selected chat instead.

## Surface and interaction

Mode: **Operate**. The goal is a connected project with clear choices, not a
product tour. Reuse the client's native question controls and chat rendering.
No new visual identity, browser wizard, custom frontend or animation is needed.
No typography, color or layout system should override the client's interface.

The user-facing flow has five steps:

1. **Confirm project and client.** Show detected context concisely. Do not make
   users re-enter known facts or scan the repository to invent project policies.
2. **Ask about Smart Router.** It defaults to ON for a new project, with a clear
   opt-out. Existing saved choices take precedence on repeat onboarding.
3. **Preview proposed changes.** Identify the target project/client, feature
   preference and intended configuration edits. Preserve unrelated settings.
4. **Apply after confirmation.** Cancellation leaves setup untouched.
5. **Report actual readiness.** State what works or what needs attention.
   Do not turn an enabled preference into an unsupported readiness claim.

Only available capabilities receive onboarding questions. PR handling,
templates, tests/builds and code/security review are future evaluations, not
placeholder toggles or required questions in this version.

## First question: approved copy

**Use Smart Router for this project?**

**Use Smart Router — default**\
Automatically get provider, model, and effort recommendations before delegating
work. Your client still launches and controls the tasks.

**Keep existing model selection**\
Continue with your current model-selection workflow. You can enable Smart Router
later.

The default is a proposed preference, not permission to write configuration
before the preview is confirmed.

## Readiness and fallback

Keep preference and readiness distinct:

- **Off:** the project opted out. This plugin does not consult Smart Router.
- **On · Ready:** the required setup checks actually passed.
- **On · Setup needed:** the project opted in, but configuration or another
  prerequisite is missing or unusable.

These labels describe current observed state, not a permanent guarantee that
the next request will have an eligible route. Initial examples are disabled
and empty today; onboarding must not invent strategy rows, account facts or
entitlement to make an ON preference appear ready.

When an enabled Smart Router cannot recommend an eligible route:

1. Post a brief notice that clearly identifies `vibe-workflow`.
2. Name the observed problem and suggest an applicable corrective action.
3. Continue with the client's existing selection.

Example:

> **vibe-workflow:** Smart Router found no eligible route. Check routing settings
> or account availability. Continuing with your client's existing selection.

Use more specific wording when a known reason supports it. Do not claim an
account is exhausted, signed out or charge-safe without corresponding evidence.
An intentional opt-out is not an error or an occasion for repeated warnings.

Fallback concerns the lack of an advisory recommendation only. It never
overrides a client permission denial or authorizes work the client would not
otherwise run. No plugin-driven retry, model/provider switch, permission change,
launch, quota reservation or additional spending authority is introduced.

## Partnership and scope boundaries

- Keep task ownership, roles, review independence, execution, continuation and
  result delivery with the client and existing workflow owners.
- Evaluate Superpowers and other plugins/skills before adding overlapping PR,
  template, test/build or review behavior.
- Do not generate policies, rewrite `AGENTS.md`, install dependencies, copy
  credentials or introduce a project database as onboarding side effects.
- Use the least configuration necessary; a second project registry is not an
  assumed requirement.
- Adding later capabilities must not reset the project's prior opt-out choices.

## Proof expected from implementation

- The chat presents the two options clearly and defaults a new project to ON.
- Known project/client facts are shown, not asked repeatedly.
- Preview and cancellation preserve existing configuration.
- Repeated onboarding preserves the user's saved choice and avoids duplication.
- Missing configuration remains visible as setup needed, not false success.
- A failed advisory selection produces a plugin-identified, actionable notice
  and continues through the client's existing selection.
- Opt-out makes no Smart Router acquisition request.
- Native permission/execution denials remain effective.
- Unavailable future features add no questions or configuration.
- Checks distinguish mock conversation proof, actual client loading and live
  account/model behavior; none substitutes for the others.

## Choices deferred to implementation planning

Store client-neutral project preferences in
`<project-root>/.vibe-workflow/settings.json`. Keep native credentials and
account/usage caches outside the project. The user approved this location.

The exact entry-point name, project-settings schema, repeat-notice
handling, readiness checks and client-specific integration mechanism remain
to be selected against the actual host APIs. The plan must explain how callers
automatically consult the advisory selector: the current tool does not itself
intercept or launch native work.

Only OpenCode integration is an initial delivery target. This design defines
shared UX expectations for later clients, not their implemented support.
Existing plugin installation, fresh router setup and project opt-in remain
distinct responsibilities. No implementation, permanent installation,
inference, publication or merge follows merely from saving this design.
