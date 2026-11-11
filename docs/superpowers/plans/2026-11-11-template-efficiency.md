# Template Efficiency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove mechanical template work from model turns without creating another workflow engine.

**Architecture:** A read-only standard-library helper prepares selected context and checks explicit output documents. The existing prepared reader owns exact template delivery; onboarding and delegation remain the instruction owners. A pure notice formatter supports future narrowly triggered hooks without registration or automatic repair.

**Tech Stack:** Python 3.11+, existing unittest/sharded runner, Markdown and existing instruction map.

**Spec:** `docs/superpowers/specs/2026-11-11-template-efficiency-design.md`

## Global Constraints

- Preserve Toolkit coverage, source templates' customization contract, all original evidence and newer task-profile generation.
- No dependencies, model calls, network, automatic writes, universal schema, new state database or default hook registration.
- Explicit selected files only; at most 64 paths per selection, 1 MiB per file and 4 MiB per operation.
- No implicit inference budget renewal; no native hook qualification from unit tests.
- Work on `feat/template-efficiency`, based on current `main`; publish a separate draft PR.

## Review Focus

- Links and selected paths cannot traverse outside the checkout or follow symlinks to private files.
- Original author-like prose must survive; residue detection applies to additions and does not auto-delete.
- Missing or changed consumer dependencies must fail before preparation claims success.
- Combined documents and non-Git projects must work without a mandatory nine-file layout.
- Hook feedback must stay bounded, silent on success and deduplicated only for the same observed content.

---

### Task 1: Read-only preparation and mechanical checks

**Files:** create `runtime/template_tools.py`, `scripts/template_tools.py`, `tests/test_template_tools.py`.

**Interfaces:** `prepare(root, templates, context, *, page=1, revision=None)` returns a context receipt and existing reader page; `check(root, documents, templates=(), *, before=None, required=None)` returns findings and checked-content identity; `notice(result, previous=None)` formats bounded advisory feedback. The CLI exposes `prepare` and `check`, with explicit `--cwd`, repeated `--template`/`--context`/`--document`, optional `--before`/`--require path=sha256`, and quiet check success.

- [ ] Add tests for the five Review Focus cases, stale prepared sources, duplicate/nonregular paths, exact template selection and machine-readable CLI failure.
- [ ] Commit tests and observe the new helper's absence fail the focused module; baseline prepared-reader tests must pass.
- [ ] Implement bounded local reads, dependency hashing, selective reader reuse and findings without invoking project code.
- [ ] Commit and run `python3 -B scripts/test_sharded.py test_template_tools test_prepared_instructions`; expect all selected tests to pass.

### Task 2: Narrow instruction changes and replay proof

**Files:** modify `skills/onboard-my-project/references/procedure.md`, `templates/project/product.md`, `templates/project/overview.md`, `templates/project/local-preparation.md`, `policies/03-agent-delegation.md`, `docs/policy-sources.md`, `clients/instruction-map.json`; create `clients/template-tools.md`; extend `tests/test_template_tools.py` with protected fixture replay.

**Interfaces:** existing onboarding mapping supplies explicit destinations; existing assignment context supplies exact dependencies. Owners link the helper procedure only for template adaptation/checking or cross-checkout context transport. The helper never replaces source inspection, semantic review or accepted setup recovery.

- [ ] Show exact before/after instructions before editing.
- [ ] Replay original synthetic inputs with a missing consumer owner, a copied setup author note and a missing local link; verify rejection, preservation and repaired controls without model execution.
- [ ] Document explicit callable triggers and future hook limits, with no general startup loading.
- [ ] Regenerate the instruction map, commit, then run focused helper/reader/harness checks and `python3 -B scripts/validate.py`.

### Task 3: Review and publish

**Files:** execution notes in this plan; private review report outside the checkout.

- [ ] Inspect final diff and changed instruction routes; obtain one independent behavior/security review with read-only scope.
- [ ] Resolve substantiated findings with focused regression proof; reuse unaffected results.
- [ ] Record actual checks, observed overhead and unavailable live qualification; push and open a draft PR against `main`.

## Execution record

Status: WORKING. The user approved the design and requested continuation; implement inline rather than adding an execution-choice pause. Current main includes a generated worker specialist roster; preserve it. Historical evaluation remains on its original branch/PR. The previous live-inference budget is closed. This delivery implements the offline/helper slice and exposes callable triggers; a numeric pilot budget and native hook qualification remain dependent follow-up work, not implied passes.
