# Vibe Workflow project onboarding

## Question style

Every question uses the client's native question control, in the same form, so
the user never has to learn a new layout. Use each Header, Question and choice label
exactly as written, apart from the markers and the saved choice's place.

- **Header** of at most 30 characters.
- **Question** that ends with a question mark and names one decision. In OpenCode, the control's question text is the Context followed by the Question; the control has no other field for it.
- **Context** shown before the choices. It defines every term the choices use,
  once, and adds the status sentence its question names, when that applies. The
  project path and where settings are saved belong to the opening line only.
- **Two or three choices**, each with a label of one to five words and a
  one-line description of what happens.
- **Choice list:** with nothing saved, use the plain Choices list. When the saved
  value is on and the question has a "Choices when it is saved on" list, use that
  list. Otherwise use the plain list with the saved choice moved first.
- **Markers:** the recommended choice is labeled "(Recommended)" and comes first
  when nothing is saved. A saved choice is labeled "(Current)" and is kept unless
  the user chooses otherwise. When the saved choice is also the recommended one,
  label it "(Current, Recommended)"; otherwise "(Recommended)" stays on its own
  choice.
- **Plain text:** no backticks or emoji in anything the user sees.
- **Consent:** no answer is not consent. Stop on cancel.

## Questions

### 1. Smart Router

- Header: Smart Router
- Question: Use Smart Router for this project?
- Context: Smart Router suggests a provider, model and effort before work is delegated. Your client still runs every task. It downloads public model scores from openrouter.ai about weekly and sends no credentials. When it is saved on and the step 1 preview's readiness.state is setup_needed, add: It is on, but not ready yet. When readiness.reason is selector_unavailable, also add: Check that Python and the plugin package are available.
- Choices:
  - Turn it on (Recommended) — Get a suggestion before each delegated task.
  - Leave it off — Keep choosing models yourself.
- Choices when it is saved on:
  - Keep it on (Current, Recommended) — Suggestions start once it is ready.
  - Turn it off — You choose models yourself. Run onboarding again to undo.

### 2. Pull request merging

- Header: Pull request merging
- Question: Who merges ready pull requests in this project?
- Context: Agents open each pull request as a [BLOCKED] draft and mark it [READY] only when its checks and review are current for the latest commit. These are reminders to the agent, not hard rules. When the git workflow status preview in step 3 reports readiness.reason guard_unavailable, add: Reminders are not available in this OpenCode, so this is guidance only.
- Choices:
  - I merge them (Recommended) — Agents mark the PR [READY] and wait for you.
  - Agents merge their own — Agents merge once the PR is [READY], without asking.
  - Turn off git guidance — No PR reminders, and no cleanup. Run onboarding again to change it.

### 3. Worktree cleanup

- Header: Worktree cleanup
- Question: Clean up after a pull request is merged in this project?
- Context: Agents work on each task in a separate folder, called a worktree. After a confirmed merge, cleanup removes that folder, its local branch and older merged worktrees, and deletes tool caches such as `node_modules` without asking. Before the pull request is marked ready, it asks about any other files in the task's folder. Older worktrees with such files are skipped and reported, never asked about. It never removes unmerged work.
- Choices:
  - Clean up (Recommended) — Remove the folder and branch after each merge.
  - Keep them — You remove worktrees and branches yourself.
- Choices when it is saved on:
  - Keep cleaning up (Current, Recommended) — Remove the folder and branch after each merge.
  - Stop cleaning up — You remove worktrees and branches yourself.

### 4. GitHub branch cleanup

- Header: GitHub branch cleanup
- Question: Delete merged branches on GitHub automatically?
- Context: GitHub can delete a pull request's branch when it merges. This repository setting is off for <owner/repo> and applies to every merge there, not only agents'.
- Choices:
  - Turn it on (Recommended) — GitHub deletes each merged PR's branch. You can turn it off in GitHub settings.
  - Leave it off — Merged branches stay on GitHub.

### 5. Session titles

- Header: Session titles
- Question: Show task progress in the session title?
- Context: Adds a five-step progress bar to the session title, the chat's name in the session list, and WAITING when the chat needs you. The rest of the title is left alone. Delete the bar and that chat stops updating. When the session titles status preview in step 6 reports readiness.reason events_unavailable, add: This OpenCode does not send session events, so titles will not update yet.
- Choices:
  - Show progress (Recommended) — Updates at the end of each turn.
  - Leave titles alone — Chat names stay as they are.
- Choices when it is saved on:
  - Keep showing it (Current, Recommended) — Updates at the end of each turn.
  - Stop showing it — Titles are left alone from now on.

## Steps

