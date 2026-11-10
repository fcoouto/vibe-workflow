# Luna 6 / Haiku 5.5 — timed template qualification

**Verdict: not qualified. Stop condition: the twenty-minute limit, not success on both models.** Both models actually retrieved and used templates. Both passed the fixed read-only review case. Neither satisfied all three template cases within this evaluation: Luna's recovered minimal documentation deleted original text; Haiku's clarified customized documentation was interrupted before its setup record and final handoff.

The [original Go trial](../2026-11-10-templates-go/README.md) and [explicit-path follow-up](../2026-11-10-templates-go-followup/README.md) remain unchanged. This round used the selected subscriptions, not free Go models: `openai/gpt-6-luna#low` through the existing ChatGPT OAuth connection, and `claude-agent/haiku#low` through the existing Claude Max login. Discovery named the latter Haiku 5.5. The coordinator model, account/privacy settings, production templates, policy files and real projects were not changed.

## Results

Pass means all eight [original content criteria](../../superpowers/plans/2026-11-10-templates-go-qualification.md#fixed-cases-and-mandatory-grading) pass. A template pass also requires observed relevant-layout retrieval and visible use. Unavailable or interrupted work remains unassessed, not a model failure or a pass. A successful execution event alone is not a grade.

### Initial twelve-slot matrix

| Model / case | Prose arm | Template arm |
| --- | --- | --- |
| Luna / minimal docs | Pass | TLS failure after reading layouts; no edits; unassessed |
| Luna / customized docs | Fails preservation and final-chat snapshot binding | Pass; selected project shapes, setup and handoff read and used |
| Luna / read-only review | Pass | Pass; review and handoff layouts read and used |
| Haiku / minimal docs | Fails original-text preservation | Fails original-text preservation despite reading layouts |
| Haiku / customized docs | Catalog readiness race; zero inference; unassessed | Fails preservation and factual check coverage |
| Haiku / read-only review | Pass | Catalog readiness race; zero inference; unassessed |

### Disclosed recovery of three unassessed cells

Fresh runtimes retained the exact original prompts and criteria. Prior failures were not replaced. The native Claude discovery repair waited until the selected model appeared, rather than stopping at the first unrelated `model.updated` event. This was an operator repair, not a prompt change or an entitlement change.

| Cell | Recovery result |
| --- | --- |
| Luna / minimal templates | Completed, but failed preservation: removed the README directive and rewrote its original description. Final chat also omitted the supplied snapshot identity. |
| Haiku / customized prose | Pass; original text preserved and two assertions correctly described. |
| Haiku / review templates | Pass; both layouts actually read and used. |

### One common clarification comparison

Only the four Haiku documentation cells received the [disclosed clarification](#instruction-change-before-and-after), in both arms, from fresh original inputs. The same deadline remained in force.

| Case | Prose arm | Template arm |
| --- | --- | --- |
| Haiku / minimal docs | Pass; original text preserved | Pass; original text preserved and relevant layouts used |
| Haiku / customized docs | Pass; original text preserved and two assertions described | Deadline interruption; setup record and final handoff missing; unassessed |

The interrupted customized treatment preserved the original text in its five edited files and described the two assertions correctly. It nevertheless left three links to the absent `handbook/setup.md`. Those observations do not establish completed content compliance. All eight content grades remain null for this interrupted cell. A Luna clarification script was prepared but **never executed**; there is no clarified Luna result.

## What the comparison establishes

- **Retrieval improved over the Go trials.** All nine template slots that reached inference opened relevant document shapes or both review/handoff layouts. Luna's customized run selected product, overview and design shapes plus setup/handoff; it did not read every available project shape. The other documentation treatments opened broader sets. Exact successful reads are retained in [results.json](results.json).
- **Templates did not guarantee preservation or factual accuracy.** Original README directives disappeared from the initial Haiku documentation and recovered Luna minimal output. Luna's customized prose also deleted the release-name note. Initial Haiku customized treatment claimed three assertions/examples where `check.py` contains two, and treated internal-whitespace expansion as an existing gap rather than only a proposed requirement.
- **The common clarification helped completed Haiku outputs, not templates alone.** Both clarified minimal arms preserved the text; clarified customized prose also passed. A recovered customized prose run already passed before clarification. Samples and execution conditions do not isolate a causal template effect.
- **Review reasoning was substantively correct in both arms.** The completed reports identified same-owner/cross-tenant access and equal missing, empty or numeric identities granting access. They did not confuse truthy objects with empty identity fields or claim absent actors are allowed. Proposed reproductions were labelled static; supplied evaluator evidence was not presented as worker execution. The template reports visibly used their layouts, but the prose reports also passed.
- **One customized Luna pair favored the treatment.** Its template output preserved the release-name note and README directive that its prose output removed. This is a bounded observation, not a reliability or uplift estimate.

Passing these fixed criteria does not certify flawless standing guidance. For example, the clarified Haiku minimal setup record says local changes cannot be checked without Git; file comparison can check them. Some outputs also retain trial-specific write limits in standing guidance. These are disclosed artifact-quality concerns, not new post-hoc grading criteria or approved project instructions. No generated output was adopted into a real project.

## Execution, deadline and usage

The shared 1,200-second deadline started with the user's stop instruction and included preflight. First admitted inference occurred at **533.101 seconds**; last admission at **1,192.986 seconds**. The final batch recorded all owned servers stopped at **1,198.550 seconds**, using a small deadline safety margin. The stop-condition receipt was written later, at **1,345.01 seconds**; that is delayed bookkeeping, not an extended inference window. Its condition is `20_minute_limit` and its success flag is false.

- **19 total slots:** initial twelve, three disclosed recoveries, four common-clarification comparisons. Seventeen reached inference. Fifteen completed successfully as executions; five of those failed content criteria. Two discovery blocks, one TLS failure and one deadline interruption remain separately recorded.
- **104 hook-admitted OpenCode primary requests:** Luna 54; Haiku 50. Every admitted route was the selected provider/model with `low`; no auxiliary generation, automatic retry, warming, compaction, fallback or grader request was admitted. This records the OpenCode route/variant, not an independent audit of native SDK effort handling or physical transport requests.
- Maximum two concurrent slots, 32 primary requests and 300 seconds per slot, additionally clamped to the one shared deadline. No deadline reset after discovery repair, TLS failure or clarification.
- Returned-message token totals: Luna input 108,499, output 12,668, reasoning 8, cache-read 174,336, cache-write 0; Haiku input 102, output 56,705, reasoning 43,477, cache-read 583,366, cache-write 205,292. These are reported fields, not necessarily additive or comparable across runtimes, and omit unreturned usage.
- Returned assistant messages reported cost 0. **This does not establish zero cost or unused subscription quota.** Catalog price lists were empty for these subscription routes. Provider billing and native SDK internal requests were not independently audited.
- The interrupted transcript was recovered read-only after cutoff, with request budget zero and no subscription token in the collection environment. Its guard ledger showed **zero additional admissions**. No further model calls ran after cutoff.

The initial Luna transport error was `provider.transport`: `EPROTO: The TLS handshake failed. Does the server speak TLS on this port?`. The two initial Haiku failures occurred before session inference because asynchronous discovery had not exposed the selected route yet. Neither failure establishes model incapability.

## Safety and verification

Synthetic project copies lived outside real projects with isolated HOME/XDG directories. The OS profile denied reads under the real home and writes outside each runtime; native tools denied external-directory access and all actions except local inspection and exact authorized Markdown edits. The runtime retained provider-network authority. This was not a hostile sandbox-escape test. OpenCode's bundled native Claude provider used `settingSources: []`, disabled hooks/default tools and exposed only the controlled OpenCode tool bridge.

Every slot preserved source/check bytes, nested instructions, filenames/capitalization, fixture client/publication choices and read-only workflow material. No edit crossed the authorized Markdown paths. This does **not** mean every original document statement was preserved: the failures above are explicit. Configuration reads were denied and no configuration content was returned. No model shell, network-tool, execute or delegation use was observed.

The coordinator inspected complete outputs and handoffs independently of worker self-reports, verified frozen prompt/operator/criteria/source hashes, checked all guard admissions against the cutoff, compared write boundaries, and reran protected checks. Python documentation cases remained exit 0/no output. Every review copy retained exactly the original seven evaluator results: five passes and the expected cross-tenant/invalid-identity failures. Links resolved in completed outputs; only the interrupted customized treatment had three missing-file links.

Cleanup removed **310 evaluation-owned runtime files**, including native SQLite/WAL stores, isolated home/config/cache/state files and logs containing disposable localhost passwords. Necessary transcripts, guard evidence, synthetic outputs and operators remain private. A selected-credential byte scan found no matches before cleanup; thirteen JWT-shaped native policy-signature files were discarded without asserting that they were access tokens. The retained-private and public scans found no selected credential bytes or credential patterns afterward. Public artifacts also exclude account identifiers, native session IDs and machine paths. No global credential store or account setting was modified. This is file removal, not a secure-erasure claim.

No native OpenCode or Claude process with a working directory inside the timed root remained after cleanup. [verification.json](verification.json) records deterministic checks and cleanup. `python3 -B scripts/validate.py` passed, with the existing package-size warning; client execution was not checked by that command. The focused evidence cross-check passed and `git diff --check` was clean. No production instruction or template changed, so prepared instruction-map regeneration and behavior-suite tests are not required for this evidence-only addition.

## Instruction change: before and after

The initial matrix and recovery used unchanged explicit-path owner guidance from the Go follow-up. Only route metadata changed in initial prompts. Before the clarification comparison, the user saw these common-rule changes. Both prose and template arms received them in their disposable `.workflow/OWNER.md` and task prompt; production files did not.

### Preservation — before

```text
Preserve every original useful
statement and instruction; appending accurate guidance is fine.
```

### Preservation — after

```text
Preserve every original statement and instruction verbatim in its original file, including text that looks like an author note. Append verified guidance rather than deleting original text.
```

### Author notes and factual claims — before

```text
Remove author notes and placeholders.
```

### Author notes and factual claims — after

```text
Remove only author notes and placeholders copied from templates; this never authorizes deleting original project text. Verify claimed counts and behavior against the inspected code and check.py. Distinguish proposed scope from observed requirements.
```

[instruction-changes.json](instruction-changes.json) preserves the complete common-rule block before and after, not just these changed spans. [prompts.json](prompts.json) contains every exact task prompt and actual owner document. No later instruction change or inference followed the cutoff.

## Evidence and disposition

- [Frozen timed plan](../../superpowers/plans/2026-11-10-templates-luna-haiku.md)
- [Original eight-criterion contract](../../superpowers/plans/2026-11-10-templates-go-qualification.md)
- [Frozen source/input/prompt/operator bindings, routes and deadline](manifest.json)
- [Every prompt and owner document](prompts.json)
- [Per-criterion grades, unavailable cells, usage and exact layout reads](results.json)
- [Complete synthetic outputs, assistant prose and tool paths](outputs.json)
- [Complete before/after common instructions](instruction-changes.json)
- [Focused checks and cleanup receipt](verification.json)

**Task state: REVIEW; evidence publication only.** Keep PR #236 `[BLOCKED]`, draft and unmerged. The templates remain unqualified for this aggregate. The report still needs independent review; no additional reviewer/grader inference budget exists. The frozen plan remains unchanged as pre-execution evidence, and this report records its observed outcome. Existing thread naming is preserved because eligible creation/family metadata is unavailable.

No full suite, release, real onboarding, reapplication/undo, native-client startup, statistical replication or provider-billing audit ran. Any further behavioral comparison needs a separately authorized budget. Do not restart the prepared Luna clarification script or replay failures to green.
