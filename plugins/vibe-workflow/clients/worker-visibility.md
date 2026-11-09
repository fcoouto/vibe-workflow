# Worker declarations and visibility

Use this contract for subagent declarations and optional display.

## Declare before dispatch

Prepare every workflow subagent's role with its assignment, regardless of route,
nesting or Habblaud setup. A native thread owner needs no role badge. Choose the
role only when no bound declaration exists; reuse it for retries, follow-ups and
recovery. Supervisors and reviewers have their own assignments and roles.

Record the declaration once in the route's existing preparation fields, OpenRouter
brief/proposal or Usage Routing proposal. Add no separate role-only step or
execution blocker. Missing or invalid display metadata and failed delivery
never prevent otherwise authorized execution; runtime schemas remain permissive
for legacy input. Use this versioned object or the route's equivalent fields:

```json
{"version":1,"role":"Reviewer","specialty":"Security assessment","custom_role_name":"Threat reviewer"}
```

Choose the actual responsibility: `Coordinator`, `Scout`, `Builder`, `Tester`,
`Reviewer`, `Scribe` or `Investigator`. Never infer it from a task name, model,
specialty or D6 authority role. Supply `custom_role_name` only when explicitly
declared. Specialty and custom name are nonblank printable text, each at most
4,096 characters. Preserve Unicode and the exact custom name. A custom name
appears on native workers as `Custom role name · Model Display Name`. New
external badges display `Role · Model Display Name`. Retain canonical role and
specialty separately.
The full label is sent in the existing display field without truncation. Report
an unsupported label or receiver capability explicitly; the transport enforces
its wire byte limit separately from the declared text lengths. Habblaud uses its
standard `Subagente (Codex)` fallback when it has neither a declaration nor a
specific native role.

Validate the declaration for display; retain the route's existing execution requirements.

