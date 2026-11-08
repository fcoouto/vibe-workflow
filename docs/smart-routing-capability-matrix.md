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

## Gate status (P0)

| Gate | Status | Basis |
|---|---|---|
| P0.0 Billing/terms | **partly established** | Table above. The reference route stays disputed; ChatGPT-via-OpenCode and API grant debit order need live checks. |
| P0.2 Subscription telemetry | **partly established** | Claude Code reports `unifiedWindows` utilization (0–1) with reset times; Codex `account/rateLimits/read` reports `usedPercent` with window length and reset. Account identity between a harness and the OpenCode route is unverified, and no rate-limit data appeared in the OpenCode CLI JSON stream. |
| P0.1, P0.3–P0.8 | unverified | Not probed in this task. |

## Live checks still open

1. **OpenCode with ChatGPT** needs quota. The user's Codex weekly window is exhausted until about 2026-11-10, so no Codex or ChatGPT calls were made. Check the ChatGPT usage page for an OpenCode entry and its credits setting, and read the Codex rate limits before and after one request.
2. **Reference route billing.** Confirm in claude.ai usage that one request through the Claude Agent provider raised the plan meter and left extra usage unchanged. The `rate_limit_event` fields (`rateLimitType`, `overageStatus`) are the signals.
3. **API grants.** Before any call, confirm purchased balance is zero and auto-reload is off. Make one tiny call with a key from the linked organization and check each vendor's billing page for the grant balance and expiry.
4. **Plan tier.** Grant and sign-in eligibility depend on the exact plans, which should be recorded per account.
