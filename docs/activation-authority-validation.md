# Activation authority validation

9 November 2026. The user decided: "No extra approval should be necessary. The
activation of API Usage or OpenRouter integrations, for example, are more than
enough to allow autonomous agentic actions using those means." The user also
retired Manual mode ("That doesn't have to be documented anywhere in our
workflow") and chose that activation covers Smart Routing control-plane
endpoints that may retain data, while terms forbidding confidential data and
explicit restrictions still exclude them. The decision and its provenance are in
the [source map](policy-sources.md#activation-authority--9-november-2026).

## What changed

- **Rule.** [Agent Delegation](../policies/03-agent-delegation.md#select-capability-and-dispatch),
  [Tool Integrations](../policies/11-tool-integrations.md#choose-and-configure),
  [Workflow Maintenance](../policies/09-workflow-maintenance.md#updates-and-optional-external-labs)
  and the [shared external contract](../clients/external-delegation.md) make
  activation the only approval for an integration's own routes. An account
  connection alone grants nothing; other non-native routes still need explicit
  approval before first use.
- **Shared resolver.** `runtime/external_delegation.py` adds
  `effective_permission`: a saved permission binds as saved; without one, the
  executor derives a per-assignment permission from its own saved activation,
  whose reference names the service, checkout scope and settings revision.
- **OpenRouter.** Status reports `activation`; proposals and workers derive
  their permission from it; `approval_ref` is optional; enable needs no
  `consent_version`; enabled legacy read-only workers need no renewal. Every new
  admission needs the packet binding and the admitted reviewer.
- **API Usage.** Manual mode, the approval line, approval-history reads, the
  same-turn selection, the one-shot Manual test and the paired pilot are gone.
  An active checkout routes eligible tasks on its own; each task is sent live
  at most once per checkout. The separately approved first canary is unchanged.
- **Smart Routing.** Control-plane endpoints no longer need a per-endpoint
  opt-in. An endpoint needs known terms that allow confidential data; missing or
  malformed terms and a `user_restricted` entry exclude it, and an empty refresh
  no longer restores control rows.

Unchanged: funding, prices, caps, revision-bound grants, disable and stale
re-enable handling, source/link/exclusion/digest checks, secret inspection,
replay protection and independent native review.

## Instruction paths

| Representative task | Expected path and outcome |
| --- | --- |
| Active API Usage, bounded task needing three source files | No question; funding, caps, trusted admission, source and secret checks, and independent review still apply. |
| Account connected, integration not enabled | Not authorized; stay native (offering to enable is fine). |
| Enabled route with a saved `automatic: false` | No automatic selection; an explicit user choice may proceed. |
| Local model through a proxy, no integration | Explicit approval before first use, plus capability assessment. |
| Quote above the per-task cap | The cap binds; pause the API assignment, continue natively. |
| API Usage disabled, user says "use the API" | Do not send while off; ask to enable or treat a clear enable request as activation through the skill. |
| Saved Manual record, enabled | Runs as active and autonomous. |
| Retaining profiler endpoint with known terms | Included; unknown terms excluded. |
| Packet prepared, route disabled and re-enabled | The old preparation no longer matches; prepare again. |

## Independent evaluation

A fresh native agent, independent of the author and read-only, applied the
instructions to ten activation cases without access to `docs/`, this record or
the expected outcomes. All ten decisions matched the
author's privately recorded expectations. It found one gap: a request to use a
disabled route for one task was not classified; the shared contract now says it
does not enable the route, and the agent says it is off and asks whether to
enable it. It also noted a pre-existing loading gap outside this change: the
guidance-publication procedure that owns the strategy-table refresh is linked
only from managed delegation controls.

## Checks

- **Full suite, earlier base** (`3a3ee91`, before review fixes): 2,919 tests,
  2 skipped, 2 failures. The OpenCode plugin's node suite passed when rerun
  alone (73 seconds under load, 31 alone). The release package test compared
  the committed README with an uncommitted edit and passed once committed.
- **Head `54f6b5b` on base `1dbce05`**, the reviewed candidate: the nine slow
  affected modules passed (229 tests) and `--fast` passed (1,716 tests,
  2 skipped). The reviewer's isolated run of twelve affected modules on the same
  head passed (246 tests, 1 skipped).
- **After review fixes** (`4c04b6b` base): the OpenRouter modules passed (170
  tests, 1 skipped), the routing evidence module passed (28 tests), and the
  final run on head `44c374a` passed: the nine slow affected modules (235
  tests) and `--fast` (1,729 tests, 2 skipped), with no writes to the test home.
- `python3 -B scripts/validate.py` passed.

## Independent review

An independent agent reviewed the whole branch for behavior and security under
[Code Review](../policies/06-code-review.md) and
[Security Review](../policies/04-security-review.md), read-only, and kept its
detailed report privately outside the repository. It confirmed five findings,
and its re-check one more; all are fixed on this branch:

| ID | Severity | Finding | Resolution |
| --- | --- | --- | --- |
| EA-01 | High | A worker brief or proposal without the packet binding skipped the saved restriction and the reviewer binding; the optional `approval_ref` left that path unguarded | Every new admission requires the binding; acceptance needs the admitted reviewer; earlier attempts settle only |
| EA-02 | Medium | Control-plane terms missing the confidentiality field were treated as allowed | Eligibility needs `forbids_confidential` exactly false; missing or malformed terms exclude |
| EA-03 | Medium | An empty refresh restored the previous table, including a just-restricted endpoint | An empty refresh keeps only previous non-control rows |
| EA-04 | Low | The Codex host procedure promised a replay where a live retry is refused | The procedure describes the refusal |
| EA-05 | Low | This record was missing | Added |
| EA-06 | Low | Found by the re-check: the shared contract still called the packet binding optional | It now says OpenRouter always requires it and when API Usage may omit it |

A focused re-check by the same reviewer at head `44c374a` closed EA-01, EA-02
and EA-03 with passing regression tests (196 tests, 1 skipped) and found the two
instruction additions free of conflicts. It reported one leftover sentence for
EA-04 and the new EA-06; both were corrected in the final documentation commit.
Live clients, providers, real credentials and exhaustive concurrency were not
requalified.

## Verification boundary

These are package checks, offline tests and instruction evaluations. No live
OpenAI API, OpenRouter, Codex host or OpenCode execution ran, and no installed
client was restarted. Legacy-record compatibility is covered by fixtures, not
by migrating a real saved configuration.
