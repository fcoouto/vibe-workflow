# Shared external delegation design

Status: implemented and independently reviewed, 2026-11-05. The owner authorized native Astra/high to reconcile source and implement both routes, preserving local-model priority and native review. Subsequent steering requires simple implementation without extra hardening controls. This does not authorize live inference, new source transmission, configuration activation, billing, installation, releases, commits or publication.

## Intent and boundaries

Automatically consider suitable repository coding work for approved OpenAI API and OpenRouter routes while preserving the approved local-model preference, native coordinator and independent native acceptance. Success means useful assignments reach an authorized route without repeated permission prompts, and unsuitable assignments continue natively with a concise reason.

Preserve coordinator model, reasoning, provider, authentication, subscriptions, customizations and existing worker-selection rules. Do not introduce a universal executor, OpenAI filesystem bridge, autonomous external repository writes, provider auction or new billing mechanism. Source permission, automatic selection, execution capability and acceptance are separate decisions.

## Evidence and source baseline

Current behavior was inspected in trusted installed package 0.1.29. The decisive package-relative references are `policies/03-agent-delegation.md`; `skills/manage-usage-routing/references/{delegation,codex-host}.md`; `runtime/usage_codex_host.py` (standing-consent checks around lines 198 and 266); and `skills/openrouter-{proposals,workers}/references/procedure.md`. These are provenance identifiers, not links or routine dependencies on another checkout. Official funding, billing and installed client execution were not refreshed during this design.

Historical design-time baseline: the authoring checkout is `codex/project-repository-adjustments` at `a771516e0a44d4a98f9844573da0771cd0f0d03e`. It lacks the Usage Routing host and skill. Before implementation, reconcile an appropriate source baseline containing the inspected capabilities and check intervening changes. Do not reconstruct runtime code from this document or edit the installed package. Preserve existing untracked `.codex/` and `.vibe-coding-workflow/` state.

Implementation baseline reconciled from fetched `origin/main` at
`c4a4eb856b7c491d706148acb760bad066f4489c`, in the isolated
`codex/shared-external-delegation` checkout. Current source contains Usage Routing
host/admission and OpenRouter scripts with `runtime/openrouter_settings.py`;
implementation uses this source directly, not installed-cache reconstruction.
See the [implementation record](../plans/2026-11-05-shared-external-delegation.md).

At design time OpenAI Smart standing consent excludes repository source; exact task approval already supports broader data. Both proposal routes exchange bounded text without local tools. OpenRouter workers investigate explicitly copied files through pinned OpenCode with read-only application permissions, not an OS sandbox. Their existing qualification restrictions remain binding.

## Alternatives and recommendation

Keeping exact approval for every source-bearing task minimizes changes but prevents reusable automatic coding delegation. Building a common tool-capable executor expands permissions and duplicates working integrations. Recommend one shared selection and consent contract with small changes in existing adapters. Extend proposal admission first; retain the separately approved OpenRouter investigation capability without pretending that proposal qualification proves tool-loop safety.

## Selection and configuration precedence

Consider routes when an assignment becomes a useful delegation candidate, before native implementation dispatch. Direct native work remains appropriate when packaging, supervision, repair and review would outweigh the benefit. Consideration performs no inference request, transmission, activation or paid discovery.

Apply configuration in this order:

1. Binding client/organization restrictions and current revocations constrain every choice. Saved disable blocks new managed admissions; a task-level route request does not silently reactivate it.
2. The latest applicable explicit user choice controls provider, model, effort and fallback within its actual authorization. A provider selection alone grants no additional data permission. Specific choices override broader preferences only within their scope.
3. Otherwise, assess the approved local profile using its exact assessed language, complexity, context, budgets, capacity and verification requirements. A task entering that preference follows the local plugin's bounded waiting and native-fallback procedure. Congestion, failure, suspension or unavailable verification cannot be reclassified as external eligibility to bypass that procedure.
4. For remaining candidates, consider only enabled external modes with applicable automatic-selection permission, data permission and compatible capabilities. Preserve current OpenAI Smart authority within its scope. Existing OpenRouter connection, source permission or route enablement does not by itself grant new automatic-selection authority.
5. Choose among eligible routes using an explicit saved preference and comparable verified outcomes, including total native effort. If several qualify and no selection policy resolves them, continue natively; do not silently switch providers. Native fallback remains subject to explicit API-only choices and reviewer readiness.

