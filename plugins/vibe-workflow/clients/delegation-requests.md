# Delegation command requests

Read only the section for the current action. The installed validators remain the
schema authority; these examples execute against them in the documentation tests.
Implementation loading is unnecessary for the worked plan/prepare requests.
Stop proofs and cache publication load their complete operation-specific references below. This reference adds
no permissions, native capability or passing evidence.

## Common envelope

Use `python3 -I -B <package>/scripts/workflow.py delegation ACTION --cwd CHECKOUT`.
Mutations accept one JSON object on stdin: `client:"codex"`, `session_id`, `task_id`,
`scope_ref` and the current integer `expected_revision`, plus the action payload.
Copy identity from the accepted task and revision from its latest response/status.
Replace every angle-bracket placeholder with actual values; a reference string is
an assertion, never proof of capability. JSON payloads below omit this common
envelope. Input is bounded at 128 KiB; extra fields reject. Revision conflict means
read current state and reconcile intent, not reset a budget or replay a native send.

## Plan

Accept D5 evidence first. The immutable D6 plan records native route/controls,
shared ownership, task-selected finite budgets, fallback and dependency jobs.
A check job uses the registered D5 check ID as its job ID. That check must satisfy
its D6 dependencies before reserving or executing.
`git_directory` is observed internally; do not supply it. Roles are worker, review,
coordinator or check. Review claims must be read-only. Claims use `kind`, `key`,
`mode`; path keys are absolute, service/port claims also require their execution
`host:"local"`; ports use `tcp:N` or `udp:N` with N from 1 to 65,535. File aliases and overlapping directory claims remain validated.

Budget integers are positive. Deadline is future Unix seconds; resource units
remain at most 1,000,000,000 and deadline below 2^63. Worker concurrency must fit
observed host capacity (maximum 1,024). Job attempts must fit task attempts;
dependencies name distinct existing jobs without cycles. Byte/ledger bounds remain;
no fixed 32-job ceiling applies. Fallback is stop or explicitly authorized
direct-coordinator. Obtain capacity/route/ownership references from actual checks.

<!-- example:plan -->
```json
{
  "plan": {
    "version": 1,
    "route": {
      "mechanism": "codex-native-subagent",
      "client_surface": "codex-cli",
      "parent_session_id": "<SESSION>",
      "authority_ref": "coordinator:verified-existing-native-route",
      "controls_ref": "coordinator:exposed-tools-checked"
    },
    "authority": {
      "state_dir": "<STATE_DIR>",
      "participants_ref": "coordinator:participating-owners",
      "ownership_known": true
    },
    "selection_constraints": {
      "complexity": "Coupled code",
      "risk": "Concurrency and security",
      "rationale": "Native worker isolates bounded context",
      "required_controls": [
        "spawn"
      ]
    },
    "limits": {
      "max_attempts": 8,
      "max_resource_units": 8,
      "deadline": "<DEADLINE>",
      "max_parallel_workers": 4,
      "host_capacity": 4,
      "capacity_ref": "coordinator:observed-slots"
    },
    "fallback": {
      "action": "stop",
      "reference": "user:original-budget"
    },
    "jobs": [
      {
        "id": "edit",
        "role": "worker",
        "claims": [
          {
            "kind": "path",
            "key": "<CHECKOUT>/app.py",
            "mode": "write"
          }
        ],
        "dependencies": [],
        "max_attempts": 3
      }
    ]
  }
}
```

Return: `ok`, current `revision`, immutable `plan` reference and `plan_digest`, plus
limitations. Plan acceptance starts no native worker and spends no worker attempt.

Each job may carry an optional `profile` the coordinator fills: `task_family`,
`complexity_band`, `risk_band` (`low|medium|high|critical`), `context_tokens`, `tools`,
`review_policy` (`none|self|independent|strict`) and `uncertainty`. Code validates it;
unknown fields are rejected. Missing or invalid fields take conservative floors, and no
supplied value can lower a floor from write scope or job requirements. This task profile
is separate from guidance `profile_ids`, which only reference cached recommendations.

## Prepare worker

Choose a planned worker job and stable `dispatch_id`. Fill the exact brief using
its original claims/dependencies and D5 check IDs. Brief objective/context/self-review
are at most 8,192 characters; list entries at most 4,096, normally at most 64 items.
Instructions, acceptance, checks and stop conditions must be nonempty. File paths
are checkout-relative exact files covered by claims. Only dependency IDs may grow
with the plan. Resource units are positive and spend the original finite budget.

