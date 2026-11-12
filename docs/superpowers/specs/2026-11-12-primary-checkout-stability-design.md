# Primary checkout stability

## Approved outcome

The user approved this direction on 12 November 2026 after an independent Fable
5.2 architecture review: every repository onboarded to `vibe-workflow`, existing
and future, keeps its primary checkout on its designated branch. Task work uses
separate worktrees. This is a plugin policy, not a GitHub-only or managed-runtime
feature. The goal is a stable shared folder, not a ban on task branches.

## Approaches

1. **Shared instructions and native observations (selected).** Put the rule in
   task planning and the complete procedure in the existing delivery contract.
   Route startup, review-only Git operations and resumption to it. Reconcile
   onboarding, templates and current documentation. Check real Git identities
   before and after affected actions. This reduces mistakes and detects drift;
   it is not a sandbox or an interception layer.
2. **GitHub/Git adapter enforcement (rejected for this change).** Existing adapters
   supply remote facts and bounded transport, not ordinary local checkout
   control. This would miss basic and non-GitHub workflows.
3. **Hooks or client/OS restrictions (deferred).** These need their own explicit
   permission and qualification. A post-checkout hook is too late to veto a
   checkout. No hook, watcher, new database, permission change or automatic
   switch-back is part of this work.

## Contract and procedure

`policies/02-task-planning.md` owns the universal rule. The always-read policy
index routes task entry to planning, including checkout-affecting operations in
read-only reviews. `clients/delivery.md` owns the selectively loaded procedure:

- Resolve the effective target checkout, common Git directory and primary
  worktree through Git metadata, including commands invoked from another folder.
  Do not equate the session directory, remote URL or native attachment with Git
  identity. Separate clones remain separate repositories; submodules have their
  own Git identity. Unsupported, bare or ambiguous layouts are unresolved.
- Obtain the designated branch from accepted project instructions/setup records.
  Record a missing designation during authorized onboarding/update. Current HEAD
  and remote default are observations, not authority to silently choose a branch.
  Unknown or conflicting designation blocks only dependent mutations.
- Read primary symbolic HEAD, dirty state and writer ownership. Snapshot its
  branch identity, not just its commit. Recheck immediately before and after
  affected operations. Fast-forward updates can legitimately change its commit.
- Reuse a suitable task-owned worktree or create one through the native tool;
  use Git's native fallback only if no supported client tool exists. Bind a task
  branch there before edits. Never migrate another writer's unfinished work.
  Report the worktree path/branch for local delivery and retain uncommitted
  results without releasing them for client cleanup absent the user's decision.
- Keep instructions and setup-record discovery available in task worktrees.
  Missing checkout-local enrollment does not require ordinary work to acquire
  runtime state. Never copy enrollment or claim it activates managed controls.
- Check effective targets, not command spelling: Git selectors/environment,
  scripts, `gh pr checkout`, native APIs and direct metadata changes all matter.
  Do not switch/detach/rename the primary branch, or use rebase/bisect there.
- Unexpected primary branch, detached HEAD, dirty files or uncertain ownership
  require preservation and a scoped report, never automatic repair. Primary
  HEAD/index/source mutations and synchronization wait; independent safe reads
  or isolated work may continue. Separately authorized ignored setup outputs
  need verified disjoint paths/ownership, preserving HEAD/index/source bytes;
  generated maintenance retains its stricter clean-source conditions.
- Only explicit authorization for a particular operation supplies an exception;
  record exact scope and observed outcome without automatic restoration.
  A direct, unambiguous user request naming the primary and operation suffices;
  a conflicting destination or broad speed request does not. Carry a surviving
  deviation and restoration decision in the existing handoff, not a new registry.

## Adoption and compatibility

Onboarding and authorized updates record the primary checkout and designated
branch in the existing setup record, author portable task-worktree load paths,
and reconcile older branch-only instructions and `separate_worktrees: false`.
New adopted delivery policies select separate worktrees. The old boolean remains
readable for historical records and active pinned tasks, not a standing exception
under the new policy. Do not silently rewrite records, overwrite user sections,
change live checkouts, or invalidate active tasks. Unresolved conflicts block only
dependent changes until the accepted update or a scoped exception resolves them.
Defer incompatible deployment until affected writers stop; old-pinned tasks
remain governed by their accepted package until an authorized transition and
are not represented as new-policy compliant. Serialize conflicting new work.

Tracked setup guidance/enrollment is integrated through the authorized project
delivery path before claiming complete setup in the selected target checkout.
Verify startup at that exact checkout. A keep-local preference grants no merge
permission; preserve the task worktree and report target integration pending.

Installing this source does not update every project or running client. Use
existing authorized plugin/project update and recovery procedures; report pending
installation, pointer reconciliation and fresh-session delivery separately.

## Verification and boundaries

Keep one procedure and reuse existing primary-worktree discovery concepts and
Graphify synchronization checks. No runtime adapter or stored schema change.
Regressions cover selective loading, non-`main` designation, linked/symlinked
destinations, worktree creation, fast-forward, detached/dirty primary, historical
policy readability, review-only checkout and per-operation exceptions. Audit
tracked Markdown for contradicting live rules; label historical descriptions
explicitly rather than rewriting their evidence.

Use package validation of current instruction sources, focused tests, fresh-context
scenario evaluation and independent source/security review. Distinguish static
and disposable Git results from actual client enforcement. Uncontrolled writers,
unloaded old instructions and read/check races remain limits. This work does not
claim that unrelated applications cannot switch a checkout.
