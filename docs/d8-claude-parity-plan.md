# D8: Claude Code adapter parity

Plan · 8 November 2026 · **Independently reviewed. Slices S1, S3, S5 and S6 implemented as package behavior; S0 and the title delivery observed on Claude Code 2.1.311; hook execution of the packaged handlers not yet observed.** Goal: give the Claude Code adapter every Codex adapter capability, except an item the independent Reviewer judged unnecessary or the platform cannot support. Startup parity already exists ([Claude validation](claude-validation.md)).

Rules: reuse shared runtime owners; port only the adapter seam. Codex-only mechanisms are not copied. An unsupported capability stays unsupported and is documented. Package tests and native execution are reported separately. Platform facts come from the [Claude hooks reference](https://code.claude.com/docs/en/hooks), [sessions](https://code.claude.com/docs/en/sessions) and [sub-agents](https://code.claude.com/docs/en/sub-agents) pages.

## Platform differences that shape the port

| Codex relies on | Claude Code supplies | Consequence |
| --- | --- | --- |
| `apply_patch` is the one edit tool | `Edit`, `Write`, `NotebookEdit` (no `MultiEdit`, no patch tool) | Guard matcher `Edit\|Write\|NotebookEdit`; shell writes stay outside the guard, as in Codex. |
| `turn_id` on every event | `prompt_id` per user prompt; `tool_use_id` per tool call | `prompt_id` is the turn identity; a missing value stays `turn_uncertain` and never continues. |
| `Interrupt` event | None. `Stop` does not run on user interrupt; cancelling a tool fires no failure hook | Unsupported. No pause is invented from another signal. |
| Child hooks: `session_id`=root, `agent_id`=child | Subagent tool hooks add `agent_id` and `agent_type` | Same identity swap for guarded tools. Whether `session_id` equals the parent's is a probe (S0). |
| Output: `permissionDecision`, `decision:"block"`, `additionalContext`, `systemMessage` | Same subset. A designed Stop continuation is non-error feedback via `hookSpecificOutput.additionalContext`; `decision:"block"` shows as a hook error | Renderer is shared; Claude renders a bounded continuation as feedback. |
| Hook crash/timeout allows the action | Same by default; `onFailure:"block"` (v2.1.295+) can fail closed | Keep fail-open (parity). A plugin-wide fail-closed edit guard would block ordinary editing in unenrolled projects. |
| 128 KiB hook input bound | `Write` puts a whole file in `tool_input` | Claude runtime events read up to 8 MiB; larger input stays an explicit diagnostic. |
| `thread/name/set` any time | `sessionTitle` output only on `SessionStart` (startup, resume, fork) and `UserPromptSubmit`; `session_title` input there | Hook-mediated, parent titles only, delivered one prompt late. |
| `codex app-server`, rollout JSONL | Transcript JSONL is documented as internal and unstable; `SubagentStop` supplies `last_assistant_message` and `agent_transcript_path`; Agent SDK reads sessions | Native review/worker observation needs a new documented source and a registered `SubagentStop` hook. |

## Reviewer verdicts

Independent review, 8 November 2026. Package-level only; no native Claude run was made. **N** necessary, **X** unnecessary, **U** unsupported by the platform.

| ID | Capability | Verdict | Slice |
| --- | --- | --- | --- |
| P1–P5 | Runtime normalization, rendering, write guard, post-tool invalidation, bounded Stop continuation | N | S1 |
| P6 | Interrupt pauses the bound task | U: no interrupt hook; `is_interrupt` is a tool-level signal that does not fire on cancel | documented |
| P7, P8 | Child identity swap and parent authority; retained guard when authority is unavailable | N | S1 |
| P9 | Hook manifest, validator, package tests | N | S1 |
| P10 | Bash truncation notice | X: Codex exec marker; Claude's Bash response is an object | — |
| S1, S2 | Runtime scope gates in `startup.py`; Claude orientation line | N | after N4 |
| S3 | Client choice in task, evidence, delivery, delegation and doctor controls | N | S3 |
| S4 | `DONE`, `revalidate_ready`, evidence on Claude records | N; review-requiring plans stay unsatisfiable until N1 | S4 |
| S5 | `doctor` | N: `claude --version`, `--init-only`, plugin validation; `claude doctor` is unverified | S5 |
| S6 | Title Sync | N for the parent title; U for child titles and mid-turn writes | S6 |
| N1, N2 | Review collection, worker observation and dispatch controls | N via a registered `SubagentStop`/`SubagentStart` | S7 |
| N3 | Worker visibility, Habblaud | X: Codex Desktop start-event forwarder; display never blocks | — |
| N4 | Setup qualification and settings observation | N and a prerequisite for runtime activation | before S1 activation |
| N5 | Rollout accounting, exec-wrapper pairing, foreground wait, `fork_turns_none` | X: Codex internals | — |
| N6 | Capacity recovery, Codex usage-routing host | X | — |
| N7 | Guidance cache and Codex-home registries | N: neutralize paths, add Claude guidance | S8 |
| D1 | Client documentation and skill instructions | N | every slice |

Missed by the first matrix: **M1** `PostToolUseFailure` invalidation (N, in S1); **M2** oversized hook input (N, in S1); **M3** historical writer retirement (N, after N2); **M4** qualification child-patch harness (X); **M5** OpenRouter/external-route settings for a Claude coordinator (N, low priority); **M6** Codex instruction-output budgets and bootstrap reader (X); **M7** title notices on `PostToolUse`/`Stop` (U); **M8** `bashEditDiff` (X).

## Slice order

- **S0 (native probe, no code). Observed once** on Claude Code 2.1.311 with `haiku`, a disposable project and project-only settings (about $0.006): `prompt_id` was identical across main-session, subagent and Stop-continuation events, including the extra `UserPromptSubmit` the continuation produced; subagent tool hooks carry the parent's `session_id` plus their own `agent_id`; the first `Stop` has an explicit `stop_hook_active:false` and the continuation `true`; a hook `deny` blocked a write under `bypassPermissions`; a failed Bash command fired `PostToolUseFailure` with `error` and no `tool_response`. One run is not a distribution; payload sizes and the `onFailure` field on older versions were not probed.
- **S1: adapter, manifest, validator** (`PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `Stop`), M1 and M2. *Package behavior implemented.*
- **N4 qualification** *stage 1 implemented*: a real receipt from Claude's own hook event stream activates a qualification-only scope with only `startup-context` verified. Managed successors for pre-tool, post-tool and stop behavior are not implemented.
- **S3 task controls** (`start`, `update`, `status`, `title`) *implemented*; **S4 evidence** *implemented at package level*: plan, check, reconcile, verify, cleanup and status work for Claude and `DONE` is revalidated; review-requiring plans stay unsatisfiable. Delegation and retirement stay refused. **Scope of that claim:** it holds on scopes without a capability projection (fixtures). A real scope with a stage-1 receipt refuses `task-start`, `task-patch`, `check-run` and `verify-completion` until their capabilities are verified (observed), so Claude cannot yet complete work in a real qualified project.
- **S5 doctor** *implemented*: version, installed and enabled state, hook locks; hook trust and execution cannot be observed, so a healthy result exits 2.
- **N1/N2 observation source** *implemented*: hook-recorded spawn chains and collectors for the review receipt and worker facts. *Not implemented:* the D6 ledger integration (guidance, routes, `_prepare_native`, receipt pins, delegation CLI), then M3.
- **N7, then M5.**
- **S6 parent title** *implemented*. Native finding: Claude delivers and returns a hook-set title but strips the leading `U+FFFB`, so the transport delivers without it and canonicalizes observations; an exact-equality acknowledgment would have paused every title as a manual rename.
- **D1** with every slice.

## Delivery rules

One reviewable change per slice, on the task branch, with focused tests first. Independent review applies to each slice before merge. Unit tests do not establish native execution: each slice records an observed `claude --init-only` or live-session probe separately, or states that none was run.
