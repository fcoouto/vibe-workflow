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
terminal input. The controller stopped the one-attempt test immediately; it did
not replay the request or submit an API permission reply. Only one of the maximum
two permission requests ran. No prompt, tool execution, model request or rejected
model attempt occurred. The canary stayed 0; all five protected inputs matched.

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
