# Product

<!-- impeccable:product-schema 1 -->

## Platform

Chat-based coding-client workflow, not a standalone web, iOS or Android surface.
OpenCode is the first client. Codex, Claude and other clients are intended to
follow. Shared behavior should remain client-neutral; client-specific setup
belongs at the client boundary.

## Users

People working on software projects through coding clients and their existing
plugins or skills. Detailed audience segmentation has not been established.

## Product Purpose

`vibe-workflow` helps users adopt workflow capabilities without replacing the
client or recreating tools that already serve the project.
Project onboarding should be a thin, quick chat-based question-and-answer flow.
Success means the user understands the project choices, answers them, and can
continue working without onboarding becoming a new framework.

## Operating Context

The OpenCode package combines advisory Smart Router selection with chat-based
project onboarding. It is locally packaged under `dist/`, without permanent
installation.

Native clients and other tools retain task ownership, agent roles, permissions,
execution, continuation and result delivery. Future features must evaluate
partnership with Superpowers and other existing plugins or skills before adding
overlapping behavior.

## Capabilities and Constraints

### Available now

- Advisory provider, model and effort selection through `smart_router_select`.
- Isolated native-loading and lifecycle checks; see `docs/native-verification.md`.
- Existing native credentials are reused, never copied into project setup.
- Selection launches nothing and grants no admission, reservation or spend permit.
- Pull request guidance: the `vibe-pull-requests` skill (lifecycle, readiness,
  template, primary checkout) and advisory notes after matching shell commands.
  Onboarding asks who merges `[READY]` PRs; the choice is saved as booleans in
  the `git_workflow` entry of `.vibe-workflow/settings.json` at the main project
  path (named `pull_requests` in 0.5.0 and 0.5.1; the old name is no longer read). The notes are
  reminders, never enforcement. A note was observed reaching the model on
  OpenCode 2.2.6 (19 November 2026); other versions are unverified.

The shipped router configuration examples are disabled and empty. This is
different from the approved future onboarding default below.

### OpenCode project onboarding

- Use the project conversation, beginning with OpenCode.
- Confirm the project and client without asking users to repeat known facts.
- Offer Smart Router with its choice defaulting to ON and a clear opt-out.
- Store client-neutral project preferences in `.vibe-workflow/settings.json`
  at the project root. Credentials and account/usage caches remain outside it.
- Say what will be saved, then apply after the user's answers; there is no
  separate confirmation question. The client's native edit permission still
  applies and cancelling stops the flow.
- Distinguish the saved preference from actual readiness.
- If an enabled router cannot recommend an eligible route, post a brief
  `vibe-workflow`-identified notice with a relevant corrective action, then
  continue with the client's existing selection.
- Ask about future capabilities only when those capabilities are available.

### Planned evaluations, not available capabilities

- Templates beyond the pull request template:
  [issue #329](https://github.com/fcoouto/vibe-workflow/issues/329).
  Evaluate existing structures and opportunities to improve or standardize them.
- Further GitHub PR handling beyond the shipped guidance, such as a `[READY]`
  lookup before agent merges or post-merge branch cleanup:
  [issue #328](https://github.com/fcoouto/vibe-workflow/issues/328).
- Tests and builds, plus code and security review: apply the same partnership
  principle. Their exact feature boundaries remain open.

## Product Principles

- Keep setup short and disclose additional choices only when needed.
- Partner with existing workflows rather than take ownership from them.
- Preserve project customization, credentials, subscriptions and client settings.
- Make plugin-originated advice recognizable and actionable.
- Never confuse an enabled preference, a ready capability and execution authority.

## Evidence on Hand

The repository's README and runtime describe current behavior.
`docs/native-verification.md` describes tested native loading, real process-group
cleanup and cancellation against a synthetic native reader.
The approved conversational flow is captured in
`docs/superpowers/specs/2026-11-19-chat-onboarding-design.md`.
No real-provider execution, native session-interrupt propagation, completion-time
target or usability-study result is established by those checks.

## Open Decisions

Other client integrations and the boundaries of future capabilities remain
open. Neither this product record nor the UX flow authorizes installing the
plugin, enabling it in a real project, changing credentials, launching inference
or publishing changes.
