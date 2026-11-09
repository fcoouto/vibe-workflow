# Automatic Instruction Draining Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. Root implements the cohesive change; a fresh independent reviewer checks it before native qualification and benchmarking.

**Goal:** Remove model decisions between instruction batches while preserving complete delivery, freshness and scope.

**Architecture:** Add machine envelopes to the existing reader and a fixed Vibe-owned JavaScript continuation helper. The initial Codex native call owns the single-batch fast path and conditional helper loading; the awaited helper delivers each batch separately.

**Tech Stack:** Python standard library, native JavaScript, existing Python unittest and Node for deterministic host-callback tests only.

**Spec:** `docs/superpowers/specs/2026-11-03-automatic-instruction-draining-design.md`

## Global Constraints

Preserve all source requirements and existing 8,000/9,500/24,000-byte bounds. No external plugin/model/settings edits, new hook, server, dependency, persistent cache or task controls. Keep per-batch freshness checks. One paired benchmark; no automatic reruns or unrelated state-guard repair.

## Review Focus

Incomplete JSON/body counts must never be emitted as complete. Source text and shell-sensitive paths must not become executable control data. Failed/cancelled/unfinished calls must not report success. Fallback must cover exactly the missing ranges while preserving revision/target identity. A one-batch selection must not load the helper.

### Task 1: Reader, helper and owning route

**Files:** `scripts/read_instructions.py`, new `scripts/drain_instructions.js`, `runtime/state.py` (existing runtime fingerprint list only), `clients/codex.md`, `clients/instruction-map.json`, `docs/policy-sources.md`, `scripts/validate.py`, `tests/test_prepared_instructions.py`, new `tests/test_instruction_drain.py` and `tests/instruction_drain.cjs`.

**Interfaces:** Add `--json` and exclusive `--helper`; preserve existing page APIs and plain CLI. Envelope fields and injected helper callbacks follow the spec. The Node test executes the actual Codex bootstrap with real Python reader calls and controlled native transport failures.

- [x] Add meaningful CLI and native-bootstrap tests; observe unsupported JSON/helper behavior fail before code changes.
- [x] Implement envelopes, bounded helper, fast path and canonical loading contract. Verify exact text reconstruction and the specified failures, without model calls.
- [x] Update authoring coverage and package validation; regenerate the instruction map. Run focused reader/drain/startup checks and package validation.
- [x] Obtain fresh independent review of the whole slice against `385ff3e`; resolve confirmed findings and commit the reviewed product.

### Task 2: Native qualification and paired benchmark

**Files:** Owned private harness/evidence under `/tmp/vibe-drain-benchmark-20261103`; recorded outcome in this plan.

**Interfaces:** Existing same-task harness, baseline `385ff3e`, frozen reviewed candidate, unchanged prompts/model/authentication/settings. Preserve each separate notification in model-visible evidence and normalize cumulative resume counters.

- [x] Qualify actual notification delivery for the failed four-file selection, near-limit batches and an early native yield; count completeness separately from test assertions.
- [x] Freeze controls, stage/trust/preflight both arms using prior authorization, and run implementation, independent review and one resume per arm. Stop on external drift or timeout.
- [x] Independently audit exact selected-source delivery, native route adoption, acceptance and normalized overhead. Record limitations and keep incomplete work unmerged.


## Recorded outcome

Product commit: `a5a7a4d17c4fa5f5089c286f5baecb17985432ee`. Baseline: `385ff3eafd8a8b46ccc5d6da64891bf8502c04e3`. This slice remains local and unmerged; no live installation was selected.

### Package and independent review

A fresh native Astra review found two narrow issues: the executable helper needed inclusion in the existing runtime fingerprint, and the envelope decoder needed to reject a zero-length body. Both findings were reproduced with failing tests and fixed. The final focused prepared-reader, drain, startup and package-compatibility suite passed 96 tests, including 14 real-reader/native-bootstrap cases. Package validation and diff checks passed. No new runtime pin mechanism or active-task repin was added.

The unrelated existing `tests.test_state.StateTests.test_symlink_paths_missing_database_and_read_only_status_are_safe` failure was not changed. The full runtime suite was not rerun; focused package validation is separate from observed client execution.

### Native qualification

