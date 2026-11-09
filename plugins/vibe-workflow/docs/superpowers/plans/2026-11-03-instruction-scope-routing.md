# Instruction Scope Routing Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. Root implements this cohesive slice; a fresh independent reviewer checks it before native qualification and benchmarking.

**Goal:** Route explicit mixed instruction selections safely through the existing runner and remove the observed ad hoc recovery detour.

**Architecture:** Python determines per-source authority inside the existing read. One automatic selection revision binds all ordered sources; the existing JavaScript runner drains it with fixed child budgets and separate notifications.

**Tech Stack:** Python standard library, packaged native JavaScript and existing Python/Node tests.

**Spec:** `docs/superpowers/specs/2026-11-03-instruction-scope-routing-design.md`

## Global Constraints

Preserve all source/path/hash/target and delivery checks, 8,000/9,500/24,000-byte bounds, explicit legacy modes and exceptional native recovery. No separate resolver, cache, raw recovery API, dependency or hook. RTK, other plugins, model, reasoning and authentication stay unchanged. One paired benchmark; no automatic retries or merge/live installation.

## Review Focus

Automatic routing must never reclassify a package escape as external or replace a known alias's prepared key. Revisions must reject target/authority/mode changes even with identical bytes. Invalid later sources must fail before stdout. Mixed clipping recovery must retain ordered exact-once source delivery and fixed budgets. Exceptional recovery guidance must retain its existing availability without aggregating output or bypassing hash checks in the reader.

### Task 1: Automatic authority and owning recovery route

**Files:** `scripts/read_instructions.py`, `scripts/drain_instructions.js`, `clients/codex.md`, generated `clients/instruction-map.json`, `docs/policy-sources.md`, `tests/test_prepared_instructions.py`, `tests/instruction_drain.cjs`.

**Interfaces:** `load_page/load_batch(..., auto=False)` retain existing defaults. `--auto` selects per-source validation and a mode-bound revision. Automatic JSON envelopes expose `mode="auto"`; the runner uses `--auto`, removes the global external choice and otherwise retains its read/drain contract.

- [x] Add mixed-source, authority/alias, revision and CLI tests; observe failure against the existing reader/runner.
- [x] Implement per-source classification and mode-bound identity with unchanged legacy behavior. Update the runner and sole owning contract, including one-result recovery budgets; retain all existing delivery tests.
- [x] Regenerate the map. Run `python3 -B -m unittest tests.test_prepared_instructions tests.test_instruction_drain tests.test_startup tests.test_package_compatibility`, package validation and diff checks. Expected: focused suite and package checks pass.
- [x] Obtain fresh independent review of the complete diff against `2179691`; resolve confirmed findings with regression evidence and commit the product.

### Task 2: Native qualification and same-task benchmark

**Files:** Private artifacts under `/tmp/vibe-scope-qualification-20261103` and `/tmp/vibe-scope-benchmark-20261103`; outcome in this plan.

**Interfaces:** Frozen baseline `2179691387316eee3f5154430a371b249aeac861`, reviewed candidate, unchanged fixture/prompts/settings. Analyzer handles per-source automatic routing separately from legacy explicit-mode counts.

- [x] Qualify the actual mixed selection, automatic draining, clipping recovery and early yield; independently audit source bytes and budgets.
- [x] Freeze fresh controls, stage/trust/preflight both arms under existing authorization and complete implementation, independent review and one resume per arm. The complete replacement pair is recorded below; prior interrupted attempts remain separate.
- [x] Independently audit source selection, delivery, recovery budgets and normalized usage. Record limitations and leave the slice unmerged.

## Local review and validation

The focused reader/drain/startup/package-compatibility suite passed 103 tests, including 25 real-reader bootstrap scenarios. Package validation and `git diff --check` passed. Independent Astra review found no actionable issues; isolated probes also passed legacy parity, mixed exact reconstruction and bounds, alias/escape/private-target rejection, revision identity changes, no partial stdout, later clipping recovery without replay, awaited notification failure and metadata tampering. Review evidence: `/tmp/vibe-scope-review-20261103/review.md`. These are package and local execution checks; native delivery and benchmark results are recorded separately below.

