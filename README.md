# Vibe Coding Workflow

<img src="assets/logo.png" alt="Vibe Coding Workflow logo: a gold W on a dark rounded square" width="160" height="160">

[Vibe Coding Toolkit](https://github.com/soumatheusgomes/vibe-coding-toolkit), packaged for your coding agent.

**Early development.** Codex has opt-in task controls, read-only diagnostics and shared canonical worker/check/review ownership. Recorded [D2](docs/d2-validation.md) and [D5](docs/d5-validation.md) qualification covers bounded CLI/bundled app-server behavior, a controlled Interrupt case, Desktop startup orientation and the earlier D5 lifecycle at their stated versions and pins. It does not qualify the composed canonical native lifecycle. General guard/continuation turn correlation, other desktop behavior and Claude lifecycle remain unqualified. See the [client contracts](clients/index.md) for local procedures.

For task controls, read [task runtime](clients/runtime.md); for accepted checks, review and completion, load [evidence](clients/evidence.md); for native delegated work only, load [native delegation](clients/delegation.md). Local readiness covers current task evidence and grants no merge authority. D7's [bounded fixture inspector](clients/repository-gate.md) is implemented but always exits 1; production evidence sources, publishing and repository enforcement remain unqualified.

Optional [OpenRouter proposals](skills/openrouter-proposals/SKILL.md) use an explicitly approved MCP connection for bounded work. Discovery is cached; native agents select candidates using benchmarks and comparable task history, then verify each proposal. OpenRouter manages spending limits. The helper never handles credentials or executes model output. Package checks and observed live-client execution are reported separately.

The separate [OpenRouter worker pilot](skills/openrouter-workers/SKILL.md) runs background OpenCode agents under genuine native Codex supervisors. The first slice permits read-only tools over explicitly copied files, with native verification, observed progress and the existing optional Habblaud role/model display. It requires its own OpenCode installation and OpenRouter login; it does not change the coordinator's provider or enable external repository writes. OpenCode owns transient request retries; the workflow adds no outer retry loop and permits native takeover after confirmed local stop. Credit checks are optional; OpenRouter enforces spending limits.

Explicitly invoke [LLM Council](skills/llm-council/SKILL.md) with `$llm-council <question>` for three independent answers, one anonymous critique round and fresh native chair synthesis. Choose Frontier or Balanced; Free is temporarily unavailable, with no automatic paid substitution. Runtime task-aware selection chooses a group-appropriate native member and two external models at supported maximum reasoning, preserving coordinator settings. Text questions use direct tool-free requests; repository questions use qualified read-only snapshot inspection. Every technically usable answer and critique reaches the chair unchanged; models evaluate claims, assumptions and dissent. Council needs saved consent and reviewed transmission scope, and defaults to 20 minutes including preparation and waits. [Validation](docs/council-validation.md) separates package/synthetic checks from observed client execution. Historical runs remain readable and cancellable with operational settlement; live availability, comparative quality and savings require separate evidence.

Use [Manage OpenRouter Integration](skills/manage-openrouter/SKILL.md) for saved setup, status and enable/disable controls. It starts disabled and currently supports an already enrolled Codex POSIX checkout with accepted private runtime state. Managed executors enforce the selected routes and configured paths. Disable blocks new admissions; launched work can finish and receive native verification. Saved enablement does not verify a connection, client execution or model fitness, and disabling does not revoke direct account access.

Use [Workstreams](skills/workstreams/SKILL.md) for suitable coordinated contexts,
saved mode controls, resource admission, recovery and managed titles. New supported
Codex runtime scopes start enabled; existing preferences remain. Managed titles
require explicit adoption. See the [main-runtime boundary](docs/workstreams-main-port.md).

## Prepare your Workspace

Open this checkout in a coding agent with terminal access to the destination machine:

```text
Read skills/prepare-my-workspace/SKILL.md and use it to prepare this machine.
```

For local plugin testing, run `codex plugin marketplace add .` from this checkout, then restart Codex desktop. Select `vibe-workflow-local`, install `vibe-workflow`, and invoke `prepare-my-workspace`, which uses the same instructions. Refresh the installed copy after changes. See [OpenAI's marketplace guide](https://developers.openai.com/plugins/build/plugins#add-a-marketplace-from-the-cli).

For Claude Code local testing, launch `claude --plugin-dir .` from this checkout. See the [Claude Code contract](clients/claude-code.md) for loading and qualification.

## Enroll a project

For an explicitly selected existing project, use [onboard-my-project](skills/onboard-my-project/SKILL.md) to inspect and prepare a private enrollment plan. Planning does not change the target. After trusted approval binds the exact plan and application manifest, `onboard apply` makes recoverable changes and activates enrollment only after required adoption evidence qualifies. `onboard resume` reconciles interrupted work; `onboard rollback` restores transaction-owned changes. Each command uses `--plan-dir` and a bounded JSON request on stdin. See [application and recovery](skills/onboard-my-project/references/apply-and-recovery.md). Package validation and adoption evidence do not qualify client execution.

## Review a project

Use [review-my-project](skills/review-my-project/SKILL.md) to review selected repositories and areas with safe local evidence. It does not enroll repositories. Until managed target-owner reconciliation is available, limited findings are returned in chat and detailed reports are not saved.

## Observability

Use the separate [Manage Observability Integration](skills/manage-observability/SKILL.md) skill for optional integration setup, credentials, status, scope or enable/disable requests. Its name is provider-neutral; Langfuse is the first connection option. Existing accepted Codex POSIX checkouts support private connection settings, explicit OS storage or environment credentials, current-project verification and an authorized fixed metrics-only synthetic probe. Recommend a dedicated Langfuse project per logical Vibe Project; explicitly confirm sharing before another distinct Project links to a known actively used destination. Same-Project repositories/worktrees normally share one destination. Current checkout controls do not automate canonical cross-Project discovery. Saving/replacing or removing shared credentials follows the provider's [shared impact rule](skills/manage-observability/references/langfuse.md#shared-credential-impact). Desired opt-in starts no native collector; Project-wide activation, workflow measurements, real OS/account qualification and Windows runtime support remain separate.

Project onboarding does not configure or activate observability or OpenRouter delegation. After successful qualified onboarding, it may offer either separate management skill as an optional follow-up.

[Development guide](AGENTS.md)
