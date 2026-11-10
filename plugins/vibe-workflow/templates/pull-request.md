# Pull request

The body of a PR an agent opens or updates; its title prefix and draft state follow the [PR lifecycle](../policies/08-merge-readiness.md#pr-lifecycle). Use it only when the project publishes through PRs, and keep a project's own PR layout when it has one. Omit lines that don't apply; never invent values.

## Why

[the request or problem, with links]

## What changes

- [each change and where it is]
- [what is deliberately unchanged or out of scope]

## Review

[reviewer, scope and rounds; findings and their disposition, or a link to the review report]

## Checks

- [command: result, on which commit]
- [checks not yet run, and why]

## Readiness

Optional summary for when the PR becomes `[READY]`, in the body or a comment. Reference or update the evidence above rather than copy it; the [readiness requirements](../policies/08-merge-readiness.md#establish-readiness) decide what is complete.

- Candidate and base: [head commit, and the base it was checked against]
- Evidence: [which checks and review above cover that head; reruns, and reused evidence with the reason]
- Limits: [what was not run or not observed]
