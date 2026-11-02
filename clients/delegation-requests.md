# Delegation command requests

Read only the section for the current action. The installed validators remain the
schema authority; these examples execute against them in the documentation tests.
Implementation loading is unnecessary for the worked plan/prepare requests.
The explicit stop-proof/cache-publication exceptions below retain their authoritative
validator lookups. This reference adds
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

## Prepare worker

Choose a planned worker job and stable `dispatch_id`. Fill the exact brief using
its original claims/dependencies and D5 check IDs. Brief objective/context/self-review
are at most 8,192 characters; list entries at most 4,096, normally at most 64 items.
Instructions, acceptance, checks and stop conditions must be nonempty. File paths
are checkout-relative exact files covered by claims. Only dependency IDs may grow
with the plan. Resource units are positive and spend the original finite budget.

`control_observation` comes from exposed native controls. `discovery_expectation`
comes from current compatible discovery; it binds the fresh dispatch reader, not
a version allowlist. `guidance` copies snapshot reference/digest/time/freshness from
compatible cached guidance status. Profile IDs must exist there. Select model and
supported effort from the actual native catalog. Explicit mode requires explicit
model/effort and fork isolation controls; inherit mode adds
`inheritance:{model,effort,reference}` matching verified suitable settings. Preserve
coordinator settings/authentication. Examples are illustrative, not native evidence.

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
Never reset original attempts/resources/deadline. Managed child naming and delivery
use the [dispatch protocol](workstreams-protocol.md); do not add a combined
Workstreams revision to ordinary delegation or continuation.
Native review admission uses D5's [review preparation](evidence-review-prepare.md)
and [common request fields](evidence.md#request-fields),
not the worker brief above.

Return: `attempt_id`, task `revision`, `assignment_digest`, native `task_name`,
`candidate_digest`, requested settings and the exact `prompt` containing the
immutable assignment body and its pinned plan.
Ordinary workers launch only on `launch_admitted:true`; Workstreams preparation
instead returns `dispatch_prepared:true` and requires its separate consume step.
A replay is not another launch. Keep the exact marker, assignment and candidate
binding for subsequent observation; status is not delivery proof.

## Observe

Action `observe` adds `attempt_id`, `parent_turn_id`, `spawn_item_id` to the common
envelope. Use the actual original native parent turn and spawn event, not display
titles. Return: `attempt_id`, `status`, current `revision`, `observation_digest`,
`replayed` and limitations. Uncertain observation retains ownership. Observed
completion still requires independent effect/descendant reconciliation.

## Reconcile

Action `reconcile` adds `attempt_id`, `host_proof:{path,sha256}` and `assessment`.
For proof construction only, load `_validate_role_proof` in
[the authoritative validator](../runtime/delegation.py), including its complete
version-2 identity, native identity, candidate, plan, claim, observation and result
bindings. This reference defines the submission envelope, not a replacement proof
schema. Never replace that lookup with invented fields or weaker flags.
Proof path is absolute, private and content-hash bound to a trusted independently
inspected stop result. An assessment has `outcome` (completed, failed or cancelled),
`changed_paths`, `result_assessment`, `partial_change_assessment`, nonempty
`evidence` and `coordinator_ref`. Assessment text is bounded at 4,096 characters;
path/evidence lists at 64 entries of at most 4,096 characters. Do not manufacture
stop flags, report digests, lineage or a never-launched claim from missing history.
Follow [native reconciliation](delegation.md#observe-and-reconcile-independently) for establishing
the proof, then submit this envelope. Checks/coordinator operations have distinct
role proofs under [model-free controls](runtime.md#coordinator-resource-claims).

Return: `attempt_id`, settled `status`, current `revision`, reconciled `outcome`,
`replayed` and limitations. Only matching independently verified effects release
ownership. A replay requires the same semantic request. Failed/cancelled outcomes
do not complete dependencies; uncertainty never clears merely with time.

## Status

`delegation status --cwd CHECKOUT --client codex --session SESSION` takes no stdin.
It reports plan, assignments, conflicts, budgets, capacity and blockers. Request it
when those details are needed; preserve the latest mutation revision instead of
polling or repeatedly copying full historical assignment state into the chat.
Guidance cache controls use their [existing cache procedure](delegation.md#use-the-relevant-guidance)
and surface/applicability rather than the task envelope. Cache status takes
`client_surface`, `applicability:{client_version,capability_fingerprint}` and optional
`profile_ids`. Refresh claims additionally take `trigger:{kind,id}` (routine, manual
or maintenance), optional `lease_seconds` (1–600); publication takes the actual
`token`, positive `generation`, `coordinator_ref` and exactly one of `guidance` or
`failure`. For constructing a publication only, load `_common`, `_trigger` and
`_guidance` in [the validator](../runtime/delegation.py). Keep that bounded lookup
until a complete public publication schema replaces it; ordinary cache reuse does
not require it.
