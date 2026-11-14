# Desktop Stop Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package a portable native connector and complete-change Desktop panel that authorize one stopped original builtin edit only through exact dual-gated approval.

**Architecture:** Extend the existing `StopApprovals` owner with in-memory viewer submission state. A separate OpenCode entry registers the shared native RPC and supplies a lazy official client; a native Desktop panel performs the ordinary user reply. Preserve the generic Python package and relative resource layout in a separate prepared OpenCode artifact.

**Tech Stack:** TypeScript, Node built-in tests, official `@opencode/client` and OpenCode plugin/GUI SDK, the official GUI Vite preset, Python standard-library packaging.

**Spec:** [Desktop-first design](../specs/2026-11-14-opencode-desktop-stop-approval-design.md), including preserved requirements in the linked original design.

## Global Constraints

- Keep PR #294 draft/BLOCKED; no merge, release, registry publication or installed-package update.
- No new authentication, credential copying, global permission change or shared-service restart.
- One original builtin `edit`; no custom writer, patch executor, model call or polling.
- Preserve native allow/ask/deny, ordinary edits, earlier escalation stages and root reset.
- Preserve exact generation/tree/session/message/call/hold/change/file binding and successful-completion release.
- Preserve the approved no-concurrent-external-writer assumption from native preparation through commit.
- Support one regular non-symlink UTF-8 file up to 1 MiB and complete native metadata up to 64 KiB.
- Start acquisition before connector work; 110 seconds acquisition, 120 seconds total, eight seconds maximum per owner operation.
- Reserve cleanup; at most six owner form operations. Count the GUI's one native reply separately.
- No automatic create/reply retry; unknown reply outcome denies, even if later native state says approve.
- Desktop's own local background server only; other clients/remote presentations remain unsupported.
- No vendored `node_modules` or generated GUI output in the generic export; preserve its 8 MiB, 1 MiB/file and 4096-path limits.
- Keep every historical failure, allocation and receipt unchanged. Allocate native Desktop and builtin campaigns separately.
- Native title synchronization remains paused. Do not change the coordinator's model or authentication.

## Review Focus

- A default composer approval before or racing viewer submission must not become retroactive authorization.
- Native reply events, a fulfilled void HTTP 204 and viewer acknowledgement can arrive in either order without provisional release.
- A second window, duplicate IDs, close/server switch and reset/re-hold cannot spend, replace or resurrect an attempt.
- Long lines, control/bidirectional text and narrow windows cannot hide part of an approvable change.
- A prepared artifact must find Python helpers without the checkout and must not bundle host GUI runtimes or start a service.

---

## File responsibilities

- Modify `runtime/adapters/opencode_workflow/stop.ts`: same owner, bounded viewer protocol, existing native permit/completion lifecycle.
- Modify `runtime/adapters/opencode_workflow/index.ts`: narrow optional registration seam and cleanup, without adding dependency requirements to generic imports.
- Create `runtime/adapters/opencode_workflow/connector.ts`: lazy supported same-service `StopForms` transport; no discovery until a stopped attempt calls it.
- Create `runtime/adapters/opencode_workflow/rpc.ts`: native shared RPC declaration, strict bounded JSON schemas.
- Create `runtime/adapters/opencode_workflow/server.ts`: OpenCode-specific entry and owner/RPC/event integration.
- Create `runtime/adapters/opencode_workflow/viewer.ts`: testable single-submission frontend controller and exact text representation checks.
- Create `runtime/adapters/opencode_workflow/gui.tsx`: native `Panel` contribution, host controls/theme, accessible complete-text viewport.
- Create scoped `package.json`, lockfile and `vite.config.ts` under `runtime/adapters/opencode_workflow/`: declared dependencies and one prebuilt CJS GUI output.
- Create `scripts/package_opencode.py`: prepare an owned separate artifact from the clean generic export, preserving helper layout; no automatic dependency install or client loading.
- Extend `tests/opencode_workflow/stop.test.ts`; create focused connector/viewer suites and `tests/test_package_opencode.py`; include suites in `tests/test_opencode_workflow.py`.
- Update `clients/opencode-escalation-gate.md`: source/build instructions and explicit native qualification limits, not an installation change.

