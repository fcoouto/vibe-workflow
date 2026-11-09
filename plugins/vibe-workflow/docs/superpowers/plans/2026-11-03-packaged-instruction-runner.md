# Packaged Instruction Runner Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. Root implements the cohesive refactor; a fresh independent reviewer checks it before native qualification and benchmarking.

**Goal:** Reduce repeated model-generated loader code while preserving automatic instruction draining and every delivery check.

**Architecture:** The existing fixed helper becomes the complete runner. A short native launcher validates its helper envelope, then calls it with the original selection and native callbacks.

**Tech Stack:** Existing Python reader, native JavaScript and real-reader Node/Python tests; no new dependency.

**Spec:** `docs/superpowers/specs/2026-11-03-packaged-instruction-runner-design.md`

## Global Constraints

One helper command per selection is explicitly approved, including small loads. Preserve all 8,000/9,500/24,000-byte bounds, source authority/freshness and failure behavior. Keep RTK, other plugins and native settings unchanged. No new protocol, cache, runtime controls or automatic source grouping. One paired benchmark, no automatic retries, no merge/live installation.

## Review Focus

The short launcher must reject clipped, empty, wrong-kind/version, nonzero or unfinished helper output before evaluation. Moving instruction checks must not lose first-page/mode, revision, target, Unicode or pending/cancelled behavior. Notifications and the final receipt must remain awaited. Source text and shell-sensitive paths must stay data. Existing helper containment and runtime fingerprints remain in force.

### Task 1: Move the loader into the fixed runner

**Files:** `scripts/drain_instructions.js`, `clients/codex.md`, `tests/instruction_drain.cjs`, `docs/policy-sources.md`, generated `clients/instruction-map.json`. Keep the reader CLI and runtime pin list unchanged.

**Interfaces:** `drainInstructions({root, paths, external, exec, emit})` performs all instruction reads and returns the unchanged complete receipt. The launcher obtains only the fixed `--helper` envelope and supplies `tools.exec_command`/`notify`.

- [x] Update real-bootstrap tests for helper-first loading, one helper for small/large selections and zero instruction reads after invalid helper delivery. Observe the small/helper tests fail against the previous launcher.
- [x] Move command construction and instruction validation into the existing helper; shorten the documented launcher without weakening its helper gate. Preserve the existing continuation loop and failure probes.
- [x] Update source coverage and regenerate the instruction map. Run `python3 -B -m unittest tests.test_prepared_instructions tests.test_instruction_drain tests.test_startup tests.test_package_compatibility`, `python3 -B scripts/validate.py` and `git diff --check`. Expected: focused suite and package checks pass.
- [x] Obtain one fresh independent review against `1fc4dfa`, resolve confirmed findings with regression evidence, and commit the reviewed product.

### Task 2: Native qualification and paired benchmark

**Files:** Private qualification and harness/evidence under `/tmp/vibe-runner-qualification-20261103` and `/tmp/vibe-runner-benchmark-20261103`; outcome recorded in this plan.

**Interfaces:** Frozen baseline `1fc4dfaf58deb6dda9be7cf246d08ec470af88d6`, reviewed candidate, existing same-task prompts/acceptance/model settings. The analyzer recognizes the actual short launcher, preserves notifications and normalizes resumed cumulative counters.

- [x] Qualify the actual launcher: small selection, Graphify, maximum batch, forced child clipping and early yield/wait. Independently match delivered source bytes.
- [x] Freeze fresh controls, stage/trust/preflight both arms under existing authorization and execute implementation, independent review and one resume per arm. Stop on drift/timeout.
- [x] Independently audit source delivery and normalized overhead. Record mixed results and limitations accurately; keep the slice unmerged.


## Recorded outcome

Product commit: `973fe019f00ea01f6821527f8ff2387313bfa4e8`. Baseline: `1fc4dfaf58deb6dda9be7cf246d08ec470af88d6`. The fixed runner replaces repeated generated implementation code without changing the reader protocol. Every selection now loads the helper once, including small selections, as explicitly approved. No merge or live installation was performed.

### Review and package validation

The updated small-load test failed against the former launcher, then passed after the refactor. The final prepared-reader, drain, startup and package-compatibility suite passed 96 tests in 72.082 seconds, including 22 real-reader/native-bootstrap cases. Package validation and diff checks passed. A fresh Astra review found no actionable issues and independently passed 13 additional probes covering helper evaluation gates, first-packet metadata, shell-sensitive paths and awaited/rejected notifications.

The full runtime suite was not rerun. The known unrelated `tests.test_state.StateTests.test_symlink_paths_missing_database_and_read_only_status_are_safe` failure remains unchanged and is not represented as passing.

### Native qualification

