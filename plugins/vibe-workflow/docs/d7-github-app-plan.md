# D7: Shared personal GitHub App — installed 26 October 2026; publisher unqualified

26 October 2026 · The coordinator observed successful registration and installation through Safari, with the exact permissions below. App-authenticated API access, evidence collection, publication and enforcement remain unqualified. This supplements the [D7 plan](d7-repository-gate-plan.md), outside routine instruction loading.

## Account and scope

Registered [fcouto-codex](https://github.com/apps/fcouto-codex), App ID **5485218**, owned by personal account `fcoouto`, as “Only on this account”. [Installation **175581025**](https://github.com/settings/installations/175581025) covers **All repositories**, explicitly current and future repositories on that account. Repository access does not activate enforcement: each project's target branches, accepted policy and merge controls still need explicit adoption. [App settings](https://github.com/settings/apps/fcouto-codex), [installation documentation](https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app).

Webhooks, OAuth during installation and device flow were off. No private key, token or client secret was generated or downloaded. No workflows, services or repository protections were changed.

The inspected Phi Codex installation belongs to `fcoouto-phioon`, with read-only checks. It does not establish this target's coverage or a trusted execution location. Keep its settings unchanged.

## Minimum access

Installed permissions match the approved scope: **Checks: read/write** to publish and inspect checks, plus the following reads. Safari displayed read access to actions, code, metadata and pull requests, and read/write access to checks. [Check runs](https://docs.github.com/en/rest/checks/runs).

- **Metadata: read** for repository identity and effective rules.
- **Pull requests: read** for candidate/base and review observations.
- **Contents: read** for immutable commits and approved verifier/policy definitions.
- **Actions: read** only to inspect optional existing run/job evidence. It never launches or reruns workflows; this integration does not require Actions evidence.

All other permissions remain off. Classic branch-protection reads would require separate **Administration: read** authorization; effective ruleset reads use Metadata. [Rules](https://docs.github.com/en/rest/repos/rules#get-rules-for-a-branch), [branch protection](https://docs.github.com/en/rest/branches/branch-protection#get-branch-protection). Keep the exact endpoint inventory and minimum permissions. [Endpoint permissions](https://docs.github.com/en/rest/authentication/permissions-required-for-github-apps).

## Protected execution and evidence

Tests and builds stay **local**. The proposed required path is an **on-demand local trusted collector, verifier and publisher**, calling the GitHub Checks API directly. It needs no Actions workflow, always-on service or scheduler and has no Actions-minute dependency. The App supplies publisher identity and permissions; registration does not run or authenticate the verifier.

Run candidate tests separately from the protected local verifier/publisher. Its approved code, policy and App credentials must remain outside candidate and worker control. Local execution alone does not prove this isolation; the mechanism still needs qualification.

Mandatory D5/D6 evidence needs an authenticated collector that independently checks native provenance, review coverage/findings, exact candidate/base/policy, freshness and stopped writers. Signing uploaded caller JSON does not validate those facts. Retain required independent review without adding a universal human-review requirement. Requery authoritative state before publication; missing, stale or untrusted evidence blocks success.

## Decision and qualification

Next, define the bounded local collection/publication steps, protected files, credential custody and disposable scope. Qualify provenance/isolation, App-specific check-source binding, forgery/replay rejection, changed candidates, unresolved writers, bypasses, supported merge paths and legitimate acceptance under the [D7 probes](d7-repository-gate-plan.md#3-qualify-enforcement-then-propose-live-rules). Separately approve each live enforcement settings diff. [Current validation](d7-validation.md) remains fixture evidence; production enforcement is unavailable until these dependencies pass.
