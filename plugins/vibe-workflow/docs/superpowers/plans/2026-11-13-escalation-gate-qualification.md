# Escalation Gate Qualification Implementation Plan

> **For agentic workers:** Use `executing-plans` for this validation campaign. The coordinator owns deterministic tests, native probes and integration; one fresh-context reviewer checks the whole result before merge.

**Goal:** Qualify remaining mechanical Escalation Gate paths on released 0.1.39, and test its controls and failure boundaries extensively without expanding product behavior.

**Architecture:** Retain the accepted gate design and its later review changes. Combine deterministic adversarial tests, actual native-session observations, and explicitly labelled fault injection. Keep evaluators, budgets and raw logs outside disposable agents' writable scope; verify observable effects independently.

**Tech Stack:** Existing Python standard library, Node's test runner, OpenCode 2.2.6 native sessions and supported plugin hooks. No dependencies or external inference service.

**Spec:** [Accepted gate design](../specs/2026-11-12-opencode-escalation-gate-design.md), especially its superseding plan-review changes; [shipped contract](../../../clients/opencode-escalation-gate.md).

## Global constraints

- Test the existing mechanical gate. Semantic judging and Smart Routing integration remain deferred.
- No product-code fixes, changed thresholds, user configuration changes, trust approval, or new billing route is authorized by this validation task.
- Preserve the coordinator's model, effort, subscription, permissions, primary branch and unfinished work.
- Use released runtime bytes, pinned by SHA-256, in disposable fixtures. No tests edit real project files.
- Distinguish unit assertions, instrumented native fault injection, actual CLI/TUI behavior and historical evidence.
- A failure remains a failure; no automatic retry of a case or reset of its allocation. Fixing evaluator preconditions before model dispatch does not create a new case.
- Native live allocation: at most 240 model requests, including child and auxiliary requests, and 60 minutes of live execution. Each case has one attempt and a fixed share below. Stop affected execution at the first limit. Native test-writing/review children are separate bounded workflow assignments, not hidden live probes.
- Only current native OAuth routes; no API-key route, fallback provider, billing activation, or credential copying. Test-local effort/model variation is necessary to exercise ranking and does not change coordinator/global choices.
- Message-reset and stop-stage permission tests apply only to declared synthetic canaries. They never clear a real task hold or approve hook trust.
- Fault injection and reload target only owned disposable plugin instances and locations. Never restart or reload the shared service or unrelated sessions.

## Review focus

1. A shell pipeline exiting zero must still trip on its actual complete failing unittest footer, not a permission error.
2. Historical output, wrapper options and expected failures must not create holds.
3. Child edits must share their root's holds; same-level, old, failed, interrupted or editing diagnoses must not release.
4. Both three-escalation cycles and the single architecture reset must reach the documented ask stage without silently resetting budgets.
5. Missing/lost event channels, delayed settings and cleanup must fail open without later accidental reactivation; configuration denies remain final.

## Task 1: Deterministic adversarial coverage

**Files:** Create `tests/opencode_workflow/adversarial.test.ts`; modify `tests/test_opencode_workflow.py` to include it. Do not change runtime files.

**Interfaces:** Consume existing `Gate`, settings, route comparison and hook exports. Produce bounded, reproducible tests and a private mutation-check receipt.

- [x] Run existing gate suites as baseline: 70 tests passed, zero skipped.
- [ ] Add missing meaningful boundary/property tests: independent roots on identical paths; stronger-than-every-level, not just latest; alias/multi-file behavior; read-only status; bounded state; malformed input/output. Avoid duplicating existing cases.
- [ ] Use fixed seeds/bounded iterations for generative checks. Mutation-check selected assertions against disposable source copies; never alter runtime in the task checkout.
- [ ] Run focused new suites, self-review, and hand over exact file claims and results. Report product findings without fixing them.

## Task 2: Native qualification matrix

**Files:** Private campaign scripts, observer and fixtures outside the repository; sanitized result fixture at `tests/fixtures/opencode_workflow/qualification-2026-11-13.json` after observation.

**Interfaces:** Consume released plugin path and current native API/tool/hook schemas. Produce ordered permission/tool/route/session observations, immutable fixture digests, per-case verdicts and aggregate budget usage.

- [ ] Reuse the previous native CLI runner where applicable. Self-test parsing, idle-time comparison, child-inclusive accounting and budget rejection before inference. Check plugin readiness through actual status, not a fixed sleep.
- [ ] Preflight exact check commands and allowed resources before relying on failure attribution. Record actual exit metadata and complete output.
- [ ] Run these separately isolated cases once, keeping passing/failed/not-run distinct:

| Case | Model-request share | Required observations |
| --- | ---: | --- |
| Masked failure and diagnosis | 30 | Three genuine failing piped unittest runs with exit zero; hold; root and same-level child denied; same-level review preserves hold; fresh stronger read-only child releases; subsequent edit succeeds. |
| Unexpected-success failure | 12 | Actual unittest unexpected success with masked exit creates the intended hold. |
| Negative output controls | 24 | Expected failures, historical/sample output, mixed viewers and ambiguous wrapper options do not create holds; ordinary fail/fix/pass stays usable. |
| Full escalation lifecycle | 90 | Three observed stronger diagnoses, architecture re-evaluation and one reset, three more escalations, stop-stage ask, rejected edit unchanged, exact canary approval starts fresh. Fixture-only ranked native routes. |
| Lineage, isolation and reset | 28 | Independent root unaffected; child/negated/synthetic prompts do not reset; declared root user message clears only selected held files; other held file stays denied. |
| Reload and feature controls | 16 | Reload loses memory as documented; disabled/invalid settings enforce nothing; custom check patterns take effect only at load; no status registration while inactive. |
| Interactive TUI | 24 | Actual TUI prompt and native tool activity; root/child event order and child's permission identity observed, not inferred from API-only CLI behavior. |
| Fault injection | 16 | Native disposable instance loses its gate event stream before/after deferred feature resolution; internal hook error and cleanup allow actions and expose expected status. Label instrumentation explicitly. |

- [ ] Check every protected original and final canary independently. Confirm all owned sessions/processes idle or stopped; retain uncertain writers rather than taking over.
- [ ] No claim that documented limitations are defects fixed by testing: file-based false holds, moving-file misses, shell/API bypasses, memory loss and fail-open behavior remain explicit.

## Task 3: Evidence, checks and delivery

**Files:** `docs/qualification/2026-11-13-opencode-escalation-gate.md`, the sanitized result fixture, and the existing client contract's qualification-status paragraph if new evidence warrants it. No new feature or portable model policy.

**Interfaces:** Consume accepted case observations and test results. Produce independently reviewed, snapshot-bound evidence with reproduction instructions and honest limits.

- [ ] Record case expectations before execution; preserve failed preconditions/runs and distinguish instrumentation from unmodified released behavior.
- [ ] Review changed tests and evaluator claims independently. Recheck raw denial/status and file effects, not model self-reports.
- [ ] Validate and commit the candidate; run relevant suites and one full suite on an unchanged committed candidate using canonical private `TMPDIR`.
- [ ] Publish a draft `[BLOCKED]` PR; promote/merge only with current checks and independent review. Preserve any concurrent main changes.
- [ ] Synchronize clean primary without changing its branch. Apply cleanup only through its accepted scope, ownership and eligibility checks; keep required evidence outside removable resources.
- [ ] Report package checks, native observations, fault-injection evidence, failures and remaining limitations separately. Stop when the matrix is proved or remaining work has a real blocker/budget limit.
