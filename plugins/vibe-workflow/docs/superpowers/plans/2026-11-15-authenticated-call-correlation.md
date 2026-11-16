# Authenticated Call-ID Correlation Implementation Plan

> **For agentic workers:** Use test-first execution in this isolated task worktree.

**Goal:** Reproduce the real distinct-ID failure offline and repair native/Core
correlation without accepting an unauthenticated join.

**Architecture:** Keep native outer-call identity separate from hook receipt
identity. Derive any relation only from already-supported native source records;
reject missing or ambiguous relations before issuing a managed witness. Preserve
both identities and their source evidence through live and retained consumers
where an authenticated relation exists.

**Tech Stack:** Python 3.11+ standard library, SQLite, existing fake-native test
transport. No dependency, native installation or model call.

**Spec:** The user's implementation request and accepted Astra diagnosis from
this task. The design constraints below are their implementation record.

## Design and scope

The real guard denial is not a qualification proof. Its native rollout names an
outer Code Mode call, while its Core receipt names a nested invocation absent
from that rollout. Matching patch fingerprints, selecting the sole fresh
receipt or giving a test both the same ID does not authenticate their relation.

Use direct native IDs and the existing authenticated nested-completion path.
Do not invent a rejected-call event, undocumented host field, public mapping
flag or source of authority. A rejected wrapper without native correlation must
remain blocked with a specific diagnostic, even when a synthetic receipt happens
to reuse the outer ID. If the installed native surface cannot supply rejection
correlation, report that capability unavailable; do not claim a positive repair
of the retained live rejection.

Alternatives rejected: fingerprint/uniqueness fallback weakens identity; another
live turn cannot recover a field missing from the retained native source;
altering native configuration or adding a proxy exceeds this offline slice.
Separating execution admission from historical verification is independent work.

## Global constraints

- Worktree: `authenticated-call-correlation`; branch
  `fix/authenticated-call-correlation`, from current main `488274679474681ce428065ab5f948b807942911`.
- The primary checkout remains on `main`, unchanged.
- Preserve all historical sources, native traces, receipts, grants and spent
  budgets. Do not repin the old `9dea8e51` qualification task.
- No real Codex/app-server, credentials, network service, native installation,
  Langfuse export, live qualification or model retry.
- Test-controlled fake native executables are allowed only in private test homes.
- No fingerprint-only, caller-authored, ambiguous, replayed or wrong-scope join.
- Preserve direct-call behavior, child/post-tool behavior, canary, watermark,
  pin, source-currentness, deadline and qualification-purpose checks.
- Preserve historical proof readers; add no flag claiming unsupported readiness.
- Keep existing file-size ceilings and use existing module ownership.
- Commit focused regression changes before their first test run. Commit the
  implementation before the committed-candidate offline checks.

## Review focus

- Realistic distinct outer/hook IDs must not be collapsed by test fixtures.
- Printed or JSON-authored mappings must never become native provenance.
- Retained consumption must retain and recheck the same authentic relation.
- Cross-turn/scope, replayed, conflicted or pre-watermark receipts remain refused.
- Existing authenticated nested completions and direct calls remain compatible.

### Task 1: Reproduce and repair the native identity boundary

**Files:** `tests/test_parent_observation.py`, existing fake-native fixtures and
the nearest parser tests; add one small fixture/module only if necessary.

- [ ] Use a sanitized faithful rejection shape with distinct outer and hook IDs,
  no native nested event, and a real Core-owned receipt/before-artifact.
- [ ] Replace the misleading wrapped-parent positive fixture with honest
  identity-aware cases. Keep direct-call positive coverage.
- [ ] Add missing-relation, false same-ID wrapper, sole receipt/fingerprint,
  wrong selector, source/turn/scope and ambiguity negatives as applicable.
- [ ] Commit tests, run the focused offline reproduction, and retain its intended
  RED result. Do not treat an unrelated error as reproduction.

#### Repair authenticated correlation at its native owner

**Files:** Existing native parser/managed adapter, managed readback and retained
setup consumer only where the actual data flow requires a change.

**Consumes:** Native-issued source records, original task/allocation/before state
and exact Core receipt.
**Produces:** A source-backed distinction between native call and hook call IDs,
or a precise fail-closed unavailable-correlation result.

- [ ] Implement the narrowest shared native resolution using genuine supported
  source records. Do not infer a missing rejection link.
- [ ] Use authenticated hook identity for Core lookup while retaining the actual
  native selector and source relation; preserve current proof compatibility.
- [ ] Keep the genuine unsupported rejection blocked, with a specific diagnostic.
- [ ] Add positive tests only for a relation the native source contract actually
  supports. Existing completion evidence is not evidence of rejection.
- [ ] Validate package, commit implementation, then run focused and transitive
  offline checks on the committed candidate.

### Task 2: Verify and deliver

- [ ] Self-review exact diff and report any remaining missing native capability.
- [ ] Obtain independent general/security identity-boundary review.
- [ ] Run proportionate offline checks, including full suite if shared proof
  readers or broad fixture behavior changed. Freeze tracked files during tests.
- [ ] Publish the tested branch and draft `[BLOCKED]` PR under existing repository
  rules; do not mark ready or merge an incomplete native capability.
- [ ] Report software proof separately from native qualification. Do not launch
  a new live campaign or imply the old evidence became qualified.

## Progress

Baseline: fresh native-created worktree, clean task branch from current main.
The user explicitly requested implementation and offline checks after accepting
Astra's diagnosis; no repeated design or routine execution approval is needed.
