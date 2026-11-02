# Shared startup contract

Read this when implementing or diagnosing startup, or preparing enrollment records. Ordinary tasks follow the [policy index](../policies/00-index.md) and their selected client instructions.

The shared runtime resolves the nearest Git checkout and common Git directory. Legacy `.vibe-coding-workflow/project.json` enrollment is checkout-root scoped. Canonical Project authority may live at an accepted Project ancestor, with explicit repository, checkout and current-client membership. Ancestry alone never grants enrollment. Without enrollment, startup produces no output; invalid, conflicting or unavailable authority produces a diagnostic. Startup supplies orientation only, without onboarding, task state, action guards or merge enforcement.

## Enrollment input

Canonical Project authority is created through approved D4 onboarding/birth. Its selected client must explicitly cover the current checkout. A runtime-enabled legacy schema-1 record does not establish current task authority in the composed package; retain and use its original pinned package. A legacy record without runtime remains startup-only and supplies no task authority. Supported M history may be inspected through the explicit read-only D4 path; E lowercase schema-3 history remains with its original E package and reader. Disposable fixtures exercise software behavior; they do not establish completed onboarding or qualified enforcement. Record shape, digests and stored qualification labels do not authenticate host intent or establish native client execution.

Private Project-plan schema 2 is not an enrollment record. Inspection/proposal support alone grants no membership, enrollment or future-member activation. Canonical adoption uses the approved D4 operation and explicit accepted membership; the runtime/SQLite schema is a separate version contract.

| Legacy schema-1 field | Meaning |
| --- | --- |
| `schema_version` | Integer `1` |
| `project_id` | Stable identifier, 1–128 letters, digits, underscores or hyphens |
| `repository_root` | Exact canonical absolute checkout/worktree path |
| `git_common_dir` | Exact canonical absolute common Git directory |
| `clients` | Nonempty list of selected native client IDs: `codex`, `claude-code` |
| `policy_digest` | SHA-256 over sorted `policies/*.md`: filename, NUL, file bytes, NUL, repeated |
| `runtime` (optional) | Codex D2 opt-in: schema version and private external state directory; see the [runtime contract](runtime.md) |

These fields describe only the legacy checkout-root record. The current client must be explicitly selected in `clients`. Canonical schema-2 authority binds Project/repository membership, physical identities, selected checkouts and client activation; it may govern descendants only when its accepted membership includes them. Its selected runtime configuration has a separate version, described in the [Codex task runtime](runtime.md).

Public task, evidence, delegation, doctor and retirement controls remain Codex-only. A selected Claude client may receive startup orientation; the runtime field alone does not enable Claude lifecycle controls. Read [Claude's prepared guard boundary](claude-code.md#prepared-file-guard) only for Claude support work.

A copied checkout record, changed policy digest, malformed JSON, link or special-file record produces a diagnostic. Reconcile accepted scope/version through onboarding; never silently rewrite it during startup. For the legacy record, omitting `runtime` preserves startup-only behavior. A stored qualification flag is not a native observation, and startup never qualifies a host surface. The runtime field alone does not enable Claude lifecycle controls.

## Runtime limits

Require Git and Python 3.11+ on the hook PATH. Current file operations require POSIX; Windows and non-Git projects remain unqualified. Input is limited to 128 KiB, Git lookup to two seconds and each hook to five seconds. Startup performs no writes, network requests or model calls. Interpreter isolation prevents project Python files or environment imports from replacing runtime modules.

Each lifecycle event may refresh context after resume, fork or compaction. There is no persistent deduplication: native events supply no unique invocation ID. Supplied model and permission settings remain observations; missing values, client version and UI surface remain unknown. Never infer approval from environment variables or hook output.

Run `python3 -B scripts/validate.py` and `python3 -B -m unittest discover -s tests`. These establish package and fixture behavior. Native qualification must separately observe one handler, actual output, permissions and the client's installation/trust rules.
