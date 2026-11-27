# Security review: proportional ship checks and an on-demand project review

Date: 27 November 2026. Status: decisions D1 to D3 made by the user; awaiting
approval of the whole design.

## Intent

The user wants confidence that code is safe to ship whenever a feature is
delivered or a plan is completed, at a depth that matches the change: a small
patch gets a light check, a new feature gets a real one, and nothing runs a full
audit while applying a patch. Separately, the user wants a way to ask for a full
security review of a whole project on demand.

The old plugin (`old-vibe-workflow`, `policies/04-security-review.md` and the
security area of `skills/review-my-project`) covered both, but with heavy
machinery: evidence bound to candidate digests, a 30-line report template,
private report files and a merge rule that required security review "including
for trivial changes". This design keeps the old policy's good judgment (read-only
reviewers, no live probing, never print secrets, counterevidence before a finding,
"no findings" is not "secure") and drops the machinery.

Superpowers, the main workflow partner, has one line on security
(`requesting-code-review/code-reviewer.md`: "Security concerns?"). That is the gap.

## Use cases

1. **Ship check on delivery.** When work reaches code review, branch finishing
   or plan completion, the agent sizes the security check to what the change
   touches and records the result in the pull request.
2. **Patch.** Same hook, but a change with no security surface costs one line
   of reasoning, no reviewer.
3. **Full project review.** A user-triggered skill and command that reviews the
   whole checkout within a stated bound.
4. Covered by the same tiers, not separate features: a dependency added or
   upgraded, CI or GitHub Actions changes (including workflows that run AI
   agents), and preparing a private repository to go public.

## Goals

- Depth follows the change's security surface, not its size or file type.
  (Trail of Bits' `differential-review` puts it as "classify by risk, not size";
  a two-line change to an auth check is high risk.)
- One new skill, `vibe-security-review`, owns the method for both modes.
  Existing skill notes point to it; no new enforcement.
- Use Trail of Bits skills when the user has them installed; work without them.
- Results are short and live where the user already looks: the PR body and chat.

## Non-goals

- No evidence binding, receipts, runtime database or report template beyond
  what fits in a PR section.
- No hooks that block tools or merges. Notes are advisory, like every other
  vibe-workflow note.
- No scanner installation during a review. Semgrep, CodeQL or Trail of Bits
  skills are used only if already present, or installed through
  `vibe-prepare-my-workspace` with approval.
- No compliance or certification claims.
- No copying of Trail of Bits text into this package (see Licensing).

## Anti-goals (failure even if it works)

- A full audit runs on a typo fix or a docs change.
- Findings are so noisy or so often wrong that users learn to ignore them.
- A review leaks a secret, a private finding or exploit detail into a public PR,
  commit or log.
- A review changes the checkout: installs, fixes, stashes, commits. The one
  allowed write is a report in the ignored reports folder (Design §3).
- The ship check becomes a reason to skip the ordinary code review.

## Constraints

- The plugin delivers advice through skill notes (`src/skill-advice.js`) gated on
  an onboarded project, and through shell-command notes (`src/pr-advice.js`).
  Skills ship under `skills/<id>/`; commands are registered like `/vibe-council`
  (`src/council.js`).
- New shipped files must be listed in `package.json` `files` and
  `scripts/manifest.py` in the same change (`AGENTS.md`).
- OpenCode is the first client; wording stays client-neutral.
- Trail of Bits skills are CC-BY-SA-4.0. This package is MIT.
- Several Trail of Bits plugins depend on Claude Code features OpenCode lacks:
  the `Workflow` tool, `${CLAUDE_PLUGIN_ROOT}` and plugin-scoped agents
  (`insecure-defaults`, `audit-context-building`, the `static-analysis` scan
  workflows). Plain skills that only read, grep and run shell commands are
  likely portable; this is unverified on OpenCode.

## Approaches

1. **Advice only, no skill (rejected).** Add security sentences to the existing
   notes on `requesting-code-review` and `finishing-a-development-branch`.
   Cheapest, but notes are one paragraph; there is no room for the triage
   rules or a full review method, and the full-review use case has no home.
