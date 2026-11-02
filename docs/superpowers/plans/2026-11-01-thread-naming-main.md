# Thread Naming Main Integration Plan

> **For agentic workers:** Use the existing implementation coordinator and one
> bounded Astra/high review. Preserve the already authorized execution method.

**Goal:** Merge only Thread Naming and essential dependencies into local `main`.

**Architecture:** Port the reviewed formatter, presentation store and family
allocator to main's existing schema-2 runtime. Migrate task statuses in their
existing owners, then repair titles at existing command and hook boundaries.

**Tech Stack:** Python standard library, SQLite and Codex App Server JSON-RPC.

**Spec:** [Approved integration scope](../specs/2026-11-01-thread-naming-main.md).

## Global constraints

Keep the dirty primary checkout untouched. Exclude the 88 development-base
ancestors and efficiency candidates. Keep all task statuses uniform and preserve
evidence, ownership, historical pins and manual-title precedence. No new native
generation, trust changes, global installation or push.

## Tasks

- [x] Preserve the reviewed development snapshot on its own branch. Create a
  separate integration branch from freshly fetched `origin/main`.
- [x] Port canonical task states and milestone validation in state, engine,
  evidence and delegation; preserve explicit historical D4 reads. Add failing
  portable state tests first, then run the affected existing suites.
- [x] Port title formatting, persistence and family allocation. Reuse main's
  native process transport and atomic storage. Add failing title tests first;
  verify manual edits, replacement, readback and completion freshness.
- [x] Integrate command/event repair in `scripts/workflow.py`, retaining guard
  results, startup context and continuation limits. Verify actual CLI boundaries.
- [x] Add naming-only Workstreams guidance and update main's existing runtime
  contracts without advertising absent execution/resource features.
- [x] Freeze, run package and main-based tests, then obtain Astra/high review of
  the actual integrated result. Fix reproduced material findings.
- [ ] Commit and merge locally into `main`; verify the merged tree and report
  exact commits, tests and the unverified native-hook boundary.

## Review focus

- Legacy actors and unresolved assignments cannot disappear during admission.
- A stale completed candidate cannot retain `DONE` in its title.
- Manual edits and lost delivery acknowledgments cannot be mistaken for consent.
- Hook failures cannot alter patch guards or consume extra continuation budget.
- Native transport remains bounded, model-free and independent of version labels.

## Source qualification

The final runtime `e1e19514` passed all 513 main-based tests in 244.067 seconds.
Astra/high rechecked both reproduced findings and reported no remaining finding
in that bounded review. The native title read was separately observed; installed
hook callbacks remain unverified. The user authorized the local merge with this
limitation disclosed. The primary checkout and separate efficiency work remain
untouched.
