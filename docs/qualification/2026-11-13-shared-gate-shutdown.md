# Shared-server gate flow and shutdown — 13 November 2026

**Partial qualification.** Shared-server hold/release and disconnect during child
work passed. Actual approval control did not pass. The task remains open;
Smart Routing, semantic judging and productivity comparisons remain deferred.

## Subject and limits

- Released 0.1.39 gate runtime, unchanged from the earlier qualification. Source
  base: `0ce70fbeea70f98999e69d5394a8414f17fed927`.
- Installed OpenCode CLI and existing shared server reported 2.2.7. Both fixture
  observers ran inside the existing shared PID 61057, unchanged throughout.
  Versions are provenance, not an exact-release eligibility requirement.
- Two actual TUI-created roots used disposable canaries, native general children,
  native edits and unmasked unittest failures. No synthetic gate events or API
  permission replies were used by the campaign controller.
- Fixed allocation: 80 model requests over 30 minutes, including children and
  auxiliary requests. Case A consumed 27/44; Case B consumed 22/36. Total 49/80.
  Neither case was replayed; no allowance was reset or transferred.
- No runtime fixes, threshold changes, dependency installs, credential copying,
  global settings changes, shared-service restart or productivity trials.

The [sanitized evidence](../../tests/fixtures/opencode_workflow/shared-shutdown-2026-11-13.json)
retains source/capture digests, native route provenance, verdicts and limits.
Private preparation artifacts retain full native events, terminal captures,
controller inputs, protected originals, evaluations and failure recovery.

## Passed: Case A

1. Three native edit/failing-check pairs held `canary.py` after two repair rounds.
   Root and same-level native child edits were denied. The child's actual primary
   route matched the root's low effort; its completion did not release the hold.
2. A fresh native child actually ran at high effort, diagnosed without edits and
   completed successfully. The hold cleared; repair to 99 and the unchanged test
   passed. An unrelated file remained editable.
3. Another native child ran a controller-owned Unix-socket barrier. The controller
   observed the real handshake and active child identity before frontend exit.
4. The frontend accepted its configured exit key, drained its terminal output,
   exited 0 and was reaped without signals. Root and child remained active in
   the same shared server after frontend disconnection.
5. The controller released only its barrier. Child and root completed. Reattaching
   to that completed root rendered its completion transcript without another model
   request; that frontend also exited 0 without signals.

Protected test/configuration/settings/barrier bytes remained unchanged. Final
canary was 99. An independent local unittest rerun passed. All four owned
sessions were inactive at final observation. This proves the tested native
sequence, not general reliability or semantic diagnosis quality.

## Failed: Case B approval control

The highest-ranked fixture effort reached architecture re-evaluation after three
failures. A fresh same-effort read-only native child released it once. Two further
edit/failure pairs reached stop. The actual TUI rendered the canary diff and
**Allow once**, **Always allow**, and **Reject** controls.

The controller intended to select Reject. Native evidence instead recorded:

- Permission asked at event index 76; native reply `once` at index 77.
- Native reply timestamp: `1794584032029` ms.
- Controller Reject input timestamp: `1794584032038.527` ms, later than that reply.
- The controller's pre-click canary snapshot already contained `VALUE = 6`.
- Native logs recorded two subsequent HTTP permission-reply attempts returning
  404. The retained records do not identify who originated the earlier approval.

The edit completed and the gate cleared after native approval. The controller's
later assertion expected rejected bytes to remain 5 and failed. Recovery killed
and reaped only the owned frontend (`-9`), then reconciled both owned sessions as
inactive. No shared server was stopped. Protected inputs remained unchanged;
the independent final check still failed on 6 versus 99, as expected.

**Do not count this as a successful Reject or deliberate Allow-once interaction.**
The later Allow-once test and Case B's clean completed-work exit were not run.
The unexpected reply precedes the controller click; wrong click coordinates are
not an established cause. Approval-source attribution remains unresolved.

### Read-only continuation: matching Desktop approver found

