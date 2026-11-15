# Upgrade the plugin in every client

Each supported client keeps its own copy of this plugin and has its own update command. Nothing upgrades them together, so run this procedure when the user asks to upgrade or update the plugin, when preparation finds it outdated (step 4 of the [procedure](procedure.md)), or when the user acts on a startup drift warning. Update Codex and Claude Code, then reconcile any existing OpenCode sources that depend on this package. This does not add OpenCode startup or onboarding support.

Run only the update commands and scoped source reconciliation below, under the [consent rule](../../../policies/11-tool-integrations.md#choose-and-configure): a request to upgrade or to prepare the workspace covers an outdated plugin, announced first, and nothing else starts an upgrade. Preserve unrelated client configuration, permissions, model choices and other plugins.

## Before you start

1. Run `python3 -B scripts/workflow.py upgrade status` from the package you are upgrading to: the release or checkout you just installed or refreshed, not a client's cached copy. It writes nothing. It shows the package version, the expected-version record, and each client's installed version. A client shows `not-installed` when its command ran and found no install, and `unknown` with a reason when its command is missing, fails, times out or prints something unexpected.
2. Note each client's source from the status and marketplace. A Git release marketplace is the recommended source, because Codex can refresh it with one command. Do not move a client to another source unless the user chooses to.
3. Inventory existing plugin-owned skill registrations and instruction pointers in the selected clients and known project setup records. Record their resolved package, version/content identity and any deliberate task pins before replacing caches. Back up affected configuration through the [workspace procedure](procedure.md#steps); do not scan or rewrite every enrolled project. A directory that still exists may nevertheless contain obsolete instructions.

## Update each client

**Codex**

- Git marketplace: `codex plugin marketplace upgrade <marketplace>`.
- Local source (a folder on this machine): refresh the folder first, then `codex plugin remove vibe-workflow@<marketplace>` and `codex plugin add vibe-workflow@<marketplace>`. Codex copies a local package when it installs, so it does not refresh on its own. For an export of a development checkout, refresh it with `python3 -B scripts/package.py` from that checkout.

**Claude Code**

- `claude plugin marketplace update <marketplace>`, then `claude plugin update vibe-workflow@<marketplace>`. Add `-y` when the command does not run in an interactive terminal, and `--scope <scope>` for an install outside user scope.

A client that is not installed, whose command is missing or whose command fails is not upgraded. Report it that way and continue with the other clients and the rest of the preparation. Do not retry in a different way, such as another source or command, without asking.

## Reconcile dependent sources

Client installation does not refresh another client's references. In OpenCode, inspect the effective `skills` and `instructions` sources, including contributing configuration, and the known plugin pointers identified above. Use the current official OpenCode configuration/skills documentation; preserve unrelated entries, permissions, models and plugins.

- Prefer a maintained, version-independent export when it is already the accepted source. For this repository's local development setup, `~/.cache/vibe-workflow/dev-package/skills` survives Codex cache replacement. Refresh that export from the intended committed source before relying on new content; verify its manifests and instruction/skill bytes. A stable path alone does not prove freshness.
- Replace only confirmed plugin-owned obsolete references. Reuse the accepted source; do not migrate a Git-marketplace user to a development export or invent an export directory. If the supported source is an installed versioned copy, reconcile its dependents on every upgrade before retiring the old copy. Missing authority, an unavailable source or an unresolved custom reference leaves that dependent integration pending.
- Preserve deliberately pinned active tasks and their old package resources until an authorized transition; global discovery changes do not repin them. Follow [deployment reconciliation](../../../clients/delivery.md#adopt-and-update-the-rule) for known project instruction pointers.
- Query a fresh native OpenCode skill catalog for the affected working directory after the change. Confirm the expected package skills, including `recovering-task-context` when shipped, resolve to the accepted source and match its content. Re-read configured instruction targets and known project pointers against that same source; skill discovery alone does not verify instruction delivery.
- OpenCode reloads configuration changes without a restart. If discovery remains stale, use its supported reload once and verify again; do not restart the shared service or launch a model solely to manufacture proof. Record unavailable catalog or fresh-context instruction observations as pending. Do not claim existing conversations discarded their earlier context.

Keep the backup, before/after source identities, actual catalog readback and unresolved pins in the preparation record. Cache removal must not strand a known dependent reference; if reconciliation cannot finish safely, retain the needed old resource or stop the dependent update.

## Restart and trust

1. Start a new session in Codex and Claude Code. A running session keeps the version it loaded. OpenCode uses the source/discovery verification above; it has no workflow startup hook to qualify here.
2. If the client asks to review or trust the plugin's hooks, ask the user to confirm it. Do not approve trust for them.

## Confirm

1. Run `upgrade status` again. Every installed client must show the package version.
2. Confirm dependent source reconciliation separately before claiming the upgrade complete. `upgrade status` and `upgrade record` check Codex/Claude installed versions, not OpenCode discovery or instruction freshness; a matching version is not proof of those.
3. Run `python3 -B scripts/workflow.py upgrade record` from the same updated package, because it takes the expected version from the package it runs in. It writes that version to a private file outside every repository. It refuses while any installed client shows another version, and while any client is `unknown` for any reason, including a missing command; the message names the client and why. Only a client confirmed `not-installed` is listed without blocking. Fix the client and run it again.
4. Report separate states for each client:
   - **Installed version** from `upgrade status`.
   - **Source**: Git release marketplace or local folder.
   - **Hooks trusted**: confirmed, pending or unknown.
   - **Startup observed**: whether a new session ran the startup hook and showed no drift warning.
   - **Dependent sources verified**: resolved instruction/skill source, content identity and actual catalog or fresh-context observations; pending items stay explicit. Mark this plugin's startup/hook-trust states not supported for OpenCode, not passed.

Anything not seen stays `unknown` or pending. Never report an upgrade as working before a new session has run.

## Good to know

- The expected-version record lives under `XDG_STATE_HOME` (default `~/.local/state`). A client started with a different `XDG_STATE_HOME` will not see it and simply shows no drift warning.
- A drift warning adds about one line to the startup context.
