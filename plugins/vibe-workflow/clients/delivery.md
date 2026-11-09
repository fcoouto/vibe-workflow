# Delivery workspace and publication controls

Load this contract for delivery preparation, publication, merge reconciliation or remote cleanup. Follow the applicable scope, planning, evidence and merge policies. Naming remains in the existing Thread Naming contracts.

## Prepare the destination

Separate implementation worktrees are the default; preserve an explicit opt-out. Read-only inspection needs no new checkout. Inspect native attachments first and reuse a suitable task-owned worktree. Otherwise use the client's supported native creation tool, retain its returned operation and attachment identity, and continue at the returned directory. After a lost creation response, inspect native status/attachments; unresolved ownership never permits blind duplicate creation. Native client storage and cleanup remain client-owned.

Discover the actual Git repository and common directory independently of the native attachment. Reload applicable instructions in the destination. For managed workflow tasks, admit that exact checkout/client through existing onboarding before starting its editable task; never transplant the source task's accepted identity. Ordinary unenrolled source development does not imply managed activation.

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

An expanded scope with existing milestones must include revised accepted `milestones` in the same request. Store them before computing weighted progress; PR count never determines the meter. A previously `DONE` task reopens to `REVIEW`; preserve explicit `PAUSED`, `BLOCKED` and `WAITING` conditions. Historical plans remain managed dependencies.

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

The GitHub and Git transport adapters are bounded primitives, not delivery authority. Their mutation sources default to unavailable. The callable owner does not orchestrate checkpoint/commit/push, agent merge or remote cleanup. Do not invoke a primitive directly to bypass that boundary or supply handcrafted readiness/cleanup receipts. Existing PR metadata uses workflow labels and append-only operation-marked comments to preserve concurrent human title/description edits. Local worktree and local branch cleanup remain client-owned.