A subsequent user-authorized investigation inspected the installed Desktop 2.2.7
bundle and only its relevant native settings record, without changing either.
`app/src/session/requests/auto-approve.ts` subscribes to `permission.asked` across
the server connection and submits `once` for projects covered by Desktop's
client-local approval preference. The observed Desktop setting covered the
fixture. The terminal's `session.permissions=prompt` does not disable this
separate approver. This is a matching enabled mechanism, not conclusive actor
attribution for the historical reply.

An offline check executed the installed pure auto-approval state machine: an
approve event produced a reply effect; cancellation produced no new reply. It
made no model/network calls or native settings changes. Node's type-stripping
helper emitted its experimental-feature warning; no product API changed.

Fixture-only isolation is not established. Original fixtures belong to native
project `global`, so a project override would not uniquely target this test.
Moreover, the Desktop listener can approve an unknown session using its default
before looking up that session's project. Do not assume that adding a project
override alone removes the race. No supported GUI driver/tool is available here;
direct edits to native settings/database files are not a substitute.

**Current blocker:** establish Desktop approval isolation through an explicitly
authorized, supported/manual control. Preserve the user's global preference
unless they authorize its temporary change. No live replay, new allocation,
application shutdown, driver install or service restart occurred. Other mechanical
implementation and original selected qualification tasks are already complete;
the routed judge still depends on Smart Routing and remains outside this plan.

### User-authorized fixture-only isolation preflight

After the user disabled Desktop's global auto-accept, a new zero-model fixture
tested whether its native permission request would wait for terminal input.
Read-only checks before and after observed the persisted Desktop default off and
no project overrides. The existing shared process remained 61057/version 2.2.7.

**Result: failed.** The first externally created native canary permission request
resolved `ask` but received native `once` **19 ms later**, before any controller
permission-choice input. The controller stopped the one-attempt test immediately; it did
not replay the request or submit an API permission reply. Only one of the maximum
two permission requests ran. No prompt, tool execution, model request or rejected
model attempt occurred. The canary stayed 0; all five protected inputs matched.

The empty decision-controls trace contains no guarded click, prompt or exit key.
Automatic terminal-query responses were enabled but not separately input-traced;
this is not a claim that the controller sent zero protocol bytes to the terminal.
No evidence establishes that those protocol responses caused the approval.

The requested five-second pending observation and deliberate TUI Reject/Allow-once
controls were therefore not reached. Failure recovery killed and reaped the
owned frontend (`-9`); that is not a clean-exit pass. The owned root was inactive
and had no pending permissions at final observation. No file edit, settings
change, service restart or old-case replay occurred.

This is an API-created idle-root permission/UI preflight, **not** another live
gate-flow case. Earlier Case A/B results and the 49/80 allocation remain unchanged.
The result establishes that persisted Desktop auto-accept being off is not enough
to establish control isolation on this shared connection. It does not identify
the responder or prove whether a stale Desktop view, another client or a different
approval path answered. Do not ask the user to repeat the same setting change as
if the persisted change were absent. Caller-level attribution or supported client
isolation is needed before another attempt; the live gate follow-up stays blocked.

### Read-only trace of possible `once` writers

The user subsequently requested tracing without another fixture run or settings
change. Inspection of the installed 2.2.7 CLI's embedded source narrows the path:

`session.permission.create` calls `Permission.ask`, evaluates the request, parks
it and publishes `Asked`. It does not manufacture `once`. The reply endpoint
validates the request/session pair and calls `Permission.reply`, which publishes
`Replied`, resolves the waiting permission and removes it. The server plugin
`ctx.permission.reply` reaches that same service. A saved allow changes evaluation
before parking; permission-service shutdown rejects pending requests. Neither
explains the observed `Asked` followed by `once`.

