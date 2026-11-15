# Unconditional Stop Execution and Release Plan

> **For agentic workers:** Use `executing-plans` for inline verification work, with independent design/evidence and final release review. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify Claude execution, real native UI behavior and late-completion races, then independently review and fully test the exact release candidate before publishing.

**Architecture:** Keep the merged stop implementation unchanged unless a reproduced defect requires a narrow fix. Reuse the private native fixture, original tools, immutable checks and bounded observation controls. Extend only disposable timing observation for genuine native races; never seed state or fabricate completion events.

**Tech Stack:** Python standard library, TypeScript/Node, installed native OpenCode/Claude and repository release tooling.

**Spec:** User's 15 November request; `clients/opencode-escalation-gate.md`; existing source and qualification report `docs/qualification/2026-11-14-unconditional-stop.md`.

## Global Constraints

- Task worktree: `test/unconditional-stop-release`, from current `main` at `9dea8e51e1b48c19599cc11f001d8be9ad701216`. Preserve the earlier task worktree and evidence.
- Preserve settings, authentication, subscriptions, other sessions, previous failures and consumed allowances. Never reload shared state merely because chats appear idle.
- PR #294 stays deferred, draft/BLOCKED, unmerged and unreleased. No custom approval UI or automatic continuation.
- Existing Claude authentication may be used only through a verified authorized native path. No credential copying, secret inspection, new sign-in, separately billed API route or unapproved shared Keychain refresh risk.
- Native qualification uses fresh exclusive attempt records and finite per-case allocations established before launch. Count all model kinds/actors; no retries or automatic renewal.
- Synthetic public fixtures only. Protect evaluators/configuration; correlate real permission, tool, lineage and completion identities and preserve before/after hashes.
- A timing barrier may delay an original native callback but must not change its fields, fabricate events, invoke production state methods or supply a replacement writer. Label instrumented scheduling separately from uninstrumented execution.
- UI proof must use an actual native frontend. A private standalone TUI is the existing safe candidate; Desktop coverage remains a separate observation, never inferred from API or terminal text.
- Release only after the requested execution evidence and independent review pass, then a successful full Python suite on the exact final committed release candidate, including version and release notes.
- Use `docs/release.md` for version/tag/package/marketplace publication and disposable install verification. Do not update personal installed clients or other projects as a release side effect.

## Review Focus

- Claude bridge tools must reach the same native permission and completion controls; model text alone is not execution proof.
- Private HOME and filesystem sandboxing do not prevent daemon-mediated Keychain refresh. Resolve that boundary before Claude probes or inference.
- Native UI must show refusal without approval or automatic resumption; retain actual frontend provenance and clean/forced-exit results separately.
- A real edit admitted before the stop must deliver its successful completion after the stop; stopped state and subsequent refusal must persist.
- A race harness must preserve genuine tool/call/session identity and distinguish physical write timing from completion-notification timing.

## Task 1: Verify prerequisites and safe execution design

- [x] Observe task/primary identities and preserve source/settings; create a native task worktree from current `main`.
- [x] Run focused baseline checks against unchanged source: 79/79 Node cases passed.
- [x] Independently review the Claude isolation/authentication path without starting a probe or reading credentials.
- [x] Inspect the original native hook ordering and existing TUI harness.
- [x] Record exact acceptance, attempt budgets, ownership and stopping conditions before any native inference.

## Task 2: Observe Claude and native UI execution

- [x] Record the unavailable isolated Claude authentication prerequisite without weakening preservation.
- [ ] Exercise original native Claude read/edit/check/status and child permissions through the current stop implementation.
- [ ] Exercise the final refusal and separate explicit continuation in an actual native frontend; retain rendered output alongside native records.
- [ ] Verify unrelated work, source/protected inputs, selected shared fingerprints, actor settlement and owned process cleanup.

## Task 3: Observe genuine native late-completion races

