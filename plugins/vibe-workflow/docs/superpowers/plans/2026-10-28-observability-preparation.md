# Task-status observability preparation implementation plan

> **For agentic workers:** Use superpowers:executing-plans for this single, coupled task. The user authorized owner agreement followed by implementation in this chat.

**Goal:** Project an already-produced task-status report into bounded local preparation data without collection or authority.

**Architecture:** A pure, default-off function imports no core runtime. Genesis retains future caller integration at the existing task/status report boundary. The existing source report is the input; no new observation envelope or export API is introduced.

**Tech Stack:** Python standard library and the repository's unittest suite.

**Spec:** [Agreed preparation contract](../../d9-observability-preparation.md).

## Global constraints

- Entry: `prepare_task_status(report, *, enabled=False)`; disabled returns `None` without touching report.
- Preserve owner text/pin/identity semantics and fixed unknown/unverified outcomes from the spec.
- No filesystem, persistence, network, native history, core state imports/calls, readiness mutation, aggregation, exports or provisioning.
- Only helper/tests and preparation documents belong to this slice. Shared primary plans and core callers remain Genesis-owned.

## Review focus

- An apparently ready or historical source report must not become a current authority claim.
- A malformed client/state/pin/identity or non-boolean enable value must fail without exposing content.
- Excluded nested content must never be serialized, traversed or copied.
- A valid report must work; rejecting every report cannot pass acceptance.
- Source imports, aliasing or hidden I/O must not undermine purity.

## Task 1: Prepare existing status reports

**Files:** Create `runtime/observability.py` and `tests/test_observability.py`; update `docs/d9-observability-preparation.md`.

**Consumes:** An existing `runtime.state.task_status(...)` Python result, obtained outside the helper by the ordinary owner operation.

**Produces:** `prepare_task_status(report, *, enabled=False) -> dict | None`, with the spec's projection, null unsupported measurements and fixed uncertainty gaps; malformed enabled input raises a bounded content-free `ValueError`.

- [x] Agree caller and input/output/ownership contract with Genesis.
- [x] Write valid real-response, disabled sentinel, legacy/empty/client, historical, prohibited-content, malformed/bounds, nonmutation and audited no-side-effect tests.
- [x] Observe the canonical positive test fail because the helper does not exist.
- [x] Implement only the contracted pure projection and bounded validation.
- [x] Run `python3 -B -m unittest tests.test_observability -v`; all 13 tests pass.
- [x] Independently review the frozen helper/tests/spec with Genesis; source/security review found no blocking findings.
- [ ] Complete full regression qualification. Package validation and diff checks passed; the single full discovery attempt (`-v` for retained progress) was interrupted near the agreed 30-minute cap: 455 passed, no reported failures/errors/skips, one interrupted, 495 not started. Genesis owns this open broad lifecycle check; no full-suite pass is claimed.
- [ ] Hand off exact paths, hashes and review/check evidence to Genesis. Coordinate Git integration separately; do not modify its core caller or primary plans implicitly.

Execution note: the full baseline run was interrupted before runtime edits; two relevant existing state baseline tests passed. The final frozen source received one bounded full-discovery attempt, with its actual incomplete result preserved in the preparation contract and handoff artifacts. No source changes or reruns were made to conceal an unfinished check.
