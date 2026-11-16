# OpenCode context reminders

Load this file when you test or diagnose the experimental OpenCode task-record reminder. Status: **prototype, inactive by default, not qualified for production.** Its native effect on agent behaviour is under evaluation; unit tests prove only the adapter's own behaviour.

## What it does

The plugin in [`runtime/adapters/opencode_workflow/`](../runtime/adapters/opencode_workflow/context.ts) adds one short system block to each model request of one explicitly bound root session (OpenCode's `context` hook, which covers the agent loop and tool-driven continuations). The block names the task record and tells the agent to reconcile it with newer user instructions and actual results at each checkpoint, to perform a next action that is its own, authorized, ready and within the original limits, and otherwise to state the specific hold. It is not authorization: a pause, an exhausted allowance or a genuine blocker still wins, and a completed check is not repeated because the record is stale.

- **Pointer only.** The hook never reads the record. It only checks, on each request, that the record is a regular file below the bound checkout, reached through no symbolic link. A missing or unsafe record adds nothing and records one short code.
- **Bounded.** The block is at most 1,536 bytes; the record path is JSON-quoted data.
- **No scheduling.** It makes no model call, prompt, synthetic message, child session, retry or timer. It starts nothing when the agent is idle.
- **Independent of the [escalation gate](opencode-escalation-gate.md).** It works with the gate off, and fails open: any error leaves the request unchanged.

## Binding

Nothing is discovered. A test launcher supplies the binding as the plugin option `contextReminderTestBinding`:

```jsonc
{ "assignment": "task-1", "checkout": "/abs/checkout", "record": "PLAN.md", "sessionID": "ses_…" }
```

`record` is relative to `checkout` and must not escape it. `sessionID` is optional; without it, the root session (no parent) whose directory is `checkout` matches, so the checkout must be dedicated to one assignment. Child sessions, other directories and an unbound plugin are unchanged. Ending the assignment means removing the option or unloading the plugin; a reload clears all state. A missing or invalid binding leaves the feature inactive.

## Limits

- Not a security or enforcement boundary; the agent may ignore the block.
- It adds up to 1,536 bytes of context to every request of the bound root, which is a recurring token cost.
- Documentation describes the hook; local delivery, ordering and the final outgoing request are checked only by native qualification.
- A pause that arrives while a request is being assembled is governed by OpenCode's own controls, not by this plugin.