For `visibility` and the CLI's automatic `visibility_preparation`, follow the
shared [role preparation](worker-visibility.md#declare-before-dispatch).

`control_observation` comes from exposed native controls. `discovery_expectation`
comes from current compatible discovery; it binds the fresh dispatch reader, not
a version allowlist. `guidance` copies snapshot reference/digest/time/freshness from
compatible cached guidance status. Profile IDs must exist there. Outside a managed
project, select model and supported effort from the actual native catalog. Explicit
mode requires explicit model/effort and fork isolation controls; inherit mode adds
`inheritance:{model,effort,reference}` matching verified suitable settings. In a
[managed project](../policies/03-agent-delegation.md#managed-projects), policy sets
the mode: `routed` omits `model` and `effort`, and routing checks the admitted route
against the same catalog; explicit and inherit settings become hard routing
constraints. Preserve coordinator settings/authentication. Examples are
illustrative, not native evidence; the example below is unmanaged explicit mode.

<!-- example:prepare -->
```json
{
  "dispatch_id": "request-1",
  "job_id": "edit",
  "resource_units": 1,
  "brief": {
    "objective": "Implement bounded edit",
    "context": "Inspect existing callers first",
    "instructions": [
      "Test first; preserve validation"
    ],
    "files": [
      "app.py"
    ],
    "resources": [
      {
        "kind": "path",
        "key": "<CHECKOUT>/app.py",
        "mode": "write"
      }
    ],
    "dependencies": [],
    "acceptance": [
      "Required behavior verified"
    ],
    "checks": [
      "unit"
    ],
    "self_review": "Inspect diff and callers",
    "stop_conditions": [
      "Stop for review after this assignment"
    ]
  },
  "selection": {
    "model": "<MODEL>",
    "effort": "high",
    "rationale": "Capable native model for exact scope",
    "profile_ids": [
      "<PROFILE_ID>"
    ],
    "mode": "explicit"
  },
  "control_observation": {
    "version": 1,
    "client_surface": "codex-cli",
    "native_version": "<NATIVE_VERSION>",
    "parent_session_id": "<SESSION>",
    "dispatch_controls": {
      "spawn": true,
      "explicit_model": true,
      "explicit_effort": true,
      "fork_turns_none": true
    }
  },
  "discovery_expectation": {
    "client_surface": "codex-cli",
    "native_version": "<NATIVE_VERSION>",
    "capability_digest": "<CAPABILITY_DIGEST>",
    "parent_model_provider": "<PROVIDER>",
    "parent_session_id": "<SESSION>"
  },
  "guidance": {
    "snapshot_ref": {
      "name": "<GUIDANCE_NAME>",
      "digest": "<GUIDANCE_DIGEST>"
    },
    "snapshot_digest": "<GUIDANCE_DIGEST>",
    "checked_at": "<GUIDANCE_TIME>",
    "freshness": "<GUIDANCE_FRESHNESS>"
  }
}
```

Ordinary delegation may allocate immutable `continuation_limits` with
`max_continuations` and `backoff_seconds`. Shared continuation and acceptance
checks apply whether resource admission and capacity recovery are on or off.
Ordinary plan, prepare and continuation requests require neither feature revision.
Only a heavy resource operation uses `resource_revision`; only parent recovery
registration uses `recovery_revision`. New `delegation prepare` and
`delegation coordinator reserve` requests reject `workstream_revision` or
`workstream_limits` before reserving work. Existing original records and their
readers retain their historical fields.

Optional retry fields: `retry_of` is the original attempt; `diagnostic` records
`prior_attempt_id`, `cause`, `evidence`, `change` after diagnosis.
Never reset original attempts/resources/deadline. Managed child delivery
uses the [dispatch protocol](workstreams-protocol.md); do not add a combined
Workstreams revision to ordinary delegation or continuation.
Native review admission uses D5's [review preparation](evidence-review-prepare.md)
and [common request fields](evidence.md#request-fields),
not the worker brief above.

For the selected managed child-hook witness, an approved standalone Codex CLI worker may include `brief.foreground_wait`
with absolute canonical `marker` and `python` paths and integer `seconds` from
1 to 20. Reserve the exact marker path for reading. Its observer owns a private
directory outside the checkout and starts with the marker absent. Use
`foreground_wait_args` in `runtime/adapters/codex.py` to generate the exact
`exec_command` arguments for the private packet and worker instructions before
prepare. The prepared brief pins the descriptor and replay cannot change it.
The generated wait uses `/bin/sh`, a non-login, non-TTY command and a yield
window longer than its own deadline. Use a fresh marker per attempt.
Publish the marker only after independently confirming the original child and
turn are observed and the parent can authorize the patch. End dependent work
if the bounded wait fails or returns a resumable session.

Use the wait only for a route that declares it. The standalone CLI qualification
canary can bind the exact patch to the prepared child claim through paired hook
receipts without a marker. Preserve the selected route's admission rules.

Return: `attempt_id`, task `revision`, `assignment_digest`, native `task_name`,
`candidate_digest`, requested settings and the exact `prompt` containing the
immutable assignment body and its pinned plan.
Managed native workers outside Workstreams dispatch launch only on `launch_admitted:true`; Workstreams preparation
instead returns `dispatch_prepared:true` and requires its separate consume step.
A replay is not another launch. Keep the exact marker, assignment and candidate
binding for subsequent observation; status is not delivery proof.

## Observe

Action `observe` adds `attempt_id`, `parent_turn_id`, `spawn_item_id` to the common
envelope. Use the actual original native parent turn and spawn event, not display
titles. Return: `attempt_id`, `status`, current `revision`, `observation_digest`,
`replayed` and limitations. Uncertain observation retains ownership. Observed
completion still requires independent reconciliation. Set optional
`cooperative:true` only when collecting a completed turn for the supported
cooperative path. A successful observation adds the bounded `activity` object.

## Reconcile

Action `reconcile` adds `attempt_id`, `host_proof:{path,sha256}` and `assessment`.
For proof construction, load the complete role schema in
[reconciliation proof fields](delegation-proofs.md).
Proof path is absolute, private and content-hash bound to independently inspected
facts. An assessment has `outcome` (completed, failed or cancelled),
`changed_paths`, `result_assessment`, `partial_change_assessment`, nonempty
`evidence` and `coordinator_ref`. Assessment text is bounded at 4,096 characters;
path/evidence lists at 64 entries of at most 4,096 characters. Do not manufacture
stop flags, report digests, lineage or a never-launched claim from missing history.
Follow [native reconciliation](delegation.md#observe-and-reconcile-independently) for establishing
the proof, then submit this envelope. Checks/coordinator operations have distinct
role proofs under [model-free controls](runtime.md#coordinator-resource-claims).

Return: `attempt_id`, `status` (`settled` or `cooperative-completed`), current `revision`, reconciled `outcome`,
`replayed` and limitations. Only matching independently verified effects release
ownership. A replay requires the same semantic request. Failed/cancelled outcomes
do not complete dependencies; uncertainty never clears merely with time.

## Status

`delegation status --cwd CHECKOUT --client codex --session SESSION` takes no stdin.
Append `--compact` for recorded progress. It returns at most 20 assignments,
unsettled first, with task/attempt identity, recorded state and existing observation
and reconciliation references. Missing references remain null. Counts and blockers
cover all assignments; `assignment_count` and `details_required` disclose omitted
rows or conflict detail. Omit the flag when acceptance, reconciliation or diagnosis
needs the full assignments, conflicts, budgets or capacity.

Compact status performs existing bounded ledger validation, without native
observation, completion revalidation or title synchronization. It establishes no
current readiness, permission, liveness or stopped ownership. Follow policy 03's
[progress retrieval rules](../policies/03-agent-delegation.md#progress-and-result-retrieval).
Ordinary task/evidence status retains authoritative revalidation; preserve the
latest mutation revision instead of using those commands as progress heartbeats.

Guidance cache controls use their [existing cache procedure](delegation.md#use-the-relevant-guidance)
and surface/applicability rather than the task envelope. Cache status takes
`client_surface`, `applicability:{client_version,capability_fingerprint}` and optional
`profile_ids`. Refresh claims additionally take `trigger:{kind,id}` (routine, manual
or maintenance), optional `lease_seconds` (1–600); publication takes the actual
`token`, positive `generation`, `coordinator_ref` and exactly one of `guidance` or
`failure`. For publication fields and bounds, read
[guidance publication](guidance-publication.md); ordinary cache reuse does not
require that reference.
