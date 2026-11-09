# Shared startup contract

Read this when implementing or diagnosing startup, or preparing enrollment records. Ordinary tasks follow the [policy index](../policies/00-index.md) and their selected client instructions.

The shared runtime resolves the nearest Git checkout and common Git directory. Legacy `.vibe-workflow/project.json` enrollment is checkout-root scoped. Canonical Project authority may live at an accepted Project ancestor, with explicit repository, checkout and current-client membership. Ancestry alone never grants enrollment. Without enrollment, startup produces no output; invalid, conflicting or unavailable authority produces a diagnostic. Startup supplies orientation only, without onboarding, task state, action guards or merge enforcement.

## Enrollment input

Canonical Project configuration is created through approved D4 file configuration. Runtime birth is a separate managed-operation effect. Its selected client must explicitly cover the current checkout. A runtime-enabled legacy schema-1 record does not establish current task authority in the composed package; retain and use its original pinned package. A legacy record without runtime remains startup-only and supplies no task authority. Supported M history may be inspected through the explicit read-only D4 path; E lowercase schema-3 history remains with its original E package and reader. Disposable fixtures exercise software behavior; they do not establish completed onboarding or qualified enforcement. Record shape, digests and stored qualification labels do not authenticate host intent or establish native client execution.

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

A copied checkout record, changed legacy policy digest, malformed JSON, link or special-file record produces a diagnostic. Reconcile accepted scope/version through onboarding; never silently rewrite it during startup. For the legacy record, omitting `runtime` preserves startup-only behavior. A stored qualification flag is not a native observation, and startup never qualifies a host surface. The runtime field alone does not enable Claude lifecycle controls.

Canonical guidance-only startup requires current client instructions and the policy index, exact accepted physical membership and unchanged selected shared instructions. Accepted package digests remain provenance; unrelated package updates do not suspend guidance. This path neither reads optional runtime sources nor creates runtime state or managed authority.

## Codex qualification

Apply the [scope guarantee](../policies/01-project-scope.md#authority-and-activation). A trusted owner supplies actual setup behavioral evidence and current observable configuration through the existing acquisition seam. Keep the historical qualification binding/provenance separate from current registration, trust and configuration observations. Reuse compatible client capabilities without rewriting their original version provenance; managed authority still requires strict policy/runtime source identity. Copied labels or receipts without genuine owner observation cannot activate a scope. The current implementation and qualification boundary is recorded in [onboarding qualification](../docs/onboarding-authorization-qualification.md).

Codex `qualification.current` contains `client`, `configuration_digest` and `hook_owner`. Historical surface/version stay in `qualification.binding`; they are not assertions about the current session. Legacy five-field current inputs remain readable without rewriting stored facts. Other clients retain their existing current-surface checks. Startup eligibility never qualifies an unobserved surface-specific operation.

For an explicitly approved disposable qualification target, `AcceptedScope.qualification` carries only its retained receipt reference, verified behavior keys, computed full-coverage result and owner-derived native bootstrap result. The complete catalog and pending evidence stay in the existing D4 setup journal. Genuine native safety can admit qualification-only startup while Toolkit startup remains pending. SessionStart then supplies restricted context, creates no task and skips ordinary title repair. Pending dependent use requires its exact original qualification admission. A selected managed workflow requires its applicable startup and pre-tool evidence; editing controls additionally require post-tool evidence. Ordinary native editing, tests and review do not depend on these controls. Active startup does not establish full onboarding. Legacy fully qualified scopes retain their original interpretation. See [staged qualification](../skills/onboard-my-project/references/qualification.md#staged-disposable-qualification).

## Runtime limits

Require Git and Python 3.11+ on the hook PATH. Current file operations require POSIX; Windows and non-Git projects remain unqualified. Input is limited to 128 KiB, Git lookup to two seconds and each hook to five seconds. Startup performs no writes, network requests or model calls. Interpreter isolation prevents project Python files or environment imports from replacing runtime modules.

Each lifecycle event may refresh context after resume, fork or compaction. There is no persistent deduplication: native events supply no unique invocation ID. Supplied model and permission settings remain observations; missing values, client version and UI surface remain unknown. Never infer approval from environment variables or hook output.

From a checkout of this repository, run `python3 -B scripts/validate.py` and the tests that [AGENTS.md](../AGENTS.md#verification-and-delivery) selects for the change; installed packages may omit `tests/`. These establish package and fixture behavior. Native qualification must separately observe one handler, actual output, permissions and the client's installation/trust rules.
