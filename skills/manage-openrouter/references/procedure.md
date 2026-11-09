# Manage OpenRouter Integration

Use these controls when requested. They save checkout consent for the existing
proposal and OpenCode worker routes. Ordinary tasks use their executor procedures;
they do not load this management procedure or enable a route automatically.

## Select scope and inspect status

Resolve the explicitly selected checkout using its current working directory.
A configured Codex POSIX Git checkout without a selected managed runtime can
configure the read-only workers route. Its consent lives in the private
`~/.vcw-openrouter-consent` store and binds the current canonical
enrollment and physical checkout. A change to that enrollment requires fresh
consent. When the checkout selects a managed runtime, configuration and
enablement require an accepted canonical scope and its existing schema-3
database. Proposal routes require that managed authority. Selecting a managed
runtime switches to its separate consent store. Existing basic consent cannot
admit workers in managed mode and does not migrate.
Status and disable instead verify the current physical project/scope identity and
ownership of its saved private settings. Stale qualification, changed source digests
or a missing, replaced or corrupt database do not prevent viewing or disabling
established consent. Preserve historical bindings, expected revisions and atomic
ownership checks; never repin or renew consent during recovery. A basic checkout
without saved consent reports unconfigured. This skill supplies no native qualification reader. Enrollment lives in
`.vibe-workflow/project.json`; managed settings live outside Git in that runtime’s
private state directory. A repository clone, sibling worktree, parent directory or
account connection does not inherit consent. Consent remains checkout-bound;
Project-wide consent and other client adapters are not implemented.

Use the packaged CLI with Python 3.11+ and one JSON object on stdin:

```text
python3 -I -B <plugin-root>/scripts/openrouter.py <action> --cwd <selected-checkout>
```

For `status`, supply `{}`. It is offline and read-only: no storage creation,
OpenCode execution, credential-content access, network request or model call.
Report `configured`, `enabled`, `routes`, `revision`, `control_scope`,
`consent_version`, `worker_capability`, `enablement_renewal_required`,
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

Enablement is the only approval: it authorizes automatic selection of the
enabled routes and transmission of the checkout's task-relevant files. To narrow
that, `configure` accepts an optional `external_permission` restriction under the
[shared permission contract](../../../clients/external-delegation.md#saved-permission-optional);
omission preserves it and null removes it. Keep existing funding choices.

An explicit request to enable this integration activates the selected checkout;
merely opening this skill or completing onboarding does not. Keep task
transmission within the activation's scope and any saved restriction, and
inspect selected files for secrets. Never ask for approval of work on an enabled
route.

Read `status`, then use its exact `revision` as `expected_revision` for each
mutation. A conflict requires rereading current state and reconciling the user's
intent; never silently overwrite another change.

`configure` selects a nonempty subset of `proposals` and `workers`. Use workers
for background OpenCode agents; add proposals only when that route is requested.
It saves configuration while disabled and unacknowledged:

```json
{"expected_revision":0,"routes":["workers"],"opencode":"/absolute/pinned/opencode","auth_data":"/absolute/private/opencode-data"}
```

Use the existing pinned OpenCode 1.19.5 executable and its existing owner-only
authentication directory. Obtain nonsecret paths from approved local setup when
available; do not read or copy the key. A proposals-only configuration uses
`"opencode":null,"auth_data":null` and the current client's existing MCP connection.
This skill does not install clients, initiate login, change providers or add a
connection. If separate setup is needed, identify it without pretending local
configuration verifies execution.

Enabling the workers route records version-2 consent, which also covers scoped
worktree editing and registered checks under the task's declared ownership. The implemented runner is
read-only over copied files: status reports `worker_capability:read_only`. Editing,
registered-check execution and automatic native worker dispatch remain unavailable.
Explain this distinction when enabling; saved authorization is not implemented
capability or execution qualification. Do not add a mode selector or revoke existing
consent. Preserve task scope and authorization without recurring confirmation.

For any `enable`, send `{"expected_revision":<current-revision>}`; a legacy
`"consent_version":2` field is still accepted but no longer required. Enabling a
workers route records version 2; enabling an already enabled record changes
nothing. A legacy version-1 record that is already enabled keeps read-only worker
admission without renewal and becomes version 2 only through a later disable and
enable. Never infer enablement from an account connection or a configured path.
`enablement_renewal_required` is always false and remains for compatibility.

The proposal route grants no file tools. Enabling saves the activation without
inference or a connection probe. Use status after the mutation to report the
observed result. Reuse saved configuration when re-enabling. Reconfiguration
requires disable first and another current revision; enabling then activates
the new configuration.

Saved permission and local references do not establish coding-dispatch readiness.
Worktree editing and registered checks require the executor's qualified task/worktree
binding, ownership, check bridge and independent verification before their dispatch. This management operation cannot
qualify those mechanisms or bypass an unavailable native execution prerequisite.

Settings contain only versioned consent, selected routes, nonsecret paths and physical
scope bindings. They use owner-only files and compare-and-swap publication.
Benign enrollment updates and native database content changes preserve consent;
replaced ownership or invalid enrollment blocks access rather than silently
resetting it. Do not edit settings JSON or move it to another scope.

## Disable and admission

Disable is an offline local control and makes no account or provider calls.
For `disable`, send `{"expected_revision":<current-revision>}`. With no saved
configuration, revision 0 returns disabled without creating configuration. Disable
retains settings, cache, task history and pending results for inspection and
settlement; it does not erase data or revoke the OpenRouter key.

Both public helpers enforce saved consent and the configured scope and paths.
Every new proposal `begin` needs the `external` binding and admits exactly one
bounded call under a short shared guard; the final `dispatch` check runs before the call.
That check revalidates permission, revision and source and consumes the same token;
its issued token may settle after disable. Worker preparation is not launch:
the runner checks consent again at process creation, including the prepared
revision and configured executable/auth directory. Disable after preparation
blocks launch; re-enable does not revive stale preparation. Already-launched
workers finish under their existing deadline and can be verified after disable.
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
