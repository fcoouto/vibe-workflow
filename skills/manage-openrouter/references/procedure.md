# Manage OpenRouter Integration

Use these controls when requested. They save checkout consent for proposal,
OpenCode worker and Council routes. Ordinary tasks use their executor procedures;
they do not load this management procedure or enable a route automatically.
An explicit [LLM Council](../../llm-council/SKILL.md) invocation uses the separately saved
Council consent and [deliberation procedure](../../llm-council/references/procedure.md);
invocation and activation are distinct requests.

## Select scope and inspect status

Resolve the explicitly selected checkout using its current working directory.
The supported scope is an already enrolled Codex POSIX Git checkout with an
accepted private runtime database. Enrollment lives in
`.vibe-workflow/project.json`; settings live outside Git in that runtime's
private state directory. A repository clone, sibling worktree, parent directory or
account connection does not inherit consent. Canonical Project-wide activation
and other client adapters are not implemented.

Use the packaged CLI with Python 3.11+ and one JSON object on stdin:

```text
python3 -I -B <plugin-root>/scripts/openrouter.py <action> --cwd <selected-checkout>
```

For `status`, supply `{}`. It is offline and read-only: no storage creation,
OpenCode execution, credential-content access, network request or model call.
Report `configured`, `enabled`, `routes`, `revision`, `control_scope`,
`local_configuration` and the returned nonsecret runtime/work references.
`connection` and `execution_qualification` remain `not_checked`: local file checks
do not verify login, installed version, account allowance, model fitness or
Habblaud delivery. An unsafe, busy or unsupported scope is an error, not proof
that integration is disabled.

When prerequisites are missing, explain the specific limitation and retain native
execution. Do not bootstrap a database, enroll a project, create membership,
repin replaced authority or run machine preparation through this skill. Existing
enrollment and native lifecycle own those prerequisites.

## Configure and enable

Reuse the user's authorization within its scope. An explicit request to enable
this integration authorizes the selected checkout's controls; merely opening this
skill or completing onboarding does not. Keep task transmission within approved
scope and inspect selected files for secrets. Do not repeat approval requests for
already-authorized work.

Read `status`, then use its exact `revision` as `expected_revision` for each
mutation. A conflict requires rereading current state and reconciling the user's
intent; never silently overwrite another change.

`configure` selects a nonempty subset of `proposals`, `workers` and `council`. Use
workers for background OpenCode agents; select Council only for an explicit
Council activation request. Existing worker or proposal consent does not authorize
Council. Add proposals only when that route is requested.
It saves configuration while disabled:

```json
{"expected_revision":0,"routes":["workers"],"opencode":"/absolute/pinned/opencode","auth_data":"/absolute/private/opencode-data"}
```

Use the existing pinned OpenCode 1.19.5 executable and its existing owner-only
authentication directory. Obtain nonsecret paths from approved local setup when
available; do not read or copy the key. A proposals-only configuration uses
`"opencode":null,"auth_data":null` and the current client's existing MCP connection.
Council setup requires these OpenCode references even when workers are not selected:
configure with `"routes":["council"]` and the existing pinned paths, then explicitly
enable the resulting configuration. Status returns a separate `council_root` for
Council artifacts; do not substitute `worker_root` or another checkout's path.
Council text mode uses an owned direct Chat Completions child reading only the
saved private `openrouter` API record (`type: "api"`); it does not invoke OpenCode.
Unsupported/missing auth fails before send. Repository mode uses qualified pinned
OpenCode inspection. The Council procedure selects the mode and eligibility;
Free is temporarily unavailable, with no automatic paid substitution. Historical
Council runs remain readable/cancellable with operational settlement only.
This skill does not install clients, initiate login, change providers or add a
connection. If separate setup is needed, identify it without pretending local
configuration verifies execution.

For `enable`, send `{"expected_revision":<current-revision>}`. Enabling a configured
route saves consent; it performs no inference or connection probe. Use status
after the mutation to report the observed result. Reuse saved configuration when
re-enabling. Reconfiguration requires disable first and another current revision.

Settings contain only consent, selected routes, nonsecret paths and physical
scope bindings. They use owner-only files and compare-and-swap publication.
Benign enrollment updates and native database content changes preserve consent;
replaced ownership or invalid enrollment blocks access rather than silently
resetting it. Do not edit settings JSON or move it to another scope.

## Disable and admission

For `disable`, send `{"expected_revision":<current-revision>}`. With no saved
configuration, revision 0 returns disabled without creating configuration. Disable
retains settings, cache, task history and pending results for inspection and
settlement; it does not erase data or revoke the OpenRouter key.

Both public helpers enforce saved consent and the configured scope and paths.
Proposal `begin` admits exactly one bounded call under a short shared guard;
its issued token may settle after disable. Worker preparation is not launch:
the runner checks consent again at process creation, including the prepared
revision and configured executable/auth directory. Disable after preparation
blocks launch; re-enable does not revive stale preparation. Already-launched
workers finish under their existing deadline and can be verified after disable.
Council admission checks its own selected route, configured root, runtime
references and preparation revision under the same saved-consent guard.
Concurrent mutation/admission may report busy; it does not justify another worker
or a different store. Retain native execution until the requested state is known.

These controls govern workflow helper paths, not arbitrary same-user programs or
the account's direct MCP tools. Report that boundary accurately. Preserve the
executors' cached discovery, retry ownership, independent verification, truthful
progress and usage reporting. OpenRouter manages spending limits; no fresh credit
check is required for an assignment.

## Completion

State which checkout and routes changed, the resulting saved state/revision and
any readiness limitation. Loading a skill, a successful setting write, package
validation and observed client execution are separate evidence. Offer this skill
only after successful qualified onboarding; an offer never invokes or enables it.
