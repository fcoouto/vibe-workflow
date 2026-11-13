# Free Go template qualification — partial, not qualified

**Verdict:** This trial does **not** establish effective template use or qualify the workflow for the selected free models. Beam completed the tasks, but none of its three template-arm sessions opened a template. LongCat was unavailable in isolated discovery; a harness guard error blocked Iceman before inference. These are three different outcomes, not eighteen model failures.

## Results

| Model / reasoning | Minimal documentation, prose → templates | Customized documentation, prose → templates | Review and handoff, prose → templates |
| --- | --- | --- | --- |
| Beam Free / observed `low` | Content criteria pass → content criteria pass, **template use fails** | Missing next product delivery → same omission, **template use fails** | Counterevidence/accuracy failures → counterevidence failure, **template use fails** |
| LongCat 2.5 Free / no exposed reasoning control | Unavailable → unavailable | Unavailable → unavailable | Unavailable → unavailable |
| Iceman Contributor Free / no exposed reasoning control | Harness-blocked → harness-blocked | Harness-blocked → harness-blocked | Harness-blocked → harness-blocked |

The strict workflow result is **1/3 prose versus 0/3 template runs for Beam**. Considering the eight content criteria alone, both arms pass only the minimal case: **1/3 versus 1/3**. No meaningful template-content uplift can be estimated: the treatment failed to reach the model. These small counts are not reliability rates, statistical estimates or proof that template content makes output worse.

### The discovery failure

Each template-arm session successfully read the complete `.workflow/OWNER.md`, including its last-line instruction to read and use linked templates. The Markdown links resolved relative to that file; the layouts were at `.workflow/templates/…` and their frozen bytes matched the source templates.

No subsequent `read` targeted a template. In the review session, Beam searched `templates/**` from the project root, got no files, then searched `**/*.md`, which did not expose the hidden `.workflow` subtree. It continued without resolving the relative link or reporting template use blocked. The two documentation sessions likewise continued after root discovery without reading the linked index or handoff layout. This is observed failure of the **trial's linked-discovery route**, not proof that the production onboarding entry or native instruction reader is broken.

The files were available to direct local reads. Nevertheless, this trial did not exercise their content. An assertion that the resulting headings “look like the template” would not establish retrieval or use.

### Content failures and counterevidence

- **Customized documentation, both arms:** no next useful product delivery was documented. The prose output ended at owner review/merge; the treatment's generic future backup/undo/integration suggestion did not identify a concrete label-tool delivery. This requirement was present in both frozen prompts and owners. It is grouped under the product responsibility (D4), not an invented requirement for particular headings or a separate mapping table.
- **Prose review:** the cross-tenant finding listed counterevidence only as “none”; it did not identify a checked counterexample or explain the lack of available countervailing evidence. Its second finding described the equality as “loose”, although the actual source uses `===` (R5/R6).
- **Template-arm review:** counterevidence was hypothetical caller enforcement for one finding and “none” for the other, rather than something checked (R5). The report did preserve the actual denial of absent inputs in its narrative, so it had useful counterevidence available but did not apply it properly.
- **Both reviews did identify the two intended classes of reachable access failure**, with concrete examples and the correct source line. No repair was attempted. Unsupported deployment or independent-execution claims were not found.
- The treatment review proposed a `tenant` field while the protected evaluator uses `tenantId`. The visible fixture does not establish the tenant property's spelling, so this is an **underspecified schema/unsafe proposal limitation**, not a separately scored hidden criterion. Do not adopt that proposal as a verified fix.
- Existing project statements, code, checks, nested instructions, paths/capitalization and local-only/Codex/manual-merge/Workstreams choices were preserved. Allowed Markdown changes only; no observed shell, network-tool, execute or delegation use. A treatment session attempted to read `opencode.json`; the read was denied. No credential/configuration content was returned.

Additional unscored limitations: minimal outputs copied some temporary task restrictions into standing `AGENTS.md` guidance; the treatment mislabeled the root instruction owner as “nested”. Those artifacts are evidence, not approved project guidance. The trial's content passes are limited to its fixed criteria and do not certify future-task compatibility.

## What actually ran

- **18 frozen slots:** 12 fresh sessions created, 6 of those reached inference (all Beam); 6 LongCat slots stopped at discovery. No replacement sessions or post-failure coaching were sent.
- **34 primary requests passed the request hook**, all exactly `opencode-go/beam-free#low`. All returned assistant messages reported that route and zero cost; observed total cost **$0**. Counts describe hook-admitted requests, not independently audited provider billing. No paid fallback, title, warming or compaction request was admitted.
- LongCat was listed by the connected host catalog but absent from isolated model discovery. The cause was not established; it is **not claimed** to be a confirmed region-policy denial. No endpoint/privacy setting was changed.
- Iceman was discovered with zero listed prices, but OpenCode resolved its unqualified selection to variant **`default`**. The trial guard expected `null` for models with no exposed picker variants and rejected all six requests before transmission. This is an **operator/harness error**, not evidence of model incapability or provider unavailability. No Iceman completion was obtained and no low reasoning setting is claimed.
- A second operator error requested `limit=1000` from the message endpoint; this runtime permits at most 200. It occurred after execution/wait, not during the model's work. Existing transcripts were recovered using read-only API requests at limit 200, through their terminal idle events. Inference was disabled during recovery; **zero additional requests** passed the guard. The original collection errors remain in the machine-readable results.
- Beam's six terminal events were `succeeded`; Iceman's were `failed` with `guard_variant`. A successful terminal event is execution completion, not evaluator acceptance.

