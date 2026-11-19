# Vibe Workflow project onboarding

1. Call `vibe_workflow_onboard` with `{"v":1,"op":"preview"}`. Show the
   preview's `project` path as the project, and the client. When `root`
   differs, the session is in a worktree; say its settings file is saved in that
   worktree. If blocked, explain the fixed reason and stop.
2. Ask **Use Smart Router for this project?**, with the `project` path in the
   question's context, and these choices:

   **Use Smart Router — default**\
   Automatically get provider, model, and effort recommendations before delegating
   work. Your client still launches and controls the tasks.

   **Keep existing model selection**\
   Continue with your current model-selection workflow. You can enable Smart Router
   later.

   ON is the proposed default only for a new project. In an existing project,
   show the saved choice and keep it unless the user chooses otherwise. No
   answer is not consent. Stop on cancel.
3. Call `vibe_workflow_onboard` with `{"v":1,"op":"preview","feature":"pull_requests"}`.
   If that preview is blocked, explain the fixed reason, skip this question and
   leave pull request guidance off; for `project_root_unavailable`, say the main
   checkout could not be found from this worktree and onboarding must run there.
   Otherwise ask **Who merges `[READY]` PRs in this project?** and these choices:

   **I merge them. Agents mark the PR `[READY]` and wait.** — default

   **Agents merge their own `[READY]` PRs.**

   **Skip pull request guidance.**

   Explain that agents open each PR as a `[BLOCKED]` draft and mark it `[READY]`
   only when its checks and review are current, and that reminders are advisory,
   not enforced. In an existing project show the saved choice (`previous_enabled`,
   `previous_agent_merge`) and keep it unless the user chooses otherwise. No
   answer is not consent. Stop on cancel.
4. If an answer changes a proposal, preview that feature again with the selected
   booleans (`enabled`, and `agent_merge` for pull requests; **Skip** is
   `enabled: false` and `agent_merge: false`). Show the project, client, relative
   settings path `.vibe-workflow/settings.json` (the full `path` when `root`
   differs), each preference and whether the file would be created, updated or
   left unchanged. For pull requests, say the main checkout's settings file will
   change and that committing it is the user's step. Show only the proposed
   changes, not unrelated settings. Ask for one explicit confirmation covering
   both and wait.
5. Only after the user confirms these specific previews, apply Smart Router
   first: call `vibe_workflow_onboard` with `v: 1`, `op: "apply"`, the preview's
   `enabled`, and `expected_snapshot` set to its `snapshot`. Both features share
   one file on a primary checkout, so then preview `pull_requests` again with the
   confirmed values. If its `change` and values match what the user confirmed,
   apply it with `feature: "pull_requests"`, both booleans and that fresh
   `snapshot`, without asking again; otherwise ask again. Chat confirmation and
   native file-edit permission are separate. If edit is denied or permission is
   unavailable, do not write another way; for pull requests tell the user to run
   onboarding from the main checkout. If settings changed or the result is lost,
   inspect with a fresh preview instead of repeating apply; get fresh
   confirmation for any changed preview. If apply reports
   `project_settings_lossy`, say that existing numbers cannot be preserved by the
   writer. Do not rewrite the file; ask the user to review it.
6. Report actual readiness for each. Smart Router: for `off`, say **Off — existing
   model selection**. For `ready`, say **On · Ready — configuration checked; route
   eligibility is checked when work is delegated.** For `setup_needed`, say
   **On · Setup needed** and explain the reported reason. An enabled preference is
   not proof of account availability or entitlement. Pull requests: for `off`, say
   **Off — no pull request guidance**. For `ready`, say
   **On · Reminders registered — not yet observed in this OpenCode; advisory only, not enforced.**
   For `setup_needed` with `guard_unavailable`, say
   **On · Guidance only — reminders are not available in this OpenCode.**

Do not install anything, modify unrelated settings or ask about unshipped
capabilities. When an enabled selector returns a fallback `notice`, show it
before continuing with the client's existing selection and permissions.
