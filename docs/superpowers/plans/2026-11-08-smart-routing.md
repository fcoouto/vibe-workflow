# Smart Routing (Vibe Router) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace vibe-workflow's competing worker selectors with built-in Smart Routing, reusing Workstreams jobs and reserving eligible funding for parallel work, delivered in independently useful slices.

**Architecture:** Workstreams owns the canonical task graph. One Python routing core (settings, profiles, compiled evidence, deterministic ranker, allocator and the existing SQLite ledger) serves every client through one local JSON command. Host adapters stay thin: the OpenCode V2 adapter is a policy-free shim that blocks unadmitted launches and requests before transmission.

**Tech Stack:** Python 3.11+ standard library and SQLite (the repo's runtime contract), `unittest`, one OpenCode V2 plugin shim whose loader and test runner P0.1 pins.

**Spec:** [Smart Routing — Vibe Router integration design](../specs/2026-11-08-usage-routing-opencode-subscriptions-design.md) v1.5, which includes amendments A1–A14.

- **Version:** 1.7 draft
- **Date:** 2026-11-08
- **Status:** Restructured for incremental delivery; spec amended to v1.5; runtime implementation gated per slice by P0. Amended 2026-11-08 at the user's direction: no shadow or simulation mode (A14).
- **Supersedes:** v1.6 (`docs/Smart Router - Impl Plan.md`).
- **Target:** Vibe Router inside vibe-workflow, with OpenCode V2 as the first enforced worker-routing host.

The canonical design owns product behavior and merge decisions. This plan records the ordered implementation handoff. Implementation belongs in vibe-workflow; its runtime must not depend on the standalone planning checkout.

### Changes from v1.6

| Change | Reason |
|---|---|
| Ledger, allocator and ranker stay in Python; OpenCode gets a thin shim (A1). | The repo's runtime contract is Python + SQLite under `runtime/adapters/`, and Codex/Claude Code coordinators need the same ledger without OpenCode running. |
| Deterministic compiled-evidence ranking is the v1 default; model ranking is an optional later promotion (A2–A5). | Removes per-assignment inference, free-endpoint churn and data-handling exposure from the hot path; the deterministic policy was already the evaluation baseline. |
| Allocation inputs account for time to reset and stranded credit; within-class allocation becomes stateless (A6–A9). | Utilization without pace misjudges short versus long windows; expiry pressure should reflect credit that would otherwise expire unused; fewer persisted counters. |
| Four slices with per-slice P0 gates; allocator replay and a bounded enforced canary before cutover (A11); no shadow or simulation mode (A14). | v1.6 deleted old selectors before any qualification, leaving nothing to roll back to. A route is active or it is not; nothing computes decisions without enforcing them. |
| New billing/terms gate P0.0, applied identically to Claude and OpenAI subscription and API routes; former P0.9 and P0.10 move to the phases that build them (A12). | Whether a subscription route counts as included usage depends on its harness, which is a billing question before it is a telemetry question; router fitness and the shared preparation path cannot be proven before they exist. |
| Default concurrency caps; OFF-mode worker-choice shape defined (A10, A13). | Unlimited fan-out after removing the slot meets provider limits; v1.6 left "configured choices" undefined. |
| Formulas, constraints and amendments are defined in the spec and referenced here. | One definition per rule prevents plan/spec drift. |

## 1. Components and responsibilities

| Component | Responsibility |
|---|---|
| **Coordinator (user-selected model/effort)** | Writes each Workstreams job, including the structured `TaskProfile` fields, and supervises, verifies and synthesizes. Never switched by routing. |
| **Profile service (`ensure_profile`)** | Validates coordinator- or metadata-supplied profiles, applies deterministic requirement floors, and fills gaps conservatively. Calls Jev only when the user has configured it and a semantic field remains unresolved. |
| **Evidence compiler** | At maintenance refresh, compiles vendor guidance, catalog availability and benchmark evidence into a validated, versioned strategy table. |
| **Deterministic ranker** | Looks up qualified model/effort/harness strategies for a profile from the compiled table. No inference. |
| **Model ranker (optional, S4)** | Ranks strategies from supplied evidence only after replay shows it beats the deterministic ranker. Never required for dispatch. |
| **Deterministic allocator** | Checks feasibility, chooses the funding class and route, reserves capacity and authorizes execution. |
| **Host adapter** | Enforces the admitted route before transmission and reports lifecycle events. Contains no selection or funding policy. |

Profiles describe; rankers order; the allocator validates and authorizes. Capability, funding, review and permission constraints are enforced in code. Model output never authorizes a launch.

## 2. Constraints

Every constraint in v1.6 "Global Constraints" remains in force through the spec v1.5 sections below; this table only points to the owning definition.

| Constraint | Owning definition |
|---|---|
| Fixed coordinator; bounded briefs instead of model names | Spec §1, §3, §8 (coordinator boundary) |
| Workstreams owns the graph; lightweight adoption; one preparation path | Spec §4.1–4.2 |
| Managed admission for every worker, nested spawn, reviewer, repair and escalation | Spec §1, §8 |
| Built in, ON by default, explicit OFF persists, disable needs no model call | Spec §3 |
| Enabled versus ready; default ON never configures paid services or bypasses P0 | Spec §3 |
| Dynamic evidence; no hard-coded commercial model IDs, effort ladders or tiers | Spec §5.2 (A2) |
| Separate funding pools; API Usage opt-in with secure keys; billing class per route; same subscription and API route model for every provider | Spec §6 (A12) |
| Credit settings (98% default, 0–100%, `no-limit`) | Spec §6.2 |
| Parallel buffered reservations; no global slot; concurrency caps | Spec §7.4 (A10) |
| Subscription/API preference | Spec §7.2 (A6–A8) |
| Breaking merge; one selector and one ledger writer | Spec §10.4 (A11) |
| Whole-task accounting and retention | Spec §11 |

## 3. Amendments (in spec v1.5)

Amendments A1–A14 are defined in the spec; the IDs below are used throughout this plan. The spec's Appendix B lists each one's sections.

| ID | Summary | Spec sections |
|---|---|---|
| A1 | Python routing core; one JSON command (`scripts/routing.py`) for all clients; policy-free OpenCode shim. | §10.3 |
| A2 | Strategy table compiled at maintenance refresh; deterministic ranking by default; model ranking promoted only on evidence. | §5.2–5.4 |
| A3 | Ranker unavailability falls back to deterministic ranking instead of blocking. | §3, §5.3, §9 |
| A4 | Coordinator fills `TaskProfile` fields; conservative floors for gaps; Jev optional and user-configured. | §5.1, §4.1 |
| A5 | Profiling/ranking endpoints must be zero-retention, no-training, unless the user opts in per endpoint. | §5.2 |
| A6 | Subscription utilization projected at reset (`pace_w`, `min_elapsed_fraction = 0.10`). | §7.2 |
| A7 | Expiry pressure `E = clamp(1 − t/T) × clamp(stranded / r)`, `lookback_hours = 72`. | §7.2 |
| A8 | Class deficits derived from admission records; one unit per assignment per `(role, complexity_band)` bucket. | §7.2 |
| A9 | Within class, admit the highest-pressure feasible strategy; no deficit state. Changes case 7. | §7.3, §13 cases 7–8 |
| A10 | Concurrency caps: onboarding limit, else 4 total and 2 per provider account. | §7.4 |
| A11 | Allocator replay and a bounded enforced canary before cutover; budgeted quality re-execution. | §10.4, §13 |
| A12 | Billing class per route. **Claude and OpenAI use the same model:** each offers subscription routes (subscription login through a harness the vendor bills as included usage, such as Codex or Sign in with ChatGPT for OpenAI, and Claude Code or OpenCode's Claude Agent provider for Anthropic) and API Usage routes (secure key, `api_grant`, plus `api_prepaid` only under `no-limit`). Extra or purchased usage is excluded for both. | §1, §6.1, §12 |
| A13 | OFF-mode `worker_choices`: ordered per-role list; first feasible entry admitted. | §3 |
| A14 | No shadow or simulation mode: a route is active or it is not. API Usage and live funding are explicit enabled/disabled states with their P0 gates, not a third mode. | §3, §10.4, §12, §13 |

## 4. Slices, placement and P0 gates

| Slice | Delivers | Independently useful because |
|---|---|---|
| **S1 — Parallel ledger** | Per-task padded claims, allowance policy, concurrency caps, slot removal in the existing Python ledger. | Removes serial API execution and the 20% floor. S1 contains no simulation mode: `simulation_only` is removed from `runtime/usage_routing.py`. API Usage stays OFF until explicitly enabled with a secure key, and live API funding needs P0.3/P0.4 before it is enabled. |
| **S2 — Unified deterministic routing** | Settings, profiles, compiled evidence, deterministic ranker, allocator, cutover criteria, allocator replay. | One decision engine is measurable offline by replaying recorded arrivals against isolated ledgers. |
| **S3 — OpenCode V2 managed launches** | JSON command, OpenCode adapter and shim, dispatch authority, enforcement, lineage and settlement. | Enforced routing on the first host; Codex/Claude Code use the same command. |
| **C — Cutover** | Feature contracts F01–F17, caller replacement, retirement of old selectors and slot paths, default ON. | Runs only after S2 replay and S3 canary criteria pass. |
| **S4 — Optional model ranking** | Routing benchmark, quality evaluation, model-ranker promotion. | Ships only if it beats the deterministic ranker. |

Proposed placement (new files appear with their slice only):

```text
runtime/usage_budget.py              # S1: the one ledger (extended)
runtime/usage_routing.py             # S1: slot removal; retired at C
runtime/routing_settings.py          # S2: Smart Routing, caps, worker_choices, thresholds
runtime/routing.py                   # S2: contracts, ensure_profile, prepare_assignment
runtime/routing_evidence.py          # S2: evidence compiler and strategy table
runtime/routing_allocator.py         # S2: pure allocation functions
scripts/routing.py                   # S3: the single JSON command for all clients
runtime/adapters/opencode.py         # S3: probe, event normalization
runtime/adapters/opencode_plugin.ts  # S3: policy-free pre-transmission shim
clients/opencode.md                  # S3: qualification and loading contract
```

### P0 gates by slice

Gate meanings are in spec §12 and v1.6 §4; P0.0 is new. A missing proof is `unverified`, never `qualified`.

| Gate | Required proof | S1 | S2 | S3 |
|---|---|---|---|---|
| P0.0 Billing/terms | Every candidate route classified under A12 from current terms and observed billing. | live | ✓ | ✓ |
| P0.1 Source/runtime | Reconciled baseline; shim loader, test runner and per-request latency; one ledger path across worktrees. | ✓ | ✓ | ✓ |
| P0.2 Subscription telemetry | Account-bound utilization, scale, windows, resets and freshness for each `included_subscription` route. | — | ✓ | ✓ |
| P0.3 API grants | Grant origin, scope, balance, expiry, debit order, prices and eligible transport. | live | ✓ | ✓ |
| P0.4 Credential/funding binding | Scoped enablement, secure key resolution, isolated concurrent credentials, no spill past strict allowance. | live | — | ✓ |
| P0.5 Model/effort | Requested versus observed model/variant on each admitted route. | — | — | ✓ |
| P0.6 Mandatory routing | Built-in, nested and alternate launch paths blocked before transmission. | — | — | ✓ |
| P0.7 Dispatch recovery | No duplicate dispatch from duplicate IDs, racing callers or lost acknowledgments. | — | — | ✓ |
| P0.8 Lineage/accounting | Coordinator, control, worker, review, repair, auxiliary and retry usage attributed or marked unknown; gaps resynchronized. | — | — | ✓ |

"live" means the slice can merge without the gate, but live funding stays disabled until the gate passes. Former P0.9 (role fitness) is the S4 entry gate; former P0.10 (Workstreams binding on the target host) is the S3 exit. Unsupported routes stay out of enforced automatic dispatch; after-the-fact interruption is recovery, not enforcement.

## 5. Review focus

- **RF1 — Independent controls:** Smart Routing ON never activates API Usage, OpenRouter, Council, Jev or observability; a direct grant never funds a gateway. Owner: S2, C.
- **RF2 — Cached context:** profiles and strategy-table rows never cross incompatible scopes, roles or changed requirements. Owner: S2.
- **RF3 — Ready-to-launch races:** a reopened dependency, changed writer or disabled route after preparation blocks an unsent launch, even on a cache hit. Owner: S3.
- **RF4 — Accounting edges:** free pricing, unknown completion, expiry and parent/child transfers never divide by zero, double-charge or release uncertain capacity. Owner: S1, S3.
- **RF5 — Ordinary UI/management:** startup, naming, status, diagnostics and deterministic checks perform zero routing inference. Owner: C.
- **RF6 — Billing classification:** no `purchased_usage` route is treated as subscription headroom, whatever its telemetry shows; Claude and OpenAI subscription and API routes are classified and admitted under the same rules. Owner: P0.0, S1, S2.
- **RF7 — Control-plane data:** no brief or profile reaches an endpoint that retains, trains on or forbids confidential data, unless the user opted in for that endpoint. Owner: S2, S4.
- **RF8 — No availability regression:** ranker unavailability never blocks delegation that the deterministic path can admit. Owner: S2, C.

## 6. Traceability and completion

The independent review of v1.0 lives in the standalone planning checkout, outside this repository, and does not approve this plan. Its findings remain acceptance requirements:

| Finding | v1.7 owner |
|---|---|
| Funding/account binding | P0.0, P0.3–P0.4, S1, S3 |
| Exclusive dispatch and ambiguous launches | P0.7, S3 |
| Stale observations and double debits | S1, S3 |
| Stage-bound reviewer funding | S1, S3 |
| Retention versus unresolved safety | S3, C |
| Rescue attribution and sparse quality evidence | S3, S4 |
| Deterministic allocation and comparable units | S2 |
| Cohort costs including failures | S3, S4 |
| Expiry after inactivity | S3 |
| Chronological replay and quality thresholds | S2 (allocator replay), S4 (quality) |

**Definition of done:** a user works with their chosen coordinator. Workstreams writes dependency-aware, bounded jobs whose profiles the coordinator supplies and code validates; Jev is optional. Built-in Smart Routing is ON by default and supplies a deterministic, evidence-backed strategy plus fresh funded admission for every launch on every client through one command. Ranker unavailability degrades to the deterministic path instead of blocking. Ready independent jobs run concurrently within buffered reservations and concurrency caps; supported nested work uses the same path. Claude and OpenAI subscription and API routes follow the same model, API Usage routes participate only with explicit enablement and a secure key binding, and no route is misclassified as subscription headroom. Outcomes settle once into root-task accounting. Users can turn automatic selection OFF and fall back to explicit worker choices without losing ownership or weakening funding limits. No duplicate task graph, legacy selector, second ledger writer or standalone-checkout dependency remains.

## Appendix A — Feature ownership

The feature-by-feature matrix is [design Appendix A](../specs/2026-11-08-usage-routing-opencode-subscriptions-design.md#appendix-a--impact-on-every-existing-feature). Ownership in this plan:

| Feature IDs | Feature | Owner |
|---|---|---|
| F01–F02 | Prepare my Workspace; Onboard my Project | S2 (settings), C.2a |
| F03, F11 | Workstreams; planning/native delegation/continuation | S2.2, S3, C.2b |
| F04 | Manage API Usage | S1, C.2c |
| F05–F07 | Manage OpenRouter; proposals; workers | C.2c |
| F08 | LLM Council (checkout feature) | C.2d |
| F09 | Review my Project (checkout feature) | C.2d |
| F10 | Manage Observability | S3.6, C.2e |
| F12 | Resource admission; capacity recovery | S3.5, C.2b |
| F13–F14 | Tests/security/code review/evidence; worktrees/delivery/merge | S3.6, C.2e |
| F15–F17 | Naming/Habblaud; context/handoff; startup/maintenance/diagnostics | S2.3 (refresh), C.2a, C.2b |

## Appendix B — Execution tasks

Read the owning spec section and amendment before each task. Do not invent provider methods to make tests pass. Commit checkpoints apply only when the execution workflow authorizes commits. Every task ends with `python3 -B scripts/validate.py` passing; observed host/provider results are reported separately from package and unit checks.

### Task 0.1: Reconcile the baseline

**Files:** create `docs/smart-routing-feasibility.md`.

- [ ] Choose the implementation baseline (inspected `main` was `d73252a77d1e784f8a7d6f637bc661467485178d`; this checkout lacks `runtime/usage_*.py`, `runtime/external_delegation.py` and `skills/manage-usage-routing/`). Record the chosen revision and how the local branch changes are reconciled.
- [ ] Record the owner of every F01–F17 row on that baseline and the actual test toolchain.
- [ ] Run `python3 -B scripts/validate.py`; expected PASS.

### Task 0.2: Amend the spec

**Files:** modify `docs/superpowers/specs/2026-11-08-usage-routing-opencode-subscriptions-design.md`.

- [x] Apply A1–A13 to the sections each one names; bump the spec to v1.5, and update §13 case 7 per A9 and §12 with P0.0.
- [x] Replace section 3 of this plan with links to the amended spec sections.
- [x] Run `python3 -B scripts/validate.py`; expected PASS.

### Task 0.3: Classify billing and terms (P0.0)

**Files:** create `docs/smart-routing-capability-matrix.md`, `tests/fixtures/smart-routing/capabilities.json`.

**Interface:** one manifest record per route: `{route_id, provider, account_ref, harness, billing_class, terms_source, terms_checked_at, evidence_refs, status: qualified | unsupported | unverified}`. No secrets, prompts or account identifiers.

- [ ] Classify every route currently reachable from the user's configured clients under A12, citing current vendor terms and observed billing.
- [ ] Record the reference Claude subscription route first: OpenCode running Claude through its Claude Agent provider (the Claude Agent SDK) on the Claude subscription, the user's working setup (spec §6.1; standing disputed, see the capability matrix). Capture its harness identity and rate-limit telemetry as an `included_subscription` fixture.
- [ ] For Claude and OpenAI alike, find the remaining subscription routes the vendor bills as included usage: the provider's own harness (Claude Code, Codex) launched directly, OpenCode's Claude Agent provider, and OpenCode's Sign in with ChatGPT. Record each as `included_subscription`, or as `purchased_usage` if it bills extra usage.
- [ ] For both providers, confirm that API Usage key routes draw on the subscription-linked API allowance (`api_grant`) before any other credit.
- [ ] Stop work on any slice whose required routes are all `unsupported` or `unverified`, and report it.

### Task 0.4: Probe the remaining P0 gates per slice

**Files:** extend the capability matrix and fixtures.

- [ ] P0.1: pin the OpenCode plugin loader and shim test runner; measure the median and p95 latency of a no-op `scripts/routing.py` call from a plugin hook.
- [ ] P0.2–P0.3: record telemetry and grant observations for each route classified `included_subscription` or `api_grant`, within authorized bounds.
- [ ] P0.4–P0.8: probe only once S3 is next; record requested versus observed behavior.
- [ ] Sanitize the earlier scratch probe output before reusing any of it.

### Task S1.1: Allowance policy replaces the 20% floor

**Files:** modify `runtime/usage_budget.py`, `tests/test_usage_budget.py`.

**Interface:** the organization policy stores `allowance_bp` (0–10000, default 9800) or `allowance: "no-limit"`, plus the verified issued allowance `G`. `Ledger.credit_headroom(org)` returns `max(0, B − ceil((10000 − allowance_bp) × G / 10000) − R − U)` in integer micro-units; `no-limit` uses protected = 0 and `B` across verified eligible lots.

- [ ] Write failing tests: `G=100_000_000`, 9800 bp, `B=30_000_000`, `R=5_000_000`, `U=0` → `23_000_000`; 0 bp → 0; 10000 bp → `25_000_000`; `no-limit` excludes unverified lots; a billing-date change without an observed issuance mints nothing.
- [ ] Run `python3 -B -m unittest tests.test_usage_budget`; expected: the new tests fail.
- [ ] Remove the `(balance + 4) // 5` floor paths in `usage_budget.py` and `usage_routing.py`; implement the policy.
- [ ] Re-run; expected PASS.

### Task S1.2: Per-task claims replace the slot

**Files:** modify `runtime/usage_budget.py`, `tests/test_usage_budget.py`.

**Interface:** `begin(...)` no longer reads or writes `data['slot']`; the state version increments and the `slot` key is removed. Each run holds its own claim.

- [ ] Write failing tests: two affordable distinct runs both begin; a repeated `run_id` returns the existing run without adding a hold; two `Ledger` instances on one file, racing distinct runs from two threads for the last headroom, admit at most what fits.
- [ ] Rewrite every test that expects `request_in_flight` for an affordable second task into a shared-headroom assertion.
- [ ] Run `python3 -B -m unittest tests.test_usage_budget`; expected: the new tests fail.
- [ ] Remove the `slot` checks from `begin`, `reserve_generation` and `dispatch`; re-run; expected PASS.

### Task S1.3: Padded reservations, top-ups and safe release

**Files:** modify `runtime/usage_budget.py`, `tests/test_usage_budget.py`.

**Interface:** `begin(..., estimate, next_request_bound, margin_bp=2000, ...)` reserves `max(ceil(estimate × (10000 + margin_bp) / 10000), next_request_bound)`. `top_up(run_id, added, revision, now)` admits atomically or raises `insufficient_headroom`. `complete(...)` charges the actual cost and releases unused padding; `uncertain(...)` retains exposure.

- [ ] Write failing tests for the spec §7.4 example in micro-units: with `30_000_000` available, estimates 5/10/8 reserve `6_000_000`/`12_000_000`/`9_600_000`; a fourth reserve of `3_600_000` fails; completing the first at `4_000_000` releases `2_000_000` and the fourth then fits. A timeout keeps its full hold, and a top-up beyond headroom fails without changing state.
- [ ] Write a failing test that a parent review provision transferred to a child is not counted twice.
- [ ] Run the module tests (expected FAIL), implement, re-run (expected PASS).

### Task S1.4: Concurrency caps (A10)

**Files:** modify `runtime/usage_budget.py`, `tests/test_usage_budget.py`.

- [ ] Write failing tests: with caps 4 total and 2 per account, the third open run on one account fails with `concurrency_cap`, a run on another account succeeds, and closing a run frees a place.
- [ ] Implement the caps as admission checks reading open runs inside the same transaction; re-run; expected PASS.

### Task S1.5: Slot-free routing, recovery and instructions

**Files:** modify `runtime/usage_routing.py`, `tests/test_usage_routing.py`, `skills/manage-usage-routing/references/*.md` and `SKILL.md` if it states the serial rule.

- [ ] Write failing tests: `_evaluate` admits an affordable second task; recovery enumerates every pending attempt, not just the slot owner.
- [ ] Remove `require(data['slot'] is None, 'request_in_flight')` and the slot-owned dispatch, completion and recovery paths; implement the enumeration.
- [ ] Remove the serial-execution rule from the skill references; keep the entry point within 150 words.
- [ ] Run `python3 -B -m unittest tests.test_usage_routing tests.test_usage_budget` and `python3 -B scripts/validate.py`; expected PASS.
- [x] Removing `simulation_only` must not remove a gate. Live API funding stays blocked until API Usage is explicitly enabled, a secure key binding is ready, qualification is recorded and admission evidence is present, and until P0.3/P0.4 pass for the route. Regression coverage: `tests/test_openai_api.py` (`live_admission_required`, `credential_unavailable`) and `tests/test_usage_live.py` (`test_off_and_json_evidence_never_resolve_a_credential`) check that no provider request is transmitted without admission evidence or a valid key binding, and that OFF never resolves a credential. Extend them if a gate combination is found uncovered.

### Task S2.1: Routing settings

**Files:** create `runtime/routing_settings.py`, `tests/test_routing_settings.py`.

**Interface:** `resolve(stored: dict) -> dict` and `update(path, change, expected_revision) -> dict`. Defaults: `smart_routing=True`, `api_usage_enabled=False`, `L=0.65`, `H=0.85`, `T_hours=72`, `min_elapsed_fraction=0.10`, `lookback_hours=72`, `margin_bp=2000`, the caps from A10 and `worker_choices={}`. Reads the Workstreams preference from its existing authority without copying it.

- [ ] Write failing tests: `resolve({})["smart_routing"] is True`; `resolve({})["api_usage_enabled"] is False`; an explicit `smart_routing=False` survives a write/read cycle; `0 <= L < H <= 1` and `T_hours > 0` are enforced; `worker_choices` entries with unknown roles or missing fields are rejected; a stale `expected_revision` is refused.
- [ ] Run `python3 -B -m unittest tests.test_routing_settings` (expected FAIL), implement, re-run (expected PASS).

### Task S2.2: Profiles in Workstreams jobs (A4)

**Files:** create `runtime/routing.py`, `tests/test_routing_profiles.py`; modify `runtime/delegation.py` and the Workstreams job/brief contract in `clients/workstreams-protocol.md`.

**Interface:** `ensure_profile(job: dict, *, jev=None) -> dict` returns a `TaskProfile` with per-field provenance (`coordinator | metadata | floor | jev`), a contract version and a semantic digest over objective, role, scope, tools, review/risk requirements and contract version.

- [ ] Write failing tests:
  - A complete coordinator profile makes zero `jev` calls.
  - Balance, progress or retry changes keep the same digest.
  - A changed role, scope or tools changes the digest.
  - Another project's scope cannot reuse a profile (RF2).
  - Missing fields with `jev=None` take conservative floors with `floor` provenance.
  - Requirement floors cannot be lowered by supplied or Jev values.
- [ ] Run, implement, re-run; expected PASS. Keep existing `selection.profile_ids` guidance references separate from task profiles.

### Task S2.3: Evidence compiler and strategy table (A2, A5)

**Files:** create `runtime/routing_evidence.py`, `tests/test_routing_evidence.py`, fixtures under `tests/fixtures/smart-routing/`.

**Interface:** `compile_table(guidance: list, catalog: list, benchmarks: list, terms: list) -> dict` produces versioned rows keyed by `(role, task_family, complexity_band, risk_band)`, each an ordered list of `{strategy_id, model, effort, harness, evidence_refs}`. `lookup(table, profile) -> list | blocked`.

- [ ] Write failing tests:
  - Rows naming models absent from the catalog, or unsupported efforts, are dropped.
  - A new catalog model qualifies only with cited evidence.
  - A failed refresh keeps the last verified table with its original age.
  - Control-plane candidates without zero-retention/no-training terms are excluded (RF7).
  - The source tree contains no hard-coded commercial model ID.
- [ ] Hook compilation into the existing maintenance guidance refresh (`policies/03-agent-delegation.md`, worker selection), outside spawn paths.
- [ ] Run, implement, re-run; expected PASS.

### Task S2.4: Deterministic ranker (A2, A3)

**Files:** modify `runtime/routing.py`; create `tests/test_routing_ranker.py`.

**Interface:** `rank(profile: dict, table: dict, constraints: dict) -> dict` returns `{strategy_ids, reason_codes, table_version, profile_digest}`; no spending authority.

- [ ] Write failing tests:
  - An exact row returns its order.
  - A missing row uses the next stricter band, then `ranking_conservative`.
  - Explicit user constraints narrow the candidates and never widen them.
  - Ranking makes zero network or model calls.
  - A table refresh re-ranks without re-profiling.
- [ ] Run, implement, re-run; expected PASS.

### Task S2.5: Pure allocation functions (A6–A9)

**Files:** create `runtime/routing_allocator.py`, `tests/test_routing_allocator.py`.

**Interface:** `projected_utilization(windows, reserve_new, now, min_elapsed_fraction) -> (float | None, reasons)`; `expiry_pressure(remaining, expected_spend, hours_to_expiry, T_hours) -> float`; `api_share(u, e, L=0.65, H=0.85) -> float`; `choose_class(shares, admissions, bucket, epoch, feasible) -> str | None`; `choose_route(candidates, now, minimum_horizon) -> str | None`. Both return `None` when nothing is eligible or fundable; `api_share` raises `ValueError` for unknown utilization; bad numbers raise `ValueError` with a fixed reason.

- [ ] Write failing tests:
  - `api_share(0.40, 0) == 0`; `api_share(0.75, 0) ≈ 0.5`; `api_share(0.80, 0) ≈ 0.75`; `api_share(0.90, 0) == 1`; `api_share(0.40, 2/3) ≈ 2/3`; `api_share(0.75, 2/3) ≈ 0.833`.
  - `expiry_pressure(remaining=50, expected_spend=0, hours=24, T=72) ≈ 2/3`; `expected_spend=50` gives 0; no grant gives 0.
  - A weekly window at 30% after 10% of its length projects above the bare 30%; a 5-hour window at 80% resetting in 10 minutes projects about 83%; before `min_elapsed_fraction`, the pace term is omitted with `pace_unavailable`.
  - A healthier alternative subscription sets `u`; stale observations return `None`.
  - Class choice from recorded admissions matches the shares over 20 sequential decisions, API wins ties, and a rejected decision leaves no record.
  - Within class, the amended case 7 sends the first 55 to the 75-pool; case 8 picks the 1-hour pool for the first 17 admissions; zero-demand candidates never divide by zero.
- [ ] Run, implement, re-run; expected PASS.

### Task S2.6: Allocate and reserve in one transaction

**Files:** modify `runtime/routing.py`, `runtime/usage_budget.py`; create `tests/test_routing_admission.py`.

**Interface:** `admit(assignment, ranking, settings, observations, *, ledger) -> dict | blocked` filters by eligibility (spec §6.4), billing class (A12) and feasibility (spec §7.1), computes the class and route, then reserves and records `{bucket, shares, class, route, settings_revision}` in the same ledger transaction. The result is the admission (single-use dispatch authority added in S3).

- [ ] Write failing tests:
  - API Usage OFF excludes direct API routes and their expiry pressure.
  - No organization defined or enabled: `admit` still works for subscription and `worker_choices` routes, funded routes are excluded with `no_funded_organization`, and nothing raises. Routing never creates or enables an organization or key; the Personal workspace is enabled only by the user, and an enabled organization with no ready key is unavailable with a reason (spec §6.4).
  - A stored-but-unenabled key excludes them; a wrong-scope or revoked binding blocks the route.
  - OpenRouter uses only its own funding gate.
  - A `purchased_usage` route is never counted as subscription headroom (RF6).
  - With identical fixtures for Claude and OpenAI, both providers produce the same candidate kinds and decisions. Each provider's subscription and API routes compete under section 7.2, and API Usage OFF leaves both providers' subscription routes available (RF6).
  - OFF mode admits the first feasible `worker_choices` entry or returns `worker_choice_unavailable`.
  - A model-ranker timeout falls back to the deterministic ranking (RF8).
- [ ] Run, implement, re-run; expected PASS.

### Task S2.7: Cutover criteria (A11)

**Files:** create `docs/smart-routing-validation.md`.

- [ ] Write the cutover criteria before any canary starts: minimum canary sample, the disagreement rate that triggers quality re-execution, zero funding violations in allocator replay, latency budgets for warm and cold preparation, and the canary bounds (below). A disagreement is a recorded historical assignment whose accepted model/effort or funding class differs from the strategy the new ranker and allocator choose for it; S2.8 replays those recorded assignments to produce the rate.
- [ ] Define the canary bounds in the same document: one named project allowlist, a spend cap per funding pool, a maximum duration, concurrency caps (A10), an abort condition (any funding violation, unresolved launch or recovery error) and the Smart Routing OFF kill switch (spec §3), which stops new automatic admissions without a model call.
- [ ] There is no shadow or simulation mode and no decision-only hook at the current selectors. Current selectors keep dispatching until C.3 replaces them.

### Task S2.8: Allocator replay

**Files:** create `tests/test_routing_replay.py`, `tests/fixtures/smart-routing/arrivals/*.json`.

- [ ] Write replay cases over isolated ledgers: identical chronological arrivals, concurrent claims, resets and expiries, catalog refresh, OFF toggles, and a six-job dependency plan with zero Jev calls.
- [ ] Assert: no funding violation, no resurrected capacity, exact replay of class and route decisions from admission records.
- [ ] Run `python3 -B -m unittest tests.test_routing_replay`; expected PASS once S2.5–S2.6 are complete.

### Task S3.1: Single routing command

**Files:** create `scripts/routing.py`, `tests/test_routing_command.py`.

**Interface:** JSON on stdin and stdout; subcommands `prepare` (profile, ranking, admission), `check` (pre-transmission envelope and route check), `record` (lifecycle event), `status` (local only, no inference), `set` (settings, including OFF). Every response is versioned and either an admission/decision or `{"state": "blocked", "reason": …}`.

- [ ] Write failing tests: malformed input is blocked; `status` and `set` make zero model, network or secret-store calls (RF5); `set smart_routing=false` invalidates unsent automatic admissions under the old revision.
- [ ] Run, implement, re-run; expected PASS.

### Task S3.2: OpenCode adapter and client contract

**Files:** create `runtime/adapters/opencode.py`, `clients/opencode.md`, `tests/test_opencode_adapter.py`; modify `clients/index.md`, `clients/instruction-map.json` and the support targets in `docs/onboard-my-project-plan.md#client-scope-and-adapter-organization`.

- [ ] Write failing tests against recorded, sanitized OpenCode V2 hook and event payloads: normalization yields task, parent, stage, attempt, session/message IDs, requested model/effort and route identity; unknown fields stay unknown.
- [ ] Run, implement, re-run; expected PASS.

### Task S3.3: Pre-transmission shim

**Files:** create `runtime/adapters/opencode_plugin.ts`; extend `tests/test_opencode_adapter.py`.

- [ ] The shim forwards each worker spawn, nested spawn, model switch and provider request to `scripts/routing.py check`, blocking unless it returns an admission. A missing or failing command blocks (fail closed). The shim contains no selection or funding logic.
- [ ] Qualify P0.6 live: built-in, nested and alternate launch paths are blocked before transmission. Record any unblockable path as unsupported in the capability matrix.

### Task S3.4: Single-use dispatch authority

**Files:** modify `runtime/usage_budget.py`, `runtime/routing.py`; create `tests/test_routing_dispatch.py`.

- [ ] Write failing tests: 15 callers racing for one attempt produce at most one authority; distinct affordable attempts run concurrently; a lost acknowledgment leaves the claim `unknown` and is not retried without proven host deduplication or proof of non-launch.
- [ ] Run, implement, re-run; expected PASS. Qualify P0.7 live.

### Task S3.5: Fresh gates at dispatch (RF3)

**Files:** extend `tests/test_routing_dispatch.py`; modify `runtime/routing.py`.

- [ ] Write failing tests: reopening a dependency, changing writer ownership, losing a resource claim, disabling API Usage or changing the credential binding after `prepare` produces zero sends, even on a profile/ranking cache hit.
- [ ] Run, implement, re-run; expected PASS. This is the former P0.10 exit on the OpenCode host.

### Task S3.6: Lineage, settlement and retention

**Files:** modify `runtime/routing.py`, `runtime/usage_budget.py`, `runtime/evidence.py` integration; create `tests/test_routing_accounting.py`.

- [ ] Write failing tests:
  - Duplicate child receipts charge once.
  - One accepted task at 100 plus one rejected at 90 reports 190 per accepted delivery.
  - Reviewer rescue is recorded as incremental rework.
  - Ambiguous timeouts keep their exposure.
  - Expired diagnostic evidence cannot influence a decision, while active caps, unknown claims and ownership survive retention (spec §11 defaults).
  - Observability OFF exports nothing and loses no local accounting.
- [ ] Run, implement, re-run; expected PASS. Qualify P0.8 live.

### Task S3.7: Canary

- [ ] Enable enforced routing for one explicitly chosen project on OpenCode within the S2.7 canary bounds; record admissions, blocks, latency and settlement against the S2.7 criteria, and abort on any bound or abort condition. Other projects and clients continue on current selectors.

### Task C.1: Confirm the cutover criteria

- [ ] Confirm that S2.8 replay and S3.7 canary meet the S2.7 pre-registered criteria; re-execute flagged disagreement samples within the recorded budget. If the criteria fail, stop and report; current selectors remain.

### Task C.2: Feature contracts, one group per task

**Files:** create `tests/test_smart_routing_features.py` with adapter doubles, extended by each group.

- [ ] C.2a F01, F02, F15, F17: zero inference and zero secret access on startup, status, naming and diagnostics; explicit OFF survives re-onboarding; readiness is reported separately from enablement.
- [ ] C.2b F03, F11, F12, F16: one task graph; unchanged continuations reuse profile and child but get fresh admission; resource and funding gates bind the same job revision.
- [ ] C.2c F04–F07: API Usage stays opt-in; OpenRouter keeps its own funding; proposals gain no new permissions; the V1 worker qualification does not authorize V2 editing.
- [ ] C.2d F08, F09: Council runs only on explicit invocation and keeps its members, groups, independence and anonymity; project review stays scoped and read-only.
- [ ] C.2e F10, F13, F14: exports are sanitized and opt-in; review independence is kept; routing success grants no merge, push or cleanup authority.
- [ ] For each group: run `python3 -B -m unittest tests.test_smart_routing_features` (expected FAIL before C.3, PASS after).

### Task C.3: Replace callers and retire selectors

**Files:** modify `runtime/delegation.py`, `runtime/external_delegation.py`, `scripts/openrouter_proposals.py`, `scripts/openrouter_workers.py`, `policies/03-agent-delegation.md`, `docs/policy-sources.md`, `clients/instruction-map.json` and affected skill entry points.

- [ ] Route every native, external, API and OpenRouter selection through `prepare_assignment` (`scripts/routing.py prepare`). Keep endpoint, scope, permission, acceptance and explicit user controls as constraints.
- [ ] Stop legacy admissions, settle or reconcile outstanding legacy attempts, and keep historical costs and unresolved ownership.
- [ ] Retire `scripts/usage_routing.py` behavior that `scripts/routing.py` replaces. No compatibility alias and no second ledger writer.
- [ ] Run the feature contracts, the full `python3 -B -m unittest discover tests` and `python3 -B scripts/validate.py`; expected PASS.
- [ ] Run instruction-loading walkthroughs for Workstreams, API Usage, OpenRouter, Council and project review. Report them separately from package checks.

### Task C.4: Default ON

- [ ] Install with Smart Routing ON by default and explicit OFF honored; API Usage, OpenRouter, Jev and observability unchanged. Unready routes report enabled-but-blocked/unsupported, and ranker unavailability uses the deterministic path.
- [ ] Rollback is explicit OFF (worker choices through the same allocator) or the previous strategy-table version; ledger limits and claims are preserved.

### Task S4.1: Routing benchmark (former P0.9)

- [ ] Pre-register the routing-role benchmark (selection quality against the deterministic ranker, structured-output validity, high-risk handling, unsupported-option avoidance, latency and availability) and the sample strength, before seeing results.

### Task S4.2: Quality evaluation and promotion

- [ ] Re-execute the pre-registered, budgeted historical sample under the deterministic and model-ranked policies with fixed acceptance checks; measure accepted deliveries per budget, cost per accepted delivery including failures, review/repair burden and overhead.
- [ ] Promote the model ranker (A2) only if it meets the pre-registered margins; it keeps the A3 fallback, the A5 data rules and spec §5.3 router selection. Otherwise record the result and keep the deterministic ranker.
- [ ] Learned routing weights stay deferred (spec §11).

## Appendix C — Self-review

Author self-review of v1.7 on 2026-11-08 against the v1.6 requirements, spec v1.5 and the repo's development rules:

- Every v1.6 requirement is either referenced through section 2, kept in an Appendix B task or amended in spec v1.5 (A1–A13).
- Amendments that change agreed behavior are A1, A2, A6–A9, A11 and A12. The user accepted them on 2026-11-08, with A12 applying the same subscription and API route model to Claude and OpenAI. The visible acceptance changes are cases 7 and 8 (A9) and new cases 23–26. Spec anchors for `api_share` and cases 6, 9 and 16 are unchanged.
- A12 cites an April 2026 vendor announcement; P0.0 still confirms current terms and that key-bound requests draw on the allowance.
- This self-review is not independent approval, and it closes no P0 gate.
