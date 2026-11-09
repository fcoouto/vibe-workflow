# Proposal selection, verification and recovery

Use existing coordinator task scoping, native worker selection and independent
review contracts. API proposals receive only the bounded approved task packet;
no shell, tools, native connector access, external writes or reviewer authority.
For foreground work, load [Codex host admission](codex-host.md). For optional
local tests, drive the private runner with a connection factory and credential resolver fixture.

For external consideration and reusable source permission, apply the [shared contract](../../../clients/external-delegation.md).

## Admission and execution

1. Preserve explicit provider/model/local choices, scope and transmission consent.
   Freeze the source snapshot, task, profile, reasoning, output allowance and price
   evidence. Track each original task through native integration and review.
2. Select a bounded eligible assignment. Manual needs explicit task selection.
   Smart Balance may choose within standing consent. Require independently
   checkable output and a ready native reviewer. Unknown consent, funding, price, expiry or budget pauses the API assignment.
3. Pin current consent and admission evidence through atomic begin/dispatch. Count
   and generation have separate attempt IDs under one run, which holds its own claim. Reserve count's
   known maximum first; generation needs its own bound after the exact count.
   Never assume counting is free or shrink the maximum using cache discounts.
4. Prepare IO before the final atomic dispatch grant. Disable ordered before that
   grant prevents admission. Work already granted dispatch is accounted as
   in-flight; a later disable cannot guarantee physical cancellation or no send.
   Never hold a database lock over network IO. Check profile and funding freshness and expiry deadlines before both sends. Client timeout is
   not proof of server termination.
5. Return bounded untrusted proposal text with `accepted:false` and
   `requires_native_review:true`. Snapshot-check native integration, run accepted
   checks, and obtain a fresh independent reviewer against the exact candidate.
   Author rationale or model-produced receipts cannot replace that review.
6. Include the route in the approval or result summary and report the outcome,
   review state and any pause reason. If the host uses a real native supervisor,
   prepare its [route suffix](../../../clients/worker-visibility.md#external-routes-in-chat-and-habblaud)
   before dispatch. Use the applicable host procedure for optional Habblaud
   visibility. Keep prompts, credentials and raw responses out of telemetry.

## Assignment and execution evidence

Associate the proposal and its native continuation or review with the original
task through existing context and evidence references.

| Fact | Source |
| --- | --- |
| Task, scope, bounded brief, source snapshots, role, specialty and custom name | Coordinator assignment context and original visibility declaration |
| Requested selection | Proposal `provider` (`openai-api` or Smart `auto`) and existing local and API-only choices |
| Actual route | Successful generation result `execution_route: openai-api` from the runner. The host verifies it. A replay of stored state makes no execution claim. |
| Attempts and costs | Existing ledger run ID, `<run_id>:count`, `<run_id>:generation`, consent scope, admission pin and generation state and cost |
| Configured and observed model | Approved profile and validated provider response. The display name is for presentation. |
| Native child | Observed native spawn and its parent and attempt. Foreground generation creates no child. |
| External display | Stable UUIDv5 derived from actual route, observed parent and run ID. The existing receipt retains the original declaration and binding. |
| Native handoff and review | Existing native selection, one-use handoff claim, independent review and completion evidence. External completion grants no acceptance. |

Keep native supervisors, handoff workers and reviewers under their own native
identities. Use actual native observations for D6. External display uses the
canonical role and retains any custom role name in the declaration.

## Recovery

Each attempt sends at most once. A repeated run/attempt ID returns stored state,
never another generation. A host transport result proving no send can reject that
attempt with zero cost and close its claim. Sent or uncertain failures retain
maximum reservations and unresolved exposure; do not infer zero cost from
elapsed time, process death or a refreshed balance. No automatic retry, provider
switch, new purchases or paid funding is permitted.

Count completion does not authorize generation. If later consent, reviewer readiness,
source, price or affordability checks fail before sending generation, retain the
count charge and release only the proven-unsent generation reservation. A scoped
correction uses a new run within the original task budget; it requires reasoned selection under the accepted envelope.

Known generation completion can close the run's claim while retaining credit
charges. Native integration and independent review establish task completion.
An account-wide change alone does not measure the cost of one task.
Unknown or insufficient evidence never waives review.
Existing conservative deductions can pause before provider credits run out.

## Continue remaining work natively

Credit/budget/freshness/expiry stops recommend native handoff under the user's
standing authorization for the original task. Preserve any later explicit API-only
or other-provider choice and wait if the independent reviewer is unavailable. Send
one bounded remaining-work brief and fresh source references, not billing pages or
the whole conversation. Preserve coordinator model/authentication and existing
native worker selection; do not use an API-key or paid-provider substitute.

Request/refresh the handoff, then claim its stable dispatch attempt before existing
native tools. Only a new claim may start a child. Replayed claims observe the
existing attempt; they never respawn after uncertain host dispatch. Claim metadata
is not execution, review or completion evidence. Unclaimed handoffs recheck the
latest task choices and reviewer readiness.

Native continuation can proceed independently of a pending tool-free API result,
with all API maximum charges and unresolved claims retained. Do not duplicate external
actions, assume upstream cancellation or accept late API text without fresh checks.
The native agent owns remaining implementation/checks and still needs an independent
reviewer before acceptance. Complete or abandon under the existing native workflow;
no handoff releases API accounting merely because a child started.
