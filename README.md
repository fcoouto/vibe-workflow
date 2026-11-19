# Smart Router for OpenCode

An on-demand **advisory selector**, extracted from Vibe Workflow.
It returns a configured provider, model and effort. It never launches a worker,
reserves quota, grants spending authority, or changes another plugin's behavior.

**Breaking change in 0.4.0:** The slim OpenCode plugin replaces the legacy
workflow toolkit. It is not an update for legacy Codex or Claude Code installs.
It does not migrate legacy settings, credentials, or account caches.

## Requirements

- OpenCode V2 with server tool transforms and the session context hook.
- Git for project discovery, even in folders that are not Git repositories.
- Python 3.11+ on macOS or Linux. No Python or npm dependencies.
- For the Claude path only, the existing native Claude CLI login.

Downloading the package does not install or activate it.
No credentials, strategy rows, account history or trial evidence are included.

## Install later

After choosing to install, add the export directory to the existing `plugins`
array in `opencode.jsonc`. Preserve every other entry and setting.

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "plugins": [
    { "package": "/absolute/path/to/export" }
  ]
}
```

Do not copy `index.js` alone: its Python runtime and configuration manifest must
remain beside it. To unload, remove only this package's entry. OpenCode reloads
configuration changes; no background service restart is needed.

### Onboard a project

Once the plugin is loaded, ask in the project's OpenCode chat:
**“Use `vibe-onboard-project` to onboard this project.”**
The `vibe-onboard-project` skill and `vibe_workflow_onboard` tool show the
detected checkout, ask about Smart Router, and preview the settings change.
Confirm the preview before saving. Native edit permissions still apply.

The project choice lives in `.vibe-workflow/settings.json` at the checkout root:

```json
{"version": 1, "smart_router": {"enabled": true}}
```

New proposals default to ON. Repeat onboarding preserves a saved choice unless
you change it. Choose **Keep existing model selection** to opt out. This
portable file contains only project preferences; native credentials, strategy
configuration and account caches stay separate. Onboarding does not install
anything, change client configuration or create a strategy table.

For ON projects, additive chat context asks callers to consult
`smart_router_select` before delegating. If no recommendation is available,
the caller should show the `vibe-workflow` notice and keep the client's existing
selection. The plugin does not replace `subagent`, agent roles, permissions,
models or retries. This is advisory guidance, not enforced model behavior.

## Configure explicitly

State defaults to `${XDG_STATE_HOME:-~/.local/state}/vibe-workflow/smart-router`.
An optional plugin `options.state_dir` must be an absolute canonical path.
Use private directories (`0700`) and files (`0600`), without symlinks or hardlinks.
The selector never reads legacy `routing/` or `usage/` state.

Copy the two examples into that directory as `router.json` and
`strategy-table.json`. They deliberately start **disabled and empty**.
Missing/disabled configuration or empty rows produce no selection without
starting a native reader or creating state.
Onboarding reports **On · Setup needed** in that case. **On · Ready** means
local configuration passed inspection, not that a route is eligible or an
account is available. Eligibility is checked when the caller requests a route.

To enable later, set `enabled: true`, choose path IDs from `config/routes.json`,
and supply independently verified native provider/model/effort/harness identities
in the table. This is a shape example with invented names, not a usable route:

```json
{
  "schema": 2,
  "version": 1,
  "rows": {
    "review|high|high": [
      {
        "strategy_id": "example-review",
        "provider": "claude-agent",
        "model": "replace-with-native-model-id",
        "effort": "replace-with-native-effort",
        "harness": "claude-agent-sdk via opencode"
      }
    ]
  }
}
```

Rows are ordered preferences, not evidence of entitlement. Optional
`context_limit` is a token count. Caller constraints only narrow the rows.
Without profile bands, classification uses high complexity, high risk and
independent review. Supported work kinds: implementation, review, investigation,
planning, testing and writing.

```json
{"v": 1, "work_kind": "review"}
```

Optional `profile` fields: `complexity_band`, `risk_band` (low/medium/high/critical),
`review_policy` (none/self/independent/strict), `context_tokens`, `tools`, and
`uncertainty` (low/medium/high). Optional `constraints` fields: `allowed_models`,
`excluded_models`, `allowed_efforts`, `allowed_harnesses`, `allowed_strategy_ids`.
The tool accepts no credentials, observations, commands or execution requests.

## What selection proves

Only a current, account-bound `provider_blocked` paid-overflow fact permits a
selected result. `provider_managed` and `unknown` are excluded, never relabeled.
Current ChatGPT and Go readers do not prove blocked overflow, so those paths
normally remain unselected. The Claude reader can prove it only when native
metadata explicitly reports extra usage disabled.

Results have `state: selected`, `no_selection` or `blocked`, and always:

```json
{"admission": false, "reservation": false, "spend_permit": false}
```

Only `selected` carries a non-null `route`, with exact `provider`, `model`, and
`effort`. A choice is not a permit, billing receipt, resolved SKU, entitlement
check or proof of production safety. Native execution must independently honor
the account, permissions and requested model.

Readers reuse native credentials in memory; they never copy them into state.
Facts expire after 900 seconds. Only positive, account-bound exhaustion observed
within 60 seconds excludes a route; unknown quota is not invented headroom.
Explicit zero-utilization/null-reset idle meters create no window or reset.
Acquisition runs on demand, with bounded transports, private caches, generation
fences and 300-second failure backoff. No polling, daemon, model probe or provider
retry is added. Native metadata readers may contact their providers without
inference; global native configuration remains a documented upstream boundary,
not a sandbox provided by this plugin.

To pause selection, set `router.json`'s `enabled` to `false`. State and credentials
remain untouched. To remove the plugin, unload its registration; deleting native
credentials or legacy workflow state is neither required nor recommended.

## Development

Run from the extracted branch:

```sh
python3 -B -m unittest discover -s tests
node --test tests/plugin.test.js
python3 -B scripts/validate.py
python3 -B scripts/package.py --output dist
python3 -B scripts/validate.py --root dist
```

`dist/` itself is the installable package root; no nested package directory is needed.
The output path must not already exist. Choose a new path for another export;
the builder never replaces an existing directory. The explicit allowlist excludes
tests, development instructions, old adapters, skills, policies and private state.
See `docs/dependencies.md` for source provenance and the exact extraction inventory.
