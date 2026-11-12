# OpenCode escalation gate behavior evaluation

## Result: qualification incomplete

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

## Follow-up allocation

The user approved one further round: Luna on the ChatGPT subscription,
low/high/max as needed, at most 60 requests and 30 minutes, one recurrence case
and one re-evaluation case with 30 requests each. Earlier failures remain
recorded. Local regression checks must pass before this round starts.

The candidate adds a narrow recognition of unittest's exact failure summary in
captured check output. It does not rewrite shell commands or treat arbitrary
mentions of failure as proof. The recorder will inspect permission decisions
directly rather than infer them from serialized tool errors.

## Limits

- One observation per case does not establish general reliability.
- Same-file unrelated failures still need the deferred routed judge to avoid
  unnecessary diagnosis. The first mechanical version cannot classify invariants.
- Repairs moving to new files remain outside the file-based detector.
- Provider-internal retries, billing receipts and child-session token totals
  were not collected. Subscription routes are observed, not a monetary receipt.
- The copies are synthetic and disposable; the process isolation is not an OS
  sandbox or proof against arbitrary shell/API access.
