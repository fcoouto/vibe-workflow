# Smart Routing (Vibe Router) Implementation Plan

> **9 November 2026:** activation is now the only approval for API Usage and OpenRouter, and Manual mode is retired. Where this document requires a separate approval, permission, approval line, endpoint opt-in or Manual selection, the [activation authority decision](../../policy-sources.md#activation-authority--9-november-2026) and the current contracts supersede it.

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
- [ ] For Claude and OpenAI alike, find the remaining subscription routes the vendor bills as included usage: the provider's own harness (Claude Code, Codex) launched directly, OpenCode's Claude Agent provider, and OpenCode's Sign in with ChatGPT. Record each as `included_subscription`, or as `purchased_usage` if it bills extra usage. Partly observed on 10 November 2026: OpenCode's Sign in with ChatGPT ran one request on `gpt-6-luna` with a null cost and a window settlement ([live report](../../smart-routing-qualification.md#chatgpt-attempts-5-and-6)). The vendor's billing class (usage page, pool shared with Codex) is not yet confirmed, so this item stays open.
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

**Contract (v1).** `scripts/routing.py <op>` reads one request on stdin. `scripts/routing.py --serve` reads one request per line and writes one response per line, for the OpenCode shim's persistent child. Both accept `--settings`, `--ledger` and `--table` paths. A request is `{"v":1,"op":…,"id"?,…}`; a valid `id` is echoed back. A response is `{"v":1,"state":…}`: either a result or `{"state":"blocked","reason":<fixed code>}`. Blocked is an outcome, so the exit code is 0; only a usage error exits 2. Malformed, oversized (1 MiB) or unknown input is blocked with no traceback, input text or secret in the output. Consumers ignore unknown response fields: S3.4 adds `authority` to `check` results without changing `v`.

| op | Request | Result |
|---|---|---|
| `prepare` | `assignment{attempt_id, task, scope, role?, choice?}`, `job`, `observations`, `constraints?`, `workflow?` (S3.5) | `admitted{duplicate, admission, ranking, profile, workflow_bound}` or `pending{reason, reasons}` |
| `check` | `attempt_id` and/or `session_id`, `authority?`, `request{provider, model, variant, harness, kind, funding_pool, bound?}` | `allow{attempt_id, route, pool, settings_revision, remaining, authority?}` or `unmanaged`; reads only |
| `dispatch` (S3.4) | `attempt_id`, `request` (as for `check`) | `dispatched{attempt_id, authority, session_id, message_id, route, pool, reconcile_hint}` once, else `already_dispatched{attempt_id, hold_state, session_id, message_id, reconcile_hint}` |
| `record` | `event{event_id, attempt_id, kind: dispatched / uncertain / not_dispatched / complete / observed (S3.6a), stage?, request_kind?, at?, usage?, root_attempt_id?, funding?}` | `recorded{event_id, duplicate, outcome?, settlement?}` |
| `settle` (S3.6a) | `root_task_id`, `workflow?{client, cwd, session_id, task_id}` | `settled{cost_to_date, by_stage, rework, unresolved, acceptance, accepted, ratio, ratio_num, ratio_den, …}`; reads only |
| `resolve_cost_unknown` (S3.6a, operator) | `attempt_id`, `cost_usd_micros`, `event_id`, `receipts?` + `source?: provider_receipt` | `recorded{event_id, duplicate, outcome, settlement}` |
| `resolve_overrun` (S3.6a, operator) | `attempt_id`, `decision: confirm / dismiss`, `event_id` | `recorded{event_id, duplicate, outcome, settlement}` |
| `reconcile_uncertain` (S3.6b-2, operator) | `attempt_id`, `evidence{kind: receipts, receipts, cost_usd_micros}` or `evidence{kind: charge_reservation}`, `event_id` | `recorded{event_id, duplicate, outcome, settlement}` |
| `resolve_block` (S3.6b-2, operator) | `organization`, `reason: over_bound` + `ids` + `decision: confirm / dismiss`, or `reason: legacy` + `decision: confirm` + `marker`; `event_id` | `recorded{event_id, duplicate, outcome: block_resolved, organization, reason, decision, resolved, charged, dropped, remaining_reasons}` |
| `status` | — | `ok{settings, settings_revision, effective_caps, project_pinned, ledger{…, authority_required, unstamped_open_holds, unbound_open_holds}}`; local files only |
| `set` | `expected_revision` + `change`, or `organization{id, enabled, policy?, expected_revision?}`, or `authority_required: true` (S3.4) | `updated{revision, settings, invalidated_attempts, ledger_state?}`, `updated{organization}` or `updated{authority_required}` |

Rules the command applies:

- **Roles.** `prepare` maps the job's profile role to a worker-choice role with `routing_settings.PROFILE_ROLES`. An explicit `researcher` or `repair` role is accepted; any other explicit role must match the mapping (`role_mismatch`). A coordinator is never routed (`coordinator_not_routed`).
- **Strategy table.** `prepare` ranks from the compiled table stored next to the settings (`strategy-table.json`, or `--table`). A missing, unreadable or malformed table falls back to worker choices or the explicit choice with `table_unavailable`; it never blocks. A table or ranking with a non-string strategy id or an unbounded version counts as malformed.
- **Clock.** The command uses its own clock. `prepare` refuses a snapshot whose `now` differs from it by more than 60 s (`observation_stale`) and stamps the admission with its own time.
- **Unsent admissions.** An unsent (`admitted`) attempt passes `check` only under the same settings revision and at an age of 0–900 s; exactly 900 s still passes. Otherwise it is blocked with `settings_revision_changed`, `admission_expired` or `clock_invalid`.
- **Dispatched attempts.** A dispatched attempt skips the revision and age checks, so turning Smart Routing OFF does not stop started subscription work.
- **Dollar-funded holds.** On every `check`, dispatched or not, a dollar-funded hold re-checks the following, blocking with `api_usage_disabled`, `organization_unavailable` or `funding_unverified`:
  - API Usage is still enabled (direct API routes);
  - its organization is enabled and not blocked;
  - the policy revision, balance freshness and cycle are still valid;
  - the reconciled credit still covers what is held.

  S3.1 records no consumption, so a dispatched hold's own spend is counted twice: once in the reservation and once in the lower observed balance. A long funded job therefore stops once its observed spend exceeds the unspent part of its reservation. S3.6a charges at completion only and keeps settled charges in R, so the double count with the observed balance remains until a watermark-carrying observation lets reflected charges be retired (deferred in S3.6b-2) and progress is recorded. While any organization hold is open, the collector must refresh the balance at least every 900 s (ideally about every 5 minutes) and must never timestamp an observation ahead of the command's clock. A policy edit (new organization revision) blocks all in-flight funded work.
- **Envelope.** S3.1 records no consumption, so the remaining envelope is the full reservation. A request `bound` larger than it is blocked with `envelope_exhausted`; an absent or empty bound passes while any amount is reserved.
- **Request kinds.** `check` covers model requests and subagent spawns (`primary`, `subagent`, `title`, `compaction`, `generate`). Tool, MCP and webfetch calls are governed by S3.3's policy deny rules; any other kind is blocked with `unsupported_kind`.
- **Smart Routing OFF.** `set smart_routing=false`, like any settings change, persists a new revision, and unsent admissions from the old revision fail `check` and `dispatch`. `set` writes the settings while holding the ledger's write lock, and `dispatch` reads the settings and its clock inside its own write transaction, so a change and a dispatch are totally ordered: `invalidated_attempts` lists exactly the attempts still unsent at the change, none of which can be dispatched afterwards, and a dispatch that took the lock first is not listed and keeps running (a dispatched hold skips the revision check). An absent, busy, corrupt or unsafe ledger never stops a settings change, because turning API Usage or Smart Routing off is a money stop: the settings are still written (the expected revision is still checked) and the result has `invalidated_attempts: null` and a `ledger_state` reason (`absent`, `ledger_busy`, `unsafe_ledger_path`, `ledger_unavailable` or another ledger code). Only the exact list is lost: `dispatch` re-reads the settings inside its own transaction and `check` compares revisions, so old admissions are still refused. Nothing is released until a `not_dispatched` record establishes non-dispatch, because `check` is not single-use.
- **Recording outcomes.** A bare `complete` on an uncertain attempt is refused with `reconciliation_required`; the operator settles the hold with `reconcile_uncertain` (S3.6b-2). Event ids, usage and settlement follow the [S3.6a contract](#task-s36-lineage-settlement-and-retention).
- **Busy ledger.** `check` and `status` wait at most 200 ms for a busy ledger and then answer `ledger_busy`. Writes wait at most 300 ms under `--serve` and 5 s in one-shot mode.
- **Caps.** Effective caps are the stricter of the settings and ledger caps, so `set` can only lower them. Raising them is an explicit ledger action (`Ledger.set_caps`).
- **Dispatch authority (S3.4).** A managed launch calls `dispatch` after `prepare` and before creating anything on the host. In one write transaction, reading the settings and the command clock inside it, `dispatch` re-runs every `check` gate on the unsent admission (settings revision, age, dollar funding, route identity, envelope) and then [S3.5's fresh gates](#task-s35-fresh-gates-at-dispatch-rf3), which only read; any refusal is blocked with that reason and leaves the hold `admitted` with nothing consumed. Otherwise the hold becomes `dispatched` and the response carries `authority`, a random 256-bit token (43 URL-safe characters). The ledger stores only its SHA-256, so the token appears in that one response and nowhere else: not in the ledger, `status`, any other response or a log. Every repeated or racing `dispatch` gets `already_dispatched` without a token, and a token whose response was lost cannot be recovered. A hold already `uncertain` also answers `already_dispatched`; a closed one is `attempt_closed`. No host call happens inside `dispatch`. `Ledger.convert_hold` accepts only a hold dispatched this way (never an admitted, bare-dispatched or uncertain one) and keeps its dispatch record.
- **Derived host ids (S3.4).** The launch must use the session and message ids derived from the ledger identity, the attempt id and the admission's nonce: `ses_` and `msg_` followed by 32 lowercase hex characters of a domain-separated SHA-256 (`runtime.routing.derive_ids`, read through `admission_ids`). `admit` stores a random 16-byte nonce (32 hex characters) in each admission record, so a duplicate `prepare` keeps the same ids, two ledgers on one server never share ids, and an attempt id reused after it ages out of the ledger (the last 1000 ids are refused outright) gets a new nonce and new ids. Admissions recorded before nonces derive with none. The nonce adds 43 bytes to a record; the longest admission in the capacity tests is 4,097 of the 6,656-byte cap (4,096 plus S3.5's 2,560-byte binding allowance). The ids are a conservative subset of the adapter's `SESSION` and `MESSAGE` patterns; the probe fixtures are sanitized, so the server's own id body format is not on record. OpenCode's own ids are time-ordered and these are not, so P0.7 re-qualification must confirm the server accepts them and that one prompt with a derived message id causes exactly one model request. The ledger keys the dispatch on them, and `dispatch` refuses ids another remembered attempt owns (`id_collision`). The host deduplicates them (P0.7), so re-sending the same prompt with the same ids after a lost host acknowledgment is the only retry: it needs the token and never a second `dispatch`.
- **Handshake (S3.4).** The launcher hands the token to the OpenCode shim only through the plugin's in-memory `routing.attach` for the derived session; the shim keeps it in memory, sends it as `authority` with `attempt_id` and the hook's `session_id` on every `check` for that session and its child sessions, and never logs, persists or echoes it. A hold dispatched with an authority allows only requests carrying its token (`authority_required`, `authority_invalid`); the allow result then adds `authority: "verified"`. A check that sends `session_id` or `authority` is strict: a hold without an issued authority (unsent, or dispatched by a bare `record dispatched`) is `dispatch_required`. The ledger flag `authority_required` is off by default and turned on only by the operator's explicit `set authority_required=true`; it can never be turned off. Each admission made while it is on is stamped with it and keeps that rule for its life, so in-flight holds keep the rule they were admitted under. For a stamped admission, a token-less attempt-only `check` and a bare `record dispatched` are `dispatch_required`; an unstamped one keeps the S3.1 rules, and one dispatched by a bare `record dispatched` stays open until `complete`. `set authority_required=true` is the only `set` form that creates a ledger. Pre-canary checklist: the shim forwards the token and session id (done in S3.3b, tested with a fake child and the real command on pinned temporary stores, not yet observed on OpenCode), `status` shows `authority_required: true`, and `status` shows `unstamped_open_holds: 0` (open holds whose admission predates the flag). S3.5 adds the [workflow-binding requirements](#task-s35-fresh-gates-at-dispatch-rf3): the command runs with `--project` (`status` shows `project_pinned: true`), and every planned job is prepared with `workflow` and an `attempt_id`, so `unbound_open_holds` counts only explicit delegation.
- **Unmanaged sessions (S3.4).** A `check` with only a `session_id` that no dispatched hold stores and no admission record of this ledger derives (never admitted here, or aged out of both tables, since deriving needs the record's nonce) answers `{"v":1,"state":"unmanaged"}`: interactive sessions stay unguarded, and the shim blocks this answer for a session it attached. Only session ids match: a message id sent as a session id is unmanaged. A known attempt whose hold or record is gone is `attempt_closed`. With both ids, a session belonging to another attempt is `session_mismatch`; an unknown one (a child session) is checked under the attempt. A missing ledger answers `ledger_absent` (fail closed), never `unmanaged`.
- **Lost acknowledgments (S3.4).** If the launcher cannot confirm the host launch, it records `uncertain`: exposure and the concurrency place stay counted, and every `check` of the attempt is blocked from then on. S3.4 accepts no proof of non-launch: `not_dispatched` is valid only for a hold that was never dispatched (`admitted`), so a dispatched or uncertain hold stays counted until completion or, for an uncertain dollar hold, the operator's `reconcile_uncertain` (S3.6b-2), which needs receipts or charges the full reservation. `reconcile_hint` names the host read (`GET` of the derived message) for the operator and is marked `authoritative: false`: a not-found answer can also come from a deleted or reverted message or another server or directory, and P0.7 is qualified only for the cases probed.
- **Test switch.** The `--test-inputs` command-line flag lets tests supply `now` to `check` and `dispatch`, an off-clock snapshot and a `table` to `prepare`. `record` takes no `now` (S3.4 removed it): a `record` request with one is `invalid_request`, with or without the flag. No request, configuration, table or environment variable can enable it. It is a usage error unless both `--settings` and `--ledger` are given and differ from the production defaults, and `status` reports `test_inputs`.

### Task S3.2: OpenCode adapter and client contract

**Files:** create `runtime/adapters/opencode.py`, `clients/opencode.md`, `tests/test_opencode_adapter.py`; modify `clients/index.md`, `clients/instruction-map.json` and the support targets in `docs/onboard-my-project-plan.md#client-scope-and-adapter-organization`.

- [ ] Write failing tests against recorded, sanitized OpenCode V2 hook and event payloads: normalization yields task, parent, stage, attempt, session/message IDs, requested model/effort and route identity; unknown fields stay unknown.
- [ ] Run, implement, re-run; expected PASS.

### Task S3.3: Pre-transmission shim

**Files:** create `runtime/adapters/opencode_plugin.ts`; extend `tests/test_opencode_adapter.py`.

- [ ] Use the hooks recorded in the [capability matrix](../../smart-routing-capability-matrix.md#opencode-enforcement-p01-p05p08): `model.request` for every model request, `tool.execute.before` for subagent, MCP and nested tool calls, `permission.evaluate` for `webfetch`, and `shell.create.before` to strip credentials from the command environment. Each forwards to `scripts/routing.py check` and blocks unless it returns an admission. A missing, failing or slow command blocks (fail closed). The shim contains no selection or funding logic.
- [x] Run one persistent child over stdio per plugin instance with a timeout of about 500 ms on every check; kill and respawn on a stall, and throw if the respawn fails.
- [ ] Register a `routing.health` plugin RPC. A managed launch calls it for the session's canonical directory (symlinks resolved) and refuses to send when it fails. Interactive sessions are not blocked by a plugin failure (user decision).
- [ ] Worker profiles are enforced only when policy rules deny `shell`, MCP tools, Code Mode `execute` and `webfetch` (or allow a narrow list). Profiles that keep full shell access run advisory-only, with no funding guarantee. Install the plugin globally; it enforces nothing outside the bounded canary project until C.4.
- [ ] Record route identity as alias + requested variant + catalog family; the resolved model is `unknown`.
- [x] S3.3b: the shim sends `attempt_id`, the hook's `session_id` and the `authority` token on every `check` for attached sessions and their children; `routing.attach` accepts only the attempt's own derived session, verifies the token and that ownership through the child before recording anything, and answers every refusal with `attach_refused`; `routing.detach` needs the same attempt and token; derived session IDs an open attempt owns are blocked until attached (expiring after 5 minutes), a finished attempt's session and the sessions recorded under it stay blocked, also after detach, and only attached roots strip shells or start lookups. The contract is in [clients/opencode.md](../../../clients/opencode.md#handshake).
- [x] S3.3c: `routing.descendants {session_id, attempt_id, authority}` returns the shim's recorded managed descendants of an attached root (IDs and parent links only, at most 512, `truncated`), with `complete: false` and a fixed `reason` when the shim cannot vouch for the set (no or a failed event stream, the root's creation not seen while attached, the descendant budget exceeded, an undetermined parent chain); the same authority rules as `routing.detach`, answering only while attached. `runtime/opencode_accounting.py` cross-checks it against the launcher's listing (`shim_descendants`, `cross_check_lineage`). The launcher order and contract are in [clients/opencode.md](../../../clients/opencode.md#plugin-rpc). Tested with a fake event stream and the real `routing.py --serve`; observed live on 9 November 2026 ([run](../../smart-routing-qualification.md#live-probe-run-descendants-and-s33d-harness-85ae5f4), 5 calls). Positive run: `complete: true` with a tool subagent, a `subagent: true` command child and an API-created child, equal to the launcher's listing; accounting with `shim_descendants` had no unknowns; refused after `detach`. Negative run (root created before `attach`): `complete: false`, `root_creation_unobserved`, accounting `shim_lineage_incomplete`. Not observed: `truncated`, the descendant budget, an API-funded attempt with real costs. Review of PR #152: every shim-recorded session must be included (S1); a repeated root creation event never vouches again (S3); only spend paths (`model.request`, subagent spawns) mark roots for an undetermined chain (S2, partial). Follow-up (not done): replace the sticky per-root `lineage_lookup_failed` flag with a bounded per-root set of undetermined sessions that decays and is cleared when a later lookup proves the session belongs to another tree.
- [x] S3.3d: for every non-interactive session (attached, pending or closed) the shim denies image generation (`experimental_image_generate`, a paid model call that never reaches `model.request`; permission action and tool) and every `edit`, `write` or `patch` whose resources include an OpenCode config path (`.opencode`, `.claude`, `.agents`, `opencode.json(c)` anywhere; `.config/opencode`; the global and system config roots; plus the `protected_paths` option, which only adds) or are missing or unreadable; `routing.health` lists `managed_denies`, and `probe` is `enforced` only with `image_generation` denied and `config_edit` denied or narrowed. Launchers never invoke project commands inside a managed session. Established by read-only inspection of the 2.2.6 binary and package-tested with a fake context; config-path writes and the command-shell environment were observed live (below), image generation was not. Before cutover, run the [required live probe](../../../clients/opencode.md#image-generation-and-configuration-edits-s33d) (image request denied with no `model.request`, model write to `.opencode/command/x.md` denied, whether a `` !`env` `` command under an attached root sees credential-like names stripped). Live probe on 9 November 2026 ([run](../../smart-routing-qualification.md#live-probe-run-descendants-and-s33d-harness-85ae5f4), 2 calls):
  - **Config edits, observed:** model `write`s to `.opencode/command/x.md` and `sub/opencode.json` were denied and the files stayed absent; a relative `agents/ok.txt` write was allowed.
  - **Shell environment, observed: not stripped.** A `` !`env` `` command under an attached root saw the dummy credential-like variable. That command shell never reached `shell.create.before`, so the rule that launchers never invoke project commands inside a managed session is load-bearing.
  - **Image request, unobserved:** there was no image model.
  Review of PR #154: edits resolve against the instance directory (`ctx.location.directory`), so `agent(s)`, `command(s)` and `mode(s)` directories are protected only inside config directories (fail closed when the directory is unknown); absolute paths are protected only through the config names, `.config/opencode` and the computed global and system config roots; an invalid `protected_paths` option (at most 32 entries of 16 segments) makes `routing.health` `not_ready` (`protected_paths_invalid`). Follow-up (not done): tighten `websearch` for managed sessions, which sends query text to a third-party provider like `webfetch`.
- [ ] Re-qualify P0.6 on the finished shim: model requests, subagent and nested spawns, tool and MCP calls, and `webfetch` are blocked before transmission, and a failed plugin load makes the health check fail. Record any unblockable path as unsupported in the capability matrix. Partly observed live on 9 November 2026 ([live report](../../smart-routing-qualification.md)): the handshake, blocked unattached and wrong-token sessions, `webfetch` evaluation, shell stripping and a command subagent child on another model; `subagent: true` commands skip `tool.execute.before` and are stopped only by lineage. Tool-call spawns, MCP, Code Mode and a failed plugin load were not re-run. The re-run also observed a finished attempt's session blocked (reason `attach_required` within the 5-minute pending window) and an interactive session untouched with the routing child killed. The live probes (9 November 2026, harness 85ae5f4) observed a tool-call subagent spawn on the allowed route: it passed `tool.execute.before`, and its child's request was checked and allowed. A spawn on another model through the tool was not attempted. A `` !`env` `` command shell under an attached root did not reach `shell.create.before`.

### Task S3.4: Single-use dispatch authority

**Files:** modify `runtime/usage_budget.py`, `runtime/routing.py`; create `tests/test_routing_dispatch.py`.

- [x] Write failing tests: 15 callers racing for one attempt produce at most one authority; distinct affordable attempts run concurrently; a lost acknowledgment leaves the claim `unknown` and is not retried without proven host deduplication or proof of non-launch.
- [x] Derive the session and message IDs from the attempt ID. The server deduplicates them (observed), but the `prompt` hook can run more than once for one message, so key the ledger on the message ID and recover an unknown attempt by reading the message by ID.
- [x] Run, implement, re-run; expected PASS (done). Re-qualify P0.7 on the finished path (not yet observed on OpenCode), including that one prompt with a derived message id causes exactly one model request, and that OpenCode's own session ids never have the derived shape (`ses_` + 32 lowercase hex), which the S3.3b shim assumes when it decides which sessions to ask the ledger about. Observed live on 9 November 2026 ([live report](../../smart-routing-qualification.md)): derived ids accepted, one request per derived message id, no request on a duplicate send (an extra `idle` record appears), read-back by id, OpenCode ids are 26 mixed-case characters, insertion-ordered listing; the re-run observed the lost-acknowledgment drill (hold stays counted, `record uncertain` keeps exposure; an uncertain subscription hold has no operator path). Racing launchers on the host and server restart were not run.

The contract (dispatch, derived ids, the shim handshake, `authority_required`, `unmanaged` and lost acknowledgments) is defined once in the [S3.1 contract](#task-s31-single-routing-command).

### Task S3.5: Fresh gates at dispatch (RF3)

**Files:** create `runtime/routing_gates.py`, `tests/test_routing_gates.py`; modify `runtime/routing.py`, `runtime/usage_budget.py`, `scripts/routing.py` and the read-only accessors `runtime/delegation.py` (`routing_view`, `routing_etag`), `runtime/state.py` (`ChangeWatch`), `runtime/workstreams.py` (`setting_marker`).

- [x] Write failing tests: reopening a dependency, changing writer ownership, losing a resource claim, disabling API Usage or changing the credential binding after `prepare` produces zero sends, even on a profile/ranking cache hit.
- [x] Run, implement, re-run; expected PASS (done on the package; not yet observed on the OpenCode host). This is the former P0.10 exit on the OpenCode host.

**Contract.** Smart Routing ON, OFF and explicit choices all use it.

- **Binding at `prepare`.** A `prepare` with `workflow{client, cwd, session_id, task_id, job_id, attempt_id?}` names the canonical workflow job (the root task, the planned job, and optionally the delegation assignment that already reserves its claims). The command reads that job before any ledger write and admits only a ready one: `job_not_found` and `binding_stale` (the request's job has a different semantic digest from the canonical one) are blocked; `job_not_ready`, `dependency_not_ready`, `ownership_conflict` and `resource_authority_unavailable` are pending and hold nothing. The admission record then stores `binding{v, job, credential}`. `job` holds the locator, role, job revision digest (plan digest, planned job and bound assignment), `semantic_digest`, a dependency snapshot (`count` and a digest of the accepted ids and revisions), the writer-claim digest and a resource-authority marker (bound assignment and the `resource_admission` preference, or null when neither applies). `credential` is a digest of the trusted key binding for a dollar-funded route, else null. The binding holds no prompt, brief or source. Locator fields are bounded (`cwd` 1,024, `session_id` 256, ids 128 characters). The serialized binding, with the largest credential marker, must fit 2,560 bytes; a longer one is blocked at `prepare` with `binding_too_large` (non-ASCII text serializes as six-byte escapes). Without `workflow`, `job` is null and only the funding, settings, route and credential gates apply. `prepare` reports `workflow_bound`, and `status` counts open holds without a job as `unbound_open_holds`.
- **Gates at `dispatch`.** After the S3.4 gates, `dispatch` checks the stored binding; no request field can supply or replace it (`gates`, `binding`, `workflow` or `job` in a `dispatch` request is `invalid_request`). Fixed reasons, first failure wins:
  - `binding_missing`: admitted before S3.5;
  - `credential_changed`: the trusted key binding was added, removed, disabled, rebound or reconfigured;
  - `binding_stale`: the job, task or bound assignment is gone or no longer open, or its role, revision or semantic digest changed (brief, scope, role or tools);
  - `dependency_reopened`: a dependency is no longer accepted or its accepted revision changed;
  - `ownership_changed`: a conflicting unresolved writer exists or the claim digest changed;
  - `resource_authority_lost`: the bound assignment settled or changed, or the resource preference changed;
  - `workflow_changed`: the state kept changing during the snapshot;
  - `gate_error`: a reader failure, malformed view or any exception.

  `api_usage_disabled`, `organization_unavailable` and `funding_unverified` keep their S3.1 checks. Any refusal leaves the hold `admitted`, writes nothing and sends nothing. A repeated `prepare` or a complete supplied profile (the cache hit) confers no authority: every dispatch runs every gate.
- **Snapshot and etag.** The workflow read never holds the ledger lock. Before the write transaction, `dispatch` reads one view (`delegation.routing_view`), reusing `validate_dependencies` and `_resources_available` with the same cooperative choice delegation's own admission makes. The database part is one read transaction. The reader keeps one `state.ChangeWatch` per locator: a separate read-only SQLite connection. Its etag is the file's (device, inode), that connection's `PRAGMA data_version` and the digest of the Workstreams setting file. `data_version` changes whenever another connection, in any process, commits, and never on a rollback. The etag is taken before and after the view's transaction; if they differ, the round counts as `workflow_changed`. Inside the ledger transaction only the etag is re-read (one pragma, 100 ms busy limit). A different or busy etag aborts the round with nothing written. Dispatch retries up to three rounds, then refuses with `workflow_changed`.

  Guaranteed: no commit to the delegation database or change to the setting file lands between the view and the in-transaction etag read. Nothing in this path opens and closes a raw descriptor on the database. On POSIX, closing any descriptor of a file drops every lock the process holds on it. Only SQLite's own close keeps the descriptor open while other connections hold locks. So the view's read lock, and any write transaction this process already holds, survive view and etag reads (regression tests). Not guaranteed: no commit to the workflow store between the in-transaction etag read and the ledger commit (verdict, remaining gates and the ledger write with its fsync), because the two stores commit separately. The gates are cooperative, like delegation admission itself.
- **Credential marker.** The trusted key binding is the existing Usage Routing record in the ledger (`routing` entries holding an opaque `credential_ref`), matched by the admission's `credential_ref`. Routes whose reference has no such record are checked only for staying unbound.
- **C.3 wiring and pre-canary requirements.** S3.5 adds the gates; nothing calls them with a job until C.3, so RF3 is not yet enforced in production. `workflow` is the caller's choice: omitting it skips every workflow gate. Before the canary:
  - every planned (Workstreams) job is prepared with `workflow`;
  - its `cwd` is tied to the service's own project by running the command with `--project <checkout root>`. With the pin, the reader compares the checkout root the locator actually resolves to (realpath) with the realpath of `--project`, at `prepare` and again at every `dispatch`, before any workflow read. Any other checkout is blocked with `project_mismatch`, including a nested, separately enrolled checkout under the pinned root and a `cwd` symlink swapped after `prepare`. Paths compare exactly, so on a case-insensitive filesystem a differently cased spelling of the pinned root is also refused (fails closed). Without the pin, request input can name any enrolled checkout of the same user. That read returns only digests and reason codes, which is acceptable before C.3. C.3 must make `--project` mandatory for production;
  - the binding is made after delegation's own `prepare_assignment`, with its `attempt_id`, so the binding covers the reserved claims and the brief.

  The coordinator profiles exactly `delegation.routing_job(scope, locator)`, the canonical semantic job (role, scope reference, planned claims, plus the bound assignment's objective and context), adding only a supplied `profile`. Without `attempt_id`, the canonical job has no objective, context, tools or requirement floors. The context-token estimate then falls back to the supplied profile, explicit floors are not bound, and brief or tool changes are detectable only through a bound assignment.
- **Capacity.** `MAX_ADMISSION_BYTES` is 6,656 (4,096 plus `MAX_BINDING_BYTES`). The worst-case ledger in the capacity tests is 10.53M of the 12M hard limit (87.8%, within the 90% margin). Python 3.11+ as for the rest of the runtime; 3.9 cannot import it.

### Task S3.6: Lineage, settlement and retention

**Files:** modify `runtime/routing.py`, `runtime/usage_budget.py`, `scripts/routing.py`, `runtime/routing_gates.py` and the read-only accessor `runtime/delegation.py` (`routing_acceptances`, over delegation's evidence-backed reconciliation records); create `tests/test_routing_accounting.py` and (S3.6b-2) `tests/test_routing_reconcile.py`.

- [x] Write failing tests (S3.6a):
  - Duplicate child receipts charge once.
  - One accepted task at 100 plus one rejected at 90 reports 190 per accepted delivery.
  - Reviewer rescue is recorded as incremental rework.
  - Ambiguous timeouts keep their exposure.
  - Expired diagnostic evidence cannot influence a decision, while active caps, unknown claims and ownership survive retention (spec §11 defaults).
  - Observability OFF exports nothing and loses no local accounting.
- [x] S3.6a (accounting core, package only): run, implement, re-run; PASS in `tests/test_routing_accounting.py`. Not observed on the OpenCode host.
- [ ] S3.6b: account from message and session token totals, not from usage events (not durable). Title requests create no message, so their tokens are the session total minus the sum of its messages. Detect event gaps with `durable.seq` and re-read session and message state to resync. Reconcile uncertain and `cost_unknown` holds from evidence, over-bound S1 calls and legacy blocks (the only way to lift those reasons), and settled charges against observed balances (spec §8: remove an unreflected debit only with evidence that the balance reflects it).
  - [x] S3.6b-1 (host accounting, package only): `runtime/opencode_accounting.py`, pure, over the adapter's normalized messages and events; PASS in `tests/test_opencode_accounting.py`. It builds the S3.6a `usage` object from message tokens (one `primary` or `compaction` request per message with its id as receipt, grouped by route above 32), adds a `title` request for the session-minus-messages remainder (unknown when negative, incomplete or inconsistent), keeps subscription costs `null` (a provider's token-price estimate is not proof of out-of-pocket spend), reports API costs as `estimate` (OpenCode prices locally and reports 0 for unpriced models) and nulls every API cost when anything is unknown, including a zero cost for a request that used tokens, an unfinished message, an unaccounted child session or more than 32 routes; accounts spawned child sessions passed with the root; tracks `durable.seq` per aggregate (`gap`, `out_of_order`, `seq_conflict`, `reset`, `version_changed`, `joined_late`), recomputes totals from re-read state with deltas (never accumulated), and passes `native.rateLimit` through as an observation with units. Contract and wiring: [OpenCode client](../../../clients/opencode.md#usage-accounting-and-resync). Not wired to the ledger or a launcher; not observed on the OpenCode host. Verified on three recorded sessions (one non-zero title remainder) and through the S3.6a `record complete` path in-process; no `generate` request, paging, deletion or real sequence gap is in the fixtures.
  - [x] S3.6b-2 (operator reconciliation, package only): `reconcile_uncertain` and `resolve_block`, PASS in `tests/test_routing_reconcile.py`; retiring reflected settled charges is deferred until an observation carries a watermark. Not observed on the OpenCode host.
- [ ] Run, implement, re-run; expected PASS. Re-qualify P0.8 on the finished path. Blocked on 9 November 2026 ([live report](../../smart-routing-qualification.md)): an attempt whose session ran a `subagent: true` command cannot be accounted (child linked only by `parentID`; its `synthetic` result makes the title unknown). After #146 the re-run accounted it, settled the subscription hold and left `status` clean; retries, `generate`, compaction and API-funded holds remain unobserved.

**Contract (S3.6a).** Package-only; no model call, no OpenCode server. Responses stay `v:1` and clients ignore unknown fields.

- **Lifecycle events.** `record` accepts `event_id` (opaque, at most 128 characters) and remembers it. The same id with the same payload (canonical SHA-256 of the event without the client's `at`, so a retry with a new timestamp is a duplicate) returns the original outcome with `duplicate: true` and changes nothing; a different payload under a remembered id is `event_conflict`. A refused event is not remembered, so it can be retried once it applies. 15 racing processes with one event apply it once: the lookup, the hold transition, the settlement, the root aggregate and the event row commit in one ledger write transaction. A new id for an effect that already happened answers `duplicate: true` as in S3.1; a closed attempt refuses every new event (`attempt_closed`). Optional fields: `stage` (`coordinator`, `profiling`, `routing`, `worker`, `review`, `rescue`, `repair`, `check`, `escalation`, `auxiliary`; default from the admitted role), `request_kind` (a `check` kind), `at` (the client's time, informational; retention uses the command's clock), `usage` (on `complete` and `observed`). `observed` records usage with no ledger transition and no charge (for example the coordinator's own requests); it needs `stage`, `usage` and `authority`, optionally `funding` (`api` or `subscription`; only `api` usage counts as dollars), and joins the root of an OPEN ledger attempt: its own `attempt_id`'s hold, or the hold named by `root_attempt_id`. That anchor must exist (`unknown_attempt`), be open (`attempt_closed`), have been dispatched with an authority (`dispatch_required`) and `authority` must be its token (`authority_invalid`); different roots are `root_mismatch`. These checks run before the event-id lookup, so a replay with a wrong token or a closed anchor gets that answer, never `duplicate: true`. So it can never be aimed at a closed attempt or another session's root. The token is checked against the stored hash and never stored, logged or included in the event digest. Any other field, including a self-reported acceptance, is `invalid_request`. The stored `LifecycleEvent` holds ids, labels, integer totals and the outcome, never a prompt, body, transcript, model output or credential, at most 16K characters; the table keeps at most 4,096 rows, dropping the oldest except transitions of still-open holds (at most three per hold).
- **Usage.** `{source: host_message_totals | provider_receipt | estimate, requests: [{kind, model, variant?, tokens{input, output, reasoning, cache_read, cache_write}, cost_usd_micros?, units?{win:<pool>/<window>: n}, receipt?}]}`: 1 to 32 requests, integers within the micro-unit bound, at most eight distinct window keys, receipts at most 128 characters. Model names are validated, not stored. Money: a dollar hold is charged its full measured cost; a receipt repeated within the same report counts once, at its highest cost (an unknown cost wins), and receipts seen elsewhere (an `observed` report, a sibling attempt's per-session message id) never reduce a charge. Report: `settle` counts each receipt once per attempt (an attempt reporting it in an `observed` event and again at completion); the same id from another attempt is that attempt's own request. USD, tokens and window units are never added or compared across units.
- **Completion settlement.** Only `complete` on a `dispatched` (or `cost_unknown`) hold settles; before dispatch it is `hold_state_forbidden`, on an `uncertain` hold `reconciliation_required`. A window-only (subscription) hold releases its claims; its usage units are recorded as an observation (`settlement{kind: window, released, observed_units}`) and window headroom keeps coming from telemetry, never from local arithmetic; a subscription token price is never charged as dollars. A dollar hold charges the cost to the organization's settled total, its cycle and its task, sets its remaining reservation to zero and releases the rest, once (`settlement{kind: usd_micros, reserved, charged, released, overrun, remaining, cost, cost_source}`, with `charged - overrun + released = reserved`). R keeps the charge, so the spec §7.4 example holds: $30 available, holds of $6, $12 and $9.60, the first finishing at $4 releases $2 and leaves $4.40. An `estimate` is labelled and charged at no less than the admitted (unpadded) estimate, so it releases at most the padding; without a readable estimate it releases nothing. A charge above the reservation is `outcome: overrun`: the pre-send guard failed or was bypassed, and `record` has no caller authentication, so the report is not trusted beyond what was certain. The attempt closes; the full reservation is charged (`charged = reserved`, certain); the excess is kept on the hold as `overrun` with `overrun_state: unconfirmed`, outside R and every settled, cycle, pilot and task total, so one inflated report (even 2^53-1) cannot inflate the books. Exact headroom effect: while any unconfirmed excess is pending on an organization, its credit headroom and admission headroom read 0, i.e. the excess lowers headroom by at most the headroom that remains and never below zero credit; `_credit_raw` and R are unchanged. The pending excess blocks the organization through its derived `overrun` reason (below); nothing is topped up; other organizations are unaffected. The operator op `resolve_overrun{attempt_id, decision, event_id}` (never reachable through `record`, idempotent by event id, `overrun_not_pending` otherwise; concurrent operators get one effect) either `confirm`s the excess, charging it as settled (`charged = reserved + overrun`), or `dismiss`es it. It changes only that hold's `overrun_state` (plus the charge on confirm). Closed holds with an unconfirmed overrun are never pruned. When the hold table is full and at least one pending overrun occupies it (even if open holds fill the rest), every new admission is refused with `overrun_backlog`: any organization's and subscription admissions alike, until overruns are resolved or holds close; `status` shows `overrun_backlog: true`.
- **Admission block (derived).** Whether an organization is admission-blocked, and why, is never stored. `Ledger.block_reasons(data, organization)` derives it from its sources: `overrun` (closed holds with an unconfirmed excess), `over_bound` (S1 run calls whose reported cost exceeded their maximum: `complete` marks the call `over_bound=<actual>`, leaves it `unresolved`, still answers `cost_exceeds_bound`, and refuses every later `complete` of that call with `over_bound_unresolved`, so a lower report cannot settle it) and `legacy` (`legacy_block`, set only by migration). Admission, conversion, top-ups, S1 dispatch, every dollar `check` and Usage Routing's setup reuse require the set to be empty, else `manual_recovery_required` (unchanged for callers). No code path writes or clears a block flag: each resolution changes only its own source record, so resolving one reason can never lift another, in any order of events. `over_bound` and `legacy` have no operator op in S3.6a (fail closed): an organization with an over-bound S1 call or a legacy block stays blocked until the S3.6b-2 `resolve_block`. `status` lists `blocked_organizations` with their reasons and the pending-overrun attempt and over-bound run ids (16 per reason, plus `more`), the `cost_unknown` attempt ids, the `uncertain_dollar` attempt ids (uncertain dollar holds: `complete` is `reconciliation_required` and `resolve_cost_unknown` refuses them, so only the operator's S3.6b-2 `reconcile_uncertain` settles them) and `overrun_backlog`.
- **No release without settlement.** `close_attempt('complete')` refuses a dollar hold (`settlement_required`): a dispatched dollar hold settles only through the cost path (measured cost, reservation floor or operator resolution). Subscription holds are unchanged. A missing usage or any counted request without `cost_usd_micros` is `outcome: cost_unknown, cost: null`: the hold stays open in state `cost_unknown` with its full exposure and concurrency place, `check` refuses it, and a later `complete` with a known cost settles it. An operator can settle it with `resolve_cost_unknown{attempt_id, cost_usd_micros, event_id, receipts?, source?}`: only an open `cost_unknown` hold (else `hold_state_forbidden`; a closed one is `attempt_closed`, so a late `complete` and a resolution never both charge: the first wins). By default the full reservation is charged and the supplied cost is only recorded; the padding is released only for a receipt-backed cost (1 to 32 `receipts` with `source: provider_receipt`, which come together or not at all), charged as supplied. A cost above the reservation is an overrun as above. It goes through the same settlement code, which stores the audit `resolution{basis: receipts | reservation_floor, receipts: n, receipts_digest}` (the first 32 hex characters of the SHA-256 of the sorted receipt ids, or null) instead of a cost source, is idempotent by event id and frees the concurrency place. `record` cannot send it. `close_attempt` (no settlement) keeps refusing a `cost_unknown` hold.
- **Settled charges in R.** `Ledger._total` adds settled charges: in total for credit and pilot limits, per cycle for cycle caps, per task for task limits. Like S1 run costs they stay until reflected ones can be removed with evidence (deferred in S3.6b-2). Bounds: 16 cycle entries per organization (only the current cycle feeds a cap; an older entry folds into the total), 512 task entries (oldest first, never a task with an open hold, so an evicted old task's limit stops counting its earlier charges; an accepted trade-off).
- **State version.** Settled charges raise the ledger to version 4 (3 -> 4 changes no claim, hold or admission), so code that would ignore them refuses the ledger (`ledger_identity_changed`) instead of resetting caps and credit. The migration backfills the accounting root of holds admitted before v4 from their admission record (else their task id), so in-flight holds keep a stable root. It also removes the stored `blocked` flag: `blocked: true` becomes `legacy_block: true` (still blocking), `blocked: false` disappears. v3 left an over-bound S1 call `unresolved` without a marker, indistinguishable from an uncertain one, so every unresolved call of an organization with `blocked: true` is marked `over_bound` at its maximum (fail closed: a later lower report cannot settle it and the books keep the maximum); unresolved calls of unblocked organizations stay ordinary uncertain calls. These 3 -> 4 steps run once per migrated ledger, for every hold and run regardless of organizations. v4 was never released, so this is its only definition.
- **Root settlement.** Roots are keyed by a digest of the bound workflow job's full `(client, cwd, session_id, task_id)` locator, fixed on the hold at admission, so the same task id in another project or session never shares an aggregate and pruning an admission record never moves an open hold's root. An attempt with no workflow job is keyed by its task id alone and settled without `workflow` (acceptance `unavailable`); a service pinned with `--project` refuses `settle` without `workflow` (`project_required`), because it cannot check such a root against the project. Money paths keep using (organization, task). `settle` (read-only, one ledger read transaction) aggregates every attempt under the root, plus `observed` events. Every attempt counts once, including failed, abandoned and never-dispatched ones; cost to date is per unit (`usd_micros` with its `usd_micros_estimated` share, `tokens`, `units`), by stage, with reviewer rescue (`stage: rescue`) reported as `rework` and `executor_quality: not_scored`. Open holds of the root (admitted, dispatched, uncertain, `cost_unknown`) are `unresolved{attempts, usd_micros_held, units_held, unknown_cost_attempts}`, never cost. Acceptance comes only from the trusted reader for `workflow` (`delegation.routing_acceptances`: jobs that delegation's strict `validate_dependencies` accepts, so settled, completed, reconciled with retained evidence and with no reopened prerequisite); a worker or reviewer report never counts. `ratio` is the ceiling of each unit's cost divided by the number of accepted jobs (one accepted at 100 and one rejected at 90 give 190), with the exact fraction as `ratio_num` (cost per unit) and `ratio_den` (acceptances); all three are `null` with zero acceptances, and an unreadable reader or no `workflow` gives `acceptance: unavailable`. Per-root aggregates hold at most 1,024 attempts, 256 receipts and 16 unit keys (96K characters); at most 1,024 roots are kept, never one with an open hold.
- **Retention.** `Ledger.expire_evidence(now)` returns `{removed, retained_safety_records}` and is idempotent. Events: 14 days unless they changed a still-open hold. Root aggregates: 60 days unless the root has an open hold. Admission records of closed attempts: 14 days, and their quota snapshot detail (`pressure`, `snapshot_id`) 24 hours. Balance observations older than the 900 s freshness rule never fund (well inside 24 hours). The routing store keeps no task-feature table, so the 30-day default has nothing to apply to yet. Never removed by age: open, uncertain and `cost_unknown` holds, settled charges and cycle caps (a still-active cap is never reset), seen attempt ids, runs, native tasks and the organizations. Every read filters the same way (event lookup, root summary, the admission rows `prepare` uses for class deficits), so expired evidence cannot influence a decision before cleanup runs. Cleanup runs on a write command's first ledger use and at most hourly while serving; a busy cleanup never blocks the request. Nothing is exported: observability being OFF leaves local accounting complete.
- **Capacity.** The worst-case ledger record is 10.67M of the 12M hard limit (88.9%; 352 bytes of settlement per hold); S3.6b-2 adds the `over_bound` marker S3.6a's worst case omitted (at most one per run): 10,698,684 (89.16%). The margin to the 90% test ceiling is thin: do not add per-hold or per-entry fields without recomputing the worst case with every settlement field at its bound. Events and root aggregates live in two further tables of the same SQLite file, bounded by row count and row size.
- **Pre-canary.** S3.6b lands before managed clients, or every `cost_unknown` hold is resolved (`status` shows none). Resolve every unconfirmed overrun (`resolve_overrun`), every `cost_unknown` hold (`resolve_cost_unknown`), every uncertain dollar hold (`reconcile_uncertain`) and every over-bound S1 call and legacy block (`resolve_block`) before the canary: `status` shows `uncertain_dollar` and `cost_unknown` empty, no `overrun` reason, `blocked_organizations` empty and `reconcile_hint: null`. Settled charges double count against observed balances until a watermark-carrying observation exists; headroom errs low. A managed client must send `usage` with `cost_usd_micros` on `complete` for every dollar-funded attempt (else the hold stays `cost_unknown` and keeps its concurrency place), a stable `event_id` per lifecycle event, and `receipt` ids where the host has them; settlement figures are package-tested only until S3.6b and P0.8 re-qualification. OpenCode accounting on its own trusts the launcher's `children` and `lineage` (a child left out of both, or lineage that lies about a parent, settles complete with that child's spend uncounted). S3.3c adds the shim's `routing.descendants` RPC and the `shim_descendants` cross-check, which flags a shim-recorded child the launcher missed (`child_session_unaccounted`), an incomplete shim set (`shim_lineage_incomplete`) or a missing one (`shim_lineage_not_checked`), each making the API cost unknown. API-funded managed attempts are qualified only when the launcher uses the shim cross-check AND it has been observed live with the [required positive and negative runs](../../../clients/opencode.md#plugin-rpc) (not yet observed). Subscription routes are not affected in dollars.

**Contract (S3.6b-2).** Package-only; no model, OpenCode, network or credential access. Responses stay `v:1`. Two operator ops, each a dedicated command op that `record` can never reach (their kinds and fields are refused there). Every operator op (these two and S3.6a's `resolve_cost_unknown` and `resolve_overrun`) is one-shot only: `--serve`, the shim's persistent child, refuses them with `operator_op_not_served` before reading anything, so a plugin or worker that can reach that child can never settle, release or unblock. Each is idempotent by `event_id` in the S3.6a event table: the same payload returns the original outcome with `duplicate: true`, another payload is `event_conflict`, refusals are not remembered, and 15 racing processes apply one effect. The event row is the audit (ids, labels, integer totals, a receipt digest; never receipt ids, tokens or content). Each op changes only its own source record.

- **`reconcile_uncertain{attempt_id, evidence, event_id}`.** Only an open `uncertain` dollar hold (else `hold_state_forbidden`; a closed one is `attempt_closed`, an unknown one `unknown_attempt`). `evidence{kind: receipts, receipts, cost_usd_micros}` (1 to 32 distinct receipt ids of at most 128 printable characters, an integer cost of at least 1 micro-USD within the micro-unit bound) settles it like a measured completion through the S3.6a settlement code: charged as reported and the unspent rest of the reservation released on the operator's word, a cost above the reservation an `overrun` awaiting `resolve_overrun`; `resolution{basis: receipts, receipts, receipts_digest}`. `evidence{kind: charge_reservation}` charges the full reservation and releases nothing (`basis: reservation_floor`, `cost: null`). Any other kind is `evidence_unsupported`, any other shape `invalid_request`. A client's `complete` on an uncertain hold stays `reconciliation_required` (S3.6a), so the hold keeps its full exposure until the operator acts; afterwards every event and `check` of the attempt is `attempt_closed`, so the first resolution wins and nothing is charged twice. Receipts are the operator's word, not verified offline, so the release they allow is only as good as that word: the operator stops the worker first, and a request still in flight when the hold closes is covered only if its receipt is listed. A zero cost is `evidence_invalid` (it would release the whole hold without spend, which is the refused no-spend proof); the same rule now applies to a receipt-backed `resolve_cost_unknown` (without receipts a zero still charges the reservation floor).
- **No release without spend.** A `no_spend_proof` (a zero-cost provider usage report covering the attempt) is refused, because no offline shape is safe: nothing local can authenticate an operator-typed report or its digest; the ledger stores no time for the uncertain mark, and a bare `record dispatched` none for the dispatch; a worker can still spend after the mark, so a window ending there proves nothing, and a report observed now cannot cover now; and an `observed_at` after the window's end does not prove the report reflects the window (provider reporting lag, spec §8). A safe proof needs a trusted collector recording the provider's report with a provider-declared completeness watermark at or after the hold's close, in a two-phase close-then-prove protocol: future work.
- **`resolve_block{organization, reason, decision, event_id, ids? | marker?}`.** `over_bound` takes `ids` (1 to 16 distinct run ids, as `status` lists them); every id must be an over-bound run of the organization, else `block_not_pending` and nothing changes. `confirm` sets each call's cost to the reported amount (the excess enters R), `dismiss` keeps the maximum the books already held; the call completes and its run closes, since its pre-send bound failed. `legacy` takes `decision: confirm` and `marker` (`lb_` plus 32 hex): a digest of the ledger identity, the organization, its policy revision and its unresolved items (open holds, unconfirmed overruns, open S1 calls reserved, in flight, unresolved or over-bound), shown only by `status`. Any change to those items makes it `marker_stale`, so a block is never cleared blind or against a listing that moved. Overruns keep `resolve_overrun`; any other reason is `invalid_request`; an unknown organization `organization_unconfigured`. The outcome reports `charged`, `dropped`, `resolved{ids, count}` and `remaining_reasons`. The block stays derived: the organization is admissible only once that set is empty.
- **Capacity.** Neither op adds a field to the ledger record: `resolve_block` removes `over_bound` or `legacy_block` and sets an integer cost the worst case already counts, and settlements reuse the S3.6a `resolution` labels. The worst case in `CapacityTests` now also carries the `over_bound` marker that S3.6a's construction omitted, on one call per run (an over-bound count call never gets a generation call): 10,698,684 of the 12M hard limit (89.16%) against the 90% test ceiling of 10,800,000. Avoid further per-hold or per-call fields without recomputing it.
- **Status.** `ledger.reconcile{reconcile_uncertain{count, ids, more}, resolve_block{over_bound{organizations[{organization, count, ids, more}], more}, legacy{organizations[{organization, marker, unresolved{count, ids, more}}], more}}}` (16 ids and 16 organizations per list) and a fixed `reconcile_hint` text, null when nothing is pending.
- **Reflected debits (deferred).** Spec §8 removes a settled charge only with evidence that the observed balance reflects it. No observation shape carries that evidence: `Ledger.observe`, Usage Routing's `observe` (`balance`, `observed_at`, `source`, `pool`, `expiry`) and `usage_admission.Grant` have no usage cutoff, pool-window epoch or source watermark, and the ledger only ever lowers a balance. So no settled charge is retired, and a newer `observed_at` retires nothing (tested). To enable it, the provider adapter must supply, with each balance observation from a qualified source, a provider-declared `reflects_through` (the time through which all billed usage is in the balance) bound to the organization and the pool or window epoch. The ledger would also need a settlement time per charge (today charges are aggregated per total, cycle and task) and must recompute the capacity worst case for it; retirement would then run once per charge with an audit event and never touch open, uncertain or `cost_unknown` exposure.

### Task S3.6c: Subscription hold reconcile (follow-up, required before C and before any production use)

An uncertain subscription (window) hold has no operator recovery path today: `reconcile_uncertain` covers dollar holds only (`hold_state_forbidden`, observed in the live re-qualification). Such a hold therefore keeps its concurrency place for good.

- [ ] Define and implement an operator path that settles an uncertain subscription hold, releasing its claims with the observation recorded and never charging dollars. It must be idempotent by event id and one-shot only, like the other operator ops. It is required before cutover (Task C) and before any production use of enforced routing; until then the pilot runs on a dedicated canary ledger.

### Task S3.7: Canary

- [ ] Enable enforced routing for one explicitly chosen project on OpenCode within the S2.7 canary bounds; record admissions, blocks, latency and settlement against the S2.7 criteria, and abort on any bound or abort condition. Other projects and clients continue on current selectors.
- [ ] Bounded pilot (approved 9 November 2026, pre-registered in [validation](../../smart-routing-validation.md#pilot-canary-bounded-not-the-pre-registered-canary)): up to 20 subscription-only `claude-agent/haiku#low` admissions in the allowlisted project, with a durable cap of 24 requests and the Smart Routing OFF kill switch on abort. It runs through `tests/qualification/opencode_live.py --canary` and does not satisfy this task; cutover stays blocked.

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