## Native qualification

Product commit: `6c585be7b69d1cb07b3000a6234fe44dae8f5727`. Independent audit matched the actual model-visible notifications, helper bodies and per-source revisions against frozen source bytes. All three cases passed:

- The formerly failing mixed selection (external `writing-good-tests.md` plus absolute Vibe `clients/workstreams-titles.md`) delivered four pages in one notification using two commands, including the existing helper call.
- Graphify plus the Vibe contract delivered nine pages in four notifications using five commands. All 48,304 Graphify bytes and 794 lines arrived, with no gaps or repeats.
- Forcing only the first JSON child to 1,000 tokens caused real clipping. The runner withheld that body and automatically delivered four bounded single pages. An actual early yield, wait and terminal completion were observed; the helper and recovery children kept their normal 10,000-token budgets.

The canonical launcher body is 838 bytes. Existing source/page/batch bounds remained 8,000/9,500/24,000 bytes. Evidence: `/tmp/vibe-scope-qualification-20261103/independent-native-audit.md` and its reproducible JSON/script. These checks establish selected-source delivery and exercised recovery behavior, not universal applicability, semantic compliance, arbitrary native-fallback discipline or retention after compaction.

## Benchmark outcome: incomplete pair

One fresh pair used baseline `2179691387316eee3f5154430a371b249aeac861` and candidate `6c585be7b69d1cb07b3000a6234fe44dae8f5727`, with Vibe enabled in both. The fixture, prompts, implementation/review/resume order, Codex CLI 0.165.0, native `gpt-6.1-sol` at `ultra`, authentication, RTK and other plugins remained fixed. All 22 external guard snapshots matched. Native trust was applied only to the five owned private candidate hooks for each arm; both trust sessions recorded zero model turns. Live installation and global enablement were unchanged.

Baseline completed all three phases. Candidate implementation and independent review completed, but its resume failed with `server_overloaded`: “Selected model is at capacity. Please try a different model.” No model change or automatic retry followed. The failed resume had already delivered one instruction selection before rejection; it was not a zero-cost attempt. Its separate native usage was 78,553 input tokens, including 67,456 cached and 11,097 fresh, and 870 output tokens including 516 reasoning tokens, over 33.066 seconds. Empty CLI usage did not override this native evidence.

Only matched, completed implementation and review phases can be compared:

| Metric | Baseline | Candidate |
|---|---:|---:|
| Fresh input tokens | 105,143 | 98,262 |
| Cached input tokens | 1,177,088 | 1,077,632 |
| Output tokens, including reasoning | 10,617 | 10,775 |
| Elapsed seconds | 282.202 | 289.333 |
| Provider responses | 29 | 26 |
| Prepared instruction outer calls | 11 | 7 |
| Prepared reader commands | 17 | 16 |
| Helper commands | 9 | 5 |

Fresh input fell 6.5%; output rose 1.5%; elapsed time rose 2.5%. These observations are from an incomplete pair, with different selected source sets; they are not a whole-task savings or causal claim. Reasoning is included in output, not added twice. All five successful phases reconcile with native provider records; baseline resume counters were normalized against its prior implementation and excluded from the matched comparison. Evidence: `/tmp/vibe-scope-benchmark-20261103/analysis/incomplete-pair-findings.md` and `incomplete-pair-metrics.json`.

Both resulting coding changes passed the same eight independent acceptance cases. Baseline had five generated tests; candidate had six. Candidate post-failure checks were run locally by the coordinator and do not count as a successful model resume. No product change was needed in response to the capacity failure.

