# D5 checks and review validation

26 October 2026. D5 implementation, independent review and disposable native acceptance are complete for Codex CLI 0.163.0 and local Codex Desktop 26.1021.31345 with bundled Codex 0.164.0-alpha.11.1. Desktop qualification covers automatic startup orientation and the D5 checks/review/completion lifecycle below. Repository merge enforcement remains unqualified under D7. Native operation remains the default.

## Implemented behavior

The [evidence contract](../clients/evidence.md) exposes accepted check plans, current-content capture, bounded execution, check lifecycle reconciliation, native review preparation/collection/assessment and completion verification. Only the verifier can set `ready`. Missing, stale, failed or unreconciled proof and blocking findings prevent readiness. Current content is checked even when no hook reports a change.

State uses the existing SQLite tables and private immutable artifacts. Historical unresolved attempts remain blockers across task replacement and cancellation. Scoped retirement preserves old pins, conditions, evidence and history; it does not migrate active tasks or manufacture success. Check execution observes results but cannot prove that detached descendants stopped. Passing evidence therefore also requires exact-attempt host reconciliation whose underlying observation the coordinator independently verifies. JSON validates bindings, not host authority.

Codex encrypts native delegation messages, including those exposed to tool hooks. The qualified collector instead correlates a unique prepared task marker with native parent activity, child origin and checkout, then fetches the completed final report. Caller-supplied reviewer identities or report markers alone cannot establish provenance. The collector uses only bounded read-only history calls; it creates no turns, workers or new hooks. This refines the plan's original plaintext-prompt assumption while preserving the required identity and attempt correlation.

## Verification

| Check | Result | What it establishes |
| --- | --- | --- |
| Final full unit suite | 253 passed in 148.894 s | Existing behavior plus evidence, state, CLI and native-protocol regressions, including both qualified native versions and version mismatch rejection |
| Package validator | Passed; skill entries remain 149/143 words | Package structure and instruction budgets, not client execution |
| Independent general/security review | Two P1 findings fixed; scoped re-review clear | Final transaction checks concurrent reservations; detached command children cannot silently produce passing evidence |
| Independent fix verification | Six affected tests passed | Both reproduced defects and exact-proof/public-control regressions |
| Instruction walkthroughs | Seven scenarios passed | Applicable procedures load; unrelated onboarding material stays unloaded |
| Native collector qualification | Positive, duplicate and mismatch cases observed | Actual native identity/result correlation, separate from mocked protocol tests |
| Final public CLI/native lifecycle | Passed on exact frozen runtime/test/script hashes | Real check reconciliation, independent review, completion and subsequent stale-evidence rejection |
| Installed Desktop lifecycle | Passed on the frozen installed candidate | Actual startup context, failed/missing evidence blockers, native Luna review, readiness and rejection after an outside-hook edit |

The existing nonfatal SQLite `ResourceWarning` remains. No skill entry point changed. Changed guidance links and whitespace checks passed. Source/test hashes were checked independently against the final acceptance copy.

## Disposable native acceptance

A synthetic arithmetic project used actual task state and public CLI controls throughout. Direct requests to set `ready` were rejected. Failed and successful command exits both remained pending until host reconciliation; a reconciled failure stayed failed. Required missing reviews also prevented completion.

The executor independently observed its owned command lineage, matched the actual check PID, confirmed observed processes stopped and inspected the simple assertion command's lack of child creation. That evidence supports this fixture, not arbitrary commands or a general process supervisor. Host proofs used public outputs and retained immutable bindings; no private hash helper was needed.

One native Sol6.1/high coordinator and one Luna/medium reviewer supplied a fresh state-bound review. No prepared-binding helper was patched and no receipt was fabricated or reused. The coordinator assessed actual general/security coverage, then completion reached `ready` without merge authority. An external source edit with no hook event changed status back to `verifying`; verification rejected stale checks, reviews and security impact. Generation remained zero, demonstrating actual-content invalidation.

All 29 recorded commands returned their expected exits. Native model, provider, effort and permission settings were observed; authentication was inherited without modification. All owned processes stopped.

## Disposable Desktop acceptance

The user installed the isolated local plugin and approved its five hooks. All 70 cached package files matched the frozen candidate; a read-only observer found each exact hook enabled and trusted. The actual Desktop task then received native harness-marked hook context before its first tool, identifying the installed package, fixture and session. The unique SessionStart producer and unchanged inspected definitions/settings establish automatic orientation through combined evidence. No separate per-run hook ID, completion notification or trust receipt is claimed; the initial report that requested those unavailable records is retained alongside the subsequent assessment.

The collector now accepts the observed bundled version as well as CLI 0.163.0. Its executable and both native thread versions must still match. Only the collection subprocess used the bundled executable's directory on `PATH`; global settings stayed unchanged. The prior CLI proof remains applicable to its unchanged code path, with both versions covered by the final regression suite.

A real failed check stayed failed after independently verified host reconciliation and blocked completion. The one-line arithmetic correction passed, but missing general/security reviews still blocked. One native Luna/medium reviewer inspected the actual diff and assertions; the collector fetched its correlated receipt/report, and the Sol6.1/high coordinator assessed coverage and findings. Public verification reached `ready` at revision 17.

