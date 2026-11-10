# All-template Qualification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure every functional template on the two selected subscription models with independently checked, bounded evidence.

**Architecture:** Reuse the private, guarded native runtime; change only the new task's state path and add synthetic cases and evidence collection. Four isolated runtimes execute dependency-ready cells; brief consumers start only after producer collection and preservation checks. Reuse existing successful review/handoff evidence only with matching template hashes and explicit limits.

**Tech Stack:** Existing OpenCode 2.2.6, subscription OAuth, Python standard library, protected Python/Node checks and macOS sandbox.

**Spec:** `docs/superpowers/specs/2026-11-10-all-templates-design.md`

## Global Constraints

- One fresh 1,200-second deadline including preparation; no reset, retry-to-green, paid API fallback, production-template change or global credential mutation.
- Exact `openai/gpt-6-luna#low` and `claude-agent/haiku#low`; preserve coordinator settings.
- Maximum four concurrent isolated runtimes, two per account; 24 sessions, 32 requests and 300 seconds per session maximum.
- Synthetic/public material only; protected checks and source inputs stay outside model write scope.
- Retain every result, including unavailable, interrupted and rejected consumers. Only sanitized evidence is published.
- No full suite or merge; existing PR remains draft and blocked pending independent report review.

## Review Focus

- Original directives mistaken for template author notes: exact original text must remain in existing files.
- Design/Claude shapes hidden by inapplicability: the new fixture makes both applicable and checks actual template reads.
- False check/publication claims: workers must distinguish coordinator-supplied evidence, static inspection and unrun checks.
- Brief authority expansion: consumers retain a separately enforced exact grant and cannot acquire shell/network/Git permissions from prose.
- Deadline/concurrency races: admissions and all owned process groups must stay inside the shared limit; interrupted work is never a pass.

---

### Task 1: Freeze protected cases and native preflight

**Files:** private `.superpowers/templates-all/{runner.py,cases.py,run.py,state.json}`; existing source templates and fixtures remain read-only.

**Interfaces:** `cases.prepare()` produces `frozen.json` with exact prompts, owners, template/fixture hashes, per-template criteria and allowed paths. Existing `runner.Runtime` and `runner.run_one(item)` execute one frozen cell and retain its transcript.

- [ ] Verify existing linked task worktree, source HEAD and unchanged template/criteria bytes; record the approved design and deadline.
- [ ] Preflight both exact subscription routes with zero inference; verify sandbox and request-denial controls using synthetic assertions.
- [ ] Prepare matched cases and test protected check controls before inference. Freeze 20 producers/documentation cells; reserve four dependency-driven consumers.
- [ ] Show the exact changed owner-routing paragraph and new case instructions before execution.

### Task 2: Execute independent cells and brief consumers

**Files:** disposable synthetic runtime copies only; no production source changes.

**Interfaces:** `run.py` consumes the frozen producer cells, emits per-cell `result.json/messages.json`, and releases a fresh consumer only after exact producer scope and input preservation checks. Consumer input is the produced brief plus unchanged synthetic source, not a coached correction.

- [ ] Execute at most four cells, at most two per model/account, concurrently; prioritize new template coverage before repeat documentation cases.
- [ ] Freeze and dispatch each eligible brief consumer once. Retain malformed brief/blocked consumer outcomes rather than repairing the brief.
- [ ] Stop all owned process groups by the deadline. Record success only after independent checks; otherwise record deadline expiry.

### Task 3: Grade, sanitize and publish evidence

**Files:** `docs/qualification/2026-11-10-all-templates/`; no runtime package additions.

**Interfaces:** frozen cases plus retained outputs produce per-criterion grades, a thirteen-template × two-model coverage matrix, matched comparisons and cleanup receipts.

- [ ] Inspect complete outputs; rerun protected source/check/preservation/link controls and verify actual layout retrieval/use per template.
- [ ] Retain earlier failed evidence and reuse review/handoff passes only with unchanged bindings and limits. Distinguish generation, consumption and actual native integration.
- [ ] Remove only evaluation-owned credential/runtime stores after retaining useful evidence; scan public/private artifacts and confirm owned processes stopped.
- [ ] Commit sanitized evidence, run `python3 -B scripts/validate.py` and focused evidence/redaction/link/hash checks, then push and update draft PR #236. No merge or full suite.

## Execution record

Coordinator implements the disposable operator inline and coordinates parallel native evaluation sessions. This is an evaluation, not production feature development; existing negative fixtures and guard self-checks supply regression proof. Existing task worktree and PR are retained. Model-generated briefs and outputs are data under test, never new authority. The approved short design supplies execution authorization; no additional approval pause resets the clock.

Observed outcome: all 24 scheduled sessions completed with four-way concurrency and 138 exact-route admissions; the collection did not qualify before the deadline. [The evidence report](../../qualification/2026-11-10-all-templates/README.md) records each template/model disposition, reused review evidence, consumer-context transport gaps, unequal detail in prose controls and cleanup. No additional inference or instruction repair followed the cutoff. Publication remains evidence-only; independent report review and merge readiness remain blocked.