Before step 1, check where the session runs: `git rev-parse --path-format=absolute --absolute-git-dir --git-common-dir`.
When both paths are the same in a git repository, the session is in the primary checkout.
Say in one line that you are creating a worktree and moving this chat there. Call
`worktree_create` with the name `onboard-vibe-workflow` (another name when it exists), move the
session to the returned directory with `session.move`, and confirm the working directory in a
later step, because the move applies at the next step boundary. If any part fails, stop and
say why; never save in the main checkout instead. A linked worktree, or a directory that is
not a git repository, needs no move.

1. Call `vibe_workflow_onboard` with `{"v":1,"op":"preview"}`. If blocked, explain
   the fixed reason and stop. Otherwise, before question 1, write the opening line
   with the preview's `project` and `client`:
   **Project: <project>. Client: <client>. Up to five short questions.** Add that the
   choices are saved in this checkout's settings file, the preview's `path`.
   If you moved, stop before asking or saving unless the preview's `root` is the
   confirmed destination worktree.
   Their answers are the consent to save.
2. Ask question 1. The preview's `previous_enabled` is the saved value; `null`
   means nothing is saved. For `false`, that gives Leave it off (Current) first and
   Turn it on (Recommended) second.
3. Call `vibe_workflow_onboard` with `{"v":1,"op":"preview","feature":"git_workflow"}`.
   If that preview is blocked, explain the fixed reason, skip questions 2, 3 and 4 and
   leave git workflow guidance off.
   Otherwise ask question 2. The preview's `previous_enabled` and
   `previous_agent_merge` hold the saved choice: `previous_enabled` `false` is Turn
   off git guidance; otherwise `previous_agent_merge` `true` is Agents merge their own and `false` is I merge them;
   `null` means nothing is saved. The status preview is this preview, except when
   `previous_enabled` is `false`: then preview again with
   `{"v":1,"op":"preview","feature":"git_workflow","enabled":true,"agent_merge":false,"worktree_cleanup":false}`
   and use only its readiness. Choosing **Turn off git guidance** skips questions 3 and 4.
4. Ask question 3, unless question 2 was skipped or turned git guidance off. The
   preview's `previous_worktree_cleanup` is the saved value; `null` means it was
   never asked. For `false`, that gives Keep them (Current) first and Clean up (Recommended) second.
5. Unless question 2 was skipped or turned git guidance off, run `gh repo view --json nameWithOwner,deleteBranchOnMerge,viewerPermission`
   before question 4. Ask question 4 only when the call succeeds, `deleteBranchOnMerge` is `false` and `viewerPermission` is `ADMIN`.
   Otherwise skip it and report one line:
   - `GitHub auto-delete: already on.` when `deleteBranchOnMerge` is `true`.
   - `GitHub auto-delete: not changed (admin access is needed).` when it is `false` and the user is not an admin.
   - `GitHub auto-delete: not available (gh is missing or not logged in).` when the call fails because `gh` is missing or not logged in.
   - `GitHub auto-delete: not available (this remote is not on GitHub).` when there is no GitHub remote.
   - `GitHub auto-delete: not available (could not be read).` for any other failure.
6. Call `vibe_workflow_onboard` with `{"v":1,"op":"preview","feature":"session_titles"}`.
   Ask question 5, also when git guidance was turned off or skipped. The preview's
   `previous_enabled` is the saved value; `null` means it was never asked. For `false`, that gives Leave titles alone (Current) first and Show progress (Recommended) second. The status
   preview is this preview, except when `previous_enabled` is `false`: then preview
   again with `{"v":1,"op":"preview","feature":"session_titles","enabled":true}` and use
   only its readiness. If the first session titles preview is
   blocked, skip question 5, leave session titles off and report
   **Session titles: not saved.** followed by the fixed reason.
7. Keep the `previous_*` values from the previews used to ask the questions.
   If an answer changes a proposal, preview that feature again with the selected
   booleans (`enabled`, `agent_merge` and `worktree_cleanup` for git workflow;
   Turn off git guidance is all three `false`; `enabled` for session titles).
   Before accepting a fresh snapshot, compare its `previous_*` values to those used
   for the questions, including Smart Router's `previous_enabled` from question 1.
   If a value changed (including to or from `null`), stop before saving, explain
   that settings changed, and ask the user to run onboarding again.
   If every preview says `change: "none"` and no GitHub change is pending, say
   **Nothing to change.** and keep the saved choices. If Git workflow readiness
   has reason `project_root_unavailable`, report its saved-local status from
   step 10. Then stop.
   A yes to question 4 counts as a change, so it is never swallowed by that stop.
   Otherwise say what will be saved, in one plain line per change and not as a
   question, for example **Saving: Smart Router On, Git workflow I merge them, cleanup on, Session titles On —
   .vibe-workflow/settings.json (created). GitHub: turn on auto-delete of merged branches for <owner/repo>.**
   Name the preview's full `path`, and add that committing and merging that file is the user's step.
   Say it in order: the Saving line comes before anything is applied, and a cancel at any question or a denied native file-edit permission means no GitHub change.