| Possible writer | Required path | What retained evidence establishes |
| --- | --- | --- |
| TUI auto-accept | Auto flag or CLI autoaccept mode; current root/family request | Owned frontend had no auto/yolo/skip flag, explicit prompt config and no `auto` footer marker. This is setup evidence, not a complete runtime-mode trace. |
| TUI choice | Return or mouse-up selects a choice; default is once | No controller permission-choice input was recorded. Protocol traffic was not separately input-traced. |
| Desktop auto-approver | Covered project and mounted server-wide listener | Persisted preference was off during isolation. Other windows' live state/hydration was not captured. |
| Desktop dock/inbox | UI decision invokes the permission API | No actor-bound record identifies such a decision. |
| Headless `run --auto` | An awaited root/descendant ownership check must pass | Current local CLI inventory showed only the shared server, not a matching run client. This cannot exclude a past or remote client. |
| ACP client | External permission chooser returns once | A possible installed path, not an observed responder. |
| Authenticated API client or server plugin | Explicit reply supplies once | These reach the same reply service; the retained event does not distinguish them. The tested controller/observer did not call a reply endpoint. |

The CLI `api` command handler itself performs its selected HTTP request and writes
the response. Its traced body contains no automatic permission reply/subscription.
This excludes an implicit approval in that handler body, not arbitrary imported
code, remote clients or plugins. The captured `permission.replied` record contains
session, request and reply, but no approving client/process/transport identity.
The native `source` attached to an ask identifies the requesting tool, not the
actor that later approved it. Retained request-specific logs do not fill that gap.

**Controller protocol check:** an offline call of the installed pure keypress
parser at its default options discarded complete cursor-position, primary-device-attribute and color
responses. The secondary-device-attribute response produced an unnamed key, not
Return. An actual carriage return produced Return as the positive control. These
are the complete response forms the controller can generate; this does not replay
the full streaming parser or recover historical stdin fragmentation. It reduces
support for a straightforward protocol-response-as-Enter explanation without
proving that all controller input paths are excluded.

**Separate dormant preflight issue:** the isolation request used `save=[]`, for
which the native TUI offers Allow once and Reject, not Always allow. Its controller
would nevertheless wait for all three labels. The observed early-reply guard
failed before that branch, so this is not the cause of the 19-ms approval. Preserve
the executed script; align the visible-choice guard before any separately
authorized future attempt. No script repair or test replay was made here.

The installed CLI digest is
`2160e46e87e83561195271e69d0385440bbc91bc4b9c8dc38e0a3822190614a1`.
Private evidence retains bounded source-range digests and parser-check results.
**Conclusion:** an explicit reply path is traced, but the responder cannot be
identified retrospectively from the retained event/log fields. Caller-bound
telemetry or supported isolation remains necessary. No new native permission,
fixture run, live model call, settings change or service restart occurred.

### Astra investigation: approval provenance and implementation implication

The user explicitly requested a native `openai/gpt-6-astra#xhigh` investigation.
**The inspected OpenCode 2.2.7 permission API and server-plugin surfaces cannot
reliably distinguish a fresh human approval from automatic `once`.** Client code
knows which callback it ran, but that distinction does not reach the permission
service as trusted approval provenance. This is an installed-source conclusion,
not a new live test or a claim about future releases.

Manual and automatic Desktop paths submit the same reply under their connection's
credentials. The TUI likewise uses the same reply method for manual and automatic
approval. This supplies a positive counterexample to distinguishability; the
conclusion does not depend on identifying the historical responder.

| Inspected installed source | Evidence |
| --- | --- |
| Desktop source-map `app/src/session/requests/auto-approve.ts:109-120` | Automatic reply sends `decision:"once"`. |
| Desktop `session-permission-dock.tsx:52-64`, `model.ts:202-215,237-259` in the same directory | Visible Allow once reaches the same permission reply API without gesture evidence. |
| CLI bytes `148547200+1500`, `149045350+1700`, `157188000+1200` | Reply body accepts decision/message; route forwards request ID, decision and message, not approving actor. Replied data contains session ID, request ID and reply. |
| CLI bytes `155737516+4477` | Core reply publishes settlement and releases the waiting permission; no fresh-human verification or reply-validation plugin hook appears on this path. |
| CLI bytes `155398400+4800`, `156552700+5500` | Event metadata is supplied explicitly; this reply supplies none. Server authentication authorizes access, not a per-request human gesture. |
| CLI bytes `156280650+2400` | Prompt admission creates a user-message input from caller-supplied prompt data, not authenticated human presence. |

