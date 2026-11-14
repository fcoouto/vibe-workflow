# Desktop-first native stop approval

## Authority and status

The user approved a scoped connector/full-change viewer redesign on 14 November
2026 and selected **OpenCode Desktop first**. The prior instruction to continue
authorized implementation without routine pauses remains effective. This document
records the coordinator's implementation choices; it does not claim that the user
reviewed these exact technical details. Independent architecture/builder review is
required before implementing this material redesign. Escalate only a choice that
would change an agreed requirement, permission, cost or accepted limit.

This continues PR #294, which remains draft/BLOCKED. No merge, release, registry
publication, installed-package update, shared-service restart, new authentication,
credential copying or global permission change is authorized. Product source starts
at `14f2fc507cc545e335334d78f2961e31154c632c`; preserve all earlier failed trials,
consumed allocations and exact evidence. Desktop loading and builtin execution are
not yet qualified.

The [original design](2026-11-13-opencode-stop-form-design.md) remains authoritative
except for the explicitly redesigned dependency packaging and presentation path
below. Its exclusive-writer assumption and native identity/completion safeguards
remain unchanged. A form decision remains automation-capable workflow data, not
proof of a physical human or a security boundary against authenticated clients.

## Goal and non-goals

Allow one stopped original builtin `edit` only after an exact complete-change
Desktop review submission and matching authoritative native form approval. Only
its matching successful native completion clears the stopped hold.

Preserve ordinary work, earlier escalation/re-evaluation, native allow/ask/deny,
root-message reset, one regular UTF-8 file up to 1 MiB, the 64 KiB native metadata
ceiling, generation/tree/session/message/call/hold/change/file binding, target
occupancy through execution, and bounded event-driven cleanup. No custom writer,
reconstructed patch executor, judge/model call, background polling, broad allow,
new user setting or durable approval store is added.

Desktop's own local background server is the initial supported deployment. TUI,
third-party mobile clients, arbitrary web clients and remote routed servers are
not silently admitted. Unknown connection or presentation capability denies the
stopped attempt; the existing root-message reset remains available.

## Approaches

1. **Official server client plus Desktop panel — selected.** Package the official
   Promise client portably; reuse qualified no-start discovery, native authentication
   and same-process checking. Server retains form ownership and cleanup even when
   a window disappears. Add only the viewer/submission protocol.
2. **Desktop-client transport relay.** Avoid the server dependency, but make every
   create/read/cancel depend on a window, with delivery acknowledgements, executor
   election and recovery. This is not the smaller complete lifecycle solution.
3. **Restrict the old composer to short changes.** Rejected as the product solution:
   byte/line cutoffs cannot establish complete display across wrapping, Unicode and
   resizing. The retained 51,687-byte/1,205-line trial actually clipped its content
   and controls. Its failed criterion is not waived by the redesign.

Native Desktop `Panel` and host controls replace the clipped composer as the
qualified review surface. Keep the complete bound description in the original form
as data; a default composer answer alone never authorizes the edit.

## Portable artifact and connector

### Artifact boundary

Create an OpenCode-specific package, not a Node migration of the Python toolkit.
Its manifest exposes the server entry, shared RPC declaration and one prebuilt
`./gui-experimental` CommonJS entry. Declare the official `@opencode/client`
production dependency normally. Put the official GUI Vite preset/compiler tooling
in scoped development dependencies. GUI host-shared modules remain external.

Preserve the backend's package-relative Python resource layout: the current
`defaultFeatureStatus` resolves `scripts/workflow.py` three directories above the
adapter. An artifact must retain those resources at the same relative paths; it
cannot copy `index.ts` alone or depend on the developer checkout. Reuse the existing
clean package export as the resource baseline rather than duplicate gate logic.

Generate GUI output and prepared dependencies outside the generic Codex/Claude
export. Do not vendor `node_modules`, run npm in generic Python packaging, or raise
its 8 MiB/per-file/path ceilings. Measure both artifacts. Add only the scoped
manifest/lock/build/packaging code and guidance needed to produce and test this
artifact. Preserve default `scripts/package.py` behavior.

Native registry/Git installation can resolve declared production dependencies;
local directory loading does not promise dependency installation. Disposable local
qualification must prepare dependencies explicitly inside its owned artifact. Build
the GUI before loading: install scripts are not a supported GUI build mechanism.
No registry publication or installed/global configuration update follows from
creating a local development artifact.

### Lazy same-service access

Construct a narrow `StopForms` transport without importing/discovering a service
or making a form call while nothing is stopped. Load the official dependency only
inside a bounded stopped attempt. Use supported `Service.discover`, never `ensure`,
then `Service.headers` and the official generated client. Never inspect registration
contents directly, serialize credentials, use another plugin's cache path, or add
an authentication implementation or fallback endpoint.

Before publishing a cached client, authenticate `server.info` and require its PID
to equal the executing server plugin's `process.pid`. Verify the requesting
session's location and current plugin generation. Keep request guards, cancellation,
redirect rejection and bounded identity/discovery accounting. Do not turn observed
version provenance into an exact-release allowlist. Missing dependency, incompatible
API, mismatch or unavailable service denies; no process is started to repair it.

