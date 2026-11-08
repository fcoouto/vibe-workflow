# Claude Code

Apply the [policy index](../policies/00-index.md) within the recorded checkout scope. Read the [shared startup contract](startup.md) when implementing, diagnosing or preparing enrollment.

## Startup behavior

The [adapter](../runtime/adapters/claude_code.py) identifies the native client as `claude-code`. It accepts `SessionStart` sources `startup`, `resume`, `clear`, `compact` and `fork`. Optional model/permission observations are preserved; additional native fields do not grant scope or permissions.

`.claude-plugin/plugin.json` registers `hooks/claude-code.json`, using `CLAUDE_PLUGIN_ROOT`. Keep `hooks/hooks.json` absent: Claude merges the default file with manifest hooks, which could execute the handler twice. The default `skills/` directory supplies packaged skills.

Normal output adds local client/policy pointers through `hookSpecificOutput.additionalContext`, without an activation announcement. Invalid integration returns diagnostic context and a warning. The shared startup code makes the enrollment decision; installation does not enroll a project.

## Prepared file guard

The package contains a prepared `PreToolUse` guard for Claude's native `Edit`, `Write` and `NotebookEdit` tools, plus internal canonical per-client task start/update/status state support. These require an accepted `claude-code` scope and are not available through the current production CLI; see the [shared startup contract](startup.md#enrollment-input) for the activation boundary. Claude evidence checks, delegation, retirement, correlated turns and continuation are unsupported. Read the [runtime contract](runtime.md) only when the task also concerns its Codex-only APIs.

## Installation and qualification

For local testing, use `claude --plugin-dir /absolute/path/to/vibe-workflow`. Review the package before enabling it. Claude's plugin installation/trust rules apply; it does not use Codex's per-definition hash approval flow. Preserve existing hooks, permissions, model selection and authentication.

Qualify the actual client version and launch environment. A disposable `--init-only` run executes startup hooks without starting a conversation; inspect native debug output for handler count and returned context. Package checks and direct script execution do not establish native loading.

Claude Desktop Code and Cowork require their own observed qualification. Reuse this adapter only where the same protocol and executable environment are established; do not infer those surfaces from a CLI result. Claude Chat is outside scope.

References: [hook contract](https://code.claude.com/docs/en/hooks), [plugin manifest](https://code.claude.com/docs/en/plugins-reference).
