# API Usage Activation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox tracking.

**Goal:** Reuse existing API setup safely, preserve other projects' activation,
and expose the management skill as Manage API Usage.

**Architecture:** Keep the existing settings/controller, ledger and CLI. Make
unchanged policy configuration idempotent and reuse applicable setup during
explicit enablement without copying project authority.

**Tech Stack:** Python standard library, SQLite, unittest, Markdown and YAML.

**Spec:** `docs/superpowers/specs/2026-11-05-api-usage-activation-design.md`

## Global Constraints

- Preserve the user's coordinator, provider, authentication and existing WIP.
- Use one canonical ledger; retain all charges, reservations, funding and caps.
- Activation, enrollment, source permission and qualification remain distinct.
- Status is read-only; no secrets, live provider calls, billing or installation changes.
- Keep existing skill identifier/reference paths and entrypoint within 150 words.
- No automatic enrollment, binding migration, release archive, commit, push or merge.

## Review Focus

- Two scopes using identical policy both retain live consent; real changes still invalidate stale consent.
- Reuse cannot clone source permission, qualification or another checkout's disable/consent state.
- Multiple equivalent scopes do not create false ambiguity; genuinely different setups require selection.
- Expired, stale, smoke-only, malformed or missing-ledger setup cannot become valid regular routing.
- Replayed/stale enable requests and failed admission do not corrupt existing settings or accounting.

### Task 1: Safe setup reuse and discoverable API management

**Files:**
- Modify: `runtime/usage_budget.py`, `runtime/usage_routing.py`, `runtime/usage_settings.py` as needed.
- Modify: `scripts/usage_routing.py` only if the existing enable schema needs CLI support.
- Test: `tests/test_usage_settings.py`, `tests/test_usage_budget.py`, and affected routing/CLI tests.
- Modify: `skills/manage-usage-routing/SKILL.md`, `agents/openai.yaml`, and relevant references.
- Modify: `README.md`, `docs/policy-sources.md`, `docs/usage-routing-validation.md`.

**Interfaces:**
- Preserve existing configure/enable/status inputs and outputs; optional metadata or reuse selection may be added.
- Existing `enable` with explicit revision remains authoritative; target consent is never inherited silently.
- Expose enough read-only metadata to discover/disambiguate reusable setup without accessing Keychain secrets.
- A normal explicit enable request for an unconfigured enrolled checkout may select one compatible setup.

- [x] Write regression tests for unchanged-policy cross-checkout activation and observe the intended failure.
- [x] Make unchanged validated policy reuse its revision; retain validation, optimistic concurrency and real-change invalidation.
- [x] Write failing tests for unconfigured checkout enablement with existing setup, ambiguity, equivalent candidates, stale/expired/smoke setup, permission isolation, disable/revision behavior and retained accounting.
- [x] Implement the smallest reuse path and clear actionable status/errors; keep current binding identity and missing-ledger recovery.
- [x] Update the skill's public heading/display name to `Manage API Usage`; preserve `manage-usage-routing` invocation compatibility and all essential constraints.
- [x] Update current guidance, validation evidence and source-to-contract coverage; describe the exact implemented reuse schema and no repeated mechanical binding prompt.
- [x] Run affected offline Usage Routing tests, `python3 -B scripts/validate.py`, and Skill Creator quick validation. Report exact RED/GREEN commands and results.
- [x] Self-review the complete diff, write the assigned report, and hand off without committing. The coordinator owns independent review and integration.

## Execution record

Baseline: `b01a94a884d21c67a6b284ed32c400992825daa1` from current local `origin/main`.
`python3 -B -m unittest tests.test_usage_settings tests.test_usage_budget` passed
40 tests before implementation. Isolated managed worktree:
`/Users/fcouto/.codex/worktrees/api-usage-activation/vibe-coding-workflow`.

User request already authorizes implementation; continue without another plan
approval. One cohesive implementation owner prevents concurrent edits to shared
state and its management contract. A fresh independent review covers this single
outcome and the complete candidate together; do not duplicate identical reviews.

## Completion evidence

Implementation and one combined repair wave completed. The 190 affected offline
Usage Routing tests passed; package validation and Skill Creator validation
passed with the entrypoint at 145 words. A fresh independent native Astra/high
review accepted the complete candidate after verifying R1-R4 repairs for setup
freshness, ledger recovery, effective mode equivalence and meaningful discovery.
No substantiated findings remain in this scope.

Source stays on `codex/manage-api-usage` in the attached managed worktree. No
commit, merge, publication, installation, account/credential operation or live API
activation was performed. Client execution remains `not_checked`. Existing
binding identities and the internal `manage-usage-routing` invocation remain
compatible. Legacy key names are unavailable; safe connection fingerprints and
existing receipts resolve connection-only ambiguity without secret inspection.

Detailed task evidence and reviewed candidate digests remain in this plan's
ignored `.superpowers/sdd/2026-11-05-api-usage-activation/` workspace because
there is no committed recovery record. Independent reports are kept in the
private external review-evidence directory. Only completion tracking was updated
after the reviewed candidate; runtime, tests and management instructions remain
the reviewed bytes.

## Authorized release continuation

The user subsequently requested merge and release. This supersedes the earlier
source-only no-commit/no-publication boundary for this deliverable. Preserve the
accepted fix, integrate against current main, publish a patch release through the
existing marketplace, and update the installed package without changing routing
activation, billing, credentials, models, hook trust or unrelated configuration.
Keep active and historical package paths available; client refresh and live API
execution remain separately reported.
