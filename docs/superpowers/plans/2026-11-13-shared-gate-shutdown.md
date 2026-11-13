# Shared-server Gate Flow and Shutdown Implementation Plan

> **For agentic workers:** Use `executing-plans` inline; one independent final reviewer checks the new evidence. This is qualification, not product implementation.

**Goal:** Qualify native shared-server gate flow, actual approval presentation, clean exit after work and disconnect during owned child work, without Smart Routing.

**Architecture:** Reuse released 0.1.39 runtime bytes, the successful native observer and corrected PTY draining. Use disposable repositories and actual TUI-created roots/children on the existing shared server. A controller-owned local socket keeps one native child visibly active during client exit; it does not simulate gate events.

**Tech Stack:** Python standard library, dependency-free TypeScript observation, existing native OAuth and supported OpenCode CLI/API.

**Spec:** User: "Proceed with the shared-server gate-flow and shutdown tests first, keeping Smart Routing deferred." Existing [gate contract](../../../clients/opencode-escalation-gate.md), [accepted design](../specs/2026-11-12-opencode-escalation-gate-design.md) and its superseding plan-review changes apply.

## Global constraints

- No Smart Routing work, semantic judge, fallback route, productivity trial, product fix, threshold change, dependency install, credential copying or global configuration change.
- Never restart, replace or stop the shared server; verify its native process identity before/after. Software versions are provenance, not exact-release gates. Current observed CLI/server are 2.2.7 and meet the recorded 2.2.6 minimum.
- Preserve coordinator settings and actual user permissions. Fixture permissions allow only declared canary edits, read-only status, exact checks, one controlled socket-barrier runner and native general children.
- Use native tool/permission/event observations and independently checked bytes. Model reports alone do not pass a case. Approval decisions must originate from actual TUI input, not API replies.
- Stop cases at their original allowance. Total live cap: **80 requests and 30 minutes**, including children/title/auxiliary requests; fixed shares 44 and 36. One attempt per case; no replay of completed steps, automatic retries or allowance reset. Required evidence review is a separate bounded native workflow assignment, not a hidden live probe.
- State lives only in memory. Do not reload fixture plugins during a live hold. Keep all protected originals and failed observations.
- User messages/approval apply only to declared disposable canaries. No question-tool reset offer or reset command/tool.
- Close only owned frontend processes. Shared backend work may continue after disconnect; verify its identity/state, release the controlled barrier, and wait for owned sessions to finish. On failure, interrupt only identified owned sessions before takeover.

## Review focus

1. Confirm real shared-process attachment, not a new private server or merely the same version string.
2. Correlate root/child denials and stronger edit-free diagnosis with actual native identities, routes, ordering and file effects.
3. Reject/allow-once input must target the real canary permission dialog. Never select Always allow or fabricate an API approval.
4. Keep draining terminal output until EOF; never label signal termination or orphaned/unknown work a clean exit.
5. Preserve original budgets, protected hashes, unrelated work and failed preconditions; do not extend claims to judging or productivity.

## Task 1: Prepare bounded native cases

**Files:** Private controller, observer and fixtures under the existing user-private preparation root; no runtime edits. Later evidence uses `tests/fixtures/opencode_workflow/shared-shutdown-2026-11-13.json`.

**Interfaces:** Consume the existing observer's model/tool/permission/completion records and corrected terminal draining; produce original protected hashes, case allocations, actual TUI control traces and independent acceptance checks.

- [x] Baseline `python3 -B scripts/test_sharded.py test_opencode_workflow`: two Python tests passed, including 77 Node assertions.
- [x] Reuse the prior RED/GREEN PTY-drain proof and idle attachment/exit results; do not replay them.
- [x] Self-check minimal terminal cell decoding, unique permission-target selection, bracketed prompt input, request counting and rejection of generic errors/zero exits as failed-check proof. Expected: deterministic assertions pass without inference.
- [x] Preflight exact failing check and socket-barrier commands outside inference. Expected: fixed unittest mismatches; socket handshake blocks until release and exits 0. Pin sources/configuration and original canary bytes.

