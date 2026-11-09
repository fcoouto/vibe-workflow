# Workflow Templates Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans for authorized execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing native behavioral checks reusable from a local workflow package, starting with stale-handoff repair and read-only review.

**Architecture:** Bundle the small intentionally broken Toolkit fixture and its seven-check evaluator. Keep original inputs and checks outside each worker's writable copy. Link one selected procedure from the existing maintenance owner; reuse Phase 2 inputs and observations without another model campaign.

**Tech Stack:** Markdown, Node's built-in test runner and existing Python standard-library tests. No added dependency or runtime service.

**Spec:** [Workflow templates and behavioral verification, Phase 3](../specs/2026-11-05-workflow-template-design.md#phase-3--make-native-behavior-evaluation-reusable).

**Status:** Implemented and qualified within the scope recorded below on 5 November 2026. Phase 2 merged through [PR #104](https://github.com/fcoouto/vibe-coding-workflow/pull/104), commit `b01a94a884d21c67a6b284ed32c400992825daa1`. The user subsequently authorized Phase 3 integration, tracked in [PR #105](https://github.com/fcoouto/vibe-coding-workflow/pull/105). The saved combined-outcome execution method was preserved; release and installed rollout remain outside this merge.

## Global Constraints

- Retain the exact current worktree; start from merged `main` and preserve newer changes during integration.
- Use Toolkit revision `21b71b68679c11184f49c23168852014bf6dde8b`, already retained for this investigation. Copy only adopted fixture/evaluator material, retain its MIT license, and write local instructions from requirements. No routine Toolkit fetch.
- Keep one maintenance owner, one lifecycle/report owner and the existing role/title procedures. No new mandatory form, schema, status store, hook, spawn blocker or declaration step.
- Preserve coordinator model/reasoning/authentication, permissions, manual titles, active chats and user configuration. Keep the unresolved API run and its reservation on hold. No provider calls are needed for this phase.
- Use credential-free disposable copies outside the repository. Original inputs, accepted checks, package sources and unrelated project files are not writable task resources. Record cooperative scope honestly; a directory boundary is not a sandbox.
- Run one repair attempt and one independent read-only review attempt, each bounded to ten minutes. Stop at its result or limit; retain failures without automatic retries. Reuse unchanged Phase 2 evidence.
- No release archive, installed rollout, client restart or global hook/cache edit. Native observations remain separate from package and evaluator tests.

## Review Focus

1. Same user ID across tenants must remain an expected baseline failure and pass only after a real repair — Task 1 evaluator controls; Task 3 repair.
2. Missing, empty or non-string identities must deny access; valid ownership, absent inputs and frozen inputs retain their passing behavior — Task 1 controls.
3. Changed local notes or unrelated instructions must prevent an unsupported preservation claim — Task 1 notes control; Task 3 independent diff.
4. Installed package resolution and a different working directory must not require a Toolkit checkout or resolve a worker-controlled expected file — Task 1 relocation test; Task 2 procedure.
5. Review-only work and reused historical observations must not be reported as repaired code or freshly observed client behavior — Tasks 2 and 3 evidence disposition.

## Ownership and size

Treat Tasks 1–2 as one tightly coupled outcome, with one Builder owning assets, focused tests and guidance. The coordinator owns provenance, prepared maps, qualification resources, acceptance and Git. Reuse the existing Astra/ultra Reviewer for one integrated source review; review only substantive later deltas.

Use fresh bounded native contexts for Task 3's repair and review cases so neither receives the intended fix or the other's answer. Each gets only its task, original input, accepted behavior, package instruction path, permitted resources and private report destination. Normal workflow-owned role and presentation metadata are allowed through their existing procedures. Keep project-code restrictions separate from those metadata effects; bind the known family before any title qualification. Use the saved execution method rather than opening another choice of process.

Routine development gains one conditional maintenance link. Added evaluator code is the reviewed upstream script with its expected-notes path adapted. Keep reusable inputs under the existing `assets/` pattern as ordinary files and small overlays; add no fixture serialization or materializer. Reject an extra runner, generalized fixture loader or automatic model grader unless a concrete missing consumer requires it.

## Task 1: Package and prove the boundary fixture

**Files:** Create `scripts/validate-harness.mjs`; `assets/harness-evaluation/boundary/access.mjs`, `AGENTS.md`, `local-notes.txt`; `assets/harness-evaluation/LICENSE`; and `tests/test_harness_evaluation.py`. Minimally extend the existing required-file checks in `scripts/validate.py` for bundle completeness; ordinary package validation must not acquire a Node prerequisite.

**Interfaces:** Preserve `canReadRecord(actor, record)` and `node <package>/scripts/validate-harness.mjs <disposable-fixture-directory>`. Exit zero means all seven checks passed. Nonzero requires inspection of the individual results; it is not automatically the expected broken baseline. Resolve expected notes from `../assets/harness-evaluation/boundary/local-notes.txt`, relative to `import.meta.url`.

- [x] Record the pinned source paths and hashes before adopting bytes. Retain the deliberately insecure `access.mjs` and exact local notes as baseline inputs. Write concise local fixture instructions that use the evaluator path supplied by the coordinator, without requiring Toolkit or copying its prompt.
- [x] Add `HarnessEvaluationTests` using `unittest`, `TemporaryDirectory`, `shutil.copytree` and bounded `subprocess.run`. Exercise the actual Node evaluator; do not emulate its assertions in Python.
- [x] Before implementation, run `python3 -B -m unittest tests.test_harness_evaluation` and confirm failure due to the absent evaluator/assets. Missing Node is an unavailable prerequisite, not a passing check; do not install it implicitly.
- [x] Adopt the seven-check evaluator. Change only the protected reference-note path needed by this package layout. Keep checks outside worker-writable copies and preserve the original public signature.
- [x] In `test_broken_baseline_has_only_expected_failures`, require seven checks: exactly the cross-tenant and invalid-identity cases fail, and the other five pass. Retain their identities in failure diagnostics.
- [x] In `test_correct_control_passes_all_seven`, use a private test-only correct control and require all seven checks to pass. In `test_changed_notes_fail_preservation`, change only copied notes after that control and require its preservation check to fail. Neither control supplies an answer to native qualification workers.
- [x] In `test_relocated_package_uses_protected_notes`, copy the evaluator and bundled reference subtree into a temporary package root. Run from a different working directory against a separate disposable fixture; require the same expected baseline results and unchanged original inputs. A missing fixture or invocation argument must return nonzero without rewriting controls.
- [x] Run the focused module and `python3 -B scripts/validate.py`. Verify missing bundled controls fail package completeness through the existing local-file check, without executing Node. Inspect the diff: no extra dependency, application authorization change or new evaluation framework.

## Task 2: Connect selected maintenance and retain reusable cases

**Files:** Create `clients/harness-evaluation.md` and `assets/harness-evaluation/cases.md`. Under `assets/harness-evaluation/phase-two/`, create `minimal/input/{README.md,AGENTS.md,arithmetic.py,check.py}`, `minimal/expected-final/{README.md,AGENTS.md}`, `customized/input/{README.md,AGENTS.md,check.py,handbook/overview.md,handbook/engineering.md,handbook/ARCHITECTURE.md,src/AGENTS.md,src/cleaning.py}`, and `customized/expected-after-partial-undo/handbook/engineering.md`. Modify `policies/09-workflow-maintenance.md`, `docs/policy-sources.md` and `clients/instruction-map.json`. Update this plan and the design's status links. Extend `tests/test_harness_evaluation.py` only for meaningful packaged-input checks, not model behavior.

**Interfaces:** The existing maintenance policy conditionally loads the local case procedure. The procedure resolves `<package>` from its actual loaded source, copies only selected input files into a disposable directory, and supplies the exact absolute evaluator command. Expected directories are overlays over their initial inputs, inheriting unchanged files. `cases.md` describes inputs, observations and limits; it is no runtime request, status store or competing policy.

- [x] Write the selected procedure: preflight known baseline failures, unchanged protected inputs, writable scope and private report destination; run the chosen native case; independently check its result and diff; retain useful proof; clean only owned stopped resources. Link existing maintenance, handoff, delegation and title owners rather than repeat their rules.
- [x] Describe stale handoff as a repair task despite an inaccurate completion claim. Its success requires real code inspection, the repair, all seven checks and preservation. Describe read-only review separately: the original broken fixture stays byte-identical and its supported finding goes only to the supplied private destination. Its two expected baseline failures remain present.
- [x] Recover the twelve O1/O2 initial files and three changed document overlays from retained Phase 2 input manifests, preimages and final snapshots. Package ordinary files in the paths above; inspect contents and replace private absolute roots or execution identities with explicit substitutions only where present. Retain meaningful commands, custom paths, nested rules, accepted checks and later-user-edit examples. Exclude raw transcripts, accounting, live provider IDs and screenshots. O2's retained guide is after partial undo, not a fully applied or fully undone state.
- [x] Add a case table for reapplication, later-edit/partial undo and lifecycle/reporting. Name initial input, accepted observation, existing owner and evidence limits. Preserve title start/checkpoint/completion, silent unchanged checkpoints, acceptance before dependency release, manual pauses, uncertain ownership and remaining budgets. Distinguish recorded Phase 2 proof from a future fresh run; package no fabricated native receipt.
- [x] Verify packaged contents against their retained sources and reconstruct both O1/O2 inputs and overlays in temporary storage; independently run their accepted local checks with the expected initial or repaired disposition. This validates portability, not another native onboarding or lifecycle qualification.
- [x] Add only the selected-procedure pointer to the existing maintenance owner. Record pinned upstream paths, adopted bytes, local adaptations, license and evidence scope in the existing source map. Reconcile duplicate or stale guidance only within this slice; preserve original fallbacks and unrelated validators.
- [x] Run `python3 -B scripts/read_instructions.py --prepare`, `python3 -B -m unittest tests.test_harness_evaluation tests.test_prepared_instructions`, `python3 -B scripts/validate.py` and `git diff --check`. Freeze the complete candidate and obtain the integrated Astra review before native qualification.

## Task 3: Qualify the two new native cases and accept the slice

**Files/resources:** Private evidence and two independently copied boundary fixtures under the existing investigation directory. Repository sources, controls and native histories remain coordinator-owned; no real project enrollment is required.

**Interfaces:** Consume the reviewed candidate from Tasks 1–2. Each native task returns the existing compact report: result, candidate/proof, material exception and next owner. The coordinator independently reruns the protected evaluator and checks before/after bytes. A worker's successful report grants no acceptance or integration authority.

- [x] Preflight actual native capability and settings, selected package discovery, baseline identities, scope and budgets. Record unavailable controls honestly. Prepare each full assignment once through the existing role owner; fresh contexts must not inherit protected answers or earlier repair findings.
- [x] Run the stale-handoff case against one broken copy. Supply an inaccurate completion claim and the actual task/requirements, without the solution. Observe the worker checking real state and repairing it. Independently require all seven checks to pass and only `access.mjs` to change; preserve local notes and instructions byte-for-byte.
- [x] Run the read-only case against a separate original copy. Require a supported reachable finding with a concrete reproduction in the private report. Independently require every fixture byte unchanged and the same two expected baseline failures. Do not repair this copy to make its evaluator green.
- [x] Inspect relevant native instruction/tool actions where available, private artifacts, actual commands, identity/settings and ownership. Record elapsed time and usage only when exposed. File tests cannot establish pause/resume, external sends, fresh Desktop startup or first badge rendering; do not rerun providers or unchanged Phase 2 cases for those claims.
- [x] Reuse accepted Phase 2 evidence with its original input/snapshot bindings and limits. Explicitly retain the constrained Desktop trial, later successful corrected case and routed output rejection as history. One smoke pass is not a reliability rate or token-savings measurement.
- [x] Disposition all selected criteria. Fix only demonstrated slice defects; a larger runtime issue becomes separate scope. Reuse matching deterministic evidence unless a substantive change invalidates it. Record any review delta, integration readiness and remaining capability limits in this plan.
- [x] After all owned writers stop, retain useful inputs/results and remove only disposable copies. Keep role contexts, private proof and unresolved accounting. Commit the completed reviewed slice; compare against then-current `main` before proposing integration. Phase 2's merge authorization is not a release or installed-rollout instruction.

## Acceptance and rollback

Accept packaging only when protected baseline controls reject exactly the two known defects, correct controls pass seven checks, relocation works and retained inputs reconstruct. Accept each native case only under its own disposition above; expected failures in the unchanged review fixture are successful negative evidence, not repaired behavior.

Rollback removes this slice's assets, test module and conditional pointer, then regenerates the prepared map. Preserve any later user edits, existing maintenance/handoff/role controls, historical evidence and the held API state. No runtime migration is involved.


## Accepted results and limits

The combined Builder outcome adopted twenty asset files, the direct evaluator, one selected procedure, focused tests and the existing package-completeness checks. Twelve initial Python-project files and three document overlays match retained Phase 2 contents exactly; no private-path substitutions were needed. Original boundary code, notes and MIT license match pinned source bytes. The evaluator differs only by its protected expected-notes URL. Ordinary maintenance gains one conditional pointer; no runner, fixture format, runtime service, hook or role blocker was added. Token savings were not measured.

All 46 selected deterministic tests passed: nine new harness tests, thirty prepared-instruction tests and seven existing package/hook tests. The test-first missing-evaluator control failed as intended. An initial test-only reporter mismatch was corrected using Node's existing TAP option; it is not reported as a clean first attempt. Root independently verified source identities and all twelve initial files plus three overlays. Package validation and whitespace checks passed. The packaged boundary remains deliberately broken, with exactly five passing and two failing checks.

Astra/ultra reviewed source tree `f1f8dce09e35fcb307a77ec6992adbffee20d0a4` against merged main. Its one P2 finding was resolved in `1c316f0e902fba72818e2de07995638b6f331fc1`: boundary preflight applies only to selected boundary cases, and campaign budgets remain in this plan. A Python-only case needs no Node check or counterpart boundary task. The affected thirty prepared tests and package checks passed after that delta; unchanged harness/hook evidence was reused.

Two fresh native Sol/high cases then consumed the frozen 358-file candidate at `1c316f0e902fba72818e2de07995638b6f331fc1`, with full assignment preparation, independent writable copies and no answer samples in their briefs. Native metadata independently confirmed the requested model and effort. The repair took 126.646 seconds; the read-only review took 155.045 seconds, both within the ten-minute bounds.

- Stale handoff: the worker inspected actual code and observed the two baseline failures before changing only `access.mjs`. Root reviewed the pure predicate and independently reran all seven checks successfully. Instructions and notes remained byte-identical.
- Read-only review: the worker reported both reachable tenant/identity defects with executed reproductions. Root verified unchanged fixture bytes and independently reran the same five-pass/two-expected-failure baseline. Expected failures remain appropriate evidence for this case.

All 358 protected candidate files stayed unchanged. Both contexts encountered rejected asset selections through the prepared instruction reader and recovered complete source text through bounded native reads before case work. The owning procedure now states that selected assets are data outside that map. Astra accepted this narrow recovery recipe and its exact generated entry in source tree `b3d0024eed6c598056b42d0d0a9148379ce432db`; no further model campaign was justified. The affected thirty prepared tests, package and whitespace checks passed. Native proof remains bound to the earlier candidate, not silently promoted to a clean first-load result for this later wording.

The child surfaces lacked verified native identity/title readback, so titles were preserved. Explicit candidate loading does not establish fresh Desktop startup, installed activation, rendered first badges, OS isolation or general reliability. The evaluator executes candidate JavaScript; independent inspection and bounded processes remain necessary. Reused Phase 2 observations retain their original bindings and limits; providers and unchanged native cases were not rerun.

Private manifests, source deltas, non-reasoning native call records, exact final fixture bytes, independent results and review dispositions remain under the investigation's `phase-three-execution/` directory and adjacent review reports. Only owned disposable candidate/case copies were removed after native owners completed. The unresolved API run and retained reservation were preserved; no active chat, settings, receiver, hook or installed package was changed. The user subsequently authorized integration through [PR #105](https://github.com/fcoouto/vibe-coding-workflow/pull/105). Release and installed rollout remain separate scope.
