# Workspace requirements

What this workflow needs on a machine before it can load its rules and do its work, and why. `python3 -B scripts/workflow.py workspace status` checks every row for each installed client: Codex, Claude Code and OpenCode, plus the client running the session. [workspace-requirements.json](workspace-requirements.json) is the source the command reads; this page is its readable view. The list was written from Toolkit revision `7ef68dd7b00ddfc5cd464458855e4ab66dd2128f`.

| Item | Id | Scope | Why |
| --- | --- | --- | --- |
| Python 3.11 or newer | `python` | machine | Runs the plugin's hooks and checks. |
| Git | `git` | machine | Runs the plugin's checks and publishes task branches. |
| GitHub CLI, signed in | `gh` | machine | Publishes task branches and pull requests. |
| vibe-workflow plugin at the expected version | `vibe-workflow-plugin` | each client | Loads the workflow rules at session start; a stale copy leaves a client on old rules. |
| vibe-workflow hooks trusted | `vibe-workflow-hooks` | each client | Hooks that are not trusted do not run, so the workflow rules never load. |
| Superpowers | `superpowers` | each client | Design, planning, execution and review skills the workflow relies on. |
| Ponytail | `ponytail` | each client | Smallest complete change by default. |
| Caveman | `caveman` | each client | Concise communication without losing technical substance. |
| RTK | `rtk` | each client | Compact command output, fewer wasted tokens. |
| Context7 | `context7` | each client | Current library and tool documentation instead of stale memory. |
| Graphify command | `graphify-cli` | machine | Builds the local code map that agents query to find dependencies without reading many files. |
| Graphify skill | `graphify-skill` | each client | Lets each client use the code map. |

Machine items are checked once. Client items are checked once per client.

## States

Each check ends in one state: `ready`, `missing`, `outdated`, `needs-user` (a sign-in, trust approval or password only the user can give), `not-supported` or `unknown`. A reason accompanies every state but `ready`. A check is `ready` only when its own evidence was seen; what the command cannot see is `unknown`.

## What the checks touch

Checks read each client's own listings and documented config files, and nothing is installed, edited or started. MCP servers are read from configuration as names and flags only, never values; `ready` means configured, not connected. The only check that may use the network is the GitHub sign-in (`gh auth status`). A plugin or server counts only under its exact name, and a disabled or signed-out copy is not hidden by a working one.

## Not supported

A tool with no official support for a client is reported `not-supported` with the reason. Never install it by improvisation.

## Project needs

A runtime or tool the project itself declares, such as its language toolchain or package manager, is not a machine requirement. Onboarding reports it and checks it with the project's own commands.

Each item's official install instructions are found at the `install_ref` in the JSON file. Read them when installing; do not copy them here.