## Task 2: Run two isolated shared-server cases

**Files:** Immutable private observations/terminal captures and sanitized summaries. Runtime and original evidence remain unchanged.

**Interfaces:** Consume Task 1 fixtures/allocations. Produce correlated case verdicts, final file hashes, process/session closure observations and retained limitations.

### Case A — hold, release and active-child disconnect: 44 requests

- [x] Actual TUI creates a root; three native edit/failing-check pairs hold canary.py. Root and same-level native child edits are denied.
- [x] A fresh native general child at explicitly requested stronger fixture effort diagnoses without editing. Confirm actual primary route and successful completion; next canary repair to 99 succeeds and its check passes.
- [x] Dispatch one controlled read-only child running the socket barrier. Confirm native child identity, real handshake and active server execution before closing the frontend through its exit key.
- [x] Expected: frontend exits 0 without signal; shared server identity is unchanged and owns the still-active job. Release only the controller-owned barrier; await child/root completion through supported native waits.
- [x] Reattach to that completed root without another prompt/model request; exit 0 again after transcript rendering. Verify final canary 99 and all protected bytes.

### Case B — actual approval dialog and completed-work exit: 36 requests

- [x] Use a declared highest-ranked fixture effort to reach re-evaluation without three synthetic escalation cycles. Three actual failures hold the file; a same-effort edit-free architecture assessment releases once.
- [x] Two more edit/failure pairs reach stop because no stronger ranked option remains. Request one canary edit and capture the real permission UI.
- [ ] Select only the visible Reject target; verify native reject reply, unchanged bytes and retained stop. Submit a new user prompt through the TUI for the separately declared approved edit.
- [ ] Select only visible Allow once, never Always allow; verify native once reply, successful patch, cleared hold and passing check.
- [ ] Exit via the configured exit key after actual root completion; expect return code 0, no signals, no owned active sessions, and unchanged shared server identity/protected inputs.

## Task 3: Review, checks and delivery

**Files:** `docs/qualification/2026-11-13-shared-gate-shutdown.md`, sanitized fixture above, and factual qualification-status updates in `clients/opencode-escalation-gate.md` where warranted.

**Interfaces:** Consume native observations and failure history; produce independently reviewed scoped claims, current package checks and a PR under the recorded lifecycle.

- [ ] Review raw event/terminal/configuration/process evidence independently; distinguish active-job preservation, completed-work exit, backend enforcement and actual approval UI.
- [ ] Validate package; commit, then run affected gate/export/feature-consumer tests. Reuse unaffected earlier proof with exact candidate/base boundaries; no release or unrelated full-suite replay is requested.
- [ ] Publish draft `[BLOCKED]`; promote/merge only if this scope's required evidence passes. Preserve productivity and semantic judging as separate deferred work, not passed claims.
- [ ] Synchronize primary without switching it; perform only eligible authorized cleanup, preserving private evidence and any retained resources.
- [ ] Report passed, failed, blocked and unrun observations separately. Stop at sufficient proof or a real scoped blocker/limit; never retry an unchanged failed live case.

## Retained execution checkpoint

Case A passed retained native/file/process/terminal acceptance with 27/44 requests.
Case B failed with 22/36: native `once` approval preceded the controller's intended
Reject input, and its pre-click snapshot already contained 6 rather than 5. The
approval source is unknown. Its assertion failure forced the owned frontend closed
and recovery reconciled both owned sessions as inactive. The shared server stayed
PID 61057/version 2.2.7; protected inputs and released runtime bytes remained intact.

Total 49/80 requests. Do not spend the remainder replaying either case or reset the
allocation. Controlled Reject, deliberate Allow once, and Case B's clean exit stay
unqualified. Continue only independent evidence review, checks and partial-results
publication. Keep its PR draft `[BLOCKED]`; this checkpoint does not waive missing
qualification or authorize merge. A further live attempt needs approval-source
investigation/isolation and a separately bounded decision. Smart Routing, semantic
judging and productivity remain deferred. Automatic title synchronization is paused
after native `manual_title`; preserve the user's current title.