Use different filenames only if actual SDK/build constraints require them; record any substitution. No unrelated gate refactor or new option framework.

### Task 1: Deliver the complete source and disposable build path

**Interfaces:**

- Retain `StopForms.create/get/cancel` and existing `StopApprovals.before/evaluate/after/event/reset/close`.
- Add owner `pending`, `open`, `begin`, `complete`, `abandon` methods implementing the design's exact fields. A view payload contains generation, attempt/session/form IDs, canonical file, reason, complete patch, content digest and remaining time.
- `begin` takes attempt/view/submission IDs, digest and approve/reject decision. Its identical replay shares one pending GET and ticket result. `complete` takes that ticket and settled/failed outcome; it never trusts claimed native state.
- Define `StopReview` with `Rpc.define` in `rpc.ts`; JSON Schema uses `additionalProperties:false`, bounded identifiers and payloads. `server.ts` registers it with `ctx.rpc.register` and disposes it with the plugin.
- Add one optional owner-registration callback to `createWorkflowPlugin` configuration so the separate entry can register the same owner. Generic imports/tests must not need GUI/client dependencies. Preserve existing explicit unavailable default behavior outside the prepared entry.
- `createStopForms` in `connector.ts` returns `StopForms`, uses the official client and accepts only narrow deterministic loader/fetch test seams. No public feature setting is added.
- `viewer.ts` owns one mount/view/submission, reads the live client before each action, and performs begin → one native reply → complete. It aborts/abandons on invalidation and never automatically retries a reply.
- `scripts/package_opencode.py --output <owned-new-directory>` prepares the clean resource export and root manifest/exports pointing into the preserved adapter path. Its output contains no dependencies until explicitly prepared. It must reject unsafe/foreign output rather than overwrite it.

- [ ] **Step 1: Write focused failing owner tests.** Replace old native-answer-only positive fixtures with explicit viewer submissions. Pin early/default answer denial, pending GET replacement, committed receipt plus native approval, event/receipt ordering, one-use native completion, allow/ask/deny, duplicate/two-window selection, close/reset/generation changes, source/file mutation and operation/deadline bounds. Do not remove the original safety assertions merely to make fixtures pass.
- [ ] **Step 2: Observe RED.** Run `node --test --test-reporter=tap tests/opencode_workflow/stop.test.ts`. Require missing viewer-gate assertions to fail, not unrelated imports or fixtures. Save the decisive failure summary privately.
- [ ] **Step 3: Implement owner protocol and core registration seam.** Keep the same attempt/target/cancellation owner. Select submissions synchronously; `begin` owns one pending GET. Wait for committed submission before granting; events remain hints. Retain single-flight cleanup and occupancy through original native execution.
- [ ] **Step 4: Write failing connector/controller/artifact tests.** Pin lazy dependency loading, discovery without ensure, authenticated same-PID/location/endpoint checks, abort/redirect guards, missing/incompatible capability denial, void HTTP 204, failed/unknown reply, current-client checks after every await, exact text fidelity, no unsafe rendering, and foreign-output rejection/helper paths/generic membership.
- [ ] **Step 5: Observe those RED failures.** Run the new Node suites individually and the focused Python packaging test. Require intended behavioral failures. Canonical sharded checks wait for coordinator commit; direct TDD uses disposable inputs and no native service calls.
- [ ] **Step 6: Implement connector, RPC, Desktop controller/panel and separate artifact preparation.** Reuse official APIs and host controls, current client and reactive session references. Scope pending snapshots/events by exact tree/location. Mount the full safe text with horizontal/vertical scrolling and fixed reachable controls. Deny unsupported representations rather than strip text. Disable invalid/submitting actions and distinguish decision acceptance from edit completion.
- [ ] **Step 7: Prepare and build one disposable artifact.** Inspect declared package scripts/dependencies first. Use existing Node/npm and the official preset; no new global tool. Prepare dependencies with scripts disabled in the owned artifact, using a lockfile, then explicitly run the GUI build. Verify one CJS output, shared externals, helper-relative paths, actual sizes and absence of checkout/cache imports. Missing registry/build capability blocks this check, not independent source work. Do not load or install into the personal client.
- [ ] **Step 8: Run GREEN and self-review.** `node --test --test-reporter=tap tests/opencode_workflow/*.test.ts`; focused Python artifact tests; `git diff --check`; `python3 -B scripts/validate.py`. Expected zero introduced failure/skips; existing export warning remains explicit. Review ownership, error paths, bound maps, privacy, resource layout and no unrequested features.
- [ ] **Step 9: Commit and run canonical affected checks.** Coordinator commits the exact reviewed source, then runs `python3 -B scripts/test_sharded.py test_opencode_workflow test_features test_package test_package_manifest test_package_opencode`. Hold checkout unchanged during the run. Record nested Node counts separately from wrapper counts; no full-suite/release claim.
- [ ] **Step 10: Independent exact-candidate behavior/security review.** One fresh Astra/high reviewer covers authorization/concurrency, client/RPC trust boundaries, dependencies/build and complete-text/accessibility behavior. Resolve substantive findings through the same owner with focused RED/GREEN proof; review rounds stay finite. Static/mocked/build proof does not qualify native Desktop execution.

