# Model Note Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the two stale "existing selection" promises and close the two deferred minors from PR #366.

**Architecture:** Text edits in a shared skill, the product record and the README, plus one test. No runtime change.

**Tech Stack:** Markdown, `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-22-model-note-cleanup-design.md`

## Global Constraints

- Exact replacement text is in the spec's "Changes"; copy it verbatim.
- `README.md` and `PRODUCT.md` change only through the `impeccable` skill.
- `procedure.md` stays client-neutral: no OpenCode tool names.

## Review Focus

- An event with no `tools` key: the hook must not add one or throw, and the router instruction still arrives (Task 1).

---

### Task 1: Test an event without tools

**Files:** Modify `tests/plugin.test.js` (`noted()` ~line 890, "missing or odd tool shapes" ~line 959)

- [ ] **Step 1:** Expose `hook: h.hooks.get("context")` from `noted()`. Add test `an event without tools still gets the router instruction`: router on; `event = { system: [], sessionID: "ses_a", options: {} }`; after `await n.hook(event)`: `assert.equal("tools" in event, false)`, `assert.equal(event.system.length, 1)`, `assert.match(event.system[0].text, /before delegating/i)`. Drop `undefined` from the odd-shapes list.
- [ ] **Step 2:** Run `node --test tests/plugin.test.js`. Expected: PASS (covers existing behavior). Then prove it can fail: temporarily read `event.tools.subagent` unguarded in `index.js` `noteModel`, re-run, expect the new test to FAIL, restore `index.js` (`git diff index.js` empty).
- [ ] **Step 3:** Commit `test: cover a context event without tools`.

### Task 2: Text

**Files:** Modify `skills/vibe-onboard-project/references/procedure.md:186`, `PRODUCT.md:89`, `README.md` (paragraph after "workflow choices are saved in the main checkout.")

- [ ] **Step 1:** Apply the spec's three text changes (README and PRODUCT.md through `impeccable`).
- [ ] **Step 2:** Run `node --test tests/*.test.js` and `python3 -B -m unittest discover -s tests`. Expected: all pass (onboarding and package tests read these files).
- [ ] **Step 3:** Run `python3 -B scripts/validate.py`. Expected: `validation: passed`. Commit `docs: drop the stale existing-selection fallback`.
