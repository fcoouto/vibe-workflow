# Free Go template qualification — explicit-path follow-up

**Verdict: not qualified.** Explicit paths improved template retrieval. They did not establish reliable completion or accurate reasoning. The review and handoff layouts were actually read and used in one Beam run, but its report still contained a factual access-control error. Project-document shapes were still not opened. Iceman remained unassessed because the provider refused access under the existing account privacy setting.

The user approved this separate twelve-slot follow-up. The [original trial](../2026-11-10-templates-go/README.md) remains unchanged. No production template, policy, skill or project instruction was edited. Only the disposable trial owner's routing paragraph changed, as shown to the user before execution and retained [verbatim below](#instruction-change-before-and-after).

## Per-case results

| Model / case | Prose arm | Template arm |
| --- | --- | --- |
| Beam Free / minimal docs | Eight content criteria pass | Eight content criteria pass; reads catalog and handoff, **not project-document shapes** |
| Beam Free / customized docs | Fails document ownership/next-delivery mapping and snapshot-bound handoff | Provider TLS failure after inspection; no edits; **unassessed**, not a reasoning failure |
| Beam Free / read-only review | Findings present; counterevidence/prerequisite and reviewer/version-reporting failures | Both layouts read and visibly used; counterevidence and empty-identity reasoning failures |
| Iceman Free / minimal docs | Provider privacy refusal; unassessed | Provider privacy refusal; unassessed |
| Iceman Free / customized docs | Provider privacy refusal; unassessed | Provider privacy refusal; unassessed |
| Iceman Free / read-only review | Provider privacy refusal; unassessed | Provider privacy refusal; unassessed |

One Beam prose run satisfies the fixed workflow criteria. The minimal template-arm output also satisfies the eight content criteria, but it does not qualify project-template use: it never opened a project-document layout. Both completed review outputs fail substantive criteria. The interrupted customized treatment and all Iceman cases are excluded from content grading, not counted as model failures or passes. Unequal completed samples and one sample per cell prevent a meaningful aggregate uplift or reliability estimate.

### What improved

- Original treatment: zero template files read in all three Beam cases. Follow-up minimal: `.workflow/templates/README.md` and `.workflow/templates/task-record.md` read. Follow-up review: `.workflow/templates/review-report.md` and `.workflow/templates/task-record.md` read. Interrupted customized case: catalog read before the provider failure.
- The review treatment reproduced the review layout and task/handoff layout. It included reviewer identity, source/runtime versions, authorization context and explicit observed-versus-unrun evidence that the prose report omitted.
- No project-document shape under `.workflow/templates/project/` and no setup-record layout was read. Reaching an index is not retrieval of the linked document shapes. The completed minimal case therefore supplies handoff/catalog evidence, not candidate D's content qualification.

### What failed

- **Template-arm review:** its identity finding says an empty string is falsy, so some empty identities are denied. That misreads the expression: `actor` and `record` are truthy objects, `"" === ""` is `true`, and `Boolean(true)` remains `true`. The coordinator independently checked that equal empty IDs and equal numeric IDs are allowed, while a null actor is denied. Templates supplied headings; they did not prevent incorrect source reasoning (R3/R6).
- **Both reviews:** “counterevidence” was absent or cited confirming failures, rather than evidence that could disprove or bound a finding (R5). The prose report additionally claimed no attacker prerequisites for the identity issue without establishing a trust/call path; it omitted the supplied reviewer identity and runtime/check versions (R5/R6).
- **Customized prose:** the responsibility map treated nested `src/AGENTS.md` as client instructions instead of a source-directory owner. It offered only generic “extend checks or docs” wording, not a concrete next product delivery. The final handoff omitted a snapshot/candidate binding (D4/D7). Plain code-formatted handbook references also invite destination-relative path confusion; they are noted, not newly counted as broken Markdown links.
- The minimal outputs still copied temporary task write limits into standing `AGENTS.md`. This remains an unscored future-task compatibility concern from the original trial, not an added criterion after viewing results. Output artifacts are not approved project guidance.

## Availability, execution and safety

All **twelve fresh task sessions** were created. Beam produced five terminal successes and one terminal provider transport failure. Iceman produced six terminal provider refusals before any model completion/tool use. The harness's `finished` label means execution/wait and transcript collection ended; grading uses each transcript's actual terminal event.

