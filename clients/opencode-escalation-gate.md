# OpenCode escalation gate

Load this file when you install, configure, test or diagnose the optional OpenCode stuck-pattern escalation gate. It supports [stuck-pattern escalation](../policies/03-agent-delegation.md#stuck-pattern-escalation); it does not replace that procedure, and the agent still owns detection the gate cannot see. The design is in [the escalation gate design](../docs/superpowers/specs/2026-11-12-opencode-escalation-gate-design.md).

**Status: selected mechanical paths qualified on OpenCode 2.2.6 with released 0.1.39. Six original native cases passed, plus a later actual standalone TUI flow covering rendered refusal, native child permission identity and event ordering. Two instrumented native fault instances passed separately. The TUI follow-up required a fixture-only read-only tool permission correction and forced frontend shutdown; shared-server TUI attachment and general workflow improvement are not qualified. Earlier failures remain recorded; neither general semantic detection nor the deferred judge is qualified. See the [13 November report](../docs/qualification/2026-11-13-opencode-escalation-gate.md), [earlier behavior report](../docs/qualification/2026-11-12-opencode-escalation-gate.md) and [hook results](../tests/fixtures/opencode_workflow/probes.json).**

**Unreleased behavior change:** the final stop now denies edits rather than
offering approval. Late completed edits cannot clear it. Separate explicit user
direction through the existing root-message control is required before another
edit; no pending edit resumes automatically. A separately authorized private
account-free follow-up on 15 November passed the selected native maximum-effort
flow, including repeated root/child stop denials and named continuation without
automatic edits. The earlier transport failure remains recorded; see the
[qualification report](../docs/qualification/2026-11-14-unconditional-stop.md#fresh-authorized-follow-up--15-november).
The custom approval-panel work in PR #294 remains deferred,
draft/BLOCKED, unmerged and unreleased.
The later 15 November UI/race attempt stopped at a timing-RPC HTTP 400 before
either case executed. It also observed a changed shared-settings hash with
unknown attribution. Neither native race nor new TUI coverage is qualified;
see the [attempt report](../docs/qualification/2026-11-14-unconditional-stop.md#ui-and-native-race-attempt--15-november).
The user's Preserve-login decision holds Claude testing and release.
A separately approved model-free probe later reproduced rejection of the timing
RPC's JSON Schema `pattern` keyword. That is a diagnostic-harness failure, not a
stop-gate denial; native UI/race qualification is still pending. See
[the diagnosis](../docs/qualification/2026-11-14-unconditional-stop.md#model-free-timing-rpc-diagnosis--15-november).
A later authorized fix removed only that keyword from a fresh disposable helper.
Offline validation and one model-free native arm confirmation passed; no OpenCode
binary, user setting or production gate source changed. See
[the confirmation](../docs/qualification/2026-11-14-unconditional-stop.md#fixed-helper-model-free-confirmation--15-november).
A later approved race-only preparation passed its offline checks, but independent
prelaunch review found unbounded controller waits and an unhandled normal EOF in
the qualification observer. Launch was blocked and that allocation closed, with
no native attempt or model request. These are harness findings, not demonstrated
production-gate defects; see
[the prelaunch result](../docs/qualification/2026-11-14-unconditional-stop.md#race-only-preparation-blocked-at-prelaunch-review--15-november).
Separately authorized preparation-only fixes later passed 37 offline checks and
Astra's bounded review. R1/R2 are resolved in a fresh harness copy at code/offline
level only; the rejected case remains preserved and closed. No native attempt
was authorized or launched by that repair; see
[the repair result](../docs/qualification/2026-11-14-unconditional-stop.md#preparation-only-r1r2-fixes--15-november).
A separately approved fresh race then passed prelaunch review but failed its
initial status snapshot: the observer found no status tool or executor. It used zero
model requests; no race step ran. Cause remains unresolved, and plugin-active
metadata alone did not prove gate readiness. Cleanup and selected preservation
checks passed. Astra accepted the bounded failed-run record, not native race
qualification; the allocation is closed without retry. See
[the latest result](../docs/qualification/2026-11-14-unconditional-stop.md#fresh-race-only-attempt--15-november).
A subsequent offline diagnosis reproduced a missing fixture prerequisite: without
a Git checkout, feature resolution fails and the adapter registers no status
tool. A Git-backed identical control resolves the feature and registers the tool.
No failed fixture or production source was repaired, and native qualification
remains incomplete; see
[the diagnosis](../docs/qualification/2026-11-14-unconditional-stop.md#offline-gate-readiness-diagnosis--15-november).
A Git-backed fixture with a feature preflight and bounded readiness wait then
passed the status check, and the single approved race failed at round 1 on a
sandbox DNS error (`getaddrinfo ENOTFOUND`) after one model request, before any
race step. A model-free check showed the restricted profile blocks name resolution
and one added resolver-socket allowance restores it; it is unapplied. Native race
qualification remains incomplete; see
[the attempt](../docs/qualification/2026-11-14-unconditional-stop.md#git-backed-race-attempt--16-november) and
[the DNS diagnosis](../docs/qualification/2026-11-14-unconditional-stop.md#sandbox-dns-diagnosis--16-november).
After a reviewed resolver-socket profile rule, a counted retry allowance (two,
transport only) and rejection of non-primary request kinds, the native
late-completion race **passed** as mechanical evidence for an instrumented run
(49 requests, 117 s): the held successful callback was delivered after the stop,
the stop and bytes were preserved, a fresh child edit was denied without an
approval prompt, and an unrelated edit succeeded. Desktop and Claude
behavior, uninstrumented scheduling and release remain unqualified; see
[the passed race](../docs/qualification/2026-11-14-unconditional-stop.md#native-race-passed--16-november).
The original OpenCode TUI case also passed under a limited reconstructed-screen claim at
48×200: a refusal visibly associated by layout, unique input and turn order with the
denied edit (no renderer-ID binding), a typed reset releasing only the named file, and a
repair with the sibling still stopped; see
[the original TUI case](../docs/qualification/2026-11-14-unconditional-stop.md#original-tui-case-passed--16-november).

## What it does

The plugin in [`runtime/adapters/opencode_workflow/`](../runtime/adapters/opencode_workflow/index.ts) watches one tree per interactive root session: the session and its subagents. Ledger-derived sessions belong to the Smart Routing shim and are ignored. It makes no model calls and writes nothing while nothing is held.

- **Signals.** Edits the host lets run (permission requests for `edit`, `write` or `patch`, confirmed by the completed tool call); failed check runs (a shell command matching a check pattern, with a tool error, non-zero exit status or a complete, attributable unsuccessful unittest footer); and finished subagents that made no edits (a review return). A review return counts only for files edited before that subagent was created. Each session's level is the route of its latest `primary` model request.
- **Trip.** A repair round on a file is a re-edit after a failure. After two rounds and a new failure, the file is held. Passing checks never clear rounds; a second failure without a repair in between does.
- **Hold.** Edits of a held file by any session in the tree are denied, with a message naming the files, rounds, stage and next step. Everything else passes.
- **Release.** A new subagent, created after the hold, that made no edits and finished successfully releases a file when its route is stronger than every level on the file. Where two routes cannot be ranked, a different route is enough. Same-level reviews never release.
- **Stages.** Up to three escalations; then, or when no ranked option is above every level, the architecture re-evaluation: a new read-only session at least as strong as every level releases the file and resets its count once. After that, three more escalations or no stronger option stop further edits. Repeated attempts, later diagnoses and late completed edits cannot clear that stop; the user must separately direct continuation through the existing control below.
- **Ranking.** The `ladder` setting lists `provider/model` entries, weakest first. Within one model, variants rank `low`, `medium`, `high`, `xhigh`, `max`. Anything else is unranked.

## Reset and troubleshooting

- **Reset by message.** While something is held, a message from the user in the main session asking to reset the gate, counter, hold or escalation clears the named files, or every held file when none is named. The plugin appends `[escalation gate: counter reset by user for <files>]` to that message. Messages in subagent sessions and negated requests ("don't reset") never reset. The agent cannot clear its own hold.
- **Status.** While the gate is active, the read-only tool `escalation_gate_status` reports held files, rounds, stage, escalations, levels and the last internal error.

## Install and configure

Add the plugin directory to the user's OpenCode configuration and enable the feature for the project:

```jsonc
{ "plugins": ["<package>/runtime/adapters/opencode_workflow"] }
```

```text
python3 -I -B <package>/scripts/workflow.py features enable escalation_gate --cwd <checkout>
python3 -I -B <package>/scripts/workflow.py features set escalation_gate ladder '["<provider>/<model>", "..."]' --cwd <checkout>
```

The feature is off by default, needs no consent and changes a tracked file, so change it in a task worktree. The plugin reads it when it loads: after a change, use `/reload` or start a new OpenCode server. A new session on an already-running server may keep the old setting. `check_patterns` replaces the default list of check commands.

## Limits

- Not a security boundary: writes through the shell, `` !`cmd` `` command templates, other plugins and the server API are not seen. A plugin that fails to load enforces nothing, and any internal error allows the action.
- State lives in memory; a plugin reload starts afresh.
- An unavailable or lost session-event stream disables enforcement for that plugin instance, including loss before feature loading completes. Reload to start a fresh instance; the plugin does not reconnect. Cleanup also prevents late activation.
- Loops whose repairs move to files never repaired before are not detected.
- A shell pipeline can hide a runner's failure exit. Output-only detection requires complete unittest footers, one per recognized literal check invocation, with the last footer ending captured output. Positive failures, errors or unexpected successes count; expected failures and zero-only counts do not. Multiple runs count at most once per shell event.
- Output attribution supports literal check sequences, option-free wrapper prefixes, environment assignments, optional `2>&1` and a single stdin-only `tail -N` or `tail -n N`. Mixed viewer/check commands, wrapper options, quoted or substituted syntax, unsupported filters, malformed footers and mismatched run counts remain unknown. Non-zero exits and tool errors still count through the ordinary check classifier.
- This is consistency checking, not output authentication. A check can print a convincing footer. Truncated output and unsupported frameworks may hide failures; the agent still owns detection outside this narrow subset.
- Observed on 2.2.6: shell exit status is `result.metadata.exit`; permission `source.id` matches the completed call's `id`; denial messages reach the model; successful-completion events carry child identities. The earlier CLI trace saw the child prompt before creation and no root creation event, requiring `session.get`. The standalone TUI follow-up observed root creation, child creation before its prompt, rendered gate refusal and native subagent edit permission carrying the child's identity and sharing the root hold. Event ordering is surface-dependent. Shared-server TUI attachment, clean frontend exit and a TUI stop-stage approval dialog remain unqualified.
