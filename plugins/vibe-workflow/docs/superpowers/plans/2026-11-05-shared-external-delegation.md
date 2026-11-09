# Shared External Delegation Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this approved plan task-by-task. The assigned Astra/high builder is the sole writer; no further delegation or commits.

**Goal:** Reuse scoped external permission for bounded coding proposals while preserving local priority and independent native acceptance.

**Architecture:** Extend existing management records with optional permission; bind repository text through one shared packet helper. Existing OpenAI admission and OpenRouter proposal/worker paths retain their executors, attempt records and accounting.

**Tech Stack:** Python standard library, existing unittest fixtures and local Markdown contracts.

**Spec:** [Approved design](../specs/2026-11-05-shared-external-delegation-design.md).

## Global constraints

- Baseline: fetched `origin/main`, `c4a4eb856b7c491d706148acb760bad066f4489c`; source contains Usage Routing and the OpenRouter scripts/settings implementation. No installed-cache reconstruction.
- Preserve explicit route choices, local wait/native fallback, existing approvals, no-ceiling choices, route-specific retries and uncertain accounting.
- Initial implementation scope excluded live inference, configuration activation, installation, Git mutation, release and publication. The user later authorized installation and live tests; that evidence is recorded below.
- Native preparation owns truthful prose provenance and secret/exclusion inspection. Hashes prove identity only.

## Review focus

- Legacy requests and saved settings must retain their existing meaning.
- Approved source must produce the actual packet; unrelated text cannot hide behind its manifest.
- Revocation and source changes must fail at the existing launch/grant boundary.
- Failed/congested local preference cannot become automatic external eligibility.
- Independent review remains necessary; no result or receipt is acceptance by itself.

### Task 1: Shared permission and packet binding

**Files:** `runtime/external_delegation.py`, `runtime/usage_routing.py`, `runtime/openrouter_settings.py`, `tests/test_external_delegation.py`.

**Interfaces:** `validate_permission(permission)`, `select(explicit, local_preferred, candidates, preference=None)`, `prepare(root, permission, parts)`, `check(root, permission, binding, *, service, mode, model, provider, effort, class_name, automatic, now)`.

- [x] Write behavioral tests for selection, reusable permission, bounded source/derived text and destination denial through public adapters; observe failures.
- [x] Add optional permission fields to existing configure paths, preserving omitted legacy state and existing spending policies.
- [x] Implement checked source construction and deterministic packet recheck without a new executor or ledger.

### Task 2: Existing adapter integration

**Files:** `runtime/usage_codex_host.py`, `runtime/usage_admission.py`, `runtime/usage_routing.py`, `scripts/openrouter_proposals.py`, `scripts/openrouter_workers.py`, focused existing tests.

- [x] Extend optional external bindings on OpenAI proposals and recheck at final grant.
- [x] Bind OpenRouter proposal begin to the assembled packet and add a final dispatch check using its existing attempt token; preserve source-free/manual legacy requests.
- [x] Apply scoped binding to copied-file workers at preparation and launch without changing OpenCode retry/permission ownership.
- [x] Prove changed source, revision, local preference, missing review, duplicate and uncertain attempt behavior with disposable fixtures.

### Task 3: Contracts and verification

**Files:** `policies/03-agent-delegation.md`, `clients/external-delegation.md`, affected route/management references, `docs/policy-sources.md`, design and this plan.

- [x] Define shared selection/permission/provenance once with explicit loading triggers; keep entrypoints within 150 words.
- [x] Walk source-free legacy, scoped automatic source, local-priority and copied-file worker instruction paths.
- [x] Run focused existing suites and `python3 -B scripts/validate.py`; self-review the full diff.
- [x] Report exact candidate snapshot and checks. Package/fixture evidence stays separate from observed native/provider execution.

Plan self-review: all design sections map to the shared binding, adapter integration or retained route contracts. Existing accounting, supervision and visibility are reused. Independent final review belongs to the coordinator.


## Completion evidence, 2026-11-05

Implemented on `codex/shared-external-delegation` from the reconciled baseline above. The native Astra/high builder completed the source and focused regression changes. A separate native Astra/high reviewer inspected the full candidate, then reviewed the narrow corrections. All findings are resolved: filesystem aliases and linked permission areas respect source scope, OpenRouter uses the admitted checkout, and workers retain their existing 128 KiB source allowance.

