# Bounded Native Trace Reader Implementation Plan

> **For agentic workers:** Use `subagent-driven-development` for the coupled implementation outcome and independent review.

**Goal:** Correlate a rejected native Code Mode patch to its exact Core hook receipt using bounded native trace evidence.

**Architecture:** Add one focused native trace reader beside the Codex adapter.
Integrate its source-backed identity relation into the existing managed observer
and retained consumer, preserving direct calls and completion proofs.

**Tech Stack:** Python 3.11+ standard library, existing native/Core fixtures.

**Spec:** `docs/superpowers/specs/2026-11-15-bounded-native-trace-reader-design.md`

## Global Constraints

- Offline source work only: no real Codex/model call, trace recording, credential
  access, installation, native configuration/trust edit, or Langfuse operation.
- Historical campaigns, task pins, receipts, authority and spent limits remain unchanged.
- No caller/model-authored ID mapping, fingerprint-only join, or sole-receipt inference.
- No new public CLI, preference, database schema, dependency, proxy or trace viewer.
- Every new module stays below 1000 lines; existing fixed file ceilings remain unchanged.
- Commit test changes before RED runs and implementation before candidate checks.
- Worktree `bounded-native-trace-reader`, branch `feat/bounded-native-trace-reader`,
  from current primary `64ab1b2ff45f4382dcc6d3cd8216f7e2dea787cc`.
- One implementation owner; coordinator owns design/plan, private progress, review and delivery.
- Repository publishing/merge policy applies to the offline source slice, not qualification authority.

## Review Focus

- Untrusted trace locations, symlink/ancestor escapes and payload traversal must not become native provenance.
- Duplicate/conflicting JSON keys, IDs or relation edges must not select a plausible single receipt.
- Wrong/replayed thread, turn, cell or invocation must not borrow a legitimate patch or denial.
- Missing completion, altered payloads, partial recording and read-time replacement must fail closed.
- Retained revalidation must preserve original proof location, bounds, both identities and exact Core lookup.

## File map

- New `runtime/adapters/codex_trace.py`: bounded raw native bundle reading,
  validation, identity relation and source proofs; no execution.
- `runtime/adapters/codex_managed.py`: consume that relation only for the supported
  parent pre-tool rejection; retain existing rollout checks.
- `runtime/adapters/codex.py`: preserve compatibility wrapper; thread only the
  necessary private optional native trace source input.
- `runtime/managed_observation.py`: revalidate retained trace evidence and source stability.
- `runtime/setup_observation.py`: accept only the exact supported additional
  source proofs if four-proof legacy shape needs a compatible extension.
- `tests/test_codex_trace.py`: fast bounded parser/path/lifecycle regressions.
- `tests/test_parent_observation.py`: Core-owned denial and retained integration.
- Existing fake-native fixtures only if necessary; do not alter unrelated policy/settings.

### Task 1: Implement one complete trace-backed denial outcome

**Consumes:** Original native rollout bytes, selected thread/turn/outer call,
approved patch, privately selected native trace location and original deadline.
**Produces:** Derived hook ID, preserved native outer ID and recheckable source
references, or a precise fail-closed observation error.

The implementation owner chooses the narrow private reader signature after
checking native manifest and payload contracts. Keep location selection private;
do not add permissive task-ref fields. Use existing path/read/hash/budget helpers.
Preserve first two rollout proofs; additional trace proofs, if required, precede
the original before/readback proofs. Retained consumption must rederive the
original outer selector rather than confuse it with the hook ID.

- [ ] Read native reference files and all adapter/retained callers; fix exact reader
  signature and finite file/event/payload/aggregate/deadline bounds in the task report.
- [ ] Add `test_trace_rejection_derives_distinct_hook_identity` with faithful raw
  manifest/events/payloads, exact submitted patch and a native failed tool result.
  Assert outer and hook IDs remain distinct and source proofs retain all relied-on files.
- [ ] Add negatives for wrong identity/scope, absent/duplicate/conflicting relation,
  forged printed mappings, altered hash, failed payload lookup and partial lifecycle.
- [ ] Add `test_trace_reader_rejects_unsafe_or_over_budget_sources`: symlinks,
  traversal, oversized records/files/payloads, event/directory caps and timeout.
- [ ] Commit tests and run `python3 -B scripts/test_sharded.py test_codex_trace`;
  record an intended RED, not an unrelated setup failure.
- [ ] Implement the bounded reader and minimal native-owner integration.
- [ ] Add a positive Core-owned parent denial plus retained-consumption test.
  Verify canary unchanged, zero assignments, exact fresh receipt, immutable task
  purpose/pins and both source identities.
- [ ] Add retained tamper/location-change negatives and preserve existing no-trace
  wrapper failure, direct-call positives and supported completion behavior.
- [ ] Validate, commit exact implementation/tests, then run focused parser,
  parent/owner, setup and retained-source modules on the committed candidate.
- [ ] Self-review trust boundaries, error paths and transitive consumers; report
  actual offline results separately from unobserved native emission.

### Task 2: Verify and deliver the offline source slice

- [ ] Freeze the exact committed candidate and inspect full diff and dirty layers.
- [ ] Obtain one independent combined general/security review of this coupled
  outcome, including native-file custody and retained proof compatibility.
- [ ] Resolve blockers through the same implementation owner; do not invent
  positive live evidence or expand scope to enable a run.
- [ ] Run package validation and proportionate full offline suite on frozen HEAD.
- [ ] Publish draft `[BLOCKED]` PR using repository template, then follow existing
  readiness/merge permission only for the verified offline source slice.
- [ ] Preserve private test/review evidence; report cleanup eligibility honestly.
- [ ] Report live trace emission/qualification as not run. A later live run needs
  its own reviewed exact source, native recorder effects and fresh finite limits.

## Plan self-review

The parser and both live-owner/retained integrations form one coupled outcome;
separating them would leave an unused reader or accept an unverified receipt.
Review-focus cases belong to Task 1 tests, not speculative hardening.
The user's explicit implementation approval covers this accepted design and
offline execution; no repeated routine approval question is needed.
