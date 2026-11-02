# Workstreams controls

This procedure covers feature status and enable/disable requests. Load operation
details only for the route requested.

Use the installed package's `scripts/workflow.py delegation workstreams` controls
in the explicitly selected, supported enrolled checkout. Select one feature:
`resource_admission` or `capacity_recovery`. Mutations compare
`expected_revision` with that feature's current revision; on conflict, reread that
feature's status and reconcile intent. The two revisions are independent. Do not
enroll another checkout, initialize state or change authentication here.

```text
python3 -I -B <package>/scripts/workflow.py delegation workstreams status --cwd <checkout>
python3 -I -B <package>/scripts/workflow.py delegation workstreams enable --cwd <checkout>
python3 -I -B <package>/scripts/workflow.py delegation workstreams disable --cwd <checkout>
```

Supply one JSON object on stdin identifying `client` and `feature`; mutations also
require that feature's `expected_revision`. For example:

```json
{"client":"codex","feature":"resource_admission"}
{"client":"codex","feature":"capacity_recovery"}
{"client":"codex","feature":"resource_admission","expected_revision":12}
{"client":"codex","feature":"capacity_recovery","expected_revision":4}
```

The revision numbers illustrate the JSON type; use the current revision for the
selected feature. Status and resource controls return only `ok`, `control_scope`,
`scope_id`, `checkout`, `feature`, `enabled` and `revision`. They do not read
recovery state. A missing or unknown feature and extra request fields are rejected;
there is no implicit combined setting. Status is read-only: it does not initialize
state, start workers, reserve resources or create schedules.

New supported scopes start with both features enabled only when no saved choice
exists. Preserve each separate saved choice. A feature with no separate saved
choice derives from the historical combined local choice, when present; never
overwrite a separate choice. An existing scope with no saved or historical choice
remains disabled. The first feature mutation saves both effective choices so later
legacy combined changes cannot overwrite either separate preference. Explicit
onboarding does not need a separate activation announcement; explain useful next
steps, blockers or decisions. Repeating the same setting preserves its revision.
Unsupported or invalid authority is an error, never evidence that a feature is off.

Enabling resource admission permits eligible heavy phases under the
[resource procedure](../../../clients/workstreams-resources.md). Enabling capacity
recovery permits eligible workflow parents to register under the
[recovery procedure](../../../clients/workstreams-recovery.md). Neither toggle
starts a worker or creates a schedule. Ordinary delegation and shared safety do
not depend on either preference.

Disabling resource admission blocks new resource-managed phases but leaves ordinary
delegation, existing reservation status, cancellation of unlaunched waits, and safe
release/reconciliation available. Occupied or uncertain owners retain claims.

Disabling capacity recovery first runs its read-only preflight. If original-path
owners, deliveries or schedule operations are unresolved, the operation exits 1,
returns `ok:false`, `preference_changed:false`, the original-path `error` and a
`recovery` result with `ready:false`; settings remain untouched. After an
acknowledged change, the control withdraws affected registrations and returns
the preference fields plus `recovery` with the compact `wanted`, `schedule_id`,
`pending`, `suspended`, available `historical_registrations` and `next_action`.
Finish any required native schedule reconciliation; delete the shared schedule
only when no eligible parents remain. If registry reconciliation fails after the
setting is written, the command exits 1 and reports the actual `preference_changed`
value and recovery action without silently rolling back. Pending schedule work
does not block ordinary delegation or resource admission. Neither disable stops
a running command or clears ownership.
Re-enable rechecks current authority; it cannot revive stale permission or reset
consumed budgets, deadlines, delivery intents or manual schedule suspension. Keep
historical artifacts and their readers at their original paths. Do not migrate,
repin or reset old task records; if a local scope cannot be reconciled, leave it
on its existing path.

Feature-control replies identify the selected checkout and preference only. Use
the resource or recovery status operation for operational lifecycle details. A
successful local control does not establish native execution or recovery.
