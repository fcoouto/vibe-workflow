# D11: Workflow dashboards in Langfuse

Planning only · 26 October 2026; direct Langfuse dashboards approved 28 October 2026 · **Last deliverable**. This document delivers no integration, dashboard, collection, cleanup or client qualification.

**Goal:** show the selected onboarded Projects' workflow activity, supported health observations, outcomes, costs and measured benefits directly in Langfuse. Reuse its dashboards, widgets, filters and trace drilldown. The requested experience remains **“Clear, Straight to the point and a very good looking dashboard”**, within supported Langfuse capabilities. The user explicitly removed custom HTML, offline reporting and report snapshots to reduce maintenance.

**Basis:** the [canonical sequence](onboard-my-project-plan.md#5-small-deliverables), [Project/Repository model](onboard-my-project-plan.md#project-and-repository-model), [shared retention](onboard-my-project-plan.md#evidence-retention) and [D9 optional Langfuse integration](onboard-my-project-plan.md#optional-langfuse-integration). These owners define membership, lifecycle, enable/disable behavior, collection, correlation, export permissions and necessary calculations. D11 configures and qualifies their presentation; it creates no collector, registry, measurement ledger, pricing engine or custom reporting application. Follow the [instruction authoring standard](../AGENTS.md#instruction-authoring). Preserve coordinator settings, permissions, customizations and task-based worker selection.

## Reuse existing evidence and dashboard capabilities

**Canonical owner:** dashboard configuration/qualification owner, with independent source reconciliation and view review. D9 supplies qualified observations and verified outcomes from the existing owners:

| Source owner | Facts available through D9 for supported views |
| --- | --- |
| D1–D4 configuration, doctor, enrollment and recovery | Registered scope, accepted configuration, selected clients, adoption, observed loading/health, pending activation and recovery dependencies. |
| D5 checks and reviews | Candidate-bound results, review coverage, findings, verification and freshness; local completion remains distinct from enforcement. |
| D6 worker control | Task/worker/attempt identity, observed lifecycle/settings, ownership, failures, retries, stop reconciliation and uncertainty. |
| D7 repository gate | Repository/candidate/revision-bound verification and enforcement observations, including unqualified paths. |
| D8 client adapter | Client/surface/version capability and execution observations without inherited qualification. |
| D9 behavior, usage and pilot | Scoped behavioral qualification, reconciled source measurements, comparison inputs, corrections, coverage, pilot outcomes and source IDs. |

Reuse curated dashboards first. Add only a small template of supported widgets where needed for workflow outcomes and coverage. Select a supported configuration/import mechanism for the target Langfuse version; do not depend on unqualified dashboard APIs or promise identical behavior across Cloud and self-hosted versions. Preserve user-owned dashboards and edits; an update must not silently overwrite their configuration. Opening a dashboard does not authorize provisioning or updating it.

Map existing identities and outcomes into supported metadata, tags or scores without inventing new evidence. Prefer Langfuse's aggregations and direct trace links. D9 alone owns necessary workflow-specific calculations and reconciliation. Do not rebuild token accounting, pricing, percentiles, charts or arbitrary queries outside Langfuse. A view that cannot preserve the required meaning is unavailable with a stated limitation, not a reason to build a fallback UI or label a partial result complete.

Viewing and filtering dashboards may query Langfuse. They must not rerun lint, tests, reviews, native qualification, source collection or model evaluations. Add no plugin background analyzer, automatic repair, scheduled agent, service or GitHub Actions job. Dashboard observations may identify a gap and its owner/next action; performing that work retains its existing authority.

## Scope, identity and filters

Use canonical membership for accessible, selected, onboarded Projects with observability enabled. D11 neither scans the filesystem nor enrolls/adopts repositories. Umbrella discovery and activation claims require the [existing owner follow-up](onboard-my-project-plan.md#project-and-repository-model); startup remains read-only. Keep configuration, activity, hook-owner and execution qualification scoped to each repository/client.

D9's [selective workflow collection](onboard-my-project-plan.md#selective-workflow-collection) applies authorized Project/Repository scope before ingestion. D11's filters select displayed observations; they do not reduce ingested units, change collection scope or activate telemetry. Reuse Langfuse's built-in usage dashboard instead of adding a plugin unit counter or quota service.

A workflow Project is not automatically a Langfuse project. Qualify the mapping between workflow Project/Repository identities and the selected Langfuse destination. Within an authorized shared destination, provide supported Project and Repository filters and an All Projects view only for its proven selected coverage. Do not merge destinations or weaken access boundaries to create that view. Separate Langfuse projects/accounts may require separate dashboards and links; no cross-destination aggregation service is part of D11.

Reuse D9's stable checkout/task/worker/attempt/source identities and the [linked-task model](onboard-my-project-plan.md#cross-repository-tasks-and-delivery). Logical requests and repository tasks remain distinct; shared workers, clones/worktrees, replayed or corrected receipts, inclusive totals and cache/reasoning subsets must not be counted twice. Unknown/shared attribution stays explicit. Aggregate only compatible units, periods, definitions and proven-disjoint components; do not invent repository splits or move historical attribution silently.

Qualify each widget's response to dashboard filters, including widget-specific overrides and comparison baselines. Every presented total, chart, table and drilldown must match its labeled scope. Remove or clearly separate unsupported widgets rather than allowing a wider total to masquerade as the selected Project. Filtered comparisons require a comparable baseline for the same selection.

Inaccessible, excluded, pending, disabled or unsupported scope is a coverage gap, not zero activity. Read scope/status only for disabled members; do not collect their optional observations or reuse required local execution evidence for analytics. Never claim All Projects completeness when unobserved scope is missing. If the dashboard cannot express the coverage limitation safely, withhold that completeness/comparison view and explain the limit through existing operational status.

Label views as observations of selected workflow tasks, with any sampling and source limitations explicit. Untracked standalone Q&A is outside that scope; workflow totals are not account-wide usage. Omitted low-value step observations do not invalidate a qualified inclusive source total, but absent contributing usage prevents completeness claims. Detailed qualification/investigation views remain metrics-only and use D9's source rules.

## Honest status and benefits

Use a concise overview with readable labels, units, explicit status and native drilldown. Verify keyboard access and usable empty/error states on the supported Langfuse surface; document platform limitations without creating another renderer. Charts require supported comparable values, not reassuring placeholders.

Include source observation time/freshness, reporting period, workflow revision and client/surface where relevant. Distinguish configured from active, observed from qualified, and stale/unavailable/unverified from passing. Keep policy adoption, applicable actions and satisfied actions separate. Package validation, native execution and repository enforcement remain independently labeled; scores or dashboards never become permission or readiness authorities.

Benefits require comparable baseline/candidate observations, acceptance/quality outcomes, sample counts and coverage. Include all attributable observed overhead: failures, retries, coordination, review, verification, integration and observability. Show regressions and additional cost as well as gains. Unobserved overhead limits the comparison; activity counts and shorter instructions do not establish savings, and observed comparison does not establish causality.

Where baseline or attribution is inadequate, the workflow's status must say **“Benefit not yet measured”**. Use a supported dashboard label/status if available; otherwise show that explanation through existing operational status and omit the unsupported benefit widget. Do not invent an efficiency score or build custom rendering to reproduce the text. D9 distinguishes source charges from price-derived estimates and subscription usage from avoided cash payments.

## Optional operation, privacy and retention

Follow D9's single enable/disable contract. Disabled means no optional measurements, analytics-only traces, dashboard provisioning/updates or diagnostic reports. Required local check, review, qualification, ownership, recovery and merge evidence remains intact, with necessary operational status/doctor available. Do not load Langfuse procedures for unrelated disabled tasks.

Use the separate provider-neutral [Manage Observability Integration skill](../skills/manage-observability/SKILL.md) for requested integration setup, status, scope or enable/disable operations, outside the onboarding flow. Its supported checkout connection controls and fixed metrics-only synthetic probe do not start native collection, establish canonical Project-wide consent or qualify a dashboard view. Neither invoking the skill nor opening a dashboard establishes D11 acceptance. Langfuse remains the first connection provider and the D11 dashboard target; future providers require their own reviewed capabilities.

Disabling stops future plugin activity; it does not delete remote history or make an existing user-accessible dashboard disappear. Historical views must not be represented by the plugin as current observations. D9 reconciles separately configured Broadcast forwarding. Outages, quotas, missing credentials or expired history leave analytics unavailable/incomplete without changing local safety decisions. There is no offline report or substitute analytics system.

D9 owns minimal authorized exports, secret handling and the [metrics-only remote retention exception](onboard-my-project-plan.md#metrics-only-remote-retention). Eligible metrics may use Langfuse Cloud Hobby with disclosed destination retention, access windows and quotas, without plugin deletion logic. Keep untrusted values as data; validate destination links and never put credentials, prompts, source code or review text into dashboard definitions. Dashboard access and sharing must respect the user's selected destination and permissions. Verify the actual metadata/score payload and access behavior rather than assuming privacy mode or a free tier makes content safe to export.

Existing D2/D5/D6 lifecycle owners retain safe closure, seven-day detailed/four-month compact expiry, the shared 100 MiB Project allowance, protected dependencies and retirement under the authority-wide 4096 inspection bound. D11 does not reimplement cleanup or move proof into Langfuse to evade those requirements. Required local evidence survives remote expiry or outage. Once source support expires, affected totals/details cannot establish fresh validation; label gaps and avoid unsupported drilldown. Eligible remote metrics follow D9's exception and may outlive local receipts; other managed exports remain subject to shared retention. User-controlled downloads have disclosed retention limits, not a promise of remote revocation. No full transcript mirror or new summarizer is added.

## Delivery owners and acceptance

D11 remains last, after the preceding deliverables' accepted slices. Membership, retention and telemetry gaps limit affected claims; universal client success is not required. No D6 routing/control or D7 enforcement work is reopened by this reporting change.

| Subdeliverable / owner | Bounded output | Completion evidence |
| --- | --- | --- |
| D11a — mapping / dashboard owner with source-owner review | Map D9 observations and canonical identities to supported fields, destination scope, coverage and comparison semantics. | Source receipts reconcile for standalone/umbrella and two-Project cases, shared workers, replay/corrections, unknown attribution and expired support. Separate destinations do not imply a combined view. |
| D11b — configuration / dashboard owner | Reuse curated dashboards or a small supported template, native filters and direct links; preserve user customizations. | Observed target-version import/configuration, consistent widget filters, readable accessible statuses and empty/error handling; unsupported views remain explicit. No custom HTML, report snapshots or offline output. |
| D11c — qualification / independent evidence and view reviewer | Exercise the cases below without rerunning project checks just to view analytics. | Numerical reconciliation and observed scope/filter/privacy/disable behavior. Package checks, native client execution and repository enforcement remain separately reported. |

Representative acceptance cases:

1. An enabled standalone Project and an enabled umbrella use canonical membership and correct repository/client observations. Excluded paths and sibling success do not confer membership or qualification. Two Projects in one authorized Langfuse destination have consistent filters; separate destinations retain their access boundaries and separate views.
2. A linked cross-repository task, shared worker, inclusive parent totals, replay, cumulative updates and corrections count once. Every widget and comparison follows the selected scope, including environment overrides. Reduced-detail collection still reconciles to qualified source totals; sampled or missing usage cannot establish exact totals. Unknown attribution or absent comparable baselines suppress unsupported gains with the stated benefit status.
3. Never-configured and disabled Projects collect no optional telemetry and receive no plugin dashboard updates/reports. Enabled collection excludes unrelated standalone Q&A and unauthorized Project/Repository scope before ingestion, including qualified Broadcast paths; changing a dashboard filter does not change those exports. Required local evidence and operational status still work. Disabling after collection preserves safety proof, reconciles forwarding, and makes no claim that historical dashboards or downloads vanished.
4. Outages, quota exhaustion, missing credentials, sampled/delayed data, unsupported metadata and expired history yield explicit gaps without rerouting native work, bypassing gates or creating offline fallback reports. Opening/filtering a dashboard performs backend reads only, not project checks, inference or recollection.
5. Source-owner retention tests demonstrate safe closure, the Project-wide cap, protected companion proof and bounded retirement beyond 4096 settled records. Dashboard claims respect those outcomes; deleted source detail is not fresh evidence and remote loss cannot remove required local proof. Eligible metrics-only exports can use the free tier without matching local expiry or adding plugin deletion logic; actual access gaps and remote persistence are disclosed, and prohibited content remains unexported.
6. Dashboard definitions contain no secrets or prohibited content; untrusted metadata/links remain inert and access is scoped correctly. Supported filters, drilldown, keyboard use and empty/error states are observed on the target version. User-customized dashboards survive template updates.

These are future implementation and qualification criteria. This plan update establishes no measured benefit, live membership, retention enforcement, dashboard compatibility or client qualification.
