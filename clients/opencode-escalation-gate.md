# OpenCode escalation gate

Load this file when you install, configure, test or diagnose the optional OpenCode stuck-pattern escalation gate. It supports [stuck-pattern escalation](../policies/03-agent-delegation.md#stuck-pattern-escalation); it does not replace that procedure, and the agent still owns detection the gate cannot see. The design is in [the escalation gate design](../docs/superpowers/specs/2026-11-12-opencode-escalation-gate-design.md).

**Status: selected mechanical paths qualified on OpenCode 2.2.6 with released 0.1.39. Six original native cases passed, plus a later actual standalone TUI flow covering rendered refusal, native child permission identity and event ordering. Two instrumented native fault instances passed separately. The TUI follow-up required a fixture-only read-only tool permission correction and forced frontend shutdown; shared-server TUI attachment and general workflow improvement are not qualified. Earlier failures remain recorded; neither general semantic detection nor the deferred judge is qualified. See the [13 November report](../docs/qualification/2026-11-13-opencode-escalation-gate.md), [earlier behavior report](../docs/qualification/2026-11-12-opencode-escalation-gate.md) and [hook results](../tests/fixtures/opencode_workflow/probes.json).**

**Unreleased candidate:** the [approved no-concurrent-external-writer design](../docs/superpowers/specs/2026-11-13-opencode-stop-form-design.md)
now has deterministic adapter/state-machine regression coverage. Stop-stage native
approval alone no longer clears a hold. The default candidate has **no qualified
authenticated form transport**: stopped edits deny with
`stop_form_bridge_unavailable`; the existing root-message reset remains available.
The form dependency seam supports offline tests and future qualified embedding,
not a new user setting or a working installed bridge. No live qualification,
manual UI proof, installation update or release follows from those tests. PR #294
stays draft/BLOCKED. The released observations above do not qualify this change.

## What it does

The plugin in [`runtime/adapters/opencode_workflow/`](../runtime/adapters/opencode_workflow/index.ts) watches one tree per interactive root session: the session and its subagents. Ledger-derived sessions belong to the Smart Routing shim and are ignored. It makes no model calls and writes nothing while nothing is held.

- **Signals.** Edits the host lets run (permission requests for `edit`, `write` or `patch`, confirmed by the completed tool call); failed check runs (a shell command matching a check pattern, with a tool error, non-zero exit status or a complete, attributable unsuccessful unittest footer); and finished subagents that made no edits (a review return). A review return counts only for files edited before that subagent was created. Each session's level is the route of its latest `primary` model request.
- **Trip.** A repair round on a file is a re-edit after a failure. After two rounds and a new failure, the file is held. Passing checks never clear rounds; a second failure without a repair in between does.
- **Hold.** Edits of a held file by any session in the tree are denied, with a message naming the files, rounds, stage and next step. Everything else passes.
- **Release.** A new subagent, created after the hold, that made no edits and finished successfully releases a file when its route is stronger than every level on the file. Where two routes cannot be ranked, a different route is enough. Same-level reviews never release.
- **Stages.** Up to three escalations; then, or when no ranked option is above every level, the architecture re-evaluation: a new read-only session at least as strong as every level releases the file and resets its count once. After that, three more escalations or no stronger option require a distinct form decision. In the candidate, only its matching successful native edit starts the file afresh; missing form capability keeps the edit denied.
- **Ranking.** The `ladder` setting lists `provider/model` entries, weakest first. Within one model, variants rank `low`, `medium`, `high`, `xhigh`, `max`. Anything else is unranked.

## Reset and troubleshooting

- **Reset by message.** While something is held, a message from the user in the main session asking to reset the gate, counter, hold or escalation clears the named files, or every held file when none is named. The plugin appends `[escalation gate: counter reset by user for <files>]` to that message. Messages in subagent sessions and negated requests ("don't reset") never reset. The agent cannot clear its own hold.
- **Status.** While the gate is active, the read-only tool `escalation_gate_status` reports held files, rounds, stage, escalations, levels and the last internal error.

## Candidate stop-form behavior

- Only one existing regular non-symlink UTF-8 text file through the observed native
  `edit` tool is supported. File limit: 1 MiB; complete native change metadata limit:
  64 KiB. Unsupported stopped tools, multi-file requests and malformed metadata deny.
- A form decision binds generation, tree, native session/message/call, hold
  incarnation, complete native proposed change and hook-observed file digest.
  Exact authoritative approval restores the incoming native allow/ask effect;
  it never overrides a configured deny or grants broader permissions.
- Pending, reject, cancel, stale/foreign/mutated state, repeated authorization,
  interruption, bridge failure and unconfirmed settlement deny. A terminal event
  permits authoritative readback and at most one extra confirmation read; no
  periodic polling or create retry. Target occupancy lasts through native
  permission wait and execution. Failed or unrelated completions never clear it.
- Acquisition stops at 110 seconds; final 10 seconds of a 120-second form-operation
  budget are reserved for owned cleanup. Up to six form operations, with cleanup
  capacity reserved. A later native permission wait/execution is separate.
  Unconfirmed cleanup reports `stop_form_cleanup_unconfirmed`; it does not renew
  budgets or authorize an edit. A late-created form can remain unconfirmed.
- The approved first milestone assumes no concurrent external writer from native
  preparation through commit. Stable hook snapshots do not authenticate the
  executor's earlier complete before-image. A form answer is automation-capable
  workflow data, not proof of a physical-human decision.
- Tests use fake native form responses and temporary local files, not a custom
  production writer. Authenticated same-service access, complete client display,
  scheduling during a native permission hook and cleanup still need live proof.

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

- Not a security boundary: writes through the shell, `` !`cmd` `` command templates, other plugins and the server API are not seen. A plugin that fails to load enforces nothing. Ordinary internal errors fail open; captured known-stop operations remain denied if their form prerequisite fails.
- State lives in memory; a plugin reload starts afresh.
- An unavailable or lost session-event stream disables enforcement for that plugin instance, including loss before feature loading completes. Reload to start a fresh instance; the plugin does not reconnect. Cleanup also prevents late activation.
- Loops whose repairs move to files never repaired before are not detected.
- A shell pipeline can hide a runner's failure exit. Output-only detection requires complete unittest footers, one per recognized literal check invocation, with the last footer ending captured output. Positive failures, errors or unexpected successes count; expected failures and zero-only counts do not. Multiple runs count at most once per shell event.
- Output attribution supports literal check sequences, option-free wrapper prefixes, environment assignments, optional `2>&1` and a single stdin-only `tail -N` or `tail -n N`. Mixed viewer/check commands, wrapper options, quoted or substituted syntax, unsupported filters, malformed footers and mismatched run counts remain unknown. Non-zero exits and tool errors still count through the ordinary check classifier.
- This is consistency checking, not output authentication. A check can print a convincing footer. Truncated output and unsupported frameworks may hide failures; the agent still owns detection outside this narrow subset.
- Observed on 2.2.6: shell exit status is `result.metadata.exit`; permission `source.id` matches the completed call's `id`; denial messages reach the model; successful-completion events carry child identities. The earlier CLI trace saw the child prompt before creation and no root creation event, requiring `session.get`. The standalone TUI follow-up observed root creation, child creation before its prompt, rendered gate refusal and native subagent edit permission carrying the child's identity and sharing the root hold. Event ordering is surface-dependent. Shared-server TUI attachment, clean frontend exit and a TUI stop-stage approval dialog remain unqualified.
