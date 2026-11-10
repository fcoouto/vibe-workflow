# Graphify post-merge maintenance

Run this as part of completing a merge, or synchronizing merges made elsewhere,
without a separate refresh request. It is an agent-executed workflow step, not a
Git hook, watcher or unattended service.

## Select the project

Use the accepted Graphify choice in the project's setup record or current task
decision. Run only when that project is Graphify-enabled. Preserve an explicit
disabled, declined or deferred choice; no enabled choice means no automatic
enrollment. An installed CLI/skill or an arbitrary graph file is not enablement.
If records conflict, report the unresolved choice rather than assuming enabled
or claiming maintenance succeeded. A missing generated graph does not disable
an accepted Graphify setup: the extraction below rebuilds it.

## Synchronize, refresh and verify

1. Confirm the successful remote merge and its commit, or the upstream commits
   being synchronized. Resolve the project's primary checkout and configured
   merge-target branch from its scope and Git worktree information; do not use
   the task worktree just because it is the current directory. Reuse existing
   synchronization authority; this procedure grants no new remote access.
2. Check the primary checkout's branch, dirty state and writer ownership. Only
   synchronize when safe, using a fast-forward-only operation to the intended
   target. If already synchronized, verify that instead. Confirm the merged
   commit is included in the local target's history and record its actual HEAD.
   Dirty files, divergence, an occupied/wrong branch, unavailable checkout or
   uncertain writer ownership leave maintenance incomplete. Never stash, reset,
   force, switch another writer's branch or overwrite files to make it proceed.
3. Verify the project's indexing exclusions and Git exclusion of `graphify-out/`
   are in place. Reuse them; do not silently widen indexing, repair configuration,
   install a missing tool or enable a deferred project as part of maintenance.
   Run in the synchronized primary checkout:

   ```sh
   GRAPHIFY_NO_AUTO_REFRESH=1 graphify extract . --code-only --no-cluster
   ```

   This uses local code extraction only, including when the graph is missing.
   Keep the flags even for a previously broader graph. Do not invoke semantic
   extraction, clustering, hooks, watchers, a model backend or `--force` to bypass
   a shrink refusal. The environment variable prevents installed skill/plugin
   updates; it is not the graph-refresh switch. Only ignored generated output
   may be written in the primary checkout, never tracked task changes.
4. Check the command's exit status and reported outcome, parse the resulting
   `graphify-out/graph.json`, and run a representative `graphify query` for a
   symbol in current code with the same environment variable. Confirm a useful
   result, not merely an old file or a newer timestamp. Recheck HEAD and source
   cleanliness/ownership before binding freshness to the synchronized commit;
   concurrent changes invalidate that claim. Keep graph data and caches local.
5. Record the synchronized commit, refresh command and verification result in
   the existing task report. Missing tools, unsafe synchronization, refused
   shrink, invalid output or failed query leave **post-merge maintenance
   incomplete**, with the blocker and next action. Report the merge separately
   as successful; do not undo it, reuse stale output as current, or mark the
   overall operation complete while required maintenance is pending.

External merges trigger this step when the workflow next synchronizes them; no
agent is claimed to run while absent. This maintains the integrated primary
checkout, not uncommitted edits or separate task-worktree graphs.