## Continuation investigation

The user requested continuing dependency-ready implementation-plan work. Read-only
inspection found an enabled matching Desktop approver: its installed server-wide
permission listener sends `once` independently of terminal prompt mode. The
installed pure state-machine check passed without inference/network/settings
effects. Historical actor attribution remains incomplete. See the report's
read-only continuation section and retained private investigation receipt.

Approval isolation remains a real dependency: existing fixtures use native
project `global`, and the Desktop listener can apply its default before resolving
an unknown session's project. A fixture override alone is not proved safe.
No supported GUI control is installed/discovered. Preserve settings; do not edit
native databases, silently disable Desktop approval, close the app, install a
driver or restart the shared service. An explicit supported/manual isolation
decision is needed before a separately bounded approval-only follow-up. No prior
case or allowance was replayed/reset. Remaining original mechanical implementation
tasks are already complete; judge implementation still waits for Smart Routing.

## Authorized fixture-only isolation outcome

The user disabled Desktop global auto-accept and requested a fixture-only
isolation test. Before and after, persisted autoApprove was false with no project
overrides. A new API-created idle root made no prompt/model/tool calls. Its first
native canary permission request was asked, then replied once after 19 ms, before
any controller permission-choice input. Stop condition fired; no request replay occurred.
Only one of the two allowed permission requests ran; the two-minute allocation
permitted zero model requests. Original Case A/B budgets/results stay unchanged.

The fixture stayed byte-identical, the shared process stayed 61057/2.2.7, and final
root observation showed inactive/no pending permissions. Failure recovery forced
the owned frontend closed; do not count it as clean exit. Controlled Reject,
deliberate Allow once and a new live gate case were not run. Persisted settings
alone do not prove runtime/client isolation. Approval origin remains unknown;
caller-level attribution or supported isolation of the answering client/path is
the actual next dependency. Do not repeat the user's already-observed setting
change, alter other clients or restore global auto-accept without authorization.

## User-requested read-only approval-writer trace

Installed CLI source traces create→ask→park→Asked separately from explicit
reply→Replied→settlement. Saved allows do not synthesize this sequence, and service
close replies reject. Candidate writers include TUI manual/auto modes, Desktop
manual/auto modes, owned headless run auto, ACP chooser and API/plugin calls.
Retained event/log fields do not identify the approving caller. Complete controller
protocol responses did not parse as Return in an offline installed-parser check;
historical streaming input remains untraced. No fixture/native request replay or
settings/service change occurred. See the report's new writer-trace section.

A dormant isolation-driver precondition was also found: save=[] omits Always allow,
but its label guard required that choice. It was not reached before the observed
early approval and did not cause it. Keep executed evidence intact; correct that
guard only before a separately authorized new attempt. Qualification remains
blocked on caller attribution or supported isolation, not another unchanged run.

## Astra approval-provenance conclusion

User explicitly selected native openai/gpt-6-astra#xhigh for a bounded read-only
investigation. Installed 2.2.7 manual and automatic approval share the same reply
API without trusted fresh-human provenance. Coordinator verified decisive installed
source hashes and callbacks. Current stop maps to ask and clears after a completed
host-authorized edit; it cannot attest that a human made the approval. Historical
responder attribution or an isolated successful click would not fix that limit.

Documented recommendation, not an accepted or implemented redesign: keep stop edits
denied until an explicit decision/reset, reusing the existing root-message override.
That removes the automatic-once release path but retains the accepted API/plugin
prompt residual; root user-message intent is not authenticated human presence.
A strict fresh-human guarantee needs a trustworthy upstream pre-execution boundary.
Retaining current native-approval semantics instead would need an explicit decision
to weaken that requirement. See the report's Astra section for proof and options.

No runtime/settings/permission changes, live probe, fixture replay or renewed case
allowance. Smart Routing/judge remain deferred and PR #289 remains draft BLOCKED.
Any future behavior change needs its own design review and qualification. Existing
failed observations remain failed. The original isolation digest still matches its
exact five-record prefix; one later cleanup record changes only the current
whole-file digest, as reconciled in the report without rewriting raw evidence.

