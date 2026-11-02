# Workstreams dispatch and continuation protocol

Load for managed child identity and delivery details. For context planning,
ownership, naming and acceptance, use the [execution contract](workstreams.md).
Feature settings do not authorize ordinary delegation. Continuation ownership and
limits remain in base delegation; this file does not add a combined-mode permission.

Use the installed package's base workflow delegation actions with one JSON object
on stdin. Management actions are defined in the
[management procedure](../skills/workstreams/references/procedure.md). Execution
mutations carry the existing task identity (`client`, `session_id`,
`task_id`, `scope_ref`, `expected_revision`) and exact `attempt_id` where applicable.
Use the current task revision; a conflict requires reconciliation. Preparation
returns the token, revision, child binding and remaining task allowance without
repeating the prompt. Send-admitted consumption returns the exact stored prompt
for delivery. Delivery recording returns an acknowledgment without repeating that
prompt; private continuation evidence remains complete.

Ordinary delegated tasks may allocate base `continuation_limits` with
`max_continuations` and `backoff_seconds`. These limits and their ownership checks
apply with either or both optional features disabled. Ordinary prepare,
continuation preparation and delivery do not carry a Workstreams mode revision.
Use `resource_revision` only for resource admission and `recovery_revision` only
for capacity recovery. Existing recorded combined-mode fields remain historical
evidence; do not issue them for new ordinary work or treat them as feature grants.

| Action | Additional request fields and effect |
| --- | --- |
| `prepare-continuation` | Base delegation request: `reason`, `prompt`, `human_continue`, `host_proof`; optional `retry_at`. Bind a pending intent to the existing child and current evidence. |
| `consume-continuation` | Returned `token`; consume the one-use intent immediately before one native follow-up to the bound child. |
| `record-continuation` | Returned `token`, `delivery` (`sent` or `uncertain`), `delivery_ref`; preserve the delivery assertion for later independent observation. |
| Prepare or consume ordinary delegation | Use the base [delegation request contract](delegation-requests.md). Recheck task revision, ownership and the bound child. No Workstreams, resource or recovery feature revision is added. |
| Heavy local resource phase | Use `resource_revision` in the [resource request](workstreams-resources.md). This permits only that phase when its other task and ownership gates pass. |
| Parent recovery registration | Use `recovery_revision` in the [recovery request](workstreams-recovery.md). This permits only that registration when its task and schedule gates pass. |

Continuation still requires the base task's current ownership, finite remaining
allowance and stopped-writer/retry evidence. A delivery assertion cannot prove
that a native tool executed or all writers stopped. Do not resend on replay, a lost
response or an uncertain result. Use new observed native turns and base delegation
reconciliation to assess the actual outcome. Never resend a consumed intent merely
because its acknowledgment is missing.

`host_proof` is the existing private D6 stopped-writer proof `{path,sha256}`; follow
[native reconciliation](delegation.md) for its exact bindings and trusted
inspection. After a lost delivery record, human continuation requires a different
observed turn on the same child and a fresh exact stopped-writer proof.


## Immutable native names

For family `W42`, use the top-level `workstream_context_id:"WS42.01"` for a worker
or `workstream_context_id:"WS42.03"` for review. New assignments use this
top-level field; the existing `workstream_limits.context_id` fallback remains
supported. The adapter derives
`worker_ws42_01_build_<attempt>` or `review_ws42_03_review_<attempt>` from those
immutable requests, retaining the complete attempt suffix and native lineage.
These naming markers identify work only; they do not grant feature permission.
Ordinary delegation keeps its original markers. Review grouping remains available
when either feature is off. Context IDs are `WS` plus alphanumerics, a dot and a positive
decimal ordinal; use short labels to fit the validated 64-character creation marker.
Never truncate identity or use a display title as native lineage.
