# Minimum-only Compatibility Implementation Plan

> **For agentic workers:** Use `executing-plans` for inline integration and bounded native children for independent outcomes. Track steps here.

**Goal:** Accept all software releases at or above supported minimums without exact-version gates.

**Architecture:** One standard-library lower-bound comparator and documented floors. Existing adapters continue to own capability and safety checks. Version metadata is retained without version-only freshness invalidation.

**Tech Stack:** Python 3.11+, standard library, existing Git/native tooling.

**Spec:** `docs/superpowers/specs/2026-11-13-minimum-only-compatibility-design.md`.

## Global constraints

- Floors: Codex 0.163.0; Claude Code 2.1.304; OpenCode 2.2.6; Python 3.11;
  Graphify 0.9.104; optional keyring 25.7.0; optional Habblaud 1.0.3.
- No exact-release allowlist or upper bound. Unknown version remains diagnostic;
  actual required capability/identity/schema failures still block their operation.
- Preserve source digests, trust, historical receipts, permissions and ownership.
- No installs, live model calls, release or automatic migration.
- Work in existing isolated task checkout on `fix/minimum-only-compatibility` from
  `1f53c2a22ff006ddab2afb6efa61c5d67258adf9`; primary remains on `main`.

## Review focus

- CLI/server release mismatch with identical controls must not cause downgrade.
- A future major release must not hit an implicit upper bound or exact pin.
- An unknown software version must not masquerade as a missing protocol capability.
- Removing keyring's pin must not enable fallback backends or environment redirects.
- Historical binding checks must retain identity/control checks while ignoring only
  software-version differences, not schema versions or changed receipt content.

## Tasks and proof

1. Shared comparison: add `runtime/software_versions.py` and focused tests.
   Interface `at_least(version, minimum) -> bool | None`, where None means unknown;
   `MINIMUM_VERSIONS` contains the approved stable floors. Add tests, commit, observe
   missing-helper failure, implement, commit and require passing tests.
2. Codex usage outcome: lower-bound acceptance in `runtime/adapters/codex_usage.py`,
   affected hook/settings consumers and their tests. Preserve counters, privacy,
   timestamps, lineage and history continuity. Keep legacy gap codes readable.
3. OpenCode outcome: remove exact qualified-version enforcement in its adapter,
   retain actual host version/health/tool safeguards in launcher and tests.
4. Integration: change credential exact pin to minimum; preserve native backend
   safeguards. Ignore software-version-only catalog/startup freshness differences.
   Add client floors to workspace discovery; make package version drift diagnostic.
   Test each change first, without tests that merely repeat documentation.
5. Research/documentation: inspect remaining required commands/APIs, document floor
   evidence and unresolved proposals; align active instructions and dependency spec.
   Do not rewrite historical reports or silently impose unsupported new floors.
6. Validate, commit, run affected modules on unchanged source, independent code and
   security review, publish draft, resolve findings, merge and synchronize primary
   under the recorded permission. Attempt only eligible task cleanup afterward.

## Progress

- [x] Confirm user scope and floors; inspect actual branch/settings and clean primary.
- [ ] Shared comparator red/green.
- [ ] Codex usage lower-bound acceptance.
- [ ] OpenCode lower-bound acceptance.
- [ ] Credentials, workspace and version-only freshness integration.
- [ ] Remaining floor proposals and active documentation.
- [ ] Final validation, review and delivery.

No code implementation or installed integration update has occurred at plan creation.
Explicit execution approval supplies the instruction to continue without another
plan-selection round. Coordinator owns shared files, Git and final acceptance;
children get disjoint source/test scopes and no commit/publication permission.
