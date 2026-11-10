# Smart Routing — capability matrix and billing classification (Task 0.3, P0.0)

Status on 2026-11-08; the "Session ID in the environment" row was updated on 2026-11-09, and the two pool facts the user confirmed and the [route manifest](#route-manifest-user-decision-5) section were added on 2026-11-10. This is evidence for the [implementation plan](superpowers/plans/2026-11-08-smart-routing.md); it closes no gate by itself. A missing proof is **unverified**, never qualified. Probe data is in [tests/fixtures/smart-routing/probes/](../tests/fixtures/smart-routing/probes/) with prompts, tokens and account identifiers removed.

Source confidence: **D** vendor documentation, **N** news or third-party only, **U** unverified. Anthropic help pages show "Updated over a week ago" without a date and OpenAI pages are undated, so every documented row needs a recheck before a route is enabled for the first time.

## What this machine has (observed)

OpenCode 2.2.6, Codex 0.167.0, Claude Code 2.1.311. `opencode auth list` shows four stored credentials, names and types only: Claude Agent, OpenAI (api), OpenAI, and the OpenCode Console workspace "Personal". No secret was read.

## Route classification

| Route | Class | Conf. | Evidence and caveat |
|---|---|---|---|
| OpenCode "Claude Agent" provider on the user's Claude plan (**reference route**) | `included_subscription`, **disputed** | D, conflicting | Observed working: OpenCode ran Claude with no API key and cost 0 ([probe](../tests/fixtures/smart-routing/probes/opencode_claude_agent_run.json)). Anthropic's support text says plan-signed Agent SDK and `claude -p` usage "still draw from your subscription limits"; its Agent SDK overview says third-party developers may not offer claude.ai login or rate limits "unless previously approved". See the contradictions below. **Pool (user confirmation, 10 November 2026; observed by the user, not by the agent):** it draws on the same Claude subscription usage pool as Claude Code. Consistent with this, the committed `native.rateLimit` readings on this route and the `claude -p` probe report the same `five_hour` reset time (1794178200). The confirmation covers the pool only; the terms dispute stands. |
| Claude Code (`claude -p`) on the Claude plan | `included_subscription` | D and observed | Five-hour and weekly windows reported; extra usage rejected at the organization level ([probe](../tests/fixtures/smart-routing/probes/claude_code_subscription.json)). |
| Claude Code after usage credits are turned on | `purchased_usage` | D | Requests switch to pay-as-you-go on credits. Excluded from automatic routing. |
| Other third-party Claude tools using the plan login directly | `purchased_usage` or `ineligible` | N | An April 2026 announcement said subscriptions no longer cover third-party tools; a later user report (unverified) shows such a tool moved to extra usage. |
| Claude API key from the linked organization (`claude -p`, Agent SDK, Messages API) | `api_grant`, then `api_prepaid` | D | Monthly credit by plan (Max, Ultra, Team), pooled, expiring each cycle, used before purchased credit. After it runs out purchased credit or auto-reload takes over silently. |
| Interactive Claude Code with an API key; Bedrock, Vertex, Foundry | not covered by the grant | D | Paid API usage. |
| Codex signed in with ChatGPT | `included_subscription` | D and observed | Weekly window observed ([probe](../tests/fixtures/smart-routing/probes/codex_rate_limits_exhausted.json)). |
| Codex after buying ChatGPT credits | `purchased_usage` | D | Excluded from automatic routing. |
| OpenCode "Sign in with ChatGPT" | `included_subscription` for Plus and Pro; Business undocumented | D | OpenCode's provider page names Plus and Pro and says usage counts against the plan; OpenAI's own page for this path was not confirmed. Observed live for `gpt-6-luna` on 10 November 2026 (no cost, window settlement); `gpt-5.5` was refused (400 `model_not_found`). **Pool (user confirmation, 10 November 2026; observed by the user, not by the agent):** it shares Codex's usage pool. The agent read no Codex window around a request, and the ChatGPT usage page entry and credits setting were not checked. |
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

Probed on 2026-11-08 against OpenCode 2.2.6 with an isolated server (separate config, data and state directories, no credentials copied) and the `claude-agent/haiku` route only. Two rounds sent 26 tiny model requests and blocked 3 before sending. Compact fixtures are in [tests/fixtures/smart-routing/probes/](../tests/fixtures/smart-routing/probes/) (`p0-*` and `p0r2-*`); the large raw event and hook dumps were not committed. Rows marked † rest on observations whose raw data is not in the repository. Most rows come from a single run on one server (n=1) on OpenCode 2.2.6; treat them as observed for that version, not as guarantees, and re-run the probes before relying on a row after an upgrade.

| Question | Status | Evidence |
|---|---|---|
| Plugin loader and runner (P0.1) † | observed | Plugins load from `.opencode/plugins/<dir>/index.{js,ts}` or `plugins` in the config and run in the Bun embedded in the `opencode` binary; no host Bun is needed. Hooks can be unit-tested with a fake context under `node --test`. |
| Hook latency (P0.1) | qualified | Spawning a trivial `python3` takes about 15–20 ms per call (p95 about 23 ms); one persistent child answering JSON lines takes about 0.02–0.3 ms. A real routing command doing SQLite work will cost more. |
| Requested model and variant visible (P0.5) | qualified | Hooks, assistant messages and sessions carry `model{providerID,id,variant}`. |
| Resolved model and applied effort (P0.5) | unsupported | Only the requested alias is stored. Route identity is therefore alias + requested variant + catalog family; the resolved model is recorded as unknown. |
| Block before a model request (P0.6) | qualified | `model.request` and `context` hooks throwing sent nothing (no tokens). A hanging hook was still waiting after 60 s with no host timeout, so the shim needs its own. |
| Block a subagent spawn, including a nested one (P0.6) | qualified | `tool.execute.before` sees `tool:"subagent"` with its input before the child exists; a nested spawn was blocked. |
| Block a built-in tool, MCP tool, or a Code Mode nested MCP call (P0.6) | qualified | Throwing in `tool.execute.before` stopped each one (no marker file); nested calls appear under their own names. |
| Deny by policy (P0.6) | qualified | `tool.use` policy rules blocked an MCP tool and `webfetch`, and a plugin cannot loosen a policy. That a policy still holds when the plugin did not load is documented but † not separately probed. |
| Block a model or variant switch (P0.6) † | partial | No hook was found to run before the switch; the next request passes through `model.request` carrying the new model. |
| Block a shell command by a plugin hook (P0.6) | qualified | Throwing in `shell.create.before` stopped the command; the hook can also delete environment keys. It carries no session ID. |
| Commands run inside the shell (`opencode run`, `claude -p`, `curl` to a provider) (P0.6) | unsupported | Can only be matched on command text, which is best-effort. |
| Code Mode `fetch` (P0.6) | bypasses the tool hook | It reaches only `permission.evaluate` (`action:"webfetch"`), so deny `webfetch` by policy. |
| Plugin fails to load (P0.6) | unsupported (fail-open) | The server logs the failure and continues; no setting makes a plugin required. |
| Health check a launcher can call (P0.6) | qualified | A plugin RPC `routing.health` answered `ok` in about 10 ms and returned HTTP 400 `rpc.unavailable` when the plugin failed to load. It is tied to one directory: a symlinked path loaded a separate instance. |
| Duplicate session or message IDs (P0.7) | qualified for these cases | The same session ID created twice or ten times at once gives one session; the same message ID sent five times at once gives one message and one model request, though the `prompt` hook ran five times. |
| Lost acknowledgment (P0.7) | qualified for these cases | A dropped client left no message; after admission the message can be read back by ID. That retrying with the same ID is safe is inferred from the duplicate-send result. |
| Lineage (P0.8) | qualified | Child sessions carry `parentID` and `agent`. |
| Auxiliary requests (P0.8) | partial | `model.request` reports `kind`; only `title` is in the committed fixtures, and `primary`, `compaction` and `generate` are † from the documentation and the raw dumps. Title requests create no message, so their tokens appear only in the session total. |
| Retries (P0.8) † | unsupported | No retry was provoked, so the `retry` hook's silence proves nothing by itself; OpenCode's provider documentation says Claude retries internally and no attempt field was found. |
| Event stream (P0.8) † | partial | Live-only, no replay or reconnection. Durable events carry `durable.seq`; a gap was seen once without a disconnect. Re-reading session and message state resyncs. |
| Session ID in the environment (P0.8) | unsupported for routing | Not dependable on its own. The probe records conflict: `p0r2-results.json` notes that on a server started with `OPENCODE_*` removed no shell had `OPENCODE_SESSION_ID`, while `p0r2-mcp-codemode-messages.json` shows a subagent's shell printing its own session ID; in another run it held the launching session's ID, inherited. On the Desktop's `serve --service` server (9 November 2026), whose own environment lacks it, the shell tool set it per session: a parent shell and a subagent's shell each saw their own ID. Routing takes session IDs from hook context or a recorded handoff. Ordinary titles use it only after the [`--proof` check](../clients/workstreams-titles.md#opencode-sessions) binds it to the running command. |
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

## Live re-qualification (9 November 2026)

Two staged runs on one isolated OpenCode 2.2.6 server, using `claude-agent/haiku#low` only:

- **Run 1** stopped in stage 3 on an accounting blocker, which #146 fixed.
- **The re-run** (harness 3f87159, 4 calls) completed stages 0–4 with no blocker.

In total 9 of the 14 authorized calls were used. The [report](smart-routing-qualification.md) has the tables; [`qual-rerun-results.json`](../tests/fixtures/smart-routing/probes/qual-rerun-results.json) has the re-run evidence. "Observed" below means seen in these runs (n=1 each); "unobserved" means it did not run.

| Gate | Observed | Unobserved |
|---|---|---|
| P0.6 | **Handshake:** `prepare` → `routing.health` `ok` → `dispatch` → `attach` (a wrong token refused) → managed request allowed → `detach` (a wrong token refused). The hook order `qual-pre` → shim → `qual-post` was proven by probes. **Blocked at 0 tokens:** unattached and wrong-token sessions (`attach_required`); a finished attempt's session (with the reason `attach_required` instead of `attempt_closed` while its 5-minute pending entry lasted); and a `subagent: true` command child on another model (`route_mismatch`). Command subagents never reach `tool.execute.before`, so only lineage protects them. **Also:** `webfetch` evaluation denied; credential-like environment names stripped from shells while attached; an interactive session passed untouched with the routing child killed. | Real `webfetch` execution; subagent and nested spawns through the tool; MCP and Code Mode calls; a failed plugin load. |
| P0.7 | The server accepts and returns derived session and message ids. One prompt with a derived message id: exactly one model request. The same prompt again: no request, the original id returned, plus an extra `idle` record. Read back by derived id. OpenCode's own ids are 26-character mixed-case bodies, never `ses_` + 32 lowercase hex. Messages list in insertion order. A repeated `dispatch`: `already_dispatched` with no token. **Lost acknowledgment:** a launcher killed after `dispatch` left the hold dispatched and counted with the derived message absent (HTTP 404); `record uncertain` kept the exposure, and checks and `complete` stayed refused. No operator path exists for an uncertain subscription hold (`reconcile_uncertain` → `hold_state_forbidden`). | Racing launchers against the host; server restart. |
| P0.8 | **Accounting** under #146 of a managed attempt with a command-spawned child: `children` by `parentID`, `lineage` with 0 unaccounted, `synthetic` messages counted as their own type. **One real title** known by subtraction. **Usage:** subscription usage with every cost null. **Events:** `track_events` saw no sequence gap over 81 live session events, but flagged `version_changed` on every aggregate (meaning of `durable.version` unverified); `reconcile_with_state` gave zero deltas. **Settlement:** `record complete` settled the subscription hold as a window settlement (claims released, no dollar charge), and `status` was clean afterwards. | `generate`, compaction and retry requests; subagent tool-call children; API-funded holds; coordinator, review and repair stages. |

RPC refusals reach the caller as HTTP 500 `rpc.internal` without the shim's reason code.

Pilot run 1 (9 November 2026, dedicated canary state): the hook order and the guard proof were observed again. A managed request on the canary ledger was allowed; 2 calls were sent (the request and its title). The pilot then aborted on a harness `TypeError` in its accounting step, so no settlement was observed. See the [validation results](smart-routing-validation.md#pilot-canary-run-1-9-november-2026-aborted-at-admission-1).

Pilot attempt 2 (same day, harness `94f9e0e`, dedicated canary state):

- **P0.6 and P0.7:** 20 managed admissions ran end to end, each with derived ids and an attached token-checked request (one primary request each, plus one title in admission 1). The plugin-chain proof held.
- **P0.8:** usage was accounted with `children` and `lineage`, the title was known by subtraction (admission 1) or zero (titled sessions), and 0 re-reads were needed. Every subscription hold settled as a window settlement with no charge, and `status` was clean at the end.
- **Not covered:** subagents, compaction, retries and API-funded holds were not exercised. See the [validation results](smart-routing-validation.md#pilot-canary-attempt-2-9-november-2026-20-of-20-admissions-no-abort).

Live probes (9 November 2026, harness 85ae5f4, `qualification-2`, 7 calls on `claude-agent/haiku#low`; [run](smart-routing-qualification.md#live-probe-run-descendants-and-s33d-harness-85ae5f4)):

- **P0.8 (S3.3c):** `routing.descendants` answered `complete: true` for a root with a tool subagent, a command child and an API child, and equalled the launcher's listing. A root created before `attach` answered `complete: false` with `root_creation_unobserved`. The answer was refused after `detach`. OpenCode emitted the root's `session.created` after `attach`.
- **P0.6 (S3.3d):**
  - model writes to `.opencode/command/x.md` and `sub/opencode.json` were denied, with relative resources;
  - an ordinary `agents/ok.txt` write was allowed;
  - a tool-call subagent spawn on the allowed route passed `tool.execute.before`;
  - **a `` !`env` `` command shell under an attached root did not reach `shell.create.before` and saw a dummy credential-like variable**, so commands inside the shell stay unsupported and launchers must not invoke project commands in managed sessions;
  - image generation was unobserved (no image model).

Open P0.8 item: the live event stream flagged `version_changed` on every session aggregate. The resync gave zero deltas, so this errs on the safe side, but whether `durable.version` is a version per aggregate or per event type is not established.

## Gate status (P0)

| Gate | Status | Basis |
|---|---|---|
| P0.0 Billing/terms | **partly established** | Route table above. The reference route stays disputed. ChatGPT-via-OpenCode was observed live on 10 November 2026 for `gpt-6-luna` on the user's plan: subscription usage with no cost (null) and a window settlement. The user confirmed on 10 November 2026 (observed by the user, not by the agent) that ChatGPT through OpenCode shares Codex's pool and Claude through OpenCode shares Claude Code's pool. The ChatGPT usage page entry, the reference route's terms dispute and the API grant debit order still need a live check. |
| P0.1 Source/runtime | **partly established** | Latency is in the committed fixtures; the loader and test-runner findings † are observed, not committed. The workflow baseline was recorded in Task 0.1. |
| P0.2 Subscription telemetry | **partly established** | Claude Code reports `unifiedWindows` utilization (0–1) with reset times; Codex `account/rateLimits/read` reports `usedPercent` with window length and reset; OpenCode assistant messages carry `native.rateLimit` for Claude Agent. Account identity between a harness and the OpenCode route is unverified. |
| P0.3, P0.4 API grants and key binding | unverified | Not probed. |
| P0.5 Model/effort | **partly established** | Requested model and variant observed; resolved model unsupported. |
| P0.6 Mandatory routing | **partly established** | Qualified for model requests, subagent and nested spawns, and tool calls; unsupported for commands inside the shell and plugin load failure (live 9 November 2026: a command template's `` !`env` `` shell bypasses `shell.create.before`, so its environment is not stripped). The finished shim was observed live for the handshake, the blocked paths and the command-subagent path [above](#live-re-qualification-9-november-2026); command subagents are protected only by lineage. |
| P0.7 Dispatch recovery | **qualified for the cases probed** | One run on one server; server restart was not tested. Duplicate IDs are deduplicated and lost acknowledgments are recoverable by ID. Re-qualified live on 9 November 2026 for derived ids, one request per derived message id, duplicates, read-back, id shapes, ordering and the lost-acknowledgment drill. Open: an uncertain subscription hold has no operator recovery path. |
| P0.8 Lineage/accounting | **partly established** | Parent IDs and request kinds visible; title tokens by subtraction; no retry or resolved-model data; the event stream needs resync. Re-qualified live on 9 November 2026 for a worker attempt with a command-spawned child, its title, event tracking, resync and subscription settlement (#146); auxiliary kinds other than the title, retries and API-funded holds were not observed. |

## Route manifest (user decision 5)

On 10 November 2026 the user accepted this matrix as the source of `runtime/routing_routes.json`, subscription-window routes only ("Use matrix, subscription only"). Dollar, direct API and gateway routes stay out until a live qualification with real costs. The manifest lists:

| Route | Host | Pool (account) | Windows | Model evidence | Status |
|---|---|---|---|---|---|
| `opencode-claude-agent-haiku-low` | `opencode` (`claude-agent-sdk via opencode`) | `claude-subscription` (`claude-plan`) | 5h (18000 s), 7d (604800 s) | `claude-agent/haiku#low`, managed handshake and window settlement | `unverified`: `demand_unjustified` |
| `opencode-openai-gpt-6-luna-none` | `opencode` (`opencode provider openai`) | `chatgpt-subscription` (`chatgpt-plan`) | 7d (604800 s) | `openai/gpt-6-luna#none` (attempt 7); `gpt-5.5` refused, not listed | `unverified`: `demand_unjustified` |
| `codex-native` (advisory) | `codex_native` | `chatgpt-subscription` (`chatgpt-plan`) | 7d (604800 s) | none committed | `unverified`: `model_unobserved` |

Routes that share a usage pool name the same `pool` and `account`, so the collector reads one set of windows for them and their holds count together. Claude Code itself has no route: no managed launcher host exists for it (`routing_launch.HOSTS`, user decision 2). The ChatGPT pool lists only the weekly window Codex reported (`windowDurationMins` 10080, `secondary` null); no five-hour window was observed for it.

**Why no route is qualified: `demand`.** A route's `demand` is a conservative per-launch estimate in thousandths of the provider's utilization. The only committed per-request readings are Claude Agent `native.rateLimit` values during the 8 November probes, on one five-hour window (reset 1794178200), reported at a resolution of 0.01: 0.11 (the `claude -p` probe), 0.14, 0.15 after two tiny `haiku` requests, 0.16 after one more (`p0-5-model-variant-messages.json`), 0.17 (`p0-8-lineage-aux.json`) and 0.20 (`p0r2-mcp-codemode-messages.json`); the seven-day reading moved from 0.03 to 0.05. These include the user's concurrent use. They bound one tiny probe request (about 26,000 cached input tokens, at most a few hundred output tokens) below 20 thousandths of the five-hour window and below 10 of the seven-day window. They do not bound a worker launch, which makes many larger requests, and no reading exists for the ChatGPT pool (no `native.rateLimit`, no Codex reader). So every production route is `unverified` until a launch-sized measurement, or a value the user sets as policy, justifies its demand.

## Live checks still open

1. **OpenCode with ChatGPT:** partly observed on 10 November 2026 ([report](smart-routing-qualification.md#chatgpt-attempts-5-and-6)). Sign in with ChatGPT through an isolated server works end to end for `gpt-6-luna`: one request, tokens recorded, window settlement, no dollar charge. `gpt-5.5`, listed by OpenCode, was refused with 400 `model_not_found`, so a listed model is not an entitled one. Sharing Codex's pool is user-confirmed (10 November 2026). Still open: the ChatGPT usage page entry and its credits setting, and `native.rateLimit`, which this route does not report (there is no Codex window reader either).
2. **Reference route billing.** Drawing on the Claude Code pool is user-confirmed (10 November 2026). Still open: that extra usage stays unchanged after a request through the Claude Agent provider (`overageStatus` reads `rejected`, `org_level_disabled` in the committed readings), and the terms dispute above.
3. **API grants.** Before any call, confirm purchased balance is zero and auto-reload is off. Make one tiny call with a key from the linked organization and check each vendor's billing page for the grant balance and expiry.
4. **Plan tier.** Grant and sign-in eligibility depend on the exact plans, which should be recorded per account.
