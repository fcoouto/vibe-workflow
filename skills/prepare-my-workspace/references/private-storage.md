# Private workflow storage

Load for requested machine preparation, storage repair or explicit review-accounting or owner-accounting inspection. Routine tasks do not load this procedure. Existing enrolled Projects retain their configured storage. For an explicit screening-budget status, reservation or settlement request, read [screening budget controls](screening-budget.md) now and stop after that procedure; do not run storage status or resolve/fetch Toolkit.

Inspect without changing configuration:

```sh
python3 -B <plugin-root>/scripts/workflow.py storage status
```

For facility-only verification, stop after reporting this result. An explicit owner- or review-accounting request continues to its section below. Missing storage is a setup prerequisite, not permission to initialize it.

For authorized preparation, reuse the selected canonical private directory. If none exists, include a proposed location, such as `~/.local/share/vibe-workflow`, in the preparation scope. Keep it outside Git, targets, plugin caches and client instruction-discovery paths. Vendor plugin data and temporary plan directories do not select shared storage.

Run `storage prepare` with bounded JSON on standard input:

```sh
python3 -B <plugin-root>/scripts/workflow.py storage prepare
```

```json
{"private_data_dir":"/absolute/private/path"}
```

The machine configuration at `~/.vibe-workflow/machine.json` preserves the selected location and original authority bindings. Identical preparation reuses it. Another selection, interrupted initialization, missing or replaced storage, and uncertain ownership remain protected. Preserve configuration and partial files; do not delete, recreate, repin or choose another store to clear the condition. Report the observed blocker for reconciliation. This slice does not automate interrupted setup repair.

The helper creates private schema-3 review evidence without enrolling a target or fabricating Project/task records. For explicit temporary Repository owner accounting, `storage owner --cwd PATH` resolves an ordinary unenrolled Repository owner; repeated subsets and benign aliases reuse original identity and age.

An enrolled target reuses its authenticated Project store and original authority provenance. A linked checkout shares that Repository's accounting while retaining its own observed checkout facts. This grants no activation or accepted task scope. Missing machine configuration does not require setup for an otherwise authenticated enrolled target: `temporary_history` explicitly reports unknown mapping. Related temporary history, ineligible membership, conflicting stores and unresolved transitions stay protected without allocation or reparenting. `age_basis: "authority_provenance"` describes the stored age, not the Project's birth. Unknown history grants no new allowance.

For an explicit standalone or umbrella Project selection, use the same bounded D3 `project_scope` object through:

```sh
python3 -B <plugin-root>/scripts/workflow.py storage project-owner
```

Standard input is exactly `{"project_scope": ...}`. Pass the existing D3 scope accepted by [`_project_scope`](../../../runtime/onboarding.py): canonical root, selected member checkouts, literal membership, authority preimage/reference and named Project files. The helper observes ownership itself; supplied observations or plan IDs do not establish it.

A clean unenrolled umbrella creates one compact owner for its root and selected members. Repeat, member reordering and selected-member subsets with unchanged membership retain its reference and original age. Ordinary file, subdirectory and benign alias lookups for a recorded member reuse that accounting. Enrolled scopes retain their actual Project authority and baseline, including separate selected checkout facts.

Changed membership, added members, crossing roots, related historical owners, mixed enrollment and temporary linked worktrees require reconciliation; no replacement owner is allocated. Protected results retain authenticated related references and ages. Incomplete history remains explicitly unknown. Interrupted publication can leave a protected orphan; preserve it for reconciliation rather than deleting it or retrying into fresh ownership. Project-owner acquisition grants no screening reservation.

Completion means the selected authority reopens with its original bindings and produces a stable owner through the supported control. Package checks do not prove client execution, complete temporary retention, Project caps or detailed-report admission. Continue using bounded chat findings while those lifecycle requirements remain unavailable.
