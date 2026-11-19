# OpenCode Advisory Selector Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a local, install-ready OpenCode advisory-selector-only package from the approved extraction boundary.

**Architecture:** Extract function-level slices into a small Python runtime and expose one OpenCode tool with additive context. Native account readers retain their safety controls; SDD and the native executor retain all task, role, permission and execution ownership. Package from an explicit file allowlist with a separate fresh state namespace.

**Tech Stack:** Python 3.11+ standard library, JavaScript ES modules and Node built-in tests, OpenCode V2, Git.

**Spec:** `docs/superpowers/specs/2026-11-19-opencode-advisory-extraction-design.md`

## Global Constraints

- Source reference: `56cabcba4cdb801b852c7a02670ea3be29e56ab0` in the retained old task worktree; never import that checkout at runtime.
- New repository base: `697e1797eb0d73cd8ddd559a1a02e78496a5fdd2`; local `main` keeps tracking `origin/main`. The user subsequently authorized checking out the verified extraction branch at the destination.
- Task branch: `feat/smart-router-extraction`; all source edits and commits stay in its native worktree.
- Local-only delivery: no installation, activation, push, PR, merge, release archive or new inference trial.
- No full adapters, roster, task database, ledger, managed launcher, unrelated hooks/skills or gateway/API funding.
- No new dependency, credential copying, polling, daemon, model probe or automatic provider retry.
- Existing credentials, other plugins, permissions, global defaults, old state and private evidence stay untouched.
- Account freshness is 900 seconds; positive exhausted-reading trust is 60 seconds.
- New state defaults to `${XDG_STATE_HOME:-~/.local/state}/vibe-workflow/smart-router`; no implicit legacy-state read.
- Missing/disabled configuration or an empty table means no selection and no native-reader spawn.
- Independent behavior/security review is required before final package acceptance.

## Review Focus

- Account identity changes during acquisition must not carry an old generation or permit a stale route.
- Zero/null idle meters must not create resets/headroom or revive older quota rows.
- External agent names must not require the old specialist roster or change classification floors.
- Another plugin's tools, context, permissions, native execution and results must survive unchanged.
- Missing configuration, unsafe state paths and failed readers must not trigger legacy-state fallback or leak secrets.

---

## File structure and ownership

Production files after extraction:

- `index.js`, `package.json`: dependency-free OpenCode server entry and metadata.
- `runtime/__init__.py`, `runtime/routing.py`: pure profile validation and ranking.
- `runtime/routing_paths.py`, `runtime/routing_observations.py`, `runtime/routing_funding.py`: subscription-only route generation and shortlist.
- `runtime/subscription_billing.py`: account-fact checks and markers, without dispatch.
- `runtime/opencode_account.py`, `runtime/claude_usage.py`, `runtime/native_catalog.py`: selected readers and Claude-only catalog helpers.
- `runtime/storage.py`, `runtime/processes.py`, `runtime/continuity.py`: extracted private-file, process-lifecycle and generation-fencing helpers.
- `runtime/selector.py`, `scripts/select.py`: advisory composition and bounded JSON CLI.
- `config/routes.json`, `config/router.example.json`, `config/strategy-table.example.json`: three path definitions and empty configuration examples.
- `README.md`, `LICENSE`, `assets/logo.png`: user guidance, attribution and existing branding.

Development-only files:

- `AGENTS.md`, `docs/development.md`, `docs/setup.md`, `docs/dependencies.md`, this plan and its spec.
- `scripts/validate.py`, `scripts/package.py`: local boundary validation and export tools; not shipped in the runtime package.
- `tests/__init__.py`, `tests/test_selector.py`, `tests/test_readers.py`, `tests/test_storage.py`, `tests/test_package.py`, `tests/plugin.test.js`, and required invented fixtures.

Before removing legacy tracked files, derive the complete keep/delete inventory
from Git and record it in `docs/dependencies.md`. Remove only tracked files
outside the final source/development allowlist in this task worktree. Do not
delete untracked, ignored or user-owned files. Retain the original task's
policy requirements for this execution even as obsolete product instructions
are replaced; do not weaken a gate by deleting its source.

## Task 1: Standalone advisory selection core

**Files:** Create `runtime/selector.py`, configuration examples and
`tests/test_selector.py`; extract the listed ranking/path/shortlist/fact files.
Create the source/function mapping in `docs/dependencies.md`.

