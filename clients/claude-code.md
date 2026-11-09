# Claude Code

Apply the [policy index](../policies/00-index.md) within the recorded checkout scope. Read the [shared startup contract](startup.md) when implementing, diagnosing or preparing enrollment.

## Startup behavior

The [adapter](../runtime/adapters/claude_code.py) identifies the native client as `claude-code`. It accepts `SessionStart` sources `startup`, `resume`, `clear`, `compact` and `fork`. Optional model/permission observations are preserved; additional native fields do not grant scope or permissions.

`.claude-plugin/plugin.json` registers `hooks/claude-code.json`, using `CLAUDE_PLUGIN_ROOT`. Keep `hooks/hooks.json` absent: Claude merges the default file with manifest hooks, which could execute the handler twice. The default `skills/` directory supplies packaged skills.

Normal output adds local client/policy pointers through `hookSpecificOutput.additionalContext`, without an activation announcement. Invalid integration returns diagnostic context and a warning. The shared startup code makes the enrollment decision; installation does not enroll a project.

## Runtime hooks

The package also registers `PreToolUse`, `PostToolUse`, `PostToolUseFailure` and `Stop` for the shared task runtime. They need an enrolled checkout with runtime state; otherwise they stay silent and startup-only behavior applies. Read the [task runtime contract](runtime.md) for task controls and limits.

- **Guard.** `PreToolUse` guards only `Edit`, `Write` and `NotebookEdit`, under the same rules as Codex's patch guard. A write is judged where it lands: when its `file_path` (or `notebook_path`) resolves outside the session `cwd`, scope is decided from the target's directory, so a session elsewhere cannot write into an enrolled checkout unguarded. Shell commands, MCP tools and external edits stay outside it. A subagent's edit uses the `agent_id` from the hook and inherits its parent's task authority.
- **Identity.** `prompt_id` is the turn and `tool_use_id` the call. Without them an event stays uncertain, never continues and still advances freshness.
- **Freshness.** `PostToolUse` and `PostToolUseFailure` advance checkout generation; a failed command may still have changed files.
- **Continuation.** `Stop` may continue once per prompt, shown to Claude as hook feedback. `stop_hook_active` must be an explicit `false`.
- **Input bound.** Runtime events read up to 8 MiB because `Write` carries a whole file; larger input reports a diagnostic and is not guarded, so a write that large is unprotected.
- **Failure.** A crashed or timed-out hook lets the action proceed, as on Codex. `onFailure: "block"` is deliberately unused: the hooks are plugin-wide and would block ordinary editing in unenrolled projects.

