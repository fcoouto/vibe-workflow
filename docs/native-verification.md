# Native loading and lifecycle verification

This record describes the earlier nested export. The subsequent user-approved
layout uses `dist/` directly as the package root; the recorded runtime checks
remain evidence for unchanged package code.

## Scope

The user requested the distribution directory name `vibe-workflow` and further
verification of native loading, real process-group cleanup, and cancellation
during acquisition. This follow-up does not authorize inference, permanent
installation, global activation, publication, or product behavior changes.

Work happens on `chore/native-verification` in the preserved task worktree.
The source baseline is `b8106c0`; 36 Python tests and five Node tests passed.
The original extraction plan remains historical; current export commands use
`dist/vibe-workflow`. Plugin ID, state namespace and tool name are unchanged.

## Proof plan

- [x] Confirm the source, primary and task worktree; preserve existing files.
- [x] Change current export guidance to `dist/vibe-workflow`.
- [x] Load the actual export in an isolated credential-free OpenCode instance.
  Use a temporary observer RPC as a readiness barrier, not an early inventory
  snapshot. Require the selector's actual native tool registration.
- [x] Exercise the production cleanup helper against an owned process group
  with a descendant. Require actual signal effects and stopped processes.
  Finite self-expiry protects against denied signals; never bypass a denial.
- [x] Cancel an actual plugin-to-CLI acquisition after the fake native reader
  reports readiness. Require blocked output, released locks, no complete account,
  no retry, and stopped owned processes.
- [x] Validate and commit documentation/check changes, rerun the suite and
  validate the `dist/vibe-workflow` export. Report unavailable proof explicitly.

## Results

All three requested checks passed without product runtime changes.
Independent review of `ae4d5af` found no Critical, Important or Minor findings.
The reviewer independently passed 38 Python tests and five Node tests without
rerunning native probes or changing files.

- **Native loading:** OpenCode v2.2.6 loaded the actual `dist/vibe-workflow`
  directory. An observer RPC confirmed plugin `vibe-workflow.smart-router` active
  and tool `smart_router_select` present after setup. All four disposable session
  tables contained zero rows, and native shutdown was recorded. The initial
  verifier misread the RPC's `output` envelope; reinterpreting the saved response
  resolved that checker error without another native run.
- **Real process groups:** the production `_stop` helper sent real SIGTERM and
  SIGKILL to its own group. Both leader and descendant recorded SIGTERM; the
  descendant exited and was reaped, and the leader exited from SIGKILL.
  Elapsed time was 0.686 seconds. Unlike the earlier extraction environment,
  this run did not deny group signals.
- **Mid-acquisition cancellation:** an actual exported plugin-to-CLI call reached
  a synthetic native `get_usage` request before its real AbortSignal fired.
  The result was blocked, both owned PIDs stopped, both reader locks released,
  no complete account survived, and no retry or later OpenCode acquisition ran.
  Elapsed time was 0.412 seconds. The caller context was synthetic; this is not
  proof of native session-interrupt propagation or a real provider exchange.

Checks are opt-in and excluded from the runtime export:

```sh
python3 -I -B tests/tooling/verify_group.py --evidence ABSOLUTE_PRIVATE_DIRECTORY
python3 -I -B tests/tooling/verify_native_loading.py --export dist --evidence ABSOLUTE_PRIVATE_DIRECTORY
node tests/tooling/verify_cancel.mjs ABSOLUTE_EXPORT ABSOLUTE_PRIVATE_HOME
```

Use a fresh directory per run. The cancellation verifier requires a stripped
environment whose HOME equals its private evidence directory; set all XDG roots
there too. The native loader creates its own empty HOME/XDG environment.
Every synthetic native process has a finite natural expiry. A denied signal is
a blocker, not permission to retry through another signaling mechanism.

Evidence remains in the private task record outside the package. No inference,
native credential access, permanent installation, global activation, publication,
or merge occurred. Real subscription requests and native session cancellation
remain outside this verification scope.
