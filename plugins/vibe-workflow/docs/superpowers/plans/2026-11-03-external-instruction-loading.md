# External Instruction Loading Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. Root implements the cohesive reader/route change; a fresh independent reviewer checks it.

**Goal:** Deliver all applicable external instructions through the existing bounded reader without adding routine coordination or changing external sources.

**Architecture:** Add explicit external source selection before existing validated page packing; reuse rendering, batches and revision-bound continuation. Preserve the existing advisory and native fallback.

**Tech Stack:** Existing Python standard-library reader and unittest.

**Spec:** `docs/superpowers/specs/2026-11-03-external-instruction-loading-design.md`

## Global Constraints

Preserve every source requirement, authority, applicability, freshness and package-path check. Keep existing 8,000 / 9,500 / 24,000 byte bounds. No external edits, new hooks, state, dependencies or model/settings changes. One paired benchmark; no automatic repeat or unrelated state-guard repair.

**Benchmark ruling:** The first baseline completed, but its external-state guard detected concurrent live Vibe and Sites updates. Its receipts remain preserved under `/tmp/vibe-external-benchmark-20261103`; they cannot support a controlled comparison. Astra reviewed the drift and approved one separately identified replacement pair under freshly frozen current controls, without reverting those updates or changing model settings. The replacement is `/tmp/vibe-external-controlled-benchmark-20261103`. It checks controls before staging and before/after every phase. Further drift stops the benchmark; no further retry is selected.

## Review Focus

External mode must not bypass packaged-source validation, follow references, accept non-Markdown symlink targets, mix stale revisions or emit partial text before discovering an invalid later source. Metadata counts toward bounds. Normal fitting reads stay one invocation.

### Task 1: Reader and loading route

**Files:** `scripts/read_instructions.py`, `tests/test_prepared_instructions.py`, `clients/codex.md`, `clients/instruction-map.json`, `docs/policy-sources.md`.

**Interfaces:** Add keyword-only `external=False` to `load_page` and `load_batch`, and CLI `--external`. Preserve existing defaults/schema. External results carry `external=True`; render an external header and repeat `--external` in continuation. Share source-range preparation with existing authoring.

- [x] Write and observe failing external CLI and exact reconstruction tests; include freshness, target changes, source-read count, invalid paths and no partial stdout.
- [x] Implement explicit external selection and reuse bounded packing/rendering. Expected: prepared-reader tests pass with all existing package checks retained.
- [x] Update the owning Codex route and source coverage; regenerate the package map. Expected: focused startup/read tests and `python3 -B scripts/validate.py` pass.
- [x] Verify the actual Graphify source reconstructs exactly without changing it. Obtain fresh independent review, resolve confirmed findings, and commit the reviewed slice.

### Task 2: Native paired benchmark

**Files:** Private copies of the existing harness under `/tmp`; no product benchmark framework.

**Interfaces:** Frozen baseline `541c928`, reviewed candidate, same private candidate identity/path and native account/settings. External reader evidence uses exact external source snapshots; no unbounded parser campaign.

- [x] Stage/trust/preflight each frozen arm using existing authorization; run the same implementation, independent review and one resume with Vibe enabled in both.
- [x] Audit actual model-visible Vibe and selected external instruction completeness, task acceptance, source/settings preservation, normalized usage and round trips. The audit found incomplete Graphify delivery; user acceptance remains unmet.
- [x] Record observed outcomes and limitations separately from package validation; do not infer savings from omitted required text.

## Outcome: native acceptance not met

The reviewed implementation is commit `e7db3d1c9ee03cefb90f1aff27b1be2cdca3c3e2`, compared with `541c928968bb94c518b6d9d5ee5e5a16f76519a2`. It remains local and unmerged. No live installation is selected.

### Package and source validation

The external CLI regression failed before implementation, then all 22 reader tests and three focused startup tests passed. `python3 -B scripts/validate.py` and `git diff --check` passed. A fresh Astra reviewer independently reran the reader tests and found no actionable source-code defect. The actual 48,304-byte Graphify source reconstructed exactly through three bounded batches of 16,235, 16,346 and 17,014 rendered bytes; its hash remained unchanged. Evidence is under `/tmp/vibe-external-proof-20261103`.

These checks establish reader correctness, not reliable model continuation. The full repository suite was not repeated. The previously recorded unrelated state-guard failure remains outside this slice.

### Controlled native execution

The replacement pair completed implementation, independent review and one resume per arm under Codex 0.165.0, `gpt-6.1-sol`, `ultra`, the same native account, prompts, fixture and invocation flags. Vibe was enabled in both arms through the same private candidate path. The 1,772-file and 202-setting-leaf control guard passed before/after every phase and at both arm completions. RTK, other plugin contents/configuration, model and reasoning settings stayed unchanged within this pair. Each arm passed six fixture tests and eight independent acceptance cases. Both native code reviews reported no code or security findings.

| Observed metric | Before | After |
| --- | ---: | ---: |
| Provider responses | 29 | 33 |
| Fresh input tokens | 87,655 | 89,232 |
| Cached input tokens | 1,198,208 | 1,365,504 |
| Total input tokens | 1,285,863 | 1,454,736 |
| Output tokens, including reasoning | 8,757 | 9,751 |
| Elapsed seconds | 294.193 | 278.535 |
| Vibe reader commands / original pages | 8 / 19 | 8 / 19 |
| External reader commands / original pages | 0 / 0 | 8 / 16 |

Resume usage was normalized by subtracting the implementation's cumulative usage, then reconciled against unique provider response records. Counts are in `/tmp/vibe-external-controlled-benchmark-20261103/analysis/paired-metrics.json`.

### Exact instruction-delivery audit

All selected Vibe sources arrived in both arms. The before review had one child-output clip and recovered only part of Graphify, leaving 1,243 source characters unread. No outer clipping was observed.

The after implementation delivered all ten selected external sources, and its resume delivered its selected external source. The after review delivered five of six external sources. It selected four files in call `call_9a80d4765b7d49fb8ca12e3084e1192e`, received external pages 1–3 of 9 and the explicit page-4 continuation footer intact, then switched selections without continuing. Only Graphify lines 1–110 arrived; lines 111–794, or 40,370 characters, remained unread. No native child or outer clipping occurred in the after arm. This is a missed continuation, not a rendering or output-truncation defect.

The exact audit compares model-visible text with frozen source bytes, not stored raw command aggregates. Evidence is under `/tmp/vibe-external-controlled-benchmark-20261103/delivery-audit`, including `paired-source-delivery-summary.json`. The selected external source sets differ between arms. Neither complete requirement delivery nor equivalent-compliance savings is established. More provider responses and fresh/output tokens also prevent claiming that the requested fix added no measured overhead.

### Escalation and next boundary

The continuation failure was immediately escalated to Astra. Independent examination confirmed that the complete first batch and footer reached the model, which skipped the next call. A Python reader cannot force future model calls or establish their actual delivery. Another advisory cannot structurally close this gap.

Keep this candidate unmerged and stop the repair/rerun loop. The next structural design to assess is automatic native-tool batch draining with a proven total-delivery budget. That changes the current delivery contract and must establish completeness and overhead before implementation or another benchmark is selected. No new hook, cache, gate, plugin edit, model change or benchmark rerun was added to hide this failed acceptance result.