## User-authorized question-box qualification

User now requests testing whether an API client can answer a native form and
whether its recorded ID/session/answer can gate edits. This authorizes a new
disposable qualification, including API replies to its forms, not replay of the
old permission fixture or replacement of the released gate. Existing prohibitions
remain for old cases, global settings, shared-service restarts and production code.

**Test design:** use the existing shared native service and supported API with a
new fixture-only plugin. A fixed canary writer calls native `permission.assert`
before writing. Its fixture `permission.evaluate` hook defaults to deny and permits
one exact, server-bound action only after native form state records approval.
Bind form ID, owning session, target, requested value and pre-edit content digest;
consume approval before the write. Observe actual form events separately, not as
the authority to write. This is a qualification prototype, not a product redesign.

**Limits:** one live attempt, five minutes, at most 120 API calls, two owned idle
sessions, six owned forms, zero model requests and no frontend/manual-click claim.
No global/Toolkit feature settings changes, permission replies, dependencies,
service restart, original evidence mutation, judge or Smart Routing. On failure,
retain evidence and stop; cancel only owned pending forms and disable only this
fixture's registrations. Do not delete retained fixture/session evidence.

- [x] Test the pure binding predicate first; observe RED for absent guards, then
  GREEN for pending/reject/cancel, wrong ID/session/action/answer, consumed and stale
  before-image cases. Pin fixture source/configuration before native startup.
- [x] Test native API answer validation and wrong-session ownership; verify native
  recorded form ID/session/answer and actual canary bytes before/after.
- [ ] Test deny-before-answer, rejection, cancellation, exact approval, tampered
  action, stale form/generation, duplicate consumption and concurrent attempts.
  Characterize a single pair of conflicting API replies without using ambiguity
  to authorize a write. Do not infer general race-freedom from one observation.
- [ ] Reconcile owned forms/registrations and unchanged service/protected bytes;
  independently review the evidence, validate and publish scoped results in PR
  #289. Keep original human-stop qualification BLOCKED. Distinguish native
  permission-assert/RPC prototype from untested built-in edit-tool integration,
  manual question UI, restart persistence and physical-human authentication.

### Retained question-box execution checkpoint

Pure predicate GREEN covers exact approval plus 16 denials, not native enforcement.
Controller startup raced asynchronous plugin activation; continuation stopped
before cases on unexpected closed/ready replacement. Both guards failed before
sessions/forms/edits and remain retained. Current fixture registrations were closed
and no gating case was replayed or run. Startup/lifecycle diagnosis was escalated
read-only before repairs; no renewed live allocation exists.

Dependency-independent native API test passed under the same original allocation:
two idle sessions, one form, wrong-session404, invalid-option400, exact API approve
and ID/session/answer readback, sequential duplicate409. Canary remained0, no owned
pending forms/active sessions at final observation, same shared process. Total
17/120 API calls, zero edits or initiated prompts/model requests. Actual native
pre-edit binding/consumption/concurrency cases remain UNRUN; overall qualification
is partial. See the report's question-box section. No human-origin claim follows.

Astra source diagnosis separates the known async-readiness flaw from unattributed
replacement. A third ready occurred after explicit instance close, so that reply
was not durable unloading. More importantly, installed plugin setup/host contexts
lack ctx.session.form, though the HTTP client exposes it. The prototype's assumed
create/get/cancel surface is unsupported; no native gating case reached it.
Do not fix readiness alone or substitute untrusted answer snapshots.

Recovery retained the original config and removed only this owned fixture's plugin
entry. Three recovery API calls observed inventory absence/RPC unavailable and
unchanged shared PID. No global reload: that endpoint rebuilds every location.
Original test remains17 calls; recovery adds3, no renewed live allocation. Before a
separately authorized gating attempt, source-qualify authoritative form access and
an exact generation/capability readiness barrier. Runtime implementation remains
outside this qualification. Parent/sibling watching is not a proved cause.
