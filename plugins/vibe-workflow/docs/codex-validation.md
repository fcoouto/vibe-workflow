# Codex startup validation

25 October 2026. Scope: D1 startup orientation, not completed project onboarding or workflow enforcement.

This records the original D1 snapshot. The shared-parser extraction and Claude packaging addition have subsequent [cross-client regression evidence](claude-validation.md).

## Candidate

Working-tree implementation over `02f86158993f2a5695cbd4d3911b0de991346b1b`, including new files. Runtime/instruction SHA-256: `ff6ab6d6e9c8d41a10a3012f3c40222ff7f784eb67f7b1a1f272e37dc7d4f0d7`.

The fingerprint covers `.codex-plugin/plugin.json`, `hooks/codex.json`, `scripts/workflow.py`, `runtime/**/*.py`, `clients/*.md` and `policies/*.md`: sorted relative POSIX path, NUL, file bytes, NUL. Both installed probe copies matched these 21 files.

## Package and fixture checks

- `python3 -B scripts/validate.py`: passed; workspace skill remains 143 words.
- `python3 -B -m unittest discover -s tests`: 24 tests passed. Includes nested checkouts, symlink aliases, linked worktrees, separate clones, selected clients, stale policies, malformed/oversized input, unsafe records, isolated imports and package paths.
- `git diff --check`: passed.
- 111 local Markdown links and anchors resolved.
- Independent code/security review found two issues: unenrolled broken Git produced warnings, and fixture Git commands inherited external hooks. Both were fixed, reproduced in regression tests and independently rechecked. Final manifest integration also passed review.

Local dispatcher subprocess measurements, 20 runs per case: enrolled median 44.1 ms, p95 46.1 ms; unenrolled median 35.5 ms, p95 36.7 ms. Normal context was 55 words. These are local timing/output measurements, not token-savings or model-compliance evidence.

## Native observations

Tested CLI `0.163.0` and desktop-bundled app-server `0.164.0-alpha.11.1` on macOS. Each used a temporary Codex home, local marketplace install and disposable repositories. Real user configuration, authentication and hook trust were unchanged. The subprocess environment used an allowlist with no credential variables or copied authentication files.

The portable root manifest suppressed hook discovery in both versions, including inline, default-file and compatibility-overlay variants. The sole `.codex-plugin/plugin.json` preserved plugin identity, setup skill and interface metadata and exposed exactly one startup hook. This observed compatibility constraint determines the current package layout.

Normal exact-definition trust was exercised: `untrusted`, then `trusted`, then `modified` after changing the definition. No hook-trust bypass was used.

For execution, the app-server ran with network access denied by the macOS sandbox. A socket self-check confirmed denial. A separate trusted probe-only startup hook returned `continue: false`; the candidate hook remained unchanged. One bounded turn per fixture exercised startup without model generation.

| Fixture | CLI | Desktop-bundled app-server |
| --- | --- | --- |
| Enrolled | One completed candidate hook; context only | One completed candidate hook; context only |
| Unrelated | One completed hook; no output entries | One completed hook; no output entries |
| Malformed enrollment | Warning and diagnostic context | Warning and diagnostic context |
| Untrusted definition | Candidate skipped | Candidate skipped |
| Modified definition | Candidate skipped | Approval-required failure; no candidate context |

Native enrolled startup took 49 ms in the CLI runtime and 45 ms in the desktop-bundled runtime. These are individual observations, not a latency distribution. All probe processes were stopped afterward.

`thread/start` alone and `debug prompt-input` did not execute startup hooks. Discovery or a successful context-preview command therefore cannot substitute for observed `hook/completed` output.

## Remaining qualification

This validates the startup adapter and native context delivery. It does not validate desktop UI behavior, model policy reading/compliance, resume/fork/compaction in a live client, Windows, non-Git projects, D2 enforcement or D4 onboarding. Lifecycle source handling is covered by fixtures. Other clients need their own manifest, native input/output and execution qualification; shared startup code and acceptance cases can be reused.
