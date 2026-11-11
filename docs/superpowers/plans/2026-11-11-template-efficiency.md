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

- [x] Add tests for the five Review Focus cases, stale prepared sources, duplicate/nonregular paths, exact template selection and machine-readable CLI failure.
- [x] Commit tests and observe the new helper's absence fail the focused module; baseline prepared-reader tests must pass.
- [x] Implement bounded local reads, dependency hashing, selective reader reuse and findings without invoking project code.
- [x] Commit and run `python3 -B scripts/test_sharded.py test_template_tools test_prepared_instructions`; expect all selected tests to pass.

### Task 2: Narrow instruction changes and replay proof

**Files:** modify `skills/onboard-my-project/references/procedure.md`, `templates/project/product.md`, `templates/project/overview.md`, `templates/project/local-preparation.md`, `policies/03-agent-delegation.md`, `docs/policy-sources.md`, `clients/instruction-map.json`; create `clients/template-tools.md`; extend `tests/test_template_tools.py` with protected fixture replay.

**Interfaces:** existing onboarding mapping supplies explicit destinations; existing assignment context supplies exact dependencies. Owners link the helper procedure only for template adaptation/checking or cross-checkout context transport. The helper never replaces source inspection, semantic review or accepted setup recovery.

- [x] Show exact before/after instructions before editing.
- [x] Replay original synthetic inputs with a missing consumer owner, a copied setup author note and a missing local link; verify rejection, preservation and repaired controls without model execution.
- [x] Document explicit callable triggers and future hook limits, with no general startup loading.
- [x] Regenerate the instruction map, commit, then run focused helper/reader/harness checks and `python3 -B scripts/validate.py`.

### Task 3: Review and publish

**Files:** execution notes in this plan; private review report outside the checkout.

- [x] Inspect final diff and changed instruction routes; obtain one independent behavior/security review with read-only scope.
- [x] Resolve substantiated findings with focused regression proof; reuse unaffected results.
- [ ] Record actual checks, observed overhead and unavailable live qualification; push and open a draft PR against `main`.

## Execution record

Status: WORKING. The user approved the design and requested continuation; implement inline rather than adding an execution-choice pause. Current main includes a generated worker specialist roster; preserve it. Historical evaluation remains on its original branch/PR. The previous live-inference budget is closed. This delivery implements the offline/helper slice and exposes callable triggers; a numeric pilot budget and native hook qualification remain dependent follow-up work, not implied passes.

### Offline evidence

- Base: `11a8927c44c6c0c0965b836c9b00d81fb0115591`. Test-first snapshots `97adfd9` and `109f9e4` failed on the absent helper (15 and 19 tests); the existing reader baseline passed 32 tests.
- Candidate `b1a42ac`: helper 19/19 and reader 32/32 passed. The first implementation's failed duplicate-suppression test was retained and fixed by fingerprinting observed missing targets.
- Candidate `14db34f`: `python3 -B scripts/test_sharded.py test_template_tools test_prepared_instructions test_harness_evaluation` passed 61 tests in 0.8 seconds wall time. `python3 -B scripts/validate.py` passed with no warnings; `git diff --check` passed. Environment: Python 3.15.2, macOS arm64. Selected modules cover helper behavior, exact reader delivery, protected assets and evaluator compatibility; no full suite was run, as requested.
- Protected customized-project replay rejected copied setup directions, destination-relative missing links and missing consumer context, then accepted repaired controls. Original inputs and all restored project bytes were verified unchanged. This is deterministic replay, not another agent trial.
- Observed preparation example: one product shape delivered 1,580 source bytes; complete JSON response 2,114 bytes, including one context digest. These are bytes, not model tokens or billing savings. Consumer source inspection remains necessary.
- Static instruction walkthrough: selected onboarding additions load the adaptation owner, then helper and only selected shapes; a combined README remains valid. A normal one-line code change or read-only review has no template-adaptation trigger. Cross-checkout handoff checks accepted digests in the consumer and reuses equivalent assignment evidence. Semantic acceptance, tool inference, supplied dates and proposed work remain agent decisions. Native behavior and causal savings remain unqualified.
- Presentation: title helper reported `manual_title`; the existing title is preserved and automatic title changes are paused.

