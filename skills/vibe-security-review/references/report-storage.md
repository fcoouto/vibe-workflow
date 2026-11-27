# Private report storage

Use `.vibe-workflow/security-reports/` in the main checkout, not the task
worktree. Find the main checkout from the first entry of
`git worktree list --porcelain`. If this is not a Git repository or no main
checkout is found, do not write a report; give it in chat and explain why.

Before every write:

1. Refuse storage if `.vibe-workflow` or the reports folder is a symlink, or
   if `git ls-files` lists anything inside the reports folder. Give the report
   in chat and explain why.
2. Create the folder if missing, with permissions `0700` where supported. Its
   `.gitignore` must contain exactly `*`; rewrite it when missing or different.
3. Choose `YYYY-MM-DD-HHMM-<full|ship>-<topic>.md`; append `-2`, `-3` and so on
   before `.md` when taken. Reports are never overwritten.
4. Run `git -C <main checkout> check-ignore -q <report path>` for the chosen
   path before writing. If it fails, do not write; give the report in chat and
   explain why.
5. Create the report with `0600` permissions where supported. Quote secrets as
   file, line and type only.

Never `git add -f` anything in this folder. Never copy a report into a commit,
PR, issue or log. Tell the user once that ignored does not mean secret: backups
and cloud sync can include reports, and `git clean -x` can delete them.
