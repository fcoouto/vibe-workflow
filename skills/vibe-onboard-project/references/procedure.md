# Vibe Workflow project onboarding

## Question style

Every question uses the client's native question control, in the same form, so
the user never has to learn a new layout:

- **Header** of at most 30 characters.
- **Question** that ends with a question mark and names one decision.
- **Context** shown before the choices. It starts with the project path and
  defines every term the choices use, once. In a worktree, say where the
  setting is saved.
- **Two or three choices**, each with a label of one to five words and a
  one-line description of what happens.
- **Order and markers:** the recommended choice first, labeled "(Recommended)".
  In an existing project, put the saved choice first and label it "(Current)"
  instead, and keep it unless the user chooses otherwise.
- **Plain text:** no backticks or emoji in anything the user sees.
- **Consent:** no answer is not consent. Stop on cancel.

## Questions

### 1. Smart Router

- Header: Smart Router
- Question: Use Smart Router for this project?
- Context: Project: <project path>. Smart Router recommends a provider, model and effort before work is delegated. It only advises.
- Choices:
  - Use Smart Router (Recommended) — Automatically get provider, model, and effort recommendations before delegating work. Your client still launches and controls the tasks.
  - Keep existing model selection — Continue with your current model-selection workflow. You can enable Smart Router later.

### 2. Pull request merging

- Header: Pull request merging
- Question: Who merges ready pull requests in this project?
- Context: Project: <project path>. Agents open each pull request (PR) as a [BLOCKED] draft and switch it to [READY] only when its checks and review are current. Reminders are advisory, not enforced. In a worktree, add: Saved in the main checkout, not this worktree.
- Choices:
  - I merge them (Recommended) — Agents mark the PR [READY] and wait for you to merge it.
  - Agents merge their own — Agents merge a PR once it is [READY], without asking you first.
  - Skip pull request guidance — Saves this as off. No PR reminders. Run onboarding again to change it.

### 3. Save choices

- Header: Save onboarding choices
- Question: Smart Router: On. Pull requests: I merge them. Save both?
- Context: Project: <project path>. Replace the question's choices with the real ones, naming only features that change. Then list each change: the settings file .vibe-workflow/settings.json (the full path in a worktree), whether it would be created, updated or left unchanged, and that committing the main checkout's file is the user's step.
- Choices:
  - Save both (Recommended) — Writes the choices to the settings file.
  - Change a choice — Go back and answer again.
  - Cancel — Saves nothing.

## Steps

1. Call `vibe_workflow_onboard` with `{"v":1,"op":"preview"}`. Show the
   preview's `project` path as the project, and the client. When `root` differs,
   the session is in a worktree; say its Smart Router file is saved in that
   worktree. Tell the user what is coming in one line:
   **Two short questions, then one confirmation.** If blocked, explain the fixed
   reason and stop.
2. Ask question 1. In an existing project, show the saved Smart Router choice as
   the current one.
3. Call `vibe_workflow_onboard` with `{"v":1,"op":"preview","feature":"pull_requests"}`.
   If that preview is blocked, explain the fixed reason, skip question 2 and
   leave pull request guidance off; for `project_root_unavailable`, say the main
   checkout could not be found from this worktree and onboarding must run there.
   Otherwise ask question 2. In an existing project, show the saved choice as the
   current one (the preview's `previous_enabled` and `previous_agent_merge` hold
   it).
4. If an answer changes a proposal, preview that feature again with the selected
   booleans (`enabled`, and `agent_merge` for pull requests; **Skip** is
   `enabled: false` and `agent_merge: false`), and say what changed. If every
   preview says `change: "none"`, say **Nothing to change.**, keep the saved
   choices and stop. Otherwise show only the proposed changes, not unrelated
   settings, and ask question 3 once, covering both, and wait.
5. Only after the user confirms these specific previews, apply Smart Router
   first: call `vibe_workflow_onboard` with `v: 1`, `op: "apply"`, the preview's
   `enabled`, and `expected_snapshot` set to its `snapshot`. Both features share
   one file on a primary checkout, so then preview `pull_requests` again with the
   confirmed values. If its `change` and values match what the user confirmed,
   apply it with `feature: "pull_requests"`, both booleans and that fresh
   `snapshot`, without asking again; otherwise ask again and say why. Chat
   confirmation and native file-edit permission are separate. If edit is denied
   or permission is unavailable, do not write another way. Report each feature
   that was not saved as **<Feature>: not saved.** followed by how to recover,
   for example **Pull requests: not saved. Run onboarding from the main
   checkout.** If settings changed or the result is lost, inspect with a fresh
   preview instead of repeating apply; get fresh confirmation for any changed
   preview. If apply reports `project_settings_lossy`, say that existing numbers
   cannot be preserved by the writer. Do not rewrite the file; ask the user to
   review it.
6. Report actual readiness, one line per feature, in the form
   **<Feature>: <state> — <detail>.** Use these lines, exactly:
   - Smart Router, `off`: Smart Router: Off — existing model selection.
   - Smart Router, `ready`: Smart Router: On · Configuration checked — route eligibility is checked when work is delegated.
   - Smart Router, `setup_needed`: Smart Router: On · Setup needed — followed by the reported reason.
   - Pull requests, `off`: Pull requests: Off — no pull request guidance.
   - Pull requests, `ready`: Pull requests: On · I merge — reminders registered; not yet observed in this OpenCode. Advisory, not enforced.
   - Pull requests, `ready`, agents merge: Pull requests: On · Agents merge — reminders registered; not yet observed in this OpenCode. Advisory, not enforced.
   - Pull requests, `setup_needed` with `guard_unavailable`: Pull requests: On · I merge — guidance only; reminders are not available in this OpenCode. (Agents merge: Pull requests: On · Agents merge — guidance only; reminders are not available in this OpenCode.)
   - Pull requests, any other `setup_needed` reason: give the reported reason.

   An enabled preference is not proof of account availability or entitlement.
   Finish with one line: **Run onboarding again to change them.**

Do not install anything, modify unrelated settings or ask about unshipped
capabilities. When an enabled selector returns a fallback `notice`, show it
before continuing with the client's existing selection and permissions.