Proposed initial candidates are isolated fixes and tests with complete bounded context and strong independent checks. Broad architecture, security-sensitive decisions and weakly verifiable work remain native. Cached capability evidence guides selection; popularity and benchmark scores do not establish suitability.

## Reusable permission and compatibility

Reuse valid existing provider, source and task approvals exactly as granted. The source-free default applies only where source permission is absent; it must neither broaden OpenAI consent nor erase OpenRouter permissions. Existing approval evidence can remain authoritative without forcing users to repeat it in a new schema.

A reusable authorization must resolve checkout scope, task classes, permitted source areas and exclusions, service and downstream endpoints, models/effort, execution mode, automatic-selection authority, funding/spending choices and validity/revocation conditions. Store new information through existing management settings only where genuinely missing. A task binds the relevant approval reference and revision; missing or ambiguous authority blocks that route, not unrelated native work.

OpenAI permission does not transfer to OpenRouter. OpenRouter permission must cover the selected downstream provider; retain endpoint pinning and disabled provider fallback. A copied-file worker needs its tool mode covered separately from a text proposal.

Preserve accepted ceilings, no-ceiling choices, expiry and funding settings wherever supported. This design introduces no mandatory dollar cap or fixed expiry. Missing values cannot be interpreted as unlimited authority. Recommend retaining existing approved values and asking only for genuinely absent decisions before live use. Ordinary source edits within an approved area refresh task evidence without reopening permission; expanding destination, source, capability or spending authority requires a scoped decision.

## Payload provenance and admission

Native preparation selects the minimum necessary material. Represent repository-derived payload parts with canonical source identity, excerpt boundaries where applicable, digest and approval reference. Construct transmitted snippets from the checked snapshot rather than accepting unrelated text beside a reassuring manifest. Resolve paths and exclusions before reading into the transmission packet; reject escapes and unsupported links.

Free-text objectives, summaries, diagnostics and tool results can contain project content too. Classify their provenance and apply the same data scope. Derived summaries inherit their source restrictions. Unclassified project-derived text cannot enter automatic transmission; use native execution or obtain the missing scoped authorization. Inspect permitted content for secrets and excluded data. Hashes prove byte identity, not permission or absence of secrets; scanning is supplementary, not proof of complete classification. The trusted native coordinator remains responsible for truthful provenance, including prose it authors.

For tool-free proposals, validate the assembled packet before sending. For OpenCode, validate and copy only approved files, restrict reads to those copies and retain qualified runtime permissions for resulting tool output. Local reads still send information to the remote model. Managed admission does not claim to intercept arbitrary direct connector calls outside these routes.

Bind task, payload, consent revision, selected route and applicable admission evidence. Recheck them at the adapter's dispatch boundary. Changed source before dispatch requires a rebuilt packet; revoked or changed authority invalidates admission. Disable ordered before the existing final grant/launch blocks dispatch. After that boundary, disable cannot guarantee cancellation or prevent billing; retain in-flight evidence. Re-enable does not revive stale preparation.

## Execution, costs and acceptance

Confirm native integration and an independent reviewer before dispatch. Keep route-specific mechanics:

| Route | Execution and recovery |
| --- | --- |
| OpenAI proposal | Preserve count/generation attempts, funding and pricing freshness, duplicate protection and conservative unknown-cost accounting. No automatic inference retry or provider switch. |
| OpenRouter proposal | One admitted MCP inference with pinned endpoint and supported effort. Preserve provider spending controls, output/time limits and generation reconciliation. No credit preflight requirement or outer inference retry. |
| OpenRouter worker | Preserve pinned OpenCode, copied-file permissions, native supervisor, step/deadline limits and OpenCode-owned retries. No wrapper retry or provider switch. Confirm local execution stopped before native takeover. |

