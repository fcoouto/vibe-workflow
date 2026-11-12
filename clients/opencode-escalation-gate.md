# OpenCode escalation gate

Load this file when you install, configure, test or diagnose the optional OpenCode stuck-pattern escalation gate. It supports [stuck-pattern escalation](../policies/03-agent-delegation.md#stuck-pattern-escalation); it does not replace that procedure, and the agent still owns detection the gate cannot see. The design is in [the escalation gate design](../docs/superpowers/specs/2026-11-12-opencode-escalation-gate-design.md).

**Status: package-tested only; live probes on OpenCode pending.** Nothing here establishes observed OpenCode execution.

## What it does

The plugin in [`runtime/adapters/opencode_workflow/`](../runtime/adapters/opencode_workflow/index.ts) watches one tree per interactive root session: the session and its subagents. Ledger-derived sessions belong to the Smart Routing shim and are ignored. It makes no model calls and writes nothing while nothing is held.

- **Signals.** Edits the host lets run (permission requests for `edit`, `write` or `patch`, confirmed by the completed tool call); failed check runs (a shell command matching a check pattern, with a non-zero exit status); and finished subagents that made no edits (a review return). A review return counts only for files edited before that subagent was created. Each session's level is the route of its latest `primary` model request.
- **Trip.** A repair round on a file is a re-edit after a failure. After two rounds and a new failure, the file is held. Passing checks never clear rounds; a second failure without a repair in between does.
- **Hold.** Edits of a held file by any session in the tree are denied, with a message naming the files, rounds, stage and next step. Everything else passes.
- **Release.** A new subagent, created after the hold, that made no edits and finished successfully releases a file when its route is stronger than every level on the file. Where two routes cannot be ranked, a different route is enough. Same-level reviews never release.
- **Stages.** Up to three escalations; then, or when no ranked option is above every level, the architecture re-evaluation: a new read-only session at least as strong as every level releases the file and resets its count once. After that, three more escalations or no stronger option turn the hold into an approval prompt; an approved edit starts the file afresh.
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
- Loops whose repairs move to files never repaired before are not detected.
- The shell exit-status field, call-id correlation, child-session events and prompt-hook scope are assumed from the documentation and earlier probes; the live probes must confirm them before the gate is described as working.
