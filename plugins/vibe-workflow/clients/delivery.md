# Delivery workspace and publication controls

Load for workspace preparation, any checkout-affecting operation (including review-only PR checkout), publication, merge reconciliation or remote cleanup. Follow the applicable scope, planning, evidence and merge policies. Naming remains in the existing Thread Naming contracts.

## Prepare the destination

Apply [Task Planning's checkout-stability rule](../policies/02-task-planning.md#planning-and-verification). This procedure uses native observations and instruction-following, not a command interceptor or hard enforcement. Read-only inspection that does not change checkout state needs no new worktree.

1. **Resolve identity and designation.** Identify the effective target of the proposed action, including `-C`, `--git-dir`, `--work-tree`, `GIT_*` selectors, aliases, scripts and native-client operations. Observe the actual checkout root, Git directory and common directory independently of any native attachment. Use `git worktree list --porcelain -z` to locate Git's primary worktree (the first entry), then verify its Git directory equals the common directory and it is non-bare, available and unambiguous. Resolve physical paths, including symlink aliases; a session in a linked worktree still checks that primary. Separate clones are separate identities. A submodule's Git metadata does not identify the superproject's primary; unsupported/bare layouts or unclear relationships remain unresolved, not guessed.
2. Read the designated branch from accepted project instructions or the existing setup record, retaining its source. Current HEAD, `origin/HEAD`, remote metadata or the merge target alone are not authority to choose or change it. A missing/conflicting designation blocks branch-dependent mutations, including choosing the base for a new task worktree and primary synchronization, not safe reads. Ask only if existing accepted facts do not settle it. A direct user answer can establish the designation for this task: retain its scope/source in the existing task record, then persist it through authorized onboarding/update rather than silently editing setup. No network fetch is needed to read local identity. A project without Git has no checkout operation; report that boundary rather than initialize a repository.
3. **Observe before acting.** Set `CHECKOUT` to the verified effective target and `PRIMARY` to its verified primary worktree. Run the following read-only observations with inherited Git selectors removed from the observation environment; do not silently rewrite a proposed mutation's environment to make it pass. Existing package code can reuse `runtime.startup.read_git`, which already removes selectors and bounds observations.

<!-- checkout-observations -->
```sh
git --no-optional-locks -c core.fsmonitor=false -C "$CHECKOUT" rev-parse --path-format=absolute --show-toplevel --git-dir --git-common-dir
git --no-optional-locks -c core.fsmonitor=false -C "$CHECKOUT" worktree list --porcelain -z
git --no-optional-locks -c core.fsmonitor=false -C "$PRIMARY" symbolic-ref --quiet HEAD
git --no-optional-locks -c core.fsmonitor=false -C "$PRIMARY" status --porcelain=v1 -z --untracked-files=all
```

   A nonzero symbolic-ref result means detached or unavailable HEAD, not permission to attach it. Compare the full symbolic ref with `refs/heads/<designated-branch>` and retain it in the task record alongside the actual commit and writer ownership. Unknown ownership, dirty files, an unexpected branch or incomplete identity blocks changes to the primary's HEAD, branch, index and source files, and blocks synchronization. Preserve and report the observed state; never switch, reset, stash, clean or move unfinished work to repair it. Independent safe reads or work in a verified task-owned worktree can continue when they do not affect the discrepant primary or another writer. The narrowly authorized, disjoint non-source writes below have their own ownership check; dirty source files alone do not authorize or prohibit those writes.
4. **Prepare task work.** Inspect native attachments first and reuse a suitable task-owned worktree. Otherwise use the client's supported native creation tool, retaining its operation/attachment identity and returned directory. Only when no supported native tool exists, use `git worktree add` at an authorized destination; verify a project-local destination is ignored first. After a lost response, inspect native status and Git worktrees; unresolved ownership never permits blind duplicate creation. Verify the new directory's Git identity and task branch, attaching a branch there if the native tool created detached HEAD. Never switch the primary to create the task branch. Edit, commit and publish only from the prepared task worktree. Report its path and branch when delivering local results; if commits/publication are not authorized, retain uncommitted work there and do not release it for cleanup without the user's decision. Client-owned cleanup is not permission to discard results or copy them into the primary. If isolation is unavailable, report the prerequisite rather than silently falling back to the primary.
5. Reload destination instructions and retain the accepted package/setup-record pointers in the task context, including when the setup record is available only in the primary. Tracked instructions and installed skills support ordinary work without copying `.vibe-workflow/project.json` or onboarding every temporary worktree. This does not enroll that worktree or extend authority: selected managed operations still require exact checkout/client admission through their existing procedure. An unreadable instruction path blocks only the action that depends on it.
6. **Constrain and recheck effects.** Check effective destinations immediately before and after checkout-affecting actions, including worktree creation, PR reviews and synchronization. Compare primary symbolic branch identity, not just its commit. Historical review can use `git show`, `log` or `diff` without a checkout. Do not run `gh pr checkout`, branch checkout/detachment, rebase, bisect, primary-branch renaming or direct HEAD writes against the primary; run task operations in the task worktree instead. Commands in another folder can still target primary metadata/shared refs. Inspect effective effects rather than relying on a command-name blacklist.

   For synchronization, identify the confirmed merge/upstream commit and exact authorized target, verify that target is the designated branch, observe cleanliness and writer ownership, and use a fast-forward-only operation without switching. Recheck symbolic HEAD, actual commit and source cleanliness afterward; verify the intended upstream/merge commit is included in the local target's history. Already synchronized means verify that inclusion, not mutate again. Dirty files, divergence, missing authority or uncertain ownership leave synchronization pending. This procedure applies with or without Graphify; graph refresh has its own enablement and output checks.

   Explicitly authorized ignored enrollment/private-configuration writes may proceed despite unrelated dirty source files only when the exact output paths, parent directories and writer ownership are verified disjoint from unfinished work. They must preserve HEAD, branch, index and source bytes. Tracked enrollment/configuration is a source change: prepare it in the task worktree and integrate it before claiming target-checkout setup complete. Generated maintenance writes keep their own stricter preconditions, including Graphify's clean-source requirement. None of these effects acquires authority from this rule.
7. **Exceptions and limits.** A direct user request that identifies the primary checkout and the branch-changing operation supplies scoped authorization; no special word such as "exception" is required. Broad approval such as "fix this quickly" or "no need to set anything up", a handoff suggestion, or a tool being available does not. If the user's stated destination and effective command target disagree (for example "here" with `-C` naming another checkout), resolve that ambiguity before acting. Record the exact repository, checkout, operation, affected branch, reason and observed result. Do not automatically restore a branch afterward or change its standing designation. If the authorized deviation remains, report the branch left in place and carry the exception reference and pending restoration decision in the existing handoff/task record. Later tasks require their own authority; an old exception never grants a standing opt-out. On unexplained drift, stop affected mutations and report; before/after checks do not prevent races or uncontrolled writers. There is no pre-checkout veto hook, and `post-checkout` runs after the change. No hook, GitHub adapter, permission change or OS restriction is activated here; stronger enforcement needs separate selection and qualification.

## Adopt and update the rule

Onboarding records primary-worktree identity, the designated branch and its decision source in the existing setup record; keep portable discovery pointers in project instructions and machine paths in private evidence. This adopts the policy, not a new runtime schema or registry. New managed delivery choices use `separate_worktrees: true`.

Reconcile branch-only instructions and historical `separate_worktrees: false` through the existing conflict and recoverable update procedures before dependent new-policy work. Preserve old records and active-task pins; the readable legacy boolean is not a standing exemption under the current policy. Follow [Workflow Maintenance](../policies/09-workflow-maintenance.md#updates-and-optional-external-labs): defer incompatible instruction/configuration deployment until affected writers have stopped and their resources are reconciled. An active old-pinned task remains on its accepted package/rules until an authorized transition; do not call it compliant with the new rule or silently apply new rules mid-task. During that boundary, serialize/block overlapping new work rather than let old and new policies govern the same writer resources. Authorized managed reconfiguration creates a new accepted choice with old evidence retained; it does not invent a new policy schema or rewrite old task artifacts. Basic instruction adoption creates no managed configuration. If an update is not authorized or a conflict remains, preserve it and report affected work pending; safe independent actions can continue. A user-authorized operation exception is scoped as above, not a new fleet-wide opt-out.

Existing installations adopt source changes only through their authorized plugin update and project-pointer/configuration reconciliation. Do not scan or modify every enrolled repository, change hook trust or update active sessions as a side effect. Verify current instructions and fresh-session delivery where observable; report pending deployment separately from source validation. Basic onboarding and ordinary work acquire no runtime database or enforcement prerequisite.

While incompatible deployment is deferred, non-overlapping new tasks that already
use the current policy follow it in their own verified task worktrees; they may
not change old tasks' resources or switch the primary. A session still using the
old installed guidance has not thereby adopted the new policy. Keep that
deployment limitation explicit rather than treating it as an exception.

## Post-merge cleanup

Cleanup is opt-in (`cleanup.enabled: false` by default). Once enabled,
`delete_remote_branch: true` includes eligible remote branches; users can disable
that value separately. Missing or malformed settings never authorize deletion.
This is a coordinator procedure using native tools, not an unattended sweep or
mechanically enforced transaction. Use it only for the exact task PR whose merge
the coordinator has confirmed, including a manual merge observed later.

1. **Permission and merge.** Resolve feature settings at the verified primary
   checkout using [feature settings](features.md). Check `effective` before any
   subsetting. Establish standing repository-scoped cleanup permission from the
   submitted onboarding choice or an explicit user decision. Settings, a caller
   flag, or an inspection result alone grant no permission. Preserve refusals,
   old task pins and immediate session revocation. Recheck enablement and current
   permission before each destructive action; an upgrade never adopts old tasks
   silently. With no permission or cleanup disabled, retain resources and stop.
   Through the existing authenticated provider tool, observe the exact repository
   identity, PR number, merged state, merged head, head/base repositories, branch,
   target and merge commit. Closed or queued is not merged. Require the recorded
   task candidate to equal the merged head. Initially support same-repository
   GitHub PRs only; retain fork or ambiguous targets. Do not infer squash/rebase
   merge success from head ancestry.
2. **Synchronize.** Follow [delivery preparation](#prepare-the-destination): fetch
   the configured upstream and designated primary branch, fast-forward without
   switching the primary, verify the confirmed merge commit is included, and
   complete required post-merge maintenance. Dirty, diverged, detached or uncertain
   primary state leaves cleanup pending; never repair it by reset, stash or force.
3. **Inspect and establish ownership.** Keep the exact repository, PR, worktree,
   refs, expected head and merge commit in the existing surviving task context.
   Preserve evidence needed after removal outside the task worktree; otherwise
   retain it. Confirm known workers/processes stopped and no other session,
   worktree, shared branch or dependent task/PR needs the resources. Check provider
   default/protected branch status and dependent PRs with complete observations;
   unknown means retain. A clean Git status does not prove exclusive ownership.
   Run the installed helper from a stable package outside the worktree:

   ```sh
   python3 -I -B "$PLUGIN_ROOT/scripts/cleanup.py" --primary "$PRIMARY" --primary-branch "$PRIMARY_BRANCH" --worktree "$TASK_WORKTREE" --branch "$TASK_BRANCH" --head "$HEAD" --merge "$MERGE"
   ```

   Here `HEAD` is the exact merged task head, not a fresh guess from a branch name;
   `MERGE` is the provider-confirmed merge commit. `eligible` means only local
   checks passed. The helper checks primary/worktree identity, merge inclusion,
   unchanged refs, locks, Git operations, submodules, hidden index flags, and
   staged/unstaged/untracked/**ignored** work. It observes no native ownership or
   provider facts and grants no authority. A retained result stops this attempt.
   Any registered worktree's active rebase or bisect defers cleanup, including an
   unrelated one: those operations can reserve additional refs while detached.
4. **Remove locally.** Use only a native removal tool whose behavior was observed
   on disposable resources (see below). Account for any implicit branch deletion
   before invoking it; if that effect cannot preserve the expected ref, retain.
   Relocate the coordinator to the verified primary and verify the native session
   attachment changed. Recheck ownership and the helper immediately before removal.
   Use no force option. After removal, verify the worktree and its Git registration
   are absent. Keep the local branch until remote cleanup has settled or been
   skipped, so the helper can still check the task's exact ref.
5. **Delete the supported remote branch.** When enabled and authorized, inspect
   remote configuration **before local deletion**, adding `--remote "$REMOTE"
   --repository "$REPOSITORY"` to the helper. An unsupported destination skips
   remote deletion and does not prevent independently eligible local cleanup.
   The supported path is an ordinary `https://github.com/<owner>/<repo>[.git]`
   URL for the provider-observed repository, with no URL rewrites, alternate push
   URLs, custom HTTP/transport settings, transport environment overrides or active
   push hook. Keep existing authentication; do not change config to pass inspection.
   Immediately before the push, repeat the helper with `--removed --remote
   "$REMOTE" --repository "$REPOSITORY"`, and recheck permission, provider
   repository ID, branch protection/dependencies, and remote head. Bind
   the current provider repository ID to the recorded PR repository; use the
   checked literal destination, not a remote alias. `REF` is the helper's full
   `refs/heads/...` ref. Use the expected-object condition:

<!-- cleanup-remote-delete -->
```sh
git -C "$PRIMARY" -c http.followRedirects=false push --no-follow-tags --recurse-submodules=no "--force-with-lease=$REF:$HEAD" -- "$DESTINATION" ":$REF"
```

   The lease is a conditional ref deletion, not permission for forceful worktree
   removal. Verify remote absence and repository identity afterward. Provider
   identity reads and the Git push are not atomic; a rename/replacement or unknown
   result is not proof of successful cleanup. Retain unsupported cases rather
   than adding a transport fallback. Managed operations still need their selected
   owner; this procedure does not admit a managed cleanup mutation.
6. **Delete the local branch.** Recheck current permission and repeat the helper
   with `--removed` immediately before deleting the local ref. This checks that
   the exact task head remains and no worktree uses the branch. Use the native shell:

<!-- cleanup-local-delete -->
```sh
git -C "$PRIMARY" update-ref --no-deref -d "$REF" "$HEAD"
```

   Verify absence afterward. A missing branch is already absent, not a reason to
   recreate it. No command here excludes uncontrolled writers atomically; if
   ownership is uncertain, retain resources. A later explicitly requested remote-
   only attempt uses fresh native provider/transport checks and the original task
   binding; do not recreate a deleted local branch to satisfy the helper.
7. **Report and stop.** Record each resource as deleted, already absent, retained
   with reason, or outcome unknown in the existing surviving task context. On any
   failed action, interruption, changed target or uncertain result, stop remaining
   destructive actions. Do not automatically retry or resume partial cleanup.
   A later explicit request must establish exact remaining targets and fresh
   checks; insufficient evidence means retain. Never replay a completed deletion
   against a resource that reappeared. There is no recovery journal or new store.

### Native removal observations

On 12 November 2026, OpenCode v2.2.6 on macOS was probed through its native
`worktree_create` and `worktree_remove` tools in a disposable task worktree.
Removal without force refused both a modified tracked file and an untracked file.
It **deleted an ignored synthetic file** and the worktree while preserving the
local branch at its original commit. Therefore native refusal is not an
ignored-file safeguard: check ignored content and stop known writers first.
There is still a check-to-removal race with uncontrolled writers; retain the
worktree if ownership cannot be established. This probe did not qualify active
session exclusion, concurrent writes, locks, submodules, Codex or Claude removal.
For an unobserved or materially changed native tool, probe these effects on
disposable resources before relying on it; do not invent a fallback that deletes
the directory recursively. Installed presence alone is not removal evidence.

## Bind an admitted workspace

The callable `scripts.delivery_coordinator.DeliveryOperationOwner` binds an independently verified host decision to exact scope, action and request digest. It has no JSON CLI, default native source or authority to manufacture admission. Host observations and decision references bind facts; the caller must independently establish their source and meaning.

After recording the task delivery plan, `owner.run(scope, 'bind-worktree', request)` takes the existing task identity/revision plus stable `operation_id`. Its native source supplies `observe_worktree(root)` with actual root, client owner, native attachment and observation reference. The package independently observes Git identity and HEAD, then commits immutable workspace evidence through existing task/artifact storage. An unresolved or foreign attachment cannot bind. An exact completed replay returns the existing binding; changed inputs cannot reuse that identity. No transaction spans native observation.

This binding creates no worktree, edits no product file, grants no dispatch/merge permission and performs no cleanup. High-level publication remains unavailable until its implementation and evidence requirements are satisfied. Do not treat a software fixture or a manually supplied observation as live client qualification.

## Completion and retention

An editable task under an adopted delivery policy needs an explicit delivery scope decision, including when no PR is needed. Completion consumes its retained plan; a caller's merge claim or title cannot satisfy it. Runtime verification enforces the [PR merge gate](workstreams-naming.md#status-and-accepted-progress) only for required publications; a PR the plan marks optional under the user's recorded scope decision is exempt. For any other PR the thread opened or took over, withhold completion verification until its remote merge is observed or the user's explicit scope decision says otherwise. Local proof remains `implementation_verified` when delivery is pending. `verified` answers the requested overall completion; `delivery_complete` answers delivery alone. `merge-evidence` checks implementation without requiring that a merge already happened.

Only after all independent accepted work passes and every required publication has retained readiness, pending merges set the task to `WAITING` with its specific merge condition. Unpublished or unready work still needs implementation/review. While other work can advance, keep the actual working/review state. Existing Thread Naming renders this state below five cells; no provider call occurs during rendering. An accepted parent scope includes its required companions and integration; children do not assign the parent's state or meter.

Preserve pending delivery and recovery evidence through cancellation and retention. Resolve the specific wait through existing `resolution_ref` handling before another completion verification; unrelated decisions, blockers and explicit pauses remain effective. Existing-PR observation and the private manual controls below retain facts and readiness. Actual qualified host sources remain unavailable; publication still needs its separate execution boundary. A merge observation cannot manufacture missing readiness provenance. Do not substitute synthetic proof to clear it.

## Inspect pending delivery

`python3 -B scripts/workflow.py delivery status --cwd PATH --client codex --session SESSION` returns the current task's retained scope and requirements. `delivery pending --cwd PATH --client codex` inspects at most 32 known task records in that authority. Optional `--cursor` continues the same authority; `--limit` is between 1 and 32. A non-null cursor means more known records remain, even when this page has no pending deliveries. No page claims to discover unknown PRs or inventories in other authorities.

These controls call no provider, scan no checkout and perform no cleanup. Pending inventory can read the original task's retained facts after a client removes its checkout, through a surviving admitted checkout in the same authority. Missing current local proof still prevents claiming fresh completion. Public JSON/CLI cannot submit merged facts or invoke a delivery mutation.

## Revise accepted scope

Use `operate(scope, 'plan', request)` with the existing task identity/revision, complete revised `plan`, exact `previous_plan` artifact reference and accepted `change_decision_ref`. The revised plan's `decision_ref` must match that decision. Preserve existing publication identity, candidate, PR, companions and required obligations. The bounded revision path permits added obligations, not silent removal or replacement. It preserves the effective task policy unless an explicit accepted override changes it.

An expanded scope with existing milestones must include revised accepted `milestones` in the same request. Store them before computing weighted progress; PR count never determines the meter. A previously `DONE` task reopens to `REVIEW`; preserve explicit `PAUSED` and `WAITING` conditions. Historical plans remain managed dependencies.

## Observe an existing PR

A host that independently verifies scope and observation authority may call `owner.run(scope, 'reconcile', request)`. Supply existing task identity/revision, stable `operation_id` and `publication_id`. Initial binding also supplies `pull_request` and requires the original admitted workspace, native PR ownership and committed candidate. Later observation can use canonical `original` task reference plus `target_revision` from a surviving admitted producer in the same physical authority and project. Both tasks need compatible retained pins. Exact replay returns the prior observation; changed inputs require another operation identity.

This path reads provider facts outside SQLite transactions and rechecks task/plan revisions before committing managed evidence. It can retain merged facts after original checkout removal. It never substitutes fresh local proof, resolves a wait, declares `DONE`, changes accepted identity or recreates the missing checkout. Closed-unmerged, changed candidate, replaced repository and unavailable observations remain unresolved.

Known optional PRs and authorized remote cleanup also retain inventory/evidence until settled, even when not part of own completion. Delivery completion and retention completion are separate. Use existing evidence retirement checks; do not delete records because the thread finished.

## Private manual delivery

Positive readiness and merge authorization require full applicable qualification and ordinary task purpose. A [qualification task](runtime.md#staged-qualification) cannot obtain that authority from a passing canary or later setup advancement. Status, exact reconciliation and negative withdrawal retain their existing bounded owners.

Use `owner.run(scope, action, request)` with `ready`, `reopen` or `settle`; requests contain the existing task identity/revision, `publication_id` and stable `operation_id`. No new public CLI or JSON authority exists.

`ready` supports one required PR, manual merge and no required companions in a task already `WORKING` or `REVIEW`. Preserve existing obligations rather than changing another task to fit this scope. The actual qualified Gate must issue a live private `runtime.repository_gate.RepositoryReadiness` through `acquire_readiness(scope, request, publication_binding)`; the owner supplies the operation's `action` to that acquisition. It must bind current D5/D6/D7 inputs and the exact host-selected GitHub adapter. Raw flags, references and callability cannot authorize promotion. The held context spans metadata effect/readback outside SQLite and local readiness publication through `finalize(publish(db))`; the callback performs no network or nested database write.

`reopen` uses that protocol as withdrawal-only, without requesting positive checks or reading the context's positive verification property. Its effect records `local_verification: None`. Acknowledged remote draft/`[BLOCKED]` withdrawal clears retained readiness; failed withdrawal cannot acknowledge permission for further edits. Explicit pauses and blockers remain effective. The operation does not automatically resume a waiting task.

After the user merges, record the actual provider observation through `reconcile`. `settle` then consumes retained readiness and the unchanged accepted scope, resolving only its generated merge wait to `REVIEW`. Explicit pauses, unrelated pending conditions and changed scope/candidate prevent settlement. It performs no provider mutation and never writes `DONE`; run existing completion verification next, then existing title repair. Missing fresh local proof still prevents acceptance after checkout removal.

These are source consumers, not qualified host execution. Actual Gate custody, native lifecycle/currentness and metadata-write authority remain unavailable pending integration and qualification.

## Unsupported effect orchestration

The GitHub and Git transport adapters are bounded primitives, not delivery authority. Their mutation sources default to unavailable. The callable owner does not orchestrate checkpoint/commit/push, agent merge or remote cleanup. Do not invoke a primitive directly to bypass that boundary or supply handcrafted readiness/cleanup receipts. Existing PR metadata uses workflow labels and append-only operation-marked comments to preserve concurrent human title/description edits. Local worktree and local branch cleanup remain client-owned. Ordinary native cleanup follows [post-merge cleanup](#post-merge-cleanup); it does not activate or impersonate the managed owner.
