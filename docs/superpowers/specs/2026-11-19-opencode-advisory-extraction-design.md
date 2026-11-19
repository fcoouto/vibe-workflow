# OpenCode Advisory Selector Extraction

## Approved scope

On 19 November 2026 the user approved extracting only the tested OpenCode
advisory selector and the function-level dependency boundary discussed in chat.
The deliverable is a validated local, install-ready `vibe-workflow` package.
Do not install, activate, push, open a PR, merge, or run a new inference trial.

The destination primary checkout was on `main`, tracking `origin/main`.
Implementation belongs in its separate `feat/smart-router-extraction` worktree.
After the user saw the full remote tree, the user explicitly approved putting
the destination on the local extraction branch so the folder itself is thin.
Do this only after verification; local `main` continues tracking `origin/main`.
This is a branch checkout, not a merge or push.

The legacy primary is a separate checkout. Its `main` upstream was removed
with `git branch --unset-upstream main`. Its remote,
branches, commits, Git metadata and 50 registered worktrees remain preserved.
The user explicitly rejected making that old checkout non-Git after learning
that it holds shared worktree metadata. Preserve its existing deleted logo.

## Intent and approaches

Keep model/provider/effort selection useful to other plugins without taking
ownership of their tasks, agents, permissions, reviews or execution.

- Copying whole modules was rejected: the static import graph reaches 84–85
  local modules, including unrelated workflow subsystems.
- Migrating the legacy permit bridge was rejected: it requires admission,
  ledger settlement and recovery. OpenCode's separate exact-choice gate also
  validates a caller-selected model rather than performing the tested selection.
- The selected approach extracts the deterministic selector and its account,
  quota and storage dependencies, with a small OpenCode integration.

The reference source is local commit
`56cabcba4cdb801b852c7a02670ea3be29e56ab0` in the retained old task worktree.
The new repository base is
`697e1797eb0d73cd8ddd559a1a02e78496a5fdd2`.
The private trial is behavioral evidence. Do not copy its files, temporary
paths, request labels or scripted controller into the package.

## Dependency boundary

Extract the following functions and directly required validation helpers.
No row authorizes copying an entire legacy subsystem.

| Existing source | Retained responsibility |
| --- | --- |
| `runtime/routing.py` | Profile fields, conservative defaults, row resolution, context filtering and deterministic ranking |
| `runtime/routing_paths.py` | Validation and generation for the three OpenCode subscription paths only |
| `runtime/routing_observations.py` | Subscription-only candidate filtering, shortlist, account facts and quota exclusions |
| `runtime/routing_funding.py` | `subscription_shortlist` only |
| `runtime/subscription_billing.py` | Fact scope, shape, freshness and identity markers; no dispatch operations |
| `runtime/opencode_account.py` | Existing selected-account reader, continuity and quota checks, including the existing Go usage-limits API reader |
| `runtime/claude_usage.py` | Existing zero-inference account/usage exchange, scoped limits and idle-window handling |
| `runtime/adapters/native_catalog.py` | Claude initialization parsing and the local snapshot needed for scoped-limit checks, not a client adapter |
| `runtime/codex_account.py` | Generic account-generation fencing helpers only, not a native Codex producer |
| `runtime/codex_rate_limits.py` | Required generic bounded-process and reader-lock helpers only, not its Codex protocol |
| `runtime/usage_budget.py` | Private-file validation and required scalar constants only, not its ledger |
| `runtime/routing_routes.json` | OpenCode Claude, OpenCode ChatGPT and OpenCode Go path definitions only |

Required runtime software is Python 3.11+ and OpenCode V2 on macOS/Linux.
The OpenCode Claude path additionally requires the native Claude executable
because its OpenCode credential references that login. Do not require Codex,
an SDK package, an npm dependency, a database, a daemon or a new account.
JavaScript glue uses the host plugin context and Node built-ins.

Remove the broader runtime, adapters, enrollment, Workstreams, API/gateway
funding, specialist roster, managed launcher and unrelated skills/hooks from
the extracted task branch and package. Preserve the logo, license and useful
developer instructions; do not ship the old workflow's runtime instructions.
Keep the memory load-path section intact in developer instructions.

## Selection and partnership contract

