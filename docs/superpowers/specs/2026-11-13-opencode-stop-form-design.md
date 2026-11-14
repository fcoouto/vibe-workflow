# Explicit stop-stage approval for one native edit

## Status and authority

Design approved for offline implementation and deterministic regression review.
The user asked to proceed
with the next Escalation Gate milestone and submit it for review on 13 November
2026. This document does not change runtime behavior or authorize a release.
The subsequent implementation request explicitly excludes live qualification and
keeps PR #294 draft/BLOCKED. No release or installed-package update is authorized.

**Approved first-milestone assumption (user, 13 November 2026):** limit the first
milestone to files with no concurrent external writer from native edit preparation
through commit. This is a scope assumption, not a detected or enforced guarantee.
The earlier scope-record update left runtime unchanged. The later implementation
request permits candidate source changes, not live qualification or stronger
snapshot guarantees.

The milestone is one end-to-end **built-in `edit`** blocked at the stop stage,
explicitly approved through a native form, executed by the original native tool,
and correlated with the gate's successful-completion handling. The proposal is a
workflow safeguard, not a security boundary or human-presence authentication.

## Why this change

The released adapter remembers allowed and asked edit calls; a completed edit
clears a stopped file in `Gate.edited`. Native manual and automatic `once` replies
share the same channel, so this does not establish an explicit workflow decision.

