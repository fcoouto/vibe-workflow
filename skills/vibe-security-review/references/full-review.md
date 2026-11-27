# Full project review

1. **Bound first.** State the scope (path or whole checkout), depth, areas, tools
   found and exclusions before starting. Honor the user's bound; stop there and
   report what was not covered.
2. **Map.** Identify stack, entry points, roles, trust boundaries, secrets and
   user data locations, and deployment files. Separate code facts from
   deployment assumptions.
3. **Sweep by area.** For a large project, use parallel read-only subagents with
   [reviewer-brief.md](reviewer-brief.md). Cover:
   - Authentication and access.
   - Input handling and injection.
   - Secrets and data exposure.
   - Files, paths and outbound requests.
   - Insecure defaults.
   - Dependencies and supply chain.
   - CI, release and AI-agent workflows.
4. **Verify.** Give every candidate a refute pass. Search for sibling instances
   of confirmed findings.
5. **Report.** Follow [report-storage.md](report-storage.md) to save the full
   report privately. Order findings by severity and give location, scenario,
   fix and confidence, followed by coverage limits. Summarize in chat with the
   report path. Nothing found means nothing substantiated in the inspected scope.
6. **No fixes.** Offer to turn chosen findings into a plan; fixes follow the
   normal workflow.

## Secrets

These rules bind every reader: each sweep reviewer, and a coordinator working
alone on a small project.

- Reading files is necessary and is not quoting. Accepted limit: reading a file
  sends any secret in it to the reader's model and session history; nothing masks
  it.
- Never repeat a secret's value in a reply, report, pull request, commit, log or
  any command you write. Quote it as file, line and type only.
- Search for secrets only with commands that list file names, such as `rg -l` or
  `git grep -l`; never use a search tool or option that prints matching lines for
  a secret pattern.
- As soon as a likely secret is found, the coordinator tells the user immediately
  in chat, by file, line and type, that it must be treated as exposed and rotated
  now. Do not wait for the end of the review.
- A sweep reviewer that finds a likely secret stops at once and returns only the
  alert: file, line and type. The coordinator tells the user immediately to
  rotate it, then resumes the same reviewer by its session to finish its area.
  If resuming fails, dispatch a fresh reviewer for the remaining scope and give
  it the alert already raised, so it does not report it again. If that also
  fails, record the review as incomplete and never present it as clean: say in
  the report which area was not finished.

## Optional partners

Never install a tool during a review. Check where each partner skill comes from
before loading it, as [reviewer-brief.md](reviewer-brief.md) describes. When a
partner skill is skipped (not installed, an untrusted location, or a repository
skill of the same name), name it under the review's limits, in the report and
in the pull request's `Security` section if there is one.

Reviewers use partner guidance that only reads local files:
`agentic-actions-auditor` for local workflows invoking AI agents, `sharp-edges`
for API and config design, and `c-review` or `rust-review` when the language
matches. A reviewer runs a tool or partner script only when it needs no network,
credentials or writes and the user approves that exact run. The coordinator asks
the user before the sweep and names approved commands in each sweep brief.
Reviewers run no other commands from these tools or scripts.

Anything that needs network, credentials or writes is coordinator-run only and
never given to a reviewer: `supply-chain-risk-auditor`'s collector (a signed-in
`gh`, package names sent to registries and GitHub, an output file), CodeQL
database creation, and a Semgrep run that downloads rules or writes a cache. Use
Semgrep or CodeQL only if already installed. Before asking, the coordinator tells
the user what the command sends, where it sends it and where its output goes. It
runs only after the user approves that exact command, with output in a temp or
scratch folder outside the repository, redacted before any of it reaches a brief
or the report. Without approval, skip it and record the limit.