### Independent review preparation

One native General child reviewed behavior and local filesystem/security boundaries, read-only on the entire branch. Its role was `code-reviewer`, specialty template assistance and filesystem boundaries. No external route or evaluation trial was requested. Routing status reported no managed projects; Workstreams was enabled. Cached official OpenCode native guidance checked 8 November 2026 remained applicable (SHA-256 `fae88d07f36f0aad858eedd1b866f5e1ceccf082501e6ec246b6d44fbec711e1`). This harness permits model overrides only for explicit user model requests; native configured/inherited controls and coordinator settings were retained. The child reported GPT-6 Astra from its harness context; reasoning effort was not independently observed. Codex-specific UUID/display preparation did not apply to this OpenCode session; the role was declared in the native brief without claiming badge rendering.

### Review disposition

The native dispatch created child `ses_e33d2dfa5ffe7P9nixAPWPF4Gj`, which returned a private mode-0600 report for `11a8927..f09319f`. The report was read and all findings checked against source and reproductions. Three medium/Important findings entered one fix pass:

| Finding | Disposition and proof |
| --- | --- |
| R1: Inline-code text disappeared from ATX heading anchors | Fixed by preserving visible code text for heading extraction while retaining code masking for link examples. `test_heading_anchors_preserve_visible_inline_code` failed first, then passed with an absent-fragment control. |
| R2: Duplicate normalized reference labels used the last definition | Fixed by retaining the first definition. `test_duplicate_reference_labels_use_first_definition` failed in both orderings, then passed with case/whitespace-equivalent labels. |
| R3: Duplicate headings and repeated anchored links caused excessive work | Fixed with next-suffix tracking and per-observation anchor caching. `test_duplicate_heading_collisions_and_repeated_links_finish_within_budget` exceeded its five-second deadline before the fix, then passed with 20,000 duplicate headings, 100 links and mixed heading collisions. |

RED snapshot `ddef680` retained the three reproductions. GREEN candidate `567fbce` passed all 64 selected tests (23 helper, 32 reader, 9 harness) in 0.8 seconds wall time; the helper module took 0.4 seconds including the stress test. `python3 -B scripts/validate.py` passed without warnings and `git diff --check` passed. The coordinator inspected the final fix diff and reused unaffected independent coverage. No second review round or full suite was run. No minor findings were deferred.

The implementation is verified within the offline scope. Publication remains a draft because fresh instruction-effectiveness evidence needs a new numeric pilot budget. Native hook registration remains dependent on actual client qualification. These pending observations do not become passes through the review or package checks.

### Execution rulings

- Preserve current main's specialist roster rather than edit the historical evaluation branch; historical thirteen-template counts do not cover the newer machine contract.
- Deliver the offline helper and callable feedback boundary first; token effectiveness and native hook behavior remain provisional until separately authorized observations.
- Use only affected checks, following the user's limit and development guide, instead of generic skill full-suite instructions; unrelated regressions remain outside this proof. Reuse current successful runs instead of repeating them for bookkeeping.
- Native hook delivery and token/billing/causal gains remain unassessed: neither a hook adapter nor an authorized pilot exists in this slice; users cannot infer effectiveness from offline checks.
- Semantic acceptance, baseline authenticity and changes after observation remain with existing agent/evidence owners: mechanical checks cannot authenticate them; callers must review meaning and recheck changed inputs.
- Full Markdown conformance, HTML, setext headings, directory links and renderer extensions remain outside the documented basic parser: unsupported valid syntax can still need manual inspection.
- Compromised installed-package code, hostile replacement of root ancestors, live deployments, unrelated modules and package-export cleanup remain outside this local review: package and roots are trusted anchors; no assurance is claimed for those environments.
