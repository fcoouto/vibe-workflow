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
- Council: the `vibe-council` skill and `/vibe-council` command convene a
  roster of up to five seats (picked by score, frontier models from different
  labs first, unless the user names them) as `explore`
  agents (no edit tools or subagents; shell stays available, so the brief asks for
  read-only work); the `vibe_council_roster` tool checks seats against the plan
  providers in `config/routes.json` with local, token-free account reads,
  whether or not Smart Router is on. It launches nothing and grants no
  admission, reservation or spend permit. Records go to
  `docs/vibe-council/sessions/<hash>_<topic>/` and are committed with the work.
  The coordinator never votes; the user decides.
- Pull request guidance: the `vibe-pull-requests` skill (lifecycle, readiness,
  template, primary checkout) and advisory notes after matching shell commands.
  Onboarding asks who merges `[READY]` PRs; the choice is saved as booleans in
  the `git_workflow` entry of `.vibe-workflow/settings.json` at the main project
  path (named `pull_requests` in 0.5.0 and 0.5.1; the old name is no longer read). The notes are
  reminders, never enforcement. A note was observed reaching the model on
  OpenCode 2.2.6 (19 November 2026); other versions are unverified.
- Post-merge cleanup: opt-in under `git_workflow` (`worktree_cleanup`). After a
  verified merge, agents remove the task's worktree and its local branch with the
  client's own tool, never forcing and never touching the primary checkout. Remote
  branch deletion is GitHub's own setting; onboarding reads it and offers to turn it
  on when it is off and the user is an admin. Advisory, and not yet observed live.
- Project layout: the `vibe-project-layout` skill tells agents where a new file goes
  (framework paths, then the project's recorded layout, then the nearest comparable
  file, then example layouts) and, on request, restructures with a user-approved move
  map, through `safe-refactor` when it is installed. It is always registered and has no setting. In any
  onboarded project the `writing-plans` note gains one sentence pointing to it.
  Advisory, and not yet observed live.
- Security review: the `vibe-security-review` skill and `/vibe-security-review`
  command. Its ship check sizes the security work to what a change touches (tier 0
  none, 1 light, 2 focused, 3 deep with one read-only reviewer) and records the
  result in the pull request's `Security` section; its full mode reviews a whole
  project within a stated bound, in any project, onboarded or not. In every
  onboarded project, notes point to the ship check when `requesting-code-review`,
  `subagent-driven-development` or `finishing-a-development-branch` loads, and
  after a pull request is created or marked ready. Full reports go to
  `.vibe-workflow/security-reports/` in the main checkout, a folder whose own
  `.gitignore` keeps everything in it untracked. Trail of Bits skills are used when
  installed and are offered by `vibe-prepare-my-workspace`, never bundled. A review
  fixes nothing and makes no claim that code is secure. Advisory, and not yet
  observed live.

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
  continue without a recommendation.
- Ask about future capabilities only when those capabilities are available.

### Planned evaluations, not available capabilities

- Templates beyond the pull request template:
  [issue #329](https://github.com/fcoouto/vibe-workflow/issues/329).
  Evaluate existing structures and opportunities to improve or standardize them.
- Further GitHub PR handling beyond the shipped guidance, such as a `[READY]`
  lookup before agent merges, or cleaning up stale worktrees and branches that
  already exist:
  [issue #328](https://github.com/fcoouto/vibe-workflow/issues/328).
- Tests, builds and code review: apply the same partnership principle. Their
  exact feature boundaries remain open.
- Multi-client Prepare Workspace (client detection and per-client installs), a
  separate project; today it covers OpenCode only. The partner-scope rule it must
  keep: partners always install at user scope. Skills keep one shared copy in
  `~/.agents/skills/`, linked into each client's skills folder, so a later client
  only adds a link. Plugins and MCP servers are per client: offer each partner for
  every installed client it officially supports, mark the rest `not-supported`,
  and never write config for a client that is not installed.

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
