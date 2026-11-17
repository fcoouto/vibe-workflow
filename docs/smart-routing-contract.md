# Smart Routing command contract

`scripts/routing.py` is the one local Smart Routing command every client calls. This page is its request and response contract, checked against the code on 16 November 2026; where they differ, the code wins and this page is corrected. It replaces Task S3.1 of the deleted implementation plan (`docs/superpowers/plans/2026-11-08-smart-routing.md`, deleted in `11a8927c`; see Git history). Enabling and operating Smart Routing for a project is in the [Smart Routing procedure](../skills/manage-usage-routing/references/smart-routing.md).

## Invocation

```text
scripts/routing.py <op> [--settings PATH] [--ledger PATH] [--table PATH] [--project PATH] [--test-inputs]
scripts/routing.py --serve [--settings PATH] [--ledger PATH] [--table PATH] [--project PATH] [--test-inputs]
```

- `<op>` reads exactly one request on stdin and writes one response line. `--serve` reads one request per line and answers each; a bad line is answered and never ends the loop. The OpenCode shim's persistent child uses `--serve`.
- The default stores are the machine's: the settings at `~/.local/state/vibe-workflow/routing/settings.json` (path qualifications and the strategy table sit beside them) and the account-wide ledger Usage Routing already uses.
- `--project` pins the checkout a request may name. A workflow-bound `prepare` without it is `project_required`; a pinned `settle` needs a `workflow` locator (`project_required` otherwise); `status` with it adds `managed`.
- Every response exits 0, a `blocked` one included; only a usage error exits 2 (`usage_error`).

## Requests and responses

A request is one JSON object: `{"v":1, "op"?: <op>, "id"?: <id>, ...fields}`. `op`, when present, must equal the command's op. A valid `id` (printable text up to 128 characters, or a non-negative integer) is echoed back. The fields must include the op's required ones and may add only its optional ones (`invalid_request` otherwise).

A response is `{"v":1, "state":...}` and carries either a result or `{"state":"blocked","reason":<fixed code>}`. It never echoes input text, a traceback or a secret. Consumers ignore fields they do not know.

| Problem | Answer |
| --- | --- |
| More than 1 MiB | `request_too_large` |
| Not JSON, a duplicate key or a non-finite number | `invalid_json` |
| Another `v` | `unsupported_version` |
| Unknown op | `unknown_op` |
| Fields outside the op's lists | `invalid_request` |
| A ledger busy past its wait | `ledger_busy` |
| Any other ledger error | `ledger_unavailable` |
| Anything unexpected | `internal_error` |

A `prepare` that read a task profile also answers `missing_fields`: the profile fields that took their floors, never a reason to block.

## Operations