## Method and controls

The [approved plan](../../superpowers/plans/2026-11-10-templates-go-qualification.md) defines the eight mandatory criteria for each case. Grading was performed by the coordinator independently of worker self-reports, using complete generated files, the projected tool/message evidence, before/after hashes and protected checks. There was no external model grader or independent second human/agent adjudicator.

Both arms received the same prose requirements and supplied baseline results. Only the treatment additionally received unchanged templates and an owner link directing their use. Requirements were not silently withheld from the baseline. Pair order alternated across tasks/models; each slot had isolated inputs, HOME/XDG and mutable runtime resources. At most two slots ran concurrently. Each slot allowed at most 32 primary requests and 300 seconds; observed Beam durations were approximately 9–13 seconds, including startup/collection.

This is a **documentation/review slice**, using a trial-specific local owner and copied public policy context. It does not execute the complete onboarding procedure, prove production template discovery, or qualify native Codex/Claude integration. The two expected failing-test names were supplied to both review arms: this tests substantiation/reporting, not blind vulnerability discovery.

All fixture checks ran before inference and were independently rerun afterward. Python cases remained exit 0. Boundary cases remained exactly seven tests, five passing and the same two expected failures. All reviewed input bytes were unchanged; all original documentation text was retained in its original owner. Generated Markdown links had no missing target/anchor; no author placeholders were found. No real project was modified by a worker, and no template/fixture was repaired in response to a failed grade.

The runtime used OpenCode 2.2.6, coordinator Python 3.15.2 and Node v26.13.0. Model selection used the current Go catalog and official documentation for [Go](https://opencode.ai/docs/go), [configuration](https://opencode.ai/docs/config), [permissions](https://opencode.ai/docs/permissions), [API](https://opencode.ai/docs/api) and [plugin hooks](https://opencode.ai/docs/build/plugins/server), checked 10 November 2026. The existing recorder was reused without loosening the unrelated qualification harness's pinned route or durable ledger. Trials used a separate finite budget.

Final static checks confirmed frozen prompt/criteria/source digests, all eighteen classifications, exact admitted route/effort, absence of recovery inference, original input preservation and report links. The selected credential was absent from both public evidence and retained runtime files. `python3 -B scripts/validate.py` passed, with only the existing package-size warning; `git diff --check` was clean. These checks validate evidence/package consistency, not template effectiveness. No package functionality or instruction source changed, so no full-suite run was selected.

The existing selected Go key was obtained through the local authenticated API and supplied privately to the isolated process environment. No other credential was copied; no key was printed or saved in the artifacts. OS preflight observed denial of a real-project read and an out-of-runtime write, while allowing trial reads/writes. Tools were deny-by-default except local reads/search and exact authorized Markdown edits; external-directory access, shell, execute, network tools, delegation and configuration writes were denied. This was not an adversarial sandbox escape evaluation, and the server retained network authority for provider access. All evaluation servers were confirmed stopped; raw traces and synthetic runtime state are retained privately for investigation, not left running.

## Evidence

- [Frozen bindings and source hashes](manifest.json): source commit, cases, per-slot input/prompt digests, budgets and operator digest. Absolute runtime paths and credentials excluded.
- [Frozen prompts and owner routing](prompts.json): all eighteen intended inputs, including equivalent prose requirements.
- [Per-slot grades and observed outcomes](results.json): all mandatory criteria, treatment retrieval, unavailable/infrastructure classifications and retained collection errors. `null` is unassessed, never a pass.
- [Beam artifacts and tool paths](outputs.json): complete output files/final chat, tool names, requested read/search paths and observed statuses. No private raw transcript or bootstrap credentials.

## Next decision

Do **not** qualify or silently rewrite templates based on these results. First correct the harness's native `default`-variant handling, retain its fail-closed route/price checks, and verify transcript collection before inference. A further trial should distinguish explicit path resolution from actual content use. Any corrected Iceman runs or explicit-path comparison need a separately recorded extension to the exhausted eighteen-slot budget; failures in this trial remain failures. No further model calls were made.

Full-suite tests, actual onboarding/reapplication/undo, repeated statistical samples, hostile sandbox tests and paid-model comparisons were deliberately not run.
