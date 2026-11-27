# Private report storage

Reports live in `.vibe-workflow/security-reports/` in the main checkout, not the
task worktree. Never create, edit or rename anything in that folder by hand. The
bundled script makes every check and every write.

The report text never goes into a shell command: not as an argument, not inline
after the command, not through a pipe. A line of the report could end such a
block, and the shell would run what follows it. The text reaches the script
through a file:

1. Make a private temporary folder outside the repository. `mktemp -d` makes one
   with permissions `0700` under the system temp folder.
2. Write the report into a file in that folder with your file-write tool, under
   a name you choose, with permissions `0600` where the tool can set them. Quote
   secrets as file, line and type only.
3. Run the script with every argument shell-quoted:

```sh
python3 -I -B '<skill folder>/scripts/store_report.py' \
  --cwd '<checkout under review>' --mode '<full or ship>' --topic '<slug>' \
  --from '<temporary file>'
```

`<skill folder>` is the `Base directory for this skill:` path. The topic is one
short slug matching `[a-z0-9][a-z0-9-]{0,60}`.

- **Saved.** The script prints the report's absolute path, exits 0 and deletes the
  temporary file. Give the user that path and remove the empty temporary folder.
- **Refused.** It prints one line, `refused: <reason>`, exits 2, writes no report
  and leaves the temporary file in place. Give the report in chat and explain
  why, then delete the temporary file. Do not write the report another way.
- You cannot write outside the repository, Python 3.11 or newer is missing, or
  anything else fails: also give the report in chat and explain why.

One limit: the report text appears once in the session's tool history, as the
file-write call. The findings are already in the session as your own output, so
this adds no new reader; the file keeps the text out of shell parsing, not out
of the transcript.

What the script does, so a refusal can be explained:

1. Reads the report from `--from` as bytes. It refuses a symlink, anything that
   is not a regular file, and an empty report.
2. Finds the main checkout from the first record of
   `git worktree list --porcelain`, ignoring inherited Git variables and reading
   path names as bytes. It refuses when this is not a Git repository, when that
   record is bare, or when the checkout cannot be resolved.
3. Refuses when `.vibe-workflow` or the reports folder is a symlink or not a
   directory, or when `git ls-files`, run against the main checkout's own index,
   lists anything in the folder.
4. Refuses when the main checkout or `.vibe-workflow` belongs to another user or
   is writable by its group or by everyone: whoever can write a folder can swap
   what is inside it. The script never changes their permissions.
5. Creates missing folders with permissions `0700`. It refuses a reports folder
   that belongs to another user and sets an existing one to `0700`.
6. Keeps the folder's `.gitignore` at exactly `*` and a newline. A differing
   regular file is replaced whole, not edited in place; a symlink or anything
   else there is refused.
7. Names the report `YYYY-MM-DD-HHMM-<full|ship>-<topic>.md` and creates it new
   with permissions `0600`. When the name is taken it tries `-2`, `-3` and so on
   up to `-99`. Reports are never overwritten and a link is never followed.
8. Confirms `git -C <main checkout> check-ignore -q <report path>`, then that the
   name Git checked is still the folder and the file it opened. Only then is the
   report text written. Otherwise it refuses and removes the empty file it
   created; a different file found under that name is left untouched.

Never `git add -f` anything in this folder. Never copy a report into a commit,
PR, issue or log. Tell the user once that ignored does not mean secret: backups
and cloud sync can include reports, and `git clean -x` can delete them.
