# Unconditional Stop Qualification and Release Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` for approved inline verification work, with independent design/evidence and final release review. Unchecked steps are not execution authority.

**Goal:** Verify Claude execution, real native UI behavior and late-completion races, then independently review and fully test the exact release candidate before publishing.

**Architecture:** Keep the merged stop implementation unchanged unless a reproduced defect requires a narrow fix. Separate reusable deterministic proof, genuine native race proof and original-TUI proof; all remain mandatory for overall qualification. A missing UI observation method blocks the UI case, not independently approved race work. Claude qualification and release retain their separate holds.

**Tech Stack:** Python standard library, TypeScript/Node, installed native OpenCode/Claude and repository release tooling.

**Spec:** User's 15 November execution request and subsequent plan-only approval of layered qualification; [current behavior](../../../clients/opencode-escalation-gate.md); [qualification evidence](../../qualification/2026-11-14-unconditional-stop.md). Current unconditional-stop requirements supersede earlier approval-based stop behavior.

## Approval and approaches

After Astra's independent strategy review, the user approved revising this plan,
**without authorizing new qualification execution**. This revision preserves every
behavioral requirement and changes only qualification organization.

- **Combined race/UI campaign:** avoids rebuilding stop state, but its UI-evidence
  prerequisite blocked independent race proof. Retained as historical design,
  not the next execution dependency.
- **Layered qualification — selected:** reuse compatible deterministic evidence,
  then qualify race and original TUI separately. Separate fresh cases may repeat
  stop reconstruction; do not assume separation saves model calls.
- **Product redesign:** not selected. Harness compatibility and missing screen
  evidence do not establish a product defect. Reproduce a state, adapter, lineage
  or native-hook limitation before proposing a narrow fix or broader redesign.

## Global Constraints

- Task worktree: `test/unconditional-stop-release`, from current `main` at `9dea8e51e1b48c19599cc11f001d8be9ad701216`. Preserve the earlier task worktree and evidence.
- Preserve settings, authentication, subscriptions, other sessions, previous failures and consumed allowances. Never reload shared state merely because chats appear idle.
- PR #294 stays deferred, draft/BLOCKED, unmerged and unreleased. No custom approval UI or automatic continuation.
- Existing Claude authentication may be used only through a verified authorized native path. No credential copying, secret inspection, new sign-in, separately billed API route or unapproved shared Keychain refresh risk.
- Native qualification uses fresh exclusive attempt records and finite per-case allocations established before launch. Count all model kinds/actors; no retries or automatic renewal.
- All prior allowances remain closed. The earlier proposed 96 requests, 160 controller actions and 25 minutes remain unapproved; splitting cases neither grants nor duplicates them. Obtain explicit fresh per-case and aggregate limits before execution, including cleanup reserves and finite callback/capture limits.
- Use only the previously selected anonymous zero-cost public route for proposed OpenCode cases, after exact provider/model/variant and free-route admission checks. No fallback or separately billed spill.
- Synthetic public fixtures only. Protect evaluators/configuration; correlate real permission, tool, lineage and completion identities and preserve before/after hashes.
- A timing barrier may delay an original native callback but must not change its fields, fabricate events, invoke production state methods or supply a replacement writer. Label instrumented scheduling separately from uninstrumented execution.
- UI proof must use an actual native frontend. A private standalone TUI is the existing safe candidate; Desktop coverage remains a separate observation, never inferred from API or terminal text.
- No exported renderer-ID interface is prescribed. UI evidence must still distinguish the new native refusal from old redraws, assistant quotations and erased or relocated text. A witnessed alternative needs an accepted evidence procedure and explicit participant approval before use.
- Release only after the requested execution evidence and independent review pass, then a successful full Python suite on the exact final committed release candidate, including version and release notes.
- Use `docs/release.md` for version/tag/package/marketplace publication and disposable install verification. Do not update personal installed clients or other projects as a release side effect.

## Review Focus

