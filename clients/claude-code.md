# Claude Code

Apply the [policy index](../policies/00-index.md) within the recorded checkout scope. Read the [shared startup contract](startup.md) when implementing, diagnosing or preparing enrollment.

## Startup behavior

The [adapter](../runtime/adapters/claude_code.py) identifies the native client as `claude-code`. It accepts `SessionStart` sources `startup`, `resume`, `clear`, `compact` and `fork`. Optional model/permission observations are preserved; additional native fields do not grant scope or permissions.

`.claude-plugin/plugin.json` registers `hooks/claude-code.json`, using `CLAUDE_PLUGIN_ROOT`. Keep `hooks/hooks.json` absent: Claude merges the default file with manifest hooks, which could execute the handler twice. The default `skills/` directory supplies packaged skills.

Normal output adds local client/policy pointers through `hookSpecificOutput.additionalContext`, without an activation announcement. Invalid integration returns diagnostic context and a warning. The shared startup code makes the enrollment decision; installation does not enroll a project.

## Installation and qualification

For local testing, use `claude --plugin-dir /absolute/path/to/vibe-workflow`. Review the package before enabling it. Claude's plugin installation/trust rules apply; it does not use Codex's per-definition hash approval flow. Preserve existing hooks, permissions, model selection and authentication.

Qualify the actual client version and launch environment. A disposable `--init-only` run executes startup hooks without starting a conversation; inspect native debug output for handler count and returned context. Package checks and direct script execution do not establish native loading.

Claude Desktop Code and Cowork require their own observed qualification. Reuse this adapter only where the same protocol and executable environment are established; do not infer those surfaces from a CLI result. Claude Chat is outside scope.

References: [hook contract](https://code.claude.com/docs/en/hooks), [plugin manifest](https://code.claude.com/docs/en/plugins-reference).

## Native routing bridge

Load in a [managed project](../policies/03-agent-delegation.md#managed-projects) before launching a native worker or reviewer. Routing chooses the model and effort; the coordinator launches exactly that, once.

1. From the package root, prepare through the Bash tool with one JSON request:

   ```sh
   printf '%s' '{"v":1,"client":"claude-code","assignment":{"attempt_id":"<new id>","task":"<task id>","scope":"<assignment scope>"},"job":<job>}' \
     | python3 -I -B scripts/routing.py native_prepare --project /absolute/project/path
   ```

   `job` is the planned job with its task `profile`; add `"profile"` only for a coordinator-supplied profile. Never add a catalog, quota, window or observation field: the request is refused (`invalid_request`), and the catalog comes from the client's own stored snapshot.
2. Only `"state":"dispatched"` is a permit. Invoke the native Agent once with exactly its `model` alias and, when `effort` is not null, that effort; with `effort` null set none. Do not change coordinator or global defaults to make a model fit. Any other answer launches nothing: report its `state` and `reason`. `already_prepared` and `already_dispatched` mean an earlier call carried the permit; never respawn.
3. After the Agent returns, settle once:

   ```sh
   printf '%s' '{"v":1,"attempt_id":"<id>","outcome":{"kind":"complete"}}' \
     | python3 -I -B scripts/routing.py native_complete --project /absolute/project/path
   ```

   `kind` is `complete`, `not_started` (the Agent never started) or `unknown` (you cannot tell whether it ran). After a permit, routing cannot verify that a launch never started, so `not_started` and `unknown` are both recorded `uncertain` (`disposition: uncertain`): the capacity stays held until the operator's `reconcile_subscription`, and the attempt never gets another permit. Add `model` or `effort` only when the result shows what was served; the answer's `served` then reports `substituted` or `effort_changed` instead of relabelling the launch. A repeated report is a `duplicate` and changes nothing; only an attempt the bridge prepared is settled here (`not_a_native_attempt` otherwise).

   A native result reports no usage reading, so `complete` marks the pool's current readings as used: no alias of that pool (native or OpenCode) is admitted again until a reading observed after the completion arrives. If that mark cannot be written, nothing is recorded and the capacity stays held; report it and settle again.

The bridge is advisory: nothing blocks a send, so the permit is the contract. It needs, and today refuses without, three sources that are not yet qualified: a verified binding of this client's version, connection and invocation controls (`native_binding_unverified`), a stored catalog snapshot bound to it (`native_catalog_unavailable`, `native_binding_changed`), and a current subscription-only billing fact (the route stays out as `billing_unverified`). No process starts on a prepare. A checkout whose enrolment keeps runtime task state for the client uses its own delegation path (`managed_delegation_required`). An ordinary Claude Code launch needs routing state only, not Codex's task database.
