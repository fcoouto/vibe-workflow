# Workstreams: native children only — design

Date: 2026-11-10. Status: **design; independently reviewed (one round and one recheck; all findings applied or answered); not implemented.** Decided with the user in a brainstorm that included GPT-6 Astra (xhigh) and Gemini 4 Argon (high).

## 1. Goal

Make delegated work simpler and cheaper. All delegated work runs as native child sessions of one coordinator. Only the coordinator's own thread gets a managed title. The display-family system (`[W1]`, `P`/`C`, the shared register) goes away.

## 2. Decisions

| # | Decision | Reason |
|---|---|---|
| D1 | Delegated workers and reviewers run only as native child sessions (Codex subagent threads, Claude Code subagents, OpenCode `subagent` children). | Native children report back on completion, carry real parent lineage and can be continued by ID. Separate chats cannot notify the coordinator, so they need polling or relaying by the user. |
| D2 | One coordinator schedules every child; children do not start their own. | Works the same on every client: OpenCode allows one nesting level by default ([tools](https://opencode.ai/docs/tools/#subagent)), Claude Code three ([subagents](https://code.claude.com/docs/en/sub-agents)). |
| D3 | A chat the user asks for is an independent conversation, not part of a family and not orchestrated. | The user never steers a deliverable chat while its coordinator works, nor needs one to outlive it (user, 2026-11-10). |
| D4 | Only the coordinator's thread gets a managed title: `￻` + five meter cells + space + optional `STATUS · ` + topic, for example `￻▰▱▱▱▱ Thread naming` and `￻▰▰▰▰▱ REVIEW · Thread naming`. Children get no managed title. | Title writes and readbacks per child cost coordinator turns in its longest-lived context. The user does not search by `[W…]` (user, 2026-11-10). |
| D5 | No family code and no `workstream_context_id` for new work. A managed Codex child keeps the runtime's attempt-based name (`worker_<attempt>`, `review_<attempt>`) and is identified by the thread ID Codex returns. | The runtime already derives that name without a label (`runtime/adapters/codex.py:1049`). It still proves which thread belongs to which assignment and lets a lost response be recovered. No shared database is needed. |

## 3. Rules after the change

- **Delegation.** Unchanged in substance. The existing child record in the task record (`clients/workstreams.md:42`) gains "branch or worktree" and loses the family/member mapping; a new coordinator resumes from it.
- **Coordinator title.** Meter, the status label only when the user must act (`REVIEW`, `WAITING`), and a short topic. Manual titles, pauses, readback and the PR merge gate before `DONE` stay as they are.
- **Children.** No managed title, meter or status. A child started without the managed runtime gets a short description through the client's own dispatch field. A managed Codex child shows its attempt name with the existing role badge ([worker visibility](../../../clients/worker-visibility.md)).

## 4. Changes

**Contracts**

| File | Change |
|---|---|
| `policies/03-agent-delegation.md` | Native children only (D1, D2); the sidebar-chat sentence becomes D3; remove the peer-chat push lines (`:77-79`) and the family/member mapping reference (`:60-61`). |
| `policies/07-context-and-handoff.md` | Naming at starts and checkpoints applies to the coordinator's thread only (`:35`). |
| `clients/workstreams-naming.md` | Shrink to: naming activates for the coordinator only, the D4 format, statuses, meter and the PR merge gate. Remove child eligibility (`:25-27`), family resolution and allocation, `P`/`C`, deliverable and peer chats, search keys. |
| `clients/workstreams-titles.md` | Coordinator-only adoption, sync and manual titles. Remove member `C`, `parent_session_id` display binding and every "preserve the family/member binding" (`:63-74`, `:87`, `:223`, `:286-323`). Keep the OpenCode section for the coordinator's session. |
| `clients/workstreams.md` | Add "branch or worktree" to the child record (`:42`); remove "add a coordination level" (`:80`, contradicts D2); the names subsection points to root naming only. |
| `clients/workstreams-protocol.md`, `clients/evidence-review-prepare.md` | Replace the `WS42.01` examples with D5. |
| `skills/workstreams/SKILL.md` | The naming route covers the coordinator title only. |

**Code**

| File | Change |
|---|---|
| `runtime/title_sync.py` | `format_title` and the parser handle only the D4 format. A title or saved record in any other shape counts as unmanaged: sync, repair and resume write nothing for it. Configure no longer calls the allocator and rejects `member` and `parent_session_id`. |
| `runtime/opencode_titles.py` | Comparison keys (`:152`) drop `family` and `member`. |
| `scripts/workflow.py` | Reject `workstream_context_id` at worker prepare, like `workstream_revision` and `workstream_limits`, and at `evidence review prepare`. |
| Tests | `tests/test_title_sync.py`, `tests/test_title_sync_integration.py` (replace the register-corruption test with "register never opened"), `tests/test_opencode_titles.py`, `tests/test_delivery.py`, `tests/test_delivery_reconcile.py`, `tests/test_compact_status.py`; `tests/test_workstreams_cli.py:62-80` becomes a rejection test and gains the review-prepare rejection. Tests that only exercise the removed formats are deleted. |

Unchanged: `scripts/workstreams_family.py` (left byte-identical and unreferenced; it has no read-only mode and is part of the runtime fingerprint), `runtime/delegation.py`, `runtime/adapters/codex.py`, `runtime/startup.py`.

## 5. Out of scope

Resource admission, capacity recovery, the managed task runtime itself, worker role declarations, Smart Routing, and agent-team or multi-session features some clients offer.

## 6. Acceptance

1. A new coordinator title is exactly the D4 format; configure never opens or creates the register.
2. A title or saved record not in the D4 shape is never written.
3. Configure rejects `member` and `parent_session_id`; worker and review prepare reject `workstream_context_id`.
4. `opencode_title.py` accepts a D4 title.
5. Instruction paths, checked with representative tasks: a coordinator starting implementation loads the naming rules and adopts a title; a child's delegation path loads no title procedure.
6. `clients/`, `policies/` and `skills/` no longer mention `workstreams_family.py`, `member: "C"`, a title `parent_session_id`, deliverable chats or `[W`.

**Checks** (per [the development guide](../../development.md#checks)): `python3 -B scripts/validate.py`; the affected modules through `scripts/test_sharded.py`; commit, then the full suite once, because `scripts/workflow.py` is shared core and the policy edits reach many fixtures; `--fast` before `[READY]`.

## 7. Effect on other work

- **OpenCode client (R4, paused).** Its design orients root sessions only; that now matches this rule. Its title references narrow to the coordinator's session.
- **Smart Routing.** Unchanged, and better aligned: its OpenCode enforcement and accounting already follow native parent links only.

## 8. Risks

- **Coordinator lost.** A new coordinator cannot continue another session's child: OpenCode 2.2.6 refuses with "is not a child of the current session" (observed 10 November 2026), and Claude Code resumes subagents only within the same session (documented). Recovery resumes the original coordinator session; the child record and per-child branches keep the work recoverable.
- **Fewer visible names.** Children lose their family tag in sidebars that list them flat; managed Codex children show only the attempt name and role badge.
