# Native session bounds fix

Status: WORKING. User approved the measured native-only fix after the exploratory spike. This source task does not resume or reset the frozen qualification campaign, install a package, change credentials, or make Langfuse requests.

## Scope and decisions

Worktree: `native-session-bounds`, branch `fix/native-session-bounds`, base `206e0576de9eaa20a9023a88e32ad4574e9eea6d`. Primary checkout remains on `main`; its four unrelated untracked client hook/config files are preserved. Effective policy: tracked `AGENTS.md`, development guide and applicable contracts at this base. Ordinary native development; no managed task activation.

Use native-only ceilings of 72 MiB per framed record, 128 MiB per file, and 131,072 rows. A 64 MiB payload needs framing headroom. These finite ceilings intentionally do not promise arbitrary-history support. The spike measured approximately 584 MiB RSS for a 64 MiB retained-message history and 300 MiB for a single large string; bulk parsing can amplify memory. Keep shared hook/RPC/prompt/witness bounds unchanged. Preserve strict JSON, independent expected identity, complete EOF, semantic validation, source stability, and exact byte offsets/hashes. Add a set for turn membership without changing ordered results.

No streaming rewrite, new settings, dependency, resource framework, depth rule or subprocess timeout machinery. All original campaign files and candidate pins remain untouched.

## Plan and acceptance

- [ ] Add regressions first: a 64 MiB framed record through native proof consumers; large files/history and ordered distinct turns; strict malformed JSON, wrong expected identity, late unexpected calls, EOF and exact sources. Exercise chosen boundaries and file-size rejection without changing unrelated limits. Observe the intended old-reader failures.
- [ ] Apply native constants in `runtime/doctor.py` and every transcript file read/raw-line rescan in `runtime/adapters/codex.py`. Keep API and source protections. Prove guarded child, cooperative/wrapped, setup metadata and managed observations handle larger native history using existing software fixtures.
- [ ] Validate, commit, then run affected sharded modules on the committed candidate. Use available Python versions; report minimum-version absence. Run no live native qualification or installation.
- [ ] Obtain independent behavior/security review of the exact candidate, fix relevant findings, and recheck affected proof.
- [ ] Push and open draft `[BLOCKED]` PR using the repository template; establish readiness and merge only under the recorded repository permission and current PR lifecycle. Preserve pending delivery when blocked.

Every step is necessary to fix all native consumers without widening unrelated trust boundaries. Delivery follows the standing commit/push/draft-PR choice; no release archive or installation is authorized. Final results must separate source/package tests from observed client execution. Title baseline readback: `￻▰▱▱▱▱ Observability Impl.`; overall onboarding remains unfinished.
