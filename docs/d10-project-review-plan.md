# D10: Review my Project

> **Update, 11 November 2026:** the stored instruction map, `--prepare` and `build_manifest` were removed. The reader derives hashes and line ranges from the selected current sources, and package validation checks every instruction source directly. Map-generation and regeneration steps below record earlier work, not current requirements.

Instructions on `main` · 8 November 2026 · The review skill, its instruction-delivery tests and fixtures are on `main`, ported from the 5 November candidate below. The later report and retention runtime candidates described below (R1 and the original-report locator) were not merged; they are kept on the local-only `archive/d10-project-review` branch (commit `0465c93`). Managed detailed-report retention and complete client qualification remain unavailable.

**Goal:** let the user review selected repositories or an umbrella, enrolled or not, without required or silent enrollment. The skill is **Review my Project**, ID `review-my-project`. D10 is a separate deliverable in the [canonical implementation plan](onboard-my-project-plan.md#5-small-deliverables); it does not block D6/D7 or become a mandatory onboarding step.

For an enrolled Project, reuse [canonical membership](onboard-my-project-plan.md#project-and-repository-model) and qualified D3/D4/startup resolution. An unenrolled umbrella is temporary explicit review scope, without enrollment, standing join authority or auto-joining; standalone unenrolled review remains available. Follow [shared retention](onboard-my-project-plan.md#evidence-retention) using canonical target-to-owner references in existing private evidence metadata: repeated/subset/aliased reviews keep original ages/allowances, enrolled members keep their Project accounting, and overlaps reuse reconciled owners. Unresolved identity limits detailed retention, never creates another allowance or registry. D10's review owner integrates this with existing D5 artifact metadata. Preserve per-repository evidence/actual cwd; workflow diagnostics belong to last-deliverable [D11](d11-workflow-diagnostics-plan.md).

## Select the review

Select repositories first, then one or more areas or **Full Scan**. Use native multi-select when supported, with a simple numbered/text fallback. Preserve both selections.

| Area | Coverage |
| --- | --- |
| Code quality | Lint, file size, duplication, responsibility and maintainability |
| Correctness and tests | Bugs, edge cases, important missing tests and existing test/build results |
| Security and dependencies | Relevant security risks and dependency findings supported by available inspection/tools |
| Architecture and performance | Boundaries, complexity, evidence-backed risks and measured findings where available |

Full Scan covers all applicable areas only in the selected repositories, at a stated finite depth, time or file/sample bound with exclusions and gaps. Workflow health and context/token efficiency diagnostics remain separate, including under Full Scan; project review does not start them.

For the separately approved detailed source-structure and agent-context assessment,
the entry loads [focused guidance](../skills/review-my-project/references/source-structure.md)
only for matching explicit repository, candidate and analysis approval. Full Scan
may make ordinary structural observations but does not start this deeper pass.
Shortlist freshness, per-repository counted/inspected/excluded/unknown coverage,
folder grouping and sample-only priorities remain explicit. Recommendations cover
responsibilities, dependencies, interfaces, tests, boundaries, sequence, risks and
verification. Structural impact is an estimate; measured usage diagnostics belong
to D11. Analysis/report acceptance never grants refactoring authority. D3's bounded
onboarding screen remains outside this outcome.

## Inspect safely and reuse evidence

Read project rules and inspect command definitions/effects before running existing safe local lint, tests and builds by default. Do not trigger GitHub Actions, recurring/startup/periodic scans or automatic adjustments. Unsafe, unavailable or out-of-scope commands leave explicit gaps while independent inspection continues.

Reuse project tooling, relevant current evidence and installed review capabilities, such as a scoped Ponytail audit; create no duplicate audit engine. Project rules are authoritative. Where standards are absent, present recommendations rather than invented requirements. Reuse D5/D6 evidence when available and applicable, without requiring enrolled state for standalone review.

Run each canonical command once per unchanged input and share its result across categories. Rerun only affected checks after relevant changes. Do not add a second file-size checker when lint already covers it. Report actual inspected, executed, reused and unverified coverage, with evidence freshness and limits. Missing observations never count as passing; do not claim exhaustive assurance or invent measurements.

Preserve project content, except specifically approved installation effects. Use existing privacy/isolation arrangements for reports and disposable artifacts; inspect check effects accordingly. No unapproved auto-fix mode is allowed.

## Keep installations and fixes separate

If a missing tool, package or library would help, explain its package, purpose, installation scope and affected files, including manifests and lockfiles. Prefer existing or isolated tooling. Obtain explicit approval **before installation**; approval to scan does not authorize installation. Declined or unavailable tooling leaves a coverage gap, not a pass, and does not stop independent inspection.

Each finding gives evidence/location, impact, confidence, the smallest useful fix and effort. Present selected fixes for separate approval. Scan or installation approval never authorizes fixes. Only the separately approved fix scope proceeds through the applicable normal project workflow, without silent enrollment or unrelated edits.

## Delivery ownership and boundaries

**Canonical owner:** project-review authoring/implementation owner, with independent review. Preserve the user's coordinator settings and existing task/scope-based worker-selection rules. Follow the [instruction authoring standard](development.md#instruction-authoring): eventual entry point at most 150 words, containing purpose, essential boundaries and selective loading triggers; detailed procedures load only for the selected review. Existing project workflow and evidence contracts remain their rule owners.

| Subdeliverable | Dependencies and boundary | Acceptance evidence |
| --- | --- | --- |
| D10a — interaction and procedure | Canonical enrolled membership or temporary unenrolled selection, project rules and available capabilities; no new audit engine | Short entry point; repositories-before-areas selection/fallback; explicit installation and fix boundaries |
| D10b — review integration | D10a; reuse existing command/evidence facilities and privacy/isolation | Deduplicated safe checks, accurate coverage and actionable findings; no changes to D6/D7 ownership |
| D10c — behavioral qualification | Reviewed D10a/b; disposable repositories and supported client surfaces | Observed cases below, reported separately from document/package checks |

Acceptance examples: an unenrolled repository and temporary umbrella work without enrollment/auto-joining; repeated/subset/aliased/overlapping temporary reviews preserve ages, reconciled owners and enrolled Project allowances, with unresolved identity limiting detail; repository selection precedes areas; code-quality-only stays selected; Full Scan states depth/coverage only for selected repositories; categories reuse one unchanged local check at its real cwd; named installation requires approval and denial preserves a gap; installation does not authorize a fix; only selected approved fixes proceed; project review excludes workflow/token diagnostics. [D10 validation](d10-validation.md) separates package checks, native instruction probes and remaining qualification. No membership authority is added.

## Current implementation and integration handoff

Base: `be0d1cb48590a7035a033cf920964df8e3099dbf` (`origin/main`, fetched
5 November 2026). Startup-provided package: `0.1.26`. The isolated outcome uses
`skills/review-my-project/**`, focused instruction-delivery tests and this plan's
validation record. The saved Project's branch and local settings remain untouched.
The skill reuses the earlier saved D10 draft, with finite Full Scan bounds,
canonical combined-check deduplication and a separate retention loading trigger.
`clients/instruction-map.json` is generated metadata required to deliver the new
sources; integration must regenerate it after combining other instruction changes.

Implementation sequence and proof:

- [x] Establish the missing-entry failure through prepared instruction delivery.
- [x] Port the short entry and local review/focused procedures; reuse policies
  01/04/05/06 only at their stated triggers, without enrolling review targets.
- [x] Bind the new sources into the generated instruction map and prove complete,
  selective delivery without startup loading review or managed runtime.
- [x] Observe disposable native review/selection cases and verify check logs,
  source preservation and actual loaded paths; record limits in D10 validation.
- [x] Complete one independent behavior/security review of the exact candidate,
  fix supported findings and run package validation, affected checks and whitespace checks.

Current source inspection found compact owner resolution in
`runtime/review_storage.py`, including enrolled accounting and protected ambiguous
history. Temporary resolution may write owner metadata; it is not a read-only
scan operation. Current `scripts/workflow.py` has no review-storage route, and
there is no complete detailed-report producer/expiry/export path. Therefore the
usable first slice returns bounded chat findings without a storage prerequisite.
Private persistence stays unavailable, rather than creating another registry,
allowance or mandatory database. Later persistence work belongs to the existing
evidence/state owners and requires coordinated interface ownership before edits.

Source-to-contract coverage for these contracts is maintained outside routine loading paths.

The coordinator owns the final canonical source-map and onboarding-plan status
reconciliation. This outcome does not edit those shared files, core state/evidence,
onboarding/CLI, client entries or package validator.

## Related onboarding direction

Applicable lint installation belongs to the [canonical onboarding owner](onboard-my-project-plan.md#project-and-repository-model), while D10 keeps separate installation approval. Adapt the Toolkit's 350-line reference to accepted project counting rules and protections. This records direction only; onboarding implementation/qualification remain with D3/D4 and accepted check definitions with D5.

## D10c focused observations — bounded follow-up

Coordinator-authorized outcome starting 5 November 2026 at 13:16:10 UTC;
stop by 13:36:10 UTC. At most two distinct native synthetic probes, five minutes
each, and one independent delta behavior/security review within eight minutes,
including fixes. No retries, replacement workers or allowance resets. Earlier
accepted D10a/b work and observations are not rerun or reopened.

Writable scope: new `tests/fixtures/project_review/**`, this plan and
`docs/d10-validation.md`. Review references may change only for a substantiated
behavioral failure with focused proof. Shared instructions/maps, canonical plans,
runtime/evidence/storage/CLI and validator remain coordinator-owned and read-only.
No installs, live targets/accounts/services, activation, persistence implementation,
commits, publication or release are selected.

- [x] Add raw credential-free fixture targets, shortlists and scenario inputs.
  Observe an approved fresh shortlist with partial per-repository inspection,
  sample-only priorities and useful boundary/sequence/risk/verification advice.
- [x] Observe a distinct Full Scan and stale-shortlist transition. Verify that
  Full Scan alone does not activate focused analysis; later analysis-only approval
  refreshes selected existing paths without broadening missing candidates.
- [x] Continue the probes with report acceptance under analysis-only authority. Verify no refactoring,
  installation, accounting/enrollment write or detailed report persistence follows.
- [x] Independently compare fixture hashes/logs, review the exact new delta once,
  run new/affected checks plus package/whitespace checks and record observed limits.

One coordinator supervises these native probes in this chat; children report here
through native completion/attention, not peer-chat messages. New descendants load
0.1.29 bootstrap and complete policies 03/07 before acting. Preserve inherited
native execution settings and existing presentation; no naming migration occurs.
Detailed-report publication/lifecycle remains an unresolved shared prerequisite,
not a blocker to these source-based observations.

Outcome: both probes completed within their original five-minute deadlines. One
independent behavior/security review accepted the 24-file fixture/document delta
without findings or fixes. The three new fixture checks, package validation and
whitespace checks passed; unchanged skill/procedure evidence is retained. This
advances bounded source-based D10c observations only. Large-list grouping,
installed discovery, native multi-select, real installation/fixes and managed
report persistence remain unqualified. The coordinator owns acceptance and
integration of this follow-up delta into the newer combined candidate; preserve
its existing shared source/status updates rather than copying older documents
wholesale. No shared interface change is required for this outcome.

## Enrolled detailed-report lifecycle — bounded follow-up

Coordinator-authorized scope starting 5 November 2026 at 14:12:39 UTC: up to
25 minutes for implementation and focused checks, ending 14:37:39 UTC, then one
independent behavior/security review within ten minutes including fixes, ending
no later than 14:47:39 UTC. No automatic retry, replacement or allowance reset.
Earlier D10c probe/review budgets remain spent; no new native qualification trial
is selected.

Deliver one explicitly requested private detailed-report path for an existing
enrolled canonical Project with existing owned task/review evidence. Trace the
current producer, safe-closure, dependency, age, Project-cap and export owners
before test-first edits. Reuse those owners without schema/authority changes,
or stop before dependent implementation with the exact minimal missing interface
and decision. Ordinary review remains chat-first; temporary/unenrolled detailed
persistence remains a later requirement.

Reserved whole-file scope: `runtime/review_storage.py`, narrowly necessary
`runtime/evidence.py` and `scripts/workflow.py` seams, focused report/retention
tests, D10 retention/procedure guidance, and D10 plan/validation. Local generated
instruction-map refresh is allowed for checks. State/schema/onboarding, canonical
plans/source map and shared configuration remain coordinator-owned. Preserve the
newer combined CLI's role/Claude routing; integrate only an incremental route.

Acceptance includes actual owner-bound allowed/denied access, path/link/revision
races, canonical/umbrella accounting, original ages without renewal, protected
dependencies, seven-day detail/four-calendar-month compact retention, shared
100 MiB closed-detail cap with oldest eligible eviction, and safe export/copy
behavior relevant to this enrolled slice. Use disposable authorities only; never
change real storage, clean user evidence, install, fix target code, commit,
publish, release or repin active runtime controls.

### Concrete dependency and minimal interface decision

Source tracing established a blocker before report implementation. The current
backend can authenticate an original collected review and release some ordinary
closed detail after seven days. It cannot yet supply the complete lifecycle
required by this outcome, even for an enrolled Project. A report endpoint alone
would therefore misrepresent retention support. No runtime or CLI edit follows
from this design until the coordinator resolves the core interface below.

| Existing owner/seam | Implemented behavior | Missing requirement |
| --- | --- | --- |
| `collect_review()` and `_artifact()` in `runtime/evidence.py` | The real native collection owner publishes `{receipt, report}`, binds it to the existing task/Project and records `attempt.collected` | No need to create another report artifact merely to expose this original JSON |
| `_managed_artifact()`, `_source_subject()` and `_source_current_permission()` | Validate original owner, age, current enrollment, physical bindings and bytes; resolve live or retained source subject | These checks do not themselves grant a lifecycle release or certify complete retention |
| `_closure_facts()`, `close_task()` and `_retention_routes()` | Issued closure requires stopped/reconciled ownership, delivery/recovery coverage and exact typed source routes | Caller `DONE`, `approved` or `safe_closed` flags cannot replace actual closure evidence |
| `_cleanup_closed_details()` | Ordinary authenticated groups become eligible at `closed_at + 604800`; compact numerical/provenance proof is preserved before detail deletion | Groups younger than seven days are skipped even above the cap; Project-wide cap is explicitly incomplete |
| `_closed_detail_accounting()` / `_CapInputControl` | Report bounded physical/accounting observations and protected potential candidates | `complete=False` and `cap_satisfied=None`; there is no oldest-eligible Project-wide eviction owner |
| State retired-identity reader and `retire_closed()` | Existing markers represent `evidence_expired`; expired evidence must have outcome `unknown`; current retirement writes `evidence_expired=False` | No guarded four-calendar-month compact-expiry transition is exposed by the state owner |
| Existing managed-copy metadata and retention consumers | Recognize the existing copy shape and protect unknown/exported consumers | No complete copy/export expiry/accounting path; declaring a copy cannot grant deletion or a new age/allowance |

**Smallest proposed caller path, after that dependency is implemented:** extend
the existing `evidence review` command with `report`, rather than add another CLI
backend. Proposed entry is
`review_storage.publish_enrolled_report(scope, request)`, called by the existing
evidence dispatcher. The exact bounded request contains common
`client`, `session_id`, `task_id`, `scope_ref`, `expected_revision` plus
`attempt_id` and `save_decision_ref`. The last field records the explicit report
request; it is not ownership, provenance, lifecycle proof or authenticated human
consent. Reject caller paths, arbitrary report bodies, owner/age/closure fields,
copy destinations and installation/fix options.

The function derives the current canonical scope and existing task, checks the
revision and actual review attempt, then selects `attempt.collected` itself.
It authenticates the stored native receipt/report and exact Project/task owner,
current permission and physical bytes before returning the original managed JSON
artifact's reference/path, original creation time, actual closure/dependency
status and dated coverage. It creates no file, new timestamp, artifact reference,
task field, registry or database. Repeated/aliased requests identify the same
artifact. Initial request admission may require the current open owning task;
historical closed-task lookup is not silently invented. This request-time limit
must be explicit in the supported contract, while the originally exposed file
continues under its actual owner after safe closure. An expired/unavailable
source returns unavailable, never a regenerated report or fresh validation.
The collected report remains a dated review, not a new passing assessment.

Revalidate task revision, canonical membership, source metadata and file/parent
identity at the final response boundary. The existing file may subsequently
expire; a returned path is no lease or permanent export. No-copy access uses the
existing publication from `collect_review()`; invoking `_artifact()` again would
unnecessarily create another dated detail. A new formatted report or managed
copy is outside this minimal caller and must not be disguised as the same path.

**Required core contract:** extend the existing evidence cleanup owner, rather
than add a D10 cleanup mechanism. It must acquire the complete relevant Project
authority set through current canonical membership/accounting, retain unknown
temporary/exported/foreign history as protection, and derive eligible release
groups from real closure/consumer/recovery facts. A sealed observation issued by
that held acquisition must bind Project identity, authoritative stores, original
source metadata, first safe closure, protected dependencies, physical census and
the actual policy trigger. Reobserve under existing mutation guards before
recording delete intent, unlinking and committing expiry. A caller dictionary,
boolean or merely sorted cap-input list cannot issue this observation.

The existing `_cleanup_closed_details()` entry remains authoritative. Extend its
release decision for the shared 100 MiB closed-detail Project cap so the oldest
eligible evidence can expire before seven days when needed; retain protected
excess and its blockers. Preserve original ages, compact numerical receipts and
replay references. Do not allocate a separate per-repository/report allowance or
scan unrelated history recursively on every report publication.

The coordinator-owned state interface also needs a narrow proposed operation,
`expire_retained(db, owner_reference, issued_observation)`: atomically retain
non-reusable task/assignment/event replay identity and required digests while
marking released validation unavailable (`evidence_expired=True`, outcome
`unknown`). The operation must verify the actual evidence-owner observation,
exact rows/pins and absent protected consumers; no direct SQL workaround belongs
in D10. Existing fields suggest no database-schema migration is needed, but this
is a proposed owner transition, not an implemented capability. Existing active
pins/readers are not upgraded or reinterpreted by the proposal.

Calendar expiry must use an explicitly settled original-age basis, not 120 days
or the latest report request. Proposed narrow interpretation: four UTC calendar
months from the retained source's original `created_at`, clamping a missing day
to month end and retaining time of day; active/uncertain/merge/recovery exemptions
continue until safe release. This basis and the deterministic oldest-eligible
ordering need coordinator confirmation against the approved policy before code.
Existing managed copies must preserve the original source identity/age and share
Project accounting; unknown copies block affected release. The first report
caller should reject copy/export destinations rather than bypass that owner.

**Decision needed from the coordinator:** reserve/assign the state-owner expiry
transition and evidence-owner Project release acquisition, settle the calendar
age/order details, and accept original managed JSON as the first report surface
or explicitly select the additional typed-copy lifecycle. These are concrete
core prerequisites, not permission to add a registry, new authority database,
audit/report engine, background job or schema migration. D10 can then implement
the incremental caller and fixture proof under a new bounded continuation.

Test-first acceptance for that continuation:

- Resolve the actual collected/assessed attempt; deny another task/Project,
  missing explicit request reference, wrong revision, uncollected/expired source,
  caller-supplied path/body/age/closure and ambiguous authority.
- Interleave revision, membership, source/parent/symlink/hardlink replacement
  between selection and final reobservation; deny without publishing or adopting
  matching-byte replacements. Repeated calls return the same source/age/allowance.
- Compose real disposable stopped-owner closure, assignments, delivery and
  recovery evidence. A caller boolean cannot release active/uncertain or
  dependency-bound detail/compact proof.
- Exercise detail just before/at seven days, protected excess, Project-wide cap
  across members, oldest eligible eviction below seven days and incomplete
  authority/history without destructive fallback.
- Exercise four-calendar-month day/month/leap-year boundaries, clock rollback,
  protected evidence older than the limit, expiry markers/replays, unavailable
  historical validation and preserved authoritative numerical receipts until
  eligible expiry. Copy/export denial or the separately selected owned-copy path
  must not reset age, duplicate allowance or preserve expired detail.
- Apply only the new review-action/parser/dispatcher hunks to the newer combined
  CLI; prove its role/Claude routing remains unchanged. Shared map/source/status
  integration remains coordinator-owned.

**Outcome:** one independent behavior/security review challenged and confirmed
the core dependency; no design defect or fix was requested. The reviewed
two-document snapshot is
`034c5b02b3f3f49499a202d78b3d31313e5582e716ef4ee877e035f734f4dde2`.
Runtime, CLI, state, skill and instruction-map sources remain unchanged in this
outcome. Package validation and whitespace checks passed; no implemented report
endpoint or lifecycle execution is claimed. The coordinator owns the interface,
calendar/order and state-file reservation decisions above. This is the permitted
concrete blocked-design result, not completion of enrolled or unenrolled report
retention. The separate D8 CLI integration clarification does not affect it:
neither owner writes the other's physical checkout, and no D10 CLI hunk exists
yet to integrate.

## R1 — Project closed-detail cap

New coordinator-authorized outcome starts 5 November 2026 at 14:38:27 UTC.
Implementation/focused proof is limited to 25 minutes, ending 15:03:27 UTC;
one independent behavior/security review including fixes is limited to ten
minutes, ending no later than 15:13:27 UTC. Earlier outcomes and review/probe
allocations remain closed; there is no retry, replacement or native qualification.

Coordinator choices: the first later report surface is original authenticated
managed JSON without a copy/export destination. Compact expiry is four UTC
calendar months from original source `created_at`, clamping missing days to month
end, retaining time/fraction and expiring at the boundary. Neither request,
retirement nor alias changes age. Cap eviction orders eligible detail by its
original `created_at`, then first safe `closed_at`, then stable owner/reference.
Seven-day eligibility uses first safe closure. Protected/unknown dependencies
override both age and cap. These choices are recorded decisions, not execution
proof; compact expiry/report access remain later R2/R3 outcomes.

R1 changes only `runtime/evidence.py`, focused cap/retention tests and D10
plan/validation. State, report storage/CLI/procedures and shared integration files
remain unchanged. First prove under/at/over cap and younger-than-seven-day oldest
eligible release, then preserve actual Project acquisition, issued closure and
consumer/recovery provenance, compact proof, physical census, guarded intents and
mutation-boundary reobservation. Unknown authority/copy/temporary/foreign history
must remain protected with truthful excess/status. Use disposable state only;
never weaken guards, publish a placeholder endpoint, reinterpret old pins/readers
or create another store/quota/registry.

### R1 bounded implementation result — cap remains unimplemented

A genuine disposable canonical birth with existing machine storage and one
shared Project store produced an ordinary stopped, closed and retired owner.
With the cap reduced to one byte, cleanup retained its detail one second after
safe closure. The test expected early cap release and failed at physical-file
absence, as intended. Observed closed detail was 7,891 bytes; excess was 7,890.
The current result remained `complete: false`, `cap_satisfied: null`. This is a
reproduction of missing behavior, not a passing regression test or cap proof.
The unapplied test and abandoned runtime draft are retained privately under the
R1 task evidence label; neither is part of the deliverable.

The first input failure was also concrete: `_CapInputControl._collect()` compares
the complete temporary-history mapping with an exact two-field dictionary.
Canonical birth additionally authenticates `owners: []` and a handoff receipt,
so the legitimate empty-history case is rejected. A draft accepted those extra
owner-produced fields and extracted the existing guarded age-release body, but
no Project release controller was completed or independently verified. That draft
was removed in full; all four recorded runtime/CLI baseline hashes match again.
Existing age cleanup, pins, readers and deletion protections are unchanged.

The acquisition investigation separates available authority from missing proof:

- `review_storage._enrolled_selection()` scans all joined Project repositories,
  checkouts and clients and requires exactly one shared canonical store. An
  umbrella with multiple members sharing that store is supported; multiple
  selected stores are rejected. Removed members are checked for physical identity
  but their old stores are not added to the accounting authority set.
- `_enrolled_result()` supplies a real, callback-lifetime `reobserve()` only with
  existing authenticated machine storage, its temporary context and history.
  Reobservation binds canonical membership, baseline, machine descriptor/config
  and all retained members. Its source explicitly requires full reobservation
  outside evidence writers. The public accounting result grants no such guard.
  Missing machine configuration stays incomplete; no storage may be created to
  turn that absence into release authority. Unknown history stays protected.
- The supported existing-machine/single-store callback is a usable starting
  point. Its limitations alone do not prove that every positive cap path needs a
  new state-owner or storage interface. No such dependency is claimed here.
- A release controller must reacquire canonical/history/census observations
  outside each publication, intent and unlink transaction, then bind the actual
  live acquisition and held guards to its checks inside each mutation. Existing
  `_cap_input_witnesses()` includes database/sidecar state and cannot be treated
  as an unchanged witness across its own commits. Sorted candidates and caller
  booleans cannot replace this issued authority.
- Counting cannot blindly reuse `_closed_detail_accounting()`, which reads all
  live payloads. Cap acquisition permits less than 8 MiB aggregate material;
  retained consumer capture also has a 16 MiB aggregate ceiling and scans live
  records. A Project over 100 MiB needs a bounded metadata census plus the actual
  per-group closure/consumer/recovery proof without bypassing either bound.
  No complete, race-safe composition of those operations was produced in R1.

The exact remaining implementation is evidence-owner work: compose that existing
private enrolled callback with a short-lived release observation, preserve its
Project/store/trigger/policy binding, refresh it at the three existing mutation
boundaries, prove a bounded closed-detail census and original-age ordering, and
reuse the existing durable intent/unlink/commit path. Unknown authorities and
copy/temporary/foreign history remain protected. If supporting absent-machine or
historical additional stores later needs an owner change, propose that extension
at `review_storage._enrolled_result()` rather than duplicating its authority
logic in evidence. No new API, registry, quota, schema or state transition is
introduced by this result.

R1 stops at an incomplete, unverified safe-release design. Under/at/over,
oldest-younger-than-seven-days, umbrella/alias accounting, compact numerical proof,
protection, race, rollback and truthful complete-cap acceptance remain unpassed.
The coordinator must reserve a new bounded implementation outcome before further
attempts; R2/R3 must not treat R1 as accepted cap completion. Existing source-only
and native-probe outcomes were not rerun.

**R1 review and stop:** one independent native behavior/security review inspected
the fixed two-document delta, private reproduction/draft and connected owner,
consumer and acquisition sources. It found no defect in the failure record and
confirmed that the supported callback is usable: the owner failed to finish and
verify the implementation. The 8 MiB input limit bounds acquired metadata/witness
material, not physical bytes accounted; unchanged consumer traversal reads every
live payload against the separate 16 MiB capture limit. No missing state-owner
API was established for R1. Reviewed snapshot:
`358d17b76b0d415691eee86de9958d0ff45459559bb7d3c96e58169fa1ab6b3b`.

The reviewer made no writes, tests or runtime/storage operations. Actual native
execution records and current target hashes were checked before acceptance of
this report. Package validation and whitespace checks passed; client execution
was not checked. Only this final outcome paragraph and its validation companion
were added after review. The one R1 review allocation is used; no retry,
replacement, new probe or further implementation is running. This accepts an
accurate failed-attempt handoff only. R1 remains incomplete and blocks claims of
complete shared-cap enforcement; it does not erase the prior accepted D10 slice.

## Independent original-report locator — dependency assessment

The coordinator assigned a distinct eight-minute read-only assessment on
5 November 2026 at 15:23:40 UTC, ending 15:31:40 UTC. Only this plan and D10
validation may change. R1 implementation/review allocations remain spent; no
repair, v2 mandate, protocol change, new review/probe or test run is authorized.
The coordinator accepted the independent R1 diagnostic's root causes, not its
proposed architecture. Existing 0.1.29 task controls/pins/readers remain in effect;
the newly available 0.1.30 skill catalog is not a runtime migration instruction.

**Assessment:** original-source access can be a distinct read-only outcome for
an existing current, open owning task and an already collected/assessed native
review. It need not depend on completing R1 or R2. This is source-backed
feasibility, not an implemented or qualified reader. It neither completes the
approved retention deliverable nor creates a supported publication interface.
The earlier plan's blanket sequence dependency applies to full persistence and
retention completion; it is not a call dependency of this narrower reader.

| Actual call edge | Required reader behavior | Dependency |
| --- | --- | --- |
| `startup.acquire_project_entry()` / `_entry_scope()`; `state._database()` / `_check_current_authority()` (`startup.py:1365,1503`; `state.py:1688,1703`) | Acquire current accepted Project/client/physical scope and open only existing `mode=ro` state, without initialize/setup or altered pins | Current enrollment/runtime is required; neither R1 release nor R2 expiry writer is called |
| `evidence._request()` / `_task()` / `_artifact_owner()` (`evidence.py:78,85,987`) | Strict request fields, exact active session/task/scope binding, revision, accepted identity/pins, and actual derived Project/task owner | Existing helpers admit an open non-cancelled owner; closed/retired/historical lookup is outside the initial reader |
| `collect_review()` (`evidence.py:5781–5826`) and `attempt.collected` | Select the stored reference from the actual task's review attempt in `collected` or `assessed` state; never collect again or publish another artifact | Publication already occurred; no new timestamp, copy, allowance or `_artifact()` call |
| `_report()` / `_linked_review()` / `_review_quality_matches()` (`evidence.py:5730,5751`; `delegation.py:1736`) | Validate stored native receipt/report against the exact attempt, owner/session, assignment and candidate; select only the linked assignment, not the whole ledger | Existing bounded record/history reads; no fresh native collection or candidate verification is needed merely to display a dated result |
| `_managed_artifact()` / `_source_mandate()` / `_source_current_permission()` (`evidence.py:4825,4091,4036`) | Read and hash the actual selected bytes; verify managed ownership, original age, source mandate, Project identity and current enrollment/access | This reads the bounded source directly. It does not need a new immutable-source fast path, a global consumer census or cap-release authority |
| Final response boundary | Reopen/reobserve current task revision and source metadata, canonical permission and original file/parent bindings; reject changed or unavailable input | A stale SQLite read snapshot cannot establish a final revision check. A returned path is an observation, not a lease or permanent export |

The existing CLI needs a narrow read-only return path: its common evidence branch
calls `title_report()` (`scripts/workflow.py:249`), which invokes native title
repair (`:17`; `title_sync.py:318`) and can write. Likewise do not call
`evidence_status()` (`evidence.py:8463`), which can revalidate/update linked ready
state; do not call collection, assessment, verification, cleanup, owner resolution
or storage bootstrap as incidental status work. A later patch belongs in the
existing evidence owner/dispatcher, with only its exact hunks applied to the
coordinator's newer combined CLI. It must preserve D8's newer routing.

Keep the original bounded request shape (`client`, `session_id`, `task_id`,
`scope_ref`, `expected_revision`, `attempt_id`, `save_decision_ref`) if the
coordinator retains it. The decision reference records the request; it authenticates
neither ownership nor human consent. Session strings alone are also not native
caller authentication: current `_identity()` validates shape and `_task()` binds
the existing row. The supported invocation must retain its actual native caller
identity and compare that identity, the current task binding and stored receipt's
parent session. The generic CLI currently accepts session fields from JSON; do
not claim that parsing them proves the caller's native session. If the later
integration cannot establish the required caller binding, reject access or keep
that transport unavailable. This is an admission integration issue, not R1/R2.

The result must identify the original reference/path and source `created_at`,
observation time and original dated coverage/limitations. Preserve the stored
report/receipt bytes; do not redact/reformat a new file. An elapsed age is unknown
when the observed clock precedes creation; never reset creation or invent zero
age. Report metadata availability separately from policy completion: source
`live`, actual open owner and absent safe-closure timestamp are observable;
release/dependency eligibility is not evaluated, shared-cap enforcement remains
incomplete, compact-expiry enforcement remains incomplete, and no promised
retention duration or complete Project allowance is certified. A missing,
`delete_pending`, expired, corrupt, replaced or cross-owner source is unavailable;
never regenerate it. Changed candidates leave an explicitly dated old review,
not a new passing assessment. Unknown dependencies remain unknown/protected.

The approved full contract still requires actual safe-closure/consumer/recovery
proof, seven-day cleanup, Project-wide oldest eligible cap release and four UTC
calendar-month compact expiry. R1 owns release eligibility/accounting and early
cap eviction; R2 owns the guarded compact expiry/retired-identity transition.
Neither operation is needed to observe an existing open-owner source. Conversely,
a reader cannot certify their outcomes, derive a trustworthy complete dependency
status from metadata, or guarantee expiry. If the coordinator requires those
certificates before any source is exposed, this locator cannot satisfy that
stronger admission contract: keep chat-only output until R1/R2, or explicitly
reserve original-source access as a separate limited capability. No requirement
is silently dropped by calling partial access full retention support.

Current procedure/retention text still says chat-only and no supported report
interface. Any later implementation reservation must update only that narrow
availability statement and preserve every retention limitation. This assessment
changes no procedure and calls no private storage helper. Unenrolled reports,
closed-owner discovery, formatted reports, exports, copies and persistence are
outside the proposed first reader. Actual source/session/Project and byte/access
checks, final reobservation and truthful partial status remain mandatory.

**Why the retention protocol proposal expands scope:** a v2 publication mandate
would change producer validation, reserved artifact admission, dependency
extraction, source-to-mandate binding, retained provenance, D4 occurrence readers,
all full-inventory paths and legacy-version handling. A projection alone cannot
prove unchanged bytes; a qualified immutable-source/custody observation must also
reject in-place changes and replacements without rereading unrelated payloads.
Existing v1 mandates do not authenticate exhaustive consumer semantics. This
would introduce a new artifact/proof protocol and compatibility/security test
surface, not a local cap-loop optimization. The coordinator has accepted neither
that architecture nor its feasibility. No such mechanism is needed when the
limited locator reads and hashes its selected source normally.

**Next owner:** root decides whether to reserve this independent read-only slice
and its caller-identity/transport boundary, then assigns a new bounded
implementation and focused verification. D10 makes no runtime/CLI/test edit now.
R1/R2 remain incomplete and no background repair or qualification is running.

## L1 — observational original-report lookup

Root explicitly reserved this independent implementation on 5 November 2026,
starting 16:19:20 UTC: 20 minutes through implementation/proof/handoff, followed
by one root-dispatched independent review within eight additional minutes,
including preparation, fixes and rechecks. Implementation ends 16:39:20 UTC;
absolute allowance ends 16:47:20 UTC. Earlier R1/design budgets remain consumed.
No extra worker, native/account/model trial, installation or publication occurs.
Vibe 0.1.30 policies 01–07 and bootstrap were loaded for this outcome; root owns
review dispatch and receives the original reviewer report under policy 03.
Existing presentation records, runtime pins/readers and prior artifacts remain.

Root accepted the stronger-model recovery diagnosis. The previous assessment's
extra native caller-principal prerequisite is withdrawn: the existing CLI trust
boundary is the current OS user plus private files and canonical accepted scope.
Session fields select an existing owner; this route makes no isolation claim
against another same-user process. Likewise the proposed v2 mandate/immutable
source protocol is not accepted architecture. The 8/16 MiB implementation bounds
are not user retention policy. R1's future finite semantic acquisition is a
separate outcome; R2 expiry and this reader do not require early cap eviction.

The archived implementation (not on `main`) adds `evidence.review_report()` and the existing CLI route
`python3 -B scripts/workflow.py evidence review report --cwd <enrolled-checkout>`.
Its JSON stdin has only `client`, `session_id`, `task_id`, `scope_ref`,
`expected_revision`, `attempt_id`, `save_decision_ref`. The last field records the
explicit request and grants no ownership, consent or extra capability. Codex and
current accepted schema-3 scope remain required; Claude managed review and legacy
reader upgrade are not introduced.

The reader selects `attempt.collected` from the actual current open task and
requires a collected/assessed review. It checks revision, task/Project identity,
pins, exact metadata owner, stored native report/receipt, immutable D5/D6
assignment/candidate and source mandate. It reads original managed bytes through
existing ownership/integrity checks and observes current source permission.
A second full acquisition uses fresh read-only SQLite transactions. Each phase
also reopens current task/metadata after permission observation; stale read
snapshots cannot hide a committed revision. File/parent physical binding and
source stat must remain unchanged. Missing, pending, expired, cross-owner,
corrupt, replaced or incompatible inputs fail unavailable.

The response identifies the original reference/path, original source creation,
source byte count, original candidate, dated coverage/limitations, observation
time and elapsed age (unknown on clock rollback). Retention is explicitly
incomplete: source live, owner open, no safe closure, release eligibility not
evaluated, shared-cap and compact-expiry enforcement incomplete. The dated review
is not a fresh passing assessment. The path supplies no lease, new publication,
formatted report, export or new allowance. No source or task timestamp is written.

The CLI report branch returns before common title repair. It does not call
collection, assessment, status, verification, cleanup, artifact creation, owner
resolution or setup. The normal chat-only/new-report guidance remains accurate:
this creates no detailed report and adds no review-storage publication backend.
The reserved retention guidance therefore needs no changed publication claim or
root-owned instruction-map rewrite. L1 availability and exact request are stated
here; full D10 persistence/cleanup/export retention remains incomplete.

Test-first acceptance covers stable repeated and scope-equivalent reads, original
bytes/reference/date, exact request/owner/Project/revision/native provenance,
collected and assessed availability, clock rollback, unavailable statuses,
physical substitution/corruption, real committed revision changes, current
permission and parent replacement, and CLI title-repair bypass. Tests use real
disposable D4/D5 publishers with explicitly synthetic native host observations.
They are offline runtime evidence, never native client qualification.

Before handoff, self-review and focused checks must pass, state/review-storage
bytes must match the L1 baseline, and a fixed five-file snapshot must be recorded.
The owner requests review locally; root dispatches a separate read-only reviewer
and returns any fixes to this same owner. No independent acceptance or merge is
claimed before that original report is received by root. Root alone integrates
exact CLI hunks into its newer D8 composition and owns shared maps, source
coverage, Git and publication.

**L1 checkpoint:** implementation and self-review are complete; the final focused
command passed 15 tests in 68.146 seconds. Package validation and whitespace pass;
client execution remains not checked. Only the reserved evidence owner, exact CLI
route, new focused test file and these two D10 records changed in L1. State,
review-storage, retention guidance, shared maps and prior accepted slices are
unchanged. Root-dispatched independent review is now requested over the fixed
five-file snapshot; root owns acceptance and the next action. No review worker
was spawned here, no background work is running, and no retention completion is
claimed. Checkpoint hashes and preimages are under the private L1 task evidence.

**L1 same-round P2 repair:** root returned Astra's confirmed stored-receipt schema
finding after releasing its source/test readers. Genuine republished receipts
with version 99 or an unknown mechanism were wrongly accepted. A new real
publication/SQLite regression failed for both cases before the fix. The exact
existing collection receipt-validation block is now one shared
`_validate_review_receipt()` helper, called by collection and lookup. Lookup uses
the actual stored native observation's parent turn/spawn identity; the existing
quality match and every source/ownership/permission recheck remain required.
The moved block was compared verbatim with the original collection validation.

The focused schema regression, original read-only lookup and existing canonical
collection regression passed: three tests in 16.804 seconds. Schema cases cover
version 99, Boolean version, unknown mechanism, extra field, empty native version
and wrong parent turn. Storage snapshots/write/probe barriers remain active.
Package validation and whitespace pass; client execution remains not checked.
No unrelated suite was replayed. Root's same reviewer owns the focused recheck;
no new round or budget was allocated. Updated checkpoint is separate from the
original snapshot. Final hard L1 deadline is 16:47:07 UTC; reviewer recheck ends
16:46:19 UTC. This repair is not independently accepted until root receives that
original recheck report.


## R1b recovery qualification checkpoint (2026-11-05)

Root accepted repaired L1 through its own exact-source verification and three focused passing cases. The original Astra review found P2; its repair recheck did not occur before its allocation ended. Root's disposition is retained in `/Users/fcouto/.local/share/vcw-validation/d7-d11-current-main-i8wvbjl5/astra-acceptance-k0crsmt0/l1-disposition.json`. No Astra repair-recheck success is claimed.

The separately authorized R1b approach first observed the local clock at 17:25:16 UTC. Root supplied the governing implementation deadline of 18:02:03 and total deadline of 18:12:03, including the one reserved independent phase; these supersede the initial local calculation without resetting any allowance. It attempted a private, operation-owned full-semantic resource budget, original-age ordering and the existing three-phase release within the accepted 128 MiB store / 1 GiB cumulative body-work envelope. It preserved the 8 MiB canonical-input, 4,096 row/path, 4 MiB artifact, four-second callback and selected-group/D4 capture bounds. Earlier spent R1/L1 budgets were not reset.

Qualification failed before semantic proof. A genuine 139-file umbrella store held 109,247,396 closed-detail bytes, 4,389,796 bytes above the real 100 MiB cap. At 4,092 path/row charges, per-owner accounting requested another complete 139-row metadata census. Actual canonical material was 4,967,722 bytes before the failed request. The draft had read 1,085,488 managed body bytes; selected capture and semantic graph counters remained zero. No detail was released. This identifies repeated whole-inventory authentication inside accounting as the next diagnostic target; it does not establish that the accepted envelope or existing enrolled callback is inherently inadequate.

The writer stopped repairs, retained the unqualified draft and evidence privately in `/tmp/d10-r1b/`, and restored accepted L1 source exactly. The failing cap fixture is preserved privately, not admitted to the package. R1b has no review-ready candidate and no independent review was dispatched. Root owns any stronger independent diagnosis and continuation under the original remaining budget. R1 and R2 remain incomplete. R2 is independent, but root must reconcile the current reservation before starting it. Full measurements, check history, omissions and hashes are in `/tmp/d10-r1b/blocker.md`, `measurement.json` and `unqualified-draft.json`.

Root received the blocker and stopped further repair/testing. Accepted L1 restoration and final package/whitespace validation had already completed. The owner has stopped; the reserved independent phase can diagnose the frozen call chain and counters read-only within the original deadline. No review-ready candidate or successful cap qualification is claimed.


## R2 stopped qualification checkpoint (2026-11-05)

The original R2 window began at 18:00:35 UTC, with implementation/checks/handoff due 18:35:35 and hard total due 18:45:35. Root accepted a smaller genuine standalone fixture after independent diagnosis of the unchanged peer/history detail-cleanup failure. The owner adopted reviewed G1 state and kept L1 fixed.

Nine focused cases passed for calendar boundaries, nonempty event replay preservation, durable intent/recovery, independent consumer and permission protection, and stale/replaced sources. The genuine local-check case failed before compact expiry in unchanged seven-day detail acquisition: repeated original-support/source-mandate authentication exhausted the recovery capture aggregate. Nonempty assignment expiry, numerical-proof disappearance and genuine D4 retention-only consumer coverage remain unproved. The original cap/peer-history path and resource limits were not repaired.

R2 is unqualified. Its exact draft, test, unchanged fixtures, logs and hashes remain private under `/tmp/d10-r2/`; accepted L1 evidence and reviewed G1 state were restored. Package validation passed separately from client execution, which remains unchecked. Root owns any remaining independent diagnosis before the original hard deadline. No feature acceptance or new budget is claimed. Detailed evidence and omissions are in the R2 section of `docs/d10-validation.md`.


## Shared-acquisition prerequisite candidate (2026-11-05)

A separate explicit human allowance authorized only the small real-check retention prerequisite, beginning 19:17:25 UTC; implementation/handoff ends 19:47:25 and total including independent review ends 19:57:25. Root stopped repeated amplification failures, obtained independent diagnosis, and accepted a narrow phase-subject correction. Prior failures and overruns remain recorded; cap and compact-expiry drafts stayed private.

The genuine nonempty-check release now passes within the unchanged 16 MiB cumulative capture budget. Source acquisition stays bound to actual connections, read-only intervals and held guards; each durable phase reobserves proof. Per-file permission uses the existing freshly observed physical remainder with that transaction's authenticated subject and exact retained metadata. The corrected connection/lifetime negatives, permission suspension/pending retry, L1/closure and typed D4/consumer checks are recorded in the validation checkpoint, including the repaired obsolete query-count assertion and its recheck. The candidate still requires root's reserved independent review. No native client, cap or compact-expiry acceptance is claimed.

## D10c grouping and selection checkpoint (2026-11-05)

The separately authorized outcome added only a 48-candidate disposable fixture, one focused fixture-test module and these D10 records. Two native probes observed folder grouping per repository, distinct coverage counts, sample-only recommendations, repository-before-area text selection, preserved beta/alpha scope and chat-only reporting. Full Scan did not load the separately approved focused analysis. Native multi-select rendering remains untested.

The grouping probe finished in 153.195 seconds. Selection behavior was observed, but its approximately 194-second wall span exceeded the three-minute limit; the worker's contrary timing claim was rejected. No retry or instruction change followed. The aggregate qualification remains incomplete. Three fixture tests, package validation and whitespace checks passed separately; all protected source bytes and both disposable copies remained unchanged.

Root owns the one independent review over the frozen candidate and original native evidence in `/var/folders/tz/c2kgm3r911ndfsbh8zm53lvc0000gn/T/d10c-grouped-selection-461uua5w/`. Use the conservative native owner-turn deadlines: 21:17:31 UTC for handoff and 21:25:31 total, including review/repairs. Writers stop at handoff. Acceptance must preserve the timing failure and coverage limits recorded in `docs/d10-validation.md`; this work grants no retention retry, publication or D10 completion.

## Cap-only continuation (2026-11-05)

The human separately approved one cap-only continuation after root accepted the grouping fixture and bounded observations with independent review. The selection timing failure and aggregate qualification gap remain. Root observed the cap continuation's native start at 21:19:18 UTC; implementation, focused checks and frozen handoff stop at 21:59:18. The one root-dispatched independent Astra phase, including repairs and rechecks, shares the hard 22:09:18 deadline. No earlier allowance or compact-expiry work is renewed. Accepted source hashes and the complete brief are preserved in `/tmp/d10-cap-20261105-czsa4g_4/`.

The initial five-minute interface checkpoint reproduced that the accepted read view excludes cap-input budgets. A narrow draft bound the view to the original canonical budget and allowed guarded cap acquisition to reuse the actual reader. The focused genuine-owner control failed before that change and passed afterward. This proved only that interface change. The above-cap regression used the prior 26-protected-owner fixture design plus authentic check, assignment and replay evidence and a live owner. Its baseline contained 109,460,294 physical bytes and did not release the eligible young detail.

Thread Naming was reconciled during this phase under the human's explicit request. The observed title matched the last verified W23/C baseline, with the original topic and resolved override retained. For the whole D10 scope, four coarse milestones have equal weight: D10a interaction/procedure and D10b review integration are accepted; aggregate D10c qualification and the detailed-report lifecycle remain incomplete. The recomputed two-cell title `￻▰▰▱▱▱ [W23] C · D10 Project review` was written and read back through native controls. The phase is WORKING, with no human action label. This changes display metadata only; runtime pins, ownership, models, permissions and funding stay unchanged.

**Stopped at resource feasibility:** an isolated shared-reader census authenticated 28 closed owners in 0.953175458 seconds, using 2,714 inspections and 10,252,268 capture bytes. That observation did not include full cap admission. The combined actual callback failed its second metadata/stat inventory at 4,095 used inspections when three more were required, before entering the shared capture. Actual material before failure was 3,690,936 bytes; the final 8 MiB value is a poison marker, not measured volume. The measured operation took 0.844310084 seconds, separately from fixture setup. A failed measurement logger and its numeric-only repair/recheck remain recorded.

The callback still opens separate readers and completes repeated input inventories before the new shared view can run. Extending lower-level eligibility alone did not repair that caller. This is an observed acquisition/interface blocker, not proof that the fixed resource envelope is intrinsically insufficient. Further repair stopped after the recurrence. The unqualified source/test/diff and measurements remain private; accepted acquisition source was restored exactly and the new unqualified test removed from the checkout. No cap release, interruption/resume, post-release numerical/assignment/replay preservation or native qualification is claimed. Root owns any stronger diagnosis within the original allowance; no independent reviewer was dispatched here. The phase is now BLOCKED on coordinator diagnosis, with unchanged accepted progress and no human-action title label.
