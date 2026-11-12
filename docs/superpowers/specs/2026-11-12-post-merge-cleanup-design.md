# Opt-in post-merge cleanup

Approved in conversation on 12 November 2026: clean up eligible task resources
after the coordinator observes its exact PR merged. Cleanup defaults to disabled;
remote branch deletion defaults to enabled only within enabled cleanup. Ask about
cleanup immediately after merge permission, with No as the default. A submitted
Yes grants standing repository-scoped permission; settings alone grant none.

## Approach

Use one procedure in `clients/delivery.md`, native client operations, and a small
read-only Git inspection helper. Keep effect ownership with the coordinator rather
than introducing another executor or managed delivery API. The helper reports
local prerequisites, not authority, remote merge proof, or exclusive ownership.

The broader alternative was a general transport executor with a durable recovery
journal and automatic replay. The user rejected that complexity and explicitly
accepted leaving partial cleanup for a later explicit attempt. Unsupported remote
configurations and uncertain preservation retain resources instead of adding more
infrastructure. This first path supports same-repository GitHub PRs and ordinary
HTTPS Git destinations; other transports remain retained.

## Behavior

Refresh and fast-forward the designated primary branch, verify merge inclusion,
and finish required maintenance before cleanup. Confirm permission, unchanged
task refs, stopped known writers, no other owners or dependent tasks, and no
unfinished tracked, untracked or ignored work. Relocate the coordinator before
native worktree removal. Check the actual removal tool's behavior before use.
Use expected-object conditional deletion for branches; never force-remove files.

Keep exact targets and outcomes in the existing surviving conversation/task
context. Stop after failure, interruption or uncertainty. Do not automatically
retry, reconstruct lost targets, or add a recovery store. Any later attempt needs
an explicit request and fresh checks. Leave completed actions completed.

Native cleanup and Git inspection cannot exclude uncontrolled filesystem writers.
If writer ownership cannot be established, retain the worktree. An ignored-file
precheck is mandatory even when the native tool refuses dirty worktrees.

## Verification

Disposable native removal probes; isolated Git tests for disabled/malformed
settings, branch/primary identity, merge inclusion, unfinished files, locks,
submodules, changed refs, and unsupported remotes. Test conditional deletion
against a disposable bare remote. Validate instructions and package, then review
the complete change independently before publication readiness and merge.
