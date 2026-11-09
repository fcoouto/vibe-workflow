# D8: design for the remaining core slices

Proposal · 8 November 2026 · **Approved; implementation status is in the parity plan.** Slices S1, S3, S5 and S6 of the [parity plan](d8-claude-parity-plan.md) are implemented. The slices below change the evidence, delegation and setup-qualification owners (about 20,000 lines of fail-closed code). They depend on one another and need decisions that the code cannot make, so each is proposed here with its native facts, approach, risks and tests.

## Native facts observed on Claude Code 2.1.311

One disposable project, `haiku`, project-only settings, about $0.01 in total. They establish shapes, not a distribution.

| Fact | Observation |
| --- | --- |
| Spawn chain | `PreToolUse(Agent)` `tool_input`: `description`, `prompt`, `subagent_type`, `model` (the requested alias). `PostToolUse(Agent)` `tool_response`: `agentId`, `resolvedModel`, `status`, `outputFile`, `canContinueAgent`. `SubagentStart` and `SubagentStop` carry the same `agent_id`; `SubagentStop` adds `last_assistant_message` and an `agent_transcript_path` that exists on disk. |
| Identity | `prompt_id` is stable across a prompt, its subagents and a Stop continuation. Subagent tool hooks carry the parent's `session_id` plus their own `agent_id`. |
| Title | A hook-set title is stored and returned; the leading `U+FFFB` is stripped. |
| Deny | A `PreToolUse` deny blocked a write under `bypassPermissions`. |
| Not observed | Payload sizes at scale, older Claude versions, Desktop and Cowork, and execution of the packaged handlers. |

## Proposed order and dependencies

`N4 qualification` → `N1/N2 SubagentStart/Stop capture` → `S4 evidence` → `M3 writer retirement` → `N7 guidance` → `M5 routes`. N4 comes first because no managed Claude scope can be active outside test fixtures without it. N1 must precede S4 because policy 06 needs independently observed review provenance before completion.

## N4: Claude setup qualification

**Problem.** `startup.py` builds a managed scope only with a qualification binding, and only Codex can produce one: `setup_observation.py` reads `.codex` config layers, the app-server `hooks/list` trust hashes and D4 journal receipts. Claude has no hook-list or trust call.

**Proposal.** A Claude setup source whose witness is a behavioral harness rather than a registry read:

1. Observe the configuration: `claude --version`, the documented settings layers (user, project, local, managed files), `disableAllHooks`, `allowManagedHooksOnly`, plugin installation and enabled state, and the digest of `hooks/claude-code.json`. Reuse the S5 reader.
2. Run the packaged handlers in a disposable copy with `claude -p --setting-sources project --max-budget-usd` against a seeded fixture, and record hook-observed facts per capability: `startup-context` (SessionStart output), `pre-tool-enforcement` (a denied `Edit` in a non-`WORKING` task), `post-tool-handling` (generation advance), `stop-ownership` (bounded continuation). `interrupt-ownership` is **inapplicable** with a recorded platform reason, never waived silently.
3. Emit the same retained receipt shape (`content`, `assessment`, `capabilities`) so `read_qualification`, `_coverage` and `require_capability` stay unchanged; add `client == 'claude-code'` branches only at `setup_observation.py:376,384,552,1426` and `changes.py:6018`.

**Risks.** It touches D4 journals and receipts, and the harness spends model usage per qualification, so it needs an explicit budget and approval at run time. A mock witness would be exactly what the repository forbids ("a successful mock is not observed client execution").

**Decision needed.** Approve the behavioral-harness witness, and approve running it against a disposable project with a capped budget per attempt.

## N1/N2: native review and worker observation

**Proposal.** Register `SubagentStart` and `SubagentStop` and record, per `agent_id`, a bounded private receipt in the runtime state directory:

- From `PreToolUse(Agent)`: `tool_use_id`, requested `model`, `subagent_type`, a digest of `prompt`.
- From `PostToolUse(Agent)`: `agentId` and `resolvedModel` (the observed model).
- From `SubagentStart/Stop`: `agent_id`, `agent_type`, `last_assistant_message` digest and its bounded text, and the transcript path.

Provenance is the chain `tool_use_id → agentId → agent_id`, correlated by `prompt_id`. Assignment markers move from Codex's `task_name`/`agent_path` to the first line of the `prompt`, which the chain digest binds. `evidence.collect_review` and the worker observers gain a Claude collector that reads these receipts instead of an app-server.

**Not ported:** exec-wrapper pairing, rollout accounting, foreground wait and `fork_turns_none`. Dispatch controls are limited to what Claude exposes: `model` alias and, per the docs, an `effort` parameter from v2.1.292 (unobserved here).

**Risks.** Receipts are written by hooks that fail open, so a missing receipt must leave review unverified, never satisfied. Subagents may be background (`isAsync: true` was observed), so completion needs `SubagentStop`, not the tool result. `SubagentStop` also fires for internal agents.

**Decision needed.** Approve hook-written receipts as the Claude provenance source, and approve a registered `SubagentStart`/`SubagentStop` handler (more handler processes per session).

## S4: evidence and completion for Claude

*Status: implemented at package level on scopes without a capability projection. A real stage-1 scope still refuses these operations until their capabilities are verified; see the validation record.*

Lift `state._validate_task`'s refusal of `evidence`, `delegation` and `retirement` fields for Claude, remove the `_identity` gates (`evidence.py:81,1404,2108`), generalize `revalidate_ready` and the three `client == 'codex'` checks (`evidence.py:6511,6534,6860`), and let `task_status` return `DONE`. Checks (`run_check`) and reservations are already client-neutral in logic. Until N1 exists, plans that require review stay unsatisfiable, which fails closed. **Tests:** reuse the Claude-born canonical fixture for plan, check, verify and stale-evidence rejection.

## M3, N7, M5

- **M3 writer retirement** needs a Claude worker lifecycle from N2 before `writer_stop` proof can be bound.
- **N7 guidance cache** for a Claude coordinator needs new client surfaces (for example `claude-code-cli`), a Claude model catalog from the Agent `model` aliases, and neutral state paths instead of `CODEX_HOME` (`resource_execution.py:193`, `worker_visibility.py:230`). The catalog is static (no discovery call exists), so refresh uses documentation, not an API.
- **M5** adds a client parameter to the OpenRouter and external-route settings acquisition (`openrouter_settings.py:40,64,67`).

## Not proposed

Judged unnecessary by the independent Reviewer and unchanged: Bash truncation notice, Habblaud and worker visibility, Codex rollout accounting, capacity recovery and the usage-routing host. Unsupported by the platform: interrupt pause and mid-turn title writes.
