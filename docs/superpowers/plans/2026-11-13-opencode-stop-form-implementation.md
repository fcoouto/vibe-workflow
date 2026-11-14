# Native stop-form approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement and deterministically regression-test form-bound stop-stage permission and completion handling for review, without live qualification.

**Architecture:** Keep the pure gate responsible for hold incarnation and explicit successful release. A small approval owner joins native tool/permission/completion identities, owns bounded form waits and cancellation, and restores the original native permission effect only after an authoritative exact answer. The adapter supplies its existing event stream and lifecycle. No authenticated same-service transport is qualified: the default bridge stays unavailable and known stopped edits deny; deterministic tests inject the narrow form boundary, not the state machine or permission decision.

**Tech Stack:** Dependency-free TypeScript, Node built-in tests and filesystem, Python package/check runner.

**Spec:** [Approved writer-scope design](../specs/2026-11-13-opencode-stop-form-design.md).

## Global Constraints

- No concurrent external writer from native edit preparation through commit; no claim to authenticate the executor's original full-file snapshot.
- One existing regular non-symlink text file, at most 1 MiB; complete native metadata at most 64 KiB.
- Preserve native allow/ask/deny, earlier gate stages, root reset and ordinary unheld editing.
- Acquisition ends at 110 seconds; reserve final 10 seconds of 120 seconds for owned cancellation. At most six form operations, no blind create retry or periodic polling.
- Form events are hints; authoritative state plus exact native source/session/hold/generation/change/current-file binding authorizes one pending call only.
- No live form/permission/service/model operation, new dependency, setting, credentials, installed-package update, release or merge.
- Keep PR #294 draft/BLOCKED. Use fresh task branch from current main, merge approved design history, and fast-forward the existing PR head without touching its old checked-out local branch.

## Review Focus

- Reset and re-hold of the same path must reject both old answers and old completions.
- Reply-before-state, duplicate events and reply during initial read must neither grant pending state nor create an unbounded read loop.
- Native ask/reject/error after form approval must retain the hold, not broaden native permissions.
- Late create/read/cancel and interrupted/disposed waits must never re-activate a generation or borrow cleanup budgets.
- The unqualified default bridge must deny stopped edits without starting a service, spending inference or affecting ordinary work.

---

### Task 1: Implement one owned stop-approval lifecycle

**Files:**
- Modify: `runtime/adapters/opencode_workflow/gate.ts` (stop incarnation and explicit completion release).
- Modify: `runtime/adapters/opencode_workflow/index.ts` (hook/event/lifecycle wiring and injectable form boundary).
- Create: `runtime/adapters/opencode_workflow/stop.ts` (form binding/wait/cleanup owner, no writer or transport discovery).
- Modify: `tests/opencode_workflow/gate.test.ts`, `tests/opencode_workflow/plugin.test.ts`, `tests/test_opencode_workflow.py`.
- Create: `tests/opencode_workflow/stop.test.ts` (real adapter/gate, temporary files, fake native form boundary and clock).
- Modify: `clients/opencode-escalation-gate.md` (candidate behavior and explicit native-unqualified limit).

**Interfaces:**
- `Gate.stopped(session: string, file: string): { root: string; hold: number } | null` identifies a stopped incarnation.
- `Gate.edited(session: string, files: string[], approvedHold?: number): void` never clears a held entry without matching stop evidence.
- `StopForms.create(input: { sessionID: string; form: Record<string, unknown> }, options: { signal: AbortSignal }): Promise<unknown>`; `get` and `cancel` take `{ sessionID: string; formID: string }` and the same options. Responses are unwrapped native form objects. The production default supplies no unavailable service transport.
- `StopApprovals` consumes `gate: () => Gate | null`, optional `forms`, `error(code)`, and an optional monotonic `now` test boundary. Methods `before(event)`, `evaluate(event, files): Promise<void>`, `after(event): boolean`, `event(event)`, `reset(root, files)`, `close()` own correlation and lifecycle. It performs no edit.
- `createWorkflowPlugin` retains its existing feature seam and accepts optional `stopForms`/`stopNow` dependency seams for deterministic tests and future explicitly qualified embedding, not user/global configuration.

