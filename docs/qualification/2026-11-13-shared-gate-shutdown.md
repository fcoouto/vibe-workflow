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