The caller supplies `work_kind` and optional profile fields. Supported work
kinds remain `implementation`, `review`, `investigation`, `planning`, `testing`
and `writing`. No role-to-roster lookup is required. Missing profile bands use
the existing `high` complexity/risk and `independent` review defaults.
Context limits and caller constraints may only narrow ranked candidates.

The selector ranks an explicitly configured strategy table, considers only
enabled OpenCode subscription paths, acquires missing/stale native facts on
demand, applies the existing account and quota filters, and returns the first
surviving route in ranking order. As in the trial, a route must have a current
account-bound fact reporting `provider_blocked`. Other overflow states remain
visible as exclusions, never relabeled or silently enabled.

The result includes the exact provider, model, effort, exclusions and
`admission: false`, `reservation: false`, `spend_permit: false`.
Its `state` is `selected`, `no_selection` or `blocked`; only `selected` carries
a non-null route. None of these states authorizes a launch.
It launches nothing and reserves no included quota. Unknown quota is not
invented headroom. Positive fresh exhaustion excludes a route; stale or missing
quota stays unknown. Preserve the 900-second account freshness and 60-second
trust window for positive exhausted readings. Preserve the existing explicit
zero/null idle-meter behavior and account-generation checks.

Native execution, original-child continuation, reviewer independence, tool
permissions and result delivery remain the caller's responsibility.
Do not replace or rewrite the native `subagent` tool, change agent defaults,
intercept unrelated model requests, strip shell environments or change retries.
Provider-error feedback/recovery and native Codex cross-checks are not part of
this selector-only slice; do not import their stores or write new feedback marks.

## Thin OpenCode surface

Expose one tool, `smart_router_select`, backed by the JSON CLI, plus one additive
context entry explaining the advisory contract. Other plugin tools and system
instructions remain untouched. Use the current official V2 registration API.
Register no permission, shell, model-enforcement or native-worker gate hooks.

The JSON CLI is `scripts/select.py --state-dir ABSOLUTE_PATH`, with one bounded
request on stdin. The request carries `v: 1`, `work_kind`, optional `profile`
and optional ranking `constraints`; it cannot supply account facts, credentials,
quota observations or a native execution request.
The plugin runs the CLI in its actual location directory; the CLI derives
project scope from that working directory rather than accepting a supplied scope.
Malformed requests return a fixed blocked reason without echoing the input.

## Fresh state

Use a new state namespace,
`${XDG_STATE_HOME:-~/.local/state}/vibe-workflow/smart-router`.
Do not read the old `routing/` or `usage/` namespace implicitly.
Reuse credentials only through the existing native readers; never copy them.

Ship empty, disabled configuration examples, not machine strategy rows,
account snapshots, qualifications or usage history. An absent/disabled
configuration or empty strategy table returns no selection without a reader
spawn or a state write. Explicit configuration selects eligible path IDs; it
is not native-execution qualification or spending authority.

Keep ownership/per-mode checks, bounded files, atomic replacement, reader locks,
account-generation fences, timeouts, fixed failure reasons and failure backoff.
No quota polling, background server, provider retries or model probes.

## Migration, verification and rollback

The user approved retiring the old project's routing enrollment. Remove only
that primary path from the latest managed-project list through the accepted
cached package's supported operator command; preserve other entries and legacy
state. Do not delete old state, evidence, worktrees, caches or trust records.
If that accepted command source is unavailable, report the remaining enrollment
instead of editing its JSON directly.

Test with invented accounts, fake native binaries and disposable private homes.
Require focused profile/selection, reader, storage, plugin-coexistence and
package-boundary checks. Check the actual local export, not only source files.
A disposable native plugin-discovery smoke may use an isolated, credential-free
OpenCode instance with no prompt/model request. Report unavailable smoke proof.
Do not infer real-model compliance, billing proof or production qualification.

The source branch and local export require independent behavior/security review
before declaring the extracted package validated. The earlier eight-execution
trial allocation remains exhausted; a new inference trial needs separate scope.
Execution is inline, following the user's continuation; one independent final
review covers behavior and the relevant security boundaries.

Rollback leaves both primary checkouts and the retained old source available.
The new package is not registered, so no installation rollback is needed.
Restore an old routing-list entry only on explicit request, using its current
revision. Reattaching old `main` tracking is a separate explicit operation.