Keep create/get/cancel on the server. The connected Desktop client performs one
ordinary native user reply; the backend independently verifies the terminal form.

## One attempt, two required predicates

Extend the existing `StopApprovals` attempt, not a second approval subsystem.
Retain its original full native binding, snapshot, operation count, deadline,
controller and successful-completion permit. Add bounded viewer correlation and
submission state to that same lifetime.

Proposed private RPC methods are plugin-owned names, not upstream SDK APIs:

- `pending`: one scoped snapshot when a relevant Desktop session mounts, to cover
  missed live notifications. No periodic polling or cross-tree inventory.
- `open(sessionID, formID, viewID)`: claim one active view and return an immutable
  payload with generation, opaque attempt ID, original session/form, canonical
  file, reason, complete patch, payload digest and remaining time.
- `begin(attemptID, viewID, submissionID, contentDigest, decision)`: select one exact
  submission and establish that the owned native form is still pending.
- `complete(ticket, outcome)`: commit a confirmed successful native reply, or
  invalidate a failed/unknown submission. Never trust a caller-supplied native state.
- `abandon(attemptID, viewID)`: invalidate an unconsumed view/attempt and wake its
  existing cleanup owner. No second DELETE owner.

Every method checks current generation, exact attempt/view, native session/tree,
location and deadline. The frontend cannot replace the native source/hold/change
binding. Tickets and view IDs are in-memory correlation, not authentication or
cryptographic proof of rendering. RPC uses existing native authentication. Bound
all maps by the current remembered-attempt capacity; do not persist raw changes in
GUI stores or public logs. Events carry scoped identity and state, not broad file
content inventories.

### Ordering

1. Observe the original native edit and stop, copy its binding, start its budget,
   and set permission to deny before awaiting connector or form work.
2. Register the attempt before creating its one original native form. Verify the
   returned identity before exposing the immutable review payload.
3. Mount the complete exact payload in a usable Desktop panel. Enable actions only
   after its scrollable content, header and actions exist for the intended session
   on Desktop's own server. Do not require a ritual scroll-to-end or claim the user
   read every line; require that the complete change is actually accessible.
4. An explicit Approve/Reject gesture calls `begin`. Select its submission
   synchronously before awaiting a pending-state GET. Require full exact form
   identity and pending status. This GET replaces the old unconditional initial
   GET; it is not an extra acquisition read. A pre-existing answer fails the attempt.
5. Only after receiving the bound ticket, use the current connected Desktop client
   to reply to that original native form once. Pin the original server/session,
   not whichever session became current during the await. A fulfilled HTTP 204 is
   a successful void result, not missing JSON. Error envelopes, conflict, timeout,
   abort or unknown outcome do not produce a success acknowledgement.
6. `complete` records a committed submission only for that current ticket. The
   permission owner waits for BOTH its committed approve receipt and authoritative
   exact answered/approve state. Latch native events that precede reply settlement;
   they never authorize provisionally. A committed receipt also wakes the owner
   for readback if event delivery lags. Coalesce duplicate wakeups.
7. After the terminal GET and at most one event/receipt-triggered confirmation GET,
   recheck every original binding, snapshot, generation, hold and deadline. Atomically
   consume the one-use attempt, then restore only the saved native allow/ask effect.
8. Original native permission/execution continues. Only the matching successful
   builtin completion clears that stopped incarnation. Failure or unrelated
   completion never clears it; the answer cannot authorize another call.

An early/default native answer without a selected live submission invalidates the
attempt; it cannot later be paired with opening a viewer. During submission, native
state may arrive before the acknowledgement, but permission remains denied until
both predicates pass. The pending GET is not an atomic native reservation. If
another authorized client races the reply and the viewer receives a conflict/error,
deny even if later native state says approve. Do not claim answer-origin security
beyond an explicit exact viewer submission plus authoritative native approval.

## Lifetimes and replay

One active view owns one attempt. A second window cannot replace it or spend its
ticket. No takeover/lease/heartbeat protocol is needed: owner loss invalidates this
attempt; a later stopped native call gets a fresh form. Identical `begin`/`complete`
replays return the same in-flight/result state without another GET, reply or grant;
different submissions fail. GUI buttons disable on first submission. Never retry a
native reply automatically after an uncertain result.

Close, unmount, route/server switch, generation/reset changes, interruption, known
disconnect, unusable presentation or expiry invalidates an unconsumed view/ticket.
Abort local work and send best-effort exact `abandon`; after each await recheck the
mount/client/attempt before continuing. A crashed window may not deliver abandon:
the server still denies without a committed receipt and owns bounded cancellation.

Server compare-and-set ordering defines the linearization point. Invalidation
processed before consumption wins. An abandon arriving after consumed approval
cannot revoke an already released native operation. Do not promise instantaneous
remote close detection. Preserve occupancy through any later native permission wait
and execution. Closing a view after accepted approval is not an undo operation.

