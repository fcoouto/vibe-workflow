# Template Hook Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Qualify narrowly triggered native hook feedback and measure helper savings separately from hook overhead.

**Architecture:** Reuse the read-only template helper and protected synthetic project inputs. Run disposable native probes, then matched baseline/helper-only/helper-plus-hook tasks in one qualified client with unchanged model settings. Keep the operator private and retain sanitized results on the existing task branch.

**Tech Stack:** Python standard library, installed OpenCode/Codex/Claude Code clients, existing subscription authentication and host isolation.

**Spec:** `docs/superpowers/specs/2026-11-11-template-efficiency-design.md`, including its approved live-pilot extension.

## Global Constraints

- Up to 12 fresh sessions, at most two concurrently, and a 45-minute wall-clock deadline using existing subscriptions only.
- Persist the start and deadline before the first live launch; count unsuccessful launches, interrupted sessions and review. Never reset the allocation.
- Temporary hooks in disposable projects and supported trust flows only. No API billing, trust bypass, global configuration changes or permission weakening.
- Preserve real projects, original fixtures, evaluators, historical allocations, user customizations and coordinator settings.
- Keep helper calls read-only, explicitly selected, bounded and advisory. Silence success, suppress duplicate findings for unchanged content and ignore unrelated work.
- Execute inline in the existing `feat/template-efficiency` worktree. Publish sanitized evidence through draft PR #254; native observations alone do not authorize production registration or merge.

## Review Focus

- A registered hook may never execute or deliver feedback: retain native event and model-visible output evidence separately.
- Unrelated tools or unchanged documents may repeatedly inject context: test bypass, silent success and content-bound deduplication.
- Trust or authentication may silently fall back: verify exact native registration, supported trust and subscription-only routing before model execution.
- Token counters may overlap cached input or omit retries: preserve raw fields and document normalization without double counting.
- A small or failed sample may look cheaper by doing less work: retain all attempts and require identical task scope and independent quality checks.

---

### Task 1: Freeze fixtures, comparisons and bounded execution

**Files:** create private `.superpowers/template-hook-pilot/{cases,runner,test_pilot}.py` and `state.json`; retain sanitized protocol in `docs/qualification/2026-11-11-template-hook-pilot/protocol.md`. Never execute or modify the historical runner's closed state.

**Interfaces:** `cases.py` produces immutable fixture manifests, six task assignments and protected acceptance checks. `runner.py` reserves a session before launch, enforces deadline/concurrency/request limits, and emits per-session receipts. `state.json` owns only this allocation.

- [ ] Inspect historical isolation and request-guard code; reuse only reviewed controls with new paths and state. Check native subscription availability, cached official model guidance and supported trust without inference.
- [ ] Select two task cases from `assets/harness-evaluation/phase-two/{minimal,customized}/input/`: selectively adapt existing canonical product/overview/preparation documents while preserving original lines, commands, preferences and unrelated files. Freeze exact writable paths, selected shapes and requirements before launching.
- [ ] Use the same initial bytes, semantic requirements, source availability and independent checks in every arm. Baseline uses existing selective prose/reader guidance; helper-only adds explicit preparation/check calls; helper-plus-hook adds only the temporary native feedback adapter.
- [ ] Freeze one available subscription model and effort for all six comparisons, preferably through OpenCode if its probe qualifies. Record requested/configured/observed settings; do not substitute another client or model mid-comparison.
- [ ] Fix trial order: minimal baseline/helper/hook, then customized hook/helper/baseline. Start each from a fresh isolated copy; record order and cache state as limitations rather than claiming randomized causal proof.
- [ ] Write operator self-checks first: refuse the thirteenth reservation, third concurrent session and expired deadline; retain failed starts; reject evaluator/fixture writes and unapproved authentication. Verify broken controls fail and repaired controls pass.
- [ ] Implement only the missing operator controls; run `python3 -I -B .superpowers/template-hook-pilot/test_pilot.py` without model calls. Verify denied writes and network restrictions with model-free canaries before launching clients.

### Task 2: Observe native hook delivery and matched task outcomes

**Files:** private per-client adapters and receipts under `.superpowers/template-hook-pilot/`; disposable projects outside the repository; sanitized `protocol.md` and `results.json` under the qualification directory.

