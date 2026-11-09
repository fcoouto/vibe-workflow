# Independent resource admission and capacity recovery

**Status:** agreed direction, documented for review before implementation on
1 November 2026. This document changes no runtime behavior. It supersedes the
earlier proposal to make the retained features opt-in: both start enabled for new
supported workflow scopes, while existing preferences remain authoritative.

## Purpose and chosen approach

Use ordinary native delegation with the workflow's shared safety checks. Keep
resource admission and capacity recovery as independent capabilities, available
through the existing Workstreams skill. Preserve useful thread reuse, current
naming rules, model selection and independent review.

Considered approaches:

- Rename controls only: smallest edit, but leaves activation and lifecycle coupled.
- **Separate controls and lifecycle while reusing shared authority: chosen.**
  Reuse the existing implementations and move common safety checks to delegation.
- Standalone services without workflow authority: requires new identity, ownership
  and cleanup machinery; outside this release.

This is a separation of responsibilities, not a replacement scheduler. The native
client controls available worker execution; the existing workflow remains the
authority for participating tasks, ownership, budgets and accepted results.

## Controls and defaults

Expose independent enable, disable and status operations for **resource admission**
and **capacity recovery** through the current skill. Keep one settings authority
with separate feature revisions; changing one must not invalidate the other's
permissions. Exact command spelling and storage layout are implementation choices.

| Situation | Required behavior |
| --- | --- |
| New supported scope with no saved preference | Both features enabled by default. |
| Saved feature preference | Preserve it, including explicit disable. |
| Existing local combined Workstreams preference | If separate preferences do not exist, use its enabled/disabled value for both; never overwrite a separate preference. |
| Status or ordinary startup | Report preferences without creating workers, reservations or a schedule. |
| Resource admission disabled | Ordinary delegation continues; no new helper-managed launches. Existing reservations, status and cleanup remain available. |
| Recovery disabled | Withdraw affected registrations and reconcile schedule deletion when no eligible parents remain. Resource admission and ordinary delegation continue. |
| Either feature re-enabled | Recheck current authority; never revive old launch/send permission or reset consumed budgets/deadlines. Manual schedule suspension still requires explicit human reactivation. |

Enabled means available when applicable. Heavy commands use resource admission;
activated eligible workflow parents register for recovery. No global scan of
unrelated chats. Unsupported capabilities remain visibly unavailable, without
changing preferences or claiming protection.

## Shared safety remains in base delegation

Keep one implementation of task identity, checkout binding, original runtime pins,
finite budgets, ownership, independent review and accepted-output freshness.
Move common dependency and same-child continuation checks out of the optional
Workstreams orchestration path. Preserve reacquisition after settlement, current
path aliases, transitive prerequisite freshness, dependent-owner conflicts,
remaining continuation allowance and proof of a fresh native result.

Both features consume this existing authority. They do not create another task
ledger. A lost launch/send acknowledgment requires inspection of the same identity;
it never authorizes blind replay. Uncertain owners retain claims. Feature disable
never releases ownership, cancels a running command or bypasses review.

Ordinary delegation must work with all four combinations of feature settings.
Safe continuation and acceptance checks apply even when both features are off.
Native continuation alone does not replace the workflow's ownership reconciliation.

## Resource admission

Reuse `runtime/resource_admission.py` and `runtime/resource_execution.py`. Replace
the combined mode gate with the resource preference and shared delegation checks.
Preserve the existing run/status/cancel/release operation semantics:

- Shared atomic reservations cover participating parents on the same host.
- Heavy worker, reviewer and coordinator commands use the same admission checks.
- Telemetry failure admits through remaining gates and reports degraded observation;
  it does not erase reservations or bypass authority, dependencies or budgets.
- Preserve main's fresh disk-pressure checks independently of cached memory data.
- Completion events and bounded deterministic rechecks resume eligible waiting work;
  do not add model polling or a resource schedule.
- A phase identity authorizes at most one launch. Command exit or timeout alone
  does not release capacity: independently reconcile the owner and descendants.
- Disable stops unlaunched waits from blocking eligible work; occupied or uncertain
  reservations remain until existing release requirements pass.

This remains cooperative admission, not OS containment. Preserve conservative
accounting and owner-level release in this change; improving them is separate work.

## Capacity recovery

Reuse `runtime/parent_recovery.py`, the recovery CLI and the supported native
scheduled monitor. Replace the combined mode gate with the recovery preference.
Registration retains current workflow task/parent identity, pins and the original
finite task deadline. It does not require resource admission or a resource lease.
Supporting arbitrary chats without workflow task authority is outside scope.

