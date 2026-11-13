# Selective checking: ordinary-task sample

## Frozen protocol

Requested on 13 November 2026: evaluate ordinary completed tasks for actual mistakes caught, extra turns and total tokens, then assess selective checking. This is a retrospective observation, not another live trial or hook rollout.

- Select the five most recently merged ordinary PRs at collection time, sorted by merge timestamp. Exclude release publication and explicitly named qualification campaigns, not ordinary configuration or test changes. Frozen sample: #290, #288, #285, #286 and #284. #287 is excluded as an explicit qualification campaign; #281 is excluded as release publication. No replacement based on observed findings.
- Freeze merge commits, changed paths, task-start native user messages and merge timestamps before measuring checker results. Use native assistant and compaction counters in each task's start-to-merge window; include observed delegated work separately and flag missing or mixed-scope usage. Cache, input, output and reasoning stay separate. Do not equate absent data with zero or report entire long-running coordinator conversations as task totals.
- Inspect existing native tool receipts for actual optional template-check invocations, confirmed mistakes and subsequent repair responses. Count extra model responses attributable to checker findings only where the sequence supports it; general review/test fixes are not template-check gains. Replayed findings never become historical catches.
- Run the installed 0.1.39 checker once on each task's changed Markdown at its frozen merge snapshot. Compare findings with the parent snapshot to identify pre-existing findings and inspect every new finding. Omit preservation baselines for intentional edits: preservation requirements require task-specific scope, not automatic retention of every old line. No changed Markdown means not applicable, not a passing document check.
- Bound work to five tasks, existing authenticated read-only APIs, local Git history and disposable copies. No new model-worker calls, provider trials, production fixes, hook/settings changes, whole-suite run or historical-budget renewal. Native transcripts stay in a private mode-0700 directory; publish only sanitized counters, message identities and findings. Stop with explicit missing measurements rather than widening the sample.
- Completion means a per-task ledger, actual-versus-replayed finding classification, usage/turn coverage, limitations and a recommendation. Keep report and evidence on task branch `docs/selective-check-sample`; use ordinary package validation/diff checks for publication. Existing manual-title pause remains unchanged.

## Progress

- [x] Freeze five PRs and task selection before checker execution.
- [x] Reconcile native task windows, checker receipts, model counters and delegated coverage.
- [x] Replay selected document checks, adjudicate findings and retain sanitized evidence.
- [ ] Verify accounting, publish results and summarize whether selective checking is justified.

## Result

**No demonstrated productivity payoff in this sample. Keep checking optional; do not add automatic hooks or mandatory checks.** None of the five tasks invoked the optional template checker in the observed native tool receipts. Consequently, there were zero observed mistakes caught by that tool and zero checker-attributable extra responses. This is evidence of non-use, not proof that checking would be free, useless or beneficial if adopted.

The retrospective checks found only pre-existing link/anchor findings, not new findings introduced by these tasks. Large provenance documents exhausted the checker's path budget. This argues for narrow, need-driven use, not scanning every changed document as a completion gate.

## Task ledger

All tasks belong to `fcoouto/vibe-workflow`; merge timestamps are on 13 November 2026. The sample was fixed before checker execution. Native root windows begin at the recorded task request and end at PR merge. Existing dispatched children and the already-completed activation probe are included; no new workers or trials were launched.

