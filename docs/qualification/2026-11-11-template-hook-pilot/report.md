# Template helper and native hook pilot

## Result

**No total-token savings in this pilot.** Across two matched documentation tasks, helper-only consumed 24.0% more tokens than the selective baseline. Adding the advisory hook increased the helper total by another 2.1%. All six recovered comparisons passed byte-hash preservation, mechanical checks and coordinator/independent semantic inspection.

Keep selective loading as the default. Retain helpers for explicit mechanical-check needs; this sample does not justify automatic hook installation or universal helper invocation.

## Matched observations

OpenCode 2.2.6, `openai/gpt-6-luna#low`, existing ChatGPT subscription, identical initial task inputs, requirements and independent checks. Include cached input in consumption. OpenCode reports reasoning separately from output; native samples include reasoning counts larger than output. Claude's thinking count is a subset of its output and is not added again. These are token counts, not billed dollars.

| Task | Arm | Input | Cache read | Output | Reasoning | Total | Requests |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Minimal | Baseline | 8,015 | 5,504 | 864 | 75 | 14,458 | 4 |
| Minimal | Helper | 4,859 | 15,616 | 680 | 0 | 21,155 | 6 |
| Minimal | Helper + hook | 5,005 | 15,616 | 677 | 0 | 21,298 | 6 |
| Customized | Baseline | 6,761 | 14,720 | 1,275 | 0 | 22,756 | 5 |
| Customized | Helper | 6,136 | 17,792 | 1,052 | 0 | 24,980 | 6 |
| Customized | Helper + hook | 6,214 | 18,304 | 1,302 | 0 | 25,820 | 6 |
| Both | Baseline | 14,776 | 20,224 | 2,139 | 75 | 37,214 | 9 |
| Both | Helper | 10,995 | 33,408 | 1,732 | 0 | 46,135 | 12 |
| Both | Helper + hook | 11,219 | 33,920 | 1,979 | 0 | 47,118 | 12 |

Cache writes were zero in all comparisons. Helper versus baseline: +46.3% on minimal and +9.8% on customized. Hook versus helper: +0.7% and +3.4%. Helpers reduced uncached input and generated output, but additional requests reread more cached context. No comparison needed a repair response, so no avoided-repair benefit was demonstrated.

Each helper arm executed one preparation and one explicit check. Each hook arm added one silent after-edit check: 35.2 ms and 34.4 ms. Both emitted zero feedback bytes. The 983-token helper-to-hook difference includes changed instructions and stochastic output; it is not a causal estimate of injected feedback tokens. Probe notice text is retained verbatim in the sanitized results; no byte count is converted into tokens.

## Native qualification

| Client | Observed | Limit |
| --- | --- | --- |
| Claude Code 2.1.311 | Actual `PostToolUse` feedback reached the model and preserved original tool output. Corrected diagnostic logged one broken-link notice, two suppressed repeats, silent repaired checks and unrelated-file bypass. Original README restored. | Initial state corruption is retained. Worker batched two reads despite the serial prompt; independent hook receipts establish suppression. CLI evidence does not qualify Desktop or managed guards. |
| OpenCode 2.2.6 | Actual after-edit feedback reached the model and preserved original content entries. Silent success followed repair and occurred in both matched hook arms. Corrected direct helper tools executed in all four helper arms. | Native duplicate suppression remains unobserved: probe-only `pilot_batch` was hidden by configuration. Unit controls do not replace that observation. |
| Codex 0.167.0 | Official trust and feedback contracts inspected. | Live probe held for supported exact-definition hook review. No trust bypass, fabricated trust or managed-policy workaround. |

These hooks are advisory. No Stop blocker, automatic repair loop, production registration, managed task-guard qualification or universal enforcement claim was introduced.

## Failures and recovery

Original allocation: 12 attempts, at most two concurrent, 45 minutes. Start: **21:00:34.534 UTC on 11 November 2026**. Unchanged deadline: **21:45:34.534 UTC**.

1. Initial OpenCode operator disappeared during a harness location-shutdown event. Three request receipts and its transcript were recovered. Its exact orphan server was stopped and temporary credential removed. An isolated-server password appeared in diagnostic output; that server was terminated and its listener was confirmed absent. Sensitive logs are not published. That probe also exposed normalized tool-result arrays, which the adapter had mistaken for string content.
2. Initial Claude feedback worked, but append-mode state writes concatenated JSON. A failing repeat-observation test reproduced this; locked read/write state fixed it. Fresh diagnostic passed the scoped native checks.
3. OpenCode diagnostics exposed hidden custom tools: direct registration and narrow permission exceptions were needed. Earlier probe failures remain failures; native duplicate-suppression qualification remains pending.
4. Six original comparison reservations failed before client creation or inference. The operator incorrectly required a fresh 45-minute credential lifetime at every launch. A regression and fix bound it to the original remaining deadline.
5. The user approved **six additional comparison attempts**, preserving the deadline, concurrency and subscription-only constraints. All six recoveries completed. Total allocation: 18 attempts, including the reserved review; no further retry is authorized.

