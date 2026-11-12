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
   being synchronized. Use [delivery preparation](delivery.md#prepare-the-destination)
   to resolve the primary checkout, designated branch, clean state and writer
   ownership. Confirm the synchronization target matches that designation; an
   unrelated merge target is not authority to switch it. Reuse existing
   synchronization authority; this procedure grants no new remote access.
2. Complete delivery preparation's synchronization checks and retain its verified
   upstream/merge inclusion and actual local HEAD. Any unresolved synchronization
   condition leaves maintenance incomplete. Graph refresh additionally requires
   clean source and verified writer/output ownership; it cannot repair failed
   synchronization or conceal source changes.
3. Verify the project's indexing exclusions and Git exclusion of `graphify-out/`
   are in place. Reuse them; do not silently widen indexing, repair configuration,
   install a missing tool or enable a deferred project as part of maintenance.
   In the synchronized primary checkout, execute this read-only Python preflight
   with `python3` before extraction or querying:

   ```python
   import os
   from pathlib import Path

   expected = Path.cwd().resolve() / "graphify-out"
   destination = Path((os.environ.get("GRAPHIFY_OUT") or "").strip() or "graphify-out")
   if destination.resolve() != expected:
       raise SystemExit("Unapproved Graphify output destination; maintenance incomplete")
   ```

   A nonzero preflight, incompatible output override or unresolved path means
   stop without writes; do not unset or override the user's environment to make
   it pass. Check ownership and active writers of that approved output directory
   as well as the primary checkout. Only then run:

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