The independent delivery audit found all selected bytes complete and ordered exactly once. Four candidate selections mixed Vibe and external sources, including the formerly rejected titles/test-guide pair; all completed. Automatic revisions and all six observed executable launchers/helpers, including the partial resume, matched frozen sources. Across completed implementation/review phases, all instruction outer calls fell from 15 to 12 and instruction commands from 30 to 26. These broader counts include native reads; the table above counts only prepared-reader calls. No reader rejection, clipping or missed continuation occurred in either arm. Baseline had one unrelated native shell failure that recovered fully.

Source scope differed: candidate implementation selected Graphify while omitting Ponytail core and brainstorming read by baseline; the review selections matched. Graphify was complete in baseline review and both completed candidate phases. This is delivery evidence, not proof of every applicability decision or semantic requirement. Evidence: `/tmp/vibe-scope-benchmark-20261103/delivery-audit/paired-source-delivery-summary.md` and per-arm exact audits.

Budget adoption outside the runner remains cooperative. Candidate review used 10,000-token children with 12,000-token outer headroom. Its initial native RTK/AGENTS reads used 10,000/default 10,000, although actual output was complete. Those reads were not recovery after reader failure. The automatic mixed-source and bounded clipping paths passed; the independent audit found no new router/drainer defect requiring another repair cycle.

The slice remained unmerged and was not installed live. At this stage a complete paired benchmark was still outstanding; the replacement result below resolves that measurement gap. No full-runtime-suite repair was attempted; the previously recorded unrelated `tests.test_state.StateTests.test_symlink_paths_missing_database_and_read_only_status_are_safe` failure remains outside this slice.

## Authorized complete rerun: external changes interrupted comparison

The user requested another complete implementation/review/resume pair on 3 November. `/tmp/vibe-scope-rerun-20261103` preserved the same two product commits, task, prompts, native model and settings. Initial controls exactly matched the preceding attempt; both arms passed preflight with Vibe enabled. Baseline completed all three phases and the eight acceptance cases. Its independent delivery audit reproduced the mixed-scope rejection and verified complete recovery with adequate budgets.

Candidate implementation completed successfully, but its post-phase guard detected 42 added files: six under the installed `backtest-expert` skill and 36 under installed `vibe-setup`. No configuration settings or private Vibe package files changed. These additions include skill entry points and runtime sources, not logs. Candidate review and resume did not start. This attempt is invalid for controlled before/after comparison; no performance delta is claimed.

Astra investigated the repeated validation interruption. The new skills were absent from the candidate implementation's startup catalog and were not directly read, but subsequent review/resume would have a changed available-skill environment. The decision is to preserve this attempt, retain the strict guard and run both arms afresh after confirmation that external updates have finished. Do not roll back the additions, exclude them from the guard or continue only the candidate arm.

Evidence and spent usage are retained in `/tmp/vibe-scope-rerun-20261103/analysis/external-drift-stop.json`, the external-diff receipt and the independent delivery audit. The user subsequently confirmed that external updates had finished, authorizing the prepared replacement under `/tmp/vibe-scope-rerun2-20261103`.

## Replacement benchmark controls

The replacement froze the updated environment, including 865 external Markdown files, and retained the same product commits, prompts, fixture, CLI 0.165.0 and explicit `gpt-6.1-sol`/`ultra` flags. During baseline implementation, the global default model changed from Sol to Astra. The raw guard stopped after the model phase completed successfully. Independent native records proved every actual response used the explicitly pinned Sol/ultra settings through OpenAI; 1,783 file hashes and 201 other setting leaves were unchanged.

Astra approved retaining that completed implementation. A reviewed private checkpoint verified the fixture, allowed changes, configuration, recorded result and native evidence hashes, package fingerprint, hook trust and effective model configuration. It appended the original result once and continued with the unchanged independent review and single resume, without resetting or rerunning implementation. The private guard now compares effective controls: only the global model default shadowed by the explicit model flag is excluded. Raw snapshots and the original guard failure remain recorded, and six other-control mutations still reject. The same comparison protects the candidate prerequisite. No user default, product code or other plugin was changed by this correction.