Cancel uses the server's existing single-flight owner cleanup, not simultaneous GUI
and server DELETE calls. Reject replies reject and retains denial. Late/foreign
callbacks cannot activate another generation or clear a reset-and-reheld target.

## Desktop interaction

Use the documented native `Panel`, host theme and controls. Its fixed header shows
path, stop reason and actual requesting session. A plain-text scroll region exposes
the entire complete patch vertically and horizontally. Keep Reject, Approve this
edit and Cancel/Close reachable outside that region. No clipped summary, HTML,
Markdown, ANSI interpretation, executable patch, new branding or animation.

Use reactive session/server references, not captured stale routes. Parent-tree
attention may surface a child request, but the decision remains bound to its
original requesting session and form. Never approve another root/server because
events share a directory. Resize rechecks usable layout; loss of actual presentation
disables/invalidate submission rather than inventing display support. Treat text
that cannot be faithfully presented as unsupported and deny, without stripping it.

Keyboard users can enter the panel, scroll both axes, reach actions, cancel and
restore focus. No default positive action. Show reviewing, submitting, decision
accepted, native-permission/execution pending, completed, rejected/cancelled,
expired/unavailable and failed states distinctly. Approval alone never says the
file was edited or the hold cleared.

## Bounds

Preserve 110-second acquisition and 120-second total owner lifetime, starting
before connector work. Reserve the final ten seconds for owned cleanup. Keep the
eight-second per-operation cap constrained by remaining time and at most six
approval-owner form operations:

1. Create.
2. Pending read in `begin`.
3. Terminal read after committed receipt/event.
4. At most one confirmation read for already observed settlement.
5. Owned cancellation if needed.
6. Cleanup confirmation read.

No per-window creates, extra per-RPC reads or automatic create/reply retry. The
Desktop client's one native user reply is separate from the six owner operations,
as the original frontend reply was; report that invocation and its failure/unknown
outcome separately. Do not move it into the owner while hiding a seventh operation.
Discovery/info and scoped RPC/event calls have bounded deadlines and separate
accounting, never renewed form budgets. After consumption, native permission wait
and execution retain their existing separate lifetime.

## Verification and rollback

Use separate finite allocations and exact source/artifact pins for native trials.
No prior pass qualifies a new path, and no failed allocation is reset.

1. Test-first pure owner/RPC/connector regressions: missing dependency/viewer,
   same-service/location mismatch, early/default answers, exact/foreign/malformed
   states, HTTP 204, event-before-state, receipt-before-event, failed/unknown reply,
   duplicate/two-window submissions, reset/re-hold, snapshot mutation, late callbacks,
   close/resize/server switch, capacity/deadline/operation exhaustion and cleanup.
   Preserve ordinary gate, native ask/deny and matching-completion behavior.
2. Build and inspect the OpenCode artifact with normal declared dependencies and
   one GUI CJS output. Verify required Python helper paths, no checkout/cache imports,
   GUI shared-module boundaries and unchanged generic export limits/behavior.
   Clean dependency preparation/loading remains an observed gate, not a manifest claim.
3. Qualify actual disposable Desktop loading, own-server connection, RPC while a
   native permission hook waits, and independent server cleanup after GUI loss.
   Private Desktop/profile launch and authentication must use supported mechanisms;
   inability to isolate blocks that native probe, not permission to update the user's
   installed plugin or personal configuration.
4. Qualify complete Desktop display at ordinary/narrow/resized windows using the
   retained long specimen. Bound ordered overlapping captures to the same payload;
   reach first/middle/final text, long-line ends and actions. Exercise keyboard and
   close/unmount/two-window states. API/DOM text equality alone is insufficient.
5. Run the original builtin `edit` milestone through real observed edit/check/child
   signals. Pending/reject/cancel preserve bytes/hold; only exact dual-gated approval
   and matching native completion may write and clear its hold. No seeded Gate or
   assert-plus-custom-writer substitution.
6. Validate, commit and run affected committed checks; independent behavior/security/
   evidence review precedes acceptance and draft PR publication. Report source/build,
   actual Desktop execution and builtin execution separately. No release follows.

Rollback keeps the existing original root reset and missing-capability denial.
Removing/unloading a viewer invalidates pending tickets and wakes server cleanup;
it never creates a fallback native ask/automatic approval. Existing in-memory,
whole-plugin fail-open/reload and external-writer limits remain disclosed. Do not
claim that disabling the entire plugin preserves holds.

## Sources and empirical limits

Official [client](https://opencode.ai/docs/build/client),
[GUI](https://opencode.ai/docs/build/plugins/gui) and
[RPC](https://opencode.ai/docs/build/plugins/rpc) guides and inspected official
plugin/client 2.2.6 plus native CLI 2.2.7 source inform this design. Context7 was
unavailable due to quota. Native APIs are checked by actual capabilities/identity,
not a tested-version allowlist. Desktop GUI APIs are experimental.

Independent Astra/high static assessment supports the packaging/ownership route
and operation ordering. It ran no build, install, GUI or native probe. Artifact
resolution/size, precise live reply error behavior, Desktop lifecycle/rendering and
actual builtin completion remain empirical gates, not successful results.
