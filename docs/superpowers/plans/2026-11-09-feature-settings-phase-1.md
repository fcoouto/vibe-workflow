# Feature settings — phase 1 plan

Date: 2026-11-09. Status: draft for user review. Design: [feature settings](../specs/2026-11-09-feature-settings-design.md).

## Outcome

Every packaged feature has a manifest with a declared default; one resolver, CLI and startup line report all of them; `workstreams` is read from `.vibe-workflow/settings.json` instead of `AGENTS.md` and the legacy runtime gate; onboarding writes the new file and keeps private files out of Git. Consent features keep their existing stores and are reported through read-only adapters.

**Excluded:** user and checkout layers; moving `resource_admission`, `capacity_recovery`, `smart_routing`, `usage_routing`, `openrouter.*` or `observability.langfuse` state (phase 1 only materializes the two Workstreams advanced values in place); revocation records; renaming `vcw` roots. These are phase 2, one feature per change.

## Steps

Each step starts with failing tests (TDD) and ends with its listed check passing. Python files stay at or under 1000 lines.

1. **Manifests and validation.**
   - Add `features/*.json` for: `workstreams` (`settings`, default off), `workstreams.resource_admission` and `workstreams.capacity_recovery` (`adapter`, source: runtime file, checkout), `thread_naming` (`fixed`, on), `core` (`fixed`, on; skills `onboard-my-project`, `prepare-my-workspace`, `review-my-project`), `smart_routing` (`adapter`, on, source: per-user settings), `usage_routing` (`adapter`, `spends`, off), `openrouter.proposals` and `openrouter.workers` (`adapter`, `transmits`, off), `observability.langfuse` (`adapter`, `transmits`, off).
   - `scripts/validate.py` checks the manifest schema, unique IDs, required defaults, `source` on every `adapter` feature, that `procedure` and `adapter` paths stay inside the package, and that every skill under `skills/` belongs to at least one feature. A test pins the explicit feature inventory, so a feature cannot disappear unnoticed. The new manifests ship in the export (`scripts/package.py` unchanged).
   - Check: `python3 -B scripts/validate.py`; tests for each rejection (missing default, unknown key, unmapped skill, adapter without source, path outside the package) and the inventory test.

2. **Settings store and resolver.**
   - `runtime/settings_store.py`: bounded no-link read, strict JSON (duplicate keys rejected), compare-and-set on `revision` and content digest under `.vibe-workflow/private/settings.lock`, recheck of the file's identity before an atomic replace with directory fsync. Committed-file rules: no absolute paths, normal file mode. Before creating `private/` (owner-only modes), `git check-ignore --no-index` must confirm a rule covers `private/` and `git ls-files` must show it untracked; otherwise refuse. A tracked `project.json` is a warning, not a refusal.
   - `runtime/features.py`: load manifests; resolve package default < project settings; return desired, consent, effective and observed states with the fixed reason codes; report `ignored_override` for values set on `adapter` and `fixed` features; absent inherits, invalid makes the feature `unavailable`; expose a resolved-settings fingerprint.
   - Check: unit tests for precedence, each reason code, invalid versus absent (a default-on feature explicitly disabled stays off when the file is corrupted), same-revision edits and branch switches detected by digest, concurrent writers (one wins, one conflicts), refusal when `private/` would not be ignored or is tracked, a tracked `project.json` reported as a warning while settings writes still succeed, no write on read.

3. **Adapters (read-only).** Report status for the `adapter` features from their current stores, offline, without credentials or store creation, normalized into desired, consent, effective and observed states. Saved consent with a stale enrollment, qualification or checkout binding reports `stale_consent`, never effective. An unavailable store yields `adapter_unavailable`, never an error that blocks other features.
   - Check: adapter tests over disposable stores: missing, corrupt and busy stores, stale enrollment, changed qualification, and a grant copied from another checkout.

4. **CLI.** `scripts/workflow.py features status|enable|disable|set|unset|migrate`, JSON on stdout, exit 1 on errors. Writes only `settings` features; others refuse with the managing skill's name.
   - Check: CLI tests for each command, refusals and the `--summary` form.

5. **Workstreams cutover.**
   - New ordinary admissions (delegation sends and `runtime/routing_settings.workstreams_enabled`) read the resolved `workstreams` feature and bind to the settings fingerprint. The runtime file's top-level `enabled` is kept for historical requests and their pins (`runtime/resource_execution.py`, `runtime/parent_recovery.py`) and no longer decides new ordinary admissions.
   - Where advanced runtime state already exists, write the current effective `resource_admission` and `capacity_recovery` values explicitly into the runtime file, so they stop inheriting the top-level flag. Never create runtime state for this.
   - `features migrate --plan|--apply` for `workstreams`: import the owned `AGENTS.md` block (Enabled or Disabled), write `.vibe-workflow/settings.json`, replace the block with the instruction to run `features status`, keep a private preimage and support undo.
   - Rewrite `skills/workstreams/references/procedure.md` and the skill entry (150 words or fewer) to use the CLI.
   - Check: migration tests (block present, absent, malformed, conflicting); delegation and routing tests that previously depended on the runtime gate; historical requests and absent advanced entries keep their behavior; a basic checkout with no runtime state gets none created; explicit delegation and required review still work with the feature off; a fresh worktree reads the committed value; a settings change between preparation and send is detected.

6. **Startup line.** Enrolled-checkout startup output gains one `Features:` line from manifests and the settings file only, within startup's existing time and output budgets. Runtime-backed stores are never read; `adapter` features show as unchecked.
   - Check: startup tests for Codex and Claude Code output, the output bound, silence when unenrolled, zero writes or store creation, and unchanged orientation (including qualification-only output) when the settings file is invalid or busy.

7. **Onboarding.**
   - The Workstreams question writes `.vibe-workflow/settings.json` through the CLI instead of the `AGENTS.md` block.
   - Add `/.vibe-workflow/project.json` and `/.vibe-workflow/private/` to the repository's `.git/info/exclude` (path from `git rev-parse --git-path info/exclude`) when no rule covers them, through a new journaled Git-metadata operation: only that file, a lock for the shared resource, a recorded preimage, atomic write, crash resume, and undo that removes only the lines it added. Warn when `project.json` is tracked.
   - Check: tests for a linked worktree sharing the file, a concurrent edit to the file, crash and resume, undo keeping later edits, and an existing rule that makes the entry unnecessary.
   - The settings change is a recorded onboarding effect (preimage, readback, undo), not an unjournaled CLI call.
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
- A committed value for any `adapter` feature, including `enabled: true` for a consent feature, reports `ignored_override` and changes no admission; a stale saved grant reports `stale_consent`.
- A corrupted settings file never turns on a feature that was explicitly off.
- New ordinary Workstreams admissions have one source of truth: `.vibe-workflow/settings.json`, with the package default when absent. The `AGENTS.md` block is gone; the runtime top-level `enabled` is retained solely for historical requests and their pins.
- Startup shows one `Features:` line in enrolled checkouts only.
- Onboarding leaves `project.json` and `private/` untracked in a new project and reports an already-tracked record.
- Package validation fails for a skill without a feature or a feature without a default.
