# Bounded Instruction Batches Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. Root implements this cohesive slice; a fresh independent reviewer checks the complete change.

**Goal:** Remove mechanical continuation round trips while preserving complete applicable Vibe instructions.

**Architecture:** Reuse validated prepared pages and add one bounded batch result to the existing reader. Preserve the single-page fallback and qualify actual output delivery before routing Codex to batching.

**Tech Stack:** Python standard library, existing reader/startup integration and unittest.

**Spec:** `docs/superpowers/specs/2026-11-03-bounded-instruction-batches-design.md`

## Global Constraints

- Preserve exact text, scope, permissions, freshness, path safety and review requirements.
- Keep single pages at 8,000 source bytes / 9,500 rendered bytes; provisionally cap the complete rendered batch at 24,000 bytes.
- Preserve RTK, other plugins, model/effort/authentication and the live installation.
- One paired native benchmark; no automatic repeated trials or unrelated state-guard repair.

## Review Focus

- Whole rendered output, including metadata, fits the batch bound.
- Page continuation does not skip, repeat or mix revisions; fallback resumes at the same page.
- One validated source observation supplies all included pages; errors emit no partial instructions.
- Bootstrap/applicability and retained-context resume remain unchanged.
- Native output proof checks model-visible text, not merely saved command stdout.

### Task 1: Reader, boundary proof and loading route

**Files:** `scripts/read_instructions.py`, `tests/test_prepared_instructions.py`, `runtime/startup.py`, `tests/test_startup.py`, `clients/codex.md`, `clients/instruction-map.json`, `docs/policy-sources.md`.

**Interfaces:** Add `load_batch(root, paths, page=1, revision=None)`; preserve `load_page` and `render_page`. Batch results include `last_page`; CLI adds `--batch` and retains `--page`/`--revision`.

- [x] Write regression checks for exact bounded multi-page reconstruction, source reads once per selection, stale/changed continuation and CLI failure without partial stdout; observe failure before implementation.
- [x] Implement the smallest shared selection loader and bounded batch renderer; run prepared-reader tests (expected: all pass).
- [x] Verify a near-limit complete payload through actual native child/outer responses and archive exact-byte evidence (expected: no clipping).
- [x] Update the Codex route and owning contract, regenerate the map, and run startup/reader/compatibility checks plus `python3 -B scripts/validate.py` (expected: pass).
- [x] Independently review the exact candidate, resolve confirmed findings, and commit the reviewed slice.

### Task 2: Same paired benchmark

**Files:** Private copies of the existing benchmark setup, executor and analyzer under `/tmp`; no product benchmark framework.

**Interfaces:** Frozen `before=c010e7f` and reviewed `after`; same private candidate identity/path, fixture, prompts, separate review and exactly one resume. Analyzer recognizes batch page ranges and normalizes cumulative resume usage.

- [x] Stage each reviewed source with native hook trust, verify configuration/source fingerprints, and run one before/after pair with Vibe enabled in both.
- [x] Independently verify complete applicable instructions, quality checks, batch uptake, clipping/recovery and normalized metrics.
- [x] Report observed results, known baseline state-test limitation and remaining uncertainty; keep package validation separate from native execution.

## Completion evidence

Implemented and independently approved at `53e6825668029711ba649f27ab9d4942ac9b28c0`. Package validation and 121 relevant tests passed. A 23,944-byte native delivery probe arrived exactly. No new dependency, hook, cache or per-read sizing command was added.

One native before/after pair retained Vibe, Sol/ultra, RTK and other plugins. Both arms completed implementation, independent review and one resume, passing all eight independent acceptance cases. Complete applicable Vibe file sets matched, with no Vibe clipping. Reader responses fell from 17 to eight; total model responses from 45 to 27. Fresh input was 94,754 versus 84,180 tokens; output 11,783 versus 11,993; task time 368.523 versus 353.993 seconds. These task totals are observations, not a causal savings estimate.

External Graphify child clipping occurred in both arms. Direct trace review verified baseline recovery but found unrecovered Graphify text in the candidate review, so full-workflow instruction completeness did not pass. Candidate bootstrap also omitted the explicit outer budget, although its entire response arrived. Full host-skill or perfect protocol compliance is therefore not claimed. A concurrent update of disabled live Vibe was independently explained by an exact configuration backup; original guard and failed observation were preserved, active controls/catalogs matched, and continuation used a separate exact checkpoint. No benchmark rerun or broad guard exclusion occurred.

Detailed report and immutable per-arm evidence: `/tmp/vibe-batch-benchmark-20261103/report.md`. Validation evidence: `/tmp/vibe-batch-proof-20261103/`. The known unrelated state-guard test failure remains unchanged; the full repository suite was not repeated. Product changes remain unmerged and were not activated in the live installation by this task.
