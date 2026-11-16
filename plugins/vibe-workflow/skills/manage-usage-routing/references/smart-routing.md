# Smart Routing for a project

Smart Routing picks the model and effort of every worker and reviewer an agent starts in a *managed project*, on the user's subscriptions, and accounts for each launch. A project is managed when its checkout is on this machine's routing list (`managed_projects`). That list, the path qualifications and the one-way `authority_required` switch are machine state under `~/.local/state/vibe-workflow/`: nothing is committed to the project. The feature `smart_router` only reports it (`workflow.py features status smart_router`); it cannot be switched on or off through `features`.

Only the user turns Smart Routing on or off for a project. Coordinators and workers never change the list, qualify a path or call a routing operator op on their own: an agent runs this procedure's `qualify_path`, `authority_required` and `set managed_projects` only on the user's explicit request in the conversation or an explicit Yes to onboarding question 7 given on this machine by this user (its recorded `scope id` equals the current one; a Yes recorded elsewhere is not consent here), and `reconcile_subscription` only on the user's explicit request. Coordinators and workers follow [Managed projects](../../../policies/03-agent-delegation.md#managed-projects).

## What it does, per host

| Host | How workers launch in a managed project | What routing can stop |
| --- | --- | --- |
| Claude Code | Each native worker or reviewer only through the [native bridge](../../../clients/claude-code.md#native-routing-bridge). A checkout with runtime task state cannot use the bridge (`claude_code_native_bridge_unavailable`). | Advisory: routing chooses and accounts, but cannot block a launch the agent makes anyway. |
| Codex | With runtime task state: [delegation preparation](../../../clients/delegation.md#prepare-and-launch-once) (`workflow.py delegation prepare`). Without it (the basic onboarding default): the native bridge with `"client":"codex"` ([Codex client](../../../clients/codex.md)). | Advisory, as for Claude Code. |
| OpenCode | Workers only through the [managed launcher](../../../clients/opencode.md#launch-a-managed-worker) (`scripts/opencode_launch.py`). The interactive coordinator's own `task` or `subagent` tool is **not routed today**. Onboarding records only Codex and Claude Code, so an OpenCode-coordinated project joins only through a Codex or Claude Code enrolment. OpenCode Claude also needs a current native Claude login ([account identity](../../../clients/opencode.md#opencode-selected-account-identity-task-6)). | Launcher workers are enforced; the interactive coordinator is not. |

In every host the coordinator never launches outside routing, never switches to another host to get a launch, and never replaces required independent review with self-review.

Linked task worktrees of the managed primary checkout count automatically. A submodule or nested repository inside a linked task worktree does not: the worktree check stops at its own `.git`, so it counts as unmanaged.

## Charge risk: acknowledge before enabling

Tell the user, and enable only after they accept it:

- A route whose included quota is unknown or insufficient (a path qualified with `"demand": null`, or a reading that is missing, stale or too low) is admitted as provider-managed fallback: its admission carries `provider_managed_fallback` in `reason_codes`, and routing claims no included quota for it. The provider decides how the request is served and may charge it under the account's own overflow settings; routing makes no dollar estimate for it.
- Enabling also changes this machine: it turns on the one-way `authority_required` switch, which cannot be undone, and qualifies this machine's subscription paths. Both apply to every project on this machine.
- A turn that fails or is retried may still be charged by the provider.
- On Codex and Claude Code, routing is advisory: it cannot stop an agent that launches anyway.

## Commands

Run every command from the installed package root (the folder that holds `scripts/routing.py`). Each one reads exactly one JSON request on stdin; without it the answer is `{"state":"blocked","reason":"invalid_json"}`. Use the checkout's absolute path. Operator commands work only as these one-shot calls (`operator_op_not_served` otherwise).

Read the state at any time:

```sh
printf '%s' '{"v":1}' | python3 -I -B scripts/routing.py status --project /absolute/checkout
```

A `blocked` answer is a failed check, not proof that the settings are unreadable; fix the call and read again. In an `ok` answer: `managed` (true or false) answers for this checkout, `settings_state` is `unavailable` when the settings exist but cannot be read (routing then treats every checkout as managed), `settings_revision` and `settings.managed_projects` are needed to change the list, and `ledger.authority_required` shows the one-way switch. An absent ledger (`ledger.state: absent`), or a ledger without the `authority_required` key, means the switch is off.

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
     | python3 -I -B scripts/routing.py qualify_path
   ```

   - `"demand": null` qualifies the account and the launch with unknown included quota: provider-managed fallback only (see the charge risk). Measured demand instead uses exactly the path's windows in thousandths, 1 to 1000: `{"5h":<n>,"7d":<n>}` (Claude paths), `{"7d":<n>}` (ChatGPT paths), `{"rolling":<n>,"weekly":<n>,"monthly":<n>}` (Go).
   - `evidence` is printable text of 1 to 128 characters (`evidence_invalid` otherwise) naming why the path was qualified. With no measured run, as in onboarding, use `onboarding-<yyyymmdd>` with the date of the Yes, for example `onboarding-20261116`, and record it in the setup record. Never a secret.
   - Optional `"account"` names the account; one shared pool uses one account (`pool_account_conflict` otherwise).
   - Refusals: `path_unknown`, `path_test_only`, `demand_invalid` (`demand` missing or with the wrong windows), `evidence_invalid`, `account_invalid`, `pool_account_conflict`, `path_qualifications_invalid`. Undo: `{"v":1,"path":"<path id>"}` to `unqualify_path`.
3. **Require authority**, once per machine. It is **one-way** and machine-wide: it can never be turned off, every later admission on this machine is stamped with it, and every managed launch needs it.

   ```sh
   printf '%s' '{"v":1,"authority_required":true}' | python3 -I -B scripts/routing.py set
   ```

4. **Add the checkout** with the full list: the current entries plus the primary checkout's absolute path. Its linked task worktrees count automatically.

   ```sh
   printf '%s' '{"v":1,"expected_revision":<settings_revision>,"change":{"managed_projects":["<existing>","/absolute/checkout"]}}' \
     | python3 -I -B scripts/routing.py set
   ```

   Before sending, record in the project's setup record only the `settings_revision` before the change and "added this project's primary checkout". Never copy the list: it names other projects' machine paths, and the setup record may be committed. `stale_revision` means the list changed meanwhile: read `status` again and resend.
5. **Read back**: `status --project` shows `managed: true`, and

   ```sh
   python3 -I -B scripts/workflow.py features status smart_router --cwd /absolute/checkout
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
| `route_manifest: [no_generated_route]` | No strategy-table row uses a qualified path yet: refresh the table ([strategy table refresh](../../../clients/guidance-publication.md#strategy-table-refresh)). |
| `subscription_sources: [<host>_no_qualified_path]` | Qualify a path for that host (step 2). |
| `subscription_sources: [<host>_billing_unverified]` | The account read for that host failed or is stale: sign in to that account, then retry the add (it reads again). |
| `subscription_sources: [claude_code_native_bridge_unavailable]` | Claude Code cannot route a checkout with runtime task state: keep it unmanaged or use Codex. |
| `subscription_sources: [<host>_native_binding_unverified]` | The host's model catalog is not bound to its current account: sign in to that account, then retry the add (it reads again). |
| Any other check with `self_check_*` or `heading_missing` | The installed package is incomplete: update or reinstall it, then retry. |

## What the coordinator does with each reply

These replies launch nothing. Report them with their `state` and `reason`, continue independent work, and take only the action shown.

| Reply | Meaning | The one next action |
| --- | --- | --- |
| `pending` `no_feasible_route` or `explicit_choice_unavailable` with `quota_exhausted` in `reasons` | Every eligible account is at its limit. | Hold the dependent delegation until the reported reset, then prepare once more. |
| ... with `billing_unverified` in `reasons` | No current account fact for the route. | Ask the user to sign in to that account; prepare again afterwards. |
| `explicit_choice_unavailable` | The exact model and effort chosen by the user are not admissible now; routing never substitutes. | Report it; ask the user before choosing anything else. |
| `account_changed`, `native_binding_changed` | The account or its catalog changed after admission; the attempt is closed. | Prepare again with a new attempt id. |
| `account_binding_missing`, `native_catalog_unavailable` | No account binding or catalog snapshot for the route. | Ask the user to sign in to the host's account; prepare again afterwards (its account read binds them). |
| `already_dispatched` | An earlier call carried the permit. | Never respawn; find that launch. |
| `already_prepared` | This attempt id was already admitted. | If its earlier answer was a permit, the worker launched once. Otherwise prepare again with a new attempt id. |
| `legacy_route_retired` | A legacy spending route is closed in a managed project. | Use the managed path above. |
| Uncertain hold (`disposition: uncertain`) | A launch reported `not_started` or `unknown`; its capacity stays held. | Report it; `reconcile_subscription` (below) runs only on the user's explicit request. |

## Uncertain holds

`status` shows them under `ledger` (`reconcile_hint`). Run this only on the user's explicit request; otherwise show them the command:

```sh
printf '%s' '{"v":1,"attempt_id":"<attempt id>","event_id":"<new unique id>","evidence":{"kind":"abandon"}}' \
  | python3 -I -B scripts/routing.py reconcile_subscription
```

`abandon` releases the held capacity without a reading. The other form, `{"kind":"host_usage","session_id":"<host session>","usage":{...}}`, needs the host's own message totals for that session (`source: host_message_totals`).

## Disable or undo

- Read `status`, then send the same `set` request with the current `settings_revision` as `expected_revision` and the current list without this checkout. Removal is always allowed and runs no checks, and every other project stays as it is now. Never send an older list: it would undo other projects' later changes.
- `authority_required` stays on: it is one-way. Qualifications stay until `unqualify_path`.
- `smart_routing: false` is **not** a way out. The project stays managed, and each prepare uses the saved [worker choices](worker-choices.md) instead (`smart_routing_off`).