Preserve the existing recovery contract:

- Create one shared schedule when the first eligible workflow parent registers;
  delete it completely after the last departure. Retain local audit evidence.
- Observe only exact registered parents. Send same-thread `Continue` only for a
  recognized live idle capacity failure, with a final live-state recheck.
- First opportunity after 60 seconds; sends at least 60 seconds apart; stop at the
  earlier task deadline or episode start plus 3,600 seconds. No backoff or catch-up
  sends. Native retry-exhaustion proof remains waived as previously authorized.
- Preserve consumed-turn protection, one-use delivery intent and uncertain-result
  reconciliation. Read/send races remain possible; do not claim exactly-once delivery.
- Manual schedule pause/deletion suspends automatic recreation. Human continuation
  after shutdown/account limits and explicit reactivation remain unchanged.
- Parents handle children through existing safe continuation rules. The shared
  monitor does not independently restart children or acquire their ownership.

Schedule creation/deletion uncertainty blocks conflicting recovery operations.
Report recovery as unavailable or pending until acknowledged; do not report the
parent as protected. This uncertainty alone must not block ordinary delegation or
resource admission. A corrupt shared task authority still blocks affected work.

Keeping recovery keeps recurring model usage and susceptibility to monitor capacity
failures. Use compact replies, bounded live observations and one shared schedule.
Do not promise savings or introduce a daemon, new transport or another monitor.

## Existing records: preservation, not a customer migration system

The plugin has no released users updating installations. The relevant old records
are local development and qualification artifacts: pinned tasks, uncertain claims,
resource reservations, monitor registrations and saved preferences.

Do not build a general migration framework, dual-write system or indefinite legacy
dispatch layer. Preserve historical evidence and original readers separately.
Use the small preference mapping above only for local saved choices. New task
records use the new contract; never repin old tasks or reset their budgets.

Before switching a local scope, inspect only its existing authorities for active
owners, reservations, registrations and unresolved sends/schedule operations.
Finish or reconcile them through their original supported path first. If that is
not possible, leave that scope on its existing path; independent disposable scopes
can validate the new design. Do not discard live state merely because it is test
data. No broad migration promise or unrelated historical cleanup is required.

## Implementation boundaries and loading

| Existing location | Intended change |
| --- | --- |
| `runtime/workstreams.py`, `runtime/delegation.py` | Independent preferences; common dependency/continuation safety owned by base delegation. Retire only proven duplicate orchestration. |
| `runtime/resource_execution.py` | Resource-specific eligibility and shared authority calls; retain host admission engine. |
| `runtime/parent_recovery.py`, recovery CLI | Recovery-specific eligibility and lifecycle; preserve registry and send protections. |
| `scripts/workflow.py`, task initialization | Independent controls, defaults and status using existing command infrastructure. |
| Workstreams skill, management reference, delegation/resource/recovery contracts | One entry point; operation-specific loading and current naming retained. |
| Source coverage and focused tests | Trace requirements and verify independence and unchanged safety. |

Start from current `main`, preserving its fixes; the inspected efficiency branch
contains broader ancestry and older resource behavior. Do not merge that ancestry
to obtain this separation. Exact file extraction and names are builder choices;
no public command examples become executable promises before implementation.

Keep the skill entry within 150 words. Management loads controls only. Ordinary
delegation loads shared safety and only applicable feature activation guidance.
Heavy execution loads resource instructions; recovery registration, monitor runs
and departure load their own sections. Design/evidence stays outside routine paths.

## Acceptance and stop condition

Use focused tests for the four feature combinations, default initialization,
preserved preferences, independent revision changes, stale permissions, dependency
reopening, continuation ownership, telemetry failure, duplicate launches, safe
release, uncertain delivery and schedule suspension/deletion. Check that recovered
parents use current feature settings and existing task authority.

Walk representative instruction paths: status, ordinary delegation with both
features off, recovery-only parent activation, resource-only heavy work, and both
enabled. Required procedures load; unrelated procedures stay unloaded.

After deterministic checks and package validation, qualify one disposable two-parent
resource lifecycle and the recovery schedule/send lifecycle within existing native
authorization. Controlled capacity observations prove wiring only; report genuine
provider-overload recovery separately. Include coordinator, worker, review and
monitor usage where observed; leave missing costs unknown.

Stop when independent controls and lifecycle work, shared safety is preserved, and
applicable native observations pass. No new model selector, concurrency optimizer,
50-worker benchmark, process supervisor, default change elsewhere or release action.
Implementation begins only after review of this design.