**Interfaces:**
- `runtime.routing.ensure_profile(job: dict) -> dict`: caller-owned `work_kind`, no roster lookup; preserve existing floors and context handling.
- `runtime.routing.rank(profile: dict, table: dict, constraints: dict) -> dict`: preserve table schema 2 and ranking order.
- `runtime.selector.select(request: dict, *, state_dir: Path, source=None) -> dict`: code-injected fake source only in tests; production always selects its native source.
- Results always carry `admission=False`, `reservation=False`, `spend_permit=False`; selected results expose `route`, `profile`, `ranking`, `excluded`.
- Result `state` is `selected`, `no_selection` or `blocked`; the latter two always have `route=None`.

- [ ] Write tests with invented table rows proving exact/stricter-band ranking, high/high/independent missing-field defaults, all six work kinds, context narrowing and rejected malformed inputs.
- [ ] Add a literal three-route fixture proving first eligible ranked route wins; stale/mismatched facts, fresh exhaustion, non-OpenCode paths, API/gateway routes and `provider_managed`/`unknown` overflow never produce a selected route.
- [ ] Add `test_unconfigured_selection_has_no_effects`: absent/disabled configuration and empty rows call no reader, write no state and consult no old namespace.

  ```python
  result = select({"v": 1, "work_kind": "review"}, state_dir=absent, source=fake)
  self.assertEqual(result["state"], "no_selection")
  self.assertIsNone(result["route"])
  self.assertFalse(result["admission"])
  self.assertFalse(result["reservation"])
  self.assertFalse(result["spend_permit"])
  self.assertEqual(fake.calls, [])
  self.assertFalse(absent.exists())
  ```

- [ ] Observe RED against the missing new interface with `python3 -B -m unittest tests.test_selector`.
- [ ] Extract the approved pure slices; remove imports of admission, workflow gates, allocation, ledger and roster. Normalize `work_kind` without mapping or choosing a native agent.
- [ ] Implement the composition matching the tested flow; return fixed exclusion reasons, never execution authority.
- [ ] Commit the task, then run `python3 -B -m unittest tests.test_selector`; require all assertions to pass and record RED/GREEN plus source/function provenance.

## Task 2: Bounded native readers and isolated fresh state

**Files:** Extract `runtime/opencode_account.py`, `runtime/claude_usage.py`,
`runtime/native_catalog.py`, `runtime/storage.py`, `runtime/processes.py`,
`runtime/continuity.py`; add `tests/test_readers.py`, `tests/test_storage.py`
and only the needed fake-native fixtures. Wire Task 1's production source.

**Interfaces:**
- `runtime.opencode_account.refresh(settings_path, *, families=..., force=False, command=None, environ=None, timeout=10.0, need=()) -> dict`.
- `billing_fact(settings_path, route, now) -> dict | None` and `quota_problem(settings_path, route, now) -> str | None` preserve the existing producer contract.
- Claude metadata acquisition retains the existing 8-second bound, zero-inference checks, 300-second failure backoff and explicit idle-window semantics.
- Generic helper moves preserve existing process-group cleanup, file ownership/modes, locks, atomic writes and generation fences.

- [ ] Port focused regression cases using invented identities: disabled/enabled/unknown overflow mapping, mismatched second native binding, interrupted continuity, zero/null idle metadata, invalid active resets and model-scoped exhaustion.

  Pin the idle case with the fixture's explicit zero utilization and null reset:

  ```python
  self.assertEqual(result["windows"], [])
  self.assertEqual(result["reason"], "claude_usage_unconfirmed")
  self.assertEqual(account["overflow"], "disabled")
  self.assertIsNone(account["usage_at"])
  ```

- [ ] Pin the existing Go workspace/provider-header check and usage-limits parser; assert that its meters only exclude exhausted routes and never create an included claim.
- [ ] Add unsafe/symlink state, oversized/malformed JSON, atomic-write failure, concurrent-reader and timeout/cleanup cases; assert no credentials/raw native text reach output or persistent state.
- [ ] Observe RED with `python3 -B -m unittest tests.test_readers tests.test_storage`.
- [ ] Extract only required helper functions from the Codex/ledger modules. Do not retain a Codex executable/protocol dependency or provider-error recovery machinery that the advisory selector never writes.
- [ ] Keep account/catalog/window provenance in the fresh namespace; do not migrate old files or infer an account match from shared pool labels.
- [ ] Commit, then run `python3 -B -m unittest tests.test_selector tests.test_readers tests.test_storage`; require passes on synthetic transports and verify no real reader ran.

## Task 3: Thin OpenCode integration and install-ready export

**Files:** Create `index.js`, `package.json`, `scripts/select.py`,
`tests/plugin.test.js`, `tests/test_package.py`; replace package/validation
scripts and obsolete product instructions; update README, setup/development
guidance and the exact dependency/package allowlists.

