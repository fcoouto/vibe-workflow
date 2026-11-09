# D7: Repository merge assurance

Implementation plan · 26 October 2026 · Bounded fixture inspector implemented; production publishing and repository enforcement remain unqualified.

**Goal:** require a trusted final verification result before a supported repository merge. D5 establishes local completion evidence; D6 reconciles managed writers; D7 makes the repository enforce accepted merge requirements. Behavioral qualification and usage reporting retain their [D9 owners](onboard-my-project-plan.md#d9-qualification-usage-and-pilot).

Follow [Merge Readiness](../policies/08-merge-readiness.md), [Code Review](../policies/06-code-review.md), [Security Review](../policies/04-security-review.md) and [Tests and Builds](../policies/05-tests-and-builds.md). Use standard-library Python and existing evidence formats. No new database, agent launcher, billing route or automatic project adjustments.

**Approved follow-up, not current enforcement proof:** D7 owns per-target observations in `runtime/repository_gate.py`/`scripts/repository_gate.py` and the corresponding `clients/repository-gate.md` procedure for [linked delivery](onboard-my-project-plan.md#cross-repository-tasks-and-delivery). Depend on canonical repository identity, complete D5 companion links and D6 reconciliation. Derive delivery status without another gate/orchestrator or transferred merge authority. Acceptance keeps a required unmerged/unverifiable companion incomplete while preserving each target's authorization, including a manual-merge preference where configured. PHIOON's preference is project-specific. Current fixture inspection/history below proves neither this aggregation nor repository enforcement.

## 1. Establish the trust path

**Owner: coordinator with repository-gate worker.** Recheck repository access and capabilities before choosing the publisher. The [D5 preflight](d5-validation.md#settings-and-limits) recorded no workflows/rulesets and an unprotected `main`; it did not qualify a trusted publisher, protected verifier or local-review transfer. This checkout still has no `.github` workflows. Treat recorded remote observations as dated evidence, not a current protection claim.

Freeze these inputs before implementing the publishing integration:

- Repository/target identity, supported merge methods, queue use and effective bypass permissions.
- Accepted policy/check versions, candidate/base/queue identities and freshness/revocation conditions.
- Qualified server check/reviewer sources, final-check publisher and a verifier definition that the candidate cannot replace.
- Exact publishing permissions, deployment/configuration files and the disposable qualification scope. Reuse an existing authorized mechanism; new accounts, credentials or paid services require their own authorization.

Prefer existing authoritative server checks and qualified repository review. A check name or GitHub Actions app identity alone may not distinguish a protected verifier from a candidate-controlled workflow using the same name. Qualify enforceable source binding: a required protected workflow where supported, or an existing trusted publisher with a protected verifier. Do not assume either is available on the target account.

D5's native receipts and coordinator assessments are local evidence. Accept their transfer only through a qualified issuer that verifies native provenance, review coverage, findings, candidate binding and freshness. Uploading, hashing or signing caller-supplied JSON does not establish those facts. If neither qualified repository review nor an approved transfer path can satisfy required coverage, dependent enforcement remains blocked; pure verifier/fixture work may continue.

### Production source bindings

Before source integration, accept owner-issued interfaces for canonical/server target acquisition and D5/D6 evidence acquisition. Bind the qualified local scope to independently observed immutable server repository/owner IDs, target, and per-target adoption/merge authorization. Caller JSON, projected scope and artifact hashes cannot establish that authority.

| Evidence | Preserve these plan bindings |
| --- | --- |
| D5 candidate/check/review | Applicable D5 plan, including a native review's separate `body.review.d5_plan` link. |
| Native worker/reviewer assignment | Original D6 `body.plan` reference, ledger `plan_digest`, and lifecycle `d6_plan_digest`; retain D5 context separately where applicable. |
| Coordinator operation | Each applicable original `body.d5_plan`/`body.d6_plan` and corresponding proof digest. Do not manufacture D5 context for a task-only operation. |
| D7 verifier/adoption | Separate accepted protected D7 definition; no uniform equality with D5/D6 plan digests. |

Validate original owner, assignment, dispatch, candidate, pins and reconciliation links. Settled historical writers retain their original plans/revisions; never rewrite them to current plans or count their old candidate as current passing D5 evidence. Mapping acceptance must use distinct D5/D6/D7 digests, an admitted native reviewer and a settled earlier writer.

The selected source must qualify native provenance, applicable check/review evidence and actual stopped-writer/descendant observations. Qualify an owner-controlled admission boundary over participating authorities through the selected publication/merge operation; bind and revalidate revisions, generations, pins and committed head/base/tree. Changed, expired or revoked inputs invalidate acquisition. Settled completion artifacts do not provide that boundary. Keep database transactions short across network calls.

These are production integration prerequisites, not changes to existing fixture envelopes or proof of available owner APIs. Protected verifier/publisher custody, credential isolation and server target/base race handling remain separately unqualified. A local admission boundary cannot freeze GitHub state. No merge-controller interaction, permission change or operational probe is authorized by this mapping.

**Exit evidence:** read-only capability report and one selected trust design, including its unsupported paths. This is a real prerequisite, not a reason to weaken required review.

## 2. Verify current repository evidence

**Owner: repository-gate worker.** Add `runtime/repository_gate.py`, `scripts/repository_gate.py` and `tests/test_repository_gate.py`. Keep the module independent of D6's in-progress state/CLI edits. Expose one bounded verification operation and machine-readable blockers; consume the agreed D5/D6 evidence contract without copying their mutable state or creating another task-completion authority.

The protected runner obtains authoritative observations through the selected source. Validate repository, immutable candidate/base/queue identity, policy/check pins, issuer/run/workflow provenance, review coverage/dispositions and required writer reconciliation. An input record cannot select its own trusted issuer or policy. Requery relevant native state before publication; changed inputs invalidate the result.

Require explicit successful prerequisites. Reject missing, failed, cancelled, skipped, neutral, timed-out, stale or untrusted required evidence. GitHub may otherwise treat skipped/neutral checks as acceptable, so the final verifier must evaluate prerequisite conclusions itself. It must execute after failed prerequisites and must not be skippable into success. [GitHub required-check behavior](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks).

Run verifier code, policy and source configuration from the protected trusted reference. Candidate tests run separately with minimal permissions; the publishing context never executes candidate code or trusts candidate-controlled artifacts without verified provenance and binding. Do not interpret a submitted report as executable configuration. [GitHub workflow security](https://docs.github.com/en/actions/reference/security/secure-use).

**Proof:** test-first cases reject altered candidate/base/policy, fabricated issuer/reviewer, incomplete findings disposition, unresolved writers, replayed evidence and substituted verifier configuration. Accept one complete current record. These fixtures prove decisions, not repository enforcement.

## 3. Qualify enforcement, then propose live rules

**Owner: qualification worker; independent security reviewer verifies the trust boundary.** Use a separately authorized disposable repository with the selected publisher and rules. Require its final check through repository controls and inspect effective source/bypass settings. If a queue applies, qualify its actual combined candidate and `merge_group` handling. [GitHub rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets).

| Probe | Required observation |
| --- | --- |
| Missing/stale/failed review or check; cancelled/skipped/neutral prerequisite | Final verification cannot succeed; merge is rejected. |
| Candidate, base, policy or queue composition changes | Prior evidence cannot authorize the changed merge. |
| Forged report/check, same-name alternate workflow or replaced verifier | Candidate cannot manufacture the trusted final result. |
| Supported UI/API/CLI merge paths and direct target updates | Required gate cannot be escaped through an unexamined path. |
| Bypass roles and queues, where applicable | Effective exceptions are observed and explicitly scoped; an unqualified bypass prevents a universal enforcement claim. |
| Legitimate current candidate | Final verification succeeds, authorized merge succeeds, resulting repository state matches the accepted candidate/method. |

Record source/run IDs, exact revisions, effective rules, observed rejection/acceptance and resulting commits. API errors or lack of permission are not proof a gate rejected the candidate. Missing queue support is a scoped limitation, not a simulated pass.

Present the exact live ruleset/settings diff, verifier source/permissions, supported paths, recovery procedure and disposable proof for separate review and authorization. Apply only that accepted configuration and observe the resulting controls. Planning or fixture success grants no live repository mutation permission.

## 4. Parallel delivery with D6

D6 retains `runtime/evidence.py`, `runtime/state.py`, `runtime/startup.py`, `runtime/changes.py`, `scripts/workflow.py` and their assigned tests until its reviewed handoff. D7 consumes agreed identities, pins and reconciliation outcomes; any necessary changes to those shared files form one later coordinator-owned integration task.

| Task / owner | Writable scope | Dependencies / resources |
| --- | --- | --- |
| G0 / coordinator + gate worker | This plan; private read-only capability/trust report | Existing D5 evidence; no repository mutation. Freeze the selected publishing integration's exact files before dispatching that work. |
| G1 / gate worker | New `runtime/repository_gate.py`, `scripts/repository_gate.py`, `tests/test_repository_gate.py` | Agreed D5/D6 fields and trust contract. Unique temporary fixtures; pure logic may overlap D6. Publishing transport waits for G0's qualified choice. |
| G2 / guidance worker | New `clients/repository-gate.md`; `clients/evidence.md`, `scripts/validate.py`, `tests/test_hook_manifest.py`, source map | G1 reviewed and D6 guidance ownership handed off. Unique package copies; no simultaneous edits to shared guidance/validator files. |
| G3 / qualification worker | New `docs/d7-validation.md`; private probe artifacts; exact G0-approved disposable integration files | Reviewed G1/G2 and required D6 lifecycle evidence. Dedicated disposable repository, credentials/permissions and runner resources; no live target changes. |
| G4 / coordinator | Exact separately reviewed live rules/settings | G3 negative/positive evidence and explicit configuration scope. Observe enforcement after application. |

Every implementer owns its tests and self-review. Follow D6's test-first and per-wave review discipline; independent reviewers do not author the reviewed implementation. Pause all package writers for shared-checkout checks because runtime additions change D6 fingerprints. Use immutable isolated copies only when their benefit justifies the setup. Keep Git and shared planning records coordinator-owned.

For this development run, select implementation and independent review workers through the [delegation contract](../policies/03-agent-delegation.md#select-capability-and-dispatch), using applicable cached platform guidance, task risk and available controls. Security and final integration reviews retain independent contexts; no model or reasoning tier is mandatory for every review. Preserve coordinator settings and existing sessions.

Run focused tests, the complete unit suite, package validation and diff checks after integration. Review each completed wave before dependent work, then verify the combined D6/D7 boundary. Report package tests, native lifecycle proof, disposable repository enforcement and live adoption separately.

**Completion:** qualified repository controls reject invalid candidates and accept legitimate candidates through every claimed path. G0 trust gaps and unavailable required controls block their dependent claims. D6 and G1 can develop in parallel; final enforcement qualification cannot skip their integration.
