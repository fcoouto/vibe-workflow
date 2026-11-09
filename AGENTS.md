# Development rules

## Start here

1. Before substantive work in this repository, read the [policy index](policies/00-index.md), unless its complete, current text is already in context.
2. Use its **Load when** table to read every applicable policy before taking the affected action. Follow triggered dependencies to their complete local procedures; a link or routing summary is not the procedure.
3. Read applicable directory instructions. Reassess triggers when scope changes and before implementation, delegation, completion or merge. After resume or context loss, restore missing instructions and check actual project state.
4. Resolve missing, conflicting or truncated instructions before the dependent action; continue independent authorized work. Preserve normal instruction precedence and task-specific policy pins.

Hooks supplement these instructions. Their presence or silence does not establish policy compliance, enforcement or readiness. Ordinary development remains independent of optional managed controls.

## Repository constraints

- Wrap the full Vibe Coding Toolkit; preserve its scope and requirements.
- Use Toolkit as the authoring, setup and update authority. Write complete local runtime contracts; do not copy prompts or require a Toolkit checkout, snapshot or fetch during routine tasks.
- Keep environment preparation, project enrollment, and routine development distinct.
- Make basic onboarding useful without a runtime database or repository-enforcement prerequisite. Ordinary development uses native editing, checks and review; advanced controls acquire their own prerequisites only when selected. Preserve explicitly selected protections and allow configuration recovery when qualification is unavailable.
- When adding a client adapter, follow the [client interface layout](docs/onboard-my-project-plan.md#client-scope-and-adapter-organization).
- Preserve the user's permissions, model choices, subscriptions, and customizations.
- Preserve the user's chosen coordinator model and reasoning. Before delegating implementation or review, follow [worker selection](policies/03-agent-delegation.md#select-capability-and-dispatch), including cached official platform guidance and its refresh rules.
- Prefer existing code and native capabilities. Reassess complex controls against concrete user risks; consolidate duplicate checks and evidence while preserving applicable requirements.
- Keep each Python file at or under 1000 lines so agents can read it in context. `scripts/validate.py` enforces this; the legacy runtime modules it lists have fixed ceilings that must not be raised. Split by responsibility instead.

## Where to look

- For client-specific work, use [Clients](clients/index.md) and load only the entry whose trigger applies.
- For an explicit workflow operation, read its `skills/<name>/SKILL.md` entry point and triggered references. Keep workspace preparation, project onboarding and routine development scoped to the requested operation.
- For setup decisions, maintenance and undo, read [Setup record](docs/setup.md).

## Verification and delivery

- Validate with `python3 -B scripts/validate.py`.
- Run the full suite with `python3 -B -m unittest discover -s tests` when runtime, hook or policy behavior changes; it takes about two hours. `python3 -B scripts/test_sharded.py` runs the same modules in parallel shards in about a third of that time and lists failing tests; rerun a failure alone before judging it, since timing-sensitive tests can fail on an overloaded machine.
- Publish completed task changes by committing, pushing a task branch and opening a draft PR. Use `main` as the integration branch and PR destination. Merge only completed, independently reviewed slices against current `main`, in PRs marked `[READY]`, after local checks pass; the repository has no CI or branch protection. Preserve unfinished work separately and retain newer changes when reconciling older branches.
- Create release archives only when requested.
- Report package validation and observed client execution separately.

## Instruction authoring

- Use OpenAI's `skill-creator` as the standard when creating or changing skills and workflow instructions.
- Keep each skill entry point within 150 words. Define each wrapper rule once.
- Keep purpose, essential constraints, and explicit loading triggers in entry points. Load complete local procedures only when applicable; upstream research belongs to authoring, setup and updates.
- Preserve every requirement when shortening or reorganizing instructions. Maintain [source-to-contract coverage](docs/policy-sources.md) for authoring and updates; keep provenance out of routine loading paths.
- Check affected instruction paths with representative tasks: required procedures load, every applicable requirement is satisfied, and unrelated material stays unloaded. Word counts and package validation alone do not prove behavior.
- Write human-facing guidance in plain language: explain purpose, next action, and what completion means.

## Memory and project preferences

- Memory: the user-level vault `~/ai-memory-vault` is plain files, with no vault software or MCP server. Before project work, read its `MEMORY.md` index and open topic files only when relevant. Before saving, updating, migrating or pruning, read its `INSTRUCTIONS.md` in full and follow its narrow test and growth policy. Keep this section intact; it is the vault's load path.

<!-- vibe-workflow:workstreams:start -->
- [x] Workstreams
Follow the [Workstreams project preference](skills/workstreams/references/procedure.md).
<!-- vibe-workflow:workstreams:end -->
