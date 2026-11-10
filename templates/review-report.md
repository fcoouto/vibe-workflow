# Review report

The report of a code, security or project review. Each line names the review kinds that require it; a review covering several kinds fills the lines of each. Analysis, redaction and storage rules stay with the owners: [Code Review](../policies/06-code-review.md#review-the-actual-change), [Security Review](../policies/04-security-review.md#evidence-and-completion) and [project review](../skills/review-my-project/references/procedure.md#findings-report-and-retention). Omit lines no kind requires when they don't apply; never invent values. Add any section the reviewer's brief asks for.

## Review

- Kinds: [code, security, project]
- Target: [candidate and base, comparison, or the reviewed snapshot; staged, unstaged and untracked layers] (all)
- Date: [review date] (project)
- Reviewer: [who reviewed and how the execution was observed] (code, security)
- Scope: [what was reviewed and what was excluded] (all)
- Versions: [contract, check, tool and source versions] (security)
- Security context: [authorization assumptions; assets and boundaries; deployment unknowns] (security)
- Checks: [what was run, with results; reused evidence and its freshness] (all)
- Limits: [unreviewed or unverified areas, unavailable capabilities, remaining limits] (all)

## Findings

One block per finding. With none, write "None substantiated in the inspected scope".

- ID: [short identifier] (security)
- Severity: [level; its rationale for security] (code, security)
- Status: [confirmed, hypothesis, or improvement (optional improvement, hardening suggestion, recommendation)] (all)
- Change status: [introduced, regressed, pre-existing or undetermined] (security, for a change review)
- Location: [file:line; revision or layer for security] (all)
- Evidence: [supporting evidence; minimal and redacted for security] (all)
- Scenario and impact: [concrete failure scenario for code; impact for security and project] (all)
- Attacker prerequisites: [what an attacker needs] (security)
- Counterevidence: [what was checked that could have disproved it] (security)
- Confidence: [how sure, and why] (security, project)
- Reproduction or test result: [command, isolation, result and what it proves] (security)
- Fix: [proposed correction; the smallest useful one for project] (security, project)
- Effort: [rough size of the fix] (project)
- Next validation: [what would confirm the fix] (security)

## Outcome

- Disposition: [for each finding: fixed, authorized disposition, or open] (code, security)
- Residual risk: [what remains] (code, security)
- Report location: [where the private report is saved, or that none was saved] (security)
- Next: [the next useful action or validation] (security, project)