**Interfaces:** Each adapter calls existing `check(...)` and `notice(...)` on the frozen selection, then translates only the notice through that client's documented event response. Receipts bind client/version, source hashes, registration/trust observations, calls, usage fields, elapsed time and exit status.

- [ ] Allocate three native probe sessions, six matched task sessions and one independent review session: ten planned sessions. Keep two slots unused unless a diagnosed setup failure or missing observation justifies a bounded diagnostic; no unchanged retries or extra treatment search.
- [ ] Cap each probe at three minutes and each comparison at four minutes, with at most 24 primary model requests per session where enforceable. Disable automatic retries where supported; otherwise record them and hold any route whose request bound cannot be enforced. The global deadline always wins.
- [ ] Start and persist the 45-minute clock before the first live session. Launch native probes with fresh contexts and disposable registration; do not manufacture trust receipts. If a supported trust flow requires the user, hold that client and continue independent work.
- [ ] In each native probe exercise a broken selected document, identical repeated observation, a corrected document and unrelated work. Record loading, invocation, bounded model-visible feedback, silent success, deduplication, original tool-result preservation and invocation time independently.
- [ ] Record adapter error/timeout behavior through model-free controls and native observations where available. Do not describe advisory feedback as blocking enforcement or infer managed guard/Stop/Interrupt qualification.
- [ ] Run six frozen assignments only if the comparison client's native probe and isolation pass. Provide no coordinator coaching or hidden repair sessions. Within-session agent repairs remain permitted and counted.
- [ ] Independently rerun frozen quality/preservation checks, inspect every output diff and retain failures, missing data, changed scope and incomplete work explicitly. Stop further launches at exhaustion or expiry and reconcile pilot-owned processes.

### Task 3: Account, review and publish

**Files:** sanitized `results.json`, `report.md` and reproducible protocol under `docs/qualification/2026-11-11-template-hook-pilot/`; execution record below; PR #254 evidence update.

**Interfaces:** Results retain native raw usage fields plus explained normalized input/cache-read/cache-write/output totals, requests, repairs, quality and hook calls/time. The report separates comparative task consumption, probes/review/operator consumption and unavailable coordinator accounting.

- [ ] Define repair turns as model responses that edit again after a failed check or hook finding; report ambiguous cases separately. Separate local hook time/output bytes from measured model token effects; never turn bytes into claimed token savings.
- [ ] Verify each client's accounting semantics. Do not add cache-read tokens twice when included in input, equate subscription consumption with dollar savings, or replace missing counters with zero. Preserve failed-attempt usage and unknown retry usage.
- [ ] Report per-task baseline-to-helper and helper-to-hook deltas with quality beside them. Aggregate only comparable complete observations; also show total consumed pilot tokens including failed runs and diagnostics, grouped by model. Two task cases do not establish broad savings or reliability.
- [ ] Obtain one independent read-only behavior/security/accounting review within the original session and deadline limits, reserving up to six minutes. Give the reviewer frozen evidence and acceptance criteria; withhold completion if the budget prevents required review.
- [ ] Resolve supported findings without reopening the model allocation. Run only affected operator/helper checks, package validation and `git diff --check`; reuse unchanged 64-test helper evidence with its original binding.
- [ ] Retain sanitized protocol, source identities, failures, observations and limitations; keep credentials and sensitive traces private. Clean up only reconciled pilot-owned resources after retaining evidence, preserving native state needed for unresolved trust or ownership.
- [ ] Commit and push the task branch, update draft PR #254, and report qualified clients, measured deltas, failed/unrun cases and remaining limits. No production hook activation is implied.

## Execution record

- Scope and detailed plan approved in chat on 11 November 2026. Inline execution and one independent final review remain selected. Frozen comparison details are in the [protocol](../../qualification/2026-11-11-template-hook-pilot/protocol.md).
- Resume observation: clean `feat/template-efficiency` at `ca5fcd95d90f56493578806dd02c2b2dc6e13cb6`; existing manual-title pause preserved.
- Installed versions observed before compaction: OpenCode 2.2.6, Codex CLI 0.167.0, Claude Code 2.1.311. Official hook documentation was fetched; Context7 was unavailable with `Monthly quota exceeded`.
- Native trust, effective isolation, hook execution, feedback delivery and token-counter semantics remain unqualified. No live pilot session has launched; no deadline has started.
- Historical helper evidence remains 64 passing focused tests and warning-free package validation on `567fbce`. The pilot changes no helper implementation yet.