Claude Code has no interrupt hook, so a user interrupt cannot pause a task; `Stop` does not run then. `task start`, `update`, `status`, `title` and the `evidence` controls (plan, check, reconcile-check, verify, cleanup, status) accept `client: "claude-code"`; a task can reach `DONE` only through evidence verification, which is rechecked against the actual candidate on every status read. These controls are verified on scopes without a capability projection (test fixtures). In a real scope qualified by the Claude setup source, only `startup-context` is verified, so `task start`, patch guarding, checks and completion are refused until pre-tool, post-tool, stop and review-readiness behavior are verified or an exact qualification attempt is admitted. Native review uses the [review route](#native-review-through-the-delegation-ledger). Native workers, linked evidence plans, delivery and retirement stay Codex-only: a plan that requires review cannot complete until Claude subagent observation exists.

## Qualification

A managed Claude scope needs a retained setup receipt, as on Codex; a coordinator-supplied observation is never enough. Claude enrollments that were activated before this change from such an observation are no longer qualified; re-run the Claude setup source in a new approved operation. Startup never read Claude qualification before, so no deployed Claude scope relied on the old path. The [Claude setup source](../skills/onboard-my-project/references/qualification.md#claude-code-setup-source) verifies startup context from the client's own hook event stream. The resulting scope is qualification-only: pre-tool, post-tool and stop behavior stay pending, so ordinary managed work in a real project remains unavailable until a managed behavior source exists.

## Subagent observation

Claude has no thread API and documents its transcript format as internal, so subagent provenance is recorded from hooks. `PostToolUse(Agent)` carries the request (prompt, `subagent_type`, requested `model`) and the spawn result (`agentId`, `resolvedModel`); `SubagentStart` and `SubagentStop` carry the agent identity and final message. [`claude_subagents`](../runtime/adapters/claude_subagents.py) keeps a private, bounded, immutable-per-field spool and verifies the chain `tool_use_id → agentId → agent_id` bound by `prompt_id`. The assignment marker must be the first line of the spawn prompt. A missing, partial, conflicting or inconsistent chain is never evidence, because hooks fail open. A hook waits up to 2 s for the spool lock; on timeout the recorder returns false, the hook prints nothing and the event is dropped, which leaves its chain partial and so no evidence. Interrupted writes can leave `*.tmp` files, and nothing prunes them. The spool is private to the user but writable by any process of that user, including a hostile subagent with a shell, so it is integrity-checked workflow state, not a security boundary; it is not pruned.

The collectors return the receipt and report that `collect_native_review` returns for Codex, with mechanism `claude-code-subagent-hook`. Claude has no turn ids (the agent id stands in) and no effort control observed. A reviewer must echo the assignment digests exactly; the digest match is the binding and is never relaxed. The D6 delegation ledger consumes these collectors for review through the route below.

## Native review through the delegation ledger

A Claude review uses the same single D5 review path as Codex, planned and settled in the D6 ledger, under the route mechanism `claude-code-subagent-hook` on the surface `claude-code-cli`. Run `delegation guidance|plan|prepare|observe|reconcile|status` and `evidence review prepare|collect|assess` with `client: "claude-code"`. The coordinator spawns the reviewer once, with the returned task name as the first line of the `Agent` prompt and the returned `requested_model`.

- **Controls.** Only `spawn` and `explicit_model` exist. A plan that requires `explicit_effort` or `fork_turns_none` is refused, and effort is recorded as `default`, never observed.
- **Catalog.** The model aliases `haiku`, `sonnet` and `opus` are the `Agent` tool's documented values; Claude exposes no catalog call, so the list is documentation, not observation. The native version comes from `claude --version`.
- **Guidance.** Record guidance for the surface `claude-code-cli` with the same claim and record procedure, from Anthropic's published model guidance, keyed by client version and the capability fingerprint.
- **Observation.** Only the hook-recorded spawn chain: the assignment marker, the requested model alias, the resolved model and the final message. There is no pre-spawn gate, so an unprepared spawn is not blocked, only never counted. A missing, partial, conflicting or misbound chain leaves the review uncertain and is no evidence. Settlement needs the same independent coordinator assessment and host proof as Codex; `SubagentStop` alone proves nothing about descendants.
- **Trust.** The spool is hook-written state that any process of the same user could alter, including a hostile subagent with a shell. Review provenance is therefore cooperative, not tamper-proof: the coordinator's independent assessment and host proof are the control, never the spool alone.
- **Unsupported.** Workers, Workstreams, coordinator jobs, continuations, cooperative completion and retirement.
- **Real scopes.** Admission needs the `native-review` capability, which the stage-1 receipt does not verify, so a real qualified scope refuses review preparation until Claude's managed behavior source exists.

## Titles and doctor

Claude accepts a title only in hook output, so [title synchronization](workstreams-titles.md#claude-code-delivery) stages a change and delivers it on the next `SessionStart` (startup, resume, fork) or `UserPromptSubmit`. A final `DONE` title appears one prompt late.

`doctor --client claude-code --executable <claude>` reports the package, enrollment, state, `claude --version`, whether this plugin is installed and enabled, and `disableAllHooks`/`allowManagedHooksOnly`. Claude exposes no hook-trust or hook-list call, so hook execution stays unverified and a healthy result exits 2, never 0. MDM, registry and cloud managed policy are not file-observable. Package tests do not establish native hook execution, which needs its own `claude` observation.

## Installation and qualification

For local testing, use `claude --plugin-dir /absolute/path/to/vibe-workflow`. Review the package before enabling it. Claude's plugin installation/trust rules apply; it does not use Codex's per-definition hash approval flow. Preserve existing hooks, permissions, model selection and authentication.

Qualify the actual client version and launch environment. A disposable `--init-only` run executes startup hooks without starting a conversation; inspect native debug output for handler count and returned context. Package checks and direct script execution do not establish native loading.

Claude Desktop Code and Cowork require their own observed qualification. Reuse this adapter only where the same protocol and executable environment are established; do not infer those surfaces from a CLI result. Claude Chat is outside scope.

References: [hook contract](https://code.claude.com/docs/en/hooks), [plugin manifest](https://code.claude.com/docs/en/plugins-reference).
