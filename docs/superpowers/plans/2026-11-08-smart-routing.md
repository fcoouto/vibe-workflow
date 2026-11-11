# Smart Routing (Vibe Router) Implementation Plan

> **9 November 2026:** activation is now the only approval for API Usage and OpenRouter, and Manual mode is retired. Where this document requires a separate approval, permission, approval line, endpoint opt-in or Manual selection, the [activation authority decision](../../policy-sources.md#activation-authority--9-november-2026) and the current contracts supersede it.

> **Latest release scope, 10 November 2026:** the user explicitly chose no API Usage in this release and requires it in the next. Smart Routing must profile tasks and automatically select and launch across available OpenCode subscription connections (ChatGPT/Codex, Claude and OpenCode's own subscription). Codex and Claude Code clients select native models only. The [redesign release amendment](2026-11-10-smart-routing-redesign.md#release-scope-amendment-connected-subscriptions-and-native-clients) and F4.5/F4.6 own this scope. The earlier permission to leave Codex-native unqualified applies to the scheduled OpenCode canary, not omission of native routing from the release. Subscription window equations remain active; API allowance equations remain preserved for next release. This supersedes broader historical release wording below without changing recorded results.

> **Approved simplifications, later on 10 November 2026:** defer offline gateway decision/replay work with production OpenRouter; use one subscription shortlist; measure replay size before raising ledger bounds; simplify canary approval to pinned paths/table inputs while preserving request-specific stale-and-needed refresh and exact admitted identity; use focused task checks and one final full-suite gate where required; use one independent F2 reviewer with complete coverage. Historical replay, strict replay and registered canary requirements remain. The user subsequently approved all pending redesign choices, including call 36 and Edit 7, and requested merge after blocker resolution and independent review, followed immediately by orchestrated implementation. Formal Edit 7 registration and run-specific prerequisites remain; no additional live-call budget or paid overflow is authorized.

> **Expanded-client evidence correction, 10 November 2026:** the redesign's F4.2 owns harness-specific catalog/effort publication, and F4.6 owns the native command bridge and a bounded Claude usage reader shared with OpenCode. The installed offline initialization protocol has been checked; authenticated freshness, scoped windows, demand and Go's included-only billing boundary remain open. Historical seed-only behavior below is the current baseline, not the intended routine flow. F4's source gates must close before the affected support is claimed; this note does not qualify a path or authorize a live run.

> **Bounded metadata checks, 11 November 2026:** the user approved initial read-only client metadata checks and passive quota collection around one already-intended native/Go task per path, with no extra model calls or configuration changes. Codex and Go returned usage windows; Claude returned no quota data. Go's paid-overflow state and native/Go demand remain unproven. See the redesign's current evidence record; earlier statements that passive collection lacks authority are superseded only within these explicit bounds.

> **10 November 2026:** the [Smart Routing redesign](../specs/2026-11-10-smart-routing-redesign-design.md), approved by the user, replaces parts of this plan, and its tasks are in the [redesign plan](2026-11-10-smart-routing-redesign.md) (slices F1 to F8 and L1 to L4). Jev and the control roles are removed, so every mention of Jev below is history. The role lists become one specialist roster. Decision 5's per-model route manifest becomes path rules with generated routes. Slice S4 becomes the learning slices L1 to L4. This plan stays the record of the merged slices, the [pre-canary requirements](#pre-canary-requirements) and the cutover tasks.

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
| **S4 — Optional model ranking** | Routing benchmark, quality evaluation, model-ranker promotion. | Ships only if it beats the deterministic ranker. Replaced on 10 November 2026 by slices L1 to L4 of the [redesign plan](2026-11-10-smart-routing-redesign.md#after-the-canary-l1-to-l4). |
| **F1–F8 — Redesign foundation** | Task Profile template and specialist roster, Jev removal, path rules and generated routes, value order and funding order, no model names, the strict and historical replay runners ([redesign plan](2026-11-10-smart-routing-redesign.md)). | Required before the canary: the canary runs ranked on generated routes. |

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

**Status lines.** On 10 November 2026 each task got a status line from a check of this plan against the merged PRs, the tests on `main` and the recorded live runs. A box is ticked only when its work is merged and the proof the box names has been seen: tests on `main` for package work, a recorded live run where the box asks for host or provider observation. The states are:

- **Done:** ticked.
- **Package only:** merged and tested, but the live observation the box asks for has not happened. Left unticked. A box that does not ask for live observation is ticked on package evidence, even when its text says "package only".
- **Partial:** the status line names what is missing.
- **Not started.**
- **Changed:** the decision moved; the status line cites where.
- **Historical scheduling record:** on 9 November 2026 these items were recorded as deferred: canary, cutover, billing pages, plan tier and API Usage. The original API setup-versus-feature wording was not recovered. The user's explicit 10 November decision now resolves API feature scope: excluded this release, required next release. Live scheduling and budgets remain separate.

A "write failing tests" or "expected: the new tests fail" step is ticked with its task: on the recorded test-first commits where the PR shows them, otherwise on the passing tests on `main` alone.

Open work that a status line names is tracked on that task's box. Open work from PR descriptions and reviews that no box asks for is listed once under [Follow-ups from merged work](#follow-ups-from-merged-work).

### Task 0.1: Reconcile the baseline

**Status (10 November 2026):** Done (#112): the [feasibility record](../../smart-routing-feasibility.md) holds baseline `a4e0f1b`, the F01–F17 owners, the toolchain and a passing `validate.py`.

**Files:** create `docs/smart-routing-feasibility.md`.

- [x] Choose the implementation baseline (inspected `main` was `d73252a77d1e784f8a7d6f637bc661467485178d`; this checkout lacks `runtime/usage_*.py`, `runtime/external_delegation.py` and `skills/manage-usage-routing/`). Record the chosen revision and how the local branch changes are reconciled.
- [x] Record the owner of every F01–F17 row on that baseline and the actual test toolchain.
- [x] Run `python3 -B scripts/validate.py`; expected PASS.

### Task 0.2: Amend the spec

**Status (10 November 2026):** Done: spec v1.5 with A1–A13; A14 added in #114 and case 7 corrected in #120.

**Files:** modify `docs/superpowers/specs/2026-11-08-usage-routing-opencode-subscriptions-design.md`.

- [x] Apply A1–A13 to the sections each one names; bump the spec to v1.5, and update §13 case 7 per A9 and §12 with P0.0.
- [x] Replace section 3 of this plan with links to the amended spec sections.
- [x] Run `python3 -B scripts/validate.py`; expected PASS.

### Task 0.3: Classify billing and terms (P0.0)

**Status (10 November 2026):** Partial (#125, #182): the [capability matrix](../../smart-routing-capability-matrix.md) classifies every route from vendor terms with sanitized probes, and ChatGPT via OpenCode ran live for `gpt-6-luna`. The per-route manifest `tests/fixtures/smart-routing/capabilities.json` was never created; the matrix stands in for it. C.3 slice C3-1 created the route manifest `runtime/routing_routes.json` (#213; its shape is in [design §3.3](../specs/2026-11-10-smart-routing-c3-wiring-design.md#route-manifest-shape)); its content waits on [user decision 5](#user-decisions-for-c3), and until then it holds only a `test_only` route, so no project can be activated. Observed billing (usage pages, Codex pool sharing), plan tier and the API grant debit order wait on the user.

**Files:** create `docs/smart-routing-capability-matrix.md`, `tests/fixtures/smart-routing/capabilities.json`.

**Interface:** one manifest record per route: `{route_id, provider, account_ref, harness, billing_class, terms_source, terms_checked_at, evidence_refs, status: qualified | unsupported | unverified}`. No secrets, prompts or account identifiers.

- [ ] Classify every route currently reachable from the user's configured clients under A12, citing current vendor terms and observed billing.
- [ ] Record the reference Claude subscription route first: OpenCode running Claude through its Claude Agent provider (the Claude Agent SDK) on the Claude subscription, the user's working setup (spec §6.1; standing disputed, see the capability matrix). Capture its harness identity and rate-limit telemetry as an `included_subscription` fixture.
- [ ] For Claude and OpenAI alike, find the remaining subscription routes the vendor bills as included usage: the provider's own harness (Claude Code, Codex) launched directly, OpenCode's Claude Agent provider, and OpenCode's Sign in with ChatGPT. Record each as `included_subscription`, or as `purchased_usage` if it bills extra usage. Partly observed on 10 November 2026: OpenCode's Sign in with ChatGPT ran one request on `gpt-6-luna` with a null cost and a window settlement ([live report](../../smart-routing-qualification.md#chatgpt-attempts-5-and-6)). The vendor's billing class (usage page, pool shared with Codex) is not yet confirmed, so this item stays open.
- [ ] For both providers, confirm that API Usage key routes draw on the subscription-linked API allowance (`api_grant`) before any other credit.
- [ ] Stop work on any slice whose required routes are all `unsupported` or `unverified`, and report it.

### Task 0.4: Probe the remaining P0 gates per slice

**Status (10 November 2026):** Partial: P0.1 done (plugins load in OpenCode's embedded Bun; `node --test` runs `tests/opencode_plugin/`; `--serve` `check` p95 0.27 ms in #130, live shim check p95 1.18 ms over 20 pilot admissions in #145); probe output sanitized (#125, #128). P0.2 is partly established: on the ChatGPT route OpenCode's `native.rateLimit` is absent and no standalone reader of Codex's `account/rateLimits/read` exists, so whether it shares Codex's pool is unknown (#161, #182). P0.3 and P0.4 wait on the user's API Usage decision, and P0.5–P0.8 are tracked under S3.3–S3.6 and the [gate table](../../smart-routing-capability-matrix.md#gate-status-p0).

**Files:** extend the capability matrix and fixtures.

- [x] P0.1: pin the OpenCode plugin loader and shim test runner; measure the median and p95 latency of a no-op `scripts/routing.py` call from a plugin hook.
- [ ] P0.2–P0.3: record telemetry and grant observations for each route classified `included_subscription` or `api_grant`, within authorized bounds.
- [ ] P0.4–P0.8: probe only once S3 is next; record requested versus observed behavior.
- [x] Sanitize the earlier scratch probe output before reusing any of it.

### Task S1.1: Allowance policy replaces the 20% floor

**Status (10 November 2026):** Done (#113): allowance tests in `tests/test_usage_budget.py`; the floor is gone.

**Files:** modify `runtime/usage_budget.py`, `tests/test_usage_budget.py`.

**Interface:** the organization policy stores `allowance_bp` (0–10000, default 9800) or `allowance: "no-limit"`, plus the verified issued allowance `G`. `Ledger.credit_headroom(org)` returns `max(0, B − ceil((10000 − allowance_bp) × G / 10000) − R − U)` in integer micro-units; `no-limit` uses protected = 0 and `B` across verified eligible lots.

- [x] Write failing tests: `G=100_000_000`, 9800 bp, `B=30_000_000`, `R=5_000_000`, `U=0` → `23_000_000`; 0 bp → 0; 10000 bp → `25_000_000`; `no-limit` excludes unverified lots; a billing-date change without an observed issuance mints nothing.
- [x] Run `python3 -B -m unittest tests.test_usage_budget`; expected: the new tests fail.
- [x] Remove the `(balance + 4) // 5` floor paths in `usage_budget.py` and `usage_routing.py`; implement the policy.
- [x] Re-run; expected PASS.

### Task S1.2: Per-task claims replace the slot

**Status (10 November 2026):** Done (#113): state v2 without `slot`; repeated-`run_id` and two-ledger thread-race tests in `tests/test_usage_budget.py`.

**Files:** modify `runtime/usage_budget.py`, `tests/test_usage_budget.py`.

**Interface:** `begin(...)` no longer reads or writes `data['slot']`; the state version increments and the `slot` key is removed. Each run holds its own claim.

- [x] Write failing tests: two affordable distinct runs both begin; a repeated `run_id` returns the existing run without adding a hold; two `Ledger` instances on one file, racing distinct runs from two threads for the last headroom, admit at most what fits.
- [x] Rewrite every test that expects `request_in_flight` for an affordable second task into a shared-headroom assertion.
- [x] Run `python3 -B -m unittest tests.test_usage_budget`; expected: the new tests fail.
- [x] Remove the `slot` checks from `begin`, `reserve_generation` and `dispatch`; re-run; expected PASS.

### Task S1.3: Padded reservations, top-ups and safe release

**Status (10 November 2026):** Done (#113): padding, top-up, timeout and provision-transfer tests. Padding lives in the ledger only; `Controller._run` does not use it yet (#113).

**Files:** modify `runtime/usage_budget.py`, `tests/test_usage_budget.py`.

**Interface:** `begin(..., estimate, next_request_bound, margin_bp=2000, ...)` reserves `max(ceil(estimate × (10000 + margin_bp) / 10000), next_request_bound)`. `top_up(run_id, added, revision, now)` admits atomically or raises `insufficient_headroom`. `complete(...)` charges the actual cost and releases unused padding; `uncertain(...)` retains exposure.

- [x] Write failing tests for the spec §7.4 example in micro-units: with `30_000_000` available, estimates 5/10/8 reserve `6_000_000`/`12_000_000`/`9_600_000`; a fourth reserve of `3_600_000` fails; completing the first at `4_000_000` releases `2_000_000` and the fourth then fits. A timeout keeps its full hold, and a top-up beyond headroom fails without changing state.
- [x] Write a failing test that a parent review provision transferred to a child is not counted twice.
- [x] Run the module tests (expected FAIL), implement, re-run (expected PASS).

### Task S1.4: Concurrency caps (A10)

**Status (10 November 2026):** Done (#113): `concurrency_cap` tests; open holds count toward the caps since #123.

**Files:** modify `runtime/usage_budget.py`, `tests/test_usage_budget.py`.

- [x] Write failing tests: with caps 4 total and 2 per account, the third open run on one account fails with `concurrency_cap`, a run on another account succeeds, and closing a run frees a place.
- [x] Implement the caps as admission checks reading open runs inside the same transaction; re-run; expected PASS.

### Task S1.5: Slot-free routing, recovery and instructions

**Status (10 November 2026):** Done (#113): second-task and enumeration tests in `tests/test_usage_routing.py`; the serial rule and `simulation_only` are removed.

**Files:** modify `runtime/usage_routing.py`, `tests/test_usage_routing.py`, `skills/manage-usage-routing/references/*.md` and `SKILL.md` if it states the serial rule.

- [x] Write failing tests: `_evaluate` admits an affordable second task; recovery enumerates every pending attempt, not just the slot owner.
- [x] Remove `require(data['slot'] is None, 'request_in_flight')` and the slot-owned dispatch, completion and recovery paths; implement the enumeration.
- [x] Remove the serial-execution rule from the skill references; keep the entry point within 150 words.
- [x] Run `python3 -B -m unittest tests.test_usage_routing tests.test_usage_budget` and `python3 -B scripts/validate.py`; expected PASS.
- [x] Removing `simulation_only` must not remove a gate. Live API funding stays blocked until API Usage is explicitly enabled, a secure key binding is ready, qualification is recorded and admission evidence is present, and until P0.3/P0.4 pass for the route. Regression coverage: `tests/test_openai_api.py` (`live_admission_required`, `credential_unavailable`) and `tests/test_usage_live.py` (`test_off_and_json_evidence_never_resolve_a_credential`) check that no provider request is transmitted without admission evidence or a valid key binding, and that OFF never resolves a credential. Extend them if a gate combination is found uncovered.

### Task S2.1: Routing settings

**Status (10 November 2026):** Done (#118): `tests/test_routing_settings.py`.

**Files:** create `runtime/routing_settings.py`, `tests/test_routing_settings.py`.

**Interface:** `resolve(stored: dict) -> dict` and `update(path, change, expected_revision) -> dict`. Defaults: `smart_routing=True`, `api_usage_enabled=False`, `L=0.65`, `H=0.85`, `T_hours=72`, `min_elapsed_fraction=0.10`, `lookback_hours=72`, `margin_bp=2000`, the caps from A10 and `worker_choices={}`. Reads the Workstreams preference from its existing authority without copying it.

- [x] Write failing tests: `resolve({})["smart_routing"] is True`; `resolve({})["api_usage_enabled"] is False`; an explicit `smart_routing=False` survives a write/read cycle; `0 <= L < H <= 1` and `T_hours > 0` are enforced; `worker_choices` entries with unknown roles or missing fields are rejected; a stale `expected_revision` is refused.
- [x] Run `python3 -B -m unittest tests.test_routing_settings` (expected FAIL), implement, re-run (expected PASS).

### Task S2.2: Profiles in Workstreams jobs (A4)

**Status (10 November 2026):** Done (#121): `tests/test_routing_profiles.py`; plan jobs accept an optional validated `profile`.

**Files:** create `runtime/routing.py`, `tests/test_routing_profiles.py`; modify `runtime/delegation.py` and the Workstreams job/brief contract in `clients/workstreams-protocol.md`.

**Interface:** `ensure_profile(job: dict, *, jev=None) -> dict` returns a `TaskProfile` with per-field provenance (`coordinator | metadata | floor | jev`), a contract version and a semantic digest over objective, role, scope, tools, review/risk requirements and contract version.

- [x] Write failing tests:
  - A complete coordinator profile makes zero `jev` calls.
  - Balance, progress or retry changes keep the same digest.
  - A changed role, scope or tools changes the digest.
  - Another project's scope cannot reuse a profile (RF2).
  - Missing fields with `jev=None` take conservative floors with `floor` provenance.
  - Requirement floors cannot be lowered by supplied or Jev values.
- [x] Run, implement, re-run; expected PASS. Keep existing `selection.profile_ids` guidance references separate from task profiles.

### Task S2.3: Evidence compiler and strategy table (A2, A5)

**Status (10 November 2026):** Done (#121, #196): compiler, lookup and `tests/test_routing_evidence.py`; `scripts/routing_table.py refresh` writes `strategy-table.json` from local evidence files (atomic, private, keeps the last verified table when a refresh fails), proven end to end against `scripts/routing.py prepare` in `tests/test_routing_table.py`. A successful refresh of a new version keeps the replaced table as `strategy-table.previous.json` (one version); `routing_table.py rollback` swaps it back, proven end to end against `scripts/routing.py prepare` (#211; [C.4 rollback box](#task-c4-default-on)). The [maintenance procedure](../../../clients/guidance-publication.md#strategy-table-refresh) names the command, and [policy 03 worker selection](../../../policies/03-agent-delegation.md#select-capability-and-dispatch) links to it. Whether a real maintenance run has produced the guidance, catalog, benchmark and terms input files is a separate live observation; until one does, deployed checkouts have no table.

**Files:** create `runtime/routing_evidence.py`, `tests/test_routing_evidence.py`, fixtures under `tests/fixtures/smart-routing/`.

**Interface:** `compile_table(guidance: list, catalog: list, benchmarks: list, terms: list) -> dict` produces versioned rows keyed by `(role, task_family, complexity_band, risk_band)`, each an ordered list of `{strategy_id, model, effort, harness, evidence_refs}`. `lookup(table, profile) -> list | blocked`.

- [x] Write failing tests:
  - Rows naming models absent from the catalog, or unsupported efforts, are dropped.
  - A new catalog model qualifies only with cited evidence.
  - A failed refresh keeps the last verified table with its original age.
  - Control-plane candidates without zero-retention/no-training terms are excluded (RF7).
  - The source tree contains no hard-coded commercial model ID.
- [x] Hook compilation into the existing maintenance guidance refresh (`policies/03-agent-delegation.md`, worker selection), outside spawn paths.
- [x] Run, implement, re-run; expected PASS.

### Task S2.4: Deterministic ranker (A2, A3)

**Status (10 November 2026):** Done (#121): `tests/test_routing_ranker.py`.

**Files:** modify `runtime/routing.py`; create `tests/test_routing_ranker.py`.

**Interface:** `rank(profile: dict, table: dict, constraints: dict) -> dict` returns `{strategy_ids, reason_codes, table_version, profile_digest}`; no spending authority.

- [x] Write failing tests:
  - An exact row returns its order.
  - A missing row uses the next stricter band, then `ranking_conservative`.
  - Explicit user constraints narrow the candidates and never widen them.
  - Ranking makes zero network or model calls.
  - A table refresh re-ranks without re-profiling.
- [x] Run, implement, re-run; expected PASS.

### Task S2.5: Pure allocation functions (A6–A9)

**Status (10 November 2026):** Done (#118; case 7 corrected to 55 in #120): `tests/test_routing_allocator.py`.

**Files:** create `runtime/routing_allocator.py`, `tests/test_routing_allocator.py`.

**Interface:** `projected_utilization(windows, reserve_new, now, min_elapsed_fraction) -> (float | None, reasons)`; `expiry_pressure(remaining, expected_spend, hours_to_expiry, T_hours) -> float`; `api_share(u, e, L=0.65, H=0.85) -> float`; `choose_class(shares, admissions, bucket, epoch, feasible) -> str | None`; `choose_route(candidates, now, minimum_horizon) -> str | None`. Both return `None` when nothing is eligible or fundable; `api_share` raises `ValueError` for unknown utilization; bad numbers raise `ValueError` with a fixed reason.

- [x] Write failing tests:
  - `api_share(0.40, 0) == 0`; `api_share(0.75, 0) ≈ 0.5`; `api_share(0.80, 0) ≈ 0.75`; `api_share(0.90, 0) == 1`; `api_share(0.40, 2/3) ≈ 2/3`; `api_share(0.75, 2/3) ≈ 0.833`.
  - `expiry_pressure(remaining=50, expected_spend=0, hours=24, T=72) ≈ 2/3`; `expected_spend=50` gives 0; no grant gives 0.
  - A weekly window at 30% after 10% of its length projects above the bare 30%; a 5-hour window at 80% resetting in 10 minutes projects about 83%; before `min_elapsed_fraction`, the pace term is omitted with `pace_unavailable`.
  - A healthier alternative subscription sets `u`; stale observations return `None`.
  - Class choice from recorded admissions matches the shares over 20 sequential decisions, API wins ties, and a rejected decision leaves no record.
  - Within class, the amended case 7 sends the first 55 to the 75-pool; case 8 picks the 1-hour pool for the first 17 admissions; zero-demand candidates never divide by zero.
- [x] Run, implement, re-run; expected PASS.

### Task S2.6: Allocate and reserve in one transaction

**Status (10 November 2026):** Partial. Done for 7 of the 8 listed tests (#123, #124: `tests/test_routing_admission.py`, `tests/test_routing_funding*.py`); the model-ranker timeout test (RF8) waits for S4, where the ranker is introduced; the fallback when the ranking is empty or the strategy table is missing is tested instead.

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
- [x] Run, implement, re-run; expected PASS.

### Task S2.7: Cutover criteria (A11)

**Status (10 November 2026):** Done (#118; A14 in #114): [validation](../../smart-routing-validation.md) pre-registers the criteria and canary bounds, with each pilot edit recorded before its run (#145). [Edit 6](../../smart-routing-validation.md#edit-6-10-november-2026-replay-against-the-available-history) (10 November 2026, before any replay) replaces the 200-assignment replay minimum with the available history: before the canary, the strict replay over the exported pilot records must pass and an advisory historical replay must have run; the strict replay runs again at C.1.

**Files:** create `docs/smart-routing-validation.md`.

- [x] Write the cutover criteria before any canary starts: minimum canary sample, the disagreement rate that triggers quality re-execution, zero funding violations in allocator replay, latency budgets for warm and cold preparation, and the canary bounds (below). A disagreement is a recorded historical assignment whose accepted model/effort or funding class differs from the strategy the new ranker and allocator choose for it; S2.8 replays those recorded assignments to produce the rate.
- [x] Define the canary bounds in the same document: one named project allowlist, a spend cap per funding pool, a maximum duration, concurrency caps (A10), an abort condition (any funding violation, unresolved launch or recovery error) and the Smart Routing OFF kill switch (spec §3), which stops new automatic admissions without a model call.
- [x] There is no shadow or simulation mode and no decision-only hook at the current selectors. Current selectors keep dispatching until C.3 replaces them.

### Task S2.8: Allocator replay

**Status (10 November 2026):** Done (#118, #195): `tests/test_routing_replay.py` replays about 50 synthetic arrivals (concurrent claims, resets, expiries, OFF toggles, exact class and route replay) and, through `AdmissionReplay` over the merged compile/refresh, profile, rank, admit and settle path, a catalog refresh and a six-job dependency plan with zero Jev calls. The cutover replay is split by [validation Edit 6](../../smart-routing-validation.md#edit-6-10-november-2026-replay-against-the-available-history): before the canary the strict replay over the exported pilot records must pass and the advisory historical replay must have run; the strict replay runs again at C.1.

**Files:** create `tests/test_routing_replay.py`, `tests/fixtures/smart-routing/arrivals/*.json`, `tests/fixtures/smart-routing/arrivals/admission/*.json`.

- [x] Write replay cases over isolated ledgers: identical chronological arrivals, concurrent claims, resets and expiries, catalog refresh, OFF toggles, and a six-job dependency plan with zero Jev calls.
- [x] Assert: no funding violation, no resurrected capacity, exact replay of class and route decisions from admission records.
- [x] Run `python3 -B -m unittest tests.test_routing_replay`; expected PASS once S2.5–S2.6 are complete.

### Task S3.1: Single routing command

**Status (10 November 2026):** Done (#130; later ops in #135, #142, #144): `tests/test_routing_command.py`.

**Files:** create `scripts/routing.py`, `tests/test_routing_command.py`.

**Interface:** JSON on stdin and stdout; subcommands `prepare` (profile, ranking, admission), `check` (pre-transmission envelope and route check), `record` (lifecycle event), `status` (local only, no inference), `set` (settings, including OFF). Every response is versioned and either an admission/decision or `{"state": "blocked", "reason": …}`.

- [x] Write failing tests: malformed input is blocked; `status` and `set` make zero model, network or secret-store calls (RF5); `set smart_routing=false` invalidates unsent automatic admissions under the old revision.
- [x] Run, implement, re-run; expected PASS.

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
| `reconcile_subscription` (S3.6c, operator) | `attempt_id`, `evidence{kind: host_usage, session_id, usage}` or `evidence{kind: abandon}`, `event_id` | `recorded{event_id, duplicate, outcome: complete / abandoned, settlement{kind: window, released, debited?, observed_units, resolution{basis}}}` |
| `drop_debit` (C3-0a, operator) | `key`, `marker`, `event_id` | `recorded{event_id, duplicate, outcome: debit_dropped, key, units}` |
| `settle_legacy_call` (C3-0a, operator) | `run_id`, `phase`, `evidence`, `event_id` ([design §4.4](../specs/2026-11-10-smart-routing-c3-wiring-design.md#44-settle-outstanding-legacy-work)) | `recorded{event_id, duplicate, outcome: released / charged / over_bound, run_id, phase, charged, released, resolution, remaining_reasons}` |
| `status` | — | `ok{settings, settings_revision, effective_caps, project_pinned, ledger{…, authority_required, unstamped_open_holds, unbound_open_holds}}`; local files only |
| `set` | `expected_revision` + `change`, or `organization{id, enabled, policy?, expected_revision?}`, or `authority_required: true` (S3.4); a `change` with `managed_projects` is one-shot only (C3-0b) | `updated{revision, settings, invalidated_attempts, ledger_state?}`, `updated{organization}` or `updated{authority_required}` |

Rules the command applies:

- **Roles.** `prepare` maps the job's profile role to a worker-choice role with `routing_settings.PROFILE_ROLES`. An explicit `researcher` or `repair` role is accepted; any other explicit role must match the mapping (`role_mismatch`). A coordinator is never routed (`coordinator_not_routed`).
- **Strategy table.** `prepare` ranks from the compiled table stored next to the settings (`strategy-table.json`, or `--table`). A missing, unreadable or malformed table falls back to worker choices or the explicit choice with `table_unavailable`; it never blocks. A table or ranking with a non-string strategy id or an unbounded version counts as malformed. Not yet met: a missing table gives an empty ranking with no reason code; see [Not-ready reason without a table](#follow-ups-from-merged-work).
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
- **Recording outcomes.** A bare `complete` on an uncertain attempt is refused with `reconciliation_required`; the operator settles the hold with `reconcile_uncertain` (S3.6b-2). An uncertain subscription hold is settled by the operator's `reconcile_subscription` (S3.6c). Event ids, usage and settlement follow the [S3.6a contract](#task-s36-lineage-settlement-and-retention).
- **Busy ledger.** `check` and `status` wait at most 200 ms for a busy ledger and then answer `ledger_busy`. Writes wait at most 300 ms under `--serve` and 5 s in one-shot mode.
- **Caps.** Effective caps are the stricter of the settings and ledger caps, so `set` can only lower them. Raising them is an explicit ledger action (`Ledger.set_caps`).
- **Dispatch authority (S3.4).** A managed launch calls `dispatch` after `prepare` and before creating anything on the host. In one write transaction, reading the settings and the command clock inside it, `dispatch` re-runs every `check` gate on the unsent admission (settings revision, age, dollar funding, route identity, envelope) and then [S3.5's fresh gates](#task-s35-fresh-gates-at-dispatch-rf3), which only read; any refusal is blocked with that reason and leaves the hold `admitted` with nothing consumed. Otherwise the hold becomes `dispatched` and the response carries `authority`, a random 256-bit token (43 URL-safe characters). The ledger stores only its SHA-256, so the token appears in that one response and nowhere else: not in the ledger, `status`, any other response or a log. Every repeated or racing `dispatch` gets `already_dispatched` without a token, and a token whose response was lost cannot be recovered. A hold already `uncertain` also answers `already_dispatched`; a closed one is `attempt_closed`. No host call happens inside `dispatch`. `Ledger.convert_hold` accepts only a hold dispatched this way (never an admitted, bare-dispatched or uncertain one) and keeps its dispatch record.
- **Derived host ids (S3.4).** The launch must use the session and message ids derived from the ledger identity, the attempt id and the admission's nonce: `ses_` and `msg_` followed by 32 lowercase hex characters of a domain-separated SHA-256 (`runtime.routing.derive_ids`, read through `admission_ids`). `admit` stores a random 16-byte nonce (32 hex characters) in each admission record, so a duplicate `prepare` keeps the same ids, two ledgers on one server never share ids, and an attempt id reused after it ages out of the ledger (the last 1000 ids are refused outright) gets a new nonce and new ids. Admissions recorded before nonces derive with none. The nonce adds 43 bytes to a record; the longest admission in the capacity tests is 4,097 of the 6,656-byte cap (4,096 plus S3.5's 2,560-byte binding allowance). The ids are a conservative subset of the adapter's `SESSION` and `MESSAGE` patterns; the probe fixtures are sanitized, so the server's own id body format is not on record. OpenCode's own ids are time-ordered and these are not, so P0.7 re-qualification must confirm the server accepts them and that one prompt with a derived message id causes exactly one model request. The ledger keys the dispatch on them, and `dispatch` refuses ids another remembered attempt owns (`id_collision`). The host deduplicates them (P0.7), so re-sending the same prompt with the same ids after a lost host acknowledgment is the only retry: it needs the token and never a second `dispatch`.
- **Handshake (S3.4).** The launcher hands the token to the OpenCode shim only through the plugin's in-memory `routing.attach` for the derived session; the shim keeps it in memory, sends it as `authority` with `attempt_id` and the hook's `session_id` on every `check` for that session and its child sessions, and never logs, persists or echoes it. A hold dispatched with an authority allows only requests carrying its token (`authority_required`, `authority_invalid`); the allow result then adds `authority: "verified"`. A check that sends `session_id` or `authority` is strict: a hold without an issued authority (unsent, or dispatched by a bare `record dispatched`) is `dispatch_required`. The ledger flag `authority_required` is off by default and turned on only by the operator's explicit `set authority_required=true`; it can never be turned off. Each admission made while it is on is stamped with it and keeps that rule for its life, so in-flight holds keep the rule they were admitted under. For a stamped admission, a token-less attempt-only `check` and a bare `record dispatched` are `dispatch_required`; an unstamped one keeps the S3.1 rules, and one dispatched by a bare `record dispatched` stays open until `complete`. `set authority_required=true` is the only `set` form that creates a ledger. Pre-canary checklist: the shim forwards the token and session id (done in S3.3b, tested with a fake child and the real command on pinned temporary stores, not yet observed on OpenCode), `status` shows `authority_required: true`, and `status` shows `unstamped_open_holds: 0` (open holds whose admission predates the flag). S3.5 adds the [workflow-binding requirements](#task-s35-fresh-gates-at-dispatch-rf3): the command runs with `--project` (`status` shows `project_pinned: true`), and every planned job is prepared with `workflow` and an `attempt_id`, so `unbound_open_holds` counts only explicit delegation.
- **Unmanaged sessions (S3.4).** A `check` with only a `session_id` that no dispatched hold stores and no admission record of this ledger derives (never admitted here, or aged out of both tables, since deriving needs the record's nonce) answers `{"v":1,"state":"unmanaged"}`: interactive sessions stay unguarded, and the shim blocks this answer for a session it attached. Only session ids match: a message id sent as a session id is unmanaged. A known attempt whose hold or record is gone is `attempt_closed`. With both ids, a session belonging to another attempt is `session_mismatch`; an unknown one (a child session) is checked under the attempt. A missing ledger answers `ledger_absent` (fail closed), never `unmanaged`.
- **Lost acknowledgments (S3.4).** If the launcher cannot confirm the host launch, it records `uncertain`: exposure and the concurrency place stay counted, and every `check` of the attempt is blocked from then on. S3.4 accepts no proof of non-launch: `not_dispatched` is valid only for a hold that was never dispatched (`admitted`), so a dispatched or uncertain hold stays counted until completion or the operator's `reconcile_uncertain` (S3.6b-2) for a dollar hold, which needs receipts or charges the full reservation, or `reconcile_subscription` (S3.6c) for a subscription hold, which needs the host re-read or keeps a window debit. `reconcile_hint` names the host read (`GET` of the derived message) for the operator and is marked `authoritative: false`: a not-found answer can also come from a deleted or reverted message or another server or directory, and P0.7 is qualified only for the cases probed.
- **Managed projects (C.3, #212, #217).** For a project on `managed_projects`, `prepare` builds its own observations and refuses caller ones, and adding a project runs the activation precondition and gate. The reason codes are `invalid_managed_projects`, `observations_not_accepted`, `observations_unavailable`, `open_unrouted_assignments` and `managed_path_incomplete`; each is defined once in the [C.3 design](../specs/2026-11-10-smart-routing-c3-wiring-design.md#36-refusal-reasons).
- **Test switch.** The `--test-inputs` command-line flag lets tests supply `now` to `check` and `dispatch`, an off-clock snapshot and a `table` to `prepare`. `record` takes no `now` (S3.4 removed it): a `record` request with one is `invalid_request`, with or without the flag. No request, configuration, table or environment variable can enable it. It is a usage error unless both `--settings` and `--ledger` are given and differ from the production defaults, and `status` reports `test_inputs`.

### Task S3.2: OpenCode adapter and client contract

**Status (10 November 2026):** Done (#129): `tests/test_opencode_adapter.py` over the sanitized `p0-hook-event-excerpt.json` and `s3-3-shim-live.json` payloads.

**Files:** create `runtime/adapters/opencode.py`, `clients/opencode.md`, `tests/test_opencode_adapter.py`; modify `clients/index.md`, `clients/instruction-map.json` and the support targets in `docs/onboard-my-project-plan.md#client-scope-and-adapter-organization`.

- [x] Write failing tests against recorded, sanitized OpenCode V2 hook and event payloads: normalization yields task, parent, stage, attempt, session/message IDs, requested model/effort and route identity; unknown fields stay unknown.
- [x] Run, implement, re-run; expected PASS.

### Task S3.3: Pre-transmission shim

**Status (10 November 2026):** Partial (#132, #140, #152, #154; live in #145 and #163): the shim, handshake, descendants RPC and S3.3d denies are on `main` and observed live. Changed in #132: tools other than subagent spawns (MCP, Code Mode, built-ins) are governed by policy deny rules, not `check`, as the S3.1 contract states. Missing: the global plugin install with enforced worker profiles, and the rest of the P0.6 re-qualification (last box): MCP and Code Mode calls, a real `webfetch` execution, a failed plugin load making `routing.health` fail, the S3.3d image-generation probe and a tool-call spawn on another model. The `routing.health` box rests on the qualification harness (`tests/qualification/opencode_live.py`) refusing to send when health fails; no production launcher exists until C.3.

**Files:** create `runtime/adapters/opencode_plugin.ts`; extend `tests/test_opencode_adapter.py`.

- [ ] Use the hooks recorded in the [capability matrix](../../smart-routing-capability-matrix.md#opencode-enforcement-p01-p05p08): `model.request` for every model request, `tool.execute.before` for subagent, MCP and nested tool calls, `permission.evaluate` for `webfetch`, and `shell.create.before` to strip credentials from the command environment. Each forwards to `scripts/routing.py check` and blocks unless it returns an admission. A missing, failing or slow command blocks (fail closed). The shim contains no selection or funding logic.
- [x] Run one persistent child over stdio per plugin instance with a timeout of about 500 ms on every check; kill and respawn on a stall, and throw if the respawn fails.
- [x] Register a `routing.health` plugin RPC. A managed launch calls it for the session's canonical directory (symlinks resolved) and refuses to send when it fails. Interactive sessions are not blocked by a plugin failure (user decision).
- [ ] Worker profiles are enforced only when policy rules deny `shell`, MCP tools, Code Mode `execute` and `webfetch` (or allow a narrow list). Profiles that keep full shell access run advisory-only, with no funding guarantee. Install the plugin globally; it enforces nothing outside the bounded canary project until C.4.
- [x] Record route identity as alias + requested variant + catalog family; the resolved model is `unknown`.
- [x] S3.3b: the shim sends `attempt_id`, the hook's `session_id` and the `authority` token on every `check` for attached sessions and their children; `routing.attach` accepts only the attempt's own derived session, verifies the token and that ownership through the child before recording anything, and answers every refusal with `attach_refused`; `routing.detach` needs the same attempt and token; derived session IDs an open attempt owns are blocked until attached (expiring after 5 minutes), a finished attempt's session and the sessions recorded under it stay blocked, also after detach, and only attached roots strip shells or start lookups. The contract is in [clients/opencode.md](../../../clients/opencode.md#handshake).
- [x] S3.3c: `routing.descendants {session_id, attempt_id, authority}` returns the shim's recorded managed descendants of an attached root (IDs and parent links only, at most 512, `truncated`), with `complete: false` and a fixed `reason` when the shim cannot vouch for the set (no or a failed event stream, the root's creation not seen while attached, the descendant budget exceeded, an undetermined parent chain); the same authority rules as `routing.detach`, answering only while attached. `runtime/opencode_accounting.py` cross-checks it against the launcher's listing (`shim_descendants`, `cross_check_lineage`). The launcher order and contract are in [clients/opencode.md](../../../clients/opencode.md#plugin-rpc). Tested with a fake event stream and the real `routing.py --serve`; observed live on 9 November 2026 ([run](../../smart-routing-qualification.md#live-probe-run-descendants-and-s33d-harness-85ae5f4), 5 calls). Positive run: `complete: true` with a tool subagent, a `subagent: true` command child and an API-created child, equal to the launcher's listing; accounting with `shim_descendants` had no unknowns; refused after `detach`. Negative run (root created before `attach`): `complete: false`, `root_creation_unobserved`, accounting `shim_lineage_incomplete`. Not observed: `truncated`, the descendant budget, an API-funded attempt with real costs. Review of PR #152: every shim-recorded session must be included (S1); a repeated root creation event never vouches again (S3); only spend paths (`model.request`, subagent spawns) mark roots for an undetermined chain (S2, partial). Follow-up: the per-root incompleteness set under [follow-ups](#follow-ups-from-merged-work).
- [x] S3.3d: for every non-interactive session (attached, pending or closed) the shim denies image generation (`experimental_image_generate`, a paid model call that never reaches `model.request`; permission action and tool) and every `edit`, `write` or `patch` whose resources include an OpenCode config path (`.opencode`, `.claude`, `.agents`, `opencode.json(c)` anywhere; `.config/opencode`; the global and system config roots; plus the `protected_paths` option, which only adds) or are missing or unreadable; `routing.health` lists `managed_denies`, and `probe` is `enforced` only with `image_generation` denied and `config_edit` denied or narrowed. Launchers never invoke project commands inside a managed session. Established by read-only inspection of the 2.2.6 binary and package-tested with a fake context; config-path writes and the command-shell environment were observed live (below), image generation was not. Before cutover, run the [required live probe](../../../clients/opencode.md#image-generation-and-configuration-edits-s33d) (image request denied with no `model.request`, model write to `.opencode/command/x.md` denied, whether a `` !`env` `` command under an attached root sees credential-like names stripped). Live probe on 9 November 2026 ([run](../../smart-routing-qualification.md#live-probe-run-descendants-and-s33d-harness-85ae5f4), 2 calls):
  - **Config edits, observed:** model `write`s to `.opencode/command/x.md` and `sub/opencode.json` were denied and the files stayed absent; a relative `agents/ok.txt` write was allowed.
  - **Shell environment, observed: not stripped.** A `` !`env` `` command under an attached root saw the dummy credential-like variable. That command shell never reached `shell.create.before`, so the rule that launchers never invoke project commands inside a managed session is load-bearing.
  - **Image request, unobserved:** there was no image model.
  Review of PR #154: edits resolve against the instance directory (`ctx.location.directory`), so `agent(s)`, `command(s)` and `mode(s)` directories are protected only inside config directories (fail closed when the directory is unknown); absolute paths are protected only through the config names, `.config/opencode` and the computed global and system config roots; an invalid `protected_paths` option (at most 32 entries of 16 segments) makes `routing.health` `not_ready` (`protected_paths_invalid`). Follow-up: `websearch` under [follow-ups](#follow-ups-from-merged-work).
- [ ] Re-qualify P0.6 on the finished shim: model requests, subagent and nested spawns, tool and MCP calls, and `webfetch` are blocked before transmission, and a failed plugin load makes the health check fail. Record any unblockable path as unsupported in the capability matrix. Partly observed live on 9 November 2026 ([live report](../../smart-routing-qualification.md)): the handshake, blocked unattached and wrong-token sessions, `webfetch` evaluation, shell stripping and a command subagent child on another model; `subagent: true` commands skip `tool.execute.before` and are stopped only by lineage. Tool-call spawns, MCP, Code Mode and a failed plugin load were not re-run. The re-run also observed a finished attempt's session blocked (reason `attach_required` within the 5-minute pending window) and an interactive session untouched with the routing child killed. The live probes (9 November 2026, harness 85ae5f4) observed a tool-call subagent spawn on the allowed route: it passed `tool.execute.before`, and its child's request was checked and allowed. A spawn on another model through the tool was not attempted. A `` !`env` `` command shell under an attached root did not reach `shell.create.before`.

### Task S3.4: Single-use dispatch authority

**Status (10 November 2026):** Partial (#135; live in #145): the first two boxes are done. The third (P0.7 re-qualification) is unticked: live runs observed derived ids accepted, one request per message id, a duplicate message id deduplicated and the lost-acknowledgment drill, but not racing launchers against the host or a server restart.

**Files:** modify `runtime/usage_budget.py`, `runtime/routing.py`; create `tests/test_routing_dispatch.py`.

- [x] Write failing tests: 15 callers racing for one attempt produce at most one authority; distinct affordable attempts run concurrently; a lost acknowledgment leaves the claim `unknown` and is not retried without proven host deduplication or proof of non-launch.
- [x] Derive the session and message IDs from the attempt ID. The server deduplicates them (observed), but the `prompt` hook can run more than once for one message, so key the ledger on the message ID and recover an unknown attempt by reading the message by ID.
- [ ] Run, implement, re-run; expected PASS (done). Re-qualify P0.7 on the finished path (not yet observed on OpenCode), including that one prompt with a derived message id causes exactly one model request, and that OpenCode's own session ids never have the derived shape (`ses_` + 32 lowercase hex), which the S3.3b shim assumes when it decides which sessions to ask the ledger about. Observed live on 9 November 2026 ([live report](../../smart-routing-qualification.md)): derived ids accepted, one request per derived message id, no request on a duplicate send (an extra `idle` record appears), read-back by id, OpenCode ids are 26 mixed-case characters, insertion-ordered listing; the re-run observed the lost-acknowledgment drill (hold stays counted, `record uncertain` keeps exposure; an uncertain subscription hold has no operator path). Racing launchers on the host and server restart were not run.

The contract (dispatch, derived ids, the shim handshake, `authority_required`, `unmanaged` and lost acknowledgments) is defined once in the [S3.1 contract](#task-s31-single-routing-command).

### Task S3.5: Fresh gates at dispatch (RF3)

**Status (10 November 2026):** Package only (#141): the gates and `tests/test_routing_gates.py` are done, but no live `prepare` has used `workflow` with `--project`, so the former P0.10 exit on the OpenCode host is unobserved. This audit unticked the second box, which had been ticked on package evidence.

**Files:** create `runtime/routing_gates.py`, `tests/test_routing_gates.py`; modify `runtime/routing.py`, `runtime/usage_budget.py`, `scripts/routing.py` and the read-only accessors `runtime/delegation.py` (`routing_view`, `routing_etag`), `runtime/state.py` (`ChangeWatch`), `runtime/workstreams.py` (`setting_marker`).

- [x] Write failing tests: reopening a dependency, changing writer ownership, losing a resource claim, disabling API Usage or changing the credential binding after `prepare` produces zero sends, even on a profile/ranking cache hit.
- [ ] Run, implement, re-run; expected PASS (done on the package; not yet observed on the OpenCode host). This is the former P0.10 exit on the OpenCode host.

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
  - its `cwd` is tied to the service's own project by running the command with `--project <checkout root>`. With the pin, the reader compares the checkout root the locator actually resolves to (realpath) with the realpath of `--project`, at `prepare` and again at every `dispatch`, before any workflow read. Any other checkout is blocked with `project_mismatch`, including a nested, separately enrolled checkout under the pinned root and a `cwd` symlink swapped after `prepare`. Paths compare exactly, so on a case-insensitive filesystem a differently cased spelling of the pinned root is also refused (fails closed). Without the pin, request input can name any enrolled checkout of the same user. That read returns only digests and reason codes, which is acceptable before C.3. C.3 must make `--project` mandatory for production. In the [C.3 design](../specs/2026-11-10-smart-routing-c3-wiring-design.md#33-observation-collector), the service refuses an unpinned workflow-bound `prepare` with `project_required` (C3-0b, no #192 dependency), and the launcher reuses the same code (C3-1). Production in-process launches build their service only through `scripts.routing.managed_service(checkout, scope=None)` (C3-0b); delegation passes its canonical scope, and for a managed project `prepare` builds the observations itself (`observations` becomes optional in `FIELDS`);
  - the binding is made after delegation's own `prepare_assignment`, with its `attempt_id`, so the binding covers the reserved claims and the brief.

  The coordinator profiles exactly `delegation.routing_job(scope, locator)`, the canonical semantic job (role, scope reference, planned claims, plus the bound assignment's objective and context), adding only a supplied `profile`. Without `attempt_id`, the canonical job has no objective, context, tools or requirement floors. The context-token estimate then falls back to the supplied profile, explicit floors are not bound, and brief or tool changes are detectable only through a bound assignment.
- **Capacity.** `MAX_ADMISSION_BYTES` is 6,656 (4,096 plus `MAX_BINDING_BYTES`). The worst-case ledger in the capacity tests is 10.53M of the 12M hard limit (87.8%, within the 90% margin). Python 3.11+ as for the rest of the runtime; 3.9 cannot import it.

### Task S3.6: Lineage, settlement and retention

**Status (10 November 2026):** Partial (#142, #143, #144, #146): the accounting core, OpenCode accounting and operator reconciliation are done, and subscription settlement through `opencode_accounting` was observed live (re-run and the 20-admission pilot). Missing (S3.6b box): retiring reflected settled charges, which needs balance observations carrying a provider-declared `reflects_through` watermark bound to the organization and pool epoch, plus a settlement time per charge; until then headroom errs low on API-funded routes (#144). Missing (last box, P0.8): `generate` requests, compaction, retries, message paging with deletion or revert, a real sequence gap, coordinator, review and repair stages, API-funded holds, and what `durable.version` counts, since every live aggregate flagged `version_changed` with zero resync deltas (#143, #145).

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
- **Capacity.** The worst-case ledger record is 10.67M of the 12M hard limit (88.9%; 352 bytes of settlement per hold); S3.6b-2 adds the `over_bound` marker S3.6a's worst case omitted (at most one per run): 10,698,684 (89.16%). S3.6c adds the `window_debits` table (32 keys at their bound): 10,716,910 (89.31%). The margin to the 90% test ceiling is thin: do not add per-hold or per-entry fields without recomputing the worst case with every settlement field at its bound. Events and root aggregates live in two further tables of the same SQLite file, bounded by row count and row size.
- **Pre-canary.** S3.6b lands before managed clients, or every `cost_unknown` hold is resolved (`status` shows none). Resolve every unconfirmed overrun (`resolve_overrun`), every `cost_unknown` hold (`resolve_cost_unknown`), every uncertain dollar hold (`reconcile_uncertain`), every uncertain subscription hold (`reconcile_subscription`) and every over-bound S1 call and legacy block (`resolve_block`) before the canary: `status` shows `uncertain_dollar` and `cost_unknown` empty, `reconcile.reconcile_subscription.count` 0, no `overrun` reason, `blocked_organizations` empty and `reconcile_hint: null`. `reconcile.window_debits` may still list debits: they clear from telemetry, in a one-shot `prepare` or from a collector-built snapshot of a managed project (never under `--serve`, C3-0a), and need no operator action, except that `drop_debit` removes one that never clears (a rolling window). Settled charges double count against observed balances until a watermark-carrying observation exists; headroom errs low. A managed client must send `usage` with `cost_usd_micros` on `complete` for every dollar-funded attempt (else the hold stays `cost_unknown` and keeps its concurrency place), a stable `event_id` per lifecycle event, and `receipt` ids where the host has them; settlement figures are package-tested only until S3.6b and P0.8 re-qualification. OpenCode accounting on its own trusts the launcher's `children` and `lineage` (a child left out of both, or lineage that lies about a parent, settles complete with that child's spend uncounted). S3.3c adds the shim's `routing.descendants` RPC and the `shim_descendants` cross-check, which flags a shim-recorded child the launcher missed (`child_session_unaccounted`), an incomplete shim set (`shim_lineage_incomplete`) or a missing one (`shim_lineage_not_checked`), each making the API cost unknown. API-funded managed attempts are qualified only when the launcher uses the shim cross-check AND it has been observed live with the [required positive and negative runs](../../../clients/opencode.md#plugin-rpc) (not yet observed). Subscription routes are not affected in dollars.

**Contract (S3.6b-2).** Package-only; no model, OpenCode, network or credential access. Responses stay `v:1`. Two operator ops, each a dedicated command op that `record` can never reach (their kinds and fields are refused there). Every operator op (these two, S3.6a's `resolve_cost_unknown` and `resolve_overrun`, S3.6c's `reconcile_subscription`, and C3-0a's `drop_debit` and `settle_legacy_call`) is one-shot only: `--serve`, the shim's persistent child, refuses them with `operator_op_not_served` before reading anything, so a plugin or worker that can reach that child can never settle, release or unblock. Since C3-0a (#215) every in-process service, `managed_service` included, refuses them the same way; only the one-shot command's `main()` can run one. Each is idempotent by `event_id` in the S3.6a event table: the same payload returns the original outcome with `duplicate: true`, another payload is `event_conflict`, refusals are not remembered, and 15 racing processes apply one effect. The event row is the audit (ids, labels, integer totals, a receipt digest; never receipt ids, tokens or content). Each op changes only its own source record.

- **`reconcile_uncertain{attempt_id, evidence, event_id}`.** Only an open `uncertain` dollar hold (else `hold_state_forbidden`; a closed one is `attempt_closed`, an unknown one `unknown_attempt`). `evidence{kind: receipts, receipts, cost_usd_micros}` (1 to 32 distinct receipt ids of at most 128 printable characters, an integer cost of at least 1 micro-USD within the micro-unit bound) settles it like a measured completion through the S3.6a settlement code: charged as reported and the unspent rest of the reservation released on the operator's word, a cost above the reservation an `overrun` awaiting `resolve_overrun`; `resolution{basis: receipts, receipts, receipts_digest}`. `evidence{kind: charge_reservation}` charges the full reservation and releases nothing (`basis: reservation_floor`, `cost: null`). Any other kind is `evidence_unsupported`, any other shape `invalid_request`. A client's `complete` on an uncertain hold stays `reconciliation_required` (S3.6a), so the hold keeps its full exposure until the operator acts; afterwards every event and `check` of the attempt is `attempt_closed`, so the first resolution wins and nothing is charged twice. Receipts are the operator's word, not verified offline, so the release they allow is only as good as that word: the operator stops the worker first, and a request still in flight when the hold closes is covered only if its receipt is listed. A zero cost is `evidence_invalid` (it would release the whole hold without spend, which is the refused no-spend proof); the same rule now applies to a receipt-backed `resolve_cost_unknown` (without receipts a zero still charges the reservation floor).
- **No release without spend.** A `no_spend_proof` (a zero-cost provider usage report covering the attempt) is refused, because no offline shape is safe: nothing local can authenticate an operator-typed report or its digest; the ledger stores no time for the uncertain mark, and a bare `record dispatched` none for the dispatch; a worker can still spend after the mark, so a window ending there proves nothing, and a report observed now cannot cover now; and an `observed_at` after the window's end does not prove the report reflects the window (provider reporting lag, spec §8). A safe proof needs a trusted collector recording the provider's report with a provider-declared completeness watermark at or after the hold's close, in a two-phase close-then-prove protocol: future work.
- **`resolve_block{organization, reason, decision, event_id, ids? | marker?}`.** `over_bound` takes `ids` (1 to 16 distinct run ids, as `status` lists them); every id must be an over-bound run of the organization, else `block_not_pending` and nothing changes. `confirm` sets each call's cost to the reported amount (the excess enters R), `dismiss` keeps the maximum the books already held; the call completes and its run closes, since its pre-send bound failed. `legacy` takes `decision: confirm` and `marker` (`lb_` plus 32 hex): a digest of the ledger identity, the organization, its policy revision and its unresolved items (open holds, unconfirmed overruns, open S1 calls reserved, in flight, unresolved or over-bound), shown only by `status`. Any change to those items makes it `marker_stale`, so a block is never cleared blind or against a listing that moved. Overruns keep `resolve_overrun`; any other reason is `invalid_request`; an unknown organization `organization_unconfigured`. The outcome reports `charged`, `dropped`, `resolved{ids, count}` and `remaining_reasons`. The block stays derived: the organization is admissible only once that set is empty.
- **Capacity.** Neither op adds a field to the ledger record: `resolve_block` removes `over_bound` or `legacy_block` and sets an integer cost the worst case already counts, and settlements reuse the S3.6a `resolution` labels. The worst case in `CapacityTests` now also carries the `over_bound` marker that S3.6a's construction omitted, on one call per run (an over-bound count call never gets a generation call): 10,698,684 of the 12M hard limit (89.16%) against the 90% test ceiling of 10,800,000. S3.6c adds the `window_debits` table (32 keys at their bound): 10,716,910 (89.31%). Avoid further per-hold or per-call fields without recomputing it.
- **Status.** `ledger.reconcile{reconcile_uncertain{count, ids, more}, resolve_block{over_bound{organizations[{organization, count, ids, more}], more}, legacy{organizations[{organization, marker, unresolved{count, ids, more}}], more}}}` (16 ids and 16 organizations per list) and a fixed `reconcile_hint` text, null when nothing is pending.
- **Reflected debits (deferred).** Spec §8 removes a settled charge only with evidence that the observed balance reflects it. No observation shape carries that evidence: `Ledger.observe`, Usage Routing's `observe` (`balance`, `observed_at`, `source`, `pool`, `expiry`) and `usage_admission.Grant` have no usage cutoff, pool-window epoch or source watermark, and the ledger only ever lowers a balance. So no settled charge is retired, and a newer `observed_at` retires nothing (tested). To enable it, the provider adapter must supply, with each balance observation from a qualified source, a provider-declared `reflects_through` (the time through which all billed usage is in the balance) bound to the organization and the pool or window epoch. The ledger would also need a settlement time per charge (today charges are aggregated per total, cycle and task) and must recompute the capacity worst case for it; retirement would then run once per charge with an audit event and never touch open, uncertain or `cost_unknown` exposure.

### Task S3.6c: Subscription hold reconcile (follow-up, required before C and before any production use)

**Status (10 November 2026):** Partial (task added in #145; operator op in #192; pre-C requirements in C3-0a, #215; launcher wiring in C3-3, #218): `reconcile_subscription`, the three pre-C requirements and the OpenCode launcher, which records `uncertain` on a lost acknowledgment and names `reconcile_subscription`, are merged and package-tested. Missing: the lost-acknowledgment drill observed live on OpenCode, a [pre-canary requirement](#pre-canary-requirements); the harness's `drill` mode (#234) runs it.

Before #192 an uncertain subscription (window) hold had no operator recovery path: `reconcile_uncertain` covers dollar holds only (`hold_state_forbidden`, observed in the live re-qualification), so such a hold kept its concurrency place for good.

- [x] Define and implement an operator path that settles an uncertain subscription hold, releasing its claims with the observation recorded and never charging dollars. It must be idempotent by event id and one-shot only, like the other operator ops. It is required before cutover (Task C) and before any production use of enforced routing; cutover readiness also needs the launcher wiring and a live drill, and the pilot may keep its dedicated canary ledger until the drill is observed live. S3.6c (operator reconciliation, package only): `reconcile_subscription`, PASS in `tests/test_routing_subscription_reconcile.py`; contract: [OpenCode client](../../../clients/opencode.md#uncertain-subscription-holds-s36c). Not observed on the OpenCode host.
- [x] Required before cutover, from the #192 review; owned by slice **C3-0a** (ledger) of the [C.3 wiring design](../specs/2026-11-10-smart-routing-c3-wiring-design.md#63-slices). Done in C3-0a (#215), package only: tested in `tests/test_routing_legacy_settlement.py`, `tests/test_routing_subscription_reconcile.py` and `tests/test_routing_reconcile.py`, not observed on OpenCode.
  - Debit clearing runs only outside `--serve` (one-shot or collector), never in the shim's persistent child. This is a line-neutral `runtime/routing.py` change. As merged, a collector snapshot clears only for a managed project in an in-process service that is not serving, and caller-supplied observations never clear ([design §6.3 C3-0a](../specs/2026-11-10-smart-routing-c3-wiring-design.md#63-slices)).
  - A state version 5 downgrade guard, added after code is extracted from `runtime/usage_budget.py` (at its line ceiling) into `runtime/ledger_versions.py`, so older code refuses a ledger with `window_debits`. Needed before C or the canary.
  - A one-shot `drop_debit` operator op, so rolling-window debits, which never clear on their own, cannot fill the 32-key table.

### Task S3.7: Canary

**Status (10 November 2026):** Pilot done (#145): attempt 2 ran 20 of 20 admissions with no abort ([results](../../smart-routing-validation.md#pilot-canary-attempt-2-9-november-2026-20-of-20-admissions-no-abort)); pre-registered Edit 4 raised its cap from 24 to 28 before that attempt. The pre-registered canary waits on the user and needs the [pre-canary requirements](#pre-canary-requirements) (including the historical replay) and C3-4b first. The C3-3 harness update is merged (#218): in canary mode on a managed project it sends no observations, package-tested only. The user has set where and when the canary runs ([pre-canary item 11](#pre-canary-requirements)).

The canary runs between the [C.3 phases](#task-c3-replace-callers-and-retire-selectors): the phase W slices (C3-0a to C3-6) merge first, and the canary project joins `managed_projects` through the one-shot `set managed_projects` only once the activation gate passes. Phase R (C3-7) waits for C.1 and the user's cutover decision. Before the canary, C3-3 updates the qualification harness: in canary mode on a managed project it sends no observations, so the collector supplies them as in production. The bounded pilot mode keeps its own observations on its unmanaged canary ledger and moves to `--test-inputs` in C3-7 ([design §3.3](../specs/2026-11-10-smart-routing-c3-wiring-design.md#33-observation-collector)).

- [ ] Enable enforced routing for one explicitly chosen project on OpenCode within the S2.7 canary bounds; record admissions, blocks, latency and settlement against the S2.7 criteria, and abort on any bound or abort condition. Other projects and clients continue on current selectors.
- [x] Bounded pilot (approved 9 November 2026, pre-registered in [validation](../../smart-routing-validation.md#pilot-canary-bounded-not-the-pre-registered-canary)): up to 20 subscription-only `claude-agent/haiku#low` admissions in the allowlisted project, with a durable cap of 24 requests and the Smart Routing OFF kill switch on abort. It runs through `tests/qualification/opencode_live.py --canary` and does not satisfy this task; cutover stays blocked.

### Task C.1: Confirm the cutover criteria

**Status (10 November 2026):** Waiting on the user (cutover). It also needs the S3.7 canary and the strict replay over the pilot's and the canary's admission records ([validation Edit 6](../../smart-routing-validation.md#edit-6-10-november-2026-replay-against-the-available-history)).

C.1 comes after the C.3 phase W slices and the canary, and before phase R: C3-7 starts only once C.1 passes and the user decides on cutover ([C.3 phases](#task-c3-replace-callers-and-retire-selectors)).

- [ ] Confirm that the strict replay and the S3.7 canary meet the S2.7 pre-registered criteria as amended by [Edit 6](../../smart-routing-validation.md#edit-6-10-november-2026-replay-against-the-available-history); quality re-execution runs only as registered by a dated edit there before C.1. If the criteria fail, stop and report; current selectors remain.

### Task C.2: Feature contracts, one group per task

**Status (10 November 2026):** Partial (#197; markers removed in #212, #216, #217, #221, #225): `tests/test_smart_routing_features.py` holds 36 passing contracts and 2 `NEEDS_C3` expected failures (C3-7 ×2; listed on the [C.3 marker box](#task-c3-replace-callers-and-retire-selectors)). C.2c and C.2e are done; C.2a, C.2b and C.2d each name their gap.

**Files:** create `tests/test_smart_routing_features.py` with adapter doubles, extended by each group.

- [ ] C.2a F01, F02, F15, F17: zero inference and zero secret access on startup, status, naming and diagnostics; explicit OFF survives re-onboarding; readiness is reported separately from enablement. Covered (#197): zero inference or secret access on status and verify; explicit OFF survives restarts, later setting changes and one real onboarding birth plus startup acquisition in the same isolated HOME (mutation-proven); readiness reported separately from enablement. Gaps: F15 titles and visibility and F17 typed readiness in startup and doctor (see [follow-ups](#follow-ups-from-merged-work)); with Smart Routing ON and no usable table the not-ready reason is not visible (see [follow-ups](#follow-ups-from-merged-work)); onboarding is exercised only as a fresh birth, not a setup update or repair of an enrolled project.
- [ ] C.2b F03, F11, F12, F16: one task graph; unchanged continuations reuse profile and child but get fresh admission; resource and funding gates bind the same job revision. Covered (#197): one graph; parallel ready jobs; dependent and conflicting jobs wait; profile reuse without Jev; a continuation gets fresh admission; a changed brief, role or scope invalidates the selection; resource and funding gates bind the same revision; a quota reset is not authority. Gap: F11 child reuse (the same child session on an unchanged continuation) has no routing-level surface on `main`. Its C3-0b and C3-2 markers are removed (#212, #217).
- [x] C.2c F04–F07: API Usage stays opt-in; OpenRouter keeps its own funding; proposals gain no new permissions; the V1 worker qualification does not authorize V2 editing. Marked: C3-7 ×2 (routing through `prepare`); C3-4a's and C3-4b's markers are removed (#216, #225).
- [ ] C.2d F08, F09: Council runs only on explicit invocation and keeps its members, groups, independence and anonymity; project review stays scoped and read-only. Covered (#197): F09; for F08, only that routing has no Council operation or setting. Gap: Council is a checkout feature, so the rest of F08 (explicit invocation, members, groups, independence and anonymity) has no contract. The C3-6 marker (reviewers routed through the command) is removed (#221).
- [x] C.2e F10, F13, F14: exports are sanitized and opt-in; review independence is kept; routing success grants no merge, push or cleanup authority.
- [ ] For each group: run `python3 -B -m unittest tests.test_smart_routing_features`. Contracts that hold on `main` pass; a contract that needs C.3 carries `@NEEDS_C3('<slice>')`, which counts only an assertion failure as expected. The slice that makes a contract pass removes that contract's marker line, and only its own, in the same PR; an unexpected pass fails the module ([design §6.1](../specs/2026-11-10-smart-routing-c3-wiring-design.md#61-shared-file-protocol)). The marker map, with the project-pin marker under C3-0b, is in [design §6.4](../specs/2026-11-10-smart-routing-c3-wiring-design.md#64-c2-marker-map-and-pr-197-amendments) and on the [C.3 marker box](#task-c3-replace-callers-and-retire-selectors).

### Task C.3: Replace callers and retire selectors

**Status (10 November 2026):** Partial. Designed (#194; [C.3 wiring design](../specs/2026-11-10-smart-routing-c3-wiring-design.md) v1.8 records the merged behaviour and the coordinator decisions). Phase W's code is complete, package only: C3-0b (#212), C3-1 (#213) with C3-1b (#220), C3-5 (#214), C3-0a (#215), C3-4a (#216), C3-2 (#217), C3-3 (#218), C3-6 (#221) and C3-4b (#225), with the review follow-ups #222 and #224. The pre-live slices are merged, package only: `seed_window` (#228), the pre-decision-4 fixes (#229), the Codex rate-limit reader (#231), the live-session harness modes (#234) and steps on every agent (#239) ([design v1.8](../specs/2026-11-10-smart-routing-c3-wiring-design.md#8-revision-history)). Nothing has run live, and no project can be activated yet: the activation gate fails only on the route manifest (user decision 5), whose PR #227 stays open until demand is measured live. C3-7 waits on C.1 and the user. Merged work adds launcher requirements defined elsewhere: the [S3.5 wiring list](#task-s35-fresh-gates-at-dispatch-rf3) (`--project` mandatory, `workflow` binding), the S3.1 handshake pre-canary checklist (`authority_required`, `unstamped_open_holds: 0`), the S3.6 pre-canary descendants cross-check for API-funded attempts, and the S3.3d rule that launchers never invoke project commands in a managed session.

**Files:**

- Create `runtime/ledger_versions.py`, `runtime/routing_launch.py`, `runtime/routing_observations.py`, `runtime/routing_routes.json`, `runtime/delegation_routing.py`, `runtime/opencode_launcher.py` and `scripts/opencode_launch.py`. New code goes into new modules, so files already at their line ceilings do not grow.
- Modify `scripts/routing.py`, `runtime/routing.py`, `runtime/usage_budget.py`, `runtime/delegation.py`, `runtime/evidence.py`, `runtime/workstreams.py`, `runtime/external_delegation.py`, `runtime/routing_settings.py`, `runtime/subscription_reconcile.py`, `runtime/usage_routing.py`, `scripts/usage_routing.py`, `runtime/usage_codex_host.py`, `runtime/usage_canary.py`, `runtime/usage_canary_host.py`, `scripts/usage_codex_host.js`, `scripts/openrouter_proposals.py`, `scripts/openrouter_workers.py` and the qualification harness under `tests/qualification/`.
- Modify `policies/03-agent-delegation.md`, `clients/delegation.md`, `clients/delegation-requests.md`, `clients/evidence-review-prepare.md`, `clients/external-delegation.md`, `clients/opencode.md`, `docs/policy-sources.md`, `clients/instruction-map.json`, `skills/review-my-project/`, `skills/manage-usage-routing/` and the affected OpenRouter and Workstreams skill texts.

Each slice's owned files and tests are in [design §6.3](../specs/2026-11-10-smart-routing-c3-wiring-design.md#63-slices), and [design Appendix A](../specs/2026-11-10-smart-routing-c3-wiring-design.md#appendix-a--inventory-at-the-pinned-base) gives every changed file one owner.

**Phases** ([design §4.1–4.2](../specs/2026-11-10-smart-routing-c3-wiring-design.md#41-two-phases)). Phase W: slices C3-0a to C3-6 add the managed path and merge before the [S3.7 canary](#task-s37-canary). A project becomes managed only through the one-shot `scripts/routing.py set managed_projects`, and only once the activation gate passes; every other checkout keeps its current selector. Phase R: C3-7 retires the legacy selectors, and it runs only after [C.1](#task-c1-confirm-the-cutover-criteria) passes and the user decides on cutover.

Every slice routes its selections through `prepare_assignment` (`scripts/routing.py prepare`) and keeps endpoint, scope, permission, acceptance and explicit user controls as constraints. One box per slice; the scope, tests and markers of each are in [design §6.3](../specs/2026-11-10-smart-routing-c3-wiring-design.md#63-slices) and the order in [design §6.2](../specs/2026-11-10-smart-routing-c3-wiring-design.md#62-dag).

- [x] **C3-0a, ledger** (after C3-0b). Done (#215), package only: the [S3.6c pre-C requirements](#task-s36c-subscription-hold-reconcile-follow-up-required-before-c-and-before-any-production-use) (state v5 and its downgrade guard, debit clearing only outside `--serve`, `drop_debit`), the one-shot `settle_legacy_call` for every open legacy S1 call, and `status.ledger.legacy_outstanding`. Before #215, an S1 call that a crash left `dispatching` or `unresolved` had no operator path to settle it: only the legacy send path called `Ledger.complete`, and `Ledger.reconcile` only reported; `settle_legacy_call` now covers every open state ([design §4.4](../specs/2026-11-10-smart-routing-c3-wiring-design.md#44-settle-outstanding-legacy-work)). As merged, all seven operator ops are one-shot only and refused by every in-process service, and the operator-op contract rows are in [S3.1](#task-s31-single-routing-command).
- [x] **C3-0b, routing service** (no dependency). Done (#212), package only: the `project_required` refusal from [S3.5](#task-s35-fresh-gates-at-dispatch-rf3), the one-shot `managed_projects` setting behind the activation gate (`managed_path_incomplete` with `failing` and `codes`), service-built observations for managed projects (`observations_not_accepted`, `observations_unavailable`, `seen` echoed), the shared `managed_service(checkout, scope=None)` factory, and `routing_settings.managed_project(path, settings_path=None)`, which counts unreadable settings as managed, with `LEGACY_ROUTE_RETIRED`.
- [x] **C3-1, launcher core, collector and route manifest** (no dependency; runs alongside C3-0b). Done (#213) with its follow-ups C3-1b (#220: shim harness naming, gateway credential from the cached status report, endpoint uniqueness, window unit), package only. The collector is `collect(project, assignment, *, settings_path, ledger_path, source=None)` and the manifest an object ([design §3.3](../specs/2026-11-10-smart-routing-c3-wiring-design.md#route-manifest-shape)). Scope: host-neutral launcher preflight, the observation collector, which reads OpenRouter only through `openrouter_settings.status` and never takes the `admission` lock, the route manifest from [Task 0.3](#task-03-classify-billing-and-terms-p00), the "listed is not entitled" rule and the `managed_project_fixture` body. The rule answers #182, where OpenCode listed models the account could not use (`gpt-5.5` refused with 400 `model_not_found`). It is designed in [design §3.4](../specs/2026-11-10-smart-routing-c3-wiring-design.md#34-listed-is-not-entitled): only the route manifest makes a route a candidate, and a provider refusal writes a `not_entitled` mark (written by C3-3's launcher) that only the user clears.
- [x] **C3-2, native delegation and review** (after C3-0b and C3-1; shared core, so the full suite runs). Done (#217), package only, full suite passed: every native selection mode, continuations and reviewers go through `prepare` bound to the workflow job, and Workstreams dispatch through routing `dispatch`. An explicit or inherited choice is a hard constraint and is never substituted. Coordinator decisions: the managed state is read once per call, a pre-managed replay answers `legacy_route_retired`, and the activation precondition `open_unrouted_assignments` ([design §4.2](../specs/2026-11-10-smart-routing-c3-wiring-design.md#42-activation-gate)).
- [x] **C3-3, OpenCode production launcher** (after C3-0a and C3-1). Done (#218), package only, never run on OpenCode: the managed launcher and its command, with an allowlisted server environment, an inline config and interrupt handling, and the qualification harness update the [canary](#task-s37-canary) needs. Its live probes are [pre-canary requirements](#pre-canary-requirements).
- [x] **C3-4a, external choice and OpenRouter proposals** (after C3-0b and C3-1). Done (#216), package only: proposals obtain one routing admission on the `gateway` route. Routing `prepare` runs before `settings.admission` and passes `revision=seen.openrouter_revision`, so a settings change in between fails closed with `openrouter_preparation_stale` ([design §3.3](../specs/2026-11-10-smart-routing-c3-wiring-design.md#openrouter-lock-and-revision)). Coordinator decision: without a verified provider receipt a hold is charged its full reservation; `begin` and `dispatch` write ahead to the store, and `dispatch` reruns the dollar preflight ([design §6.3 C3-4a](../specs/2026-11-10-smart-routing-c3-wiring-design.md#63-slices)).
- [x] **C3-4b, OpenRouter workers** (after C3-3, C3-4a and C3-1b). Done (#225), package only, never run on OpenCode: workers obtain one routing admission and launch through the managed OpenCode launcher, in the same order as C3-4a; the V1 launch refuses a managed project. Coordinator decision: the run-state code and the managed tests are split into `runtime/openrouter_worker_routing.py` and `tests/test_openrouter_workers_managed.py`. Its known gaps fail closed until user decision 4 and are listed under the [follow-ups](#follow-ups-from-merged-work) ([design §6.3 C3-4b](../specs/2026-11-10-smart-routing-c3-wiring-design.md#63-slices)).
- [x] **C3-5, API Usage** (after C3-0b and C3-1). Done (#214), package only: every API Usage spending entry point refuses a managed project as its first action, with `Controller._run`, `Controller._evaluate` and `request_handoff` as backstops. Old consent never becomes spending authority (spec §10.4 step 4). Coordinator decision: the managed API Usage send path (`prepare` and `dispatch` for API Usage proposals) moved out of C3-5 to a [post-release follow-up](#follow-ups-from-merged-work), because the user deferred API Usage; managed projects have no API Usage route in phase W.
- [x] **C3-6, phase W instructions** (after C3-2). Done (#221), package only: the `Managed projects` section of policy 03 and the client and skill texts that link to it, including `clients/delegation-requests.md`. It is part of the activation gate. Coordinator decision: a project whose coordinator delegates through a host with no managed launcher (Claude Code native subagents) is not activated until user decision 2, and such a coordinator in an already-managed project holds and reports dependent delegation ([policy 03](../../../policies/03-agent-delegation.md#managed-projects)).
- [ ] **C3-7, retirement** (phase R; after all of the above, C.1 and the user's cutover decision; shared core): removes the managed-project branches, every legacy selector, the legacy writer and the `scripts/usage_routing.py` behavior that `scripts/routing.py` replaces, with no compatibility alias and no second ledger writer. It merges only when `legacy_outstanding` is empty, and it keeps historical costs and unresolved ownership.
- [ ] Remove every `NEEDS_C3` marker in `tests/test_smart_routing_features.py` (marker map and `managed_project_fixture`: [design §6.4](../specs/2026-11-10-smart-routing-c3-wiring-design.md#64-c2-marker-map-and-pr-197-amendments); C3-1 replaces only the `managed_project_fixture` body):
  - Remaining (2):
    - C3-7: `test_external_delegation_keeps_no_caller_owned_selector` and `test_usage_routing_cli_no_longer_selects_routes`.
  - Removed (6): C3-0b's `test_a_workflow_bound_prepare_requires_the_project_pin` (#212); C3-2's `test_native_worker_admission_calls_routing_prepare_once` and `test_native_worker_admission_is_bound_to_its_workflow_job` (#217); C3-4a's `test_proposal_begin_obtains_one_routing_admission` (#216); C3-6's `test_project_review_routes_delegated_reviewers_through_the_routing_command` (#221); C3-4b's `test_worker_prepare_obtains_one_routing_admission` (#225).

  The native review admission path also goes through `prepare` (C3-2, #217; tested in `tests/test_delegation_routing.py`, no feature contract).
- [ ] Run the feature contracts, the full `python3 -B -m unittest discover tests` and `python3 -B scripts/validate.py`; expected PASS.
- [ ] Run instruction-loading walkthroughs for Workstreams, API Usage, OpenRouter, Council and project review. Report them separately from package checks.

#### User decisions for C.3

These decisions belong to the user. Each is defined in [design §7.1](../specs/2026-11-10-smart-routing-c3-wiring-design.md#71-for-the-user), with the design's recommendation where it gives one.

1. **Cutover:** whether and when phase R (C3-7) starts after C.1 passes, and whether more projects join `managed_projects` before cutover.
2. **Default ON scope ([C.4](#task-c4-default-on)):** whether default ON covers automatic subscription selection on advisory hosts (Codex and Claude Code native subagents), or whether those hosts use only `worker_choices` until they have a pre-transmission hook. The design recommends allowing automatic subscription selection there, labelled `advisory`, because their funding is window-only.
3. **OpenRouter workers gap:** accepting that OpenRouter workers are unavailable in managed projects until C3-4b merges and the `gateway` route on OpenCode is qualified live. **Earlier decision, 2026-11-10:** the user wanted OpenRouter workers available; C3-4b is merged (#225). **Superseded scope, later on 2026-11-10:** the user approved redesign decision A, deferring production OpenRouter until after release. Gateway mode and item 8 below now gate the first production gateway admission, not the subscription-only canary. The user also approved leaving Codex-native unqualified for the scheduled canary and clarified B: automatic model selection and launch through subscription connections are required in this release; the OpenRouter deferral does not defer that core flow ([redesign decisions A–C](2026-11-10-smart-routing-redesign.md#product-decisions-after-the-root-cause-diagnosis)). The later approved cuts also defer the offline gateway rule and replay fields; [F5.3](2026-11-10-smart-routing-redesign.md#task-f53-the-funding-order) now selects from one subscription shortlist or waits, preserving existing safe refusals and recovery.
4. **API-funded OpenCode qualification:** running the S3.6 positive and negative descendants runs with real API costs, which lifts `api_funding_unqualified`.
5. **Route manifest:** accepting the capability matrix's classifications as the collector's route manifest while observed billing for several routes still waits on the user. Until then the manifest lists no qualified route, and no project can be activated. **Decided 2026-11-10** ("Use matrix, subscription only"); the manifest PR #227 stays open until demand is measured live ([pre-canary item 10](#pre-canary-requirements)). **Replaced 2026-11-10 by the redesign:** the shipped file holds model-free path rules, each machine generates its routes, and a path is qualified by the one-shot `qualify_path` operation ([redesign plan, F4](2026-11-10-smart-routing-redesign.md#slice-f4-path-rules-and-generated-routes)). #227 is marked `[SUPERSEDED]` when F4 merges; its three routes become paths.
6. **First window telemetry:** the collector learns a subscription window only from an attempt that has already settled, so the first managed launch on a route stays `pending`. **Decided 2026-11-10 and built (#228):** the one-shot operator op `seed_window`, with the merged rules in [design §3.3](../specs/2026-11-10-smart-routing-c3-wiring-design.md#seed-window).

#### Pre-canary requirements

Before the canary project joins `managed_projects` ([S3.7](#task-s37-canary)), all of these hold. Each item links to where it is defined. The live items run through the [live session runbook](../../smart-routing-qualification.md#live-session-runbook), which names the harness mode that observes each one.

1. **User decisions 1–6** [above](#user-decisions-for-c3) ([design §7.1](../specs/2026-11-10-smart-routing-c3-wiring-design.md#71-for-the-user)); 5 and 6 are decided. Decision 2 now also decides Claude Code projects: a project whose coordinator delegates through Claude Code native subagents is not activated until it is made ([policy 03](../../../policies/03-agent-delegation.md#managed-projects)). Decision 5 opens the activation gate, which fails until the manifest qualifies a route. The production manifest must declare `window_unit`, give every subscription route's `demand` in that unit, and pass the endpoint-uniqueness rule ([design §3.3](../specs/2026-11-10-smart-routing-c3-wiring-design.md#route-manifest-shape)). Before any `gateway` route is qualified, the managed-proposal money rules hold as merged: a hold without a verified provider receipt is charged its full reservation, and gateway credentials come only from the cached OpenRouter status report, never from a source (`LocalSource.credential` is not used for them) ([design §6.3 C3-4a](../specs/2026-11-10-smart-routing-c3-wiring-design.md#63-slices), [§3.3](../specs/2026-11-10-smart-routing-c3-wiring-design.md#33-observation-collector)); the receipt re-read that could lower the charge is a post-release [follow-up](#follow-ups-from-merged-work). **After the redesign (10 November 2026):** the gate fails until one path is qualified and one route is generated, with the reasons `no_qualified_path` and `no_generated_route`; the window unit and the demand windows are checked on the path rules and the path qualification record ([redesign plan, F4](2026-11-10-smart-routing-redesign.md#slice-f4-path-rules-and-generated-routes)).
2. **`seed_window`** (decision 6): built (#228), package only, so a subscription route with no stored reading can be seeded instead of staying `pending` (`window_unobserved`) ([design §3.3](../specs/2026-11-10-smart-routing-c3-wiring-design.md#seed-window)).
3. **Live OpenCode probes for the managed launcher** ([clients/opencode.md](../../../clients/opencode.md#managed-launcher), "Pre-canary requirement" and "Not yet observed"), all unobserved; the `launcher-probes` mode (#234, A1–A7) observes them:
   - OpenCode honours `OPENCODE_CONFIG_CONTENT`, `OPENCODE_SERVER_PASSWORD`, `OPENCODE_DISABLE_PROJECT_CONFIG` and the other `OPENCODE_DISABLE_*` switches the launcher sets;
   - a hostile project config (`opencode.json`, `.opencode/`) or global config can add no plugin, MCP server, formatter or LSP server, and cannot relax the permissions;
   - a session's read of OpenCode's own `auth.json` is refused, or the user records it as an accepted risk;
   - no second routing plugin loads from the user's global config;
   - models listed without variants (effort `none`) are admitted and launched correctly;
   - the `external_directory` deny given at session creation wins over project and global config;
   - the launcher's fixed `PATH` reaches the provider tools the user's sign-in needs;
   - whether a session's reads outside the project are refused (`external_directory` covers edits);
   - the stored window unit matches the real manifest's `demand` (with decision 5);
   - the reply's text format: a read-only worker's answer is read from `content[].type == 'text'` parts, a shape not yet observed on a real server ([design §6.3 C3-4b](../specs/2026-11-10-smart-routing-c3-wiring-design.md#63-slices)); #234 records whether the key is `content` or `parts`;
   - OpenCode's precedence between server-level and session-level permissions, in both directions ([clients/opencode.md](../../../clients/opencode.md#managed-launcher), #229), and the shape of the 2.2.6 permission answer (#234 assumes an `effect` field; a missing shape fails the check).
4. **The S3.6c lost-acknowledgment drill observed live**: an uncertain subscription hold from a real managed launch is settled with `reconcile_subscription` ([S3.6c](#task-s36c-subscription-hold-reconcile-follow-up-required-before-c-and-before-any-production-use)); the `drill` mode (#234) runs it.
5. **The checklists already defined elsewhere:** the S3.1 [handshake pre-canary checklist](#task-s31-single-routing-command) (`authority_required`, `unstamped_open_holds: 0`), the S3.5 [wiring requirements](#task-s35-fresh-gates-at-dispatch-rf3), the S3.6 pre-canary resolutions ([S3.6](#task-s36-lineage-settlement-and-retention)) and the S3.3d [required live probe](../../../clients/opencode.md#image-generation-and-configuration-edits-s33d).
6. **The canary project is enrolled for Codex**, with a configured or active entry, or the activation gate refuses it ([design §4.2](../specs/2026-11-10-smart-routing-c3-wiring-design.md#42-activation-gate) step 3). This holds for an OpenCode-coordinated canary too, because onboarding records only Codex and Claude Code entries today.
7. **The strict replay over the exported pilot records has passed; it gates the canary** (#230). **The historical replay has run**, after a route is qualified live (decision 5), with its advisory report recorded; its disagreement rate blocks nothing. The canary harness exports canary admission records with their snapshot detail at the next admission once the last export is 12 hours old, and at the end of each run (#234) ([validation Edit 6](../../smart-routing-validation.md#edit-6-10-november-2026-replay-against-the-available-history)). Neither replay has a runner yet: the redesign plan builds the strict one in [F7](2026-11-10-smart-routing-redesign.md#slice-f7-strict-replay-runner-and-the-ranked-canary) and the historical one in [F8](2026-11-10-smart-routing-redesign.md#slice-f8-historical-replay-runner), under a new pre-registered Edit 7.
8. **Deferred under approved redesign decision A: per-launch options, agent steps and OpenRouter ids observed live** (#229, #239; defined in [clients/opencode.md](../../../clients/opencode.md#managed-launcher), "Not yet observed", and [Per-launch options](../../../clients/opencode.md#per-launch-options)). This item gates the first production gateway admission after release, not the subscription-only canary; it remains unobserved. Future gateway work still needs scoped authorization and its missing producers/consumer. The `gateway` mode (#234) observes:
   - OpenCode 2.2.6 honours the inline config's V2 keys: the endpoint pin (`provider.only`, `allow_fallbacks: false`) and `max_tokens` in the model `body`, and `agents.<name>.steps` on every built-in agent, so a run stops at its step count;
   - the shape of `GET /api/agent` on 2.2.6: whether each entry is named by `id` or only `name`, whether it reports `steps`, and whether the hidden `compaction`, `title` and `summary` agents are listed;
   - an OpenRouter `vendor/model` id end to end: attach, the shim's `check`, a model request, accounting with a cost, and the reply text.
9. **The Codex window reader checked live** (#231; [design §3.3](../specs/2026-11-10-smart-routing-c3-wiring-design.md#codex-window-reader)): one run of `scripts/codex_rate_limits.py` with the real Codex login records that Codex accepts the handshake, the real spawn and read time, whether the read rewrote `~/.codex/auth.json` (names and times only), which windows this plan reports (only `7d` is expected), and that the Codex account is the one OpenCode's Sign in with ChatGPT uses. A route `demand` must name only windows the plan reports, or every stale period ends `codex_window_missing`.
10. **Demand measured live** (the user's decision, 10 November 2026): each subscription route's `demand` in the manifest comes from `measure-demand` (#234) over the readings the budgeted live launches already stored, with no extra model call, never from a policy number. The manifest PR #227 merges only with those values. **After the redesign:** the measured demand is recorded per path with the one-shot `qualify_path` operation, and #227 does not merge ([redesign plan, F4.3](2026-11-10-smart-routing-redesign.md#task-f43-activation-gate-the-operator-operation-and-the-runbook)). The ChatGPT pool's readings around A5 need the harness's Codex hook wired (follow-up [Codex readings in the harness](#follow-ups-from-merged-work)).
11. **Canary conditions** (the user's decisions): the canary runs on vibe-workflow itself, on the production routing ledger and settings (approved for the canary only), in a quiet window with no other lanes running. When it ends, vibe-workflow is removed from `managed_projects` with the one-shot `scripts/routing.py set`. The harness runs it on the production state only with `--production-ledger` and an `--approval` whose scope names the canary, never by default; the procedure is [runbook step 8](../../smart-routing-qualification.md#8-canary-on-the-production-routing-ledger-user-approval-canary-only).
12. **The redesign foundation is merged and its table exists** (added 10 November 2026): slices F1 to F8 of the [redesign plan](2026-11-10-smart-routing-redesign.md) are merged; the guidance is published again with rows of work kind, complexity and risk, and the production ranking table is compiled from it; validation Edit 7 is registered; and `scripts/validate.py` reports `model_names: passed` with the real home ([redesign plan, Before the live session](2026-11-10-smart-routing-redesign.md#before-the-live-session)).

### Task C.4: Default ON

**Status (10 November 2026):** Waiting on the user (cutover and default ON).

- [ ] Install with Smart Routing ON by default and explicit OFF honored; API Usage, OpenRouter and observability unchanged (Jev is removed by the redesign). Unready routes report enabled-but-blocked/unsupported, and ranker unavailability uses the deterministic path.
- [x] Rollback is explicit OFF (worker choices through the same allocator) or the previous strategy-table version (`scripts/routing_table.py rollback`, which swaps `strategy-table.json` with the one kept `strategy-table.previous.json`; a second rollback returns to the newer table); ledger limits and claims are preserved ([C.3 design §5.1](../specs/2026-11-10-smart-routing-c3-wiring-design.md#51-rollback)). Met by #211, package only: refresh v1, refresh v2, `rollback`, then a one-shot `prepare` ranks with v1 (`tests/test_routing_table.py`); explicit OFF is the S3.1 `set smart_routing=false`. No real rollback has run.

### Task S4.1: Routing benchmark (former P0.9)

**Status (10 November 2026):** Replaced by slices L3 and L4 of the [redesign plan](2026-11-10-smart-routing-redesign.md#after-the-canary-l1-to-l4); kept as written.

- [ ] Pre-register the routing-role benchmark (selection quality against the deterministic ranker, structured-output validity, high-risk handling, unsupported-option avoidance, latency and availability) and the sample strength, before seeing results.

### Task S4.2: Quality evaluation and promotion

**Status (10 November 2026):** Replaced by slices L3 and L4 of the [redesign plan](2026-11-10-smart-routing-redesign.md#after-the-canary-l1-to-l4); kept as written. Learned scores are no longer deferred: L1 records them and L3 promotes them through a chronological replay.

- [ ] Re-execute the pre-registered, budgeted historical sample under the deterministic and model-ranked policies with fixed acceptance checks; measure accepted deliveries per budget, cost per accepted delivery including failures, review/repair burden and overhead.
- [ ] Promote the model ranker (A2) only if it meets the pre-registered margins; it keeps the A3 fallback, the A5 data rules and spec §5.3 router selection. Otherwise record the result and keep the deterministic ranker.
- [ ] Learned routing weights stay deferred (spec §11).

### Follow-ups from merged work

Open work that merged PRs recorded in their descriptions or reviews and that no box above asks for. Each item names its source PR.

- [ ] **Per-root incompleteness set** (#152). Replace the sticky per-root `lineage_lookup_failed` flag with a bounded per-root set of undetermined sessions. Entries decay, and a later lookup that proves a session belongs to another tree clears it.
- [ ] **`websearch`** (#154). Tighten it for managed sessions, like `webfetch`: it sends query text to a third-party provider.
- [ ] **Explicit owner field** (#140). Have the command name the owner, for example `owner_attempt` on a session-only answer or `session_owned: true` on a strict allow. That would replace the shim's two-answer inference at `attach`.
- [ ] **Observation-cache integrity** (#194). The observation collector trusts local files the user's own processes can write: the routing settings, the observation cache and the entitlement record. The design accepts this as a residual risk and limits it: C3-3 protects the routing state directory from managed OpenCode workers, and advisory hosts get no dollar routes, but any other local process can still change those files ([C.3 design §3.3](../specs/2026-11-10-smart-routing-c3-wiring-design.md#33-observation-collector)).
- [ ] **Operator op access** (#144). One-shot operator ops have no caller authentication. They rely on worker profiles being unable to run the command, so they depend on the S3.3 deny rules.
- [ ] **Not-ready reason without a table** (#196, #197 reviews). When no table exists, `scripts/routing.py` returns an empty ranking with no reason code, and `runtime/routing.py` `_decide` drops `table_unavailable` when nothing is ranked (a waiting `prepare` reports only `worker_choice_unavailable`). Spec §3 requires the enabled-but-not-ready reason to be visible.
- [ ] **F15 and F17 readiness surfaces** (#197). F15: titles and visibility do not show the actual worker route, effort or waiting-for-budget reason. F17: `runtime/startup.py` and `doctor.py` report no typed Smart Routing readiness.
- [ ] **Load-sensitive tests** (draft #198). Wall-clock deadlines on canonical acquisition fail under machine load and are misreported as `qualification_changed` (seen in `test_qualification_before`, `test_evidence`, `test_staged_setup` and `test_native_behavior_owner` during the #192 gates). #219 now reports deadlines distinctly (`observation_deadline`) and no longer fingerprints the development worktrees next to a checkout. #222 removed #198's now-redundant test seam and added a test pinning the D4 busy-text match as exact. Open: wall-clock checks such as `test_qualification_sanitize` still fail under load.
- [ ] **Managed API Usage send path** (#214; required next release by the user's explicit 10 November decision). C3-5 currently refuses managed projects. Build `prepare` → `dispatch` → `Ledger.convert_hold` → `openai_api` with a `check` before each request → `record complete` with the measured cost, and the collector's direct-API credential reader (`LocalSource.credential` returns none today). Apply the corresponding provider binding for Claude as well; preserve shared grant/expiry arithmetic, allowance protections and API Usage OFF behavior. Until then managed projects have no API Usage route ([design §4.3](../specs/2026-11-10-smart-routing-c3-wiring-design.md#43-stop-legacy-admissions)); see the [next-release deliverables](2026-11-10-smart-routing-redesign.md#required-next-release-api-usage).
- [ ] **Receipt re-read for gateway costs** (#216; after the release). A managed proposal without a verified provider receipt is charged its full reservation. Re-reading the cost from OpenRouter's generation endpoint could lower the charge to what was spent; it needs the live API, and no network path exists ([design §6.3 C3-4a](../specs/2026-11-10-smart-routing-c3-wiring-design.md#63-slices)).
- [ ] **Endpoint in the route identity** (#213, #216, #220). The OpenRouter endpoint is a manifest field outside the four identity fields, so two qualified routes that differ only by endpoint are refused as a whole manifest (`route_manifest_endpoint_ambiguous`), and a proposal whose admitted entry names another endpoint is refused (`catalog_mismatch`). Pinning endpoints properly needs the endpoint in the routing identity, a routing-side change ([design §3.3](../specs/2026-11-10-smart-routing-c3-wiring-design.md#route-manifest-shape)).
- [ ] **`observe` against the admission** (#217). `delegation_routing.admitted_route(scope, attempt_id)` exists, but delegation's `observe_assignment` does not yet compare the observed native model and effort with the admitted route.
- [ ] **Native delegation follow-ups** (#217, #219, #221). Four are done in #222, and #222's review follow-ups (F3) are done in #224; only the optional item remains:
  - done (#222): a replayed launch-check or `dispatch` refusal is tagged `routing.reconcile_required: true` in `runtime/delegation_routing.py:prepare`;
  - done (#222): `open_unrouted_assignments` is re-checked after the `managed_projects` write, and the added projects are rolled back when it finds one (`Service._recheck_activation`, `MAX_ROLLBACK = 3`, `rolled_back`; [design §4.2](../specs/2026-11-10-smart-routing-c3-wiring-design.md#42-activation-gate) step 4);
  - done (#222): the activation precondition uses the real scope resolver (`_enrolled_clients`, `_scopes_for`), tested on the real entry path;
  - done (#222): the inline `+ 4` acquisition cap in `runtime/delegation.py` is `startup.OBSERVATION_SECONDS`;
  - done (#224 code, this docs update): `legacy_route_retired` also carries `routing.reconcile_required: true`, and policy 03 names the field;
  - done (#224): the activation gate enforces the Claude Code host rule in code (`MANAGED_HOSTS`, `advisory_host_unmanaged`, `not_enrolled`, `enrolment_unavailable`; [design §4.2](../specs/2026-11-10-smart-routing-c3-wiring-design.md#42-activation-gate) step 3), and policy 03 names the refusals;
  - done (#224): a failed rollback write is reported as `open_unrouted_assignments` with `rolled_back: false`, the failure chained and `invalidated_attempts`;
  - done (#224): `_scopes_for` raises instead of silently returning no scopes when a recorded client's scope cannot be resolved;
  - done (#224): tests for the surviving #222 mutations M1 (a rollback that removes every project, not only the added ones) and M7 (the no-enrolment codex fallback);
  - done (#224): a test that the line ceilings in `scripts/validate.py` cannot be raised silently (`tests/test_line_ceilings.py`);
  - open, optional: narrow `runtime/delegation.py`'s `StateError` handlers to `StateBusy` and `StateDeadline` where a distinct reason helps.
- [x] **Before any API-funded OpenCode or OpenRouter-worker route (user decision 4)** (#225 and its review, F4). Until then the launcher refuses these routes with `api_funding_unqualified`, so each gap fails closed ([design §6.3 C3-4b](../specs/2026-11-10-smart-routing-c3-wiring-design.md#63-slices)). The package work is done, package only; the live checks moved to [pre-canary items 3 and 8](#pre-canary-requirements):
  - done (#229): the OpenCode adapter's accounting accepts OpenRouter `vendor/model` ids through one `MODEL_ID` rule shared with the shim;
  - done (#229, #239): each launch pins the endpoint (`provider.only`, `allow_fallbacks: false`) and passes the output cap and step count through per-launch options; since #239 the step count covers every agent the host lists (`agent_steps_uncapped` otherwise);
  - moved to pre-canary item 3: OpenCode's precedence between server-level and session-level permissions is observed;
  - done (#229): the worker token test also scans the run folders (#225 review mutation M6);
  - done (#229): a worker run that was prepared but never executed is released after `ADMISSION_TTL`;
  - done (#229): a test that recovery skips a live write-ahead entry (#225 review mutation M8);
  - done (#229): `scripts/openrouter_workers.py` is split by responsibility (996 → 741 lines; the managed path is in `runtime/openrouter_worker_managed.py`).
- [ ] **Codex readings in the harness** (#231, #234). `tests/qualification/live_modes.py` `LiveEnv.codex_window` still returns `None` after the reader merged, so `measure-demand` records no ChatGPT-pool reading around A5. Wire it to the read-only reader (`runtime/codex_rate_limits.py`), with no extra model call, before the ChatGPT pool's demand is measured.
- [ ] **Smaller items** from the C.3 merges (#213, #215, #216):
  - #213: `routing_observations.clear_not_entitled` has no user-facing command yet, so a `not_entitled` mark can be cleared only from code.
  - #215: lower `runtime/usage_budget.py`'s ceiling in `scripts/validate.py` from 1,616 to 1,553 lines. Done (#222).
  - #216: `record` reports `record_refused` instead of routing's fixed refusal reason; `tests/test_openrouter_proposals.py` is at exactly 1,000 lines, so the next change must split it. Both done (#225).
- [ ] **Smaller items** recorded as non-blocking in reviews and not re-checked on `main` by this audit:
  - #113: `credit_headroom` without `now` skips lot expiry in `no-limit` mode; `observe(issued=)` has no bound; `top_up` on an unknown run raises `KeyError`.
  - #118: `projected_utilization` accepts a negative `reserve_new`; `expiry_pressure` reads NaN as 0; a constraint with `resets_at <= now` is scored rather than stale.
  - #121: a corrupt persisted table can make `rank` raise.
  - #123: S1 never prunes `runs`, so a ledger refuses new runs at `MAX_RUNS`; organizations and lots are unbounded; every write re-serializes the whole body under the lock; S1 entry sizes ignore escaped characters.

## Appendix C — Self-review

Author self-review of v1.7 on 2026-11-08 against the v1.6 requirements, spec v1.5 and the repo's development rules:

- Every v1.6 requirement is either referenced through section 2, kept in an Appendix B task or amended in spec v1.5 (A1–A13).
- Amendments that change agreed behavior are A1, A2, A6–A9, A11 and A12. The user accepted them on 2026-11-08, with A12 applying the same subscription and API route model to Claude and OpenAI. The visible acceptance changes are cases 7 and 8 (A9) and new cases 23–26. Spec anchors for `api_share` and cases 6, 9 and 16 are unchanged.
- A12 cites an April 2026 vendor announcement; P0.0 still confirms current terms and that key-bound requests draw on the allowance.
- This self-review is not independent approval, and it closes no P0 gate.