- Claude bridge tools must reach the same native permission and completion controls; model text alone is not execution proof.
- Private HOME and filesystem sandboxing do not prevent daemon-mediated Keychain refresh. Resolve that boundary before Claude probes or inference.
- Native UI must show refusal without approval or automatic resumption; retain actual frontend provenance and clean/forced-exit results separately.
- A real edit admitted before the stop must deliver its successful completion after the stop; stopped state and subsequent refusal must persist.
- A race harness must preserve genuine tool/call/session identity and distinguish physical write timing from completion-notification timing.

## Coverage and dependencies

| Required proof | Existing basis and remaining observation |
| --- | --- |
| Stop denial and late-completion invariants | Existing gate/adapter tests, including `tests/opencode_workflow/plugin.test.ts:640–697`; reuse only after source, test and execution-assumption comparison. Not a substitute for native concurrency. |
| Native permissions, lineage and explicit continuation | The recorded 57-request flow covers selected sequential controls. Bind reuse to its exact sources and scope; do not relabel it race or new-TUI proof. |
| Original completion delivered after genuine stop | Task 3, independently admissible after common preflight and its fresh execution approval. |
| Visible native refusal and separate user continuation | Task 4; its evidence method remains unresolved. Task 3 does not depend on resolving it. |
| Claude bridge execution | Task 2 remains held by Preserve login; no OpenCode result substitutes for it. |
| Preservation, route/budget enforcement and cleanup | Required for every applicable live case; incomplete evidence prevents that case passing. |
| Overall qualification and release | Require every required layer, independent evidence acceptance and the exact release-candidate full suite. No averaging or partial-pass release. |

Modify this plan and the owning qualification report when new results arrive.
Reuse private helpers by exact source pins, not by executing old controllers
against old state. Keep failed receipts and the ignored task ledger intact.

## Task 1: Verify prerequisites and safe execution design

- [x] Observe task/primary identities and preserve source/settings; create a native task worktree from current `main`.
- [x] Run focused baseline checks against unchanged source: 79/79 Node cases passed.
- [x] Independently review the Claude isolation/authentication path without starting a probe or reading credentials.
- [x] Inspect the original native hook ordering and existing TUI harness.
- [x] Record exact acceptance, attempt budgets, ownership and stopping conditions before any native inference.
- [ ] Map reusable checks and native receipts to current source/test/environment hashes and invalidate only affected evidence. The completed baseline above is historical, not a fresh release-candidate run.
- [ ] For each public-route OpenCode case, verify the existing isolation recipe before startup: fresh private HOME/XDG/configuration/database/temp and empty credential store; clean environment without real provider keys, proxy or injection variables; filesystem containment including inherited-child restrictions; Claude provider absent and Claude execution forbidden; no Keychain access or shared-service discovery/reload; ephemeral authentication only for the owned server. The separate Claude case remains held and gains no authority from this recipe.
- [ ] Before any new live case, qualify its actual pre-transmission guard offline: exact route admitted; wrong/missing route rejected before an inert downstream sentinel; shared actor/kind counting, exhaustion/deadline boundaries and retry veto checked. Existing observer budget checks alone do not prove route admission.
- [ ] Verify unchanged barrier/schema, completion tracking, timeout, capture and cleanup checks applicable to the chosen case. Failed or missing proof blocks that case; do not add a framework.
- [ ] Record a fresh case allocation, protected source/configuration pins, ownership, preservation checks and independent evidence-review bounds; obtain execution approval. This plan revision supplies none.

## Task 2: Observe Claude execution — held

- [x] Record the unavailable isolated Claude authentication prerequisite without weakening preservation.
- [ ] Exercise original native Claude read/edit/check/status and child permissions through the current stop implementation.
- [ ] Verify unrelated work, source/protected inputs, selected shared fingerprints, actor settlement and owned process cleanup.

## Task 3: Observe genuine native late-completion races

