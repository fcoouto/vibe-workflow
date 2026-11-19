# Project Feature Settings and Plugin Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship one feature-list settings format and register the plugin as `vibe-workflow`.

**Architecture:** Keep settings parsing and writes in `project.js`. Change the OpenCode ID and package metadata without moving router state, adding feature infrastructure, or modifying installed plugins.

**Tech Stack:** Node.js ES modules, Python standard library, OpenCode V2.

**Spec:** `docs/superpowers/specs/2026-11-19-feature-settings-design.md`

## Global Constraints

- `version: 1` and a required `features` array; exactly one format.
- Preserve unknown entries, their order, unrelated settings, and native write permissions.
- Start from the release tree. No changes to real settings, installed packages, routing state, remote refs, or release tags.

## Review Focus

- Old or mixed files must not be mistaken for missing settings or trigger routing.
- Duplicate entries or malformed feature objects must fail closed.
- Saving Smart Router must not change unknown feature data or its order.
- Existing preview, snapshot, permission, and lossless-write protections remain active.
- The exported package and registered plugin must use the new ID without renaming user-facing tools.

### Task 1: Strict feature settings

**Files:** `project.js`, `tests/project.test.js`, `tests/onboarding.test.js`,
`tests/plugin.test.js`, `tests/verify_cancel.mjs`, `README.md`.

**Interfaces:** Keep `createProjectSettings(directory)` and its `inspect()`,
`preview(enabled?)` and `apply(enabled, snapshot, options?)` result shapes. Reuse
`project_settings_invalid` for invalid files.

- [ ] Update fixtures to the feature array. Add tests that a new confirmed write produces exactly `{"version":1,"features":[{"smart_router":{"enabled":true}}]}`; saving edits an existing entry in place or appends it to an empty array.
- [ ] Assert old and mixed files, missing or non-array `features`, duplicate feature names, invalid entries, and non-boolean `enabled` return `project_settings_invalid` and preserve existing bytes. Exercise selector rejection before a child process starts.
- [ ] Assert unrelated top-level data, unknown features and entry order survive a change. Keep permission denial, stale snapshot, numeric round-trip, and unsafe-file tests.
- [ ] Run `node --test tests/project.test.js tests/onboarding.test.js tests/plugin.test.js`; observe failures at the old shape boundary.
- [ ] Validate the array in `readSettings()`. Locate Smart Router by name and retain its array position on apply; append it when absent. Reject a top-level `smart_router` key instead of interpreting it. Keep existing bounded reads, lossless checks, locks, snapshots, and cancellation.
- [ ] Document the new format, strictly rejected old layout, and manual backup/conversion and rollback guidance in `README.md`. No migration command or startup write.
- [ ] Rerun the focused Node command and validate before committing this task.

### Task 2: Plugin identity and export proof

**Files:** `index.js`, `package.json`, `tests/plugin.test.js`,
`tests/test_package.py`, `tests/test_verification.py`, `tests/verify_native_loading.py`.

- [ ] Assert the exported module's `default.id` and the package `name` both equal `vibe-workflow`; assert both tool names and the onboarding skill remain unchanged. Run the plugin and export tests for RED.
- [ ] Set the server part's `id` and `package.json` name to `vibe-workflow`. Update the isolated native loading verifier's expected ID and fixtures, without running or changing an installed copy.
- [ ] Run all Node and Python tests and source validation. Build and validate an export at a fresh path and commit the change. Rerun full checks on the committed candidate; record native UI verification separately if not run.
- [ ] Obtain independent review of the strict parsing and write changes. Deliver local source and verification results only.
