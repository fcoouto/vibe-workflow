# Prepared Instruction Loading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Root implements this cohesive slice; independent reviewers assess the result.

**Goal:** Prepare and deliver applicable Vibe instructions without reconstructing clipped text.

**Architecture:** Keep canonical instruction sources unchanged except for the Codex loading route. Prepare exact line ranges and digests during authoring. A read-only CLI returns bounded pages; existing startup points to it before task work, including independent review, unless complete current instructions are retained.

**Tech Stack:** Python standard library, existing hooks and unittest.

**Spec:** `docs/superpowers/specs/2026-11-03-prepared-instruction-loading-design.md`

## Global Constraints

- Preserve all requirements, authority checks, native workflow, permissions and model settings.
- Do not modify RTK, other plugins/skills or the live Vibe installation.
- No runtime database, external dependency, automatic recovery loop or silent task repin.
- Keep source bytes exact; source segments at most 8,000 bytes and rendered pages at most 9,500 bytes.
- Reuse the existing isolated checkout, staged candidate and identical benchmark protocol.

## Review Focus

- Changed source or continuation must not combine incompatible revisions.
- Missing, duplicate, overlapping or out-of-bounds ranges must not hide requirements.
- Absolute paths, traversal and symlinks must not expose files outside packaged instructions.
- Startup-only and unenrolled contexts must not acquire managed runtime prerequisites.
- A bounded child output does not protect arbitrary outer rebatching or third-party reads.

### Task 1: Prepared reader and startup integration

**Files:** Create `scripts/read_instructions.py`, `clients/instruction-map.json`, `tests/test_prepared_instructions.py`. Modify `runtime/startup.py`, `clients/codex.md`, `scripts/validate.py`, affected startup tests and `docs/policy-sources.md`.

**Interfaces:** `build_manifest(root) -> dict`; `load_page(root, paths, page=1, revision=None) -> dict`; `render_page(result) -> str`; CLI `read_instructions.py bootstrap`, or selected paths with `--page` and `--revision`; authoring-only `--prepare`.

- [x] Write and run failing tests for lossless bounded reconstruction, selective reads, stale continuation, malformed ranges and path containment.
- [x] Implement preparation and the read-only reader using only the standard library.
- [x] Route Codex startup to the reader without changing authority or Claude startup behavior; test the emitted command against real packaged sources.
- [x] Generate the manifest, integrate freshness validation, run focused tests and package validation.
- [x] Independently review the full slice and record requirement coverage; fix confirmed findings within scope.
- [x] Commit the reviewed candidate and preserve the prior candidate separately.

### Task 2: Repeat the paired benchmark

**Files:** New private run directory and copies of the existing benchmark adapter/orchestrator outside the package.

**Interfaces:** Same prompts, fixture, model/effort, implementation/review/one-resume phases and analyzer; add explicit recognition of prepared reader calls.

- [x] Stage only the reviewed candidate and refresh its existing native hook trust through normal controls.
- [x] Execute one control/candidate pair, preserving per-phase source fingerprints and configuration receipts.
- [x] Independently assess source coverage, clipping/recovery, rereads, test outcomes and normalized usage.
- [x] Report package validation separately from observed native CLI execution, including limits and any unmet acceptance criterion.

## Completed validation and integration

The reviewed candidate was committed as `4296724`, with the pre-reader candidate retained at `ce1521b`. Package validation passed. Native CLI validation remains distinct from package checks.

A subsequent direct before/after comparison kept Vibe enabled in both arms with the same private installation identity, fixture, prompts, model and reasoning settings. RTK and other plugins remained unchanged. Each arm completed implementation, independent review and one real resume, passing six task tests and eight independent acceptance cases. Applicable Vibe instructions reached both fresh roles completely; resume reused retained current context.

Prepared loading removed the two observed outer truncation incidents affecting Vibe and required no Vibe recovery rereads. It used 13 reader calls. Fresh input changed from 82,888 to 81,966 tokens; cached input from 989,312 to 1,418,880; output from 11,996 to 12,420; elapsed time from 466.173 to 422.290 seconds. Model responses increased from 23 to 37. This single ordered pair establishes observed delivery reliability, not a reliable net efficiency gain. External Graphify child-output clipping persisted and was not changed by this work.

Integration preserves newer onboarding and naming changes from `b0a2fd3` and regenerates the prepared map against their current instruction sources. No live package installation, task repin or external-plugin change is part of the merge. Reducing model round trips is a separate follow-up assessment.

Pre-merge validation also corrected two stale startup tests that expected managed runtime pointers from basic SessionStart guidance. The tests retain the 150-word limit and unchanged runtime-store checks, and explicitly exercise the managed overflow guard. Production startup semantics are unchanged.

The broader 172-test run initially reported 169 passes and three failures. Two were those stale startup expectations. The unchanged state test `test_symlink_paths_missing_database_and_read_only_status_are_safe` remains a known baseline failure: its patch-denial expectation conflicts with the current retained-state fallback. Independent tracing established that this path fails before fingerprint evaluation and is unchanged from `b0a2fd3`. Its assertion remains intact; a separate state-guard/contract reconciliation is required. The full repository suite was interrupted after slow, unrelated lifecycle cases; no fully green repository-suite claim is made.
