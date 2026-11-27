# Ship check

This check adds to the ordinary code review and never replaces it. Tier 0 skips
only security work, not code review. Review the PR's merge-base to head after the
last commit, plus staged, unstaged and untracked files about to be committed.
Choose the highest tier reached by any part, based on effect, not file type.

| Tier | Reached when the change touches | What runs |
| --- | --- | --- |
| Tier 0 None | docs, comments, styling, tests only, pure renames with no behavior change | Nothing; explain the lack of surface in one PR line. |
| Tier 1 Light | ordinary logic, new internal functions, refactors that move code across a boundary | Check the short list yourself. |
| Tier 2 Focused | new endpoints, commands, user input handling, file or network access, config defaults, logging of user data | Add a security section to the existing reviewer's brief; no extra agent. |
| Tier 3 Deep | authentication, authorization or tenancy; secrets or credentials; crypto; parsing untrusted data or deserialization; shell, SQL or template construction; file paths and uploads; outbound requests to user-chosen URLs; dependency or lockfile changes; CI workflows, release or install scripts; AI agents fed external input; removing a validation or check | Dispatch one reviewer using [reviewer-brief.md](reviewer-brief.md). |

- A refactor of security-relevant code is tier 3 until shown otherwise.
- Removing a check is tier 3, even if it is one line.
- When unsure between two tiers, take the higher one.
- Never tier 0: agent instructions, skills and prompts (`AGENTS.md`,
  `SKILL.md`, system prompts); deployment or config examples; CI fixtures and
  workflow files; anything executed or loaded at runtime.

## Secrets

When you read the diff yourself, reading a changed file is necessary and is not
quoting. Never repeat a secret's value in a reply, report, pull request, commit,
log or any command you write. Quote it as file, line and type only. Search for
secrets only with commands that list file names, such as `rg -l` or
`git grep -l`; never use a search tool or option that prints matching lines for a
secret pattern.

## Short list

Use for tier 1 and as the base of tiers 2 and 3:

- Input trusted when it should not be, or a check removed or weakened.
- Secrets, tokens or personal data in logs, errors, bundles or examples.
- Insecure defaults or fallbacks: fallback secrets, fail-open flags, default
  credentials, permissive access or debug output in responses.
- Shell, SQL, HTML or paths built from strings; new dependencies.

## Security

Add a `## Security` section to the PR body whatever template is used, including
the project's `.github/pull_request_template.md`. Include head commit, tier and
reason, what was checked, findings with severity and status, and limits. If head
changes, re-check only the tiers the new commits reach. Fixes return through the
normal workflow; re-check only the affected tier.

A confirmed high or critical finding that this change introduced or made worse keeps the pull request [BLOCKED] until it is fixed or the user explicitly accepts it.
A pre-existing finding is reported to the user and does not block an unrelated pull request.
If a required tier-3 review could not run, the pull request stays [BLOCKED] and says so.
List medium and low findings without blocking.

For a public repository, put only location and class in the PR for every finding
that is not plainly harmless, including every confirmed or likely finding and
every secret. Share details in chat and a private report using
[report-storage.md](report-storage.md).

## Going public

Cover history and every ref that will become visible, not only the checkout.
List refs with `git for-each-ref`. Use an installed history-aware scanner such
as `gitleaks` only after the user approves that exact run. Otherwise search
history with `git log --all -G'<pattern>' --format=%H --name-only`: print commits
and file names, never matching lines. Scanner output must not print secret values
either. State the limits of the search.
