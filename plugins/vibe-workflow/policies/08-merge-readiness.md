# Merge Readiness

Apply with the [shared contract rules](00-index.md).

When a merge is requested, act only on the reviewed candidate after its applicable evidence passes and the requested action is authorized. Toolkit-managed merge enforcement is an advanced capability; load and enforce its controls only when selected, preserving existing repository rules.

**Project Onboarding:** identify relevant repository rules and required checks when delivery is selected. Do not require merge-gate qualification as a condition of ordinary editing or onboarding.

| Element | Contract |
| --- | --- |
| Trigger | Before declaring a candidate merge-ready or merging; after candidate/base changes, changed check definitions or policy, new findings, or changed repository enforcement. |
| Decision | Determine the exact candidate and target, required evidence, authorization and whether repository controls actually enforce the decision. |
| Required action | Assemble current check/review evidence, resolve blockers, verify applicable existing checks and any selected managed gate, and recheck the candidate immediately before an authorized merge. |
| Evidence | Record candidate/base and any queue candidate, applicable policy/check versions, authoritative check/review results, finding dispositions, gate configuration and the actual merge result. |
| Blocking condition | Applicable required evidence is missing, failed, stale or untrusted; a blocking finding or material uncertainty remains; a selected required gate is unqualified; or the merge is outside existing authorization. Continue independent work. |

## Establish readiness

1. Identify the repository, source, target and immutable candidate/base. Separate the submitted candidate from unrelated local changes. A working-tree review must be verified against the submitted content; a branch name or previous HEAD alone is insufficient.
2. Apply [Tests and Builds](05-tests-and-builds.md), [Code Review](06-code-review.md) and applicable [Security Review](04-security-review.md). Required checks must explicitly succeed; cancelled, skipped, timed-out or absent results cannot manufacture success. Required findings need resolution or a permitted, recorded disposition.
3. Confirm evidence covers the current candidate, base, relevant check definitions and accepted policy. A rebase, conflict resolution, target update, queue composition or additional edit invalidates affected evidence. Reuse unaffected evidence only with a recorded justification and authoritative candidate binding.
4. Have the final verifier assess the evidence. A worker's success message, local readiness flag or submitted report is not sufficient proof. Report readiness separately from whether a merge was requested or performed.

## Selected repository enforcement

When managed merge enforcement is selected:

- Require its final verifier through repository-controlled rules for the selected targets and merge methods. Inspect effective rules and bypass settings; UI, API and CLI paths must not silently escape the required gate. Client hooks alone cannot establish enforcement of every merge.
- Execute verification from a trusted definition and obtain check/reviewer identity and results from qualified execution or a trusted integration. Do not trust a PR-writable report or workflow merely because it says review passed. Protect the verifier's definition and evidence path against candidate-controlled replacement.
- If a merge queue is used, verify its actual candidate and base with the appropriate required event. A passing check on an earlier PR head does not establish that the queued combination passed.
- Qualify controls in a disposable repository or authorized test scope, including failed/missing/stale checks and review evidence. Apply live rule changes only within an approved configuration scope. If a trusted review source or necessary control is unavailable under approved access/subscriptions, keep that enforcement capability unavailable and state the limitation; do not block basic onboarding, add billing or fabricate success.

## Perform and confirm

Readiness grants no new commit, push, merge or deployment authority. Reuse existing authorization without asking again unless its scope changes. Immediately before acting, verify the candidate and target still match the accepted evidence and use the supported conditional-update mechanism when available.

After the merge, observe repository state and record the resulting commit and method. If the outcome is unknown, inspect it before retrying. Preserve unrelated work and do not bypass a failed gate or force an unrelated history change to complete the request.
