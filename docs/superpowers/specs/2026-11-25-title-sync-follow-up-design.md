# Title Sync: catch up idle titles and show progress during a turn

**Status:** draft design for findings 6 and 8, after [#404](https://github.com/fcoouto/vibe-workflow/pull/404).
The earlier draft was independently reviewed. The user has since clarified task lifetime.
The disable-time rename tradeoff and design approval are pending.

## What changes

- **6 — Stale idle titles.** A successful numbered merge command in OpenCode can refresh another, idle chat's
  title. After a restart, fix `WAITING` for runs the previous server left busy. Turning Title Sync off stops
  updates. Optional prefix cleanup would cover only tasks still tracked. See the host limitation below.
- **8 — Long turns.** An existing meter can advance between model steps as work reaches a file milestone. Git
  still decides progress and the regular turn-end check still reconciles the task.

No polling, new model calls, webhooks, per-tool Git checks, or mid-turn GitHub calls for progress. Keep the existing
artifact triggers (no finding 9), normal turn-end `gh` gating, the meter's stages, and the existing `WAITING` rules.
Like #404, this design coordinates copies within one OpenCode server. Concurrent servers writing the same
project's titles are out of scope. Do not add a cross-process lock or persistent routing state.

## Evidence and approach

- In a private OpenCode 2.3.2 lab, `session.step.ended.data.files` carried repo-relative paths for `write`, `edit`,
  a multi-file `patch`, and a shell-only edit. Both loaded copies received file events for the same step, with
  the emitting worktree in `event.location.directory`. The `patch` hook also carried
  `input.patchText` and `result.metadata.files`, but the step event already covers those paths and shell edits.
  Prefer it over interpreting each tool's input or result. A step event is a hint, not proof that changes remain.
- The lab's `gh` shim used `gh pr merge 7 --merge`. Only the executing worktree copy got `execute.after`. The
  successful result had `status: "completed"`, `input.workdir`, and `result.metadata.exit: 0`. An earlier lab
  recorded the same completed status with `exit: 1`. Use the hook plus exact PR verification, not the shell
  status alone. The lab proved payloads, not a cross-copy title update or a real GitHub merge.
- A stopped or crashed server left `time.busy` from the old process with no newer `time.idle`. Use a one-time
  local recovery at owner activation, not a recurring refresh. No tested event reliably signals a web merge or
  simply opening an existing chat. A site merge still needs a normal turn to update the meter.
- Re-reading Git on every step is simpler but spends work on steps that cannot advance the meter. Advancing
  directly from the path list is cheaper but can permanently credit a revert, an ignored file, or an unrelated
  change. Verify only plausible stage changes against local Git.

## 6. Recover idle titles

### Merge in OpenCode

1. Add the canonical PR URL to the JSON fields of the existing branch-based lookup. When a result already
   qualifies for the task, retain its URL with that task as optional stored evidence; older records gain it at
   their next successful turn-end lookup. Do not change which PR that lookup selects. Clear the identity on a
   rebind, checkout adoption, or a positively observed switch away from its branch. A failed read is not a switch.
2. Admit only a foreground shell result with explicit exit `0` and an unambiguous, direct `gh pr merge`
   command with an explicit PR number (also accept `rtk gh pr merge`). Support the normal merge-method and
   delete-branch flags. With no PR number, `--delete-branch` can change the checkout before the hook, so the
   current branch cannot identify what merged. Skip implicit targets, `--auto`, repository overrides, and
   compound or dynamic commands. They keep today's turn-end behavior. Do not parse shell output for truth.
3. On that rare signal, scan the bounded title records once for the same project and a known PR identity with
   that number. If no other eligible root is tracked, do not query GitHub. Otherwise make at most one bounded
   `gh pr view <number>` call in the command's directory. Require that the directory still belongs to the
   invoking copy's Git project; pass arguments directly, not through a shell. Only `MERGED` with the exact
   stored URL qualifies. A failed lookup, mismatched URL, or still-queued merge starts no title write.
4. Route the verified result to each live owner by the current session location, not the record's old location.
   Use a process-local registry scoped to the project; register and unregister with the plugin copy. Do not
   start other copies or let the invoking copy write foreign titles. Reuse the owner's per-session queue and
   recheck the project, owner, task identity, generation, enabled state, and current title before publishing.
   Require positive idle evidence: a busy root, child, or background shell blocks the update, as does unknown
   activity. Leave an active root's display to normal evaluation. A cold owner falls back to normal evaluation
   when it next runs.
5. Keep the existing cleanup gate. If the PR is merged but its worktree still needs cleanup, retain verified
   `MERGED` evidence for that task. On a project `worktree.updated`, check the filesystem only for such tasks
   held by that owner. Complete one when its folder is confirmed gone and the session is idle; no GitHub call.
   A missing removal event or an unloaded owner falls back to activation or the next turn. Confirm native
   removal event delivery and shared registry visibility before relying on either in the implementation.

### Owner activation

After event registration, do one bounded local pass when titles are on: list marked roots in that copy's exact
directory, at most four pages of 50. For an incomplete live binding with a busy timestamp older than this
server, no newer idle timestamp, and confirmed absence of current activity, restore `WAITING` without changing
cells. Use #404's server process start, not the copy's setup time. Loading into an already running server does
not make its runs stale. Also apply a previously verified merge if cleanup is now satisfied and the root is
confirmed idle. Skip fresh, completed, silenced, moved, and unmarked titles. Read or restore stored state only
for candidates; no git processes or GitHub calls in this pass.

The existing shell map starts empty. For recovery, qualify a supported host read that can rule out running
background shells. Batch it once per owner activation if possible. If a running shell cannot be attributed,
or the host activity read fails, skip recovery for affected roots. The existing turn-boundary activity fallback
is unchanged; it is not positive idle evidence for a new out-of-turn update.

### A rename belongs to its task, not to the session

Store the full active task, including its last written title and silence flag, or no record at all. Delete the
whole record when the existing facts confirm completion, even when the final title stays as the user named it.
Prune stale records whole through the existing bounded retention path. Keep no finished-title receipt or standalone
silence flag. Do not add GitHub reads just to discover whether a silenced task ended.

During the same task, preserve a manual rename and the existing opt-out behavior. A later write that the binder
recognizes as a new task starts a fresh meter and topic, without carrying the old task's title or silence flag.
For example, a distinct spec starts a new task even if the old one went silent before completion was observed.
The old completion need not be proved again. A plan can start a task when there is no active binding. Updating
the same spec, attaching its plan, or adopting another checkout for that plan does not reset a rename.
Keep the existing first-meter timing: a bind does not write a title during the tool call. Do not treat a rename
made after the new bind as belonging to the previous task.

### Turn titles off

With Title Sync off, a new workflow does not bind or overwrite a title. A project preference generation fences
new title writes as soon as onboarding saves off. Preview, cancellation, and a failed save do not touch titles.

**Decision pending:** leave titles as-is on disable, or attempt cleanup only for still-tracked tasks. The latter
would use the existing bounded task records, not list historical sessions. The owner queues a final comparison
of the current title to the exact last title it wrote. Only a match with a valid `U+FFFB` meter prefix qualifies;
strip the prefix and preserve the topic. Completed, pruned, silenced, and renamed titles are not candidates.

The [OpenCode session update API](https://opencode.ai/docs/api) has no expected-title or revision precondition.
A manual rename between that final read and write can still be overwritten. If best effort is approved, order
the cleanup behind plugin writes already sent, invalidate it on re-enable or unload, and let the destination
owner retry once on a move within the disabled project. Preserve any live binding and do not treat a title
stripped by the plugin as a manual rename. Never repair a mismatch with a second title write.

## 8. Advance an existing meter between steps

1. Subscribe to `session.step.ended`. Ignore empty, malformed, and foreign file lists. Use only paths from the
   task checkout: initially accept the observed case where the event directory resolves to the task's checkout
   root. Ignore subfolder locations until the path base is qualified. Accept a root's own step or a descendant
   resolved by the existing bounded parent walk. Skip the walk unless this copy has a bound, eligible root in
   that checkout. A root stays owned by its current location even when its plan adopted another checkout.
2. For a task with an existing, unsilenced meter below four cells, cache declarations and confirmed hits from
   the plan and the last local evaluation. Add distinct path hints, keeping only a non-doc change flag and
   declared-path matches. An event naming the known plan invalidates cached declarations and their hits.
   Permit one declaration re-read per root turn on such a step; defer further plan changes to turn end.
   Do not keep whole event file lists, count duplicate hints, replay a previous binding's hints, or persist
   provisional state. Reset the cache/hints on rebind, adoption, move, or unload as appropriate.
3. Queue a local check only if the hints could cross a higher stage (first non-doc change, at least half the
   declared paths, or all declared paths). Coalesce requests per root; allow at most one attempt per milestone
   between that root's turn boundaries. A changed plan may invalidate hints but does not reset that budget.
   If evidence is incomplete after a restart, recover local facts on the next ordinary boundary rather than
   reading every step. A failed, premature, or missing check waits for the next higher threshold or turn end.
4. Reuse the git status, diff, untracked-file, and plan logic of the existing evaluator, without its PR lookup.
   Advance only on positive local evidence, never regress cells, and never reach five from this path. Keep
   the user-visible topic and the current `WAITING` label unchanged. Run in the owner's queue without blocking
   the tool or model step. Before the title/storage writes, recheck the owner, current task, generation, enable
   state, and prefix. Leave existing turn-end PR and diagnostics behavior intact.

A single long tool cannot report progress before its model step ends. Changes not included in a snapshot,
missed steps, and excluded paths still reconcile at turn end. Do not infer correctness or test success from
files touched.

## Proof and scope

- **Merge:** two location copies, an idle target in another checkout, and a shim. Show one lookup for a known
  matching PR and no write on an unknown PR, wrong repository with the same number, nonzero exit, queued merge,
  implicit merge followed by a branch switch, or disabled/moved/silenced target. Check cleanup on and off,
  folder removal, and a cold owner. Verify the one-lookup budget and that unrelated turns did not change.
- **Startup and binding:** persisted old busy timestamp versus a current or idle run, with a running child
  and a running background shell that predates owner activation. Verify one bounded list and zero GitHub calls.
  Check that a same-task plan preserves a rename, a distinct spec resets it, and completion and pruning leave
  no title or silence receipt. A rename after the new bind stays with the new task.
- **Disable:** a new workflow does not bind or change a title. If cleanup is approved, verify exact title matching
  with active records, no writes to completed or pruned records, and enable, move, close, and in-flight write races.
- **Progress:** two loader copies; root, descendant, and unrelated sessions; duplicate and multi-file steps;
  newly edited plan; reverted files; an untracked file; repeated hints; lost folder; and a title currently
  showing `WAITING`. Check local-read bounds and no mid-turn `gh` or first-title write. Check normal turn-end
  fallback and unchanged-turn storage behavior.
- **Native qualification before acceptance:** use a fresh private OpenCode lab and a local `gh` shim. Cover
  owner routing and worktree-removal delivery; exact-directory listing for startup recovery;
  restart/disable; and a meter advancing before a root finishes, with root and descendant changes. Prefer
  model-free checks for list, restart, removal, and settings behavior. Any additional live model turn requires
  separate authorization. Report untested host behavior as unverified.

If a host capability is missing or fails qualification, do not ship the corresponding write path or claim
its finding fixed. Shared routing is a gate for cross-copy merge and any disable writes. Scoped listing and
positive activity evidence are gates for recovery.
Worktree-removal delivery is optional: without it, retry only on owner activation or the next turn.
The mid-turn progress path has its own proof and may ship separately. Bring any scope reduction back for approval.

Product changes belong in the existing title, fact, and onboarding paths; tests belong in `tests/plugin/`.
No new dependencies, public settings, injected notes, or README changes. Leave parser and queue details to
the builder where the contracts above do not pin them.
