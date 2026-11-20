# ChatGPT quota spike

Date: 20 November 2026. Bounds set by the user: 40 minutes, up to 50 ChatGPT
requests, more capable models allowed. Used: about 5 minutes, 5 ChatGPT requests
(1 through OpenCode to renew its token, 4 direct WebSocket requests with the
prompt "Say OK" on GPT-6 Luna). Read-only: OpenCode's tokens were never
refreshed or rewritten by the spike, ChatGPT's `backend-api` was never called,
nothing was installed. Two research passes (OpenAI documentation; community
tools and issues) ran on Claude models so the ChatGPT budget stayed for probes.

## Question

Is there any source of ChatGPT plan usage, or of account identity, for
OpenCode's ChatGPT connection (Sign in with ChatGPT token sharing), so the
router could stop before a 429 or drop the same-account assumption?

## Answer

No. Nothing meets the adoption rule (documented, or observed consistently in
two checks). The design's 429 stop with the Codex resume time stays, with one
correction to where the 429 is detected (below).

## What was checked

| Lead | Result | Evidence |
|---|---|---|
| OpenAI Sign in with ChatGPT docs | No usage, quota or app-share endpoint for token sharing. The docs only say to link users to ChatGPT Settings → Usage, and not to infer a reset time from the 429 because an app-specific limit can also apply. | developers.openai.com/siwc: profiles-and-sessions ("Tracking usage"), errors-and-recovery, models-and-inference, token-reference |
| Identity | Only the ID token carries `sub` and, not guaranteed, `email`. The access token has opaque `encrypted_auth_metadata`. OpenCode does not store an ID token: its stored credential has only `access`, `refresh`, `metadata.clientID`, `methodID`, `type`. The OpenID userinfo endpoint rejects OpenCode's access token (HTTP 401, checked earlier the same day). | token-reference, sign-in; local credential field names |
| WebSocket transport | OpenCode's ChatGPT provider uses WebSocket by default (`transport: … ?? "websocket"` in OpenCode 2.2.6). Over WebSocket the server sends a `codex.response.metadata` event carrying a `headers` map, and a `responsesapi.websocket_timing` event. For token sharing the headers map holds only `x-models-etag` and an opaque `x-codex-turn-state`, with no usage fields, in four requests, including with `originator: codex_cli_rs` and OpenCode's `x-codex-beta-features` header. | 4 direct probes; OpenCode 2.2.6 program text |
| HTTP transport | No usage headers on `POST /v1/responses` (checked earlier the same day with 2 requests). | earlier probes |
| Codex app-server with OpenCode's token | Refused: "codex account authentication required to read rate limits". `account/read` returns `account: null`. Started as OpenAI documents for token sharing (`env_key`, `requires_openai_auth=false`), with an empty `CODEX_HOME`. | 1 probe, no model call |
| Community tools | Every tool that shows ChatGPT usage calls `chatgpt.com/backend-api/wham/usage` or reads `x-codex-*` headers from `chatgpt.com/backend-api/codex`, using a Codex login or OpenCode's legacy "Codex browser" login. With token-sharing tokens `wham/usage` returns 401. | openai/codex#54219, anomalyco/opencode#62529 and #56798; plugin READMEs |
| OpenCode's own handling | OpenCode recognizes `subscription_sharing_usage_limit_exceeded` in the provider error body and raises a named error, `provider.chatgpt-limit` ("ChatGPT usage limit reached. Check ChatGPT Settings → Usage for details."), treated as non-retryable. It does not keep usage numbers. | OpenCode 2.2.6 program text; opencode#56798 |

## Consequences for the design

1. **Detection surface (correction, within the design's builder latitude).**
   Because OpenCode's ChatGPT traffic uses WebSocket by default, the
   `http.response` hook does not see it. The primary detection surface for the
   429 is OpenCode's own `provider.chatgpt-limit` session error. The
   `http.response` hook stays only as a fallback for HTTP transport.
2. **Resume time.** Unchanged: the Codex reading, under the stated same-account
   assumption, or one hour without Codex. No source gives a reset time for the
   OpenCode connection itself.
3. **Balancing (deferred design).** For token sharing, plan usage percentages
   exist only through a separate Codex login (`account/rateLimits/read`), under
   the same-account assumption. A user who switches OpenCode's ChatGPT
   connection to the legacy "Codex browser" login would expose usage through
   `x-codex-*` headers on Codex's backend; that path is undocumented and was
   not adopted.