| op | Required fields | Optional fields | Where it runs | Result |
| --- | --- | --- | --- | --- |
| `prepare` | `assignment`, `job` | `constraints`, `observations`, `table`, `workflow` | any | `admitted{duplicate, admission, workflow_bound, ranking, profile}` or `pending{reason, reasons, retry_after_seconds?}`; a managed project's answers add `seen` |
| `check` | `request` | `attempt_id`, `authority`, `now`, `session_id` | any | `allow{attempt_id, route, pool, ...}` or `unmanaged`, plus `settings_warnings`; reads only |
| `dispatch` | `attempt_id`, `request` | `now` | any | `dispatched{attempt_id, authority, ...}` once, then `already_dispatched` without a token |
| `record` | `event` | none | any | `recorded{event_id, duplicate, ...}` |
| `status` | none | none | any | `ok{settings, settings_state, settings_revision, settings_warnings, legacy_caps, ledger, windows, table, test_inputs, project_pinned}`, plus `managed` with `--project`; reads only |
| `set` | none | `authority_required`, `change`, `expected_revision`, `organization` | any | `updated{revision, settings, invalidated_attempts, ledger_state?}`, `updated{organization}` or `updated{authority_required: true}` |
| `settle` | `root_task_id` | `workflow` | any | `settled{root_task_id, attempts, ...}`; reads only |
| `resolve_cost_unknown` | `attempt_id`, `cost_usd_micros`, `event_id` | `receipts`, `source` | one-shot only (operator) | `recorded{event_id, duplicate, outcome, ...}` |
| `resolve_overrun` | `attempt_id`, `decision`, `event_id` | none | one-shot only (operator) | `recorded{event_id, duplicate, outcome, ...}` |
| `reconcile_uncertain` | `attempt_id`, `event_id`, `evidence` | none | one-shot only (operator) | `recorded{event_id, duplicate, outcome, ...}` |
| `reconcile_subscription` | `attempt_id`, `event_id`, `evidence` | none | one-shot only (operator) | `recorded{event_id, duplicate, outcome, ...}` |
| `resolve_block` | `decision`, `event_id`, `organization`, `reason` | `ids`, `marker` | one-shot only (operator) | `recorded{event_id, duplicate, outcome, ...}` |
| `drop_debit` | `event_id`, `key`, `marker` | none | one-shot only (operator) | `recorded{event_id, duplicate, outcome, ...}` |
| `settle_legacy_call` | `event_id`, `evidence`, `phase`, `run_id` | none | one-shot only (operator) | `recorded{event_id, duplicate, outcome, ...}` |
| `seed_window` | `event_id`, `length`, `limit`, `pool`, `reset_at`, `used`, `window` | none | one-shot only (operator) | `recorded{event_id, duplicate, outcome, ...}` |
| `qualify_path` | `demand`, `evidence`, `path` | `account` | one-shot only (operator) | `qualified{path, demand, account, evidence, qualified_at}` |
| `unqualify_path` | `path` | none | one-shot only (operator) | `unqualified{path, removed}` |
| `native_prepare` | `assignment`, `client`, `job` | `profile` | not under `--serve` | `dispatched{attempt_id, client, model, effort, route, mode}`, `pending` or `blocked`; `attempt_id` may be left out of `assignment` ([native bridge](../clients/claude-code.md#native-routing-bridge)) |
| `native_complete` | `attempt_id`, `outcome` | none | not under `--serve` | the settlement ([native bridge](../clients/claude-code.md#native-routing-bridge)) |

Operator operations, and `set` with `managed_projects`, run only as a one-shot call: `--serve` and every in-process service answer `operator_op_not_served`. The native bridge ops answer `native_op_not_served` under `--serve`.

## Rules the command applies

- **Clock.** The command uses its own clock. `prepare` refuses a snapshot whose `now` differs from it by more than 60 s (`observation_stale`) and stamps the admission with its own time.
- **Unsent admissions.** An `admitted` attempt passes `check` and `dispatch` only under the same settings revision and at an age of 0 to 900 s. Otherwise the answer is `settings_revision_changed`, `admission_expired` or `clock_invalid`. A dispatched attempt skips both checks.
- **Settings changes.** A healthy ledger's write lock is held while `set` writes the settings, and `dispatch` reads them inside its own transaction, so `invalidated_attempts` lists exactly the attempts still unsent at the change. An absent, busy or unusable ledger never stops a settings change, because turning Smart Routing or API Usage off is a money stop: the settings are still written, and the answer has `invalidated_attempts: null` and a `ledger_state` reason.
- **`smart_routing: false`** ranks nothing: `prepare` uses the saved worker choices (`smart_routing_off`). It does not take a project out of `managed_projects`.
- **Busy waits.** `check` and `status` wait at most 0.2 s for a busy ledger. Writes wait at most 0.3 s under `--serve` and 5 s in a one-shot call.
- **Dispatch authority.** `dispatch` re-runs the admission's gates in one write transaction and answers the one-use `authority` token once. The ledger keeps only its digest, so a lost answer's token cannot be recovered. `set {"authority_required": true}` is one-way (`authority_required_one_way` for `false`), creates the ledger when it is absent, and stamps every later admission; every managed launch needs it.
- **Managed projects.** For a checkout on `managed_projects`, `prepare` builds its own observations through the collector and refuses caller ones (`observations_not_accepted`; a missing or failing collector is `observations_unavailable`). A `pending` answer with `no_feasible_route` or `explicit_choice_unavailable` also lists the collector's exclusion codes, such as `quota_exhausted` or `billing_unverified`, in `reasons`: fixed codes only, never a route or value. Adding a project runs the activation precondition and gate; their refusals and what each needs are in the [Smart Routing procedure](../skills/manage-usage-routing/references/smart-routing.md#refusals-when-adding).
- **Quota refusals.** An exhausted account reading refuses its routes only while it is at most 60 s old; a stale one is read again at most once per 60 s per account reader, inside a one-shot prepare only. A provider quota-limit error on an OpenCode route refuses that OpenCode route for at most 120 s. When such a refusal leaves a route out, the `pending` answer adds `retry_after_seconds`: the shortest remaining window, 1 to 120 s, never the scheduled reset. The native bridge, Codex delegation and the OpenCode launcher pass `reasons` and `retry_after_seconds` on unchanged.
- **Explicit choice.** `assignment.choice` names `provider`, `model`, `harness` and `funding` as text, and optionally `effort`; another shape is `blocked` `invalid_choice`. `prepare` needs `effort` (`blocked` `effort_required`). `native_prepare` without `effort` tries that model's catalog efforts in the order `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, and the first admitted one answers, named in `effort`; a model the catalog does not list, or no admission, is `pending` `explicit_choice_unavailable` with the merged `reasons` and the smallest positive `retry_after_seconds` among the refused efforts, at most 120.
- **Generated attempt ids.** `native_prepare` without `assignment.attempt_id` generates `n` followed by 24 lowercase hex digits and returns it as `attempt_id` once the request reaches routing; an earlier refusal carries none and held nothing. A supplied id behaves as before: reusing an admitted one answers `already_prepared`.
- **OpenCode launcher.** `scripts/opencode_launch.py` refuses an `assignment` without `scope` (`scope_required`) and fills a missing `job.scope_ref` from it. A `recorded` result adds `session_id`, and for a succeeded turn `answer` (the complete final text, without launcher truncation). There is no `answer_truncated` field. A failed turn adds `provider_error` when the provider gave an error type matching `[A-Za-z0-9_.-]{1,64}`, never its text; `provider.chatgpt-limit` makes the reason `provider_quota_limit`. A request without `assignment.attempt_id` gets one generated the same way as the native bridge's, returned as `attempt_id` in every result of that request; a supplied id behaves as before (`attempt_closed` on reuse). A `blocked` `interrupted` result (exit 1) adds `attempt_id` and `uncertain`. With `uncertain: true` the hold was recorded `uncertain` after a dispatch, or a dispatch in doubt, and the result also names the operator op as `launch_unconfirmed` does: `recovery` (`reconcile_subscription`, or `reconcile_uncertain` for a dollar hold) and `command` (`scripts/routing.py <recovery>`). The caller's procedure is [Launch a managed OpenCode worker](../clients/opencode-launch.md).
- **Table summary.** `status.table` is `{state, version, rows, generated_routes_per_path}`, read from the strategy table file alone: `state` is `absent`, `invalid`, `outdated` (a schema other than the current one) or `ok`, and `generated_routes_per_path` counts, per path rule, the distinct strategies with that path's harness and provider.
- **Test switch.** `--test-inputs` lets tests supply `now`, an off-clock snapshot and a `table`. It is a usage error unless both `--settings` and `--ledger` are given and differ from the production defaults, and `status` reports `test_inputs`. No request, configuration or environment variable can enable it.

The deleted plan also held the derivation of host session and message ids, the shim handshake and the unmanaged-session rules (Task S3.4), and the workflow binding and fresh gates at dispatch (Task S3.5). They are defined in `runtime/routing.py` and `runtime/routing_gates.py` and are not restated here.
