# Usage Routing Delegation Registration Implementation Plan

This is the approved design and original implementation checklist. The
[validation record](../../usage-routing-validation.md#delegation-registration-integration--4-november-2026)
records the delivered checks and the live qualification boundary.

**Goal:** Connect Usage Routing to normal delegation selection and evidence, and reuse one verified external-display registration seam without changing native execution or review authority.

**Architecture:** The existing routing ledger owns external attempts, admission and accounting; the existing assignment context and native evidence own responsibility, integration and review. The trusted client host verifies a real completed generation before invoking a small shared helper in `runtime/worker_visibility.py`, which reuses its publisher, receipt locking and receiver readback. Execution route and Habblaud client transport remain distinct so a future Claude Code host can reuse routing and registration without inheriting Codex observations or pretending the current transport supports Claude.

**Tech Stack:** Existing Python standard-library runtime and unittest fixtures; existing JavaScript Codex bridge and Node test harness; Markdown contracts. No dependency, registry, daemon, provider framework or new CLI.

**Spec:** [Usage Routing design](../specs/2026-10-31-usage-routing-design.md), [worker visibility](../../../clients/worker-visibility.md), [native delegation](../../../clients/delegation.md), and [Usage Routing proposal procedure](../../../skills/manage-usage-routing/references/delegation.md).

## Global Constraints

- “Off is the default.” Preserve Manual and Smart Balance; this slice neither activates nor qualifies either mode.
- “Independent native review and existing provider routes remain unchanged.” Preserve approved local preferences, coordinator model, reasoning, authentication and native capability selection.
- “Track each original task through integration and review.” Visibility completion never establishes native acceptance or releases API accounting.
- “Each attempt sends at most once.” Preserve uncertainty, conservative reservations and existing recovery; no display retry may resend inference.
- “A plan, count request or simulation does not create an external row.” Register only after verified completed generation, with exact run/task/scope, admission pin and recorded cost.
- “The existing transport sends only bounded display metadata, never source, prompts, raw outputs, credentials or reasoning.” Do not forward financial evidence to Habblaud.
- Preserve `uuid5(NAMESPACE_URL, f'vcw:openai-api:{parent}:{run_id}')` and the exact private receipt filename `habblaud-{external_id}.json`; retain historical receipt bindings and labels without migration or rewriting.
- “Keep each skill entry point within 150 words.” Use skill-creator for instruction changes and preserve source-to-contract coverage; do not put research in routine loading paths.
- Validate with `python3 -B scripts/validate.py`; report package checks separately from observed client execution. No release archive, live API call, account change or installation is authorized by this plan.

## Review Focus

- A Smart proposal requests `auto` but executes `openai-api`: evidence and identity must record the actual route, never `auto` (Tasks 1–2).
- A copied result or wrong task/scope/admission pin/cost must not register an external worker (Task 2).
- A delayed/failed display after settled generation must leave the proposal available for native review and leave all accounting unchanged (Task 2).
- A historical receipt or conflicting first receiver label must retain its original identity and fail visibly rather than duplicate or relabel the worker (Task 2).
- A second host with different client identity must use the shared registration logic only after its own evidence verification; fake transport success must not qualify real Claude support (Task 2).

---

## Source baseline and file ownership

Planning inspected uncommitted visibility changes in a worktree at `14f79df019db318f0b0286ef19cd638e624fdf09` and uncommitted Usage Routing changes in the primary checkout at `0e70c6b1e98b2e9e72d53d52fb10170e7ccf93c8`. The validation record identifies the exact snapshot used for integration.

Before implementation, the coordinator must reconcile the completed, reviewed slices against current `main` into a suitable authorized checkout and record the actual candidate snapshot. Preserve unfinished primary-checkout changes separately. Re-read these interfaces because concurrent owners can change them. Do not execute the plan by editing the primary WIP in place or by bulk-copying either tree.

| File | Responsibility in this slice |
| --- | --- |
| `runtime/usage_routing.py` | Existing common selector/runner; add explicit actual route to successful generation result only. |
| `runtime/usage_codex_host.py` | Keep trusted Codex admission and generation checks; delegate only display construction/publication. |
| `runtime/worker_visibility.py` | Add the small shared external registration helper; retain existing publisher/receipt implementation. |
| `scripts/usage_codex_host.js` | Preserve settlement-before-display bridge; change only if the added result field needs explicit forwarding. |
| `tests/test_usage_routing.py`, `tests/test_usage_codex_host.py`, `tests/test_usage_codex_bridge.py`, `tests/test_worker_visibility.py` | Route, host boundary, stream ordering, compatibility and second-host fixtures. |
| `policies/03-agent-delegation.md`, `clients/delegation.md` | Narrow loading/selection bridge to existing Usage Routing procedure, without rewriting native controls. |
| `skills/manage-usage-routing/references/delegation.md`, `references/codex-host.md` | External assignment/evidence ownership and precise host registration behavior. |
| `clients/worker-visibility.md` | Shared display contract and actual-route/client distinction. |
| `docs/policy-sources.md`, `docs/usage-routing-validation.md` | Coverage and honest offline/live qualification record. |
| `clients/instruction-map.json` | Regenerate with existing authoring tool if changed instruction sources require it. |

`runtime/usage_budget.py`, native D5/D6 schemas, existing handoff controls, installed Habblaud hooks and transport are consumers/invariants to inspect and regression-test, not new implementation targets. Do not add a Claude host, Anthropic API route or generic adapter registry.

## Ownership and interfaces

“Register delegated work” means associate the existing bounded assignment with its real routing run and native integration/review evidence. It does not mean creating a second runtime registry or treating Habblaud presence as execution evidence.

| Fact | Owner and existing location |
| --- | --- |
| Stable task, scope, bounded brief, source snapshots, role/specialty/custom name | Coordinator's existing assignment context; retain original `visibility` declaration. |
| Requested selection | Existing proposal `provider` (`openai-api` or Smart `auto`), plus existing local/API-only choices. |
| Actual execution route | Successful routing result `execution_route: 'openai-api'`, grounded in the direct runner; host cross-checks it. Never infer it from `auto`, model name or Habblaud account. |
| External attempts and costs | Existing ledger `run_id`, `run_id + ':count'`, `run_id + ':generation'`, consent/scope, live admission pin and generation state/cost. No duplicate accounting. |
| Configured/observed model | Approved profile and validated provider response under the existing runner. Display name is a presentation lookup, not model attestation. |
| Native child | Only actual native spawn identity and observed parent/attempt. A foreground API call has no native child or native activity card. |
| External display | Stable UUIDv5 derived from actual route, observed parent and run ID; original declaration and binding frozen by existing receipt. |
| Native handoff/reviewer | Existing native selection, one-use dispatch/handoff claim, independent D5 review and task-completion evidence. Never infer acceptance from external completion. |

Usage Routing proposals are tool-free external work. Associate native supervisors, handoff workers and reviewers with the same task through existing context and evidence references. D6 native observation requires an actual native spawn. External badges use canonical `Role · Model Display Name` and retain `custom_role_name` in metadata. A real native supervisor uses the [route suffix in its task name](../../../clients/worker-visibility.md#external-routes-in-chat-and-habblaud). Foreground Usage Routing executes in the coordinator.

### Task 1: Connect selection and record the actual execution route

**Files:** Modify `runtime/usage_routing.py`, `tests/test_usage_routing.py`, `policies/03-agent-delegation.md`, `clients/delegation.md`, and `skills/manage-usage-routing/references/delegation.md`.

**Interfaces:** Preserve `Controller._run(...)`, `evaluate`, `handoff` and existing native controls. Add `execution_route: str` with the literal value `openai-api` to a successful generation result. This field reports the runner path even for a simulation; `simulation_only` remains authoritative and simulations cannot register. Legacy replay/state-only results make no new execution claim. Do not change task/proposal schemas or approval digests merely to store display metadata.

- [ ] Add `test_generation_reports_actual_route_for_manual_and_smart_selection` to `tests/test_usage_routing.py`: use existing synthetic count/generation fixtures for explicit `openai-api` and qualified Smart `auto`; assert `result['execution_route'] == 'openai-api'`, `accepted is False`, `requires_native_review is True`, and unchanged run/attempt IDs. Preserve existing explicit-provider/local preference and paused-selection assertions.
- [ ] Run `python3 -B -m unittest discover -s tests -p 'test_usage_routing.py'`; expect the new assertion to fail because the result field is absent, with existing tests unchanged.
- [ ] Add the literal result field at the successful `_run` return. Do not move or duplicate routing selection into the visibility layer.
- [ ] Add one narrowly triggered reference under policy selection: when considering an already authorized Usage Routing proposal, load its existing delegation procedure; only explicit management loads setup. In `clients/delegation.md`, identify its launch/observe controls as native-only and point external proposals to the existing Usage Routing procedure. Record the ownership table above in that procedure, without duplicating native controls.
- [ ] Run the same suite; expect PASS. Walk four representative instruction paths: ordinary native worker (no routing setup loaded); approved Manual proposal (routing admission and native review loaded); Smart `auto` with local preference (preference preserved); API-only stop (no silent native/paid fallback). Record observed loading decisions during Task 3 validation.
- [ ] Review this bounded candidate under existing independent-review rules. Coordinator commits only when separately authorized: `feat: connect usage routing to delegation evidence`.

### Task 2: Extract verified external display registration

**Files:** Modify `runtime/worker_visibility.py`, `runtime/usage_codex_host.py`, `tests/test_worker_visibility.py`, `tests/test_usage_codex_host.py`, `tests/test_usage_codex_bridge.py`; inspect `scripts/usage_codex_host.js` and alter it only if regression tests require forwarding the new field.

**Interfaces:** Add `worker_visibility.register_external(*, module, endpoint: str, account: str, parent: str, checkout: str, run_id: str, execution_route: str, model_display_name: str, visibility: dict, state_directory: Path) -> dict`. It returns the existing compact visibility result (`delivered`, optional `reason`). It validates bounded display inputs, constructs the stable external UUID and canonical-role label, and calls the existing `publisher(...)("completed")`. It receives an already trusted transport and host-verified parent/run; it is not an authentication or admission API and is not exposed through JSON CLI. Preserve `_publish_generation(plan, task, evidence, result, habblaud)` as the Codex boundary and preserve `execute(...)`/`generation_ready(result)` ordering.

- [ ] Add host regression cases using real fixture ledger entries: individually alter result run ID, simulation flag, acceptance/review flags, execution route, task, consent scope, admission pin, generation state, closed state and cost. Assert `delivered is False` and no publisher/transport events; preserve accounting byte-for-byte. Use `generation_unconfirmed` for mismatched execution evidence. A plan, count-only result, simulation and unresolved generation must create no row.
- [ ] Add shared helper tests for exact legacy OpenAI UUID and receipt path; canonical `Scout · GPT-6.1 Sol` despite a preserved custom alias; replay of the same identity; legacy delivered receipt; conflicting parent/label; terminal repeat; active receiver-reset behavior through the unchanged publisher; unsafe receipt directory. Assert no duplicate identity, no silent relabel and no source/output in events. Retain original historical binding rather than “upgrading” its label.
- [ ] Add a fake second-host test that supplies its own verified parent, a non-Codex account, the same actual `openai-api` execution route and an in-memory transport. Assert the same registration helper and UUID algorithm are used without invoking Codex observers. A companion Codex-host test must still reject a non-Codex live Habblaud parent. This proves separation, not a working Claude transport.
- [ ] Extend the bridge fixture so generation includes `execution_route`; assert it survives when later visibility fails, stalls, returns malformed output or raises during the observation wait. Assert the settled generation is emitted before display publication and still requires native review. Independent review remains required.
- [ ] Run `python3 -B -m unittest discover -s tests -p 'test_worker_visibility.py'` and `python3 -B -m unittest discover -s tests -p 'test_usage_codex*.py'`; expect missing-helper/route assertions to fail before extraction.
- [ ] Implement `register_external` in the existing visibility module, reusing `declaration`, `display_label`, `publisher` and private receipts. Form the identity string `vcw:{execution_route}:{parent}:{run_id}` so `openai-api` remains byte-for-byte compatible. Validate route as a bounded identifier without `:`; this is an identity component, not a provider allowlist or permission grant. Keep the original declaration in the receipt and use its canonical role for the external label.
- [ ] Keep all existing generation/ledger checks inside `_publish_generation`, add the actual-route check against `openai-api`, and retain Codex parent/account/provider verification plus trusted plugin-root/model validation there. Call the helper only afterward. Preserve the existing optional-visibility exception boundary and compact failure semantics. Do not move financial evidence, Codex assumptions or parent authentication into the helper.
- [ ] Run both targeted commands again, plus `python3 -B -m unittest discover -s tests -p 'test_usage_budget.py'`; expect PASS, including unresolved API accounting and native review. The bridge must return the proposal without waiting for a display success; no new inference, model reviewer or display registry exists.
- [ ] Review the stable coupled diff for evidence spoofing, identity compatibility, receipt safety and accounting separation. Coordinator commits only when separately authorized: `refactor: share verified external worker registration`.

### Task 3: Document client boundaries and validate the integrated slice

**Files:** Modify `clients/worker-visibility.md`, `skills/manage-usage-routing/references/codex-host.md`, `docs/policy-sources.md`, `docs/usage-routing-validation.md`; regenerate `clients/instruction-map.json` if required by these edits.

**Interfaces:** No new executable interface. Document Task 2's internal helper contract, actual route versus client transport, and the existing `generation_ready` bridge result. Existing native declaration, dispatch and handoff interfaces remain authoritative.

- [ ] Update the triggered contracts to explain that registration follows verified generation, external completion is not acceptance, and Habblaud failure cannot veto native review. Specify that the installed transport currently posts Codex hooks; do not claim passing a `claude` string enables Claude Code. A future host must supply its own trusted admission/approval, native reviewer observations, parent verification and qualified client transport while reusing common routing/accounting and this display helper.
- [ ] Add source-to-contract coverage for selection, evidence ownership, display identity, uncertainty and review. Keep research references in `docs/policy-sources.md`; preserve entry-point limits and existing loading triggers. Regenerate with `python3 -B scripts/read_instructions.py --prepare` if the instruction map requires it.
- [ ] Re-run the four representative loading paths from Task 1 plus ordinary native review, unresolved external generation with authorized native handoff, completed API proposal with unavailable Habblaud, and a proposed Claude Code host with no qualified transport. Verify required procedures load, unrelated setup stays unloaded, native claims/review remain mandatory, and Claude live execution remains unqualified. Record actual checks and limitations, not inferred behavioral success from string matches.
- [ ] Run `python3 -B -m unittest discover -s tests -p 'test_usage*.py'`, `python3 -B -m unittest discover -s tests -p 'test_worker_visibility.py'`, and `python3 -B scripts/validate.py`. Record exact results and any baseline failures separately. Use existing host tests for settlement, one task through review and no repeat live run.
- [ ] Obtain required independent review of the final integrated candidate against current `main`; resolve blockers and recheck affected tests. Report offline package proof, instruction-path evidence, and live client qualification in separate fields. Coordinator commits only when separately authorized: `docs: define usage routing registration boundaries`.

## Rollout and live acceptance — separate authorization

1. Integrate reviewed prerequisite slices first, then Task 1, Task 2 and Task 3. Review the pinned package and installed transport compatibility before any approved update. Never repin active work silently or overwrite newer concurrent changes. No installed configuration is changed by this planning work.
2. Preserve historical receipt names and identities across update/rollback. Optional display failure permits ordinary work to continue. Rollback restores the prior package/approved hook configuration if needed; it does not remove financial records, reset admission, regenerate IDs or discard an unresolved run.
3. Treat recovery of the earlier repository-backed pilot as a separate operational task. See the [local retirement record](../../usage-routing-validation.md#local-retirement--4-november-2026) for the last observed status. Keep the charge unresolved until the corresponding recovery evidence exists. Elapsed time, process death, OFF state, a fresh balance or a display failure does not settle the charge. Do not unblock a live badge test by deleting/resetting state or issuing a replacement generation.
4. Prepare a live first-badge acceptance only after package installation is separately authorized, the exact client/transport are qualified, and current funding/price/review/recovery gates pass. Freeze one small approved task using the existing host, show exact source packet/scope, model/reasoning, organization/project, funding pool, maximum exposure, expiry, native reviewer and intended Habblaud parent/account/role. Obtain the exact `Approve Usage Routing <digest>` line as a direct user message in the same enrolled chat that froze the plan.
5. For the approved attempt, observe real provider dispatch and ledger settlement, then receiver readback and the **first rendered** `Role · Model Display Name` badge for that same stable external UUID. Capture the first observation independently of later correction. Verify correct parent, model text, separate native reviewer/supervisor identity if one actually exists, and display retirement without accepting the proposal. A generic/missing/wrong first badge fails visible qualification; a later correct badge is recovery only. No new request is authorized merely to obtain another screenshot.
6. Record exact Codex chat activity naming separately as unsupported for the current foreground route; do not mark it passed from commentary, a thread rename or Habblaud rendering. Run native integration/checks and independent review under existing authority, then record the verified task completion. Record reported local API cost separately from provider settlement/funding attribution and unknown native savings.

Completion of implementation means reviewed changes, passing relevant offline checks and honest qualification records. Live first-badge acceptance and Claude Code execution remain explicitly separate, unclaimed outcomes until actually observed under their own authorization.
