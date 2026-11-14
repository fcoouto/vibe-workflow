# Minimum-only software compatibility

The user approved minimum-only compatibility on 13 November 2026. Use the oldest
documented working versions where evidence supports them: Codex CLI 0.163.0,
Claude Code CLI 2.1.304, OpenCode CLI/server 2.2.6, Python 3.11, Graphify 0.9.104,
optional keyring 25.7.0 and optional Habblaud 1.0.3. These are initial support
floors, not claims that every feature was exercised at each floor. Codex usage
files were observed on 0.167.0; their required shape and counter checks remain.

## Approach

Replace exact software-version eligibility and allowlists with lower-bound checks.
There are no upper bounds, including for a newer major release. Record actual CLI,
server and historical creation versions separately; differing minor/patch versions
do not invalidate otherwise unchanged capabilities or provenance. Unknown version
metadata alone does not imply broken capabilities. Known versions below the floor
remain unsupported. Use a small standard-library comparison helper shared by
existing consumers, not a new discovery service or configuration subsystem.

Keep actual protocol/field checks, permissions, identity, source/candidate digests,
schema validation, record bindings and ownership safeguards. Software versions are
diagnostic metadata, not a substitute for those checks. A missing required API
still blocks only its dependent operation. Preserve historical receipts; do not
rewrite old evidence or equate newly accepted versions with new live qualification.

For integrations with no established floor, inspect used commands/APIs and cite
upstream introduction evidence where available. Report a proposed floor or an
honest unresolved numeric floor; never derive one from today's installed version.
Keep remote service/API contracts separate from client software release numbers.

## Alternatives and scope

Exact pins and tested-version allowlists were rejected by the user. Removing all
lower bounds was superseded by the user's minimum-only clarification. This change
does not install/update plugins or software, change authentication, run paid/live
qualification, migrate stored state or publish a release. Installed-package drift
may be reported as an update hint, but a version-only mismatch must not make an
otherwise usable workspace fail readiness. Content and trust checks remain.

## Acceptance

Equal/newer versions, including newer major versions, remain eligible when actual
capabilities pass. OpenCode CLI 2.2.6 with server 2.2.7 is not downgraded by version.
Below-floor versions are diagnosed; malformed records and unsafe backends remain
rejected. Numeric comparisons handle multi-digit components and prereleases at
the stable floor. Tests cover all changed consumers and preserved negative paths.
