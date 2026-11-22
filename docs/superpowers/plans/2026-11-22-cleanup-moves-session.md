# Cleanup Moves the Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make cleanup move the chat to the primary checkout before it removes the worktree.

**Architecture:** One skill reference edit (a step and a table column) and one test. No runtime change.

**Tech Stack:** Markdown, `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-22-cleanup-moves-session-design.md`

## Global Constraints

- Wording for the notice, step 5 and the table is in the spec's "Changes"; follow it.
- Client-neutral prose; OpenCode names only in the table.
- No code, hook, `PRODUCT.md` or README change.

## Review Focus

- The removal step must come after the move step in the file (Task 1 asserts the order).
- A client with no move mechanism must be told to ask, never to remove silently (Task 1 asserts the phrase).

---

### Task 1: Move step and test

**Files:** Modify `skills/vibe-pull-requests/references/worktree-cleanup.md` (Order, table), `tests/pull-request-skill.test.js` (after the cleanup-reference test, ~line 55)

- [ ] **Step 1:** Add test `the cleanup reference moves the session before it removes the worktree`: read the reference; assert it includes `Move the session` and `session_move` and `ask before removing`; assert `text.indexOf("Move the session") < text.indexOf("Remove the worktree")` (use the exact step headings below).
- [ ] **Step 2:** Run `node --test tests/pull-request-skill.test.js`. Expected: FAIL on the missing phrases.
- [ ] **Step 3:** Edit the reference per the spec: new notice wording; new step 5 "Move the session"; renumber to 6 "Remove the worktree" and 7 "Verify"; add the **Move the session first** column to the table. Update step 6's text so it says it runs only after step 5 is confirmed.
- [ ] **Step 4:** Run `node --test tests/*.test.js`, `python3 -B -m unittest discover -s tests` and `python3 -B scripts/validate.py`. Expected: all pass, `validation: passed`.
- [ ] **Step 5:** Commit `fix: move the session before cleanup removes its worktree`.
