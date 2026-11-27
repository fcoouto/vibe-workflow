# Council brief: rulings made while building the proportional security review

## Role
You are one of 3 council members (Fable, Astra, Opus). Answer independently. You cannot
see the other members' answers.

## The user's standing instructions
"What I really want is to ensure that when a feature is being delivered or a plan is about to be completed, it's safe to ship the code. I want every security review to be proportional to the changes made. So, if it's a small patch, check security proportionally. I don't want to run a full security review while applying a patch. For example, when a feature is added to the system or an application, we should make sure it's safe to ship from a security perspective."

"Plus, I also want a command—or maybe it should be a skill—that users can trigger to run a full security review of their project."

"Accept raw reads. And as soon a secret is exposed, request user to immediately rotate that."

"Before marking PR as [READY] call upon the @vibe-council for each point of the `Rullings I made` section."

## Bias
Weigh optimistic bias as well as pessimistic bias. Flag an item that flatters the
result as readily as one that is too cautious.

## Context
The vibe-workflow OpenCode plugin (this repository, branch `design/security-review`,
pull request #408, a [BLOCKED] draft) adds a `vibe-security-review` skill and
`/vibe-security-review` command: a ship check sized by tier (0-3) prompted at delivery by
advisory notes, a tier-3 read-only reviewer, an on-demand full project review, and private
reports written by `skills/vibe-security-review/scripts/store_report.py` into
`.vibe-workflow/security-reports/` of the main checkout. Prepare Workspace offers four
Trail of Bits skills. The build ran task by task with a fresh implementer and an
independent reviewer per task, then a final whole-branch behavior and security review and
three scoped fix waves. While building, the coordinator made rulings on the user's behalf;
you vote on each.

Read as needed: `docs/superpowers/specs/2026-11-27-security-review-design.md` (the design),
`docs/superpowers/plans/2026-11-27-security-review.md` (the plan, Tasks 1-8),
`skills/vibe-security-review/` (the skill and script), `src/pr-advice.js`,
`src/skill-advice.js`, `skills/vibe-prepare-my-workspace/scripts/check.py`, and the
"Security review: what is known" section at the end of `docs/development.md` (live probe
records). `AGENTS.md` holds the repository rules.

## Settled rules (not up for a vote)
- Advisory only: notes and skill text, no enforcement, no runtime database.
- No Trail of Bits text is copied into the package (CC-BY-SA-4.0 vs MIT).
- User decision: raw reads of changed files are accepted (a secret in a changed file reaches
  the coordinator's and reviewer's model); when a likely secret is found the user is told
  immediately to rotate it.
- User decision: partner skills keep one shared copy in `~/.agents/skills/`, read directly by
  clients that support that folder (OpenCode does), linked only where a client does not.
- User decision: the ship check is on in every onboarded project; a confirmed high or critical
  finding introduced or worsened by the change keeps the PR [BLOCKED]; reports live in the
  ignored `.vibe-workflow/security-reports/` folder.
You may comment on how items compound with these, not on whether to keep them.

## Measured facts
- Suites on the third-wave head `87ff0a1` (implementer-run): 647 Python tests OK, 469 Node
  tests pass, `python3 -B scripts/validate.py` and the package export check passed; the storage
  tests pass under both `umask 002` and `umask 022`. The third wave changed only the script's
  refusal cleanup (it now removes only an entry that still is the file it created) and the test
  fixtures.
- `python3 -B scripts/live_check.py --injection` passed on all three model paths plus the shell
  probe (OpenCode 2.3.2).
- Live probes (isolated OpenCode configs, sessions deleted): skill-load notes reached both
  skills' results with the git workflow on and off; `SHIP_PR` appeared after a successful
  create (with a stub `gh`); `vibe-security-review` was listed by the command API for a clone
  carrying this branch; a synthetic secret never appeared in the reply, a report or search
  output after the fixes, but did appear in file and diff reads (accepted by the user); the
  skills CLI installs only `<HOME>/.agents/skills/<name>/`, which OpenCode 2.3.2 loads directly.
- Without Smart Router, one probe's coordinator first chose a pay-as-you-go route; it failed on
  quota at cost 0.
- An `explore` subagent cannot load skills (`Permission denied: skill`) but reads skill files by
  absolute path.
- Not observed live: the skill text as revised by the fix waves (reviewer stop-and-resume,
  storage through the script).

## Items
- R1 — Task 4 updates existing tests that expected no ship reminder after an onboarded
  `gh pr create` / ready / [READY] retitle. Direction: neutral. If wrong: test churn only.
- R2 — The security reviewer never writes files, including partner-skill reports; it returns
  findings and the coordinator writes the report through the storage script. Direction:
  stricter. If wrong: partner report files are folded into findings instead of saved.
- R3 — For Task 2 the coordinator used a level-2 model instead of Smart Router's level-1 pick,
  citing the workflow's "standard model for multi-file integration". The final reviewer
  disagreed (router precedence comes first); kept as a process lesson. Direction: pessimistic.
  If wrong: slightly higher spend, and the router's pick is overridden without a named reason.
- R4 — Widened R1 to the onboarded, git-workflow-off fixture at `tests/plugin/pr-hook.test.js`
  (it now expects `SHIP_PR`). Direction: neutral. If wrong: one test expectation.
- R5 — An implementer's `--injection` run, made while the task said "no live checks", was
  treated as covered by `AGENTS.md`'s pre-authorization; its record update was committed
  separately. Direction: looser. If wrong: an inference run happened without the task's consent.
- R6 — Task 6's file list named `docs/development.md` but no step changed it; left to Task 7.
  Direction: neutral. If wrong: none.
- R7 — The command-listing probe read the command API for a throwaway clone carrying this
  branch's project-local plugin, since the background service loads the installed plugin.
  Direction: neutral. If wrong: one probe is unobserved.
- R8 — Ruling later confirmed by the user (shared copy read directly, no link); check.py accepts
  `~/.config/opencode/skills/<name>` or `~/.agents/skills/<name>` with a regular `SKILL.md`.
  Direction: looser. If wrong: a client that reads only its own folder needs links later.
- R9 — The coordinator's first answer to the leaked-secret probe narrowed "no secret in tool
  output" to "no secret in search output"; the final reviewer disagreed; the user then decided
  (raw reads accepted, rotate immediately). Vote on whether the skill now implements the user's
  decision faithfully. Direction: looser. If wrong: secrets reach models beyond what the user
  accepted.
- R10 — Reviewer model: with or without Smart Router, dispatch only on a provider and billing
  path the user already uses; without Smart Router the top tier on the session's own provider;
  never pay-as-you-go without approval; if unclear, ask or mark the review unavailable.
  Direction: stricter. If wrong: required reviews more often unavailable.
- R11 — The coordinator waits for the reviewer's result and does not poll with shell commands.
  Direction: neutral. If wrong: none.
- R12 — Task 8 (probe fixes) resumed Task 7's implementer instead of a fresh one; its review
  stayed independent. Direction: looser. If wrong: less fresh scrutiny of the fix.
- R13 — Report storage is a bundled, tested stdlib script instead of written steps. Direction:
  stricter. If wrong: one more shipped file to maintain.
- R14 — PRs always show only a finding's location and class, in every section and whatever the
  repository's visibility; details go to chat and the private report. Direction: stricter. If
  wrong: private-repo PRs carry less detail than reviewers want.
- R15 — Report text never passes through shell source: the coordinator writes it with its
  file-write tool to a private temp file outside the repository; the script takes `--from`,
  copies it in exclusively and deletes the temp file only after success. Direction: stricter.
  If wrong: reports fall back to chat where writing outside the repository is blocked.
- R16 — Parked: the report body also appears once in the session's tool history; the findings
  are already in the transcript as the model's own words. Direction: looser. If wrong: one more
  transcript copy of private findings.
- R17 — The script refuses when the main checkout root or `.vibe-workflow` is not owned by the
  user or is group- or world-writable, and when the folder or report identity changes between
  opening and checking; refusals go to chat. Direction: stricter. If wrong: on machines with a
  shared-group umask (`002`) reports always go to chat.
- R18 — Git paths are read as bytes, split on NUL and decoded with `os.fsdecode`. Direction:
  neutral. If wrong: none.
- R19 — A reviewer that finds a likely secret stops at once and returns only file, line and
  type; the coordinator asks the user to rotate it, then resumes the same reviewer session.
  Direction: stricter. If wrong: sweeps slow down, and resuming an `explore` session is
  untested.
- R20 — A partner skill is trusted only when its folder, after resolving links, lies inside
  `~/.config/opencode/skills/` or `~/.agents/skills/`, and the reviewed repository supplies no
  same-name skill; otherwise the built-in review runs. Direction: stricter. If wrong: a user's
  skill linked from elsewhere is ignored.
- R21 — The ship reminder skips `gh pr create/ready` with `--help`, `-h` or `--dry-run`, but a
  word that is the value of a value-taking option (for example `--title --help`) does not count.
  Direction: neutral. If wrong: one misfired or missing reminder for an unusual flag.
- R22 — No third fix wave without the user's approval; the PR stayed a [BLOCKED] draft listing
  the open findings (the user then approved the third wave). Direction: pessimistic. If wrong:
  one extra round-trip.

## Output format (strict)
Answer with at most 500 words, in this order:

1. One line per item, exactly:
   `<ID>: KEEP | CHANGE: <simplest change> | DROP | DEFER — <short reason>`
   Pick one verdict per line. Give a change only after CHANGE.
2. `Compounding`: 2 to 4 lines on how the items interact.
3. `Top 3`: the three items that matter most.

## Constraints
- Read-only. No edits, no Git writes, no subagents.
- Forbidden paths: `.env*`, any credential or key file, `~/.local/share/opencode/`,
  `~/.config/opencode/`, `.superpowers/` and `docs/vibe-council/sessions/`. Do not read them.
- Use the network only if you need it.
- Use about 15 tool calls.
- Answer in your final message.