2. **One skill with two modes, pointed to by notes (recommended).** A
   `vibe-security-review` skill holds the triage tiers, the reviewer brief and
   the full-review procedure in `references/`. Notes on the delivery skills tell
   the agent to run its ship check. A `/vibe-security-review` command starts the
   full mode. Small surface, matches how `vibe-council` and `vibe-pull-requests`
   are built.
3. **Port the old policy (rejected).** Bring `04-security-review.md`, the
   report template and evidence rules. Thorough, but it is the framework this
   product moved away from, and its "always review, even trivial" rule is the
   opposite of what the user asked for.
4. **Depend on Trail of Bits entirely (rejected).** Tell users to install
   `differential-review` and stop there. It writes a full report file for every
   review and runs six phases, which is too heavy for tiers 1 and 2. Its
   strongest companions do not run on OpenCode, and users without it get
   nothing.

## Design

### 1. Ship check (use cases 1, 2, 4)

**When.** Skill notes, in onboarded projects, on:

- `requesting-code-review`: classify before dispatching the code reviewer.
- `subagent-driven-development`: at the final whole-branch review.
- `finishing-a-development-branch`: confirm the ship check ran and its result
  is in the PR body; run it if it did not.

The note is one or two sentences pointing at the skill; the method lives in the
skill. Work can finish without loading those skills, so the existing PR shell
notes (`src/pr-advice.js`, on `gh pr create` and on marking a PR `[READY]`) also
remind the agent to run the ship check if the PR body has no `Security` section
for the current head. (Builder check; my call.)

**What is reviewed.** The PR's merge-base to head, after the last commit, plus
any staged, unstaged or untracked files the agent is about to commit. The
`Security` section names the head commit it covers. If the head changes after
the check, re-run only the tiers the new commits reach. (Builder check; my call.)

**Triage.** The agent reads that diff and picks the highest tier any part of it
reaches. Tiers are judged by effect, not file type:

| Tier | Reached when the change touches | What runs |
|------|---------------------------------|-----------|
| 0 None | docs, comments, styling, tests only, pure renames with no behavior change | Nothing. One line in the PR: why there is no surface. |
| 1 Light | ordinary logic, new internal functions, refactors that move code across a boundary | The agent checks the change against the short list below, itself. |
| 2 Focused | new endpoints, commands, user input handling, file or network access, config defaults, logging of user data | The existing code reviewer gets a security section in its brief. No extra agent. |
| 3 Deep | authentication, authorization or tenancy; secrets or credentials; crypto; parsing untrusted data or deserialization; shell, SQL or template construction; file paths and uploads; outbound requests to user-chosen URLs; dependency or lockfile changes; CI workflows, release or install scripts; AI agents fed external input; removing a validation or check | One dedicated security reviewer, read-only, with the brief in the skill. Uses Trail of Bits `differential-review` when installed. |

Rules carried from the old policy and from Trail of Bits, in our words:

- A refactor of security-relevant code is tier 3 until shown otherwise.
- Removing a check is tier 3 even if it is one line.
- When unsure between two tiers, take the higher one.
- Never tier 0, whatever the file type: agent instructions, skills and prompts
  (`AGENTS.md`, `SKILL.md`, system prompts); deployment or config examples;
  CI fixtures and workflow files; anything that is executed or loaded at
  runtime. (Builder check; my call.)

**Short list (tier 1, and the base of tiers 2 and 3).** Input trusted that
should not be; a check removed or weakened; a secret, token or personal data
written to logs, errors, bundles or examples; an insecure default or fallback
(fallback secret, fail-open flag, default credential, permissive access, debug
output in a response); string-built shell, SQL, HTML or paths; a new dependency.
The categories follow Trail of Bits `insecure-defaults` and `sharp-edges`; the
text is ours.

**Reviewer (tier 3).** One `explore` agent: no edit tools, but shell stays
available, so read-only is by instruction, as with `vibe-council`. The brief
forbids installs, builds, scanner runs and bundled partner scripts unless the
user approved that exact run. Model: Smart Router with `reviewing` naming the
author's model, so the pick prefers another lab; without Smart Router, the top
tier. The reviewer only uses providers the user already routes work to; no new
provider sees the code. (Builder check; my call.) Brief:

- the diff range and the tier-3 triggers it hit;
- trace untrusted input to the sensitive operation, look at callers;
- for each candidate finding, look for counterevidence (validation upstream,
  ownership checks, tests) before reporting it; label confirmed, likely or
  hardening (the refute-before-report idea from Trail of Bits `fp-check`);
