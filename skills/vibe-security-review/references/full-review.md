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

## Optional partners

Use available partner tools only within the reviewer brief's limits, never install
them during review. Use `supply-chain-risk-auditor` for dependencies,
`agentic-actions-auditor` for workflows invoking AI agents, `sharp-edges` for API
and config design, and `c-review` or `rust-review` when the language matches.
Use Semgrep or CodeQL only if already installed. Run each tool or partner script
only after the user approves that exact run. `supply-chain-risk-auditor` sends
package names to registries and GitHub; disclose this before asking. The
coordinator asks the user before the sweep and names approved commands in each
sweep brief. Reviewers run no other commands from these tools or scripts.