CLI coordinates are zero-based byte offset plus length in the installed binary
whose digest is recorded above. Desktop coordinates refer to embedded source-map
content in the installed archive. The private report retains additional source
ranges and digests; the coordinator independently verified eight CLI range hashes,
the full CLI digest and three decisive Desktop source hashes. Official API and
plugin documentation corroborated the surfaces; installed source is decisive.
Context7 was unavailable due to quota; no key or configuration was changed.

Headers, client labels, request metadata and timing do not fill this gap. Shared
credentials do not distinguish two branches of the same authorized client. Ask
`source` identifies the requesting tool, not the approving person. Generic event
metadata or a caller-supplied `human:true` would not establish trusted provenance.
The evaluation hook runs before any decision. A Replied listener is not a supported
pre-execution veto: filtering that event cannot prevent an edit already authorized
by `once`, and preserving its counter afterward would not undo the edit.

**Current gate semantics are host-authorized completed edit, not guaranteed human
approval.** `gate.ts:254-273` returns ask at stop; `index.ts:201-226` remembers the
tool and calls `edited` on completion; `gate.ts:218-220` clears the held stop entry.
The source comment's inference that the user approved exceeds available provenance.
Finding the earlier writer could explain the failed case or help isolate another
test, but would not resolve this design limitation. An isolated successful human
click would not prove that automatic approval cannot release the same stop.

**Future options, not implemented or accepted as a redesign:**

- Keeping current behavior means explicitly accepting native host approval rather
  than a guaranteed fresh human decision. That would weaken the latter requirement
  and needs an explicit requirements decision; this investigation does not make it.
- The smallest recommended workflow correction is to keep stop-stage edits denied
  until an explicit decision/reset, reusing the existing root-message override
  instead of an auto-answerable ask. This would remove the automatic-once release
  path. Root prompts still lack authenticated human provenance: authorized API or
  plugin callers can submit them. This retains the design's documented reset
  residual, not a physical-human authentication guarantee.
- If that strict guarantee is required against other authorized automation, the
  inspected surfaces are insufficient. It needs trusted human-presence evidence
  bound to the pending action and checked before execution, not a new label,
  after-hook, judge or generic RPC endpoint.

No runtime, reset behavior, settings or permissions changed. No fixture replay,
native permission probe or new live qualification occurred. The Astra assignment
was a separate bounded native investigation, not zero model usage. Smart Routing
and the judge remain deferred; PR #289 remains draft `[BLOCKED]`. A future runtime
change requires its own design review, implementation and qualification.

**Evidence snapshot reconciliation:** the earlier isolation event digest binds
the original 1516-byte, five-record snapshot. The current file has one later
48-byte cleanup record at `2026-11-13T18:24:32.726Z`; its full digest is now
`34d3b5e993859659d9a5d58b4ebd286b57bdbdf35b65a5ef7bfd633bf7f8ae82`.
The original prefix still exactly matches the retained `883b64d7…` digest. No
request/reply bytes changed; this appended record neither identifies the responder
nor establishes why cleanup occurred. Both observations and raw bytes are retained.

## Checks and next boundary

- Existing baseline: two Python gate tests, including 77 Node tests, passed on
  this source base before the campaign.
- Private terminal/controller preflight passed split ANSI/UTF-8 decoding, stale
  and ambiguous target rejection, bracketed prompt input, native event identity,
  request accounting and failed-check attribution. The real socket barrier
  blocked, released and reaped normally without inference.
- Evaluator self-check rejected six false-clean-exit mutations. Prior synthetic
  shutdown-drain RED/GREEN and idle startup/attachment receipts were reused, not
  replayed or used to erase earlier forced-exit failures.
- Package validation, committed affected checks and independent evidence review
  are tracked in the task PR; they do not make Case B pass.

Before another live approval attempt, identify or isolate the competing approval
source without changing the user's existing permissions implicitly. A changed
live attempt needs a separately bounded decision; do not replay Case A or reset
the original allocation. Publish these partial results as `[BLOCKED]`, not ready
for merge under the current qualification plan. Broader closure remains open.
Separately, decide the stop-stage contract above: responder isolation alone cannot
establish a guaranteed fresh human decision from the native approval channel.
