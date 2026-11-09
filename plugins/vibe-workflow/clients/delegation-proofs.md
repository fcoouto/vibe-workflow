# Reconciliation proof fields

Load only for independently reconciling an admitted operation. These schemas
bind independently observed facts to the original admission.
Retain the actual observation required by the chosen proof kind. Write its JSON to a private,
absolute, nonsymlink path outside repositories and installed package/cache trees;
submit its exact SHA-256 through `host_proof`. Strict stopping proofs must never infer stopping from elapsed
time, a report, a parent exit, or an empty process group.

Every version-2 proof has exactly these common fields:

| Field | Source |
| --- | --- |
| `version` | Integer `2` |
| `kind` | Role value below |
| `identity` | Original `client`, `session_id`, `task_id`, `scope_ref` |
| `expected_pins` | Original task pins, unchanged |
| `expected_revision` | Current positive task revision |
| `scope_id`, `attempt_id`, `dispatch_id` | Original admitted assignment |
| `assignment_digest` | Digest of its immutable assignment artifact |
| `claims_digest` | Digest of its canonical admitted claims |
| `observation_ref`, `coordinator_ref` | Nonempty references to the independent observation and assessment |

Digests are lowercase SHA-256. Use retained runtime values; do not hash a rendered
prompt when an assignment artifact digest is required. Unknown fields reject.
Recapture after relevant task, candidate, observation or ownership changes.

## Worker or reviewer

Use `kind:"assignment-writer-reconciliation"`. Add `candidate_digest` from the
original admission, a freshly captured `current_candidate_digest`, `d6_plan_digest`,
`observation_digest`, `result_digest`, `native_identity`, `launch_state`,
`stopped:true` and `descendants_stopped:true`.

`native_identity` contains exactly `client_surface`, original `native_version`,
`parent_session_id`, `task_name`, `parent_turn_id`, `spawn_item_id`, `worker_id`,
`worker_turn_id`, `result_item_id`. The first four come from admission; the last
five come from the latest observed native receipt, including null when absent.
`observation_digest` is that observation artifact's digest; `result_digest` is
the receipt's result digest. Neither is a digest of the report text.

`launch_state` is `observed` when an observation exists. A truly unlaunched
admission uses `not-started`, null observation/result digests and null receipt
identity fields, backed by independent inspection of the exact parent/task marker.
Missing history does not establish this branch. Review proofs also require the
original linked D5 review attempt and plaintext assignment binding.

Native V1 `codex-native-collab-call` receipts correlate the completed spawn call's
prepared JSON envelope with the immutable assignment, original parent and independently
read child origin. Its `agent_path` is the actual optional native source value, or
null; no path is inferred. D5 plaintext assignment and D6 ledger digests remain distinct.
Collection and assessment retain ownership until the exact independent stop proof.
Fixture compatibility does not qualify native execution, read-only enforcement or
successful close. Close, archive, interrupt, a completed turn and parent exit alone
do not prove that native, tool, background or resource descendants stopped.

## Cooperative native completion

For a successful standalone Codex CLI worker or reviewer, use proof `version:3`
and `kind:"native-cooperative-completion"`. Use the common and native identity
fields above, with version 3 in place of version 2. Supply `activity_digest`,
`no_outstanding_coordinator_followups:true` and
`coordinator_submissions_paused:true` instead of the two stop flags.
Use `launch_state:"observed"` and the exact completed observation. Hash the
returned `activity` object as UTF-8 JSON with sorted keys, ASCII escapes and
separators `(',', ':')`, then SHA-256.

The coordinator must have no admitted or issued follow-up outstanding for this
assignment and must pause new submissions through reconciliation. These are
coordinator-trusted statements about its own actions. Independently inspect the
exact tool inputs, completed foreground activity and current candidate. Use the
same `coordinator_ref` as the assessment and include `observation_ref` in its
evidence list. Trust ordinary native completion; do not claim observation of
hidden host queues or retries. Missing required results, known pending work,
contradictory activity or unexplained effects retain ownership.

The result is `cooperative-completed`. The runtime rereads the exact native
activity, verifies effects and rechecks the candidate, ownership and pins before
release. This permits compatible dependent work, not final acceptance by itself.
Applicable checks and independent review still decide result acceptance. Stronger
consumers require their strict proof. A later strict reconciliation of the same
admission retains its cooperative evidence. Qualification covers the recorded
Mac CLI canary. Other routes need their own evidence.

**Retained version 2:** the same cooperative kind uses `activity_digest` and
`no_pending_continuation:true`. Its original stronger contract requires observing
the owning host through completion, checking loaded/idle status, the submission
queue and pending retry or continuation, with no new submissions through
reconciliation. A fresh history reader or empty queue alone cannot establish
that statement. Keep those records and requirements unchanged; never substitute
version-3 attestations or infer the old boolean from weaker evidence. Mixed
version fields reject. Strict proof kinds elsewhere remain version 2.

## Check

Use `kind:"check-writer-reconciliation"`. Add original `candidate_digest`, fresh
`current_candidate_digest`, `plan_digest`, `execution_digest`, `stopped:true` and
`descendants_stopped:true`. The execution digest is the exact ledger observation
digest, or null if no execution result was durably recorded. Missing execution
cannot prove success. Check semantics and legacy readers remain in
[check reconciliation](evidence-checks.md).

## Coordinator operation

Use `kind:"coordinator-operation-reconciliation"`. Add the original `decision_ref`,
`operation_id`, `operation_digest`, `operation_evidence`, `resource_observations`,
`operation_stopped:true` and `operation_descendants_stopped:true`.

The two evidence fields are registered artifact references `{name,digest}`.
Each artifact must contain matching `operation_id`, `operation_digest` and
`claims_digest`, alongside the independently observed facts. Include
`d5_plan_digest` and/or `d6_plan_digest` exactly when the admitted operation has
those plans. Include original `candidate_digest` and fresh
`current_candidate_digest` exactly when admission has a candidate. Task-only
operations must not invent these optional bindings.
