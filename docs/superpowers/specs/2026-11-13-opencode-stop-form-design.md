# Explicit stop-stage approval for one native edit

## Status and authority

Proposed design for independent review and user approval. The user asked to proceed
with the next Escalation Gate milestone and submit it for review on 13 November
2026. This document does not change runtime behavior or authorize a release.
Implementation planning follows design approval; no new live allocation is spent
by writing or reviewing this proposal.

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
  proposed native change and pre-edit content. It cannot authorize another call.
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
   the approach the user authorized exploring; the detailed design remains pending.

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

Create one owned native form in the requesting session. Its text names the held
file, stop reason and exact native proposed change; its explicit options are
approve this edit or reject. A digest is an identity check, not a useful substitute
for showing the change. Store only correlation fields in form metadata; keep
private file content and raw receipts out of public logs and PR bodies.

Use the installed CLI's supported form API and existing authentication, as the
qualified bridge did. The installed plugin context lacks `ctx.session.form`;
do not assume documentation's client-like context supplies it, install an SDK,
copy credentials or add a separate HTTP authentication implementation. Resolve
the running native executable through supported host facts and verify its service
identity/capabilities before allowing an edit. An executable/PID existence check
alone neither authenticates service selection nor prevents discovery startup.
If this cannot be established, keep the stopped edit denied.

Wait on correlated native form reply/cancel events through the existing event
stream, with one authoritative GET immediately after creation to cover settlement
before waiter installation. A matching event triggers authoritative state readback;
event contents alone do not authorize. No periodic form-state polling, automatic
reply or model request is added.

Before returning from evaluation, require exact answered approve, same form/session,
same active generation and stopped target, unchanged native change binding and
unchanged file digest. Claim the one-use permit synchronously after the last
await and recheck, so another pending request cannot claim it concurrently.

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
- At most six selected form API operations per attempt, including an optional
  owned-nonce lookup after uncertain creation and cancellation. Never retry create
  blindly. Discovery/authentication HTTP traffic is separate from this count.
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
   unrelated completion, late callbacks and unsupported tools. Prove ordinary
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

The next deliverable is this design's independent review and the user's approval.
Runtime changes, implementation planning and a fresh live campaign remain pending.

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
