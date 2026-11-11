# Project-document templates implementation plan

> **Update, 11 November 2026:** the stored instruction map, `--prepare` and `build_manifest` were removed. Nested `templates/**/*.md` stay readable through the reader's allowed-path rule, and package validation checks every such source directly. Map regeneration steps below record earlier work, not current requirements.

> **For agentic workers:** Use `executing-plans` for this single, coupled change.

**Goal:** Implement candidate D: locally available project-document shapes for all nine onboarding responsibilities, without creating nine mandatory files.

**Architecture:** Add eight locally adapted Markdown templates in `templates/project/` and reuse `templates/setup-record.md` for setup/evidence. Link each shape from onboarding's existing responsibility table; move its inline starting section into the shared-instructions template. Extend the existing bounded reader to nested templates, retaining containment, Markdown-only and manifest checks. Keep existing flat template paths unchanged.

**Tech stack:** Markdown, existing Python instruction reader and unittest fixtures; no dependencies.

**Spec:** [Templates across the workflow](../specs/2026-11-09-templates-across-the-workflow-design.md), candidate D, authorized in chat after A–C merged; this document records the bounded extension and execution.

## Constraints and approach

- Owners retain applicability, preservation, policy resolution, current-Toolkit setup authority and recovery rules. Templates provide document shapes, not authorization or scripts to execute.
- Retain canonical filenames, combined documents, custom instructions, selected clients, stack and local-only publication preferences. No forced new stack or extra files.
- Routine project tasks do not fetch Toolkit. Setup/update still resolves current Toolkit guidance and reconciles material changes against these local adaptations.
- Local adaptations rather than an unmodified upstream snapshot: keep relevant content but replace bootstrap-specific defaults with observed project facts. Retain the Toolkit's MIT notice and authoring provenance.
- One task worktree/branch/PR. The user requested necessary tests only: no full suite or `--fast`.

## Review focus

1. Customized handbook paths and nested instructions: templates preserve them and links are adapted from the generated destination.
2. Non-Next, database-free and non-UI projects: no bootstrap defaults leak into generated guidance.
3. Claude-only/unselected clients and local-only publishing: no new client, permission or publication requirement.
4. Nested Markdown, outside-root symlinks and forged manifest entries: only contained instruction files can be served.
5. Reapplication and partial undo: existing file ownership and recovery contracts stay unchanged; templates do not become a second source of project facts.

## Task 1: Local templates and reader support

**Files:** `templates/project/{product,overview,shared-instructions,claude-instructions,architecture,design,local-preparation,quality}.md`; `templates/README.md`, `templates/LICENSE`; `scripts/read_instructions.py`; `tests/test_prepared_instructions.py`; `skills/onboard-my-project/references/procedure.md`; existing design; generated `clients/instruction-map.json`.

**Interfaces:** Existing `instruction_name(name)` and `build_manifest(root)` accept nested `templates/**/*.md` while retaining their public shapes and checks. Onboarding's nine-row table remains the routing owner. No new schema or runtime state.

- [x] Update the reader test to reconstruct nested template bytes and reject non-Markdown, unrelated folders, traversal and escaping symlinks, including forged map entries. Observe failure before implementing the reader change.
- [x] Extend source discovery and allowed-path classification; preserve the existing reader's validation. Rerun the focused test.
- [x] Add the eight shapes, author-only index and license. Preserve the inline policy-entry section verbatim when moving it; use safe descriptive filenames so packaged templates aren't automatically loaded as `AGENTS.md` or `CLAUDE.md` instructions.
- [x] Replace onboarding's field descriptions with selective links, keeping all preservation/recovery rules. Record source-to-local coverage and candidate D's status; regenerate the map.
- [x] Run link and responsibility coverage checks and record static walkthroughs for review-focus cases 1–3 and 5. Existing customization fixtures remain protected inputs; no native-client claim from static checks.
- [ ] Validate, commit and run only the affected reader/onboarding test modules on the unchanged candidate. Independently review behavior, requirement preservation and security in one round; apply substantiated findings and rerun only affected checks.
- [ ] Publish a draft PR against current `main`; mark ready and merge only within existing authorization and evidence rules. Report unrun native cases and preserve unrelated work.

## Observations

- Reader baseline: 31 tests passed. The modified nested-template case failed on missing map membership before the production change; both nested delivery and containment cases passed after it. The full reader module then passed 32 tests.
- Static check: 66 local template/procedure links and anchors resolve, every responsibility has a shape, and the old starting section is byte-identical in its new home. No `AGENTS.md` or `CLAUDE.md` filename exists under `templates/`.
- Minimal Python fixture: product/overview can remain combined; real command is `python3 -B check.py`, with no package manager, database, UI or Claude bridge introduced. Its Codex-only, local-only/manual-merge choices stay authoritative. Templates are read only for missing content.
- Customized fixture: `handbook/overview.md`, `handbook/ARCHITECTURE.md` and `handbook/engineering.md` stay canonical; root and `src/AGENTS.md` remain owners. Generated links are recalculated from their destination (for example, handbook guidance reaches root via `../AGENTS.md`). No nine-file rewrite is authorized by the template.
- Resume/reapplication: existing preimage/journal recovery and no-op guidance are unchanged. A later Friday-release edit in the retained fixture still requires reconciliation before undo; adding shapes changes no recovery operation. Fixture bytes/evaluators are untouched.
- Setup without a selected Claude client records the bridge's inapplicability; setup with Claude adapts its supported import and preserves customizations. Review-only and routine task startup still reach installed policies rather than this template catalog.
- These are static instruction walkthroughs, not fresh Codex/Claude execution, safe-undo execution or measured harness reliability.
