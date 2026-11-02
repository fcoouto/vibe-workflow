# Workstreams title synchronization

Load for adopting a managed title, recording task progress, resolving a manual
rename, or diagnosing missed title updates. The [naming contract](workstreams.md#names-identify-the-parent-and-its-children)
owns the format. The task's canonical state is rendered verbatim; there is no
separate display-status list.

## Adopt a parent or child

After the workflow task is bound, configure its title once through the enrolled
package. Use the current task identity and revision:

```text
python3 -I -B <package>/scripts/workflow.py task title configure --cwd <checkout> < title.json
```

Parent request:

```json
{
  "client": "codex",
  "session_id": "<native_thread_id>",
  "task_id": "<bound_task_id>",
  "scope_ref": "<bound_scope_reference>",
  "expected_revision": 1,
  "reference": "<user_or_workflow_decision>",
  "topic": "Thread naming"
}
```

For an explicitly associated child, also provide `parent_session_id` and `member`
(for example `C1`). The parent session must differ from the child's session;
`P` is reserved for the family's own parent. Allocate each child ordinal once in
the existing task plan. These labels never establish native lineage, ownership or
dispatch authority. Existing delegation admission remains required.

Configuration reserves the shared family code, records the current title, then
synchronizes the managed format. The same bound task cannot be configured twice. After an authorized task
replacement in the same native thread, an explicit configuration or resumption
request rebinds presentation to the new task and archives the old presentation.
The family/member identity remains stable; an existing manual-title pause survives
configuration until the user explicitly requests resumption. Unmanaged threads and unrelated chats stay outside
automatic title synchronization.

## Record status and accepted progress

During normal work, the coordinator records a task transition when the user's
instruction, acceptance or next action changes. Discussion, design, investigation,
implementation and fixes use `WORKING`. A concrete result awaiting acceptance uses
`REVIEW`. Approval that starts another agreed phase returns to `WORKING`; it does
not complete the whole task. A neutral acknowledgment changes no state.

Use the existing task update command and its revision checks. Optional
`milestones` entries are exactly `{id, weight, accepted, reference}`. IDs are
unique; weights are positive bounded integers; accepted is boolean; references
record the coordinator's acceptance decision. Keep milestones in this task plan,
including required review and integration. Do not accept a worker's self-report
as independent acceptance or count child work twice.

The title meter rounds accepted weighted progress down to completed fifths,
with at most four filled cells before `DONE`. No measurable milestones means an
empty meter. `DONE` always uses five filled cells and still requires the normal
completion evidence gate. Reopening work retains accepted milestones unless
scope or evidence invalidates them.

## Event repair and failures

Committed task, evidence and delegation commands repair an adopted title. Existing
`PostToolUse`, `Stop` and `SessionStart`/resume handlers provide additional repair
opportunities. The synchronizer uses the supported Codex App Server protocol,
without starting a model turn. Interrupt's short deadline leaves repair for the
next event. Stop does not imply completion.

Completion evidence is revalidated before deciding whether a title is unchanged,
so changed candidates cannot retain a stale full-meter DONE. Ordinary commands
skip native calls when revisions are unchanged and no delivery is pending.
Explicit repair and eligible hooks still check for manual edits.
A per-thread lock serializes participating writers. Desired titles are persisted
before writing; readback acknowledges delivery. Lost acknowledgments, changed
revisions, unavailable clients and timeouts leave repair for the next event.
There is no idle watcher, polling loop or automatic retry model turn. Presentation
failures preserve task outcomes, guard decisions and continuation limits.

Private sidecar files in the existing runtime state directory contain title
presentation and delivery metadata only. Task state and accepted milestones remain
in the authoritative task record. Historical task pins and closed evidence are
not modified by title delivery.

## Manual titles

Before an automatic write, reconcile any pending delivery and compare the current
title with the last verified managed title.

- If only the topic changed and the format is unambiguous, adopt the topic and
  continue meter/status updates. Queued updates use the new topic.
- If meter, family, member, status or overall format changed, preserve the title
  and pause title synchronization for that thread. Announce the pause once.
- Resume only after an explicit user request. Use `task title resume` with the
  current task identity, revision and decision reference. Optional `topic`
  supplies the user's intended wording; other managed fields come from authority.

Pausing title synchronization does not pause the task. A manually typed `DONE`
cannot complete work or change family identity or permissions. New turns, runtime
restarts and milestones do not silently resume title synchronization. A concurrent
manual edit can still race a native write; the client currently provides no
verified compare-and-set operation. Detect observed conflicts and preserve them.

For a presentation read, use `task title status`; for explicit repair, use
`task title sync`. These take only `{client, session_id}`. Configuration and
resumption require the complete identity/revision request above. Resume retains
the original family/member binding.

## Update and qualification boundary

The task vocabulary is a pinned runtime change. Legacy lowercase records remain
historical; old `ready` meant completion, while new `READY` means eligible to
begin. Do not translate old titles into task state or rewrite old records/pins.
Reconcile affected owners with their original runtime and use the existing
[approved runtime update](runtime.md#change-a-pinned-runtime) procedure with a fresh
state authority. Existing ownership and unresolved assignments remain binding.

Formatting, state, command and hook-boundary tests qualify source behavior.
Installed hook trust and callback delivery, rendering and literal title search
require separate observation. A standalone rename or package validation does not
establish those capabilities.