Procedures: [native assignments](#native-workers-and-reviewers),
[existing children](#prepare-a-follow-up-for-an-existing-child),
[external routes](#external-routes-in-chat-and-habblaud).

## Reconcile on discovery and upgrade

For a selected fresh Habblaud installation, a discovered existing installation,
or an authorized upgrade, first follow the packaged
[receiver setup procedure](habblaud-receiver.md). Then inspect the current
start hook and reviewed adapter. Reuse the existing command and reconcile its owned wrapper with this
package when needed. Preserve other hooks, arguments, customizations and a
recoverable copy of the configuration. Keep one wrapper for this contract.
Use the client's supported trust flow for changed commands and qualify the
first badge of a future authorized worker. Record pending trust or refresh
steps explicitly. Active workers continue under their loaded configuration.
This procedure adds no execution requirement to delegation.

## Native workers and reviewers

For a managed D6 worker or D5 reviewer, include `visibility` in the existing
`workflow.py delegation prepare` or `workflow.py evidence review prepare`
request. The CLI captures the returned declaration and ledger assignment digest
in a private display context after the runtime operation, outside its transaction.
The response adds `visibility_preparation`; its failure preserves the immutable
prompt, launch result, and distinct review and ledger digests. The declaration
remains optional in the request schema and, when supplied, part of its immutable
digest, separate from authority roles and review report digests.

For ordinary native workers, reviewers and supervisors, prepare the full
assignment in one operation with the workflow package used by the chat. This
requires no enrollment, runtime task or database:

```text
python3 -I -B <package>/scripts/worker_visibility.py prepare
```

Supply `parent_session_id` (actual native UUID), `checkout` (the chat's current
canonical absolute working directory), `task_name` (the immutable creation name),
`message` (the full assignment brief), and an explicit `visibility` for a fresh
assignment on stdin; the entire JSON request must fit 128 KiB. An existing context
with the same parent, checkout and creation name reuses its saved declaration
when `visibility` is omitted. Retain the original `task_name`, `message`, model,
effort, fork settings and permissions before invoking the helper. On success,
copy the returned `task_name` and `message` into the native
spawn tool and preserve those execution controls. Preparation automatically
adds the existing correlation marker to the message and returns the context
path; retrying with the returned message does not duplicate its exact first-line
marker. A `prepared:false` result, including invalid or conflicting metadata,
returns the original name and message. If the helper cannot run or returns
invalid or error output, use the retained original
inputs instead. Report display unavailable on failure. Do not add the marker yourself.

Use a distinct task name for each child. The default context path is
`vibe-worker-visibility/contexts` under the active Codex home, shared with the
reconciled Habblaud start hook. The legacy role-only `prepare` request remains
available for compatibility and recovery. Managed recovery uses the worker's
`assignment_digest` or reviewer's distinct `ledger_assignment_digest` and
preserves the exact immutable prompt.

A native CLI probe received encrypted spawn text in `PreToolUse`, so this
workflow uses assignment preparation and adds no pre-spawn hook. The exposed
native spawn tool has no role argument: code cannot intercept arbitrary native
tool calls outside this workflow. A direct call that bypasses assignment
preparation can still omit role metadata and use the original fallback.
Qualification must observe the first badge; preparation alone does
not prove rendering.

### Prepare a follow-up for an existing child

Use the same `prepare` action when preparing a new assignment for a reused
native child. Add its actual `worker_id` to the full brief. Keep its original
parent UUID, checkout and creation `task_name`; use the original `--context`
if it was explicitly selected. Omit `visibility` to reuse a saved declaration;
otherwise supply it from the assignment. The helper verifies the
native binding before capturing a missing declaration. It preserves the child,
saved declaration and exact follow-up message, without adding a marker to it.
If the original context has no observed child and Codex omits its native path,
include the original `parent_turn_id` and `spawn_item_id` together. Preparation
checks the saved marker or immutable assignment digest against that spawn.

For configured Habblaud delivery, include the same `--visibility-plugin`,
`--habblaud-endpoint` and `--habblaud-account` options as publication below.
The result's `badge` reports a display-only update. For managed continuations,
`workflow.py delegation workstreams prepare-continuation` performs this same-child
preparation once after the ledger operation and before the one-use intent is
consumed. It reuses the original declaration and retained request prompt without
adding the prompt to the public response. Display failure preserves the token,
revision and continuation fields. `consume-continuation` performs no display
preparation. Use the exact admitted prompt and existing continuation controls to send it.

### Forward the original Desktop start event

When the existing Habblaud `SubagentStart` command is configured to use this
adapter, it reads the prepared context and supplies the badge. If the hook uses
a custom context directory, pass the same directory with `--context-directory`
when preparing the ordinary assignment or recovering its context.

Wrap the existing command with the reviewed package:

```text
python3 -I -B <package>/scripts/worker_visibility.py forward-start
  --context-directory <private-directory>
  --codex-executable <absolute-reviewed-Codex-executable>
  -- <original-command-and-arguments>
```

Preserve the original hook configuration for recovery. Review the changed
command through Codex's supported hook trust flow and use a Desktop session
that has loaded it. Project hooks run alongside user hooks. Adding a second
hook does not replace the original Habblaud command.
The shared hook verifies each event's canonical working directory. Supply
`--checkout` only for a deliberately checkout-scoped hook.

The wrapper checks the genuine child, parent, checkout and creation name with
the native observer. For nested delegation, it verifies the native ancestry to
the originating client and resolves a root-valued hook session to the child's
verified immediate parent before locating its declaration and forwarding the
display event. Explicit publication still requires the exact immediate parent.
Display observation validates each native thread's identity and canonical working
directory independently. The event directory, any explicit hook checkout scope,
and the declaration checkout must each match the verified immediate parent's or
child's directory; ancestors may use other worktrees. Saved observations retain
the declaration checkout, and forwarded events retain their original directory.
Execution-authority observers keep their existing checkout scope requirements.
It preserves the incoming native role separately and forwards
the original event if the private declaration or live identity is unavailable.
For a matching declaration, the display label uses the native model catalog's
`displayName`, formatting the actual configured model identifier for display
if the catalog is unavailable. The stored model identity stays exact.
The wrapper sends `vcw_display_label` on the event copy
and calls the existing command. Previously forwarded labels remain unchanged. Habblaud 1.0.3 may capitalize or clip labels. This route forwards
labels that fit 200 UTF-16 units and survive that normalization. Qualify the
first visible badge using a fresh worker. The receiver gives a new generic child
up to three seconds for its start label, without delaying execution. A later
hook can still follow the first fallback badge; report that observation rather
than treating recovery as first-badge qualification. Verified native metadata
can correct a provisional root parent without replacing the child or its history.

### Publish after dispatch

After successful dispatch, use the actual returned child UUID and a short
observed `phase` in `{"worker_id":"<actual-child-uuid>","phase":"Working"}`:

```text
python3 -I -B <package>/scripts/worker_visibility.py publish --context <same-file>
  --visibility-plugin <trusted-installed-local-model-delegation-root>
  --habblaud-endpoint <literal-loopback-HTTP-url>
  --habblaud-account <observed-Codex-account-id>
```

The Codex adapter checks actual parent/child lineage and checkout. The native
path must match the original creation name. If the path is absent, also provide
the actual `parent_turn_id` and `spawn_item_id` in the publish request. It reads
that exact spawn item and checks the original marker or immutable assignment
digest without forwarding any prompt. Same-parent lineage alone is insufficient.
The observed configured model supplies display text; this does not establish
the execution model. Missing observations produce no character. Do not publish
a proposed child, failed launch or fabricated native log.

Use the same context and child for continuation, recovery and meaningful phase
changes. Do not retire the display on an intermediate turn completion. Use a
terminal display phase only when retiring that assignment. An authorized new
attempt or native fallback with a real new child uses a new context with the
same declaration and its own observed identity. A direct coordinator fallback
has no new child to register.

## External routes in chat and Habblaud

Use [Declare before dispatch](#declare-before-dispatch) for declarations.

When preparing the task name of a real native supervisor for an authorized
external route, append the route suffix before recording the assignment or
dispatching the supervisor. Codex Desktop renders underscores as spaces in
native activity names.

| Route | Native task name example | Activity label |
| --- | --- | --- |
| OpenRouter | `review_results_via_openrouter` | `Review results via openrouter` |
| OpenAI API | `review_results_via_openai_api` | `Review results via openai api` |

Use the prepared name for dispatch and any visibility declaration. Keep it
fixed for that supervisor's lifetime. Apply the suffix only to a supervisor
assigned to the route. The activity box covers the supervisor's native work.
Report provider execution and review outcomes from their own evidence. Use the
activity box for start and finish and omit standalone chat messages that repeat
its label. The current foreground Usage Routing host runs in the coordinator
and has no native supervisor activity box.

For a completed Usage Routing generation, the trusted host verifies its
execution evidence and Habblaud parent before calling the shared
`register_external` helper. The helper uses the actual execution route,
parent and run ID to preserve the external display identity across delivery
attempts. It forwards the canonical role and verified model display name.
The routing ledger owns execution and accounting, and native review owns
acceptance. A display receipt establishes visibility only.

The [Habblaud adapter](../runtime/habblaud.py) owns transport, label rendering,
registration and recovery for all routes. The current transport handles Codex accounts and hooks. A host for
another client can reuse registration after verifying its own parent and
execution evidence and supplying a qualified transport for that client.
Observe the first rendered badge separately from delivery and later recovery.
[Usage Routing host procedure](../skills/manage-usage-routing/references/codex-host.md)

## Binding and delivery

Supply labels before this adapter's first registration. The publisher freezes
the declaration and child, account and parent binding before delivery. Report
conflicting receiver labels or parents; publication cannot overwrite them.
The bundled receiver can replace a generic fallback with a declared label from
a native start event bound to the same account, child and parent. This recovery
does not replace a frozen publisher receipt or qualify the first visible badge.
Repeated publication uses the same child identity; it cannot create a synthetic
replacement. An active display lost after receiver reset can be restored with
the same observed identity and original label. OpenRouter retains its separate external display ID and genuine
native parent association. Preserve its frozen declaration when taking over
through an approved native fallback.

The existing transport sends only bounded display metadata, never source,
prompts, raw outputs, credentials or reasoning. Readback checks the exact assigned
label, or an accepted localized standard fallback when no declaration exists,
and the parent binding. Actual rendering and accessibility need client observation.
Delivery, failure and display retirement never change execution, ownership,
acceptance or evidence.