Validate each route's accepted funding/spending choice without inventing stronger guarantees. Worker steps and timeouts are not money caps; requests already in flight may be billed. Preserve unknown costs and outstanding reservations. OpenCode event costs are observations, not reconciled provider bills; account deltas have concurrent-use attribution limits. A timeout proves neither cancellation nor zero cost. Native continuation follows the existing route-specific ownership rules and original budgets.

External results remain untrusted proposals or findings. Native integration checks paths, scope and current source, then applies changes and runs relevant checks. Source changes after dispatch require fresh applicability assessment; never blindly apply a stale patch. A distinct native reviewer accepts the exact candidate independently of the producer and integrator. Preserve review role and existing model/provider selection. Self-reports, receipts and display completion cannot substitute for observed checks and independent review.

Use existing task/attempt records and route visibility. Show the selection reason, actual route, requested versus observed model/provider/effort, review state and known or unknown usage. Keep prompts, source and credentials out of telemetry. Savings require comparable task evidence including native preparation, supervision, repair and review.

## Delivery, recovery and observable checks

1. Reconcile the source baseline and map existing authorities. Define the shared rule once, link it from relevant delegation triggers and maintain source-to-contract coverage. Keep skill entry points within 150 words and load only applicable route details.
2. Add reusable scope binding and packet provenance to existing settings/admission paths. Preserve legacy approvals and recorded attempts; never silently migrate disabled routes into automatic use.
3. Extend bounded proposal selection across both providers. Retain worker-specific permission, qualification, retry and accounting controls; add shared scope checks without replacing its executor.
4. Verify offline behavior, then stage independently authorized live qualification per route. Roll back by disabling new automatic consideration while preserving manual authorization, history, charges and in-flight reconciliation. Do not rewrite account settings or discard uncertain attempts.

Focused acceptance scenarios must demonstrate:

- Local-eligible work waits/falls back through the local plugin, including congestion and failure; no external admission occurs.
- Valid OpenRouter source approval is reused; source-free OpenAI consent rejects the same source-bearing packet. Neither prompts again within valid scope.
- Unapproved endpoint, excluded snippet, derived summary or tool output is rejected even with a valid hash. Unknown provenance does not pass as source-free.
- Ordinary approved-area edits refresh snapshots; stale packets and revoked revisions fail admission. Post-grant disable retains truthful in-flight accounting.
- Explicit no-ceiling settings survive; absent funding authority, timeout and missing billing evidence never become unlimited permission or zero cost.
- Duplicate attempts do not resend; stale results require native reassessment; acceptance requires relevant checks and independent native review.

Plan `python3 -B scripts/validate.py` plus focused behavioral checks and instruction-loading walkthroughs. Package validation, fixtures, observed native dispatch, actual provider execution and OpenCode permission qualification are distinct evidence classes. Live checks require their own applicable authorization.

## Decisions left open

Implementation approval and source reconciliation are complete. Preserve legacy compatibility discovered in the selected source baseline. The builder may choose field placement and helper reuse without expanding authority.

Before future live use: resolve only missing automatic-selection permission, source/exclusion scope, destinations/models/effort, route preference and accepted funding/validity choices. Existing valid decisions remain reusable. No values are selected by this draft, and no savings claim is established.

## Evaluation

A fresh native Astra/high builder review examined the complete draft at SHA-256 `a6e57d17c9867e7164236e30a3e783f3a07293e5ac44187161a4a0b12e101aef` on 2026-11-05 and reported no material unanswered questions for implementation planning. The review covered authorization, local preference, routing, provenance, accounting and independent review. That design-stage assessment preceded source reconciliation and implementation. Missing live settings remain separate decisions.

At design-stage evaluation, `python3 -B scripts/validate.py` passed in the original authoring checkout; only this design document had changed, and client execution was not checked.

Implementation and independent code/security review are now complete on the reconciled baseline. All review findings were corrected through focused regressions and scoped re-review. See the [implementation plan](../plans/2026-11-05-shared-external-delegation.md) for verification evidence and remaining live-execution limits. No live provider request, route activation, source-permission expansion or installation was performed.
