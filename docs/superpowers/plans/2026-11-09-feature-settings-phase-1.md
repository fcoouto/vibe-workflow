# Feature settings — phase 1 plan

Date: 2026-11-09. Status: draft for user review. Design: [feature settings](../specs/2026-11-09-feature-settings-design.md).

## Outcome

Every packaged feature has a manifest with a declared default; one resolver, CLI and startup line report all of them; `workstreams` is read from `.vibe-workflow/settings.json` instead of `AGENTS.md` and the legacy runtime gate; onboarding writes the new file and keeps private files out of Git. Consent features keep their existing stores and are reported through read-only adapters.

**Excluded:** user and checkout layers; moving `resource_admission`, `capacity_recovery`, `smart_routing`, `usage_routing`, `openrouter.*` or `observability.langfuse` state; revocation records; renaming `vcw` roots. These are phase 2, one feature per change.

## Steps

Each step starts with failing tests (TDD) and ends with its listed check passing. Python files stay at or under 1000 lines.

1. **Manifests and validation.**
   - Add `features/*.json` for: `workstreams` (`settings`, default off), `thread_naming` (`fixed`, on), `core` (`fixed`, on; skills `onboard-my-project`, `prepare-my-workspace`, `review-my-project`), `smart_routing` (`adapter`, on), `usage_routing` (`adapter`, `spends`, off), `openrouter.proposals` and `openrouter.workers` (`adapter`, `transmits`, off), `observability.langfuse` (`adapter`, `transmits`, off).
   - `scripts/validate.py` checks the manifest schema, unique IDs, required defaults, that `procedure` and `adapter` paths stay inside the package, and that every skill under `skills/` belongs to at least one feature. The new manifests ship in the export (`scripts/package.py` unchanged).
   - Check: `python3 -B scripts/validate.py`; tests for each rejection (missing default, unknown key, unmapped skill, path outside the package).

2. **Settings store and resolver.**
   - `runtime/settings_store.py`: bounded no-link read, strict JSON (duplicate keys rejected), `revision` compare-and-set under `.vibe-workflow/private/settings.lock`, atomic replace with directory fsync. Committed-file rules: no absolute paths, normal file mode.
   - `runtime/features.py`: load manifests; resolve package default < project settings; return desired, effective, source, revision, consent and reason code per feature; ignore and report values for `adapter` and `fixed` features; consent features never effective from settings alone.
   - Check: unit tests for precedence, each reason code, invalid files (falls back to defaults and reports the path), concurrent writers (one wins, one gets a revision conflict), no write on read.

3. **Adapters (read-only).** Report status for `smart_routing`, `usage_routing`, `openrouter.*` and `observability.langfuse` from their current stores, offline, without credentials or store creation. An unavailable store yields `adapter_unavailable`, never an error that blocks other features.
   - Check: adapter tests over disposable stores, including missing and corrupt ones.

4. **CLI.** `scripts/workflow.py features status|enable|disable|set|unset|migrate`, JSON on stdout, exit 1 on errors. Writes only `settings` features; others refuse with the managing skill's name.
   - Check: CLI tests for each command, refusals and the `--summary` form.

5. **Workstreams cutover.**
   - Delegation admission and `runtime/routing_settings.workstreams_enabled` read the resolved `workstreams` feature. The legacy runtime file's top-level `enabled` is no longer read; its `features.*` entries stay for phase 2.
   - `features migrate --plan|--apply` for `workstreams`: import the owned `AGENTS.md` block (Enabled or Disabled), write `.vibe-workflow/settings.json`, replace the block with the instruction to run `features status`, keep a private preimage and support undo.
   - Rewrite `skills/workstreams/references/procedure.md` and the skill entry (150 words or fewer) to use the CLI.
   - Check: migration tests (block present, absent, malformed, conflicting); delegation and routing tests that previously depended on the runtime gate; a fresh worktree reads the committed value.

6. **Startup line.** Enrolled-checkout startup output gains one `Features:` line from the resolver, bounded and offline; unenrolled checkouts stay silent.
   - Check: startup tests for Codex and Claude Code output, size bound, and silence when unenrolled.

7. **Onboarding.**
   - The Workstreams question writes `.vibe-workflow/settings.json` through the CLI instead of the `AGENTS.md` block.
   - Add `/.vibe-workflow/project.json` and `/.vibe-workflow/private/` to `.git/info/exclude` when the project's ignore rules do not already cover them; warn when `project.json` is tracked; undo removes only lines it added.
   - The shared instruction pointer tells agents to run `features status` before feature-dependent work.
   - Check: onboarding apply, resume and rollback tests for these effects.

8. **This repository.**
   - `.gitignore`: replace `/.vibe-workflow/` with `/.vibe-workflow/project.json` and `/.vibe-workflow/private/`.
   - Commit `.vibe-workflow/settings.json` with the Workstreams value the user chooses.
   - `AGENTS.md`: one rule that new features and skills ship a manifest with a declared default, linking a new author contract `clients/features.md`. Update `docs/setup.md`, `docs/policy-sources.md` and the instruction map.
   - Check: `validate.py`, link check of changed instructions.

9. **Verification and review.**
   - `python3 -B scripts/test_sharded.py` (runtime behavior changes) and `validate.py`.
   - Observe startup in a real Codex and Claude Code session on the export: the `Features:` line appears in the enrolled checkout and nothing in an unrelated repository.
   - Independent review covering behavior and security (consent cannot be enabled by the committed file, a clone or a copied grant), then a `[READY]` PR.

## Acceptance

- `features status --cwd <checkout>` lists every packaged feature with its default, effective state and source.
- A committed `enabled: true` for a consent feature reports `needs_consent` and changes no admission.
- `workstreams` has one source of truth: `.vibe-workflow/settings.json`, with the package default when absent; the `AGENTS.md` block and the runtime top-level `enabled` are gone.
- Startup shows one `Features:` line in enrolled checkouts only.
- Onboarding leaves `project.json` and `private/` untracked in a new project and reports an already-tracked record.
- Package validation fails for a skill without a feature or a feature without a default.
