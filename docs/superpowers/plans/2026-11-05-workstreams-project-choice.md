# Workstreams Project Choice Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for this single instruction slice, followed by independent Astra review.

**Goal:** Make onboarding and the Workstreams skill manage the same effective Enabled/Disabled choice.

**Architecture:** Native edits to an owned checkbox in project guidance. Reuse existing advanced controls without changing their runtime authority.

**Tech Stack:** Markdown instructions and existing native tools.

**Spec:** [Workstreams project choice](../specs/2026-11-05-workstreams-project-choice.md).

## Constraints and review focus

Preserve user guidance, permissions, model choices, selected protections and
uncertain ownership. No runtime database, VM, Docker test, qualification or plugin
install. Missing choices, linked checkout boundaries, repeated requests, mixed
advanced settings and required review must remain truthful.

## Task 1: Shared project choice

- [x] Make `skills/workstreams/references/procedure.md` own the checkbox and native status/edit procedure; move existing advanced instructions intact to `advanced-controls.md`.
- [x] Update skill routing, onboarding's existing question, delegation triggers, README and source-to-contract coverage.
- [x] Exercise representative tasks with an isolated disposable instruction fixture; independently check preserved bytes, state and routing.
- [x] Save Enabled in this selected checkout's `AGENTS.md`, retaining a private preimage and hash; verify exact readback and idempotence.
- [x] Refresh the prepared instruction map and run `python3 -B scripts/validate.py` plus affected instruction-reader checks.
- [x] Request fresh independent Astra review of the final diff and usability; address supported findings and record results.

The user's existing instruction authorizes needed work and specifies the simple
choice. No additional design approval or execution-method question is needed.
Commit, merge, release and installation are not part of this slice's completion.

## Observed results

- Package validation passed; Workstreams entry point is 142 words under the package counter. Client execution remains `not_checked`.
- Eight native instruction/file-fixture scenarios passed. The coordinator independently checked all five final fixture trees, preserved Unicode/unrelated bytes, repeated-enable idempotence, resolved links and retained simulated ownership. These are not managed execution or qualification results.
- `python3 -B -m unittest tests.test_prepared_instructions tests.test_workstreams_cli` (separation tests now live in `tests.test_workstreams`) ran 87 tests: 86 passed; one fixture setup reported `configuration_changed` while the coordinator changed policy during the run. With files frozen, `python3 -B -m unittest -v tests.test_workstreams.SeparationTests.test_feature_replies_do_not_claim_lifecycle_state` passed. No runtime repair was needed.
- Independent Astra final review found no blockers, including the final Disabled/missing-choice clarification. Advanced control instructions remain verbatim after their introductory routing paragraph.
- Private preimage, SHA-256, fixture results, coordinator checks, candidate manifest and Astra report are retained under `/Users/fcouto/.local/share/vcw-validation/workstreams-checkbox-saut_f_y/`.
- Enabled applies to this selected checkout through its local source procedure. The dirty primary checkout and installed 0.1.23 were not changed. Reusable onboarding/skill updates remain unpublished and uninstalled.
