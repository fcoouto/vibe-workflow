# Merge Readiness

Apply with the [shared contract rules](00-index.md).

When a merge is requested, act only on the reviewed candidate after its applicable evidence passes and the requested action is authorized. Toolkit-managed merge enforcement is an advanced capability; load and enforce its controls only when selected, preserving existing repository rules.

**Project Onboarding:** identify relevant repository rules and required checks when delivery is selected. Do not require merge-gate qualification as a condition of ordinary editing or onboarding.

| Element | Contract |
| --- | --- |
| Trigger | When opening or updating a PR; before declaring a candidate merge-ready or merging, including a direct commit or push to a merge target; when synchronizing merged changes; after candidate/base changes, changed check definitions or policy, new findings, or changed repository enforcement. |
| Decision | Determine the exact candidate and target, required evidence, authorization and whether repository controls actually enforce the decision. |
| Required action | Assemble current check/review evidence, resolve blockers, verify applicable existing checks and any selected managed gate, and recheck the candidate immediately before an authorized merge. |
| Evidence | Record candidate/base and any queue candidate, applicable policy/check versions, authoritative check/review results, finding dispositions, gate configuration and the actual merge result. |
| Blocking condition | Applicable required evidence is missing, failed, stale or untrusted; a blocking finding or material uncertainty remains; a selected required gate is unqualified; or the merge is outside existing authorization. Continue independent work. |

## Establish readiness

1. Identify the repository, source, target and immutable candidate/base. Separate the submitted candidate from unrelated local changes. A working-tree review must be verified against the submitted content; a branch name or previous HEAD alone is insufficient.
2. Apply [Tests and Builds](05-tests-and-builds.md), [Code Review](06-code-review.md) and applicable [Security Review](04-security-review.md). Required checks must explicitly succeed; cancelled, skipped, timed-out or absent results cannot manufacture success. Required findings need resolution or a permitted, recorded disposition.
3. Confirm evidence covers the current candidate, base, relevant check definitions and accepted policy. A rebase, conflict resolution, target update, queue composition or additional edit invalidates affected evidence. Reuse unaffected evidence only with a recorded justification and authoritative candidate binding.
4. Have the final verifier assess the evidence. A worker's success message, local readiness flag or submitted report is not sufficient proof. Report readiness separately from whether a merge was requested or performed.

## PR lifecycle

When agents publish through pull requests, the agent that opened or took over a PR keeps its title prefix and draft state in step with its readiness:

1. Open every PR as a draft, where the host supports drafts, titled `[BLOCKED] <title>`. `[BLOCKED]` is the normal state while work, checks or review continue. Write the body from the [PR template](../templates/pull-request.md) unless the project has its own layout.
2. When [readiness](#establish-readiness) holds for the current candidate, change the prefix to `[READY]` and mark the PR ready for review. The template's readiness section can carry that evidence.
3. Before pushing to a `[READY]` PR, and as soon as a base change, failed check or new finding invalidates its evidence, return it to `[BLOCKED]` and draft.
4. If the project's recorded merge permission covers the target, or the user explicitly asks for this merge, merge a `[READY]` PR as described in [Perform and confirm](#perform-and-confirm). Otherwise stop at `[READY]` and report that it awaits the user's merge.
5. When other work replaces a PR, by the user's decision or within its own task's scope, change its prefix to `[SUPERSEDED]`, name what replaces it in a closing comment, and close it unmerged; keep its branch. A publication that selected managed delivery requires still needs the user's scope decision.

The prefix belongs to the agent; never ask a person to change it. When agents may not merge, merging a `[READY]` PR is the person's step. Re-read the title before changing the prefix and keep the rest of it. A prefix reports readiness and is never evidence of it; one set by anyone else establishes nothing. A PR without a prefix gets the one its current evidence supports when an agent next updates it. Prefixes describe PRs, not threads; thread statuses stay in the [naming contract](../clients/workstreams-naming.md#status-and-accepted-progress). Selected managed delivery marks the same two phases with its own labels and comments ([delivery](../clients/delivery.md#private-manual-delivery)).

## Selected repository enforcement

When managed merge enforcement is selected:

- Require its final verifier through repository-controlled rules for the selected targets and merge methods. Inspect effective rules and bypass settings; UI, API and CLI paths must not silently escape the required gate. Client hooks alone cannot establish enforcement of every merge.
- Execute verification from a trusted definition and obtain check/reviewer identity and results from qualified execution or a trusted integration. Do not trust a PR-writable report or workflow merely because it says review passed. Protect the verifier's definition and evidence path against candidate-controlled replacement.
- If a merge queue is used, verify its actual candidate and base with the appropriate required event. A passing check on an earlier PR head does not establish that the queued combination passed.
- Qualify controls in a disposable repository or authorized test scope, including failed/missing/stale checks and review evidence. Apply live rule changes only within an approved configuration scope. If a trusted review source or necessary control is unavailable under approved access/subscriptions, keep that enforcement capability unavailable and state the limitation; do not block basic onboarding, add billing or fabricate success.

## Perform and confirm

A merge target is a branch the project designates for integrating completed work, normally its primary branch; a task branch that only serves as the base of a stacked PR is not one. A direct commit or push to a merge target bypasses PR review and counts as a merge: it needs the same readiness and explicit authorization. Authority to commit, push or open PRs for task branches never covers it. Readiness grants no new commit, push, merge or deployment authority. Reuse existing authorization without asking again unless its scope changes. Immediately before acting, verify the candidate and target still match the accepted evidence and use the supported conditional-update mechanism when available.

After the merge, observe repository state and record the resulting commit and method. If the outcome is unknown, inspect it before retrying. Preserve unrelated work and do not bypass a failed gate or force an unrelated history change to complete the request.

For Graphify-enabled projects, complete [Graphify post-merge maintenance](../clients/graphify-maintenance.md)
after confirming the merge, and when synchronizing merges made elsewhere. Run it
as part of the operation without another user request. A successful remote merge
does not establish successful local synchronization or graph refresh; report
pending maintenance separately and do not claim the overall operation complete.
