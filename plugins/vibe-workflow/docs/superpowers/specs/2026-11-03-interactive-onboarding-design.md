# Guided onboarding preferences

Status: question wording frozen for a first onboarding dialogue pilot at the
user's request on 3 November 2026. The three retained topics are publishing work,
merging pull requests and Workstreams. The seven rejected questions and empty
advanced step remain removed. Workstreams activation scope is still deferred.
This design changes no runtime behavior or existing settings.

## Purpose and approach

Help users understand and choose how agents will work in their project. Each
question explains the benefit, the effect of each choice and where to learn more.
Follow the [native single-select procedure](../../../skills/onboard-my-project/references/procedure.md#basic-onboarding). Preserve submitted choices across interruptions.

Considered approaches:

- **Short guided setup, selected:** retain publishing work, merging pull requests
  and the Workstreams topic for reassessment. Do not add questions for settled
  workflow behavior.
- Full walkthrough: explains every applicable preference, but adds more decisions
  before useful setup can proceed.
- Defaults summary: quickest, but gives users less help understanding each choice.

Reuse existing decision records, project instructions and feature controls. Do
not add a separate questionnaire service or preference database. Complete Toolkit
catalog assessment, baseline discovery and applicable Context7 checks remain.

## Flow

1. Discover the selected project, repositories, primary branches, client support
   and existing preferences. Do not infer the primary branch from the checked-out
   branch alone. Reuse explicit existing choices and show them in the summary.
2. Ask the core questions only for missing choices. For existing choices, offer
   an explicit change path without requiring the user to answer them again.
3. Resolve actual existing-workflow conflicts through the established scope
   policy when necessary. There is currently no advanced-preferences menu.
4. Show a compact summary of chosen settings, their scope and any unavailable
   capability. Continue already-authorized setup; the summary adds no universal
   approval step.
5. Report what was saved, what became active, what remains pending and how to
   change the choices later. Retain recovery information.

Use `(default)` for the initial selection when no saved choice exists and
`(recommended)` for the suggested choice. These may identify different answers.
A recommendation never changes the selection automatically; neither label is
submitted consent.
An unanswered question never authorizes merging, publication or enabling
a feature. Continue independent authorized setup while a consequential answer is
pending. Noninteractive runs return the question and affected work; hooks never
prompt users. Answer wording and labels below are frozen for the pilot;
existing explicit choices and permissions remain authoritative.

## Core question: publishing work for review

Explanation: agents can turn completed local work into a draft pull request,
making the changes and verification results available for review without another
publication instruction.

> How should agents publish completed task changes in `<repository_name>`?
>
> - **Keep changes local. Ask me before committing or publishing.** (default)
> - **Commit the task changes, push a task branch, and open a draft PR.** (recommended)

Show the selected repository and remote. The publication choice covers only
task-owned changes and the named remote; it does not authorize force-pushing,
direct writes to the primary branch, merging or deployment. Preserve existing
commit/publication rules. If only some effects are already authorized, retain
them and ask only about the missing effects. This question applies only where
the selected host supports pull requests. Provide a help link explaining the
steps from local changes to a reviewable draft PR.

## Core question: merging pull requests

Explanation: agents can prepare and independently review changes while you keep
the final merge decision. Optional merge permission lets them complete eligible
pull requests without waiting for a separate merge instruction each time.

> May agents merge pull requests into `<project_name>`'s primary branch,
> `<primary_branch_name>`?
>
> - **No. I will review and merge them myself.** (default)
> - **Yes. Merge independently reviewed PRs marked `[READY]` when all required checks pass.**

Link to a concise explanation of merge readiness and the scope of this permission.
Use discovered repository and branch names. For a multi-repository project,
show every affected repository/target and preserve differing existing choices.
Unknown or conflicting targets require resolution before recording merge consent.

Record explicit opt-in for those repositories and targets. Existing branch
protections, current candidate-bound review, security coverage and merge-readiness
requirements remain binding. Readiness never grants additional commit, push,
deployment, bypass or branch-protection configuration authority. Unsupported
verification or enforcement leaves automatic merging unavailable, even when the
user has recorded their preference.

`[READY]` is a PR-facing indicator that the current candidate is ready to merge.
Derive it from successful merge verification, and clear or recompute it when the
candidate, target, checks or review evidence change. It summarizes readiness;
it is neither merge proof nor merge permission. Keep it distinct from the runtime
task state `READY`, which means ready to start work. The preference question
does not itself merge any current pull request.

## Core question: Workstreams — activation deferred

> Enable Workstreams for `<project_name>`?
>
> - **Keep it off for now.** (default)
> - **Yes. Enable Workstreams for this project.** (recommended)

The benefit explanation and activation scope need reassessment. The earlier draft
mapped this question to resource admission and capacity recovery. The user has
removed those choices from onboarding and treats them as baseline behavior. Do
not use this question to disable either control or change their runtime defaults.
The earlier proposed default-off migration for those controls is withdrawn.

Retain the Workstreams topic and “Learn about Workstreams” help-link requirement
for pilot feedback. Its distinct optional effect must be established
before implementation; no replacement feature or master toggle is assumed here.

## Settled behavior, outside the questionnaire

- Handle review findings under the existing implementation and review workflow;
  do not introduce a new approval preference.
- Parallel implementation remains available and is used when appropriate under
  existing ownership, capacity, budget and independent-review rules. Do not offer
  an onboarding switch for it.
- Resource admission and capacity recovery are baseline capabilities, not separate
  onboarding questions. The current implementation enables both by default for
  new supported scopes while preserving saved choices. Enabled preference does
  not mean a recovery monitor is always running or every interruption is eligible.
  This draft changes no existing preference or execution prerequisite.
- Dependency maintenance belongs to the updater improvements, outside this
  onboarding design.
- Reuse existing communication preferences; do not ask about progress detail.
- Do not clean task worktrees. Remove both the cleanup question and its proposed
  standing cleanup permission.

Reuse inherited model, provider, authentication, budget and permission settings.
Mandatory tests, independent review, security and data-preservation requirements
remain intact. Observability and OpenRouter retain their separately authorized
follow-up flows; they do not become onboarding activation questions.

## Recording and implementation boundaries

The [existing decision format](../../onboard-my-project-plan.md#questions-within-the-selected-mode)
already records scope, options, selected value, provenance, status, affected
actions and invalidation conditions. Reuse it where managed plans are selected.
For basic native setup, retain the same meaning in the existing canonical project
instructions/setup record without requiring managed runtime state.

Keep one authoritative reporting/question procedure, routed by the onboarding
entry point. Persist effective choices where routine tasks already discover
project policy, not only in a private onboarding transcript. Link supported
feature controls for changes. Instruction entry points remain within 150 words.

Expected implementation touches onboarding routing/policy and its decision
procedure, plain-language help, relevant checks and the source-to-contract map.
Workstreams activation changes remain unresolved until its retained question is
reassessed; no runtime default migration is selected. Preserve the earlier
clarification separating future application prerequisites from actual onboarding
blockers. Installed plugin
refresh and live client qualification remain separate from source implementation.

## Acceptance checks

- The remaining questionnaire topics are publishing work, merging pull requests
  and Workstreams. The seven removed questions and the empty advanced menu do
  not appear. Workstreams remains pending reassessment of its optional effect.
- Ask applicable, unresolved choices once, with a benefit explanation, scoped
  effects and a help link. Preserve manual merging as the default.
- Default and recommendation labels may differ; neither is submitted consent.
  An unanswered question grants no publication, merge or activation authority.
- Existing, mixed and resumed preferences survive; only missing or invalidated
  choices are asked. A user can deliberately change an existing choice.
- Opt-in merge authority names the discovered repositories/targets. Failed,
  missing or stale evidence and `[READY]` alone cannot authorize a merge.
- The PR-facing `[READY]` indicator reflects current verified merge readiness and
  is invalidated when its evidence changes. Runtime task `READY` keeps its existing
  meaning. Publication permission does not silently grant merge permission.
- Onboarding does not toggle baseline parallelism, resource admission or capacity
  recovery. Preserve existing saved choices, shared safety and execution checks.
- Onboarding grants no task-worktree cleanup permission and performs no cleanup.
- Basic setup with no managed runtime records preferences without silently
  allocating one. Unsupported activation and partial multi-checkout results are
  reported accurately.
- Missing future application tools do not become onboarding blockers; baseline
  discovery, catalog coverage and Context7 checks still occur when applicable.
- Native single-select boxes preserve the options, labels, scope and help. An
  unavailable box, unanswered question or interruption leaves the choice pending
  without repeating completed work.
- Run package validation, relevant preference-preservation checks and representative
  instruction-path evaluations. Report live client observation separately.