- [x] **Step 1: Write and commit failing regression tests.** Change the old implicit-native-approval release expectation. Assert unauthorized `edited` leaves a stopped entry unchanged; exact incarnation clears once and old incarnation cannot clear reset/re-hold. Add real adapter tests using native-shaped source/metadata, actual temporary file hashing and controlled form responses. First RED: default known-stop permission is deny, not ask; an exact form-approved allow remains held until matching completion.
- [x] **Step 2: Run RED.** `node --test --test-reporter=tap tests/opencode_workflow/gate.test.ts tests/opencode_workflow/plugin.test.ts tests/opencode_workflow/stop.test.ts`. Expect assertions showing missing form gating/explicit release, not import or fixture errors.
- [x] **Step 3: Implement pure hold identity and explicit release.** Preserve existing trip/release/reset behavior; deny stale completed-stop mutations without matching incarnation.
- [x] **Step 4: Implement `StopApprovals` and adapter wiring.** Observe full native tool identity; pre-set deny at a stop; reject unsupported tool/resource/metadata/file types and capacity. Generate an owned form ID before create so uncertain creation never needs another create. Show complete proposed diff in required decision field description. Register bounded event notifications, perform initial authoritative read and at most one pending-after-terminal confirmation. Claim once synchronously after final rechecks, preserve incoming native effect and retain target occupancy until matching completion. On failure/interruption/reset/event loss/cleanup invalidate before waking and reconcile only exact owned forms within reserves. Missing transport denies with a generic diagnostic; no discovery fallback.
- [x] **Step 5: Expand deterministic failure-path regressions before each corresponding implementation.** Exercise reject/cancel/pending/foreign form or session/value/metadata/source, unsupported/multiple files/tools, size/symlink/binary limits, stale file, reset/re-hold, old generation/late promise, duplicate authorization/completion, concurrent target, native ask/reject/error, cleanup/event loss/interruption, acquisition/operation/cleanup timeout and six-call reserve. Assert actual gate/effect/filesystem results; no assertion that mocks themselves exist. Add suites to canonical wrapper.
- [x] **Step 6: Run GREEN.** `node --test --test-reporter=tap tests/opencode_workflow/*.test.ts`. Expect zero failures/skips and no outstanding waits; existing adversarial fixed-seed cases stay green.
- [x] **Step 7: Update candidate contract and validate.** State default unavailable native transport, changed known-stop behavior, explicit root override, non-live test coverage and remaining native limits. `python3 -B scripts/validate.py` and `git diff --check` must pass; existing export-size warning stays disclosed.
- [ ] **Step 8: Commit implementation and run affected committed checks.** `python3 -B scripts/test_sharded.py test_opencode_workflow test_features test_package test_package_manifest`. Expect all selected modules pass with zero skips. Check test imports/source isolation and record RED/GREEN receipts separately from native qualification.
- [ ] **Step 9: Independent whole-branch review and publication.** One bounded native behavior/async/security reviewer reads exact candidate, spec, tests and ledger, without live execution. Verify substantive findings and apply focused test-first fixes within original review bounds. Reuse unaffected tests/reviews; keep actual missing native transport/display/scheduling/cleanup qualification explicit. Push the exact reviewed descendant to PR #294, update body and read back full content/head/draft/BLOCKED. No merge or live trial.

## Execution record

The user explicitly requested implementation in a fresh worktree with deterministic tests and no live qualification. Execute this coupled adapter/state-machine outcome inline with one independent final reviewer; splitting its shared lifecycle across writers would add handoffs without an independent implementation outcome. No redundant plan approval is requested. Runtime code in the candidate may change; the installed release and shared service do not.
