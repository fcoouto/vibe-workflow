# Vibe Workflow project onboarding

## Question style

Every question uses the client's native question control, in the same form, so
the user never has to learn a new layout:

- **Header** of at most 30 characters.
- **Question** that ends with a question mark and names one decision.
- **Context** shown before the choices. It starts with the project path and
  defines every term the choices use, once. In a worktree, say where the
  setting is saved. Question 1 leaves the path and the worktree note to the
  opening line.
- **Two or three choices**, each with a label of one to five words and a
  one-line description of what happens.
- **Order and markers:** the recommended choice first, labeled "(Recommended)".
  In an existing project, put the saved choice first and label it "(Current)"
  instead, and keep it unless the user chooses otherwise. Question 1 keeps both
  markers: "(Current, Recommended)" when they are the same choice, otherwise
  "(Recommended)" stays on its own choice.
- **Plain text:** no backticks or emoji in anything the user sees.
- **Consent:** no answer is not consent. Stop on cancel.

## Questions

### 1. Smart Router

- Header: Smart Router
- Question: Use Smart Router for this project?
- Context: Smart Router suggests a provider, model and effort before work is delegated. Your client still runs every task. When it is saved on and the step 1 preview's readiness.state is setup_needed, add: It is on, but not set up yet.
- Choices:
  - Turn it on (Recommended) — Get a suggestion before each delegated task.
  - Leave it off — Keep choosing models yourself.
- Choices when it is saved on:
  - Keep it on (Current, Recommended) — Suggestions continue, or start once it is set up.
  - Turn it off — You choose models yourself. Run onboarding again to undo.

### 2. Pull request merging

- Header: Pull request merging
- Question: Who merges ready pull requests in this project?
- Context: Project: <project path>. Agents open each pull request (PR) as a [BLOCKED] draft and switch it to [READY] only when its checks and review are current. Reminders are advisory, not enforced. In a worktree, add: Saved in the main checkout, not this worktree.
- Choices:
  - I merge them (Recommended) — Agents mark the PR [READY] and wait for you to merge it.
  - Agents merge their own — Agents merge a PR once it is [READY], without asking you first.
  - Skip git workflow guidance — Saves all of this as off, including cleanup. Run onboarding again to change it.

### 3. Worktree cleanup

- Header: Worktree cleanup
- Question: Clean up after a pull request is merged in this project?
- Context: Project: <project path>. A worktree is the separate folder an agent works in for one task. Cleanup runs only after a merge is confirmed, and never removes unmerged work, uncommitted work or ignored files; it stops and tells you instead.
- Choices:
  - Clean up (Recommended) — Removes the task's worktree and its local branch after a confirmed merge.
  - Keep them — Leaves worktrees and branches in place. You can remove them yourself.

### 4. Remote branch cleanup

- Header: Remote branch cleanup
- Question: Delete merged branches on GitHub automatically?
- Context: Project: <project path>. GitHub, not the agent, can delete a pull request's branch as soon as it merges. It is off for <owner/repo>. It applies to every merge there, and you can turn it off again in GitHub's settings.
- Choices:
  - Turn it on (Recommended) — GitHub deletes a PR's branch when it merges. Runs gh repo edit --delete-branch-on-merge.
  - Not now — Leaves the setting off.

### 5. Session titles

- Header: Session titles
- Question: Show task progress in the session title?
- Context: Project: <project path>. The session title is the chat's name in the session list. The meter is a five-step progress bar added to the title; the title shows WAITING while the session needs you. Only the meter is changed. If you delete the meter from a title, that chat stops updating.
- Choices:
  - Show progress (Recommended) — Adds the meter to the title and updates it when each turn ends.
  - Keep titles as they are — Leaves every title alone. You can turn this on later.

## Steps

1. Call `vibe_workflow_onboard` with `{"v":1,"op":"preview"}`. Show the
   preview's `project` path as the project, and the client. When `root` differs,
   the session is in a worktree; say its Smart Router file is saved in that
   worktree. Tell the user what is coming in one line: **Up to five short questions.**
   Their answers are the consent to save. If blocked, explain the fixed reason
   and stop.
2. Ask question 1. With no saved choice, use its default choices (the plain
   Choices list). When the preview's `previous_enabled` is `true`, use the saved-on
   choices. When it is `false`, use the default choices with Leave it off (Current) first and Turn it on (Recommended)
   second.
3. Call `vibe_workflow_onboard` with `{"v":1,"op":"preview","feature":"git_workflow"}`.
   If that preview is blocked, explain the fixed reason, skip questions 2, 3 and 4 and
   leave git workflow guidance off; for `project_root_unavailable`, say the main
   checkout could not be found from this worktree and onboarding must run there.
   Otherwise ask question 2. In an existing project, show the saved choice as the
   current one (the preview's `previous_enabled` and `previous_agent_merge` hold
   it). Choosing **Skip git workflow guidance** skips questions 3 and 4.
4. Ask question 3, unless question 2 was skipped. In an existing project, show a saved
   `previous_worktree_cleanup` of `true` or `false` as the current choice; `null` means
   it was never asked, so ask without a current marker.