- [x] Implement only the minimal disposable scheduling barrier after source-backed hook-order inspection.
- [x] Self-test its matching, one-shot release, timeout and no-field-mutation behavior.
- [ ] In a fresh private fixture, reach genuine re-evaluation, complete the required original read-only architecture child and perform the fourth repair/check. Never seed or restore gate state.
- [ ] Admit a genuine child edit, verify its physical marker write and hold that same successful callback before the unchanged subject sees it. Correlate root/child, permission source, tool call and callback identity.
- [ ] While held, reach final stop on both files through the fifth real repair/check. Save complete held state and file bytes, then release the original callback once with every field unchanged.
- [ ] Require real child completion, identical stopped state/bytes and a fresh native child edit denial afterward, without a matching approval request. Require an unrelated edit to succeed with matching allowed permission, completed tool identity and exact intended byte change; both stopped files must remain unchanged.
- [ ] Verify protected/prior/shared fingerprints, no internal adapter error, inactive actors and clean owned-server exit/reaping/group absence. Retain separately any preservation uncertainty or forced cleanup.
- [ ] Obtain independent read-only acceptance of correlated raw evidence. A passed race case does not qualify the UI or authorize release.
- [x] Independently assess the failed attempt's raw identities, bytes and preservation; retain unexecuted race/UI limits.

This proves a physical write **before** stop with completion notification **after**
stop. It does not prove prevention of physical writes already admitted.

## Task 4: Observe original native TUI — evidence method pending

- [ ] Review a bounded evidence procedure before launch. It must correlate the actual visible native tool-error block and relevant prompt/tool-input context with this root turn and native permission/tool/message records. Retain continuous visible context and native records, not assistant narration or an isolated substring.
- [ ] If proposing a witnessed interaction instead of automated capture, specify the witness's actions, retained evidence and independent acceptance checks; obtain explicit participant and method approval. This plan does not grant either.
- [ ] Require the method to reject old-refusal redraws, assistant quotations and erased/relocated text; unsupported or ambiguous capture remains unverified. Do not repeat an open-ended source hunt or require internal renderer access merely as a preferred implementation.
- [ ] With common preflight and fresh UI execution approval satisfied, use a new private original-TUI case with two genuinely stopped files. Retain frontend provenance; do not assume a stopped race runtime remains available.
- [ ] Submit a distinct edit request. Require the new native refusal visibly associated with that turn/tool, native denial, unchanged stopped state/bytes and no matching permission approval request.
- [ ] Separately type a root-user request to reset only canary.py and acknowledge without editing. Require only canary released, sibling still stopped, and no tool/permission/byte change or pending-edit resumption.
- [ ] Type a distinct repair request. Require the original edit to 99 and immutable `python3 -B -m unittest test_canary` exit 0 while sibling remains stopped at 5.
- [ ] Verify preservation, actor settlement and clean original-TUI/server exit, reaping and absent owned groups. Forced shutdown preserves safety but does not qualify clean frontend exit.
- [ ] Obtain independent read-only acceptance of visible and native evidence. If association remains ambiguous, record the UI case incomplete; neither a race pass nor human acknowledgment fills the gap.

## Task 5: Independent review and release preparation

- [x] Update the owning qualification report and client status with observed results and remaining limits.
- [ ] Obtain independent behavior/security/evidence review of the exact candidate; fix only substantiated in-scope defects with regression proof.
- [ ] Select the next unused version after observing current manifests/tags and concurrent release state.
- [ ] Update both manifests and release notes; validate, commit, run focused checks, publish a draft PR and follow ordinary readiness/merge rules.

## Task 6: Qualify and publish the release

- [ ] Pin the exact merged release commit in a clean task checkout.
- [ ] Run `python3 -B scripts/test_sharded.py` without changing that checkout; retain full output and failures/skips.
- [ ] Only after that run passes, validate release eligibility and tag that same commit.
- [ ] Build from the qualified tag, inspect the package/source/version, and fast-forward the marketplace release branch.
- [ ] Verify the released package through a disposable installation. Record publication separately from personal installation/activation.

