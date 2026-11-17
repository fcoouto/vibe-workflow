# Codex sandbox for the native bridge

Load this file when a Codex coordinator in a [managed project](../policies/03-agent-delegation.md#managed-projects) runs the [native bridge](claude-code.md#native-routing-bridge) inside a Codex sandbox.

**Why the bridge needs more than read-only.** Every `native_prepare` writes routing state under `~/.local/state/vibe-workflow`. When the stored Codex account reading is missing or stale, it also makes one zero-token account read by starting the user's own `codex app-server`, which writes Codex's state under `~/.codex` and needs the network. Codex's default sandboxes deny this, so the read cannot run and the bridge answers `native_binding_unverified` or `native_catalog_unavailable`. That is a sandbox limit, not an account problem.

**Per-run flags that made the bridge run in a live test.** The user starts the Codex coordinator with them; no configuration file changes:

```sh
codex exec --json -C /absolute/checkout \
  -c 'default_permissions="vwbridge"' \
  -c 'permissions.vwbridge.extends=":read-only"' \
  -c 'permissions.vwbridge.filesystem={"<home>/.local/state/vibe-workflow"="write","<home>/.codex"="write","<home>/.codex/config.toml"="read","<home>/.codex/hooks.json"="read","<home>/.codex/auth.json"="read"}' \
  -c 'permissions.vwbridge.network.enabled=true' \
  '<prompt>'
```

- `<home>` is the user's home directory as an absolute path.
- The profile extends `:read-only`, grants write to `~/.local/state/vibe-workflow` and `~/.codex`, keeps `config.toml`, `hooks.json` and `auth.json` read-only, and enables the network.
- Add no `--sandbox` flag: a permissions profile must not be combined with `sandbox_mode`.
- The checkout stays read-only under this profile, so it suits read-only work; adding write access to the checkout is untested.
- Never edit the user's Codex configuration for this.

**What the profile still allows.** The network for every command, and write access to the rest of `~/.codex`, including its plugin cache, skills, rules and `AGENTS.md`. A tighter guard list is untested.

**Without these flags.** Report that the bridge needs them and hold the launch. Never switch hosts or launch outside routing.