**Interfaces:**
- Plugin ID `vibe-workflow.smart-router`; tool name `smart_router_select`.
- `scripts/select.py --state-dir ABSOLUTE_PATH` reads one JSON request, capped at 64 KiB, and emits one bounded JSON result with fixed errors.
- Tool request: `v: 1`, `work_kind`, optional `profile` and `constraints`; no supplied observations, credentials, commands, admissions or launch fields.
- `python3 -B scripts/package.py --output dist/vibe-workflow-smart-router` exports only the production allowlist, README/license/logo and explicit install/configuration guidance.
- `python3 -B scripts/validate.py [--root DIRECTORY]` validates the extracted source/export contract, imports, local paths and absence of legacy package references; it makes no network/model call.
- The plugin passes its actual location directory as the child working directory; the CLI derives scope there, not from untrusted input.

- [ ] Write fake-context tests proving exactly one selector tool and additive context registration; existing tools/instructions remain unchanged, with no permission, shell, model-enforcement, retry or native-subagent replacement.

  ```javascript
  assert.deepEqual(registeredTools, ["smart_router_select"])
  assert.deepEqual(existingSubagent, originalSubagent)
  assert.deepEqual(existingPermissions, originalPermissions)
  assert.equal(nativeDispatchCalls.length, 0)
  ```

- [ ] Add CLI tests for invalid/oversized requests, path handling, reader failure and complete provider/model/effort delivery without native dispatch.
- [ ] Add export tests proving no old adapters, skills, policies, task/ledger code, private state/evidence, absolute machine paths or unapproved imports are shipped.
- [ ] Observe RED with `node --test tests/plugin.test.js` and `python3 -B -m unittest tests.test_package`.
- [ ] Implement the small V2 server entry using the official host API, no npm dependency; preserve bounded process/error handling and cancellation.
- [ ] Replace the broader plugin's tracked content only through the reviewed keep/delete inventory. Keep concise developer instructions and the existing memory load path; retain the original logo/license.
- [ ] Document fresh configuration and advisory limits, the task-worktree source path, install/disable instructions and the fact that installation/activation has not occurred.
- [ ] Commit, then run `python3 -B -m unittest discover -s tests`, `node --test tests/plugin.test.js`, `python3 -B scripts/validate.py`, `python3 -B scripts/package.py --output dist/vibe-workflow-smart-router`, and `python3 -B scripts/validate.py --root dist/vibe-workflow-smart-router`; require all mandatory checks to pass.
- [ ] Perform one credential-free, zero-prompt native plugin-discovery smoke only if safely available. Record native discovery separately from package/fake-context proof; do not run an inference trial.
- [ ] Obtain independent behavior/security review of the committed extracted candidate and export; resolve blocking findings with focused rechecks, without resetting any review/trial budget.
- [ ] Retire only the old primary's remaining routing-list entry using the latest revision and the accepted cached package's supported operator command. Preserve all other entries, qualifications, ledgers and evidence; report unavailable command provenance instead of editing state JSON directly.
- [ ] Recheck both primaries, task branch, old upstream removal, preserved assets, unrelated configuration/state, package inventory and stopped owned processes. Deliver local paths, results and remaining qualification limits; do not install, publish or merge.

## Planning evidence and handoff

- [x] User approved OpenCode advisory selection only, the thin dependency boundary, fresh state, credential reuse and local install-ready delivery.
- [x] Old `main` upstream removal verified; its remote, refs, worktree inventory and file changes were preserved.
- [x] New independent clone matches remote `main`; pre-existing logo bytes and both `.DS_Store` files were preserved.
- [x] Native task worktree created on `feat/smart-router-extraction`.
- [x] Baseline `python3 -B scripts/validate.py`: PASS, with the existing full-plugin bundle-size warning; client execution not checked.
- [x] Baseline `python3 -B scripts/test_sharded.py test_task_profile test_opencode_account test_claude_usage`: 97 tests PASS, 0 skipped, 19.9 seconds; logs retained privately.
- [x] Human approved proceeding; execute inline and use one independent final review.
- [ ] Implementation, independent review, extracted-package validation and old routing-list retirement.

Plan self-review: each approved dependency maps to a task and a final file;
all five Review Focus cases have explicit tests. The public caller supplies
classification, not a roster ID; native dispatch is never an output side effect.
Task 3 owns package deletion and native glue, not Tasks 1–2. No current baseline
result claims the extracted package exists or passes.
