# OpenCode as a full plugin client — design brief

Date: 2026-11-10. Status: **brief for brainstorming.** Next: brainstorming, independent review and the implementation plan, in a separate session. This brief is slice R4 of the [workspace readiness plan](../plans/2026-11-09-workspace-readiness-and-onboarding.md).

## Decision

OpenCode becomes a full `vibe-workflow` client, on par with Codex and Claude Code (user decision, 2026-11-10). Reason: OpenCode will receive routing delegation to its own models (free models and the OpenCode Go plan), so its sessions must load the same workflow rules, project preferences and version checks as the other clients.

## Today

- OpenCode is declared a routing client only: `docs/onboard-my-project-plan.md` §"Client scope and adapter organization" and `clients/opencode.md` ("not an onboarding or startup client").
- What exists for OpenCode: `runtime/adapters/opencode.py` (pure normalizer for routing hooks and events) and `runtime/adapters/opencode_plugin.ts` (V2 server plugin, routing shim; ~960 lines), the routing command, and an ordinary-path title contract (`clients/workstreams-titles.md` §"OpenCode sessions", `scripts/opencode_title.py`).
- Client lists that exclude OpenCode: `runtime/onboarding.py` (`_source_record`), `runtime/state.py`, `runtime/observability.py`, `scripts/workflow.py` (hook adapters), `runtime/package_versions.py`, `clients/startup.md` (`clients` field), and the readiness command from R1 (reports the plugin as not supported in OpenCode until R4).
- OpenCode already reads the project's `AGENTS.md` natively, so the repository's own "Start here" rules reach OpenCode sessions without the plugin.
- On this machine: OpenCode 2.2.6; `~/.config/opencode/opencode.json` lists one plugin (`@superwhisper/opencode`).

## Scope to design

1. **Packaging and install.** One OpenCode V2 plugin for `vibe-workflow`: how it is distributed (local folder, Git, npm) and installed (`plugins` config or `opencode plugin add`); how the existing routing shim joins it instead of shipping separately; how upgrades work and how `package_versions`/`workspace status` read the installed version.
2. **Startup orientation.** The same enrollment context Codex and Claude Code get from `SessionStart`, through OpenCode's supported hooks. Candidates seen in official plugins: `experimental.chat.system.transform` (Ponytail) and session/context hooks used by Superpowers' V2 plugin. Decide which hook, when it fires (new session, resume, compaction, child sessions), and how the Python startup decision (`scripts/workflow.py`, `runtime/startup.py`) is invoked within OpenCode's time budget.
3. **Enrollment.** Add `opencode` as a client id wherever enrollment, startup, state and observability accept clients; canonical `project.json` membership; onboarding client selection.
4. **Trust and safety.** OpenCode's plugin trust model compared with Codex hook trust and Claude plugin trust; what `workspace status` can observe; no secrets in injected context.
5. **Version drift and readiness.** OpenCode in `upgrade status/record`, the startup drift warning and the readiness warning (R5).
6. **Child sessions and routing.** How orientation behaves for subagent and routed sessions (parent chain, managed vs interactive sessions in the shim), without duplicating context or weakening routing enforcement.
7. **Contracts.** `clients/opencode.md` and the plan's support targets updated; `clients/index.md` loading trigger; source-to-contract coverage.

## Constraints

- AGENTS.md: Python 3.11+ runtime and SQLite stay the core; thin client adapters; each Python file ≤1000 lines; entry points ≤150 words; one rule stated once; Toolkit is authoring authority.
- Startup stays cheap: no network or model calls, bounded time, failure never breaks the session.
- Keep the routing shim policy-free; Smart Routing owns routing behavior.
- Preserve the user's OpenCode configuration and other plugins; changes to `opencode.json` are backed up first.
- Observed client behavior is reported separately from package and unit checks.

## Open questions for brainstorming

1. One plugin or two (orientation plugin plus the existing routing shim)? What is the smallest packaging that keeps upgrades to one step?
2. Which OpenCode hook gives the most reliable "session start" equivalent, including resume and compaction?
3. Should OpenCode orientation inject the full context or only pointers, given OpenCode already loads `AGENTS.md`?
4. How does the TypeScript plugin call the Python decision: subprocess per session, or a cached result?
5. What is the minimum live qualification on OpenCode 2.2.6 before calling it supported?

## Acceptance (for the eventual plan)

- In an enrolled checkout, a new OpenCode session receives the same orientation as Codex and Claude Code, and an unenrolled checkout receives none.
- Onboarding enrolls OpenCode by default when installed; `workspace status` reports the plugin's version and state in OpenCode.
- An upgrade covers OpenCode in the same procedure; drift is warned.
- The routing shim keeps its enforcement guarantees.
- The full suite passes once for the slice (shared core changes); live OpenCode observations are recorded separately.