The exact documented loader delivered the original four-source, nine-page selection in four notifications: 16,848, 16,313, 16,361 and 12,750 UTF-8 bytes. Every source byte appeared exactly once, including all 794 Graphify lines. A separate boundary probe delivered a 24,000-byte notification intact and completed after an early outer-cell yield and wait. A forced child-output clip recovered through two small-page reads without emitting the damaged body or duplicating source ranges. An independent audit matched all recorded notifications to the native rollout.

For an intact selection of N batches, the reader still executes N times. More than one batch adds one helper command inside the same awaited call and removes the N−1 model continuation decisions. A fitting selection loads no helper. Cancellation, malformed small pages and changed source revisions fail explicitly.

### Paired benchmark

One fresh BEFORE/AFTER pair used the same coding task, independent review and one resume. Both arms kept Vibe enabled, `gpt-6.1-sol`, `ultra`, native authentication, RTK and other plugins unchanged. Every phase and final external-state guard passed. Both arms passed all eight independent acceptance cases; only `tags.py` and `test_tags.py` changed in the disposable fixture. There were no model-run retries.

| Metric, all three phases | BEFORE | AFTER | Change |
|---|---:|---:|---:|
| Provider responses | 36 | 37 | +1 |
| Total input tokens | 1,646,064 | 1,795,294 | +9.07% |
| Cached input tokens | 1,496,448 | 1,684,992 | +12.60% |
| Fresh input tokens | 149,616 | 110,302 | −26.28% |
| Output tokens, including reasoning | 11,234 | 15,203 | +35.33% |
| Elapsed seconds | 268.834 | 365.593 | +35.99% |
| All instruction outer calls, including direct reads and bootstrap | 19 | 17 | −2 |
| All instruction commands, including helpers and direct reads | 19 | 27 | +8 |
| Reader commands | 19 | 16 | −3 |
| Helper commands | 0 | 4 | +4 |
| Reader and helper commands combined | 19 | 20 | +1 |

An independent source-byte audit passed both arms. Every explicitly selected instruction source arrived completely, including all 794 Graphify lines / 48,304 UTF-8 bytes. No skipped continuation or native clipping occurred. In AFTER review, Graphify's four batches arrived inside one awaited outer call with a complete nine-page receipt.

The candidate used the exact canonical loader eight times: seven completed; one mixed an external reference and a packaged Vibe contract under `--external`. That request failed explicitly, and two direct reads recovered both sources completely. Two additional calls loaded the bootstrap. Seven other native instruction calls brought the comparable AFTER total to 17, not the prepared-reader subset of ten. Helpers and recovery increased the instruction command count. Small direct reads and the rejected mixed-scope attempt mean observed protocol adoption was partial.

Instruction calls by phase were implementation 9→10, review 9→5, and resume 1→2. Candidate implementation selected one additional Caveman source; baseline review selected an additional Vibe policy and two host skills; candidate resume selected one additional review skill. Selected-source completeness does not establish that every applicable instruction was selected or semantically followed.

Resume usage subtracts the implementation thread's cumulative counters. Reasoning tokens are already included in output and are not added twice. These are usage measurements, not a monetary-cost estimate.

The paired runs selected different instruction sets and generated different tests. This single pair demonstrates observed behavior, not a controlled causal estimate of total savings. Fresh input decreased, but output, overall model responses and elapsed time increased. There is no demonstrated net efficiency or zero-overhead win.

### Evidence

- Product review: `/tmp/vibe-drain-review-20261103/astra-independent-review.md`.
- Native qualification and independent audit: `/tmp/vibe-drain-qualification-20261103/qualification-proof.json` and `independent-native-audit.md` in that directory.
- Frozen benchmark, phase receipts and guards: `/tmp/vibe-drain-benchmark-20261103`.
- Independent source-delivery audit: `delivery-audit/paired-source-delivery-summary.md` and the per-arm JSON/Markdown audits in the benchmark directory.
- Reconciled metrics: `analysis/paired-metrics.json` and `analysis/metric-findings.md` in the benchmark directory.
- BEFORE implementation/resume: `01a1a2d2-3f98-7a22-8f55-25320e379e25`; review: `01a1a2d4-35b7-7861-bbe4-ef0b37cf97d3`.
- AFTER implementation/resume: `01a1a2d9-7e59-7653-b017-a1c76f932736`; review: `01a1a2db-e17e-7e22-b96b-de0d08016746`.

The private analyzer initially retained snapshot paths from the preceding experiment. Those paths were corrected and offline analysis was repeated against this pair's frozen inputs. Model runs and their receipts were preserved.