- [x] Implement only the minimal disposable scheduling barrier after source-backed hook-order inspection.
- [x] Self-test its matching, one-shot release, timeout and no-field-mutation behavior.
- [ ] Observe actual successful edit permission/completion with completion delayed across a genuine final-stop transition.
- [ ] Release the real callback; require identical stopped state and a fresh native edit refusal afterward.
- [x] Independently assess the failed attempt's raw identities, bytes and preservation; retain unexecuted race/UI limits.

## Task 4: Independent review and release preparation

- [x] Update the owning qualification report and client status with observed results and remaining limits.
- [ ] Obtain independent behavior/security/evidence review of the exact candidate; fix only substantiated in-scope defects with regression proof.
- [ ] Select the next unused version after observing current manifests/tags and concurrent release state.
- [ ] Update both manifests and release notes; validate, commit, run focused checks, publish a draft PR and follow ordinary readiness/merge rules.

## Task 5: Qualify and publish the release

- [ ] Pin the exact merged release commit in a clean task checkout.
- [ ] Run `python3 -B scripts/test_sharded.py` without changing that checkout; retain full output and failures/skips.
- [ ] Only after that run passes, validate release eligibility and tag that same commit.
- [ ] Build from the qualified tag, inspect the package/source/version, and fast-forward the marketplace release branch.
- [ ] Verify the released package through a disposable installation. Record publication separately from personal installation/activation.

## Current State

The user explicitly chose **Preserve login** after disclosure of shared Keychain
refresh and separate-lock interference risk. Do not perform Claude probes,
Keychain visibility queries, inference or release while that decision applies.
Astra found no supported Claude path satisfying every preservation constraint.
Continue independently safe public-route UI/race tests and current-candidate
checks; none may substitute for Claude qualification or authorize release.

The single combined public-route UI/race attempt failed at `Timing RPC arm: 400`
after 25 requests and four repair rounds, before race scheduling or frontend
launch. Preserve the receipt; no replay or implicit replacement is authorized.
Owned server cleanup passed. Source/protected/prior-evidence hashes matched,
but shared OpenCode settings changed during the window with unknown attribution.
Coordinator raw checks confirm partial-only coverage; independent assessment
accepted the accurate failure record, not execution qualification. Package
validation and two committed-candidate Python checks passed; the latter include
the unchanged Node suites. The existing bundle-size warning remains.
The failure record is published as draft
[PR #310](https://github.com/fcoouto/vibe-workflow/pull/310), `[BLOCKED]`, unmerged.
No Claude probe, full-suite run or release has occurred. Further native attempts
need a separately authorized allocation after diagnosis; the existing failed
attempt cannot be replayed.

Separately approved model-free diagnosis subsequently reproduced
`rpc.invalid_input` for the timing schema's `sessionID.pattern`. The first
model-free attempt stopped on a premature inventory read; a newly authorized
corrected attempt used a bounded activation wait and captured the untruncated
error. It made three HTTP calls, no model requests, and stopped cleanly.
This identifies a copied-schema failure mechanism, not native race/UI proof or
the missing historical response body. No schema fix or further native attempt
is authorized by that closed diagnostic allocation. Claude and release remain
held; all earlier failed receipts remain preserved.

The user subsequently authorized the one-keyword helper fix and one fresh
three-call/five-minute/no-retry model-free confirmation. A fresh helper copy
removed `actor.pattern`; existing barrier checks stayed unchanged. All 11 offline
checks passed, then native OpenCode 2.2.6 returned HTTP 200 with `armed: true`.
The confirmation used three HTTP calls, zero model requests and 742 ms.
Cleanup, selected shared hashes and prior/source pins matched. This closes only
the diagnostic arm issue; native release/race/UI, Claude and final release
qualification remain pending. No further native call is authorized by that
closed confirmation allowance.
Earlier source and 57-request public-route proof remain unchanged and scoped;
they do not satisfy the newly requested Claude/UI/race observations.
