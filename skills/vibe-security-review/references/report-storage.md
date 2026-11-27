# Private report storage

Reports live in `.vibe-workflow/security-reports/` in the main checkout, not the
task worktree. Never create, edit or rename anything in that folder by hand. The
bundled script makes every check and every write. Run it with the report on
standard input, through a quoted heredoc so the shell expands nothing in it:

```sh
python3 -I -B <this skill's folder>/scripts/store_report.py \
  --cwd <the checkout under review> --mode <full|ship> --topic <slug> <<'REPORT'
<report text>
REPORT
```

`<this skill's folder>` is the `Base directory for this skill:` path. The topic
is one short slug matching `[a-z0-9][a-z0-9-]{0,60}`. In the report, quote
secrets as file, line and type only.

- **Saved.** The script prints the report's absolute path and exits 0. Give the
  user that path.
- **Refused.** It prints one line, `refused: <reason>`, and exits 2 with no report
  written. Give the report in chat and explain why. Do not write the file another
  way.
- Python 3.11 or newer missing, or any other failure: also give the report in chat
  and explain why.

What the script does, so a refusal can be explained:

1. Finds the main checkout from the first record of
   `git worktree list --porcelain`, ignoring inherited Git variables. It refuses
   when this is not a Git repository, when that record is bare, or when the
   checkout cannot be resolved.
2. Refuses when `.vibe-workflow` or the reports folder is a symlink or not a
   directory, when the reports folder belongs to another user, or when
   `git ls-files`, run against the main checkout's own index, lists anything in
   the folder.
3. Creates missing folders with permissions `0700` and sets an existing reports
   folder to `0700`.
4. Keeps the folder's `.gitignore` at exactly `*` and a newline. A differing
   regular file is replaced whole, not edited in place; a symlink or anything
   else there is refused.
5. Names the report `YYYY-MM-DD-HHMM-<full|ship>-<topic>.md` and creates it new
   with permissions `0600`. When the name is taken it tries `-2`, `-3` and so on
   up to `-99`. Reports are never overwritten and a link is never followed.
6. Confirms `git -C <main checkout> check-ignore -q <report path>` before the
   report text is written, and removes the empty file if Git does not ignore it.

Never `git add -f` anything in this folder. Never copy a report into a commit,
PR, issue or log. Tell the user once that ignored does not mean secret: backups
and cloud sync can include reports, and `git clean -x` can delete them.