- Final coordinator run: `python3 -B -m unittest tests.test_external_delegation tests.test_usage_codex_host tests.test_usage_admission tests.test_usage_routing tests.test_usage_live` ran 57 tests: 56 passed and one case-sensitive-filesystem scenario was skipped on this case-insensitive Mac.
- The coordinator also ran that coverage with six scoped OpenRouter public-adapter cases before the final two-line link correction: 62 tests, one filesystem skip, no failures. Those adapter files were unchanged by the final correction; independent review retained their coverage.
- The builder's full frozen-code OpenRouter proposal, worker and settings run passed 101 tests. After the first corrections, six scoped/legacy adapter smoke cases passed. Relevant changed paths received focused regressions; the unaffected broad suite was not repeated.
- `python3 -B scripts/validate.py` and `git diff --check` passed. All skill entry points remain within 150 words. Five instruction-loading walkthroughs covered local priority, source-free OpenAI, scoped OpenRouter proposals, copied-file workers and permission management.

Independent acceptance verified all 24 candidate file hashes against review-manifest SHA-256 `136c67c9334c4689e6fe1c1812bd32445d3338aff7e68221df7ee871ddedbc2c`. The coordinator added only these completion notes, checkmarks and the design's status/evaluation afterward. Runtime code, tests and contracts remain the reviewed bytes. Private reports remain with the coordinating chat.

At initial source completion, observed client/provider execution, live OpenCode qualification, billing and savings had not been tested. No route activation, source permission, installation, commit, release or publication was performed in that phase.

## Authorized installation and live tests, 2026-11-05

Installed the reviewed feature through Codex's native local marketplace controls as `vibe-coding-workflow@vcw-shared-external-candidate`, version `0.1.30`. The candidate starts from the newer installed stable `0.1.30`, retaining its concurrent Workstreams changes. Reviewed feature Python files are unchanged; only the shared delegation policy and source-coverage document required three-way merges. Package validation and the shared helper's six tests passed, with one filesystem skip. The original stable installation remains recoverable and disabled; only the candidate Vibe plugin is enabled.

A fresh Codex CLI `0.165.0` session reported both Usage Routing and OpenRouter worker skills in startup context. It exited successfully without startup warnings or tool calls. No model, reasoning, authentication or hook-trust override was used. This establishes fresh CLI skill availability, not Desktop reload, startup-hook qualification or automatic routing behavior.

One scoped OpenRouter worker, `shared-scoped-live-hx6ozxic`, used the existing approved `z-ai/glm-5.3-flash` / `z-ai/fp8` / low route through pinned OpenCode `1.19.5`. Only a newly created synthetic Python clamp file was copied. A permitted file read completed, the worker returned a correct one-line proposal, and local execution stopped. The coordinator reproduced the original defect and passed thirteen native cases. A separate native Astra/high reviewer independently passed those cases and accepted exact candidate SHA-256 `09f9d3af32141080362f5a480a3ceda398bd377a3996d5b44b8444cd9edf7847`. The runner now records native acceptance. One Markdown JSON fence was removed without semantic repair; strict JSON-only output was not met.

OpenCode reported 3,333 total tokens and USD `0.0001359501`; provider billing, downstream model/provider/effort and savings were not independently established. The Habblaud child and initial transport delivery were observed; the final accepted-phase delivery was unconfirmed, and rendered UI was not inspected.

Removed the synthetic source and temporary source permission. OpenRouter's prior enabled workers route, runtime/auth references and consent version were restored through public controls at revision 11, retaining history. Both providers have no standing external source permission. The local-priority selection check retained `local_wait_or_native_fallback`; no local assessment approval was expanded. Private installation, rollback, execution and review receipts remain with the coordinating chat.

## API admission verification, 2026-11-05

The foreground host and canary bind authorized tasks to current funding, pricing, source consent and a distinct native reviewer. API reservations, uncertain attempts and historical accounting remain in the existing ledger. Local preference and native acceptance retain their existing owners.

Reconciled the candidate with current `main`, `7dd2bdb7046c80e1ccd046986213353bad830774`, retaining its newer role and workflow guidance. The coordinator's focused host, bridge, admission, budget, canary, live, Smart Balance, settings, OpenAI and shared-source suites ran 156 tests: 155 passed and one filesystem-specific case was skipped. The full OpenRouter proposal, worker and settings suites passed 104 tests. Package validation and whitespace checks passed. Independent native Astra/high review accepted the complete forty-file candidate with no material findings. Only these execution notes were updated afterward.

The installed foreground host then completed one source-free synthetic OpenAI API proposal using the existing `gpt-6.1-sol` / medium profile, Keychain connection, Smart consent and approved benefit funding. Count and generation completed once; the API slot is clear and native task completion is recorded. The exact proposal required no formatting or semantic repair. Thirteen coordinator cases and 748 independently executed native review cases passed. The conservative recorded generation cost is USD `0.001485`; actual provider billing and savings were not independently reconciled. No repository files were transmitted by this API test. Smart Balance remains live-enabled at revision 33 with its funding choices intact.