The six pre-inference failures consumed zero model tokens, with explicit authentication-preflight evidence. They remain in [results.json](results.json), not silently replaced by successful recoveries.

## Total effort and limits

Comparisons consumed **130,467 tokens**. Five probes/diagnostics consumed **105,829 tokens**: 22,285 on OpenCode and 83,544 on native Claude. Combined trial consumption: **236,296 tokens**.

The [accounting snapshot](accounting.json), taken at **21:30:42 UTC**, also includes actual coordinator and reviewer counters:

| Work | Model | Tokens |
| --- | --- | ---: |
| Coordinator preparation | `openai/gpt-6-astra#high` | 6,057,978 |
| Coordinator execution, debugging, review and publication so far | `openai/gpt-6-astra#high` | 11,939,390 |
| Coordinator compaction | `openai/gpt-6-astra#high` | 471,382 |
| Independent review | `openai/gpt-6-astra#high` | 809,318 |
| Comparisons and probes | Models above plus `openai/gpt-6-luna#low` and native Claude | 236,296 |
| **Observed lower bound** | | **19,514,364** |

Coordinator scope begins with the explicit hook-pilot request, excluding earlier helper development. Its 18,468,750 tokens include 17,622,144 cache-read tokens. Review used 13 model responses and 809,318 tokens, including 739,840 cache-read tokens. One unfinished coordinator response and subsequent publication/final-response usage are excluded, so the sum is a lower bound, not a final total or bill. Native Claude upstream retries are not independently enumerated; final native counters are retained. No additional accounting model calls were made.

Eight model-free Python operator tests passed after review. Adapter controls passed for original-result preservation, array content, bounded feedback, deduplication, ignored tools, silent success and helper failure. Native filesystem canaries and guarded request refusal passed. The unchanged helper retains its 64-test evidence on `567fbce`. Package validation is recorded with the publication commit; no full suite was run.

Two tasks, one model, one run per arm and uncontrolled provider caching establish observations only. Outputs passed the same checks but differ in wording and length. No statistically reliable or collection-wide savings estimate follows. Extensive coordinator preparation/debugging must not be presented as free.

## Independent review and disposition

Native General child `ses_e337f7401ffe7plsnXwSmkrklQ` completed one read-only behavior/security/accounting review at 21:28:53 UTC, within six minutes and the original deadline. Its native transcript confirms `openai/gpt-6-astra#high`; no override was supplied. Review covered committed `ca5fcd95..5f45297` and the then-untracked pilot evidence/operator. The coordinator retained write ownership; read-only behavior was a brief constraint, not a claimed sandbox.

**F1, low, fixed:** protected-file checks used newline-normalizing text reads. A synthetic LF-to-CRLF change incorrectly passed. A failing regression reproduced the defect; the checker now compares protected files against frozen SHA-256 hashes. All eight tests passed, and all six recovered final protected-file hash sets matched their original manifests. The correction affects collection only; it required no repeated inference and does not alter task outputs or counters. Final operator hashes and pre-review hashes are both retained. Unaffected independent coverage is reused.

The reviewer independently reconciled totals, inspected all six outputs and found no substantiated security vulnerability in the inspected scope. The detailed review remains in the private native conversation; no separate private report was saved. No broad security guarantee follows.

## Protocol deviations

- Comparisons proceeded as exploratory measurements after OpenCode feedback delivery worked, although full native deduplication qualification had not passed. The plan's full-qualification prerequisite was therefore not satisfied; these results cannot establish a qualified production hook.
- Claude used a 24-turn bound and process deadline, not an independently enforced upstream-request/retry cap. Filesystem canaries and tool restrictions were tested; an OS provider-domain network allowlist was not established.
- One OpenCode diagnostic used the runner's four-minute default rather than the planned three-minute probe timeout; it completed in 27.923 seconds. Actual runs stayed below three minutes. Comparisons used a two-worker queue, so listed submission order is not completion order.
- The interrupted OpenCode operator demonstrated that its in-process watchdog did not survive coordinator process loss. Its guarded deadline remained, and the exact orphan was reconciled manually. No automatic crash-cleanup guarantee is claimed.

## Evidence and next action

The [protocol](protocol.md), [results](results.json), [source identities](source-identities.json), [accounting](accounting.json) and [archived operator](operator/README.md) retain comparisons and failures. Sensitive native logs and authentication storage remain private. All recorded isolated server PIDs and ports were absent at final reconciliation; native Claude launchers observed their processes stopped. No further live attempt is authorized.

Publish this bounded evidence through draft PR #254, retaining its `[BLOCKED]` status for incomplete native qualification. Further Codex trust or OpenCode deduplication probes need a new authorized allocation. Production hooks remain disabled.