### Task 2: Qualify native Desktop and builtin execution, then publish retained progress

**Consumes:** committed artifact and accepted Task 1 checks/review. No implementation change bypasses rechecking affected evidence.

- [ ] **Step 1: Establish supported disposable Desktop launch/profile isolation.** Use official documentation and static source first. Require a separate owned native profile/server/configuration without personal credentials copied, personal plugin update or shared-service restart. Ask Astra/high for an evidence-backed recovery if support is uncertain. If isolation is unavailable, record the exact blocker and finish independent delivery work.
- [ ] **Step 2: Allocate a finite Desktop campaign.** Pin source, built artifact, official executable, private launch/controller, allowed native form/RPC/HTTP operations, presentation captures, process/file effects, deadlines and recovery. Keep previous consumed allocations immutable. No actual launch before exact allocation and ownership checks.
- [ ] **Step 3: Exercise native load and presentation.** Prove Desktop's own-server identity, waiting-hook RPC, missing/early/default denial, actual long-specimen first/middle/final/long-line reachability at ordinary/narrow/resized layouts, keyboard/focus/actions and GUI-loss server cleanup. Retain real rendered evidence; API text equality is not display proof.
- [ ] **Step 4: Allocate and exercise the original builtin edit milestone separately.** Use real edit/check/child signals, not seeded Gate or custom writer. Pending/reject/cancel preserve bytes/hold; exact approved native edit alone writes; matching successful completion alone clears its incarnation. Reconcile every process, form, operation and unknown outcome before acceptance.
- [ ] **Step 5: Independently review native evidence.** Accept only its exact scope; preserve failed attempts and disclosed limits. If native work is blocked, retain the source/build progress without a positive-native claim.
- [ ] **Step 6: Publish to existing PR #294.** Reverify actual Git/PR state; non-force push exact task descendant to its existing head branch and update its draft/BLOCKED body/comment with current source/build/native distinctions and blockers. No ready promotion, merge, release or installed-package update. Preserve handoff and worktree for unfinished native scope.

## Execution and authority

User already approved redesign, chose Desktop first and required autonomous continuation unless a real blocker remains beyond Astra/high. Do not add a routine plan/continuation approval. The independent builder check found no material unanswered question on exact design digest `ceb19bb67752441d7e16b88afa59d8b8684eefe63f9e49c6af47b9a84fd61ebe`.

Keep one coupled source outcome with one reusable software-engineer child: owner, connector and viewer must agree on the same protocol and artifact. Coordinator owns design/plan/ledger, commits, lockfile acceptance, independent review, qualification allocations and publication. No parallel source writers. Parent can prepare non-overlapping verification/publication context while the child works. A child checkpoint is not completion of the parent scope.
