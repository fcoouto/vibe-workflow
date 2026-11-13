# Luna 6 / Haiku 5.5 timed template qualification

The user explicitly added Luna 6 and Haiku 5.5, then set two stopping conditions: stop at twenty minutes, including preflight, or earlier when both models satisfy the template cases. The private operator records one shared monotonic deadline from that instruction. No deadline reset after a failure, repair or case change.

Use `openai/gpt-6-luna#low` through the existing ChatGPT OAuth connection and `claude-agent/haiku#low` (catalog name Haiku 5.5) through the existing Claude Max login. These are subscription routes, not the earlier free-Go routes; missing cost entries do not mean zero price. No billed API key, paid API fallback, model substitution, account/privacy change or new sign-in is authorized. The coordinator model stays unchanged.

Start with twelve fresh slots: two models × the same three synthetic cases × prose/template arms. Preserve the [original criteria](2026-11-10-templates-go-qualification.md), source hashes, output permissions and [explicit-path owner guidance](2026-11-10-templates-go-followup.md). Only route metadata changes in prompts. There is no model-facing instruction change for this matrix. Retain all earlier evidence and failures.

## Safety and execution

- Credential-free project copies outside real projects; isolated HOME/XDG and OS protections, exact Markdown write permissions, no model shell/network/execute/delegation/config access. Protect evaluators and source inputs.
- Reuse only the selected subscriptions. Pass the Claude login's still-valid native access token privately to its supported runtime environment; never copy its refresh token. Use the existing ChatGPT access token in the isolated supported credential store with an empty refresh token, and refuse when its expiry cannot cover the remaining deadline. No global credential mutation. Revoke/remove only disposable credential entries after collection; retain no tokens in publication artifacts.
- Confirm the actual native runtime excludes filesystem settings before the Claude route is used. OpenCode 2.2.6's bundled provider sets `settingSources: []`, hooks disabled, strict MCP config, no skills/default tools and explicit OpenCode tool bridging. Fake HOME also excludes private instruction and memory files.
- Exact selected-route primary-request guard, no auxiliary generation, automatic retry, warming or compaction. Maximum 32 OpenCode primary requests and 300 seconds per slot, further clamped to the shared deadline; at most two concurrent slots. Native SDK internal transport accounting is not claimed independently observed.
- Up to one further twelve-slot comparison may target a concrete observed failure within the same deadline. Show complete before/after instructions before any such change, retain the failed matrix, and never coach/replay an unchanged run to green. No extra reviewer/grader calls.
- A model succeeds only when its three template-arm cases satisfy every applicable content criterion, scope/preservation checks and actual applicable-layout retrieval/use. A successful terminal event, formatting or self-report alone never establishes success. Compare prose arms for effectiveness without averaging away failures. One sample per cell establishes no universal reliability.
- Do not stop early for a failed/unavailable route or exhausted first matrix: continue independent prerequisite diagnosis or a bounded justified comparison until both succeed or the shared deadline expires. Do not busy-poll or manufacture filler calls while blocked. All live execution must end by the deadline.

## Checklist

- [ ] Record deadline, exact routes and reusable authentication without inference; test sandbox, guard and collection boundaries.
- [ ] Freeze new prompts, original criteria/template/fixture hashes and operator digest before inference.
- [ ] Execute the initial twelve fresh slots with remaining-time limits; retain tool evidence, terminal failures, usage and stopped-process receipts.
- [ ] Independently inspect outputs and rerun protected checks; record per-model success against all template cases and prose differences.
- [ ] If needed, run only one disclosed, failure-driven candidate within remaining time; otherwise retain the baseline and explain the blocker.
- [ ] Report whichever stop condition occurred, exact before/after instructions if any, all failures, observed usage and limits. Stop servers and remove only temporary credentials.

The prior source/policy snapshot remains pinned for comparison; this plan changes neither production instructions nor templates. Run only focused evidence and fixture checks, not a full suite or release. Update the existing draft evidence PR; independent report review and merge remain separate.