Independent auditing matched actual native command sequences, notifications and completion receipts for all four cases. Every selected source byte arrived exactly once. The small selection used one helper plus one reader; the original Graphify selection used one helper plus four readers and delivered all 794 lines. A 24,000-byte notification remained intact across an actual early yield/wait. Forced clipping of the first instruction read recovered through two small pages without emitting clipped text or duplicating source ranges. The helper was never subjected to that artificial clipping limit.

The executed 847-byte body matches the committed canonical launcher excluding its final newline. It replaces the prior 1,872-byte body, a 54.75% static reduction. This is a byte measurement, not a token estimate.

### Fresh paired benchmark

Both arms ran the same implementation, independent review and one resume with Vibe enabled. Codex CLI 0.165.0, `gpt-6.1-sol`, `ultra`, native authentication, RTK and other plugins were unchanged. All 22 external guard snapshots matched the initial controls. Both native trust-only sessions had zero model turns and usage records. Both arms passed six generated unit tests and all eight independent acceptance cases; only the fixture's `tags.py` and `test_tags.py` changed. No model-run retries were used.

| Metric, all three phases | BEFORE | AFTER |
|---|---:|---:|
| Total input tokens | 1,717,028 | 1,587,119 |
| Cached input tokens | 1,616,896 | 1,471,744 |
| Fresh input tokens | 100,132 | 115,375 |
| Output tokens, including reasoning | 18,682 | 11,389 |
| Elapsed seconds | 369.800 | 225.376 |
| Provider responses | 35 | 32 |
| All instruction outer calls, including direct reads and ancestor probes | 16 | 15 |
| All instruction commands, including helpers and direct recovery | 22 | 31 |
| Prepared reader commands | 16 | 17 |
| Helper commands | 2 | 9 |
| Generated JavaScript bytes across runner invocations | 22,735 | 11,694 |
| Output tokens for runner-bearing responses | 7,989 | 4,774 |

Output fell 39.04% and elapsed time fell 39.05%; fresh input rose 15.22%. Actual generated runner-call JavaScript fell 48.56%. Every AFTER runner used the exact shorter executable body; one BEFORE body differed only in line wrapping. Runner-bearing responses include their reasoning and other emitted content, so their usage does not isolate the body alone. Resume totals subtract prior cumulative implementation usage; reasoning is already included in output.

These are observations from one pair. The selected instructions and other model behavior differed; other responses also used fewer output tokens. This establishes actual adoption and reduced generated code, not a causal estimate of total savings or a monetary-cost guarantee.

### Delivery and remaining model behavior

Independent exact-byte auditing found complete delivery for every selected source in both arms, including all Graphify bytes during review. No native clipping or skipped continuation was observed. AFTER had nine runner invocations: eight complete receipts and one rejected mixed-scope selection. Both arms emitted 14 instruction notifications.

The recurring mixed-scope mistake was escalated to Astra. The model combined an external test-writing reference and an absolute path to packaged `clients/workstreams-titles.md` under external mode. The unchanged reader correctly rejected the package path. Direct recovery delivered both missing files completely: 8,268 and 9,529 UTF-8 bytes respectively. There was no false completion or lost source text.

Astra identified this as the existing cooperative selection error, independent of the packaging refactor. The recovery also requested two 6,000-token children under the default 10,000-token outer budget, contrary to the prescribed aggregate headroom; actual output remained complete. Preserve that limitation rather than claiming universal instruction adherence. Automatic scope grouping or a new recovery mechanism is outside this approved simplification and was not added.

Selected-source completeness does not prove every applicable skill was selected or every instruction followed. Neither implementation selected Graphify. AFTER implementation added Ponytail and brainstorming; AFTER review omitted the Ponytail core skill while retaining Ponytail-review. The benchmark therefore supports observed delivery and measured overhead, with partial protocol adoption and applicability limitations.

### Evidence

- Independent product review and probes: `/tmp/vibe-runner-review-20261103/review.md` and `probes.cjs`.
- Native qualification: `/tmp/vibe-runner-qualification-20261103/independent-native-audit.md`, companion JSON and native event extracts.
- Paired controls, snapshots and receipts: `/tmp/vibe-runner-benchmark-20261103`.
- Metrics: `analysis/paired-metrics.json`, `analysis/loader-accounting.json` and `analysis/metric-findings.md` in that directory.
- Exact delivery and escalation: `delivery-audit/paired-source-delivery-summary.md` and the per-arm JSON/Markdown audits in that directory.
- BEFORE implementation/resume: `01a1a306-136a-7101-a2c6-bcf9c0e6aded`; review: `01a1a309-0375-74a2-b564-34671f20a9c5`.
- AFTER implementation/resume: `01a1a30c-84aa-7220-be8b-1f453d9f1d9d`; review: `01a1a30d-ea30-7c82-8720-ed147fbe98b2`.
