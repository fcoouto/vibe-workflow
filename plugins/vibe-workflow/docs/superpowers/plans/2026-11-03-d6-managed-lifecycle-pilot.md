# Held D6 managed-hook qualification record

**Historical stronger track; on hold. Live execution allowance is zero.**
This record preserves the managed setup and child-hook qualification design.
It is not the acceptance checklist for cooperative native delegation. Use the
[canonical D6 plan](../../d6-native-delegation-plan.md) and the short
[successor packet](2026-11-04-d6-successor-release-packet.md) for the core flow.
No boot, Docker tests, VM/Colima launch, live qualification or plugin installation
is authorized. The Lima/Docker preflight remains retired.

Load this record only when selecting managed setup or qualifying its child-hook
protection. Its marker wait, exact canary and witness ordering serve that guard.
They must not become default requirements for ordinary native delegation.
Conversely, do not remove the barrier and claim the existing guarded child path
works: that path still requires its actual supported admission and observation.

## Current disposition

- Reuse existing D4 setup and original authority only where the selected managed
  route requires them. Do not repeat the full setup campaign to certify basic
  delegation or manufacture fresh state to escape a retained claim.
- Preserve this record's exact canary and original ceilings for any later attempt
  using this stronger design. A new core pilot has its own bounded scope and
  does not reset an older attempt's limits.