- say whether the issue was introduced by this change or already existed;
- never run commands with real credentials, contact live services or print a
  secret's value; when a line looks like a secret, quote only its file, line
  and type, never the line itself, in tool output as well as the report;
- if `differential-review` is installed, use it within these limits, read its
  `adversarial.md` instead of delegating to its plugin agent, and point its report
  file at the private reports folder (see Report storage), never elsewhere in the
  repository. Do not use `fp-check`: its deep path writes and runs exploit code.

**Result.** A `Security` section in the PR body: head commit, tier and reason,
what was checked, findings with severity and status, and limits. The skill tells
the agent to add the section whatever template the project uses; the bundled
`vibe-pull-requests` template also gains it. For a public repository, every
finding that is not plainly harmless (any confirmed or likely one, and any
secret) appears only as location and class; the detail goes to the user in
chat and to a private report (see Report storage). (Builder check; my call.)

**What a finding does** (user, D1). A confirmed high or
critical finding that this change introduced or made worse keeps the PR
`[BLOCKED]` until fixed or the user explicitly accepts it. A pre-existing one is
reported to the user and does not block an unrelated PR. Medium and low are
listed and do not block. If a required tier-3 review could not run, the PR stays
`[BLOCKED]` and says so. Fixes go back through the normal workflow and only the
affected tier is re-checked.

**Going public (use case 4).** The check covers the Git history and every ref
that will become visible, not only the checkout: a secret removed in a later
commit is still published. Use a history-aware secret scanner if one is
installed (for example `gitleaks`); otherwise search history with `git log -p`
and state the limit. (Builder check; my call.)

### 2. Full project review (use case 3)

**Trigger.** `vibe-security-review` skill in full mode, or the
`/vibe-security-review [path or focus]` command. Available in any project,
onboarded or not, like `vibe-council`.

**Procedure** (`references/full-review.md`):

1. **Bound first.** State the scope (path, or whole checkout), depth, the
   areas, the tools found and the exclusions before starting; honor a bound the
   user gives. Stop at the bound and report what was not covered.
2. **Map.** Stack, entry points, roles, trust boundaries, where secrets and
   user data live, deployment files. Separate code facts from deployment
   assumptions.
3. **Sweep by area,** in parallel read-only subagents when the project is large:
   auth and access; input handling and injection; secrets and data exposure;
   files, paths and outbound requests; insecure defaults; dependencies and
   supply chain; CI, release and AI-agent workflows.
4. **Verify.** Every candidate gets a refute pass before it is reported.
   Confirmed findings get a search for sibling instances (the
   `variant-analysis` idea).
5. **Report.** Save the full report (findings by severity with location,
   scenario, fix and confidence, then coverage limits) to the private reports
   folder (see Report storage), and give a summary in chat with the report's
   path. "Nothing found" means nothing substantiated in the inspected scope.
6. **No fixes.** Offer to turn chosen findings into a plan; fixes go through
   the normal workflow.

**Partner tools used when present** (never installed mid-review):
`supply-chain-risk-auditor` for dependencies (asks first, since it sends
package names to registries and GitHub); `agentic-actions-auditor` when
workflows invoke AI agents; `sharp-edges` for API and config design;
Semgrep or CodeQL if already installed; `c-review` or `rust-review` when the
language matches.

### 3. Report storage (user, D3)

Security reports live in `.vibe-workflow/security-reports/` and are never
published. Used by every full review, and by a ship check whenever details are
kept out of the PR body (a public repository) or a partner tool writes a report.

- **Location: the main checkout,** found from `git worktree list` (its first
  entry), not the task worktree. Post-merge cleanup removes task worktrees, and
  reports must outlive them. (My call.)
- **The folder's own `.gitignore`** contains exactly `*`, so Git ignores the
  folder's whole contents, including that file. Nothing in it is ever tracked,
  whatever the project's root `.gitignore` says.
- **Before every write:** create the folder if missing, rewrite `.gitignore` if
  it is missing or differs, then confirm with `git check-ignore -q <report
  path>`. If the check fails, do not write; give the report in chat and say why.
