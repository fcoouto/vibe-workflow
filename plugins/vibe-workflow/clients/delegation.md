# Native delegation

Ordinary native delegation follows [Agent Delegation](../policies/03-agent-delegation.md) for execution choice, worker selection, briefs, ownership and review. It does not require managed runtime enrollment. Load the controls below only for a selected managed assignment route. Use [task runtime](runtime.md#runtime-authority) for its shared state authority and [evidence](evidence.md) for its review, checks and completion. Direct checks and coordinator resource claims do not need native guidance. Startup alone needs none of this procedure.

For automatic Workstreams coordination, first honor the saved [project preference](../skills/workstreams/references/procedure.md). Onboarding and the skill share that choice. It does not select the managed launch controls below or alter explicitly selected protections.

The [cooperative D6 contract](../docs/d6-native-delegation-plan.md#normal-completion-and-uncertain-ownership) uses normal native completion and independent result inspection. The [Mac CLI pilot](../docs/superpowers/plans/2026-11-04-d6-successor-release-packet.md) qualified one bounded guarded canary. Preserve selected protections and historical proof meanings.

The launch and observation controls below apply to native children. For an authorized Usage Routing proposal, use the [Usage Routing delegation procedure](../skills/manage-usage-routing/references/delegation.md).

Load the [display naming rules](workstreams-naming.md) for native parent/child eligibility, including ordinary delegation and review. Follow their implementation trigger and applicable [title synchronization](workstreams-titles.md) procedure without changing prepared creation markers or launch permissions.

## Observe the route and select settings

The package provides Codex CLI/Desktop native observation. Qualify the actual surface and required controls under [capability-based compatibility](../policies/11-tool-integrations.md#qualify-the-actual-workflow) before relying on them. Recorded version numbers are provenance, not support gates. Package validation does not establish observed native execution. Approval alone cannot make an unsupported route executable.

Use `discover_native_workers` in the [Codex adapter](../runtime/adapters/codex.py) to inspect current surface, native version and catalog with the coordinator's actual exposed dispatch controls. Select suitable worker settings under policy, retaining the control observation and expected capability digest. Explicit model/effort selection requires supported controls and `fork_turns="none"`; otherwise supply supported inheritance and its evidence only when suitable. Missing controls are limitations, not permission to change coordinator or global settings.

New admission reobserves required controls/catalog and compares capabilities, surface, provider and parent identity. Release metadata differences do not block admission. Record the actual reader version in each new binding while retaining submitted observations and historical receipts unchanged.

Keep requested settings, configured defaults, spawn-observed settings and per-turn execution facts separate. Unknown execution, provider or endpoint facts stay unknown. Historical D5 receipt fields retain their original meanings; see [review evidence](evidence.md#request-fields). Earlier D5 qualification does not qualify this linked schema-2 lifecycle.

## Use the relevant guidance

Call `guidance_status` with the observed `client_surface`, `applicability` (`client_version`, `capability_fingerprint`) and only relevant `profile_ids`. Retain source URLs/digests, checked time, applicability and immutable snapshot digest. Recommendations do not establish model availability, source verification or route authority; independently verify those facts.

Reuse compares the capability fingerprint on the same surface. Keep the cached client version, checked time and source reference unchanged when reusing guidance after an update. Discovery fingerprints exclude release metadata. Refresh claims still bind the exact requested applicability: an update cannot relabel an outstanding claim or publish an old snapshot as newly verified guidance. Historical control/source keys remain readable without rewriting them. A compatible original snapshot can establish an old failed-refresh key; when legacy applicability cannot be recovered, allow one normal re-evaluation rather than infer unchanged capabilities. Subsequent failure keys suppress version-only retry storms.

When policy's refresh triggers apply, call `claim_guidance` before fetching and `record_guidance` afterward with the live token/generation and verified guidance or failure. The claim serializes publication; an expired or superseded claim cannot publish. Preserve the same trigger identity rather than retrying a failed refresh under invented IDs. Follow policy's compatible stale-cache reuse and dependent-only blocking procedure. No routine upstream fetch is needed merely because time passed.

## Prepare and launch once

Managed launch and continuation require their applicable qualified capabilities. For a [qualification task](runtime.md#staged-qualification), admission covers only the retained attempt's exact operations and original allocations. Recheck before a new effect; an expired attempt, later receipt or reacquired reservation cannot renew its deadline or budget. Readback and exact reconciliation remain available without granting another launch.

Run the installed package's `scripts/workflow.py` with the enrolled checkout as `--cwd`. The following controls read bounded JSON on stdin, except assignment status:

| Control after `workflow.py` | Load only this request reference |
| --- | --- |
| `delegation guidance status\|claim\|record --cwd /checkout` | [Guidance cache controls](#use-the-relevant-guidance) |
| `delegation plan --cwd /checkout` | [Plan](delegation-requests.md#plan) |
| `delegation prepare --cwd /checkout` | [Prepare worker](delegation-requests.md#prepare-worker) |
| `delegation observe --cwd /checkout` | [Observe](delegation-requests.md#observe) |
| `delegation reconcile --cwd /checkout` | [Reconcile](delegation-requests.md#reconcile) |
| `delegation status --cwd /checkout --client codex --session SESSION` | [Status](delegation-requests.md#status); no stdin |

Use the [common envelope](delegation-requests.md#common-envelope) and current response
revision for mutations. The action references include validator-checked examples.
For the applicable proof kind, read [reconciliation fields](delegation-proofs.md).
For cache refresh publication, read [guidance publication](guidance-publication.md).
Implementation reads are needed only for diagnosis or implementation work. The runtime
does not choose jobs, budgets, settings or authorization.

Accept the D5 evidence plan, then an immutable D6 plan with exact jobs, claims, dependencies, finite admission limits and verified capacity. Apply policy's isolation and ownership rules under the [shared authority](runtime.md#runtime-authority). Claims include relevant path aliases, Git and mutable resources; directory claims conservatively serialize path ownership. Admission requires completed, reconciled dependencies and available capacity/budget, including unresolved retained assignments.

For a worker, supply a stable dispatch ID, exact planned worker job, selection/control/discovery/guidance evidence, resource units and bounded brief. `_brief` binds files, resources and dependencies to that job and checks to accepted D5 definitions. Include applicable instructions, acceptance, self-review and stop conditions. Native review preparation instead uses the single [D5 review path](evidence.md#request-fields); worker preparation cannot admit a reviewer.

For managed native assignments outside Workstreams dispatch, only a newly committed response with `launch_admitted:true` permits one coordinator spawn, using its exact task name and prompt, with the accepted supported worker settings. Returned `requested_model`/`requested_effort` are null for inheritance; preserve the recorded inheritance rather than inventing overrides. Replay returns the same assignment without another permit. This is protocol admission, not native spawn idempotency. A lost response requires inspection of the same admission and native identity; never blindly respawn. Reservations coordinate participating owners without enforcing native tools, paths or external writers. Deadlines/resource units bound admission, not measured execution or usage.

Managed Workstreams preparation returns no launch permission. Use its [dispatch protocol](workstreams-protocol.md) to consume the one-use permission before native sending; disable/re-enable invalidates stale preparation.

## Observe and reconcile independently

Observe the prepared attempt with its actual `parent_turn_id` and `spawn_item_id`. Compatible later observations retain the same child identity; an exact duplicate is a no-op. Contradictions, missing history or collection failures retain uncertainty and ownership.

The prepared report contract and `_worker_report` define worker result fields: version, attempt ID, original assignment/candidate digests, status, changed paths, evidence and limitations. Worker status is `completed`, `blocked` or `pending-decision`. This report cannot prove passing checks, stopped descendants or completion. Reviewers retain D5's report contract and [separate digest domains](evidence.md#request-fields).

Review lifecycle observation may retain incomplete or mutated work; it does not accept review quality or release ownership. Perform collection and independent assessment through D5 separately.

Inspect the diff, actual checks and partial repository/shared-resource effects, including failed or interrupted work. Reconcile with an independent coordinator assessment and private digest-checked host proof. The [strict native proof](delegation-proofs.md#worker-or-reviewer) establishes that the exact worker and all writable descendants stopped. JSON flags and references do not authenticate host authority.

For successful standalone Codex CLI completion, use the version-3 [cooperative proof](delegation-proofs.md#cooperative-native-completion). The reader accounts for a bounded one-turn rollout with synchronous `apply_patch`, the [prepared marker wait](delegation-requests.md#prepare-worker) when declared, and tool-free review of supplied candidate material. Independently inspect inputs and effects and account for coordinator-issued follow-ups. Missing required evidence retains ownership.

On the selected qualification-canary path, a read-only reviewer may inspect the exact candidate already reconciled from its same-task cooperative worker. The worker retains its original preimage witness. The reviewer's own before record binds the current candidate, revision, generation and receipt watermark; it cannot become a pre-edit guard witness. Unexplained candidate changes still block admission. Registered checks retain their strict reconciliation requirements.

`cooperative-completed` releases claims and capacity for compatible operations of the same task. It allows accepted read-only checks, planned native dependencies and local qualification completion. Existing D4 recovery, ordinary D5 readiness and merge evidence require strict settlement. Supply genuine strict proof separately when those operations need it. Uncertain attempts retain claims and consumed budgets. Further work requires new admission. Automatic takeover is deferred.

A genuinely never-started admission requires independently inspected exact-parent/task-marker host evidence and no contrary observation. Missing history or an elapsed deadline cannot establish that branch. Recapture proof after intervening candidate, ownership or observation changes. Cancellation or interrupt acknowledgement does not release claims or restore consumed budgets. Failed/cancelled reconciliation does not complete dependencies. Apply policy's diagnosis and retry rules within original limits, then use [D5 verification](evidence.md#completion-and-limits) for readiness.