- **Beam:** exact observed route `opencode-go/beam-free#low`; 35 primary requests passed the guard across six slots, including the final failed transport attempt. The customized treatment ended with `provider.transport`, `EPROTO: The TLS handshake failed. Does the server speak TLS on this port?`. It was not retried or replaced.
- **Iceman:** six exact `opencode-go/iceman-contributor-free` requests with native variant `default` passed the corrected guard. Isolated discovery still exposed no effort variants; `default` is not an asserted low-effort setting. Each request received `provider.invalid-request`, HTTP 400: the model trains on request data and requires the account option named “Allow paid endpoints that train on request data”. User consent to synthetic-data exposure does not change that account setting. The setting is broader than this trial, so it was left unchanged. This is a provider-policy availability limitation, not model incapability, and the wording does not establish that these zero-listed-price calls would be billed.
- **41 total hook-admitted primary requests.** All selected model prices were listed as zero. Returned Beam assistant messages reported an observed total cost of **$0**. Failed/refused calls have no returned token/cost measurements; provider billing was not independently audited. No paid fallback, auxiliary generation, warming, compaction, task replay or extra grader call was admitted.
- All original text, code/check bytes, nested instructions, filenames and selected fixture choices were preserved. Writes stayed inside authorized Markdown destinations. Independent protected checks remained Python exit 0 or boundary seven tests with exactly the original five passes/two expected failures. No generated Markdown links were broken and no author placeholders were detected by the focused scan.
- The runtime denied configuration reads attempted by Beam. No credential/configuration content was returned. No shell, network-tool, execute or delegation use was observed. OS preflight again blocked real-project reads and out-of-runtime writes. The runtime retained network authority for provider access; this was not a hostile sandbox-escape test.
- Every evaluation server was confirmed stopped. Raw error bodies/transcripts and operator scripts remain private under the approved temporary root. Shared evidence redacts account identifiers and excludes credentials.

## Harness repairs and proof

The native-default guard regression was reproduced without inference: the original exact-route guard rejected Iceman's observed `default` because its control expected `null`. The repaired control pins exactly `default` for the unqualified Iceman route; it does not accept arbitrary variants or invent a reasoning-effort option. A zero-inference self-check then admitted the selected route and rejected the wrong provider, model, variant, auxiliary title and exhausted budget; it also verified retries remained disabled. Current isolated discovery and the original six blocked hooks supplied the variant evidence.

Transcript collection changed from `limit=1000` to `limit=200`. Preflight observed HTTP 400 for 1000 and a valid-query HTTP 404 for 200 against an absent session, without creating or prompting a session. All twelve real task transcripts were then collected within 200 messages through their terminal idle event. No recovery replay or pagination repair was needed.

The live boundary fixture, owner/prose requirements and original eight criteria remained fixed. All twelve prompts were byte-identical to their matching original-trial prompts; the only task-material change was the treatment owner's routing paragraph. Template/fixture source hashes matched the original manifest. Each case used a fresh isolated HOME/XDG and synthetic project, at most two concurrent slots, with a 32-request/300-second cap. The user’s coordinator, account credentials, endpoint/privacy settings and original results were preserved. Grading was performed by the coordinator independently of worker self-reports; no second agent/human adjudicator or paid grader was used.

Final focused verification passed: twelve session outcomes, 41 exact-route requests, frozen operator/prompt/criteria/source digests, input preservation, protected baseline results, transcript collection, report links and credential/account-identifier redaction. `python3 -B scripts/validate.py` passed with the existing package-size warning; `git diff --check` was clean. No package behavior changed. The evidence report itself still awaits independent review; these checks and worker-output grading do not establish merge readiness.

## Instruction change: before and after

These are the complete changed paragraphs in disposable `.workflow/OWNER.md`. Prose-arm owners did not change. No template content changed.

### Documentation — before

```text
For the document responsibilities, select and read the relevant layouts linked by [project template index](templates/README.md#project-documents). Use applicable shapes; no new files are authorized by a shape. Use [task/handoff record](templates/task-record.md) for the final handoff. Do not copy package-relative author links into generated documents.
```

### Documentation — after

```text
For the document responsibilities, read `.workflow/templates/README.md` with the read tool, then select the relevant linked layouts. Markdown links are relative to the containing file; pass project-root-relative paths to tools. For example, its `project/product.md` link resolves to `.workflow/templates/project/product.md`. Read `.workflow/templates/task-record.md` for the final handoff. Use applicable shapes; no new files are authorized by a shape. Do not copy package-relative author links into generated documents.
```

### Review — before

```text
For this code+security review, read [review report](templates/review-report.md) and [task/handoff record](templates/task-record.md), then use their applicable fields.
```

### Review — after

```text
For this code+security review, read `.workflow/templates/review-report.md` and `.workflow/templates/task-record.md` with the read tool, then use their applicable fields.
```

## Evidence and disposition

- [Approved follow-up plan](../../superpowers/plans/2026-11-10-templates-go-followup.md)
- [Frozen bindings, budgets and source hashes](manifest.json)
- [Unchanged prompts and actual owner text](prompts.json)
- [Machine-readable before/after paragraphs](instruction-changes.json)
- [All twelve outcomes and criterion dispositions](results.json)
- [Complete Beam outputs, final chat and tool paths](outputs.json)

**Disposition:** explicit root-relative tool paths are supported as a local discovery improvement. The current evidence does not support marking the templates or low-effort workflow qualified. Review templates improved field coverage but did not prevent a factual error; project-document templates still lack an actual-use result. No further instruction edits, privacy changes, model calls or automatic reruns were made. Further comparison requires a separately authorized budget and should target the remaining concrete failure, not rerun until green.

Full-suite tests, actual onboarding/reapplication/undo, native Codex/Claude integration, statistical replication and hostile sandbox tests were not run. This report covers the bounded OpenCode documentation/review slice only.
