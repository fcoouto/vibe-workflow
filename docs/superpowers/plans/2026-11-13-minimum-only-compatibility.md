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
- [x] Shared comparator red/green.
- [x] Codex usage lower-bound acceptance.
- [x] OpenCode lower-bound acceptance.
- [x] Credentials, workspace and version-only freshness integration.
- [x] Remaining floor proposals and active documentation.
- [ ] Final validation, review and delivery.

No code implementation or installed integration update has occurred at plan creation.
Explicit execution approval supplies the instruction to continue without another
plan-selection round. Coordinator owns shared files, Git and final acceptance;
children get disjoint source/test scopes and no commit/publication permission.

## Execution evidence

The implementation children were subsequently given explicit local task-branch
commit permission in their separate native worktrees, so tests could run on
committed candidates without interfering with coordinator edits.

- Shared helper: missing-module red on `1124540`; 4 tests passed on `b1b5fd6`.
  A later PEP-440 `.dev1` regression failed on `60e9ccb`; `58275da` adds its
  bounded parsing alongside stable, prerelease and postrelease cases.
- Coordinator tests on `857cc9c` reproduced version-only rejection in credentials,
  native catalogs, hook notes and workspace. On `4f04b22`: 188 tests run, two
  optional pinned-native-backend classes skipped, zero failures. Source-pinned
  tests remain historical backend evidence, not production software gates.
- Codex child: native General `ses_e2ac3643affeNktCNR9FFcpmsD`, branch
  `fix/minimum-codex-usage`, commits `253db9f`, `a5d339c`, `61fb039` integrated as
  `acdaa933`, `b4f672e5`, `992d9ffc`. Test-first reader regressions failed before
  minimum-only acceptance. Reported reader 87/87, accounting 26/26 and two changed
  hook regressions passed. A broader child rerun had no final terminal result at
  handoff, so it is not counted as passing evidence; parent runs its own checks.
- OpenCode child: native General `ses_e2ac302e9ffevccpcP9IfvORxp`, branch
  `fix/minimum-opencode`, commits `2542966` through `e991890` integrated without
  conflicts. Child observed red version regressions, then 317 tests passed across
  eight focused modules. Gateway isolation/server equality guards are removed;
  actual versions are separate metadata and real isolation/budget checks remain.
- Parent reproduced obsolete collection-status expectation with the exact
  `ObservabilitySettingsTests.test_project_settings_round_trip_with_compare_and_set`
  test, then aligned it with `codex >= 0.163.0`. A guessed nonexistent test name
  failed discovery first; it is not behavioral evidence. A 120-second settings
  module run was stopped and all its units were terminated, not claimed passed.
- Research child: native General `ses_e2ac29914ffeGqyOX7ajCOVmGQ`, read-only at
  `b1b5fd6`, supplied official introduction evidence. Proposed Git 2.36.0, gh
  1.9.0 for JSON inspection / 2.13.0 for conditional merge, RTK per-client
  0.30.1 / 0.50.0 / 0.52.0. These remain proposals, not newly imposed gates.
  Skill plugins and remote Context7 have no justified numeric floor yet.
- Final integrated source at `c553efe7`: package validation and diff whitespace
  checks passed, retaining the existing package scan-size warning. No installed
  software was updated or probed for live qualification. Keyring API guidance was
  checked through Context7 `/jaraco/keyring`; native backend selection, priority
  and password-method contracts remain, without fallback discovery.

Current acceptance requires parent checks and independent review of the final
integrated candidate. All review/worker settings used native configuration; no
model override, paid route or credential access was introduced. Remaining
software-version comparisons in receipts and upgrade recording bind historical
facts; schema/content checks are deliberately not removed.

## Final review and integration

On frozen candidate `4e7a402`, the parent ran 35 modules as 36 units: 1,149 tests,
three skips and zero failures in 330.8 seconds. Skips were two optional
source-pinned credential-backend classes and one filesystem-alias-dependent
qualification case. Private-HOME writes in two qualification modules stayed
inside the runner's disposable homes. This was selected coverage, not a full run.

Independent native reviewer `ses_e2ab23643ffeghYF4iYD607N4W` covered all 39 changed
files and connected identity/guard code. The reviewer reported one medium
compatibility finding, no substantiated security issue, and no declined file.
F1: `_qualified_child_patch` still equated observer and child software versions;
this gates an incoming event, not historical receipt integrity. Parent verified
the caller reaches `state.process_event` and denies an otherwise exact approved
patch. Fix accepted: compare only the supported minimum and retain all parent,
task, model, effort, canary, ownership and replay checks.

Regression `CooperativeQualificationTests.test_guarded_child_patch_accepts_differing_supported_observer_and_child_versions`
failed on `eb594d0` with `deny != pass`. On `5af9533`, both complete qualification
cases passed (same release and differing supported releases), including below-floor,
wrong-parent and repeated-patch denials. No second whole-review round is needed
for this narrow, reproduced correction; affected guard checks are rerun below.

Integrated current main `bb245f442784099e1cfabf54388125803e30d235` without conflicts.
Its disjoint changes qualify escalation-gate mechanical/TUI paths; no compatibility
implementation file changed. Preserve the 1,149-test evidence for unchanged leaf
behavior; rerun shared guard/delegation, admission, instruction and escalation
integration checks on the merged candidate. [PR #288](https://github.com/fcoouto/vibe-workflow/pull/288)
remains draft until those checks and final package validation pass.

The first integrated guard check run on `0194be5` was explicitly interrupted and
all units stopped after package validation found `runtime/delegation.py` three
lines over its fixed 3,358-line ceiling. No passing evidence is claimed for that
interrupted run. Kept the same minimum condition in the existing assignment
filter and reused the module import list, preserving the ceiling without raising
it or dropping any safety check. Package validation then passed. The guard and
integration checks run again on that final committed correction.
