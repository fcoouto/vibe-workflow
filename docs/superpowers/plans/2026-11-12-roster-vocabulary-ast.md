# Bounded roster vocabulary checker implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Accept attempt-kind syntax without per-consumer exceptions while continuing to reject retired specialist-role writes.

**Architecture:** Keep the conservative lexical gate and independent role checks. Use standard-library AST expression spans for Python attempt-kind occurrences and the overlapping review-metadata/native-marker exceptions. Do not infer arbitrary dataflow or duplicate the attempt-kind registry.

**Tech Stack:** Python standard library, unittest, existing sharded runner.

**Spec:** The user-approved bounded design and qualification in this conversation, followed by the explicit implementation request.

## Global constraints

- No dependencies, new attempt-kind registry, broad ignores, or changed runtime role-validation policy.
- Preserve Markdown/JSON/JavaScript scanning, stored legacy roles and unresolved-participant behavior.
- Narrow the overlapping `runtime/delegation.py` and `runtime/evidence.py` allowances to actual review metadata/native markers; remove the `runtime/setup_readiness.py` attempt-kind exception.
- Keep real role violations visible. Apply the qualified `unknown` telemetry fallback for unlinked collected receipts rather than inventing a canonical specialist.
- Leave the unrelated `/private` assertion, account settings, O6 campaign and cloud services untouched.

## Review focus

- A valid kind on the same line must not suppress an invalid role or positional fallback.
- Positive role context takes precedence, including chained assignments and nested conditional expressions.
- Unicode columns, multiline syntax and parse errors must not bypass occurrence checks.
- Compatibility records retain their actual stored role; a missing specialist must not erase a known thread's usage.
- Syntax recognition is bounded; computed strings and arbitrary cross-function dataflow remain runtime-validation/review responsibilities.

## Task 1: Checker and regression proof

**Files:** `tests/test_roster_vocabulary.py`, `runtime/observability_hook.py`, `tests/test_observability_hook.py`.

**Interface:** Preserve `violations(root, allowed=ALLOWED)` and its path/line diagnostics. Extend only the overlapping allowance entries with explicit syntax contexts.

- [ ] Add regression cases for accepted kind constructions, assignments, comparisons and membership in a previously unseen module, without an allowance.
- [ ] Add rejected role writes/defaults/comparisons, positional/conditional fallbacks, mixed valid/invalid expressions, overlapping allowances and malformed Python.
- [ ] Add observability regressions for unknown specialist labels, preserved stored roles, deduplication and counted usage.
- [ ] Commit the test-first checkpoint, then run the focused tests and observe the intended failures.
- [ ] Implement bounded AST occurrence classification and narrowly scoped overlapping allowances. Replace the invented telemetry fallback with `unknown`.
- [ ] Validate, commit and run the affected sharded modules: roster vocabulary, observability hook, usage reader/accounting, task profile and delegation.
- [ ] Obtain independent behavior/security review; resolve findings and rerun invalidated checks.
- [ ] Publish through the repository's adopted draft-PR/readiness/merge procedure after applicable checks and review pass.

## Execution record

- Implementation is inline; user explicitly requested execution now, so no additional planning approval is pending.
- Native worktree: `roster-vocabulary-ast`; branch `fix/roster-vocabulary-ast`, based on observed `origin/main` `49be63a7c27bcb5f5d0525a07b63d56050f50200`.
- Coordinator title adopted/read back as `￻▰▱▱▱▱ Vocabulary checker`; previous title was `￻▰▰▰▰▱ WAITING · D9 Observability Impl.`.
- Publication follows the existing project decision; no extra companion publication is required.
- Baseline and final check results will be reported in the PR and conversation. No native-client or live-service qualification is claimed.
