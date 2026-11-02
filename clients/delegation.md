# Native delegation

Read this contract only when preparing, observing or recovering native delegated work. Apply [Agent Delegation](../policies/03-agent-delegation.md) for execution choice, authorization, worker selection, briefs, ownership, review and retry decisions. Use [task runtime](runtime.md#runtime-authority) for the approved shared state authority. Use [evidence](evidence.md) for native review admission, review quality, accepted checks and completion. Direct checks and coordinator resource claims do not need native guidance. Startup alone needs none of this procedure.

## Observe the route and select settings

The package provides Codex CLI/Desktop native observation. Qualify the actual surface and required controls before relying on them. Recorded version numbers are provenance, not support gates. Package validation does not establish observed native execution. Approval alone cannot make an unsupported route executable.

Use `discover_native_workers` in the [Codex adapter](../runtime/adapters/codex.py) to inspect current surface, native version and catalog with the coordinator's actual exposed dispatch controls. Select suitable worker settings under policy, retaining the control observation and expected capability digest. Explicit model/effort selection requires supported controls and `fork_turns="none"`; otherwise supply supported inheritance and its evidence only when suitable. Missing controls are limitations, not permission to change coordinator or global settings.

Keep requested settings, configured defaults, spawn-observed settings and per-turn execution facts separate. Unknown execution, provider or endpoint facts stay unknown. Historical D5 receipt fields retain their original meanings; see [review evidence](evidence.md#request-fields). Earlier D5 qualification does not qualify this linked schema-2 lifecycle.

## Use the relevant guidance

Call `guidance_status` with the observed `client_surface`, `applicability` (`client_version`, `capability_fingerprint`) and only relevant `profile_ids`. Retain source URLs/digests, checked time, applicability and immutable snapshot digest. Recommendations do not establish model availability, source verification or route authority; independently verify those facts.

When policy's refresh triggers apply, call `claim_guidance` before fetching and `record_guidance` afterward with the live token/generation and verified guidance or failure. The claim serializes publication; an expired or superseded claim cannot publish. Preserve the same trigger identity rather than retrying a failed refresh under invented IDs. Follow policy's compatible stale-cache reuse and dependent-only blocking procedure. No routine upstream fetch is needed merely because time passed.

## Prepare and launch once

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
revision for mutations. The action references include validator-checked examples;
implementation reads are for diagnosis, explicit implementation work, or the narrow
uncovered-schema lookups below. For trusted stop-proof construction, read only
`_validate_operation_proof` in [the validator](../runtime/delegation.py); for cache refresh
publication, read only `_common`, `_trigger` and `_guidance` there. These lookups
remain required until complete public references replace those specific schemas;
do not load the whole implementation for ordinary plan/prepare/observe requests. The runtime
does not choose jobs, budgets, settings or authorization.

Accept the D5 evidence plan, then an immutable D6 plan with exact jobs, claims, dependencies, finite admission limits and verified capacity. Apply policy's isolation and ownership rules under the [shared authority](runtime.md#runtime-authority). Claims include relevant path aliases, Git and mutable resources; directory claims conservatively serialize path ownership. Admission requires completed, reconciled dependencies and available capacity/budget, including unresolved retained assignments.

New worker plans and admissions require filesystem write claims (`path`, `build`, `database`) to stay within the task checkout. Declared and resolved paths, including named brief files, cannot cross an external or nested repository/submodule boundary. External reads and separately declared service/port coordination retain their existing rules. Directory descendants and unknown aliases are not exhaustively inspected; this is an admission check, not filesystem isolation. Existing plans and assignments remain readable for replay and recovery; a rejected new admission does not release their ownership or reset budgets.

For a worker, supply a stable dispatch ID, exact planned worker job, selection/control/discovery/guidance evidence, resource units and bounded brief. `_brief` binds files, resources and dependencies to that job and checks to accepted D5 definitions. Include applicable instructions, acceptance, self-review and stop conditions. Native review preparation instead uses the single [D5 review path](evidence.md#request-fields); worker preparation cannot admit a reviewer.

For ordinary delegation, only a newly committed response with `launch_admitted:true` permits one coordinator spawn, using its exact task name and prompt, with the accepted supported worker settings. Returned `requested_model`/`requested_effort` are null for inheritance; preserve the recorded inheritance rather than inventing overrides. Replay returns the same assignment without another permit. This is protocol admission, not native spawn idempotency. A lost response requires inspection of the same admission and native identity; never blindly respawn. Reservations coordinate participating owners without enforcing native tools, paths or external writers. Deadlines/resource units bound admission, not measured execution or usage.

Managed Workstreams preparation returns no launch permission. Use its [dispatch protocol](workstreams-protocol.md) to consume the one-use permission before native sending; disable/re-enable invalidates stale preparation.

## Observe and reconcile independently

Observe the prepared attempt with its actual `parent_turn_id` and `spawn_item_id`. Compatible later observations retain the same child identity; an exact duplicate is a no-op. Contradictions, missing history or collection failures retain uncertainty and ownership.

The prepared report contract and `_worker_report` define worker result fields: version, attempt ID, original assignment/candidate digests, status, changed paths, evidence and limitations. Worker status is `completed`, `blocked` or `pending-decision`. This report cannot prove passing checks, stopped descendants or completion. Reviewers retain D5's report contract and [separate digest domains](evidence.md#request-fields).

Review lifecycle observation may retain incomplete or mutated work; it does not accept review quality or release ownership. Perform collection and independent assessment through D5 separately.

Inspect the diff, actual checks and partial repository/shared-resource effects, including failed or interrupted work. Reconcile with an independent coordinator assessment and private digest-checked host proof using `reconcile_assignment`. Its version-2 native proof binds the exact admission, canonical claims, original/current candidate, original plan/pins, revision, latest observation/result and native identity. Establish that the exact worker and all writable descendants stopped; JSON flags and references do not authenticate host authority.

A genuinely never-started admission requires independently inspected exact-parent/task-marker host evidence and no contrary observation. Missing history or an elapsed deadline cannot establish that branch. Recapture proof after intervening candidate, ownership or observation changes. Cancellation or interrupt acknowledgement does not release claims or restore consumed budgets. Failed/cancelled reconciliation does not complete dependencies. Apply policy's diagnosis and retry rules within original limits, then use [D5 verification](evidence.md#completion-and-limits) for readiness.

## Compact progress

For monitoring, append `--compact` to `delegation status --cwd /checkout --client codex --session SESSION`. It projects recorded assignment state, unsettled/conflict counts, blockers and existing evidence references. At most 20 assignments are shown, unsettled first; `assignment_count` and `details_required` disclose omitted detail. Rerun without the flag when full conflicts, budgets, capacity or assignment detail is needed.

This view performs the existing bounded ledger validation, without new native observation or completion verification. Recorded `observed` or `settled` assignment state is not current native liveness or fresh readiness. Missing observation/reconciliation references remain null. Follow policy's event/bounded-wait guidance; collect current lifecycle/report evidence for acceptance, reconciliation or a specific diagnostic need. Ordinary task/evidence status retains its authoritative revalidation and should not be used as a progress heartbeat.