With that Desktop task idle, the original coordinator changed the source externally. Generation stayed 28 and receipt count stayed 29. The resumed task's first public status call still observed generation 28, revoked readiness at revision 18, and completion verification rejected stale checks, both reviews and security impact at revision 19. This demonstrates content-based invalidation without a new hook hint. Independent audit verified the command/process proofs, native review bindings and both readiness transitions.

Owned check processes stopped and the reviewer completed. Cleanup cancelled the disposable task at revision 20 and archived its unchanged seeded enrollment outside the checkout. Private state, evidence and the external edit were retained. The user's installed plugin, approved hooks and other settings were preserved. This is a fixture qualification, not completed real-project onboarding.

## Settings and limits

Native sessions temporarily registered disposable projects in global configuration. The acceptance runs' changes were isolated to their owned project-trust entries; removing those entries in memory reconstructed each immediate baseline exactly. An earlier probe's configuration hash change remains unattributed. A cleanup attempt correctly aborted when unrelated concurrent changes appeared. Final cleanup removed only the three unchanged D5-owned trust tables, preserving every other byte, parsed setting and file mode. No whole-file restoration to an obsolete baseline occurred.

Native receipts prove correlated execution identity, not readable prompt contents, semantic correctness or enforced read-only permissions. Coordinator review and genuine host lifecycle evidence remain required. Output redaction is pattern-based; inspect private diagnostics before sharing them. Local readiness is point-in-time evidence, not a security boundary against same-user filesystem processes or a repository merge gate.

**29 October 2026 capability update:** the installed Desktop bundle reports Codex `0.165.0-alpha.1.2`, app `26.1023.51421`, build `22530`. This tuple is unqualified and the current adapter rejects it. Read-only schema/source discovery, 32 adapter/Claude boundary fixture checks, and package validation prepare further qualification; they do not extend the historical native results below. Exact project-only package materialization, current-version hook/lifecycle delivery, authenticated host evidence, descendant stopping, and effective worker isolation remain unresolved.

Desktop qualification used the user's installation/trust actions and a native Desktop task, without bypassing the earlier denied UI inspection. Raw per-run startup receipts, canonical patch-guard/continuation turn enforcement, other client/version surfaces and live-project onboarding remain unqualified. The Desktop task retained uncertain guard turn correlation; successful D5 evidence verification does not establish D2 enforcement.

Repository gate preflight observed this public personal repository with no rulesets or workflows; the `main` branch-protection API returned 404, "Branch not protected." D7 remains blocked: a trusted final-verifier publisher/source, protected verifier definition, and authoritative review-transfer/freshness path are not implemented. The connected GitHub app is not evidence that it can publish the required trusted final-verifier result. No disposable repository enforcement matrix, live ruleset qualification, or repository enforcement test is claimed. These gaps do not waive the merge gate.

No release archive was created. Repository merge remains blocked pending qualification.

Private CLI evidence: `/Users/fcouto/.local/share/vcw-validation/d5-implementation-okvpko1p`, with acceptance in `acceptance/final/`. Desktop evidence: `/Users/fcouto/.local/share/vcw-validation/d5-desktop-run-ra5xynk_/observations`, including startup provenance, installed-package hashes, actual lifecycle results, independent audit, final coordinator verification and fixture cleanup. Earlier failed probes and initial limitation reports remain separate from final proof.

## Managed review-readiness on Codex CLI 0.165.0

On 4 November 2026, the D4 owner published a successor setup receipt at revision 5 in a disposable Linux checkout. The canonical receipt SHA-256 is `f6bfe5180319705f89730df8c463c03138a70c41459983508cfc540f32b6cb7d`. It retains its predecessor, verifies bootstrap and D5 review-readiness, and binds seven evidence references to candidate `44337b6aad8d1c251d3a3e82f9ac4686d1f6fd4ceb7737462febee70e4334cd1`. The other eight behavior cases and 44 fixture requirements remain pending. The qualification task stayed in `TRIAGE` with no merge authority. The original machine-bound receipt and native records remain private.

The user trusted five exact hooks. The CLI denied the single submitted patch and the canary stayed unchanged. One registered check passed on the public canary in a workload with networking disabled. One independent native child, requested as GPT-6 Luna/high, reviewed the same candidate's supplied snapshot and correctly identified `62 65 66 6f 72 65 0a` as ASCII `before` followed by one LF. The report covered general and security criteria with no findings. The app-server history and native rollout both showed zero child tool calls. The check and reviewer workloads closed before the verifier accepted their evidence. The collected review artifact SHA-256 is `5f43474ba206b3eec86271bf08890f14c39987e282160c1f5be6cab66428bd4a`.

The live result binds to staged runtime digest `1c40abb8008fd1b3711d87b32c37029c17263d408dc6e418d14dcf5cad875436` and policy digest `12a8ecdf666fd6408a510008a4c0e2c7eb906f41b3b9b371b62a4c362cd6607e`. The D5 source and test files carried forward from that run are byte-identical. Reconciliation with newer `main` produces runtime digest `8efbeb967d80ceeebc9cbf33b360d8ee8cfbcdf0ce0369de8b7eabfa2470734a` under the same policy digest. Automated checks for that revision are separate from the live result. The native review covers the supplied snapshot. Reviewer shell access and D6 lifecycle qualification remain pending.
