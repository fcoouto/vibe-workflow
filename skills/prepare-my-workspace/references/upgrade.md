# Upgrade the plugin in every client

Each supported client keeps its own copy of this plugin and has its own update command. Nothing upgrades them together, so run this procedure when the user asks to upgrade or update the plugin, or after a startup drift warning. It covers Codex and Claude Code, one after the other.

Run the user's approved commands only. Keep the client configuration, permissions, model choices and other plugins as they are. Never start an upgrade the user has not asked for.

## Before you start

1. Run `python3 -B scripts/workflow.py upgrade status` from the package you are upgrading to: the release or checkout you just installed or refreshed, not a client's cached copy. It writes nothing. It shows the package version, the expected-version record, and each client's installed version. A client shows `not-installed` when its command ran and found no install, and `unknown` with a reason when its command is missing, fails, times out or prints something unexpected.
2. Note each client's source from the status and marketplace. A Git release marketplace is the recommended source, because Codex can refresh it with one command. Do not move a client to another source unless the user chooses to.

## Update each client

**Codex**

- Git marketplace: `codex plugin marketplace upgrade <marketplace>`.
- Local source (a folder on this machine): refresh the folder first, then `codex plugin remove vibe-workflow@<marketplace>` and `codex plugin add vibe-workflow@<marketplace>`. Codex copies a local package when it installs, so it does not refresh on its own.

**Claude Code**

- `claude plugin marketplace update <marketplace>`, then `claude plugin update vibe-workflow@<marketplace>`. Add `-y` when the command does not run in an interactive terminal, and `--scope <scope>` for an install outside user scope.

A client that is not installed, whose command is missing or whose command fails is not upgraded. Report it that way. Do not skip it and do not retry in a different way without asking.

## Restart and trust

1. Start a new session in each client. A running session keeps the version it loaded.
2. If the client asks to review or trust the plugin's hooks, ask the user to confirm it. Do not approve trust for them.

## Confirm

1. Run `upgrade status` again. Every installed client must show the package version.
2. Run `python3 -B scripts/workflow.py upgrade record` from the same updated package, because it takes the expected version from the package it runs in. It writes that version to a private file outside every repository. It refuses while any installed client shows another version, and while any client is `unknown` for any reason, including a missing command; the message names the client and why. Only a client confirmed `not-installed` is listed without blocking. Fix the client and run it again.
3. Report four separate states for each client:
   - **Installed version** from `upgrade status`.
   - **Source**: Git release marketplace or local folder.
   - **Hooks trusted**: confirmed, pending or unknown.
   - **Startup observed**: whether a new session ran the startup hook and showed no drift warning.

Anything not seen stays `unknown` or pending. Never report an upgrade as working before a new session has run.

## Good to know

- The expected-version record lives under `XDG_STATE_HOME` (default `~/.local/state`). A client started with a different `XDG_STATE_HOME` will not see it and simply shows no drift warning.
- A drift warning adds about one line to the startup context.