8. Apply Smart Router first: call `vibe_workflow_onboard` with `v: 1`,
   `op: "apply"`, the preview's `enabled`, and `expected_snapshot` set to its
   `snapshot`. The features share one file, so then
   preview `git_workflow` again with the answered values. Compare its
   `previous_enabled`, `previous_agent_merge`, and `previous_worktree_cleanup`
   with the values used for the questions, including `null`. Do not compare
   `change`: the first save can turn `create` into `update` without changing
   any git workflow choice. If the previous values still match, apply with all
   three answered booleans and the fresh `snapshot`, for example
   `{"v":1,"op":"apply","feature":"git_workflow","enabled":true,"agent_merge":false,"worktree_cleanup":true,"expected_snapshot":"<snapshot>"}`;
   if a previous value changed, ask the affected question again and say why.
   The user's answers are the
   consent; native file-edit permission is the only other prompt, and it stays the
   client's. If edit is denied, permission is unavailable or apply fails for any
   reason, do not write another way: never import or run the plugin's files outside the tool, and never write the settings file yourself.
   Unless question 5 was skipped, apply session titles last, the same way: preview
   `session_titles` again with the answered `enabled`. Ask again if `previous_enabled`
   changed from the value used for question 5. Apply with the confirmed answer
   and fresh `snapshot`, for example
   `{"v":1,"op":"apply","feature":"session_titles","enabled":true,"expected_snapshot":"<snapshot>"}`.
   Report each feature that was not saved as **<Feature>: not saved.** followed by how
   to recover, for example **Git workflow: not saved. Run onboarding again.** If settings changed or the result is lost, inspect with a fresh preview
   instead of repeating apply, and ask the affected question again if the proposal
   changed. If apply reports `project_settings_lossy`, say that existing numbers cannot
   be preserved by the writer. Do not rewrite the file; ask the user to review it.
9. On a yes to question 4, run `gh repo edit <owner/repo> --delete-branch-on-merge`. It
   runs after the settings apply and is independent of a technical failure there (busy,
   changed or lossy settings), but not of a cancel or a denied permission. Read the setting
   back with the same `gh repo view` call and report one line: `GitHub auto-delete: turned on.`,
   or `GitHub auto-delete: not changed.` with the reported reason, or that it could not be
   verified. Never claim the change without the read-back.
10. Report actual readiness, one line per feature, in the form
   **<Feature>: <state> — <detail>.** Use these lines, exactly:
   - Smart Router, `off`: Smart Router: Off — existing model selection.
   - Smart Router, `ready`: Smart Router: On · Scores loaded — route eligibility is checked when work is delegated.
   - Smart Router, `setup_needed`: Smart Router: On · Setup needed — followed by the reported reason.
   - Git workflow, `off`: Git workflow: Off — no git workflow guidance.
   - Git workflow, `ready`: Git workflow: On · I merge — reminders registered; not yet observed in this OpenCode. Advisory, not enforced.
   - Git workflow, `ready`, agents merge: Git workflow: On · Agents merge — reminders registered; not yet observed in this OpenCode. Advisory, not enforced.
   - Git workflow, `ready`, cleanup on: Git workflow: On · I merge, cleanup on — reminders registered; not yet observed in this OpenCode. Advisory, not enforced. (Agents merge: Git workflow: On · Agents merge, cleanup on — …)
   - Git workflow, `setup_needed` with `guard_unavailable`: Git workflow: On · I merge — guidance only; reminders are not available in this OpenCode. (Agents merge: Git workflow: On · Agents merge — guidance only; reminders are not available in this OpenCode.) Add ", cleanup on" after the merge choice when cleanup is on.
   - Git workflow saved or unchanged, `setup_needed` with `project_root_unavailable`: Git workflow: Saved locally — the main checkout could not be resolved; agent merge and cleanup are unavailable.
   - Git workflow, any other `setup_needed` reason: give the reported reason.
   - Session titles, `off`: Session titles: Off — titles are left alone.
   - Session titles, `ready`: Session titles: On — updated at the end of each turn.
   - Session titles, `setup_needed` with `events_unavailable`: Session titles: On · Setup needed — this OpenCode does not deliver session events.

   Add the GitHub line from step 5 or step 9 when question 4 ran or was skipped for a reason.
   An enabled preference is not proof of account availability or entitlement.
   When `root` differs from `project` and Git workflow readiness does not have reason
   `project_root_unavailable`, add before the last line:
   **Git workflow and cleanup take effect only after this branch is merged into main.**
   Smart Router and session titles apply in this worktree now and in the project after the merge.
   Offer to open the pull request with `vibe-pull-requests`; do not merge unasked.
   Finish with one line: **Run onboarding again to change them.**

Do not install anything, modify unrelated settings or ask about unshipped
capabilities. When an enabled selector returns a fallback `notice`, show it,
then continue without a recommendation, within the client's permissions.
