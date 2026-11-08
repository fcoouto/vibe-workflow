# Development rules

- Wrap the full Vibe Coding Toolkit; preserve its scope and requirements.
- Use Toolkit as the authoring, setup and update authority. Write complete local runtime contracts; do not copy prompts or require a Toolkit checkout, snapshot or fetch during routine tasks.
- Keep each skill entry point within 150 words. Define each wrapper rule once.
- Keep environment preparation, project enrollment, and routine development distinct.
- When adding a client adapter, follow the [client interface layout](docs/onboard-my-project-plan.md#client-scope-and-adapter-organization).
- Preserve the user's permissions, model choices, subscriptions, and customizations.
- Preserve the user's chosen coordinator model and reasoning. Before delegating implementation or review, follow [worker selection](policies/03-agent-delegation.md#select-capability-and-dispatch), including cached official platform guidance and its refresh rules.
- Validate with `python3 -B scripts/validate.py`.
- Create release archives only when requested.
- Report package validation and observed client execution separately.

## Instruction authoring

- Use OpenAI's `skill-creator` as the standard when creating or changing skills and workflow instructions.
- Keep purpose, essential constraints, and explicit loading triggers in entry points. Load complete local procedures only when applicable; upstream research belongs to authoring, setup and updates.
- Preserve every requirement when shortening or reorganizing instructions. Maintain [source-to-contract coverage](docs/policy-sources.md) for authoring and updates; keep provenance out of routine loading paths.
- Check affected instruction paths with representative tasks: required procedures load, every applicable requirement is satisfied, and unrelated material stays unloaded. Word counts and package validation alone do not prove behavior.
- Write human-facing guidance in plain language: explain purpose, next action, and what completion means.

## Memory

- Durable lessons live in the plain-file vault `~/ai-memory-vault/`. No client auto-loads that folder; this section is the wiring. At session start, before project work, read `~/ai-memory-vault/MEMORY.md` (the index) and open a linked topic file only when relevant to the task.
- `~/ai-memory-vault/INSTRUCTIONS.md` owns the save test, topic-file template and growth policy. Read it before saving, updating, migrating or pruning: err toward not saving, and delete a migrated index entry only after its long-term store note read-back succeeds.
- Keep temporary handoffs in the task record, never the vault. Entries migrated out of the index go to `docs/memory/`, created on first migration.