5. Unless question 2 was skipped, run `gh repo view --json nameWithOwner,deleteBranchOnMerge,viewerPermission`
   before question 4. Ask question 4 only when the call succeeds, `deleteBranchOnMerge` is `false` and `viewerPermission` is `ADMIN`.
   Otherwise skip it and report one line:
   - `GitHub auto-delete: already on.` when `deleteBranchOnMerge` is `true`.
   - `GitHub auto-delete: not changed (admin access is needed).` when it is `false` and the user is not an admin.
   - `GitHub auto-delete: not available (gh is missing or not logged in).` when the call fails because `gh` is missing or not logged in.
   - `GitHub auto-delete: not available (this remote is not on GitHub).` when there is no GitHub remote.
   - `GitHub auto-delete: not available (could not be read).` for any other failure.
6. Call `vibe_workflow_onboard` with `{"v":1,"op":"preview","feature":"session_titles"}`.
   Ask question 5, also when git workflow guidance was skipped. In an existing project,
   show the saved choice as the current one (the preview's `previous_enabled`; `null`
   means it was never asked, so ask without a current marker). If that preview is
   blocked, skip question 5, leave session titles off and report
   **Session titles: not saved.** followed by the fixed reason; for
   `project_root_unavailable`, say onboarding must run from the main checkout.
7. If an answer changes a proposal, preview that feature again with the selected
   booleans (`enabled`, `agent_merge` and `worktree_cleanup` for git workflow;
   **Skip** is all three `false`; `enabled` for session titles). If every preview says `change: "none"` and no
   GitHub change is pending, say **Nothing to change.**, keep the saved choices and
   stop. A yes to question 4 counts as a change, so it is never swallowed by that stop.
   Otherwise say what will be saved, in one plain line per change and not as a
   question, for example **Saving: Smart Router On, Git workflow I merge them, cleanup on, Session titles On —
   .vibe-workflow/settings.json (created). GitHub: turn on auto-delete of merged branches for <owner/repo>.**
   Use the full `path` when `root` differs. Session titles are saved in the main checkout too.
   For git workflow, add that the main checkout's
   settings file will change and that committing it is the user's step.
   Say it in order: the Saving line comes before anything is applied, and a cancel at any question or a denied native file-edit permission means no GitHub change.
8. Apply Smart Router first: call `vibe_workflow_onboard` with `v: 1`,
   `op: "apply"`, the preview's `enabled`, and `expected_snapshot` set to its
   `snapshot`. The features share one file, so then
   preview `git_workflow` again with the answered values. If its `change` and
   values match what the user answered, apply it with all three booleans and that
   fresh `snapshot`, for example
   `{"v":1,"op":"apply","feature":"git_workflow","enabled":true,"agent_merge":false,"worktree_cleanup":true,"expected_snapshot":"<snapshot>"}`;
   otherwise ask the affected question again and say why. The user's answers are the
   consent; native file-edit permission is the only other prompt, and it stays the
   client's. If edit is denied or permission is unavailable, do not write another way.
   Unless question 5 was skipped, apply session titles last, the same way: preview `session_titles` again with the
   answered `enabled` and apply it with that fresh `snapshot`, for example
   `{"v":1,"op":"apply","feature":"session_titles","enabled":true,"expected_snapshot":"<snapshot>"}`.
   Report each feature that was not saved as **<Feature>: not saved.** followed by how
   to recover, for example **Git workflow: not saved. Run onboarding from the main
   checkout.** If settings changed or the result is lost, inspect with a fresh preview
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
   - Smart Router, `ready`: Smart Router: On · Configuration checked — route eligibility is checked when work is delegated.
   - Smart Router, `setup_needed`: Smart Router: On · Setup needed — followed by the reported reason.
   - Git workflow, `off`: Git workflow: Off — no git workflow guidance.
   - Git workflow, `ready`: Git workflow: On · I merge — reminders registered; not yet observed in this OpenCode. Advisory, not enforced.
   - Git workflow, `ready`, agents merge: Git workflow: On · Agents merge — reminders registered; not yet observed in this OpenCode. Advisory, not enforced.
   - Git workflow, `ready`, cleanup on: Git workflow: On · I merge, cleanup on — reminders registered; not yet observed in this OpenCode. Advisory, not enforced. (Agents merge: Git workflow: On · Agents merge, cleanup on — …)
   - Git workflow, `setup_needed` with `guard_unavailable`: Git workflow: On · I merge — guidance only; reminders are not available in this OpenCode. (Agents merge: Git workflow: On · Agents merge — guidance only; reminders are not available in this OpenCode.) Add ", cleanup on" after the merge choice when cleanup is on.
   - Git workflow, any other `setup_needed` reason: give the reported reason.
   - Session titles, `off`: Session titles: Off — titles are left alone.
   - Session titles, `ready`: Session titles: On — updated at the end of each turn.
   - Session titles, `setup_needed` with `events_unavailable`: Session titles: On · Setup needed — this OpenCode does not deliver session events.

   Add the GitHub line from step 5 or step 9 when question 4 ran or was skipped for a reason.
   An enabled preference is not proof of account availability or entitlement.
   Finish with one line: **Run onboarding again to change them.**

Do not install anything, modify unrelated settings or ask about unshipped
capabilities. When an enabled selector returns a fallback `notice`, show it
before continuing with the client's existing selection and permissions.
