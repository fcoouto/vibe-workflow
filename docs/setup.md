# Harness Improvement Workflow Setup

**Mode**: Native method adaptation (bounded evidence-based comparison in the current agent, inspired by Harness2 — does not execute the Harness2 framework, reproduce its algorithm, or benchmark results).

**Toolkit version**: vibe-workflow session (2026-11-08). Guide reference: `docs/prompts/15-harness-improvement.md` (remote repo: `soumatheusgomes/vibe-coding-toolkit`). Local toolkit not available; using prompt's native method adaptation.

**Rationale**: No existing `docs/07-harness-evaluation.md` or harness smoke cases exist in this project. Per the prompt's default rule: "If no failure justifies a trial, document the reusable invocation and evaluation procedure without inventing a problem or changing working instructions."

---

## 1. Identified State

- **Harness evaluation doc**: `docs/07-harness-evaluation.md` — does not exist
- **Harness smoke cases**: none defined
- **Prompt 14**: not present in this project
- **Observed failures**: none that justify a trial (no broken instructions, memory, context-routing, or tool-use failures detected)
- **Current changes**: session's `.vibe-workflow` unification (31 files renamed from `vibe-coding-workflow` → `vibe-workflow`, committed to `origin/main` at `adf2504`)

Since no failure justifies a trial, no working instructions are changed.

---

## 2. Reusable Invocation & Evaluation Procedure (no trial warranted)

This procedure is documented for future reapplication when a harness failure is observed. It is **not** a currently active trial.

### Procedure: `run_harness_smoke`

```text
1. Identify one observed instruction, memory, context-routing, or tool-use failure.
   — If none exists, stop: document the reusable invocation without inventing a problem.

2. Capture the baseline in a disposable copy outside the toolkit and original project.
   — Do not blindly copy credentials, private caches, or production data.
   — Keep original inputs and the evaluator outside the trial agent's writable scope.
   — Use native independent contexts when available; record unavailable observations as not run.

3. Propose one minimal change to project-owned harness files and test it from the same initial state
   with comparable settings.
   — Keep acceptance criteria and evaluator unchanged.
   — Do not weaken guardrails, permissions, security, or existing quality gates.
   — Run the same checks, inspect the diff, distinguish observed actions from agent self-reports.
   — Do not add dependencies, an orchestrator, global tools, automatic hooks, or a new memory system.

4. Adopt a supported improvement only within the requested setup scope; otherwise retain the original harness.
   — Apply only the chosen relevant diff, preserve custom content.
   — Verify in the target project with appropriate checks.
   — Confirm that setup reapplication will not duplicate sections, skills, MCPs, or scripts.

5. Report:
   — Mode selected
   — Rationale
   — Toolkit/upstream revision
   — Actual client/model and sanitized authentication mode
   — Runtime
   — Cases, inputs, commands/results
   — Adopted or rejected changes
   — Elapsed time
   — Limitations
   — Next action
```

---

## 3. Workflow Reusability

To reapply this setup in the future:

- Copy only this `docs/setup.md` block into the project's existing prompt location when appropriate,
  adapting paths without weakening its boundaries.
- Record its versioned source and invocation in `docs/quality.md` (or this location).
- Update `docs/setup.md` or the canonical equivalent with: mode, rationale, toolkit/upstream revision
  where applicable, actual client/model and sanitized authentication mode, runtime, cases, inputs,
  commands/results, adopted or rejected changes, elapsed time, limitations, and next action.
- Store durable lessons with evidence, scope, verification date, and invalidation trigger in the
  responsible canonical document. Keep sensitive traces private; record tokens/cost only if observable.

---

## 4. Separation of Concerns

Each selected check is marked pass/fail/not run. Do not present one passing task as universal
reliability or promise measured savings. Reapplying setup must preserve the chosen mode and
working configuration, without restarting trials or paid runs automatically.

Keep useful evidence until reviewed, then remove only temporary resources created by this workflow.

---

## 5. Authorized Work Scope

This workflow authorizes **bounded local setup only**. It does not authorize:
- Production access
- External uploads
- Global/client permission changes
- API billing
- Proxy configuration
- Benchmark runs
- Publishing
- Commits, pushes, or messages beyond what is documented herein

---

**Verification date**: 2026-11-08  
**Invalidation trigger**: Appearance of `docs/07-harness-evaluation.md` or observation of a harness  
failure in the project's instructions, memory, context-routing, or tool-use.  
**Next action**: When a failure is observed, re-enter this workflow with the prompt's 4-step trial  procedure, or document unchanged if no failure justifies a trial.