The [qualification evidence at its immutable candidate](https://github.com/fcoouto/vibe-workflow/blob/864b10dd4a3fa403d7f830717d329471d6949bac/docs/qualification/2026-11-13-shared-gate-shutdown.md)
supports a separate form-access bridge and one-use binding in a native
permission-assert/custom-RPC canary prototype. Its 13 scoped checks passed; its
controller exited 1 during teardown, and later removal readback exceeded the
30-second recovery observation criterion. Neither the successful prototype nor
that later reconciliation qualifies built-in tool integration or overall readiness.

## Requirements and non-goals

- Preserve repair detection, earlier escalation stages, diagnosis release,
  architecture re-evaluation and the existing root-message reset override.
- Prompt only at a known stop-stage edit, never for ordinary work or earlier holds.
- No native `once`, `always`, saved allow or auto-accept can substitute for the
  matching form decision while this active instance is enforcing a stop.
- Pending, rejected, cancelled, expired, missing or invalid approval cannot run
  that stopped edit or clear its hold.
- A decision belongs to one generation, tree, session, native tool call, resource,
  proposed native change and hook-observed file snapshot, under the writer
  assumption below. It cannot authorize another call or hold incarnation.
- Clear a stop only on the matching successful native completion. Failure,
  interruption, missing completion and unrelated completed calls do not clear it.
- Keep existing native permission rules; form approval grants no broader access.
- Stay dependency-free, with no model/judge calls or background polling.
- Do not change Smart Routing, global settings, the installed release or the
  separate cleanup repair. Preserve original qualification receipts and budgets.

This first slice grants approval only for one existing, regular text file through
the native `edit` tool. Multi-file patches, moves, creates, deletes, `write`, other
tools and ambiguous native metadata do not get a fallback native approval. Their
stopped edits remain denied, with an explanation of the supported path and existing
explicit reset override. This does not restrict their ordinary unheld behavior.

The existing shell/API/other-plugin bypass limits, root-prompt API residual and
in-memory/fail-open lifecycle remain disclosed. Persisting holds across reload,
changing whole-plugin failure behavior, manual UI qualification, physical-human
authentication and general race-freedom are separate milestones.

## Approaches

1. **Keep native ask/reply.** Smallest diff, but automatic approval still releases
   the edit. Rejected because it does not satisfy explicit-decision binding.
2. **Deny until the existing root-message reset.** Smaller and usable, but resets
   the hold rather than approving one concrete native change. Keep as the already
   documented override, not the selected next milestone.
3. **Native form before the original native edit commits — proposed.** Reuse the
   qualified authoritative bridge and existing permission/completion hooks. Bind
   approval to native facts, not an agent argument or custom RPC writer. This is
   the approach the user subsequently authorized implementing for offline review.

## Integration

### Capture native identity, not an invented edit API

Use `tool.execute.before` to correlate the native `edit` tool with its
`sessionID`, `messageID` and call `id`. This hook does not write the file, replace
the native executor, parse/execute a patch or add an approval/reset tool.

In `permission.evaluate`, join `source` to that observed tool call. Accept only
one exact edit resource and one well-formed native proposed-file diff in
`metadata.files`. Installed 2.2.7 source shows that built-in `edit` prepares this
diff before `FileWrite.authorize`, then commits only after authorization. The
native tool retains its existing matching, locking and formatting behavior.

Bind the complete native change, not just a displayed or truncated summary. The
binding contains the plugin generation, root/session, source message/call, action,
normalized resource, digest of the complete native proposed-file metadata and
digest of the current raw file bytes. Copy the bound metadata before awaiting.
Do not substitute caller-supplied form IDs, answers, before-images or custom RPC
metadata for these observations.

Distinguish three facts: the native executor's earlier read snapshot, its proposed
diff, and the raw file bytes first observed by the permission hook. Native proposed
metadata does not expose a digest of the executor's full original bytes. Equal
hook-entry and pre-release digests prove stability only between those observations;
they do not prove equality with the native executor's earlier read. This slice
assumes no external writer between native preparation and commit; the native lock
serializes cooperating native writers, not editors, shell/API writes or plugins.
Use an exclusively owned disposable file for qualification. The pre-hook external
write gap is a disclosed limit, not a passed before-image guarantee. If equality
with the native full-file snapshot is required, the milestone is blocked until a
supported native source exposes that fact. Do not rebuild the patch to invent it.

Only an existing non-symlink regular file up to 1 MiB and a native change payload
up to 64 KiB qualify in this slice. These are conservative fixed bounds, not new
settings. Reject unrepresentable or inconsistent metadata before creating a form.
Native path/diff schema and identity matching require source-backed regression
tests and actual built-in qualification; documentation alone is insufficient.

### Keep the native edit pending behind the form

For a known stop, set the permission draft to `deny` before any asynchronous
operation. Save the incoming permission effect separately. The native operation
stays inside its pending permission evaluation; no deny-and-retry protocol or
second custom writer is introduced.

Create one owned native form in the requesting session. Use a required string
field `decision` with `custom: false`, no default and approve/reject options. Put
the held file, stop reason and complete bounded native proposed change in that
field's `description`, not invisible metadata. Official `Form.StringField` supports
this text surface; complete client rendering is still a qualification prerequisite.
Reject payloads that the qualified client cannot present completely. A digest is
an identity check, not a useful substitute for showing the change. Store only
correlation fields in form metadata; keep private file content and raw receipts
out of public logs and PR bodies.

Use the installed CLI's supported form API and existing authentication, as the
qualified bridge did. The installed plugin context lacks `ctx.session.form`;
do not assume documentation's client-like context supplies it, install an SDK,
copy credentials or add a separate HTTP authentication implementation. Resolve
the running native executable through supported host facts and verify its service
identity/capabilities before allowing an edit. An executable/PID existence check
alone neither authenticates service selection nor prevents discovery startup.
If this cannot be established, keep the stopped edit denied.

Executable/service selection is a named capability prerequisite, not a claim that
the prototype's default discovery is production-safe. The native CLI documents an
explicit `--server` connection, but installed source shows that this path uses
existing password authentication, whereas shared discovery supplies its native
endpoint authentication. No supported source of a pinned, authenticated same-server
connection has been qualified for this integration. Do not copy a discovery token,
extract credentials, add a user setting or fall back to automatic service startup
to fill that gap. Resolve it through supported host capabilities before the positive
built-in campaign; report blocked if unavailable. Offline design/testing can proceed.

Wait on correlated native form reply/cancel events through the existing event
stream, with one authoritative GET immediately after creation to cover settlement
before waiter installation. A matching event triggers authoritative state readback;
event contents alone do not authorize. No periodic form-state polling, automatic
reply or model request is added.

Native reply/cancel events can be published before native state is updated. If
the event-triggered GET still says pending, allow at most one additional
confirmation GET, only for that already observed terminal event and within the
remaining acquisition deadline/operation budget. Coalesce duplicate events. This
is bounded event-triggered reconciliation, not a polling loop or repeated create.
If state still is not terminal, deny as unconfirmed settlement, invalidate the
attempt and reconcile only its owned form. Do not wait for a second settlement
event that the native API does not promise.

Before returning from evaluation, require exact answered approve, same form/session,
same active generation and stopped target, unchanged native change binding and
unchanged file digest. Claim the one-use permit synchronously after the last
await and recheck, so another pending request cannot claim it concurrently.
The target binding includes the hold incarnation: resetting and re-holding the
same path invalidates old approvals and completions within the same generation.

After successful verification, restore the incoming native permission effect,
not unconditional `allow`. An existing native `ask` may still require its ordinary
permission decision; a configured native deny never reaches this hook. Saved
allows or auto-accept cannot replace the separate form prerequisite.

### Complete only the authorized action

Remember an approval-qualified permit under the full native source/session tuple,
not a bare call ID or file path. `tool.execute.after` consumes that record once.
Only matching `completed` status may tell the state machine to clear the stopped
entry. An ordinary completed edit is not evidence that a stop was approved.

The native form is one-use even if execution later fails or the native permission
is rejected. Keep the hold and require a fresh form for a later stopped attempt.
Reject any repeated/ambiguous edit authorization under the same call in this
single-edit slice. Missing or evicted correlation denies release, never infers it.
Keep the target occupied through any subsequent native permission wait and tool
execution, not merely until form readback. Native post-format completion metadata
need not equal the pre-format proposed diff; match call/session/hold identity and
successful status without disabling native formatting.

Once that matching completion clears the stop, later requests follow the ordinary
fresh-file rules. This is not a permanent one-edit quota. Requests already
classified as stopped cannot borrow another request's permit or form answer.

## Lifecycle, bounds and compatibility

- Keep one pending approval per held target; reject another already-stopped
  request without creating another form. Bound bookkeeping by the existing
  remembered-call capacity; never evict an active approval to authorize a new one.
- Proposed fixed total approval-operation deadline: 120 seconds, including a
  final 10 seconds reserved for owned-form cancellation/reconciliation. Native
  subprocess timeouts fit remaining time; use monotonic elapsed-time accounting.
  Stop approval acquisition before that reserve. This deadline does not bound a
  separate native permission wait or tool execution after the form hook returns.
- At most six selected form API operations per attempt, including an optional
  owned-nonce lookup after uncertain creation and cancellation. Reserve cancellation
  capacity before either confirmation GET; if unavailable, deny. Normal operations
  are create, initial GET, event GET, at most one pending-state confirmation GET
  and cancellation when needed. Uncertain-create reconciliation is a different
  branch, not a new create allowance. Never retry create blindly. Discovery,
  authentication and separately allocated capability checks are not form operations;
  report their actual HTTP/process costs separately.
- No new process or form operation while nothing is stopped. At a stopped attempt,
  costs are bounded native CLI children and local file/diff hashing, not inference.
- Rejection, cancellation, timeout, interruption, bridge failure, unexpected form
  state, event loss or cleanup abort settles the pending request as denied. Cancel
  only forms proven owned by that generation/attempt. Report unconfirmed cleanup;
  never reset a deadline or convert an expired observation to success.
- Cleanup invalidates permits and wakes pending waits before disposing resources.
  An old generation can neither authorize nor clear a new instance's hold.
- This does not make the original gate durable or fail-closed after complete
  disablement/event-stream loss/reload. The existing instance-wide fail-open
  behavior remains a limitation; pending known-stop operations themselves stay
  denied on approval failure. Do not claim reload-proof enforcement from it.
- Root-message reset remains a distinct user override. If it changes the held
  target while a form is pending, invalidate that attempt rather than letting a
  late reply or completion clear subsequent gate state.

This design supersedes the original design's native-ask stop-stage release only
after approval and implementation. It does not otherwise rewrite its accepted
plan-review changes. Stop-stage bridge failures deny known stopped work, without
turning ordinary unheld operations into a new global fail-closed mode.

## Verification and review gates

1. **Offline test-first.** Existing gate/adapter tests cover native identity and
   diff binding, pending/reject/cancel/timeout, bridge and metadata errors, approval
   plus existing native ask/deny, stale content, same IDs in another session,
   generation/reset invalidation, concurrency, failures/interruption, duplicate or
   unrelated completion, late callbacks and unsupported tools. Include terminal
   event before authoritative settlement, reply during initial GET, duplicate events,
   reset-and-rehold, native ask following form approval and before/after-first-hash
   external-write interleavings. Do not label the pre-hook observation gap detected.
   Prove ordinary
   repair/escalation/reset paths unchanged. No mocked completion is a live claim.
2. **Cleanup prerequisite.** Inspect the independent cleanup repair and its actual
   proof before reusing it. Do not take over its files or infer it passed. Save
   case results even when teardown fails; preserve both verdicts independently.
3. **Fresh bounded native qualification.** Write a separate allocation and source
   pins before activation. Use disposable sessions/files and the candidate adapter,
   reach stop through its real observed signals, and invoke the original built-in
   `edit`, not permission.assert followed by a custom writer. A model-emitted tool
   call, if needed, uses the user's native route with explicit request/time caps;
   no substitute billing, new provider, global auto-accept toggle or service restart.
4. **Acceptance.** Observe no write while pending or after reject/cancel; native
   auto-approval alone cannot release; one exact approved native edit completes and
   clears only its matching hold. Correlate native form ID/state, tool source,
   permission evaluation, successful completion and actual file bytes. Preserve
   historical failures; concurrent-unit proof and manual UI proof stay separate.
5. **Independent review.** Review the exact candidate and source/native evidence
   for behavior, async/error handling, permission scope and truthful claims. Validate,
   commit, run affected committed tests and publish draft/BLOCKED. No release,
   installation update or promotion follows from a mocked or partial result.

Independent design review is complete, and the user approved the first milestone's
no-concurrent-external-writer assumption on 13 November 2026. The user subsequently
requested implementation with deterministic tests in a fresh worktree, no live
qualification and draft/BLOCKED publication. The [implementation plan](../plans/2026-11-13-opencode-stop-form-implementation.md)
records that scope. Default native form transport remains unavailable; candidate
stopped edits deny rather than discover/start a service or copy credentials.

## Initial review disposition

The independent builder/code/security review found two medium design issues.
Current-file hashing does not establish the executor's earlier full before-image;
the proposal now names the exact weaker observation, exclusive-writer assumption
and unsupported stronger guarantee. The user approved that scope for the first
milestone on 13 November 2026; this does not waive or relabel any historical failed
criterion. Native reply events can precede state settlement; the
proposal now defines one bounded confirmation read and denied unconfirmed outcome.
Neither finding is an observed shipped vulnerability or a newly run live failure.
Executable/authenticated same-service selection, complete form-description display,
pending-hook scheduling and repaired cleanup remain capability prerequisites for
the positive native milestone. Review is not evidence that these already work.

## Sources and discovery limits

- Current implementation: [adapter](../../../runtime/adapters/opencode_workflow/index.ts),
  [state machine](../../../runtime/adapters/opencode_workflow/gate.ts) and
  [client contract](../../../clients/opencode-escalation-gate.md).
- Original [accepted design and superseding review changes](2026-11-12-opencode-escalation-gate-design.md).
- Official [server plugin guide](https://opencode.ai/docs/build/plugins/server)
  and [API reference](https://opencode.ai/docs/api), read 13 November 2026. Public
  documentation can drift; installed native behavior must qualify the integration.
- Context7 lookup was unavailable because its monthly quota was exceeded. Official
  documentation and read-only installed-source inspection were used instead.
