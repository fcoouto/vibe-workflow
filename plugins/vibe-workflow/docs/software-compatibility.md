# Software compatibility

Software support uses minimum versions only: no upper bound, exact-release
requirement, tested-version allowlist or CLI/server equality requirement. Newer
minor, patch and major versions remain eligible. Record actual versions for
diagnostics; a version-only change does not invalidate unchanged capabilities.
Unknown release metadata is not evidence of a missing capability.

Required protocol, identity, permissions, hook trust, schema, counter continuity
and source/candidate integrity checks still apply. These floors do not assert
every feature was exercised on every release. A real unsupported API affects only
its dependent operation; users can report compatibility failures through a
[GitHub issue](https://github.com/fcoouto/vibe-workflow/issues/new).

## Initial supported floors

The user accepted the oldest documented working versions as initial support
floors on 13 November 2026. Values live in `runtime/software_versions.py`.

| Integration | Minimum | Basis and scope |
| --- | --- | --- |
| Codex CLI | 0.163.0 | [Startup](claude-validation.md#native-execution) and [runtime controls](d2-validation.md#native-execution-observed). Usage-file evidence is from 0.167.0; the reader still checks actual record shape and counters. |
| Claude Code CLI | 2.1.304 | [Native startup](claude-validation.md#native-execution). Later routing controls retain their actual capability checks. |
| OpenCode CLI and server | 2.2.6 | [Adapter and probe evidence](../clients/opencode.md#evidence-limits). CLI 2.2.6 with server 2.2.7 is permitted. |
| Python | 3.11 | Existing runtime requirement, including standard-library TOML parsing. |
| Graphify | 0.9.104 | [Recorded command/source evidence](policy-sources.md) for code-only extraction, output configuration and auto-refresh suppression. |
| keyring, optional native credential storage | 25.7.0 | [Native-backend evidence](d9-observability-preparation.md). Select the explicit platform backend, not fallback discovery; environment redirects and missing APIs remain rejected. |
| Habblaud, optional display receiver | 1.0.3 | [Recorded receiver behavior](../clients/worker-visibility.md#forward-the-original-desktop-start-event). Display metadata is nonblocking; no new receiver version probe is introduced. |

The workspace checks known below-floor client releases and Graphify; consumers
with observed version metadata use the same lower-bound rule. A reported version
is not a substitute for running the required API or checking its result.

## Proposed floors from required commands

These are justified proposals, not newly imposed gates. They need acceptance
before adding new numeric requirements to integrations that had no floor.

| Integration | Proposed minimum | Required capability and official evidence |
| --- | --- | --- |
| Git | 2.36.0 | `worktree list --porcelain -z`, introduced in [2.36](https://github.com/git/git/blob/v2.36.0/Documentation/RelNotes/2.36.0.txt). `rev-parse --path-format` arrived in [2.31](https://github.com/git/git/blob/v2.31.0/Documentation/RelNotes/2.31.0.txt); the newer required command sets the floor. |
| GitHub CLI, ordinary PR inspection | 1.9.0 | PR JSON output, introduced in [1.9](https://github.com/cli/cli/releases/tag/v1.9.0); [tagged fields](https://github.com/cli/cli/blob/v1.9.0/api/query_builder.go) include `state`, `mergedAt` and `mergeCommit`. |
| GitHub CLI, conditional merge | 2.13.0 | `pr merge --match-head-commit`, introduced in [2.13](https://github.com/cli/cli/releases/tag/v2.13.0). Prefer this floor for repositories where agents merge conditionally. |
| RTK with Claude Code | 0.30.1 | ASCII status markers used by the readiness parser; [0.30.1](https://github.com/rtk-ai/rtk/releases/tag/v0.30.1) removed decorative status emojis. |
| RTK with Codex | 0.50.0 | Native Codex hook support introduced in [0.50](https://github.com/rtk-ai/rtk/releases/tag/v0.50.0). |
| RTK with OpenCode V2 | 0.52.0 | OpenCode V2 integration introduced in [0.52](https://github.com/rtk-ai/rtk/releases/tag/v0.52.0). Do not impose this newer floor on Claude-only installations. |

Superpowers, Ponytail and Caveman are discovered as named plugins/skills; no
version-bound API introduction was established. Context7 is a configured MCP
service exposing `resolve-library-id` and `query-docs`; the remote service does not
require a local package release. Keep capability discovery for these integrations
instead of inventing numeric floors. The research checked their official project
documentation, not today's installed versions.

## Boundaries

Schema versions, accepted source/policy digests and immutable receipt bindings are
not software release numbers. Preserve them and historical evidence unchanged.
The upgrade command may require matching versions before recording the factual
claim that clients completed that upgrade; this does not gate routine execution.
Package drift remains an advisory update hint, not a workspace eligibility error.

Tests cover lower-bound comparisons and preserved capability/safety failures.
This change does not install software, refresh installed plugins or establish new
live qualification. Historical version-specific probes remain historical evidence.
