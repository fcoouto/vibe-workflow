# Project feature settings and plugin identity

Use one project settings format in `.vibe-workflow/settings.json`:

```json
{"version": 1, "features": [{"smart_router": {"enabled": true}}]}
```

Each array entry is an object with one unique feature name and an object of
settings. An empty array is valid. Smart Router alone is recognized today;
preserve unknown feature entries, their order, and unrelated fields. Smart
Router retains its boolean `enabled`, its default proposal of ON, and opt-out.
Do not create a feature registry or add unshipped features.

The user chose a strict cutover over reading both formats. Missing settings
remain unconfigured. A file with a top-level `smart_router` key, including a
mixed-format file, blocks reads and writes without modification. Document
manual conversion for 0.4.0 users, preserving their saved choice and a backup.
Preview and native-permission-checked apply remain the only onboarding writes.

Use `vibe-workflow` for both the OpenCode server plugin ID and package name.
Keep `smart_router_select`, `vibe_workflow_onboard`, `vibe-onboard-project`,
the router state directory, and all installation and project configuration
unchanged. This change does not publish or update installed copies.
