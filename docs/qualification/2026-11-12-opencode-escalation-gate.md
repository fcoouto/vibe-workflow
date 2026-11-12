# OpenCode escalation gate behavior evaluation

## Result: selected mechanical paths passed after the masked-exit fix

The follow-up ran both positive cases successfully on candidate `b8afed0`, using
51 of 60 approved model requests between 18:07:39 and 18:15:23 UTC. The original
failed cases remain below; the [follow-up results](../../tests/fixtures/opencode_workflow/behavior-followup-results.json)
do not replace them. No further live retry was made or is authorized.

| Follow-up case | Requests | Observed acceptance |
| --- | ---: | --- |
| Recurrence, including piped failing checks | 23/30 | Fourth patch denied; stronger diagnosis; successful subsequent patch; original tests pass. |
| Re-evaluation at the top ranked effort | 28/30 | Fourth patch denied at re-evaluation; read-only assessment preserves requirements; release; original tests pass. |

The architecture assessment compared a redesign with the smaller repair and
recommended no redesign. The coordinator accepted that recommendation within the
agreed requirements. Tests, instructions, plugin/configuration and feature
settings remained unchanged in both cases, and the coordinator independently
reran the original four tests after each worker stopped.

### First round: qualification incomplete

The first allocation used 87 of 150 approved model requests across six cases,
between 17:46:52 and 17:54:50 UTC on 12 November 2026. No case was retried.
The [sanitized results](../../tests/fixtures/opencode_workflow/behavior-results.json)
retain the fixed expectations, observed outcomes and initial fixture digests.

| Case | Requests | Result |
| --- | ---: | --- |
| Recurrence | 7/35 | Enforcement not demonstrated: output pipelines masked failing test exits. |
| Re-evaluation | 15/35 | Re-evaluation not demonstrated for the same reason. |
| Unrelated files | 12/20 | No hold; matched the control. |
| Unrelated bugs in one file | 23/25 | False hold; a stronger diagnosis released it. |
| Notes after review waves | 16/20 | Two reviews and two notes updates without a hold. |
| Repairs moving to new files | 14/15 | No hold; confirmed the stated detection limit. |

The user switched remaining cases from the Claude subscription to the ChatGPT
subscription before the unrelated-files case. Earlier results and allocations
were not reset. Exact routes are recorded in the result file, not portable rules.

## What worked and what did not

The same-file control showed a complete mechanical hold-and-release cycle: the
fourth patch was denied, a new higher-effort child completed a diagnosis with no
observed edits, and the root's subsequent patch succeeded. That child used four
requests. This is observed diagnostic work, not a counterfactual estimate of the
total overhead: a run without the gate was not measured.

The two intended positive cases did not establish their expected behavior. The
agent piped failing unittest output through `tail`; the shell returned zero. The
gate only consumed the shell status and never saw a failure. Passing final tests
do not turn these cases into successful enforcement observations.

The recorder also failed to recognize the denial label in the same-file case.
The native CLI's tool error contains the gate's fixed denial and stage, so that
native error is the source for the corrected outcome. Recorder labels alone are
not accepted as proof that no hold occurred.

After every case stopped, the coordinator reran its original unittest checks;
all passed. Git comparisons confirmed that tests, instructions, plugin
configuration and feature settings were unchanged. Notes gained exactly two
nonblank lines in the notes control.

## Follow-up allocation and fix

The user approved one further round: Luna on the ChatGPT subscription,
low/high/max as needed, at most 60 requests and 30 minutes, one recurrence case
and one re-evaluation case with 30 requests each. Earlier failures remain
recorded. Local regression checks must pass before this round starts.

The candidate added a narrow recognition of unittest's exact failure summary in
captured check output. It does not rewrite shell commands or treat arbitrary
mentions of failure as proof. The recorder was changed to inspect permission decisions
directly rather than infer them from serialized tool errors. Two regression tests
failed before the fix and passed afterward. The recorder's corrected denial
classification also passed a model-free self-check before the live runs.

## Checks

### Later output-attribution repair

Independent review found that the earlier summary scan could count historical
failure output and missed unittest's unexpected-success failures. The later repair
requires complete footers consistent with a supported literal check sequence,
validates the entire result count list and rejects ambiguous wrapper options.
It preserves status/error detection and the existing hold and release rules.

The focused regression selection passed 22 tests after test-first repairs. The
wrapper follow-up first failed four tests (18 passed), then passed all 22. These
are synthetic predicate and plugin-hook checks, not a repeat of live qualification.
The live results above still describe `b8afed0`, not this later candidate.

The roster failure was also reproduced against freshly fetched current `main`
`6840d5333043f73b30bd52cdd9d776194f754b9b`: three roster tests ran, one failed on
the same two strings in `runtime/observability_hook.py`. This establishes a current
baseline failure; it neither fixes nor waives it. Final candidate checks are
recorded separately when executed.

PR #277 subsequently merged as `c4b2172ee010a7e847dcc1eb7ea92bbaba205c76`.
The integration retains current `main`, including its roster fix and retirement
of the stored instruction map. The gate runtime and tests are unchanged from the
independently reviewed `f1bcb6c` repair. Earlier failed full runs remain historical
evidence; checks on the integrated committed candidate are recorded in PR #264.
No new live qualification is claimed for this integration.

### Historical checks on the live-evaluated candidate

- Gate Node suites: 43 passed, including the masked-exit regression.
- Selected Python modules on `b8afed0`: 129 tests passed.
- Full suite on `b8afed0`: 4,333 tests, two skipped, one failure:
  `test_roster_vocabulary.RosterVocabularyTests.test_no_old_role_name_outside_the_allowlist`.
  It flags two pre-existing role strings in `runtime/observability_hook.py`,
  unchanged by this branch. The same failure was reproduced on base `b73b8f11`
  before these qualification changes. The full suite is not green.
- Package validation passed with the existing bundle-size warning.

## Limits

- One observation per case does not establish general reliability.
- Same-file unrelated failures still need the deferred routed judge to avoid
  unnecessary diagnosis. The first mechanical version cannot classify invariants.
- Repairs moving to new files remain outside the file-based detector.
- The live re-evaluation exercised the no-stronger-ranked-option branch, not
  three full escalation cycles. The one-time reset/second-exhaustion transitions
  have unit coverage, not a live observation here.
- No material redesign was needed, so redesign acceptance and user escalation
  for a changed requirement were not exercised live.
- Provider-internal retries, billing receipts and child-session token totals
  were not collected. Subscription routes are observed, not a monetary receipt.
- The copies are synthetic and disposable; the process isolation is not an OS
  sandbox or proof against arbitrary shell/API access.
