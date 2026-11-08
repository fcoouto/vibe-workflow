# Thread Naming title synchronization

Load for eligible implementation naming, adopting a managed title, recording
task progress, resolving a manual rename, or diagnosing missed title updates.
The [naming contract](workstreams-naming.md) owns the format, attention rules
and adoption trigger.

## Adopt a parent or child

First apply the [new-thread scope](workstreams-naming.md#new-thread-scope), then
resolve the [display-family association](workstreams-naming.md#resolve-the-display-family),
then inspect the existing title and recorded presentation ownership. Reuse the
adopted mapping, reconcile manual edits before writing, and
retain any manual-title pause. A known user-selected title is an override even
before managed adoption; preserve it until the user explicitly requests adoption.
Explicit title instructions and recorded pauses also take precedence over
automatic adoption.

At the [implementation naming trigger](workstreams-naming.md#automatic-implementation-naming),
the naming policy authorizes a first managed title for an eligible context even
if earlier turns were planning. This permission applies to the current
implementation family and does not establish who set the current title.
Do not require a native title-author signal. Outside this trigger, preserve an
existing title of unknown ownership unless the user explicitly requests adoption.
An eligible context's independently observed automatic/default title also
permits adoption when no known override or pause applies.

Observe the initial title, then re-read it after preparation immediately before
writing. Preserve any observed intervening change instead of overwriting it.
The first successful write and matching readback establish the last verified
managed title. Retain an uncertain write as pending and reconcile it against that
same native identity; do not restart adoption or reset the baseline to each new
observation.

When a workflow task is already bound, configure an eligible title once through
the enrolled package, using the naming rule as the workflow decision
reference. Use the current task identity and revision:

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
  "workflow_topic": "Thread naming"
}
```

Use `workflow_topic` for text chosen by the coordinator. Use `topic` for an
explicit user choice. Supply only one. The saved presentation records workflow
ownership when established. Older records without that observation retain their
topic during an automatic switch. `workflow_topic` is a configure input only.

For an explicitly associated member, supply its recorded root coordinator UUID as
`parent_session_id` and `member: "C"`. Store and display the same literal `P` or
`C` role; the actual native UUID identifies the context.
Title controls use `parent_session_id` for the display root; delegation and worker
visibility use that field for the actual native parent. This display binding must
satisfy the selected runtime's validation; it never changes native lineage,
ownership or dispatch authority. Existing delegation admission remains required.

Configuration resolves the declared root's shared family code, records the current
title, then synchronizes the managed format. The same bound task cannot be configured twice. After an authorized task
replacement in the same native thread, an explicit configuration or resumption
request rebinds presentation to the new task and archives the old presentation.
The family/member binding remains stable; an existing manual-title pause survives
configuration until the user explicitly requests resumption. Unmanaged threads and unrelated chats stay outside
automatic title synchronization.

Choose the path per native thread: a bound parent does not bind its workers or
reviewers. For an already-adopted bound thread, use `task title sync` rather than
configure again. A failed runtime synchronization retains its pending state;
do not bypass it with a direct write.

For ordinary native work without a bound runtime task, reuse known supported
client title controls after observing the actual native identity. If discovery
is needed, list tool names within the current client's namespace, then inspect
only the selected read/write definitions; do not print cross-service metadata.
Keep the family/member mapping, last verified managed title, pending delivery, topic
ownership and any manual-title pause in the existing task plan. Record workflow
ownership only for a topic the workflow chose. Apply the manual-title rules below
before every write and acknowledge delivery by readback. Resolve an uncertain
write against that same identity before retrying. Update titles during existing
milestone, phase and blocker handling using accepted plan progress. Do not create
a runtime task or runtime database, watcher or extra model turn solely for naming. This
direct path does not acquire the runtime event-repair guarantees below.

### OpenCode sessions

OpenCode titles are supported only on this ordinary path; there is no
runtime-bound OpenCode title sync until the Smart Routing S3 slice. Pass the
`ses_` session ID, the formatted title and, once one exists, the last verified
managed title as `baseline`:

```text
python3 -I -B <package>/scripts/opencode_title.py < title.json
```

```json
{"session_id": "<ses_id>", "title": "<formatted title>", "baseline": "<last verified title>"}
```

Use the session ID from the plugin hook context (the `sessionID` on the tool
or model event for that session), or the ID recorded for a child in your task
plan. Do not use the `OPENCODE_SESSION_ID` environment variable: OpenCode 2.2.6
does not set it, so any value is inherited from whatever launched the server and
can name an unrelated session. Until the routing plugin or a recorded handoff
supplies an ID, an agent cannot title its own session; this path then does
nothing. Never pick a session from
`opencode session list` or by any other guess: the command only checks that
OpenCode answered for the ID you passed, not that it is the right session, so a
wrong ID could rename another chat.

The command reads and writes only the session title through `opencode api`
and never starts a model turn. It always exits successfully and prints at most
one JSON line. `{"ok": true}` means readback confirmed the title; record its
`baseline` value as the new baseline.

A failed rename never interrupts the chat or appears as an error to the user.
The command retries a failed or timed-out call up to three times within a few
seconds, then returns `deferred: true` with a short `reason`. A missing
`opencode` program, missing session or unexpected response is not retried.
Keep the plan unchanged and deliver the title again at the next update. The
`manual_title` and `topic_changed` reasons come from the
[manual title rules](#manual-titles): record the pause, or adopt the returned
`topic`.

Without a baseline, the command writes only over an empty title, OpenCode's
default `New session - <time>` or `Child session - <time>` title, or a title in
the managed format. A managed-format title may belong to another family, such
as `W2`; replacing it re-adopts the session after a lost baseline. Any other
existing title counts as a manual title.

Add `"adopt": true` to one request without a baseline when the
[implementation naming trigger](workstreams-naming.md#automatic-implementation-naming)
applies or the user explicitly asks to adopt or rename the session. That
request may replace the title it first observes, such as OpenCode's generated
summary, and returns the replaced title as `previous` so you can restore it if
the user objects. `previous` is `null` when nothing was replaced or the old
title was too long to return. Never save or repeat `adopt`; later requests pass
the returned baseline, so any title the workflow did not write counts as manual
and is not overwritten. A non-boolean `adopt`, or `adopt` with a baseline, is
rejected as `invalid_request`.

OpenCode has no compare-and-set title update, so a rename made between the
command's read and its write, usually a fraction of a second, can be
overwritten. Readback catches a rename that lands after the write.

Only tests with a fake `opencode` executable check this command, not OpenCode
itself.

## Record status and accepted progress

For an adopted runtime-bound thread, the coordinator records a task transition when the
user's instruction, acceptance or next action changes. Discussion, design, investigation,
implementation and fixes use `WORKING`. A concrete result awaiting acceptance uses
`REVIEW`. Approval that starts another agreed phase returns to `WORKING`; it does
not complete the whole task. A neutral acknowledgment changes no state.

Apply the [PR merge gate](workstreams-naming.md#status-and-accepted-progress) to
every PR the thread opened or took over; for required PR delivery also use
[delivery completion](delivery.md#completion-and-retention).
The existing `WAITING` state covers merge waits; its reason identifies the dependency.
One or several PRs do not change the title format. A child's wait never assigns
the parent's state or progress; the parent follows its own accepted scope.

For direct display controls, use the same phase meanings and next-action owner
in the existing task plan without creating runtime state. For runtime-bound
threads, pass `user_attention: true` in the revision-checked task update when
the [attention rules](workstreams-naming.md#status-and-accepted-progress) apply.
Use a same-state update if the next action changes without a phase change. Pass
`user_attention: false` to suppress a generated label when the next action
belongs elsewhere.
Generated waits for manual merge use the retained delivery plan and readiness.
Optional `milestones` entries are exactly `{id, weight, accepted, reference}`. IDs are
unique; weights are positive bounded integers; accepted is boolean; references
record the coordinator's acceptance decision. Keep milestones in this task plan,
including required review and integration. Do not accept a worker's self-report
as independent acceptance or count child work twice.

Use the [meter rules](workstreams-naming.md#status-and-accepted-progress) for both title
paths. The activity indicator for unmeasured `WORKING` belongs only to the title.
`DONE` still requires the normal completion evidence gate and the PR merge gate;
observe each PR's remote state before any `DONE` write or completion
verification, on either path. Reopening work retains accepted milestones unless
scope or evidence invalidates them.

### Reopen a completed objective

For an adopted chat with a completed objective, classify the user's request
before the first substantive action, including investigation, review, edits or
delegation.

- **Continue or correct the same objective.** Record the actual phase, normally
  `WORKING`. Keep accepted milestones that still apply, add the agreed
  remaining work and recalculate the meter.
- **Clearly start a different objective here.** Switch automatically. Keep the
  same family and member. Start the new objective with zero accepted milestones
  and retain the old objective and evidence in history.
- **Ask about the completed result or acknowledge it.** Keep the full meter.
- **The requested work could belong to either objective.** Ask which one before
  switching or starting work.

For ordinary native work, record the phase and progress in the existing plan
and use the direct title procedure. For a runtime-bound continuation, use
`task update` with the current revision. For a runtime-bound switch, use
`task start` with a new task identity and `replaces` containing the current
`task_id` and `expected_revision`. The clear work request supplies the scope
decision reference. Runtime authority and replacement prerequisites still
apply. Record the new phase, then use `task title configure` with the new
binding and its current revision.

Reconcile pending delivery and compare the observed title with the last
verified title before choosing the topic. During a switch, use a short topic
for the new objective only when the workflow owns the current topic. For
runtime-bound titles pass that proposed text as `workflow_topic` so configure
can recheck ownership against the observed title. If ownership is unknown,
preserve the current topic. Apply the [manual title rules](#manual-titles).

Attempt title delivery before substantive work. A display failure alone does
not block authorized work. On an uncertain rebind, inspect the task binding
and presentation before retrying. Use `task title sync` when the rebind is
already recorded with delivery pending. Reconcile against the same task
identity rather than replacing it again for title repair.

## Event repair and failures

Committed task, evidence and delegation commands repair an adopted title. Existing
`PostToolUse`, `Stop` and `SessionStart`/resume handlers provide additional repair
opportunities. The synchronizer uses the supported Codex App Server protocol,
without starting a model turn. Interrupt's short deadline leaves repair for the
next event. Stop does not imply completion.

Ordinary tool and stop events reuse the saved task status and skip native calls
when revisions are unchanged and no delivery is pending. Startup/resume and
explicit repair revalidate completion evidence and check for manual edits.
Task and evidence controls remain responsible for current completion authority;
a displayed title is the last delivered status, not fresh evidence.
A per-thread lock serializes participating writers. Desired titles are persisted
before writing; readback acknowledges delivery. Lost acknowledgments, changed
revisions, unavailable clients and timeouts leave repair for the next event.
There is no idle watcher, polling loop or automatic retry model turn. Presentation
failures preserve task outcomes, guard decisions and continuation limits.

### Claude Code delivery

Claude Code has no title call. A title is observed from a hook's `session_title` and delivered through `hookSpecificOutput.sessionTitle`, which applies only on `SessionStart` (`startup`, `resume`, `fork`) and `UserPromptSubmit`. Tool and `Stop` events can neither observe nor deliver, and control commands stage the desired title without an observation. A delivered title stays pending until a later observation equals it, so one lost response is repeated, not treated as a manual edit. Claude stores a managed title without its leading `U+FFFB`; the transport delivers it without that character and restores it when reading. Adoption assumes no custom title when none is observed, so an existing custom name is preserved as a manual title. A generated title is invisible to the hook, and only parent titles are supported; subagents have none. Unobserved presentation, including rendering, stays unqualified.

Private sidecar files in the existing runtime state directory contain title
presentation and delivery metadata only. Task state and accepted milestones remain
in the authoritative task record. Historical task pins and closed evidence are
not modified by title delivery.

## Manual titles

Before an automatic write, reconcile any pending delivery and compare the current
title with the last verified managed title. Apply these rules to both direct
display-title controls and runtime synchronization. An unexplained mismatch
indicates an external change, not who made it; the user, client or another
automation may have changed the title.

- If only the topic changed and the format is unambiguous, adopt the topic,
  clear workflow topic ownership and continue meter/status updates. Queued
  updates use the new topic.
- If meter, family, member, status or overall format changed, preserve the title
  and pause title synchronization for that thread. Announce the pause once.
- Resume only after an explicit user request. For runtime-bound titles, use
  `task title resume` with the current task identity, revision and decision
  reference. Optional `topic` supplies the user's intended wording; other managed
  fields come from authority. For direct controls, retain the recorded
  family/member identity and accepted plan progress, read the current title as
  the new baseline, and clear the recorded pause only for that explicit request.

Pausing title synchronization does not pause the task. A manually typed `DONE`
cannot complete work or change family identity or permissions. New turns, runtime
restarts and milestones do not silently resume title synchronization. A concurrent
manual edit can still race a native write; the client currently provides no
verified compare-and-set operation. Detect observed conflicts and preserve them.
On initial adoption, a manual rename before the very first title observation is
indistinguishable from the initial title without native origin metadata. Do not
claim that this path detects every pre-adoption manual rename. The final
read-to-write race also remains; baseline comparison does not eliminate it.

For a runtime-bound presentation read, use `task title status`; for explicit
repair, use `task title sync`. These take only `{client, session_id}`. Configuration and
resumption require the complete identity/revision request above. Resume retains
the original family/member binding.

## Update and qualification boundary

The task vocabulary is a pinned runtime change. Legacy lowercase records remain
historical; old `ready` meant completion, while new `READY` means eligible to
begin. Do not translate old titles into task state or rewrite old records/pins.
Reconcile affected owners with their original runtime and use the existing
[approved runtime update](runtime.md#change-a-pinned-runtime) procedure with a fresh
state authority. Existing ownership and unresolved assignments remain binding.

An authorized display-format update can reconcile saved titles with earlier
status layouts or without the leading meter character. Preserve the family/member
binding, topic-only edits and manual-title pauses. A user changing the layout
or meter remains a whole-format override. Do not bulk-rewrite titles, replace
original runtime readers or silently repin active tasks for a format change.

Formatting, state, command and hook-boundary tests qualify source behavior.
Installed hook trust and callback delivery, rendering and literal title search
require separate observation. A standalone rename or package validation does not
establish those capabilities.
