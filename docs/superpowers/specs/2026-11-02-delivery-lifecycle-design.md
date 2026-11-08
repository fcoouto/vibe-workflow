# Task delivery and client owned worktrees

Naming note: the `P`/`C` examples below record the earlier design. New-context
labels and preservation of adopted titles follow [Thread Naming](../../../clients/workstreams-naming.md).

Status: lifecycle direction approved in chat on 2 November 2026, including onboarding selection of merge policy, manual merge by default, explicit per-task overrides, separate worktrees by default, and client-owned local cleanup. The user selected the existing `WAITING` status for merge waits, with the reason recorded in the task rather than a new status. This document does not activate runtime behavior or authorize publication from this chat.

## Purpose

Keep completed implementation visible until its required PRs actually merge. Separate implementation verification, delivery confirmation, and resource cleanup so a READY label cannot conceal unfinished delivery. Let clients manage local worktree storage instead of building a competing cleanup system.

This extends the existing [linked delivery design](../../onboard-my-project-plan.md#cross-repository-tasks-and-delivery), [completion contract](../../../clients/evidence-completion.md#completion-and-limits), and [merge policy](../../../policies/08-merge-readiness.md). Their check, review, security, ownership, and permission requirements remain binding. Vibe Setup supplies an implementation reference, not a new runtime dependency or replacement for Toolkit authority. Execution is specified in the [implementation plan](../plans/2026-11-02-delivery-lifecycle.md).

## Decisions and alternatives

Use one lifecycle with two merge policies. Separate pipelines would duplicate verification and recovery. Requiring the plugin to physically remove every local worktree would duplicate client behavior and tie feature delivery to disk housekeeping.

The proposed division is:

- The plugin owns task publication, current readiness, merge observation, pending delivery, and safe remote-branch cleanup.
- The client or user owns local worktree retention and deletion. The plugin records that owner and any recovery information; it does not run a local deletion sweep.
- Delivery can be complete while a local worktree remains. A deleted or archived worktree never proves delivery complete.

## Onboarding and task selection

During onboarding, record the merge policy for each repository and permitted target: `manual` by default, or `agent` with standing scoped merge authorization. Preserve existing stricter repository rules, permissions, and merge methods. A Project can propose common defaults, but one repository's authorization never transfers to another.

Record an explicit per-task override in the existing task plan, including its scope and decision reference. An override to agent merge requires matching user authorization; wording such as "finish the feature" does not select it. An override cannot bypass protected targets, required reviews, or platform approvals. Later onboarding changes do not silently repin active tasks or retroactively grant merge permission; revoked authority blocks further mutations.

Separate worktrees are the default for implementation. Reuse a suitable task-owned isolated checkout before creating another. Prefer the client's native worktree tool so its own retention mechanism recognizes the checkout. Verify repository, base, task ownership, command directory, and instruction loading before edits. Read-only inspection needs no new worktree. If isolation is unavailable, report the prerequisite and use an explicitly accepted exception rather than silently editing the primary checkout. Existing user opt-outs remain effective.

Record publication and remote-branch cleanup permissions separately from merge policy. Manual merge does not prohibit authorized PR updates or post-merge branch deletion. Prefer an already-configured provider branch-deletion setting; changing repository settings requires its own authorized setup scope.

## Lifecycle and completion claims

| Stage | Required result and visible status |
| --- | --- |
| Plan accepted | Record scope, observable acceptance criteria, intended target, effective merge policy, and planned checkpoints. Obtain the design/plan acceptance required by the selected workflow before implementation. |
| Workspace prepared | Create or adopt the isolated checkout and bind it to the task. A failed preparation blocks dependent edits. |
| PR opened | Publish a draft `[BLOCKED]` PR once a meaningful publishable diff exists. A plan commit may provide that diff when appropriate; do not create empty commits to manufacture a PR. |
| Work progressing | Update the same PR at planned checkpoints with scope, progress, validation, remaining work, and blockers. Preserve human-authored text and avoid an update per tool call. |
| Implementation verified | Current acceptance checks, general/security reviews, required repository checks, and writer reconciliation pass for the candidate. Confirm non-draft status and publish `[READY]`. |
| Awaiting manual merge | Report "implementation complete; awaiting merge" with PR, target, and candidate. Retain pending delivery and reconciliation information. |
| Agent merge requested | Recheck current authorization, candidate/base, required gate evidence, and mergeability. Merge only the accepted candidate through the supported conditional mechanism or qualified queue. |
| Delivery confirmed | Observe authoritative merged status, intended repository/target, accepted PR head, and resulting merge identity. All required companion PRs must be confirmed before aggregate delivery is complete. |
| Remote cleanup settled | Confirm eligible remote branch deletion or record retained, pending, or user-disabled cleanup separately. Local worktree storage remains client-owned. |

Both merge modes use the same evidence. Manual handoff completes implementation work, not the pending delivery record. Agent mode continues through observed merge within its standing authorization. A merge request, queue admission, successful command exit, closed PR, READY title, or worker success message alone cannot establish delivery.

Use provider-aware evidence for squash and rebase merges; ancestry of the original feature head alone is insufficient. If the merged PR head differs from the reviewed candidate, record the observed merge separately and reconcile the changed scope/evidence before claiming verified delivery. An unapproved human merge is an observed fact, not retroactive passing review.

Before further implementation on a READY PR, withdraw readiness and return it to `[BLOCKED]`; re-establish affected evidence before the next READY transition. Rebase, target movement, queue composition, or changed checks invalidate affected proof under existing policies. A no-change task reports its verified result without inventing a PR or merged delivery.

## Thread naming and pending merge status

Reuse canonical `WAITING` for an expected merge, reply, or dependency. Record the reason and resumption condition in the existing task record, for example "waiting for manual merge of PR #42". Add no status, abbreviation, or title-specific merge logic. Preserve the existing family/member identity, stable topic, five-cell meter, manual-title protections, and event-driven synchronization.

The implementation plan uses fetched `origin/main` at `8d21679bdc60f0f8808a156797bcedf086d11480`, including Thread Naming. Its owners are `runtime/task_statuses.py`, `runtime/title_sync.py`, `clients/workstreams-naming.md`, and `clients/workstreams-titles.md`. Reuse those owners without recreating the feature. Existing thread `READY` means ready to begin work; PR `[READY]` means ready to merge. Keep those meanings distinct.

| Observed task condition | Thread status |
| --- | --- |
| Implementation or review fixes are underway | `WORKING` |
| Required review is pending or underway | `REVIEW` |
| Progress awaits an expected merge, reply, or dependency and no independent work can advance | `WAITING` |
| The agent is actively carrying out an authorized merge | `WORKING` |
| Readiness is invalidated or a conflict, failed requirement, or unresolved delivery needs intervention | `BLOCKED`, or `WORKING`/`REVIEW` while the responsible actor resolves it |
| Required merges, every PR the thread opened (merged remotely) and the thread's other agreed completion obligations are verified | `DONE` |

Manual handoff and merge-queue waits use `WAITING` when no other work can advance; a prerequisite merge before later implementation uses the same status. Agent merge needs no mandatory intermediate title write. `WAITING` alone claims neither PR readiness nor implementation completion. Keep an explicit pause as `PAUSED` and an intervention-required failure as `BLOCKED`. A closed-unmerged PR cannot become `DONE` without an explicit scope change or cancellation. Record unavailable observations as pending verification, not passing evidence.

Accepted implementation milestones remain counted while awaiting merge. Required integration is part of the parent task's plan, so its meter stays below five cells. Compute the actual weighted progress; do not assign four cells merely from the status. For example, where accepted milestones justify four cells:

```text
▰▰▰▰▱ [W7] P · Search filters · WAITING
▰▰▰▰▰ [W7] P · Search filters · DONE
```

Work that opened no PR may reach `DONE` through its ordinary completion gate when its recorded scope excludes publication or carries an explicit no-PR decision; any PR the thread opened must be observed merged first (superseded 8 November 2026 by the PR merge gate in `clients/workstreams-naming.md`). Client-owned local cleanup never delays `DONE`; remote cleanup is reported separately unless expressly included in the thread's required completion scope.

### Multiple PRs and parent scope

One thread may own several required publications. Keep their complete set in the existing task plan, each bound to repository, PR identity, target, accepted candidate, delivery owner, dependencies, and observed outcome. Required work without a PR yet remains unfinished scope; inspecting only the PRs already opened cannot establish completeness. References to optional or unrelated PRs do not automatically make them required. Removing, replacing, or cancelling a required publication needs an explicit scope decision, not silent omission after failure.

Apply the same status rules regardless of PR count. While independent authorized work continues, keep `WORKING` and record waiting/blocked components in the task record. If only review can advance, use `REVIEW`. Otherwise use `WAITING` for an expected event or `BLOCKED` when intervention is required. Preserve explicit pause/cancellation semantics. No fixed ranking of child statuses or distinction between final and prerequisite merge waits is needed in the title.

Parent and child states remain independent. A child may be `WAITING` for its merge while its parent is `WORKING` on other children. A child scoped only to implementation or review may be `DONE` once its own acceptance gate passes; the parent still owns any required publication and integration. Neither all children being `DONE` nor one child's merge completes the parent: verify the combined acceptance criteria, required parent review/integration, and every required delivery. Unknown required evidence cannot establish `DONE`.

Reuse authoritative child results and publication observations without double-counting shared PRs or milestones. Child titles are presentation, not evidence. Keep PR-level detail and a summary such as "1 of 3 required PRs merged; 1 ready; 1 in review" in the existing task record or requested status response. Keep one status in the title; PR counts do not determine the five-cell meter. A newly accepted scope extension reopens the parent and adds its obligations before computing progress; a completed child keeps its result unless its own scope or evidence changes.

Recompute affected owners and parents during existing lifecycle reconciliation. One PR merge may leave the thread awaiting other merges, unblock implementation, or require new combined validation. Recheck affected readiness after target changes; a previously ready sibling is not assumed to remain ready. Do not add a polling loop, second aggregation registry, or title-driven state propagation. Remote cleanup remains per owned branch and must preserve dependencies even after that branch's PR merged.

The delivery owner changes canonical task state after authoritative evidence is reconciled; title synchronization then renders that state through existing repair events. It does not query the provider, launch a watcher, infer delivery from a title, or grant merge authority. A manual merge discovered on the next supported reconciliation updates the title then; without a trigger, the title is not promised to update immediately. Failed title delivery preserves the task outcome and retries through the existing pending-title mechanism. Manual-title pauses remain respected.

The completion change is still required: current main verification can set `DONE` from implementation evidence. For delivery-required tasks, preserve verified implementation but withhold `DONE` until the delivery gate is satisfied. Integrate ordinary and linked completion/status paths and retirement protection without changing the status vocabulary. Keep existing `WAITING` guards: observation may establish that the recorded condition is resolved, then the coordinator explicitly records the appropriate transition before merge execution, verification, or implementation resumes. Resumed edits require readiness withdrawal. Never release unresolved ownership, reset limits, or bypass paused/blocked conditions as a side effect of reconciliation. Preserve historical records and pinned-runtime update rules; title text never establishes task state.

## Manual merge reconciliation

At READY handoff, save the pending delivery in the existing private task/evidence authority, outside the disposable worktree and plugin cache. Retain repository and PR identity, intended target, last published/reviewed head, effective policy and authorization references, associated branch ownership, required companion links, local storage owner, and the next action. Reuse existing records rather than introducing a second registry.

The default trigger is one bounded reconciliation when the user reports a merge, requests delivery status/cleanup, resumes the task, or enters another normal task in the same enrolled repository. The coordinator examines known pending deliveries under existing inspection bounds; no directory-wide orphan discovery or startup-hook mutation is added. Process eligible known items, retain any remainder with a continuation point, and avoid polling unchanged PRs within the same turn.

For each triggered reconciliation:

1. Read authoritative PR state through the qualified repository integration. A user saying "merged" triggers the check but does not replace it.
2. If open, retain pending delivery and assess readiness and the owner's remaining scope using the multiple-PR rules above; do not mutate the PR merely to reconcile status. If closed-unmerged, report unresolved delivery; do not delete its branch as merged work. A replacement or abandonment requires an explicit scope decision and preserves recoverability.
3. If merged, reconcile the exact candidate/target and required companions, then record delivery independently of cleanup.
4. Apply the remote cleanup rules below. If local retention is client-owned, record that responsibility without issuing deletion or chat-archive commands.
5. Report only changed outcomes, blockers, or a requested status. Unknown remote state stays pending with a concrete retry action.

No background execution is implied. If the user never returns and no supported event wakes the workflow, plugin reconciliation remains pending. Existing provider automatic branch deletion can still operate independently. An already-authorized, qualified PR event may invoke this same idempotent procedure; installing a webhook, recurring monitor, or scheduler is outside the default slice. Native client disk cleanup is not a merge notification.

## Remote branch cleanup

Keep this capability, but let provider-native deletion satisfy it when already configured. Verify absence rather than issuing a duplicate delete.

For plugin deletion, require confirmed merged delivery for the relevant publication, matching branch ownership, current authorization, and no active task, open PR, or dependent stacked branch that still needs the ref. Re-read the exact remote ref and compare it with the accepted published head. A reused branch or newer commits block deletion. Use a qualified conditional delete bound to that head; if the mechanism cannot prevent a changed-ref race, retain the branch for explicit resolution.

Never delete protected, default, foreign, or ambiguously owned branches. Reconcile participating writers before deleting their publication ref. Check authoritative absence after deletion; distinguish a successful empty lookup from failed authentication or connectivity. Reconcile uncertain outcomes before retrying. Do not delete local branch refs or force-remove local files as part of this action.

## Local worktree ownership

Official documentation checked on 2 November 2026 establishes different client behavior:

- [Codex managed worktrees](https://learn.chatgpt.com/docs/environments/git-worktrees#worktree-cleanup): automatic cleanup is configurable, normally retaining the most recent 15. Chat archival or retention pressure can trigger removal; active/pinned chats and legacy permanent worktrees have protections. Codex saves a restorable snapshot. This is storage management, not merge verification.
- [Claude Code worktrees](https://code.claude.com/docs/en/worktrees#clean-up-worktrees): interactive exit handles clean worktrees differently from changed worktrees and named sessions. Non-interactive `-p` worktrees do not receive the same exit cleanup. Do not generalize one client's guarantee to another client or execution surface.

For Codex-managed worktrees, defer to the existing client policy without changing retention settings, unpinning chats, or archiving them merely to force cleanup. Native explicit archive remains available when separately requested; use the native owner and preserve needed ignored files, which are not covered by the archive tool's snapshot guarantee.

For another client, record the actual supported owner and cleanup behavior during onboarding. For manually created or unsupported worktrees, report user-managed retention and the exact location. This first slice adds no generic fallback deletion engine. Lack of automatic local cleanup does not weaken default isolation or turn delivered work into incomplete implementation.

Local disappearance before merge leaves delivery pending and recoverable through the recorded PR/remote branch or native recovery facility. Do not rebuild the worktree simply to observe a remote merge. If unfinished work exists only locally, retain its recovery dependency and do not authorize plugin-triggered archival. Preserve current evidence-retention protections until pending merge, execution, and recovery dependencies settle.

## Implementation boundaries

Extend the existing owners after design approval:

| Owner | Required change |
| --- | --- |
| D3/D4 onboarding and policies 01/10 | Adopt repository/target merge policy, task override rules, default isolation, publication/remote cleanup permission, and client storage ownership. Preserve opt-outs and active pins. |
| Policy 02 and existing task records | Record accepted design, effective mode, meaningful checkpoints, and publication links without a new planning service. |
| D5/D6 evidence and ownership | Preserve implementation verification, current candidate binding, stopped writers, pending delivery dependencies, and existing linked-task semantics. |
| D7 repository integration and policy 08 | Own draft/READY transitions, authoritative merge observations, permitted agent merge, idempotent reconciliation, and conditional remote deletion. Current fixture inspection does not qualify this production path. |
| Client adapters and policy 11 | Create/adopt native worktrees and describe actual retention capabilities; local deletion remains with the client/user. |
| Policy 07, source map, existing status surfaces | Retain durable pending delivery and distinguish implementation, merge, remote cleanup, and local storage responsibility. |
| Existing Thread Naming state and synchronization owners | Reuse `WAITING` and task-level reasons, bind `DONE` to required delivery, retain progress rules and manual-title protection, and reuse existing title repair events. |

Implement in that order: accepted settings and task bindings; worktree preparation and publication; readiness and both merge modes; reconciliation and remote cleanup. Each slice must qualify its actual client/provider path before reporting enforcement. No new dependency, background service, local cleanup engine, runtime Toolkit fetch, model/provider change, release archive, or live repository-rule change is included.

## Acceptance and validation

Representative instruction and behavior cases must establish:

1. Fresh onboarding defaults to manual merge and separate worktrees; saved opt-outs survive updates. A task override changes only its authorized task/target.
2. Existing isolated work is reused. Failed isolation cannot silently fall back to primary-checkout writes. Read-only questions load no publication or cleanup procedure.
3. Draft `[BLOCKED]` publication and checkpoint retry do not create duplicate PRs or overwrite human text. Missing meaningful diff creates no empty PR.
4. READY requires current checks/reviews and observed remote state. Further edits withdraw it. Manual mode never invokes merge; agent mode cannot bypass stale proof or missing authority.
5. User-reported manual merge triggers authoritative reconciliation. Open, closed-unmerged, unknown, changed-head, squash/rebase, and partially merged companion cases retain accurate outcomes.
6. Provider-deleted branches are accepted as absent. Reused/advanced/protected/shared refs and unresolved writers prevent deletion. Lost responses and concurrent updates cannot cause duplicate or unsafe actions.
7. A client-removed worktree does not erase pending delivery. Pinned/active/client-retained worktrees are not forcibly removed. Unsupported local cleanup remains user-managed and does not block confirmed delivery.
8. Reconciliation runs once per supported trigger, leaves bounded continuation state, and never invents background monitoring. Delivery records survive worktree removal and remain protected while dependencies are unresolved.
9. Managed titles reuse `WAITING` without a new status or changes to PR/thread `READY` semantics. Test manual handoff, agent queue wait, active merge, stale readiness, paused tasks, child completion with a waiting parent, and partial companion delivery. Progress remains acceptance-based and below five until `DONE`; the task record explains each wait.
10. Implementation verification cannot set `DONE` prematurely for delivery-required tasks. Merge reconciliation updates canonical state before title delivery; failed title writes and manual-title pauses preserve authority. Existing waiting guards require explicit resolution before execution or verification resumes; resumed edits withdraw readiness. Historical pins and unresolved ownership remain protected without a vocabulary migration.
11. Exercise one thread with multiple PRs in mixed states, required unpublished work, a prerequisite merge with later implementation, changed sibling readiness, shared PR references, and explicit replacement of a closed-unmerged PR. Partial delivery never completes the aggregate; both final and prerequisite merge waits use ordinary `WAITING` when no independent work can advance.
12. Exercise a child waiting for merge alongside a working sibling, all children done with parent integration still pending, a blocked child alongside independent work, and a scope extension after parent completion. Derive parent state and accepted progress from its complete obligations, never child labels or averaged meters. Confirm each merge re-evaluates affected dependencies without polling or duplicate evidence counting.

Run focused behavioral tests for implemented slices and `python3 -B scripts/validate.py`. Check selective instruction paths with representative tasks. Report package validation, synthetic provider/client fixtures, and observed client/provider execution separately. This design-only change can establish document consistency and package validity, not working merge automation.
