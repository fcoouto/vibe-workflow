# Development rules

- Wrap the full Vibe Coding Toolkit; preserve its scope and requirements.
- Use Toolkit as the authoring, setup and update authority. Write complete local runtime contracts; do not copy prompts or require a Toolkit checkout, snapshot or fetch during routine tasks.
- Keep each skill entry point within 150 words. Define each wrapper rule once.
- Keep environment preparation, project enrollment, and routine development distinct.
- Make basic onboarding useful without a runtime database or repository-enforcement prerequisite. Ordinary development uses native editing, checks and review; advanced controls acquire their own prerequisites only when selected. Preserve explicitly selected protections and allow configuration recovery when qualification is unavailable.
- When adding a client adapter, follow the [client interface layout](docs/onboard-my-project-plan.md#client-scope-and-adapter-organization).
- Preserve the user's permissions, model choices, subscriptions, and customizations.
- Preserve the user's chosen coordinator model and reasoning. Before delegating implementation or review, follow [worker selection](policies/03-agent-delegation.md#select-capability-and-dispatch), including cached official platform guidance and its refresh rules.
- Validate with `python3 -B scripts/validate.py`.
- Create release archives only when requested.
- Report package validation and observed client execution separately.
- Use `main` as the integration branch and PR destination. Merge only completed, reviewed slices against current `main`; preserve unfinished work separately and retain newer changes when reconciling older branches.
- Prefer existing code and native capabilities. Reassess complex controls against concrete user risks; consolidate duplicate checks and evidence while preserving applicable requirements.

## Instruction authoring

- Use OpenAI's `skill-creator` as the standard when creating or changing skills and workflow instructions.
- Keep purpose, essential constraints, and explicit loading triggers in entry points. Load complete local procedures only when applicable; upstream research belongs to authoring, setup and updates.
- Preserve every requirement when shortening or reorganizing instructions. Maintain [source-to-contract coverage](docs/policy-sources.md) for authoring and updates; keep provenance out of routine loading paths.
- Check affected instruction paths with representative tasks: required procedures load, every applicable requirement is satisfied, and unrelated material stays unloaded. Word counts and package validation alone do not prove behavior.
- Write human-facing guidance in plain language: explain purpose, next action, and what completion means.

<!-- vibe-workflow:workstreams:start -->
- [x] Workstreams
Follow the [Workstreams project preference](skills/workstreams/references/procedure.md).
<!-- vibe-workflow:workstreams:end -->
