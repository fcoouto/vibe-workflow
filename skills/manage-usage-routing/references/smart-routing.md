# Smart Routing for a project

Smart Routing picks the model and effort of every worker and reviewer an agent starts in a *managed project*, on the user's subscriptions, and accounts for each launch. A project is managed when its checkout is on this machine's routing list (`managed_projects`). That list, the path qualifications and the one-way `authority_required` switch are machine state under `~/.local/state/vibe-workflow/`: nothing is committed to the project. The feature `smart_router` only reports it (`workflow.py features status smart_router`); it cannot be switched on or off through `features`.

Only the user turns Smart Routing on or off for a project. Coordinators and workers never change the list, qualify a path or call a routing operator op on their own: an agent runs this procedure's `qualify_path`, `authority_required` and `set managed_projects` only on the user's explicit request in the conversation or an explicit Yes to onboarding question 7 given on this machine by this user (its recorded `scope id` equals the current one; a Yes recorded elsewhere is not consent here), and `reconcile_subscription` only on the user's explicit request. Coordinators and workers follow [Managed projects](../../../policies/03-agent-delegation.md#managed-projects).

## What it does, per host

| Host | How workers launch in a managed project | What routing can stop |
| --- | --- | --- |
| Claude Code | Each native worker or reviewer only through the [native bridge](../../../clients/claude-code.md#native-routing-bridge). A checkout with runtime task state cannot use the bridge (`claude_code_native_bridge_unavailable`). | Advisory: routing chooses and accounts, but cannot block a launch the agent makes anyway. |
| Codex | With runtime task state: [delegation preparation](../../../clients/delegation.md#prepare-and-launch-once) (`workflow.py delegation prepare`). Without it (the basic onboarding default): the native bridge with `"client":"codex"` ([Codex client](../../../clients/codex.md)). | Advisory, as for Claude Code. |
| OpenCode | Workers only through the [managed launcher](../../../clients/opencode-launch.md) (`scripts/opencode_launch.py`). The interactive coordinator's own `task` or `subagent` tool is **not routed today**. Onboarding records only Codex and Claude Code, so an OpenCode-coordinated project joins only through a Codex or Claude Code enrolment. OpenCode Claude also needs a current native Claude login ([account identity](../../../clients/opencode.md#opencode-selected-account-identity-task-6)). | Launcher workers are enforced; the interactive coordinator is not. |

In every host the coordinator never launches outside routing on its own (a user's explicit request follows the [bypass rule](../../../policies/03-agent-delegation.md#managed-projects)), never switches to another host to get a launch, and never replaces required independent review with self-review.

Linked task worktrees of the managed primary checkout count automatically: `status --project`, the native bridge and the OpenCode launcher each accept a linked worktree of a managed primary checkout. A submodule or nested repository inside a linked task worktree does not: the worktree check stops at its own `.git`, so it counts as unmanaged.

## Charge risk: acknowledge before enabling

Tell the user, and enable only after they accept it:

- A route whose included quota is unknown or insufficient (a path qualified with `"demand": null`, or a reading that is missing, stale or too low) is admitted as provider-managed fallback: its admission carries `provider_managed_fallback` in `reason_codes`, and routing claims no included quota for it. The provider decides how the request is served and may charge it under the account's own overflow settings; routing makes no dollar estimate for it.
- Enabling also changes this machine: it turns on the one-way `authority_required` switch, which cannot be undone, and qualifies this machine's subscription paths. Both apply to every project on this machine.
- A turn that fails or is retried may still be charged by the provider.
- On Codex and Claude Code, routing is advisory: it cannot stop an agent that launches anyway.

## Commands

Run every command with the installed package's absolute path, written `<package>` below: the folder that holds the installed `scripts/routing.py`. On Codex and Claude Code the startup context names it: the managed line's `routing commands:`, or the package paths it gives for policies. OpenCode has no startup line: `<package>` is two folders above the `manage-usage-routing` skill folder it loads. Never run a checkout's own `scripts/routing.py`: a checkout of this plugin has one, and it can differ from the installed package. Each one reads exactly one JSON request on stdin; without it the answer is `{"state":"blocked","reason":"invalid_json"}`. Use the checkout's absolute path. Operator commands work only as these one-shot calls (`operator_op_not_served` otherwise).

Read the state at any time:

```sh
printf '%s' '{"v":1}' | python3 -I -B <package>/scripts/routing.py status --project /absolute/checkout
```

A `blocked` answer is a failed check, not proof that the settings are unreadable; fix the call and read again. In an `ok` answer: `managed` (true or false) answers for this checkout, `settings_state` is `unavailable` when the settings exist but cannot be read (routing then treats every checkout as managed), `settings_revision` and `settings.managed_projects` are needed to change the list, and `ledger.authority_required` shows the one-way switch. An absent ledger (`ledger.state: absent`), or a ledger without the `authority_required` key, means the switch is off. `table` summarizes the [strategy table](#strategy-table-first-time).

## Before any launch: the managed check

Before any worker, reviewer, subagent or task launch, read `status --project` for the checkout (above):

- `managed: true`: launch only through the host's managed path (the table above).
- `managed: false`: the unmanaged instructions apply.
- Any other answer, or none: the state is unknown, not unmanaged. Hold the dependent launch and report it.

A missing startup line proves nothing: OpenCode prints none, and a failed startup may print none.

## Prerequisites (read-only)

Check and report each one; a missing one stays pending with its blocker, never done:

1. The checkout's enrolment is integrated in the primary checkout (onboarding step 5), with Codex or Claude Code `configured` or `active`. The add command reads it there and refuses with `not_enrolled`, `enrolment_unavailable` or `advisory_host_unmanaged`.
2. `status` answers `ok` with `settings_state: ok`.
3. Each host the project uses is signed in to the subscription account that will be used. The add command itself makes one bounded account read per host, with no model call.
4. No unmanaged delegation is still open in the checkout. The add command checks it (`open_unrouted_assignments`); settle open ones through [delegation reconcile](../../../clients/delegation.md) first.
5. Once `authority_required` is on, `status` shows `ledger.unstamped_open_holds: 0`. Holds admitted before the switch stay unstamped, and every managed prepare refuses `unstamped_holds_open` until they settle. The switch changes no other flow: every packaged launcher dispatches through `dispatch`, which carries the authority, and none records a bare `dispatched` step.

## Enable, in this order

Run these steps only once the checkout's enrolment is integrated in the primary checkout; before that the add command refuses `not_enrolled`. Until then report Smart Routing pending with the resume condition "enrolment integrated in the primary checkout".

1. **Read** `status` (above). Note `settings_revision` and the current `settings.managed_projects`.
2. **Qualify the paths of the project's enrolled hosts**, once per machine: Claude Code `claude-native-subscription`, Codex `codex-chatgpt-subscription`. Activation checks only enrolled hosts, and OpenCode is never one (onboarding records only Codex and Claude Code). The OpenCode launcher still needs its own paths qualified before it can admit a route on them: qualify `opencode-claude-subscription`, `opencode-chatgpt-subscription` or `opencode-go-subscription` only when the user says OpenCode launches this project's workers.

   ```sh
   printf '%s' '{"v":1,"path":"codex-chatgpt-subscription","demand":null,"evidence":"<evidence id>"}' \
     | python3 -I -B <package>/scripts/routing.py qualify_path
   ```

   - `"demand": null` qualifies the account and the launch with unknown included quota: provider-managed fallback only (see the charge risk). Measured demand instead uses exactly the path's windows in thousandths, 1 to 1000: `{"5h":<n>,"7d":<n>}` (Claude paths), `{"7d":<n>}` (ChatGPT paths), `{"rolling":<n>,"weekly":<n>,"monthly":<n>}` (Go).
   - `evidence` is printable text of 1 to 128 characters (`evidence_invalid` otherwise) naming why the path was qualified. With no measured run, as in onboarding, use `onboarding-<yyyymmdd>` with the date of the Yes, for example `onboarding-20261116`, and record it in the setup record. Never a secret.
   - Optional `"account"` names the account; one shared pool uses one account (`pool_account_conflict` otherwise).
   - Refusals: `path_unknown`, `path_test_only`, `demand_invalid` (`demand` missing or with the wrong windows), `evidence_invalid`, `account_invalid`, `pool_account_conflict`, `path_qualifications_invalid`. Undo: `{"v":1,"path":"<path id>"}` to `unqualify_path`.
3. **Require authority**, once per machine. It is **one-way** and machine-wide: it can never be turned off, every later admission on this machine is stamped with it, and every managed launch needs it.

   ```sh
   printf '%s' '{"v":1,"authority_required":true}' | python3 -I -B <package>/scripts/routing.py set
   ```

4. **Add the checkout** with the full list: the current entries plus the primary checkout's absolute path. Its linked task worktrees count automatically.

   ```sh
   printf '%s' '{"v":1,"expected_revision":<settings_revision>,"change":{"managed_projects":["<existing>","/absolute/checkout"]}}' \
     | python3 -I -B <package>/scripts/routing.py set
   ```

   Before sending, record in the project's setup record only the `settings_revision` before the change and "added this project's primary checkout". Never copy the list: it names other projects' machine paths, and the setup record may be committed. `stale_revision` means the list changed meanwhile: read `status` again and resend.
5. **Read back**: `status --project` shows `managed: true`, and

   ```sh
   python3 -I -B <package>/scripts/workflow.py features status smart_router --cwd /absolute/checkout
   ```

   shows `effective: true`. Report Smart Routing as on only after both. In the setup record, record the readback as `managed: true` and `effective: true` and any blocker by its refusal code only: never paste `status` or a refusal, which carry `settings.managed_projects` or `projects` paths.

## Refusals when adding

| Answer | Meaning and next action |
| --- | --- |
| `not_enrolled` | The checkout was never onboarded: onboard it first. |
| `enrolment_unavailable` | Its accepted configuration cannot be read: repair onboarding, then retry. |
| `advisory_host_unmanaged` | None of its configured or active clients has a managed path: keep it unmanaged. |
| `open_unrouted_assignments` | An unmanaged delegation is still open (`rolled_back` shows whether a raced write was undone): reconcile it, then retry. |
| `invalid_managed_projects`, `stale_revision` | Malformed list (relative, duplicate, over 64 entries) or a changed list: read `status` and resend. |
| `managed_path_incomplete` | Nothing was written. `failing` names the checks and `codes` holds each one's reasons, below. |

| `codes` entry | Next action |
| --- | --- |
| `authority_required: [authority_not_required]` | Run step 3, then retry. |
| `authority_required: [ledger_unavailable]` | The ledger is busy or unreadable: retry later; report if it persists. |
| `route_manifest: [no_qualified_path]` | Run step 2 for the project's hosts. |
| `route_manifest: [no_generated_route]` | No strategy-table row uses a qualified path yet: build the table ([strategy table, first time](#strategy-table-first-time)). |
| `subscription_sources: [<host>_no_qualified_path]` | Qualify a path for that host (step 2). |
| `subscription_sources: [<host>_billing_unverified]` | The account read for that host failed or is stale: sign in to that account, then retry the add (it reads again). |
| `subscription_sources: [claude_code_native_bridge_unavailable]` | Claude Code cannot route a checkout with runtime task state: keep it unmanaged or use Codex. |
| `subscription_sources: [<host>_native_binding_unverified]` | The host's model catalog is not bound to its current account: sign in to that account, then retry the add (it reads again). |
| Any other check with `self_check_*` or `heading_missing` | The installed package is incomplete: update or reinstall it, then retry. |

## Strategy table (first time)

`route_manifest: [no_generated_route]` means this machine has no strategy table yet, or none of its rows uses a qualified path. Building one is a separate maintenance session, never part of a launch: it needs network access to the vendors' model documentation, model judgment to turn that guidance into rows, and tokens. Ask the user before starting it.

1. Write the three inputs as local JSON files, in the shapes of the [strategy table refresh](../../../clients/guidance-publication.md#strategy-table-refresh): guidance rows from the vendors' current documentation, the catalog, and benchmarks (`[]` when none can be cited).
2. Take each client's catalog entries from that client's own catalog, never copied across clients: `claude-native` and `codex-native` from the stored snapshots in `~/.local/state/vibe-workflow/routing/native-catalogs.json`; OpenCode from one `opencode api --standalone GET /api/model`. On `claude-native`, publish only the aliases the native Agent tool accepts. List each entry's `supported_efforts` from the lowest to the highest.
3. Give each entry exactly the `harnesses` and `provider` its qualified path matches. A wrong string still compiles as `refreshed` but generates no route.

   | Path | `harness` | `provider` |
   | --- | --- | --- |
   | `claude-native-subscription` | `claude-native` | `anthropic` |
   | `codex-chatgpt-subscription` | `codex-native` | `openai` |
   | `opencode-claude-subscription` | `claude-agent-sdk via opencode` | `claude-agent` |
   | `opencode-chatgpt-subscription` | `opencode provider openai` | `openai` |
   | `opencode-go-subscription` | `opencode provider opencode-go` | `opencode-go` |

4. Give every work kind at least the rows `medium|medium`, `high|high` and `critical|critical` (`complexity_band|risk_band`). A profile uses its exact row, else the nearest stricter one, so without `critical|critical` a critical task gets no suggestion.
5. Compile:

   ```sh
   python3 -B <package>/scripts/routing_table.py refresh --guidance GUIDANCE.json --catalog CATALOG.json --benchmarks BENCHMARKS.json
   ```

6. Verify with `status`: `table.state` is `ok`, `table.version` and `table.rows` name the new table, and `table.generated_routes_per_path` lists each qualified path.

`validate.py`'s model-name check fails when a packaged instruction names a model the table names. The package's instructions name no Claude family alias, so a table set aside for that reason can come back: `python3 -B <package>/scripts/routing_table.py rollback` restores the saved previous table. Record the machine's table decisions (sources, rows, exclusions) in the project's setup record.

## Attempt ids

- Native bridge: omit `attempt_id` on the first prepare. The bridge generates one (`n` and 24 hex digits) and returns it as the answer's `attempt_id`; an answer without one held nothing.
- OpenCode launcher: send a new unique `attempt_id` for each launch.
- If a prepare's output is lost (you never saw its `attempt_id`), never prepare again for that launch: report it, because a second prepare is a new attempt and could launch twice.
- Send the same id again only to retry that same launch, so a retry can never obtain a second permit. Start a new attempt (on the bridge, omit the id again) only where the reply table says so. Never invent an id that imitates an earlier one: every session on this machine shares the ids, and a reused one answers `already_prepared`.

## A user's explicit model choice

A `choice` names `provider`, `model`, `harness` and `funding`, plus `effort` when the user chose one. When the user named only a model:

- the native bridge tries that model's efforts from the lowest and admits the first that fits (the lowest effort may be too light for a critical task: tell the user which effort it chose); the answer's `effort` names it;
- Codex delegation preparation and the OpenCode launcher answer `blocked` `effort_required`: ask the user for the effort. Prepare routed only if the user says any model will do; never take that option on your own, because routing would pick a different model than the one the user chose.

Never invent an effort.

## What the coordinator does with each reply

These replies launch nothing. Report them with their `state` and `reason`, continue independent work, and take only the action shown. The OpenCode launcher's results after a send are in [its own table](../../../clients/opencode-launch.md).

| Reply | Meaning | The one next action |
| --- | --- | --- |
| `pending` `no_feasible_route` or `explicit_choice_unavailable` with `quota_exhausted` in `reasons` | Every eligible account looked full in a reading at most 60 s old, or its provider refused for quota within the last 120 s. | Hold the dependent delegation. Prepare once more after `retry_after_seconds` (at most 120), not at the scheduled reset: each prepare rechecks the account, so an early provider reset is recognized. If that prepare is refused for quota again, hold and report; prepare again only when the user asks or for a new launch. Routing never polls, and neither do you. |
| ... with `billing_unverified` in `reasons` | No current account fact for the route. | Ask the user to sign in to that account; prepare again afterwards. |
| `explicit_choice_unavailable` | The exact model and effort chosen by the user are not admissible now; routing never substitutes. | Report it; ask the user before choosing anything else. |
| `account_changed`, `native_binding_changed` | The account or its catalog changed after admission; the attempt is closed. | Prepare again with a new attempt id. |
| `account_binding_missing`, `native_catalog_unavailable` | No account binding or catalog snapshot for the route. | Ask the user to sign in to the host's account; prepare again afterwards (its account read binds them). |
| `catalog_mismatch` | The host does not list the admitted model, for an explicit choice the user's own model. Nothing was sent. | Report it; never substitute another model. |
| `effort_required` | A `choice` without `effort` on a path that needs one. | Ask the user for the effort. Prepare routed only if the user says any model will do. |
| `invalid_choice` | The `choice` is malformed ([explicit model choice](#a-users-explicit-model-choice)). | Fix it; never resend it unchanged. |
| `scope_required`, `profile_unavailable` | The launcher request has no `assignment.scope`, or the `job` is not a usable task profile. | Fix the request; never resend it unchanged. |
| `invalid_json` | The command got no JSON request on stdin, or an invalid one. It says nothing about the settings. | Fix the call and run it again. |
| `enrolment_unavailable` | The checkout's enrolment cannot be read. | Report it; onboarding needs repair. Never resend it unchanged. |
| `observations_unavailable` | The routing service could not build its own observations. | Report it and hold; never supply observations. |
| `attempt_closed` | That attempt is already settled. | Start a new attempt ([attempt ids](#attempt-ids)). |
| `already_dispatched` | An earlier call carried the permit. | Never respawn; find that launch. |
| `already_prepared` | This attempt id was already admitted. | If its earlier answer was a permit, the worker launched once. Otherwise prepare again with a new attempt id. |
| `legacy_route_retired` | A legacy spending route is closed in a managed project. | Use the managed path above. |
| Uncertain hold (`disposition: uncertain`) | A launch reported `not_started` or `unknown`; its capacity stays held. | Report it; `reconcile_subscription` (below) runs only on the user's explicit request. |
| `ledger_busy` | The ledger stayed busy past its wait. | Retry the same request later. |
| Any other reply | Not listed here. | Report it and hold the dependent launch; never resend it unchanged. |

## Uncertain holds

`status` shows them under `ledger` (`reconcile_hint`). Run this only on the user's explicit request; otherwise show them the command:

```sh
printf '%s' '{"v":1,"attempt_id":"<attempt id>","event_id":"<new unique id>","evidence":{"kind":"abandon"}}' \
  | python3 -I -B <package>/scripts/routing.py reconcile_subscription
```

`abandon` releases the held capacity without a reading. The other form, `{"kind":"host_usage","session_id":"<host session>","usage":{...}}`, needs the host's own message totals for that session (`source: host_message_totals`).

## Disable or undo

- Read `status`, then send the same `set` request with the current `settings_revision` as `expected_revision` and the current list without this checkout. Removal is always allowed and runs no checks, and every other project stays as it is now. Never send an older list: it would undo other projects' later changes.
- `authority_required` stays on: it is one-way. Qualifications stay until `unqualify_path`.
- `smart_routing: false` is **not** a way out. The project stays managed, and each prepare uses the saved [worker choices](worker-choices.md) instead (`smart_routing_off`).