- Version-3 [cooperative completion](../../d6-native-delegation-plan.md#normal-completion-and-uncertain-ownership)
  now has offline coverage. Retained version-2 proofs keep their original stronger
  observation requirement. Neither this update nor a fixture retroactively
  settles an admission or qualifies native execution.
- D5 registered-check reconciliation, strict readiness and D7 merge acceptance
  retain their own requirements. A child-hook observation cannot grant them.

## 1. Source and preservation boundary

The original reviewed plan used main as observed on 3 November 2026.

| Identity | Baseline |
| --- | --- |
| Main commit | `13d409dfbe571a35ae015ff10d696a1cca191e48` (PR64) |
| Git tree | `5f628fd0a440fca35272bfedc89bb678c2455351` |
| Package version | `0.1.2`; version alone does not establish matching contents |
| Policy digest | `1ab48d6f72b59027fa072f25b51b717df67466227964ec5070f00eb1adcdab6b` |
| Runtime digest | `6915ddd4d8c92cf7705766846854bd7579c6538a5b065cd488f194dc9ab5d268` |
| Current schemas | Canonical Project authority 2; runtime configuration and SQLite 3 |
| Existing software evidence | PR64 records cumulative 207/207 selected cases; this is not native qualification or a repository-wide suite |

### 4 November 2026 offline checkpoint

[PR #78](https://github.com/fcoouto/vibe-coding-workflow/pull/78) merged into
`main` as `4ec6860458a5963c3595f10f6f802e80e4023caf`. The reviewed fix is
`a020394868d1b9032fce9bf67354bb188d4823c1`. Record the merged source as the
offline evidence checkpoint. A future release requires its own exact pins.

| Identity | PR #78 checkpoint |
| --- | --- |
| Git tree | `2c785936422247953971b1c3d82859a624eaf12e` |
| Source package version | `0.1.8` |
| Source policy digest | `12a8ecdf666fd6408a510008a4c0e2c7eb906f41b3b9b371b62a4c362cd6607e` |
| Source runtime digest | `c15e071395a0e7439feffed86c24027d503cd2119c43bcd03b79998bf136ca1a` |

The regression first reproduced the legitimate child's denial through the
hook and owner code with a disposable native transport fixture. The fix admits
the exact observed child and turn for the approved one-file canary, denies
unbound or foreign children, mismatched turns and out-of-scope patches, and
binds the PostToolUse receipt to the same child, turn, call and assignment.

All 272 affected tests passed (51 native-hook, frontend-witness
and manifest tests, 214 runtime, qualification, admission, delegation and
readiness tests, and 7 retention and linked-closure tests). Package validation
and diff checks passed. Independent behavior and security review cleared the
reviewed diff after focused rechecks. Two focused regressions and package
validation passed again before merge. Full discovery was superseded by the
affected-suite runs. Live managed child hook delivery and stop proof remain
unverified.

Recheck main, package contents, applicable instructions and observed capabilities
before release. A changed relevant source requires a reviewed successor packet and
affected checks. Never overwrite original pins, receipts, consumed limits or old
evidence. Installation must not replace a same-version cache used by another task.

Use one new public-data canary repository and its explicit standalone membership,
one fresh private runtime authority, one private D4 journal and a frozen package
copy. Exact canonical paths, physical bindings and native configuration effects
belong in the release packet. No parent/sibling enrollment or future auto-joining.
Preserve primary WIP, all other worktrees, installed packages, native homes and
existing authority/evidence. Do not hide or move machine history to force absence;
D4 must use genuine ready history or its genuine version-2 absence witness.

## Optional role display

The shared [worker visibility contract](../../../clients/worker-visibility.md)
now owns role, specialty, custom-name and actual native/external identity rules.
[PR #70's retained observations](2026-11-03-shared-worker-roles.md) cover its
bounded delivered behavior. Preserve declared labels, first-binding order,
continuations, unsupported-name reporting and existing privacy constraints.
Do not recreate a separate D6 display policy or registry. Habblaud verification
is optional and needs its own selected route; missing display evidence does not
block delegation. OpenRouter execution, credentials and spending remain outside
this native pilot. No new display implementation or activation is authorized.

## Deferred exact guard fixture

The following fixture and checks belong only to the stronger guarded route.
They remain historical planning inputs, not an executable release packet.

### Proposed public fixture

Freeze these UTF-8, LF-only bytes in the private effect manifest. File contents
end in one LF. The patch command ends immediately after `*** End Patch`.

`canary.py` before the worker runs

```python
def value():
    return 0
```

`test_canary.py` remains read-only

```python
from canary import value

assert value() == 42
```

```text
*** Begin Patch
*** Update File: canary.py
@@
-    return 0
+    return 42
*** End Patch
```

| Artifact | SHA-256 |
| --- | --- |
| Canary before | `4480052910accecafe6f206dbd8be95b8447320ff6d2332761858fd6399c1255` |
| Canary after (`return 42`) | `2f8974f06364c943a202b6a6971a24e891683fa2dc6233ad2a5c478dce8fd0b3` |
| Read-only test | `1b06d7b982eed8cb250c2037c2c7fbfaf4aa1ece3d106cba880d980da4ae8497` |
| Admitted patch | `c36d9a0b62214238eb4c98765c2f8bae25dd674cb9883a3569fae81e712683a2` |
| Denial patch (`+    return 41` in place of `+    return 42`) | `819c28ebe7290add2c91ae98839dd94ec74413b0d2b2a232f01a750bd804d14f` |

Use the pinned Python executable with `-I -B -c` and the exact
argument `import sys; sys.path.insert(0, "."); import test_canary` from the
canonical checkout. The baseline must fail on the assertion with the exact
preimage. Both registered post-edit executions must exit zero on the reviewed
postimage. Freeze the interpreter path, timeout of at most 10 seconds, minimal
environment and read-only effects in the D5 plan.

## Staged child guard checks

| Stage | Required observation and decision |
| --- | --- |
| D4 bootstrap | Under the separately released setup effects, capture genuine CLI provenance, restricted SessionStart context, bootstrap policy rejection, hook trust and registration, and independent canary facts. Use the exact disposable qualification-purpose task and original allocations. `task-start` and `task-patch` require the pre-tool key. Worker admission requires post-tool, Stop and Interrupt keys. Reviewer admission adds review-readiness. An allocation permits the listed bounded probe with pending cases. It does not mark those cases verified. |
| Observe the child before allowing mutation | Accept the D5 plan, then the D6 plan. Prepare one worker with the exact `foreground_wait` descriptor, marker read claim and generated command in its brief. Pin the interpreter and the native route. Use the permit once. Capture the actual parent turn and spawn item. Observe the exact child and first turn as `inProgress`. The observer publishes the fresh private marker only after the exact receipt and working parent are confirmed. The bounded command must complete successfully before patches, without a resumable tool session. If the frontend cannot complete the wait and patch in the same turn, hold. |
| Parent pre-tool reference, when required | While the original parent is still editable and `TRIAGE`, after assignment observation and before mutation, use the exact approved canary patch to obtain a native parent-hook denial. Capture the current parent task turn and unchanged canary while that evidence is current. This is the existing formal parent `pre-tool-enforcement` case. Then confirm `WORKING` before releasing the child barrier. |
| Child denial | After exact binding, use one separately listed, syntactically valid patch to the same disposable file with a different postimage. Use its own native call ID. Require a genuine workflow PreToolUse denial, unchanged canary and no successful patch completion. Stop on missing callback or unexpected effect. Keep foreign session, mismatched turn, malformed payload, timeout and Post collision cases in offline fixtures. |
| Child success and paired witness | In the same admitted turn, invoke canonical `apply_patch` once with the exact approved command. Keep the observed child turn and working parent current until PostToolUse persists. Record actual hook session, turn, call and command plus the result. Require the expected one-file effect. Use `observe_managed_case` with `stream='child'` and `case_id='post-tool-handling'`. Independently read the exact native rollout, ordered fresh pre/post receipts, assignment-bound fingerprint and final canary. The generation delta must match all fresh unconflicted Post receipts in the observation window. Collect during a quiet window and account for all tool completions. |

Use direct `apply_patch` with `tool_input.command` and native session, turn and
call IDs. A code-mode or sandbox rejection alone does not establish hook
delivery. The formal pre-tool witness is parent-scoped. The child witness binds
a successful Post to its admitted Pre. Retain child denial evidence separately
with native rollout, actual hook result and unchanged bytes. Current source
does not issue a formal child denial witness. Resolve any requirement for one
before release. [Pinned Codex patch handler](https://github.com/openai/codex/blob/rust-v0.165.0/codex-rs/core/src/tools/handlers/apply_patch.rs)

The [setup owner](../../../runtime/setup_observation.py) accepts one managed
witness per successor collection. Parent pre promotion rechecks the unchanged
canary. If formal promotion of both cases is required, prove a supported
publication order offline before release. Preserve original task pins and
predecessor receipts. The held lifecycle pilot can retain separate scoped
observations without granting ordinary activation. Reconcile the qualification
guide's parent-only wording with the current child Post path in the reviewed
packet.

## 4. Proposed finite ceilings for one future CLI release

| Resource | Hard admission/protocol ceiling |
| --- | --- |
| Overall window | 60 minutes from the first authorized campaign effect; one absolute deadline |
| Phase windows | Read-only preflight 10 min; controlled setup/bootstrap 15 min; managed scenario 20 min; reconciliation/report 15 min. These sum to 60, not extra time. |
| Native contexts | One coordinator and two children total; worker and reviewer run sequentially |
| New model turns | At most five: coordinator bootstrap/scenario/closure at most three; worker one; reviewer one |
| Native attempts/resources | D6 `max_attempts=2`, `max_resource_units=2`; each child consumes one unit; each native job `max_attempts=1` |
| Concurrency | `max_parallel_workers=1`; record actual host capacity and evidence of an available slot; do not relabel physical capacity as one |
| Original qualification allocation | At most eight unique admitted ledger operations and two resource units, including applicable check/coordinator history; verifier-barrier accounting follows current source |
| Child windows | Worker 5 min; reviewer 7 min, both inside the managed-scenario window |
| Review | One independent review round; blocking findings end this pilot rather than start a fix/retry campaign |
| Checks | At most three registered D5 check executions. Each canary command has a 10-second timeout. |
| Control traffic | At most 32 D5/D6 control requests, including at most 12 status/observation acquisitions; denied probes and replays count as requests |
| Replay/negative cases | Two prepare replays, one blocked check-conflict request and one incomplete-completion-evidence reconciliation request; no extra native send |
| Retries and replacements | Zero; no child continuation, replacement, nested agent, fallback route or automatic Stop continuation |
| Reports | At most 8 KiB each, validated without truncating evidence into success; reuse existing artifact/output and retention limits |

Before release, bind these ceilings into the actual D4 qualification allocation,
D5 plan and D6 plan, and verify the exact operation schedule fits all three. D6's
native counters differ from the qualification allocation's check/coordinator
history; do not copy one counter into the other. If the packet does not fit, revise
and review it before release, never inflate a live budget. Unused slots authorize
no extra job or attempt. Each planned model turn has one purpose, not a retry pool.

Timeouts limit new effects/admission; they do not kill or prove stopped writers.
Native token/currency limits are not claimed where the frontend cannot enforce
them. Record observed usage only. Do not change models or authentication to obtain
more capacity. At expiry stop new effects; preserve unresolved claims, consumed
budgets and evidence. Further recovery needs its own existing or separately scoped
authority, not a new run ID, state directory or reset deadline.

## Evidence and resumption of this stronger track

Retain original source pins, native identities, candidate/claim bindings, receipts,
consumed limits and partial effects in the existing private artifacts. Preserve
historical proof meanings and pending claims under their original authority.
Do not delete uncertainty to fit a limit, fabricate stopped flags, or treat an
empty queue, elapsed time or cancellation acknowledgement as stop evidence.
Use supported file-only undo only for owned matching outputs and authorized
recovery effects; preserve later edits and runtime history.

A future selected guard qualification must review its exact source/client,
existing setup effects, original ownership, actual callback sequence and finite
schedule. Account for guarded worker/reviewer admission, registered check and
coordinator proof requirements before that separate release. Retain independently
verifiable child denial, admitted Pre/Post pairing and unchanged unrelated files.
Optional display has its own acceptance. Unsupported transitions stay pending.

Report offline checks, native guard observations, cooperative handoff and strict
readiness separately. No fixture or this document establishes live execution,
full onboarding readiness, repository enforcement or another client surface.
No live step was run in this review. The stronger track remains on hold.
