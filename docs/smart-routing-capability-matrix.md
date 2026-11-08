# Smart Routing — capability matrix and billing classification (Task 0.3, P0.0)

Status on 2026-11-08. This is evidence for the [implementation plan](superpowers/plans/2026-11-08-smart-routing.md); it closes no gate by itself. A missing proof is **unverified**, never qualified. Probe data is in [tests/fixtures/smart-routing/probes/](../tests/fixtures/smart-routing/probes/) with prompts, tokens and account identifiers removed.

Source confidence: **D** vendor documentation, **N** news or third-party only, **U** unverified. Anthropic help pages show "Updated over a week ago" without a date and OpenAI pages are undated, so every documented row needs a recheck before a route is enabled for the first time.

## What this machine has (observed)

OpenCode 2.2.6, Codex 0.167.0, Claude Code 2.1.311. `opencode auth list` shows four stored credentials, names and types only: Claude Agent, OpenAI (api), OpenAI, and the OpenCode Console workspace "Personal". No secret was read.

## Route classification

| Route | Class | Conf. | Evidence and caveat |
|---|---|---|---|
| OpenCode "Claude Agent" provider on the user's Claude plan (**reference route**) | `included_subscription`, **disputed** | D, conflicting | Observed working: OpenCode ran Claude with no API key and cost 0 ([probe](../tests/fixtures/smart-routing/probes/opencode_claude_agent_run.json)). Anthropic's support text says plan-signed Agent SDK and `claude -p` usage "still draw from your subscription limits"; its Agent SDK overview says third-party developers may not offer claude.ai login or rate limits "unless previously approved". See the contradictions below. |
| Claude Code (`claude -p`) on the Claude plan | `included_subscription` | D and observed | Five-hour and weekly windows reported; extra usage rejected at the organization level ([probe](../tests/fixtures/smart-routing/probes/claude_code_subscription.json)). |
| Claude Code after usage credits are turned on | `purchased_usage` | D | Requests switch to pay-as-you-go on credits. Excluded from automatic routing. |
| Other third-party Claude tools using the plan login directly | `purchased_usage` or `ineligible` | N | An April 2026 announcement said subscriptions no longer cover third-party tools; a later user report (unverified) shows such a tool moved to extra usage. |
| Claude API key from the linked organization (`claude -p`, Agent SDK, Messages API) | `api_grant`, then `api_prepaid` | D | Monthly credit by plan (Max, Ultra, Team), pooled, expiring each cycle, used before purchased credit. After it runs out purchased credit or auto-reload takes over silently. |
| Interactive Claude Code with an API key; Bedrock, Vertex, Foundry | not covered by the grant | D | Paid API usage. |
| Codex signed in with ChatGPT | `included_subscription` | D and observed | Weekly window observed ([probe](../tests/fixtures/smart-routing/probes/codex_rate_limits_exhausted.json)). |
| Codex after buying ChatGPT credits | `purchased_usage` | D | Excluded from automatic routing. |
| OpenCode "Sign in with ChatGPT" | `included_subscription` for Plus and Pro; Business undocumented | D | OpenCode's provider page names Plus and Pro and says usage counts against the plan; OpenAI's own page for this path was not confirmed. Whether it shares Codex's pool is unverified. |
| OpenAI API key from the linked organization (Pro, Business) | `api_grant`, then `api_prepaid` | D | Business annual plans get the year's grant in one issuance expiring at the annual cycle end. Free credits are used before purchased; with none left, HTTP 429 `credit_balance_exhausted`. Plus and Enterprise are not eligible. |
| Codex with an OpenAI API key | not covered by the grant (`api_prepaid`) | D | "Codex usage through API keys is not supported by this benefit." |
| OpenCode Console pay-as-you-go ("Personal") | `gateway` (prepaid) | D | Paid requests fail with HTTP 402 at zero balance. |
| OpenCode Go | `gateway` (separate subscription) | D | Extra Usage can spill into purchased credits when turned on. |

## Contradictions and risks

