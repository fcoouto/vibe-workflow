# Templates across the workflow

- **Status:** draft, 9 November 2026. Independently reviewed (Astra/xhigh, one round: no blocking finding, priority A → B → C agreed; its three corrections and one optional point are applied). Approved by the user on 10 November 2026 and implemented for A, B and C in the same PR.
- **Goal:** decide which recurring artifacts gain from a template in the root `templates/` folder, what that gains, and in what order to do it.
- **Starting point:** `templates/worker-brief.md` and `templates/task-record.md` (PR #179). Their owners, `policies/03-agent-delegation.md` and `policies/07-context-and-handoff.md`, now keep the rules and link to the templates for the fields.

## Principles

1. **Owners keep rules; templates hold fields.** A policy or skill still decides when an artifact is needed, what is required and what proves it. The template only shows the fields to fill. Each field is defined once.
2. **Reached by a link, never by browsing.** An owner links its template where it is used. No agent reads the folder or an index as a routine step.
3. **No new obligations.** A template adds no required file, schema, validator or gate. Short prose that carries the same content remains acceptable.
4. **One format.** Each template starts with one line naming its purpose and owner, uses `[brackets]` for placeholders and says "omit lines that don't apply; never invent values".

This supersedes one point of the [workflow template design](2026-11-05-workflow-template-design.md#four-kinds-of-reusable-material): artifact templates live in `templates/`, not beside their owner. Its concern, that agents must not load a catalog, stays in principle 2. Worked machine requests, behavioral fixtures and future schemas keep the placement that design gave them.

## How the candidates were evaluated

- **Frequency:** how often an agent produces the artifact.
- **Duplication:** how many places describe or hand-compose its fields today.
- **Simpler instructions:** words that can leave the owner file, counting field lists only. Rules stay, so these are estimates.
- **Clarity:** whether the artifact's whole shape is visible in one place.
- **Cost and risk:** files touched, requirements that could be lost, licensing, onboarding risk.
- **Harness:** whether an existing evaluation case exercises the artifact.

Reference point from #179: the two policies lost about 110 words and the two templates added about 420. The templates load only when an agent writes a brief or a record. Expect the same pattern: the main gain is consistency and less composition, not fewer words loaded on a task that uses the template.

## Candidates

| Candidate | Fields today | Frequency | Duplication | Words leaving owners (est.) | Clarity gain | Cost and risk | Harness case | Value |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **A. Review report**: one template for code, security and project reviews | `06:30` finding fields; `04:49` report and finding fields; `review-my-project/references/procedure.md:40` | Every merge (code review); every completed change needs a security impact assessment, with a report when warranted; project review on request | Three owners describe overlapping finding fields differently; every reviewer brief restates the report format (60–80 words in each of the two reviewer briefs written while preparing PRs #178 and #179) | About 80 | High: one finding format across three review kinds | Low to medium: common finding fields once, with explicitly scoped additions. Confidence applies to security and project reviews, effort to project reviews; every security field and classification stays. Analysis rules, redaction and output destinations remain with their owners. | Read-only review exercises the report's route and finding, not its fields | **High** |
| **B. Pull request**: body with a readiness section, used only when creating or updating an authorized PR | No layout today; readiness fields in `08:14` | Every task in projects that publish through PRs (this repository does; onboarding's default keeps changes local) | Hand-composed each time (the bodies of #178 and #179 and their three readiness comments) | About 20 | Medium: same structure for draft and `[READY]` | Low: linked from the existing publication path and the readiness owner; local-only preferences and a project's own PR layout are preserved | None; inspect published PRs | **Medium-high** in projects that publish PRs |
| **C. Setup record** | Scattered in `onboard-my-project/references/procedure.md:34, 51, 111, 177`; `docs/setup.md` is one result | Each onboarding, update or maintenance entry | The record's shape is only visible by reading four places | 0–20 | Medium: whole record visible in one place | Medium: touches onboarding; must preserve the evidence-layer distinctions at `procedure.md:111` | None directly: the retained project case checks application code, not the record | **Medium** |
| D. Project documents (nine responsibilities) | `procedure.md:24-34` table; pinned Toolkit templates read at setup | Each onboarding | Toolkit copy and local table | About 0 (the mapping table stays) | Medium: Toolkit parity, setup without the Toolkit source | High: large content, MIT notice, drift from Toolkit updates, onboarding risk | Retained project case | Low now |
| E. Reviewed configuration summary | `reviewed-configuration.md:9-15` | Each onboarding proposal | None: already one dedicated file | 0 (moved, not removed) | Low | Low | None | Low |
| F. Upgrade status and maintenance evaluation record | `prepare-my-workspace/references/upgrade.md:34-38`; `09:14` | Rare | None | 0–30 | Low | Low | None | Low |

A reviewer brief needs no template of its own: it is a worker brief whose report line points to the review report template.

## Harness improvement

**Effect on the agent harness.** Templates improve it in two ways.
- **Smaller owners:** the policies agents load on every trigger lose their field lists.
- **Less composition:** briefs and reports reuse a fixed shape instead of restating it, as today's reviewer briefs did.

They do not reduce what a task that uses a template loads, because it adds one file read. No token or reliability improvement has been measured, and none is claimed.

**The Workflow Maintenance improvement method** (`policies/09-workflow-maintenance.md`, "One bounded improvement") needs an observed failure. None is observed for these artifacts: today's reviews were complete because each brief spelled out the format. So no improvement trial is proposed now. Instead:

- **Acceptance cases.** Reuse the existing harness cases, with no new evaluator, and inspect what they produce:
  - The read-only review case checks the finding, its reproduction and the unchanged fixture. For the template, also inspect that the report was written from it, with the applicable fields, to the private destination.
  - The stale handoff case does the same for the task record and worker brief.
  - Both need a fresh Codex or Claude Code worker. An OpenCode-hosted worker does not count, so these stay "not run" until such a session is available.
  - Paths no case covers get a static walkthrough: code, security and project review; authorized PR and local-only publication; setup record and reapplication.
- **Trigger for a trial.** If a real brief or review omits a required field, such as the checkout, severity or evidence, run one bounded comparison on the matching case: prose baseline against template candidate.

## Priority

Ranked by aggregated value (frequency × duplication × clarity, against cost):

1. **A. Review report.** Highest frequency, three owners with overlapping fields, and an existing harness case.
2. **B. Pull request.** Every task in projects that publish PRs, hand-composed today; low risk. Its value depends on PR publication.
3. **C. Setup record.** Low frequency but clearer, and it touches onboarding lightly.

Deferred at the A–C decision:
- **D.** Subsequently authorized by the user on 10 November 2026. The [implementation plan](../plans/2026-11-10-project-document-templates.md) adds local project-document shapes, subfolder reader support, the Toolkit license and source coverage. Setup/update still resolves current Toolkit guidance; ordinary tasks need no upstream source. Existing canonical documents, stack and selected clients remain authoritative.
- **E and F.** Little to gain. Move them only if their owner is being rewritten anyway.

## Folder structure

After A–C the five existing files stay flat: `worker-brief.md`, `task-record.md`, `review-report.md`, `pull-request.md`, `setup-record.md`. D adds the project-document family under `templates/project/`; it reuses the setup record instead of adding a second setup shape.

Add subfolders and a `templates/README.md` index for authors when the folder passes about eight files, or when a family reaches three files (D would). The prepared instruction reader serves only top-level `templates/*.md` today. Subfolders need both its source discovery and its allowed-path rule extended, as for `skills/**`, the map regenerated and the nested-path test updated.

## Delivery

One worktree, one branch and one PR for A, B and C:

- **Templates:** add the three.
- **Owners:** remove the moved field lists from `04`, `06`, `08` and the review and onboarding procedures, and link the templates where they are used. The PR template is linked from the publication path and the readiness owner.
- **Coverage:** update the 5 November design's placement line and source-to-contract coverage; regenerate the instruction map.

Acceptance:

1. **Requirement preservation.** Every removed field appears in a template, and every rule stays in its owner. The PR shows this as a table for review.
2. **Links.** Every owner links its template at the point of use, and all links resolve.
3. **Size.** Words removed from owners and added in templates are reported.
4. **Checks.** `validate.py`, tests selected under the [development guide](../../development.md#checks) and independent review all pass.
5. **Harness cases.** Read-only review with the new template runs in a native Codex or Claude Code session, with its report inspected as above, or is recorded as not run. The static walkthroughs listed under "Harness improvement" are recorded with their results.

## Out of scope

- JSON schemas or new validators.
- Moving worked machine requests or `assets/harness-evaluation/`.
- Format rules enforced by code (titles, role declarations).
- Routing and OpenRouter procedures.
- Candidates E and F (D was subsequently authorized as recorded above).