- **Files:** `YYYY-MM-DD-HHMM-<full|ship>-<topic>.md`, never overwritten. Folder
  `0700`, files `0600` where the system supports it. (My call.)
- **Never** `git add -f` anything in the folder, and never copy a report into a
  commit, PR, issue or log.
- **Limits stated to the user once:** ignored is not secret. Backups, cloud
  sync and `git clean -x` still see or delete the folder. Secrets are still
  written as file, line and type only, so a leaked report does not leak them.

### 4. Workspace preparation

Add Trail of Bits skills to `vibe-prepare-my-workspace/partners.json` as
optional skill partners, so the user can see and approve them. Proposed set:
`differential-review`, `agentic-actions-auditor`, `sharp-edges`,
`supply-chain-risk-auditor` (`fp-check` dropped after the static check). Each needs a portability check on OpenCode before
it is listed (see "Left to the builder").

## Trail of Bits evaluation

| Skill | Use in vibe-workflow | Why |
|-------|----------------------|-----|
| `differential-review` | Partner, tier 3 | Strongest change review. Too heavy below tier 3; writes a report file by default. |
| `fp-check` | Idea only (refute-before-report, in our briefs) | Refute-before-report is the main defense against noisy findings. Not a partner: its deep path writes and runs exploit PoCs, which breaks the read-only and no-exploit rules (pre-implementation check, `docs/development.md`). |
| `insecure-defaults` | Idea only (checklist categories) | Runs through Claude Code's `Workflow` tool; not runnable on OpenCode. |
| `sharp-edges` | Partner (full review), idea for the short list | Read-only skill; likely portable. |
| `supply-chain-risk-auditor` | Partner (full review, dependency changes) | Script-measured; needs `uv`, `gh` and network, so ask first. |
| `agentic-actions-auditor` | Partner (CI changes, full review) | Directly relevant to users who run coding agents in CI. |
| `variant-analysis` | Idea (sibling search after a confirmed finding) | Partner later if portable. |
| `static-analysis` (Semgrep, CodeQL) | Use if installed | Scan workflows are Claude Code-only; the CLIs are not. |
| `audit-context-building` | Skip | Claude Code `Workflow` tool; our map step covers the need. |
| `c-review`, `rust-review` | Partner when language matches | Language-specific depth. |
| `open-sourcing` | Mention for going public | Secrets hygiene before release. |
| Smart contracts, mobile, YARA, constant-time, zeroize, DWARF | Skip | Out of the general audience's scope. |

**Licensing.** CC-BY-SA-4.0 is share-alike. Copying Trail of Bits text into this
MIT package would put that text under share-alike. The design partners with the
skills (detect, recommend, call) and reuses ideas written in our own words; it
copies no text.

## Decided so far

- Depth follows the change's security surface; patches do not get a full audit
  (user).
- Both a ship check and a user-triggered full review (user).
- Evaluate Trail of Bits for inclusion (user). The partner-not-copy stance,
  the tiers, the skill name and the triggers are proposals.
- **D1. What a finding does** (user, 27 November): a confirmed high or critical
  finding introduced or worsened by the change keeps the PR `[BLOCKED]`, as does
  a tier-3 review that could not run; pre-existing ones are reported but do not
  block an unrelated PR; the rest is listed.
- **D2. Onboarding** (user, 27 November): no new onboarding question; the ship
  check is on in every onboarded project, including those already onboarded.
- **D3. Report storage** (user, 27 November): `.vibe-workflow/security-reports/`
  with its own `.gitignore`, ensured before every write. Details in Design §3.

## Review record

A builder check (independent reviewer, another lab) read this design once on
27 November 2026. Its points on review target, shell access, early redaction,
delivery gaps, tier-0 escapes, history for going public and provider exposure
are settled above as my calls. Its remaining points were D1 to D3, now
decided by the user.

## Left to the builder

- Exact note wording, kept to one or two sentences each.
- Verify which proposed Trail of Bits skills load and run on OpenCode before
  listing them in `partners.json` (`{baseDir}` placeholders, `allowed-tools`
  frontmatter, bundled scripts).
- How the skill detects that a Trail of Bits skill is installed.
- Splitting the skill's references across files.
- Tests for the new notes and command registration, following
  `tests/plugin/*.test.js`.