1. **Anthropic's documents disagree.** Support pages say plan-signed Agent SDK usage draws from subscription limits. The Agent SDK overview says third-party products may not offer claude.ai login or rate limits unless previously approved. The OpenCode provider specification itself asks whether Anthropic approval is needed. Treat the reference route as **tolerated, not guaranteed**.
2. **Policy has moved since April.** The April announcement covered all third-party tools. Anthropic's help text (15 June 2026 update) says the announced changes were paused and third-party app usage still draws from subscription limits. A later user report shows a direct-login tool pushed to extra usage. The working reading, not a documented rule, is that launching the real Claude binary is tolerated and calling the API directly with a plan login is not.
3. **Fair-use fine print** (from Anthropic's legal and compliance page; the quoted wording was not independently re-checked). Limits "assume ordinary, individual usage" and may be enforced without notice. Heavy automated parallel routing on a subscription is a risk the router should respect with its concurrency caps.
4. **OpenCode's cost of 0 is not billing evidence.** It is derived from the login type.
5. **Units differ.** Claude reports a 0–1 utilization fraction in `unifiedWindows`; Codex reports `usedPercent` 0–100. The router must normalize and never compare raw numbers across providers.

## Corrections this evidence requires

- The spec's April sentence was too narrow: that announcement covered all third-party tools. The reference route's harness is **the Claude Agent SDK launched through OpenCode's Claude Agent provider**, not "the Claude Code harness".
- No OpenCode document describes launching the Codex binary. OpenCode reaches OpenAI through Sign in with ChatGPT or an API key.
- API grants apply only to Claude Max, Ultra and Team and to ChatGPT Pro and Business.
- Every key in the linked organization shares the grant, including a key stored in OpenCode Console or OpenRouter. The router cannot rely on key separation.
- When the grant runs out, usage continues on purchased credit or auto-reload if the organization has them, otherwise API requests stop; the router must enforce the allowance cap itself.
- For annual Business plans the grant cycle and expiry are annual.

## OpenCode enforcement (P0.1, P0.5–P0.8)

Probed on 2026-11-08 against OpenCode 2.2.6 with an isolated server (separate config, data and state directories, no credentials copied) and the `claude-agent/haiku` route only. Two rounds sent 26 tiny model requests and blocked 3 before sending. Compact fixtures are in [tests/fixtures/smart-routing/probes/](../tests/fixtures/smart-routing/probes/) (`p0-*` and `p0r2-*`); the large raw event and hook dumps were not committed.

| Question | Status | Evidence |
|---|---|---|
| Plugin loader and runner (P0.1) | qualified | Plugins load from `.opencode/plugins/<dir>/index.{js,ts}` or `plugins` in the config and run in the Bun embedded in the `opencode` binary; no host Bun is needed. Hooks can be unit-tested with a fake context under `node --test`. |
| Hook latency (P0.1) | qualified | Spawning a trivial `python3` takes about 15–20 ms per call (p95 about 23 ms); one persistent child answering JSON lines takes about 0.02–0.3 ms. A real routing command doing SQLite work will cost more. |
| Requested model and variant visible (P0.5) | qualified | Hooks, assistant messages and sessions carry `model{providerID,id,variant}`. |
| Resolved model and applied effort (P0.5) | unsupported | Only the requested alias is stored. Route identity is therefore alias + requested variant + catalog family; the resolved model is recorded as unknown. |
| Block before a model request (P0.6) | qualified | `model.request` and `context` hooks throwing sent nothing (no tokens). A hanging hook has no host timeout, so the shim needs its own. |
| Block a subagent spawn, including a nested one (P0.6) | qualified | `tool.execute.before` sees `tool:"subagent"` with its input before the child exists; a nested spawn was blocked. |
| Block a built-in tool, MCP tool, or a Code Mode nested MCP call (P0.6) | qualified | Throwing in `tool.execute.before` stopped each one (no marker file); nested calls appear under their own names. |
| Deny by policy (P0.6) | qualified | `tool.use` policy rules blocked an MCP tool and `webfetch`; a policy holds even when the plugin did not load, and a plugin cannot loosen it. |
| Block a model or variant switch (P0.6) | partial | No hook runs before the switch; the next request passes through `model.request` carrying the new model. |
| Block a shell command by a plugin hook (P0.6) | qualified | Throwing in `shell.create.before` stopped the command; the hook can also delete environment keys. It carries no session ID. |
| Commands run inside the shell (`opencode run`, `claude -p`, `curl` to a provider) (P0.6) | unsupported | Can only be matched on command text, which is best-effort. |
| Code Mode `fetch` (P0.6) | bypasses the tool hook | It reaches only `permission.evaluate` (`action:"webfetch"`), so deny `webfetch` by policy. |
| Plugin fails to load (P0.6) | unsupported (fail-open) | The server logs the failure and continues; no setting makes a plugin required. |
| Health check a launcher can call (P0.6) | qualified | A plugin RPC `routing.health` answered `ok` in about 10 ms and returned HTTP 400 `rpc.unavailable` when the plugin failed to load. It is tied to one directory: a symlinked path loaded a separate instance. |
| Duplicate session or message IDs (P0.7) | qualified | The same session ID created twice or ten times at once gives one session; the same message ID sent five times at once gives one message and one model request, though the `prompt` hook ran five times. |
| Lost acknowledgment (P0.7) | qualified | A dropped client left no message; after admission the message can be read back by ID, so retrying with the same ID is safe. |
| Lineage (P0.8) | qualified | Child sessions carry `parentID` and `agent`. |
| Auxiliary requests (P0.8) | partial | `model.request` reports `kind` (`primary`, `title`, `compaction`, `generate`). Title requests create no message, so their tokens appear only in the session total. |
| Retries (P0.8) | unsupported | The `retry` hook never fired; Claude retries internally and no attempt field exists. |
| Event stream (P0.8) | partial | Live-only, no replay or reconnection. Durable events carry `durable.seq`; a gap was seen once without a disconnect. Re-reading session and message state resyncs. |
| Subagent environment (P0.8) | unsupported | `OPENCODE_SESSION_ID` is absent in a child's shell, and where it appeared it held the ID of the session that launched the server. Take session IDs from hook context instead. |
| Persistent child in a plugin (P0.1/P0.6) | qualified | After a kill the next hook respawned the child in about 16–18 ms; with respawn disabled the hook threw and nothing was sent; a stalled child timed out at about 500 ms and the hook threw. |

### Hooks S3 uses

- `ctx.session.hook("model.request", …)` for every model request, including title, compaction and generate.
- `ctx.tool.hook("execute.before", …)` for built-in, MCP and Code Mode nested tool calls, including subagent spawns (it can check `input.model`).
- `ctx.permission.hook("evaluate", …)` for `webfetch` inside Code Mode.
- `ctx.shell.hook("create.before", …)` to strip credentials from the command environment.
- A plugin RPC `routing.health` that the launcher calls before every managed attempt.

### What stays unenforceable, and the controls

- Plugin load failure for sessions the launcher does not control: the user chose to protect managed launches only. A managed launch checks `routing.health` for its canonical directory and refuses to send when it fails.
- Arbitrary commands in the shell: **worker profiles that keep full shell access are advisory-only** and get no funding guarantee. Enforced mode applies only to profiles that deny `shell`, MCP, Code Mode `execute` and `webfetch` by policy, or allow a narrow list.
- Claude-internal retries and the resolved model: recorded as unknown.
- Any directory the plugin did not load for (including a symlink variant of the same path).

### Decisions taken (2026-11-08)

1. Only managed launches are protected from plugin failure; interactive sessions stay unguarded.
2. Workers keep full shell; those profiles are advisory-only.
3. The plugin is installed globally from the start and does nothing outside the bounded canary project until cutover (Task C.4).
4. Route identity is alias + requested variant + catalog family; the resolved model is unknown.

## Gate status (P0)

| Gate | Status | Basis |
|---|---|---|
| P0.0 Billing/terms | **partly established** | Route table above. The reference route stays disputed; ChatGPT-via-OpenCode and API grant debit order need live checks. |
| P0.1 Source/runtime | **qualified** for the plugin loader, test approach and latency; the workflow baseline was recorded in Task 0.1 | OpenCode enforcement section. |
| P0.2 Subscription telemetry | **partly established** | Claude Code reports `unifiedWindows` utilization (0–1) with reset times; Codex `account/rateLimits/read` reports `usedPercent` with window length and reset; OpenCode assistant messages carry `native.rateLimit` for Claude Agent. Account identity between a harness and the OpenCode route is unverified. |
| P0.3, P0.4 API grants and key binding | unverified | Not probed. |
| P0.5 Model/effort | **partly established** | Requested model and variant observed; resolved model unsupported. |
| P0.6 Mandatory routing | **partly established** | Qualified for model requests, subagent and nested spawns, and tool calls; unsupported for commands inside the shell and plugin load failure. |
| P0.7 Dispatch recovery | **qualified** | Server deduplicates IDs; lost acknowledgments are recoverable by ID. |
| P0.8 Lineage/accounting | **partly established** | Parent IDs and request kinds visible; title tokens by subtraction; no retry or resolved-model data; the event stream needs resync. |

## Live checks still open

1. **OpenCode with ChatGPT** needs quota. The user's Codex weekly window is exhausted until about 2026-11-10, so no Codex or ChatGPT calls were made. Check the ChatGPT usage page for an OpenCode entry and its credits setting, and read the Codex rate limits before and after one request.
2. **Reference route billing.** Confirm in claude.ai usage that one request through the Claude Agent provider raised the plan meter and left extra usage unchanged. The `rate_limit_event` fields (`rateLimitType`, `overageStatus`) are the signals.
3. **API grants.** Before any call, confirm purchased balance is zero and auto-reload is off. Make one tiny call with a key from the linked organization and check each vendor's billing page for the grant balance and expiry.
4. **Plan tier.** Grant and sign-in eligibility depend on the exact plans, which should be recorded per account.