| PR / ordinary task | Changed Markdown | Observed assistant responses | Observed task-window tokens | Actual template-check catches | Checker-attributable extra responses |
| --- | ---: | ---: | ---: | ---: | ---: |
| [#290: retire advisory replay](https://github.com/fcoouto/vibe-workflow/pull/290) | 5 | 50 | 7,492,068 | 0 | 0 |
| [#288: minimum-only compatibility](https://github.com/fcoouto/vibe-workflow/pull/288) | 10 | 282 | 59,716,105 | 0 | 0 |
| [#285: enable escalation gate](https://github.com/fcoouto/vibe-workflow/pull/285) | 0 | 138 | 21,230,495 | 0 | 0 |
| [#286: admission contention test](https://github.com/fcoouto/vibe-workflow/pull/286) | 0 | 53 | 9,116,731 | 0 | 0 |
| [#284: enable project cleanup](https://github.com/fcoouto/vibe-workflow/pull/284) | 1 | 34 | 4,036,487 | 0 | 0 |
| **Total** | **16 document snapshots** | **557** | **101,591,886** | **0** | **0** |

These are whole-task observation-window counts, **not checker overhead** and not a comparison between checked and unchecked tasks. They include implementation, research, review and native compaction within the selected windows. Response counts are model responses, not user conversation turns. Ordinary reviews/tests did find and repair issues in some tasks; those are not credited to a template checker that was never invoked.

### Accounting

| Native token category | Count |
| --- | ---: |
| Uncached input | 3,187,735 |
| Cache read | 98,224,896 |
| Cache write | 0 |
| Output | 127,163 |
| Reasoning | 52,092 |
| **Total** | **101,591,886** |

The ledger contains 557 assistant-response counters and two compaction counters. Model labels recorded by the native messages aggregate to `openai/gpt-6.1-sol#high`: 97,555,399; `openai/gpt-6-astra#high`: 4,036,487. This reports recorded identities, not an independent provider/model attestation. OpenCode's normalized token categories are summed without adding cached input or reasoning twice; raw native fields are retained in [results.json](2026-11-13-selective-check-sample/results.json).

Root history pages contain each task-start message and every subsequent message through merge. Eight dispatched child histories and one existing gate-probe history were fully paginated. Message IDs are deduplicated across tasks and sessions. No selected usage record had missing token fields. Four responses finish 1–2.3 seconds after the merge cutoff: their whole counters are retained under the response-creation rule and explicitly identified. Per-tool token allocation is unavailable. Post-merge reporting, synchronization/cleanup and unrelated later work are excluded. Hidden provider retries or out-of-band activity cannot be inferred from these counters.

The gate task includes an existing bounded enforcement probe because it was part of that activation request. That historical probe is not a template-check treatment and was not repeated here. The later extensive gate-qualification PR #287 was excluded before measurement.

## Retrospective checker replay

Used the installed Codex-package checker from version `0.1.39`, with recorded SHA-256 source identities. Materialized immutable merge and first-parent trees into private disposable directories; all tracked paths existed in each replay tree. No project code was executed. The checker received changed Markdown only, no source templates or blanket `--before`: these ordinary edits intentionally change existing lines, so no unauthorized line-preservation contract was invented.

Initial once-per-task batches:

- **#290 and #288:** incomplete. Both reached the 64 accessed-path limit and the 64-finding output limit. A missing budget observation is not a passing check or a document defect.
- **#284:** clean; approximately 1.3 ms for the checker call.
- **#285 and #286:** not applicable; no changed Markdown.

A bounded, model-free per-document diagnostic followed the incomplete batches without replacing their results. Across the same 16 document snapshots: **nine clean**, **five complete with legacy link/anchor findings**, and **two incomplete provenance scans** (`docs/policy-sources.md` at the two merge snapshots). Every non-budget finding matched the parent snapshot by code, path and source-line text, avoiding false novelty from shifted line numbers. No newly introduced finding was observed in the covered output. The incomplete scans prevent a clean verdict on the complete documents.

Pre-existing findings include references to retired/missing plan files, such as `docs/superpowers/plans/2026-11-08-smart-routing.md`, and missing-anchor reports in existing routing documentation. Missing-file examples were checked against Git objects, not only the disposable copy. Anchor reports remain bounded-parser findings, not independent renderer conformance judgments. No backlog fixes were made or counted as historical catches.

Per-document calls took approximately 0.3–30.4 ms in this diagnostic. Initial batches took approximately 1.3–35.4 ms. These are in-process local checker durations, excluding CLI startup, copies, human/model interpretation and repair. They do not estimate extra model turns or tokens. The retrospective checks themselves made no model calls.

## Interpretation and next action

Selective checking is **conditionally reasonable as an optional safety tool**, but its economic benefit remains unmeasured. This sample does not justify enforcing it. Two of five ordinary tasks had no applicable document batch; broad scans of the larger document tasks produced old findings and exhausted limits.

Use it when a specific preservation or local-link risk warrants checking a small explicit document selection and equivalent evidence is not already available. Do not require `prepare` before `check`; do not turn legacy backlog findings into automatic repair work. For long, heavily linked provenance files, retain existing task-specific checks and manual review rather than treating the bounded helper as a complete audit. No new wrapper, changed-line parser, larger limit or automatic hook is proposed here.

To measure payoff rather than availability, future real work must contain actual optional-check invocations with a recorded pre-check defect, confirmed correction, subsequent response count and native usage. There is no reason to rerun synthetic model comparisons or renew the closed hook pilot now. Historical task totals cannot supply a counterfactual cost for a check that did not run.

## Evaluation overhead, safety and verification

The evaluation's coordinator consumed **at least 7,134,825 tokens** through **21:40:08 UTC**, including 6,770,688 cache-read tokens, across 23 completed responses. The [evidence snapshot](2026-11-13-selective-check-sample/results.json) records the request ID and raw categories. In-flight and later collection/publication/final-response work are excluded. This is separate from the 101,591,886 historical task-window tokens and is not a billed-dollar estimate. No new model worker, isolated trial, hook, setting or provider route was launched.

Collector self-checks verified literal token arithmetic (28 tokens across all five disjoint categories), explicit missing-usage handling, command detection and a negative native-read control. Missing-link failure and repaired-success controls passed. Published per-message counters independently reconcile exactly to the table; source identities, changed paths, selection and immutable snapshots are retained. The private collector and native transcripts remain outside Git; shared evidence excludes prompts, raw tool inputs/outputs, credentials and client configuration.

Review scope is this report and sanitized numeric evidence only; product code, installed packages, hooks and settings are unchanged. Coordinator checked record attribution, arithmetic, limits, findings classification and redaction directly. No independent new-model report review or full-suite/live-client test is claimed. Package validation and diff-check outcomes belong to the publication record. Current source changes after `0.1.39` are not relabelled as released behavior.
