# Task checks and review evidence

Load this contract only for explicitly selected managed evidence operations in Codex: checks, general or security review, completion decisions, or local merge evidence. Ordinary work uses the [native workflow](codex.md#startup-behavior) and applicable [shared policies](../policies/00-index.md), without managed task state. Follow the full [Tests and Builds](../policies/05-tests-and-builds.md), [Code Review](../policies/06-code-review.md), and [Security Review](../policies/04-security-review.md) contracts; this file gives the local controls, not replacements for those policies. This is task runtime guidance: do not load Toolkit sources or setup/onboarding procedures. Load [native delegation](delegation.md) only for native review preparation, lifecycle observation or recovery. Direct checks need no D6 native plan, discovery, model choice or guidance.

## Plan and capture

Use the installed package's `scripts/workflow.py` with the enrolled checkout as `--cwd`. Mutations take the current Codex client/session/task/scope identity and `expected_revision` as bounded JSON on stdin. Start and update controls remain in [task runtime](runtime.md). Evidence operations are:

```sh
python3 -I -B /path/to/package/scripts/workflow.py evidence plan --cwd /checkout < plan.json
python3 -I -B /path/to/package/scripts/workflow.py evidence status --cwd /checkout --client codex --session SESSION
python3 -I -B /path/to/package/scripts/workflow.py evidence check --cwd /checkout < check.json
python3 -I -B /path/to/package/scripts/workflow.py evidence reconcile-check --cwd /checkout < reconciliation.json
python3 -I -B /path/to/package/scripts/workflow.py evidence review prepare --cwd /checkout < prepare.json
python3 -I -B /path/to/package/scripts/workflow.py evidence review collect --cwd /checkout < collect.json
python3 -I -B /path/to/package/scripts/workflow.py evidence review assess --cwd /checkout < assess.json
python3 -I -B /path/to/package/scripts/workflow.py evidence verify --cwd /checkout < verify.json
```

The plan records acceptance criteria, accepted command definitions and thresholds, execution limits, baseline, candidate/base comparison, exclusions, and risk-based review applicability. Preserve `required`, justified `inapplicable`, or `unresolved` for each review scope; lack of a tool or reviewer is unresolved. Every completed change needs an assessed security impact. Do not invent commands or treat request JSON as permission to change thresholds or authorize side effects. Use existing decision references for accepted rules; a changed rule needs the established decision process.

Checks run only the planned argv and environment, without shell interpretation or inherited credentials. Checks and native reviews share participating resource ownership under the [approved state authority](runtime.md#opt-in). Review collection verifies the prepared task marker in native parent/child records, child checkout, completed turn, and fetched final report. The native prompt is encrypted and is not claimed as observed; receipt proves execution identity, not review quality or read-only enforcement. The coordinator must check semantic coverage and findings against the target. Keep detailed snapshots, output, and reports in the private evidence area; records contain bounded summaries and digests, never raw transcripts or secrets.

Review collection selects `codex` from the command's `PATH` and validates the observed native capabilities, execution identity and prepared bindings. Version strings record provenance; differences alone do not block collection or require executable changes. Preserve the admitted `native_version`; record the current reader as `current_observer_version` and native creation provenance as `parent_creation_version` and `worker_creation_version`. Missing capabilities or changed bindings leave independent review unverified. Preserve global settings and `PATH`.

## Request fields

Every mutation JSON object includes `client:"codex"`, `session_id`, `task_id`, `scope_ref`, and current positive `expected_revision`. Use the revision returned by the previous operation; after a conflict, read status and prepare a fresh request. JSON input is limited to 128 KiB; unknown fields reject.

Load only the guide for the current operation after this common contract.

| Operation | Guide |
| --- | --- |
| Plan request | [Plan request](evidence-plan.md) |
| Run or reconcile a check | [Run or reconcile a check](evidence-checks.md) |
| Prepare a review | [Prepare a review](evidence-review-prepare.md) |
| Current native review bindings | [Current native review bindings](evidence-review-bindings.md) |
| Collect or assess a review | [Collect or assess a review](evidence-review-results.md) |
| Verify completion or merge evidence | [Verify completion or merge evidence](evidence-completion.md) |
| Retire a task | [Retire a task](evidence-retirement.md) |

For review `visibility` and automatic `visibility_preparation`, follow the
shared [role preparation](worker-visibility.md#declare-before-dispatch).

## Completion and limits

For completion, freshness and local merge limits, load [completion evidence](evidence-completion.md). For an authorized runtime transition, load [task retirement](evidence-retirement.md).

## Retention maintenance

Retention runs once when accepting an evidence plan, and on explicit
`evidence cleanup --cwd /checkout` with the common current task identity/revision.
Artifact publication does not recursively scan unrelated closed-task history.
Cleanup returns expired, pending, protected and incomplete-coverage details;
inspect those results during maintenance. Existing seven-day detail retention,
four-month compact retention, 100 MiB Project cap, and protected active,
uncertain and recovery evidence rules remain unchanged. Unsupported cleanup
coverage remains an explicit limitation, not permission to delete protected data.