## Current State

**Latest fresh race failed before inference; its allocation is closed.**
Astra's strategy review
supports separating the required layers, not changing the gate to accommodate
its harness. Existing source/tests and the sequential native flow provide
counterevidence to a product-defect claim, not proof of native race/UI correctness.
Deterministic evidence reuse still needs its recorded compatibility comparison.
Race and UI each need fresh allocations; no later layer is marked passed here.

The user explicitly chose **Preserve login** after disclosure of shared Keychain
refresh and separate-lock interference risk. Do not perform Claude probes,
Keychain visibility queries, inference or release while that decision applies.
Astra found no supported Claude path satisfying every preservation constraint.
Only separately approved public-route cases may proceed. None may substitute
for Claude qualification or authorize release.

The single combined public-route UI/race attempt failed at `Timing RPC arm: 400`
after 25 requests and four repair rounds, before race scheduling or frontend
launch. Preserve the receipt; no replay or implicit replacement is authorized.
Owned server cleanup passed. Source/protected/prior-evidence hashes matched,
but shared OpenCode settings changed during the window with unknown attribution.
Coordinator raw checks confirm partial-only coverage; independent assessment
accepted the accurate failure record, not execution qualification. Package
validation and two committed-candidate Python checks passed; the latter include
the unchanged Node suites. The existing bundle-size warning remains.
The failure record is published as draft
[PR #310](https://github.com/fcoouto/vibe-workflow/pull/310), `[BLOCKED]`, unmerged.
No Claude probe, full-suite run or release has occurred. Further native attempts
need a separately authorized allocation after diagnosis; the existing failed
attempt cannot be replayed.

Separately approved model-free diagnosis subsequently reproduced
`rpc.invalid_input` for the timing schema's `sessionID.pattern`. The first
model-free attempt stopped on a premature inventory read; a newly authorized
corrected attempt used a bounded activation wait and captured the untruncated
error. It made three HTTP calls, no model requests, and stopped cleanly.
This identifies a copied-schema failure mechanism, not native race/UI proof or
the missing historical response body. No schema fix or further native attempt
is authorized by that closed diagnostic allocation. Claude and release remain
held; all earlier failed receipts remain preserved.

The user subsequently authorized the one-keyword helper fix and one fresh
three-call/five-minute/no-retry model-free confirmation. A fresh helper copy
removed `actor.pattern`; existing barrier checks stayed unchanged. All 11 offline
checks passed, then native OpenCode 2.2.6 returned HTTP 200 with `armed: true`.
The confirmation used three HTTP calls, zero model requests and 742 ms.
Cleanup, selected shared hashes and prior/source pins matched. This closes only
the diagnostic arm issue; native release/race/UI, Claude and final release
qualification remain pending. No further native call is authorized by that
closed confirmation allowance.
Earlier source and 57-request public-route proof remain unchanged and scoped;
they do not satisfy the newly requested Claude/UI/race observations.

The later reviewed combined proposal never launched. Its approved twenty-minute
offline readiness pass stopped after 112.973 seconds because exact rendered-turn
association was not established. No model/HTTP/SDK/RPC/native-launch/authentication
or test execution occurred in that pass. The new route guard and UI negative
fixtures were not implemented or tested. Earlier helper compatibility fixes
remain valid scoped progress, not evidence that the entire process was churn.

Astra subsequently supported layered qualification and corrected the inference
that trustworthy UI proof requires an exported renderer-ID interface. The user
approved this plan revision only. A witnessed original-TUI procedure is a
candidate, not an accepted method or a request for user participation.

The user later approved one conditional race-only allocation. Preparation
completed in 808.146 seconds with 27 offline checks and two committed-candidate
focused Python checks passing. Astra rejected prelaunch readiness: synchronous
`lsof` and cleanup waits lacked hard deadlines, and the qualification observer
did not treat unexpected normal stream EOF as evidence failure. Both findings
remain open; no native process, model request or authentication action occurred.
The allocation is closed, not available for repair or replay. See
[the owning result](../../qualification/2026-11-14-unconditional-stop.md#race-only-preparation-blocked-at-prelaunch-review--15-november).

The user subsequently authorized preparation-only R1/R2 repairs in a fresh copy.
Five intended regressions reproduced the gaps, then 37 offline checks and two
focused Python checks passed. Astra accepted the bounded code/offline fixes:
deadline-bounded waits keep group checks and failure receipts reachable, and
normal observer EOF closes admission and prevents final evidence acceptance.
The old rejected case remains unchanged and closed. No native execution or
prelaunch admission is authorized by this repair result; a fresh allocation and
exact readiness decision remain necessary. See
[the repair evidence](../../qualification/2026-11-14-unconditional-stop.md#preparation-only-r1r2-fixes--15-november).

The user then approved one fresh race under the reviewed 80-request,
120-action and 20-native-minute limits, within 60 aggregate minutes. Fresh
preparation passed 37 checks and Astra accepted exact-spec prelaunch readiness.
The one native attempt failed its initial gate-status snapshot: HTTP 200 reported
no status tool, executor or state through the observer lookup. It used zero
model requests, 13 actions and
801 ms; no repair/check round or timing/race step ran. Plugin-active inventory
did not establish effective gate readiness; cause remains unresolved.
The server exited zero without forced cleanup, owned processes were absent, and
fixture/source/protected/prior pins and selected shared-window hashes matched.
Astra accepted the failed-run classification and bounded evidence integrity,
not race qualification. Preflight assertions were traversed but complete raw
preflight responses were not retained. Preserve the closed allocation;
zero model use grants no replacement attempt or diagnosis probe. See
[the latest result](../../qualification/2026-11-14-unconditional-stop.md#fresh-race-only-attempt--15-november).
Race/UI, Claude, final review/full suite and release gates remain incomplete.

A separately authorized bounded offline diagnosis then reproduced the missing
Git-checkout prerequisite in the rebuilt fixture. The real feature command fails
before reading its enabled settings; the unchanged adapter consequently registers
no status tool. An identical synthetic Git-backed control resolves the feature
and registers the tool. Both setups return before feature registration completes,
so plugin activation alone remains insufficient readiness proof. Original native
subprocess output was not retained; additional native issues remain unexcluded.
No product/fixture/harness repair or native rerun occurred. See
[the offline diagnosis](../../qualification/2026-11-14-unconditional-stop.md#offline-gate-readiness-diagnosis--15-november).

The user then authorized a preparation-only fix (Git-backed fixture, feature
preflight, bounded readiness wait; accepted at code/offline level) and
pre-approved one conditional fresh race. After readiness YES, the single attempt
passed the status check and failed at round 1 with a sandbox DNS error after one
model request; no race step ran, cleanup passed, and the allocation is closed. A
model-free check showed the restricted profile blocks resolution and one added
resolver-socket allowance restores it (unapplied; it widens isolation). Astra
accepted the failed-run record, not race qualification. A changed profile needs
preparation, independent review and a fresh allocation and approval. See
[the attempt](../../qualification/2026-11-14-unconditional-stop.md#git-backed-race-attempt--16-november) and
[the DNS diagnosis](../../qualification/2026-11-14-unconditional-stop.md#sandbox-dns-diagnosis--16-november).

## Acceptance and stopping

Each case passes only with every assigned behavioral assertion, valid identities,
required preservation evidence, route/budget compliance, clean cleanup and
independent evidence acceptance. Wrong identity/order, cleared stop, allowed held
edit, automatic resumption, wrong-file reset, failing checker, internal error,
route drift, exhausted limits, ambiguous/truncated required evidence or failed
cleanup prevents a pass.

Report behavioral defects, harness/provider failures, preservation uncertainty
and missing prerequisites separately. Stop only the dependent layer; preserve
independently accepted proof. No retry, renewed budget, product patch, accepted
scope reduction or release follows automatically from a blocked case.