Evidence: `/tmp/vibe-scope-rerun2-20261103/analysis/astra-model-guard-decision.md`, `analysis/model-default-checkpoint/`, and `before/model-default-continuation/admission.json`. Reported phase runtime sums the model-process durations; it excludes harness preparation, hook trust and the coordinator's checkpoint-review pause. Prior interrupted attempts remain separate.

## Complete replacement result

Both arms completed implementation, independent review and one resume. Both independent reviews reported no code or security findings, and both results passed the same eight acceptance cases. Baseline generated seven passing tests; candidate generated six. No implementation phase was repeated or counted twice.

| Metric across all three measured phases | Baseline | Candidate | Change |
|---|---:|---:|---:|
| Fresh input tokens | 146,466 | 119,343 | −18.5% |
| Output tokens, including reasoning | 11,723 | 12,410 | +5.9% |
| Reasoning tokens, already included above | 4,240 | 6,454 | +52.2% |
| Summed phase runtime, seconds | 324.264 | 315.875 | −2.6% |
| Provider responses | 31 | 28 | −9.7% |
| Prepared instruction outer calls | 11 | 8 | −27.3% |
| Prepared reader commands | 17 | 14 | −17.6% |
| Helper commands | 9 | 6 | −33.3% |

Fresh input is total input minus cached input. Total input was 1,538,594 versus 1,348,399, including 1,392,128 versus 1,229,056 cached tokens. Visible output fell from 7,483 to 5,956 tokens; increased reasoning accounts for the higher combined output. Candidate review produced more reasoning than baseline review, while candidate implementation was shorter. These are observed phase differences, not a causal explanation of model decisions or proof of repeatable savings.

All six phase usages reconcile with unique native provider response records after subtracting prior implementation usage from cumulative resume counters. All 23 effective guard snapshots passed, including checkpoint admission; 18 raw snapshots record the shadowed Sol-to-Astra default change. Every actual phase remained Sol/ultra through OpenAI, all guarded files and other settings stayed fixed, and both trust-only sessions had zero model turns. The 486.836-second interval between baseline implementation completion and review startup, including checkpoint handling, is outside summed phase runtime.

Per-phase figures and audit receipts: `/tmp/vibe-scope-rerun2-20261103/analysis/metric-findings.md` and `paired-metrics.json`. Earlier interrupted attempts are not included in this complete pair. Product commits, RTK, other plugin files and the task prompts were not changed to obtain this result.

The final independent source audit verified every explicitly selected source exactly once per phase in both arms. Candidate completed three mixed package/external selections with canonical launchers/helpers and independently reconstructed revisions. Baseline had one mixed-scope rejection requiring complete native recovery; candidate had none. All instruction outer calls, including native reads and probes, fell from 15 to 12, and instruction-related commands fell from 32 to 25. Observed aggregate-budget gaps fell from three to zero. Neither arm lost text or skipped a successful continuation. Both reviews received all 48,304 Graphify bytes.

The source workloads were similar in size but not identical: 224,151 versus 224,026 delivered source bytes. Candidate implementation omitted the Caveman source read by baseline, candidate review added the base Ponytail guide, and candidate resume reread RTK. No within-phase full-source reread or global skill-catalog search occurred. Delivery and observed budgets are established; full semantic applicability and universal route adoption are not. Candidate exceptional native fallback was not needed, and clipping/yield recovery remains covered by the separate qualification above.

Final delivery evidence: `/tmp/vibe-scope-rerun2-20261103/delivery-audit/paired-source-delivery-comparison.md` and its JSON proof. The requested complete comparison is finished. The result supports the mixed-scope reliability fix and shows fewer instruction calls, but does not establish repeatable whole-task savings. No new product defect was identified. This turn changed benchmark records and the private measurement harness only; no merge or live installation was performed.
