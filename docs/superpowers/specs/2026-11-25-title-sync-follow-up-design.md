# Title Sync: catch up idle titles and show progress during a turn

**Status:** draft design for findings 6 and 8, after [#404](https://github.com/fcoouto/vibe-workflow/pull/404).
Independent review and implementation approval are pending.

## What changes

- **6 — Stale idle titles.** A successful merge command in OpenCode can refresh another, idle chat's title. After
  a restart, fix `WAITING` for runs the previous server left busy. Turning the feature off in onboarding removes
  its prefixes, including from completed titles whose task record is gone.
- **8 — Long turns.** An existing meter can advance between model steps as work reaches a file milestone. Git
  still decides progress and the regular turn-end check still reconciles the task.

No polling, new model calls, webhooks, per-tool Git checks, or mid-turn GitHub calls for progress. Do not change
artifact binding (finding 9), normal turn-end `gh` gating, the meter's stages, or the existing `WAITING` rules.

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
   command (also accept `rtk gh pr merge`). Support a PR number or the current branch, with the normal
   merge-method and delete-branch flags. Skip `--auto`, repository overrides, and compound
   or dynamic commands. A skipped form still gets today's turn-end behavior. Do not parse shell output for truth.
3. On that rare signal, scan the bounded title records once for the same project and a known PR identity. For a
   number, prefilter by PR number; for an implicit target, prefilter by the task checkout and the command's
   effective working directory. If no other eligible root is tracked, do not query GitHub. Otherwise make at
   most one bounded `gh pr view` call in that directory for the command's PR. Require that the directory still
   belongs to the invoking copy's Git project; pass arguments directly, not through a shell. Only `MERGED`
   with the exact stored URL qualifies. A failed lookup, mismatched URL, or still-queued merge starts no title write.
4. Route the verified result to each live owner by the current session location, not the record's old location.
   Use a process-local registry scoped to the project; register and unregister with the plugin copy. Do not
   start other copies or let the invoking copy write foreign titles. Reuse the owner's per-session queue and
   recheck the project, owner, task identity, generation, enabled state, and current title before publishing.
   A busy root, child, or background shell still counts as activity. Leave an active root's display to normal
   evaluation. A cold owner falls back to normal evaluation when it next runs.
5. Keep the existing cleanup gate. If the PR is merged but its worktree still needs cleanup, retain verified
   `MERGED` evidence for that task. On a project `worktree.updated`, check the filesystem only for such tasks
   held by that owner. Complete one when its folder is confirmed gone and the session is idle; no GitHub call.
   A missing removal event or an unloaded owner falls back to activation or the next turn. Confirm native
   removal event delivery and shared registry visibility before relying on either in the implementation.

### Owner activation

After event registration, do one bounded local pass when titles are on: list marked roots in that copy's exact
directory, at most four pages of 50. For an incomplete live binding with a busy timestamp older than this
server, no newer idle timestamp, and nothing running now, restore `WAITING` without changing cells. Also apply a
previously verified merge if cleanup is now satisfied. Skip fresh, completed, silenced, moved, and unmarked
titles. Read or restore stored state only for candidates; no git processes or GitHub calls in this pass.

### Turn titles off

Trigger cleanup only after onboarding successfully saves an on→off change. Preview, cancellation, and a failed
save do not touch any title. Do one project-scoped list of marked roots, including archived sessions, at most
four pages of 50. The marker filter is a search hint; require the exact leading `U+FFFB` marker **and** a valid
meter prefix at the write. Strip only that prefix; keep the rest of the title byte-for-byte. List the session
titles themselves rather than relying on task records, since five-cell titles have no record. Never strip a
lookalike without the marker or a title the user already renamed.

Send each candidate to its current loaded owner's queue. A project preference generation fences new title
writes as soon as off is saved; order cleanup behind writes already sent, including affected IDs even if the
list snapshot missed their new prefix.
If a cold copy later activates while the setting is explicitly off, run the same bounded pass for its directory.
Recheck that the setting is still off and the title is still marked before each write. Re-enable, a move, or
unload invalidates queued cleanup. Preserve any live task. After a successful strip, remember the exact bare
title with its task so the next ordinary evaluation after re-enable may resume the meter only if the title
still matches. Do not clear a stored user opt-out. If the title changed while off, or saving the cleanup
state failed, fall back to respecting the unmarked title. Finished sessions need no new record.
Partial cleanup is best effort, does not undo a saved preference, and must not be reported as clearing all
titles. Direct settings-file edits and uninstall do not trigger a project-wide cleanup.

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
  or disabled/moved/silenced target. Check cleanup on and off, folder removal, and a cold owner. Verify the
  one-lookup budget and that unrelated turns did not change.
- **Startup and disable:** persisted old busy timestamp versus a current or idle run, with a running child
  control. Verify one bounded list, zero GitHub calls, a completed and archived title without a record, an
  unmarked lookalike, preserved user topics and opt-outs, and concurrent enable, move, close, and in-flight
  writes. On re-enable, a live task resumes only if the bare title was not changed while off. A scan failure
  leaves the preference saved.
- **Progress:** two loader copies; root, descendant, and unrelated sessions; duplicate and multi-file steps;
  newly edited plan; reverted files; an untracked file; repeated hints; lost folder; and a title currently
  showing `WAITING`. Check local-read bounds and no mid-turn `gh` or first-title write. Check normal turn-end
  fallback and unchanged-turn storage behavior.
- **Native qualification before acceptance:** use a fresh private OpenCode lab and a local `gh` shim. Cover
  owner routing and worktree-removal delivery; project/directory list filters, including archived roots;
  restart/disable; and a meter advancing before a root finishes, with root and descendant changes. Prefer
  model-free checks for list, restart, removal, and settings behavior. Any additional live model turn requires
  separate authorization. Report untested host behavior as unverified.

Product changes belong in the existing title, fact, and onboarding paths; tests belong in `tests/plugin/`.
No new dependencies, public settings, injected notes, or README changes. Leave parser and queue details to
the builder where the contracts above do not pin them.
