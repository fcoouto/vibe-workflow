# Feature settings

Read this when adding or changing a feature, or when diagnosing `workflow.py features`. The design is in [feature settings](../docs/superpowers/specs/2026-11-09-feature-settings-design.md).

## Add a feature

Ship one data-only manifest, `features/<id>.json`, in the package:

| Field | Meaning |
| --- | --- |
| `manifest_version` | `1` |
| `id` | Lowercase, dotted for sub-features (`openrouter.workers`); equals the file name |
| `summary`, `skills` | What it does and which skills operate it |
| `default` | `{"enabled": true\|false}`: the value when project settings say nothing |
| `settings` | Typed values: `{"<key>": {"type": "boolean\|integer\|number\|string\|array\|object", "default": ...}}` |
| `scopes` | `["project"]`; other scopes are reserved |
| `consent` | `none`, `transmits` or `spends`; anything but `none` needs a private, checkout-bound grant and is never enabled from the committed file |
| `control` | `settings` (behavior reads the resolved value), `adapter` (state still lives in its own store; add `source: {"store", "scope"}`) or `fixed` (no off switch) |
| `requires` | Feature IDs that must be effective first |
| `procedure` | The package-relative procedure that manages it |

Behavior reads the resolved value with `runtime.features.resolve(cwd)["features"][id]["effective"]`, never the settings file directly.

## Project settings

`.vibe-workflow/settings.json` is committed and holds team choices only:

```json
{"schema_version": 1, "revision": 1, "features": {"workstreams": {"enabled": true}}}
```

- Missing entries use the manifest default. An invalid file or entry makes the affected feature unavailable, so a broken file never switches anything on.
- No machine paths, secrets or identities. Private files live only under `.vibe-workflow/private/` (owner-only); `.vibe-workflow/project.json` and `private/` must be ignored by Git, and writes refuse otherwise.
- Write only through the command. It holds a lock, writes a private backup record first, rechecks the file's content right before an atomic replace, and returns the backup name for `undo`.

## Commands

```text
workflow.py features status [<id>] --cwd <checkout> [--summary]
workflow.py features enable|disable <id> --cwd <checkout>
workflow.py features set <id> <key> <json-value> --cwd <checkout>
workflow.py features unset <id> <key> --cwd <checkout>
workflow.py features migrate plan|apply --cwd <checkout>
workflow.py features undo --backup <backup> --cwd <checkout>
```

`status` is read-only. Each feature reports `desired` (value and source: `default`, `project`, `legacy`), `consent`, `effective`, `observed` and a `reason`: `default`, `project`, `migration_pending`, `ignored_override`, `needs_consent`, `prerequisite_missing`, `invalid_settings` or `unchecked`. Writes accept only `control: settings` features without consent; others point to their procedure. Errors exit 1 with `{"ok": false, "error": ...}`; `status` also exits 1, with `ok: false` and its `errors` list, when settings are invalid. Messages name keys and paths, never supplied values.
