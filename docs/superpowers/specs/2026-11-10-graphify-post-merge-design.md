# Graphify post-merge maintenance

Approved by the user on 10 November 2026: refresh automatically as part of the
workflow's post-merge actions, only for Graphify-enabled projects. This replaces
the proposed watcher and refresh-on-read approaches.

## Outcome

After observing a successful merge, the agent safely synchronizes the project's
primary checkout and refreshes its local code map before declaring post-merge
maintenance complete. The same procedure applies when synchronizing merges made
elsewhere. No separate refresh request is needed.

The accepted project setup decision determines whether Graphify is enabled.
Installed tools or arbitrary graph files are not consent; a missing generated
graph does not disable an accepted setup. Preserve disabled and deferred choices.

## Approaches

- **Selected: existing merge procedure.** Add a required agent-executed maintenance
  step and route onboarding and synchronization instructions to it. This matches
  the repository's instruction-driven merge workflow without inventing an executor.
- **Rejected: Git hooks or watchers.** Hooks skip linked worktrees and introduce
  shared Git configuration; watchers add persistent processes and clustering work.
- **Rejected as the primary trigger: refresh-on-read.** The user wants maintenance
  during workflow operations, not deferred until someone queries the graph.

## Safety and limits

Use the primary checkout, not the task worktree. Preserve dirty files and other
writers; synchronize only by a safe fast-forward. Verify the merged commit is
present before refreshing. Reuse project exclusions and run the existing explicit
code-only, no-clustering command with `GRAPHIFY_NO_AUTO_REFRESH=1`, which suppresses
installed skill/plugin changes rather than graph updates. No hooks, watchers,
model calls, new settings, runtime activation or database are added.

Validate the effective output destination before extraction or querying, including
`GRAPHIFY_OUT` and symlinks. Only the approved ignored primary-checkout output
and its established writer ownership are eligible; incompatible overrides stop
maintenance without changing the user's environment or writing elsewhere.

Verify the output and a representative query; bind the result to the synchronized
commit. Missing tools, unsafe synchronization or failed refresh leave maintenance
incomplete, never undo the merge or label an old graph current. External merges
are handled when the workflow next synchronizes them, not by an unattended daemon.
This does not keep uncommitted worktree edits indexed.
