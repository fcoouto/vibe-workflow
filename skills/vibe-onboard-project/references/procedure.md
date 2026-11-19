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
3. If the answer changes the proposal, preview again with `enabled` set to the
   selected boolean. Show the project, client, relative settings path
   `.vibe-workflow/settings.json`, preference and whether the file would be
   created, updated or left unchanged. Show only the proposed change, not
   unrelated settings. Ask for explicit confirmation and wait.
4. Only after the user confirms this specific preview, call
   `vibe_workflow_onboard` with `v: 1`, `op: "apply"`, the preview's `enabled`,
   and `expected_snapshot` set to its `snapshot`. Chat confirmation and native
   file-edit permission are separate. If edit is denied, do not write another
   way. If settings changed or the result is lost, inspect with a fresh preview
   instead of repeating apply; get fresh confirmation for any changed preview.
   If apply reports `project_settings_lossy`, say that existing numbers cannot
   be preserved by the writer. Do not rewrite the file; ask the user to review it.
5. Report actual readiness. For `off`, say **Off — existing model selection**.
   For `ready`, say **On · Ready — configuration checked; route eligibility is
   checked when work is delegated.** For `setup_needed`, say **On · Setup needed**
   and explain the reported reason. An enabled preference is not proof of account
   availability or entitlement.

Do not install anything, modify unrelated settings or ask about unshipped
capabilities. When an enabled selector returns a fallback `notice`, show it
before continuing with the client's existing selection and permissions.
