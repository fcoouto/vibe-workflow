# Claude Code

Apply the [policy index](../policies/00-index.md) within the recorded checkout scope. Read the [shared startup contract](startup.md) when implementing, diagnosing or preparing enrollment.

## Startup behavior

The [adapter](../runtime/adapters/claude_code.py) identifies the native client as `claude-code`. It accepts `SessionStart` sources `startup`, `resume`, `clear`, `compact` and `fork`. Optional model/permission observations are preserved; additional native fields do not grant scope or permissions.

`.claude-plugin/plugin.json` registers `hooks/claude-code.json`, using `CLAUDE_PLUGIN_ROOT`. Keep `hooks/hooks.json` absent: Claude merges the default file with manifest hooks, which could execute the handler twice. The default `skills/` directory supplies packaged skills.

Normal output adds local client/policy pointers through `hookSpecificOutput.additionalContext`, without an activation announcement. Invalid integration returns diagnostic context and a warning. The shared startup code makes the enrollment decision; installation does not enroll a project.

## Runtime hooks

The package also registers `PreToolUse`, `PostToolUse`, `PostToolUseFailure` and `Stop` for the shared task runtime. They need an enrolled checkout with runtime state; otherwise they stay silent and startup-only behavior applies. Read the [task runtime contract](runtime.md) for task controls and limits.

- **Guard.** `PreToolUse` guards only `Edit`, `Write` and `NotebookEdit`, under the same rules as Codex's patch guard. Shell commands, MCP tools and external edits stay outside it. A subagent's edit uses the `agent_id` from the hook and inherits its parent's task authority.
- **Identity.** `prompt_id` is the turn and `tool_use_id` the call. Without them an event stays uncertain, never continues and still advances freshness.
- **Freshness.** `PostToolUse` and `PostToolUseFailure` advance checkout generation; a failed command may still have changed files.
- **Continuation.** `Stop` may continue once per prompt, shown to Claude as hook feedback. `stop_hook_active` must be an explicit `false`.
- **Input bound.** Runtime events read up to 8 MiB because `Write` carries a whole file; larger input reports a diagnostic and is not guarded.
- **Failure.** A crashed or timed-out hook lets the action proceed, as on Codex. `onFailure: "block"` is deliberately unused: the hooks are plugin-wide and would block ordinary editing in unenrolled projects.

Claude Code has no interrupt hook, so a user interrupt cannot pause a task; `Stop` does not run then. Evidence and delegation follow in later slices; until then Claude task records cannot reach `DONE`, and only `task start`, `update`, `status` and `title` accept `client: "claude-code"`.

## Qualification

A managed Claude scope needs a retained setup receipt, as on Codex; a coordinator-supplied observation is never enough. The [Claude setup source](../skills/onboard-my-project/references/qualification.md#claude-code-setup-source) verifies startup context from the client's own hook event stream. The resulting scope is qualification-only: pre-tool, post-tool and stop behavior stay pending, so ordinary managed work in a real project remains unavailable until a managed behavior source exists.

## Titles and doctor

Claude accepts a title only in hook output, so [title synchronization](workstreams-titles.md#claude-code-delivery) stages a change and delivers it on the next `SessionStart` (startup, resume, fork) or `UserPromptSubmit`. A final `DONE` title appears one prompt late.

`doctor --client claude-code --executable <claude>` reports the package, enrollment, state, `claude --version`, whether this plugin is installed and enabled, and `disableAllHooks`/`allowManagedHooksOnly`. Claude exposes no hook-trust or hook-list call, so hook execution stays unverified and a healthy result exits 2, never 0. MDM, registry and cloud managed policy are not file-observable. Package tests do not establish native hook execution, which needs its own `claude` observation.

## Installation and qualification

For local testing, use `claude --plugin-dir /absolute/path/to/vibe-workflow`. Review the package before enabling it. Claude's plugin installation/trust rules apply; it does not use Codex's per-definition hash approval flow. Preserve existing hooks, permissions, model selection and authentication.

Qualify the actual client version and launch environment. A disposable `--init-only` run executes startup hooks without starting a conversation; inspect native debug output for handler count and returned context. Package checks and direct script execution do not establish native loading.

Claude Desktop Code and Cowork require their own observed qualification. Reuse this adapter only where the same protocol and executable environment are established; do not infer those surfaces from a CLI result. Claude Chat is outside scope.

References: [hook contract](https://code.claude.com/docs/en/hooks), [plugin manifest](https://code.claude.com/docs/en/plugins-reference).
