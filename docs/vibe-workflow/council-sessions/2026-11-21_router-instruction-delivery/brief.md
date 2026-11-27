# Council brief: how Smart Router delivers its "consult smart_router_select" instruction

## Role
You are one of 3 council members (Fable, Astra, Opus). Answer independently. You cannot
see the other members' answers.

## The user's standing instructions
The question, verbatim:

> Shall we simplify the way we push instructions regarding smart_router_select consultation for all clients? ChatGPT, Claude and OpenCode?
>
> And maybe, MAYBE, use a normal injection as a fallback?
>
> What would the council say about it?

A standing business rule the user set earlier, verbatim:

> I want the usage readings to be as lightweight as possible. Actually, this applies for the whole Smart Router solution

From the repository's `AGENTS.md`, verbatim:

> - Keep the smallest sufficient change. Reuse the standard library and existing helpers.

## Bias
Weigh optimistic bias as well as pessimistic bias. Flag an item that flatters the
result as readily as one that is too cautious.

## Context
This repository is an OpenCode V2 server plugin (`index.js`). When Smart Router is on
for a project, the plugin tells the session's model: "Before delegating work, consult
smart_router_select …" (the full text is `ROUTER_INSTRUCTION` in `index.js`). "Clients"
in the user's question means the three plans Smart Router routes through inside
OpenCode: ChatGPT (provider `openai`), Claude (provider `claude-agent`, which runs
Claude Code as a subprocess) and OpenCode Go (provider `opencode-go`).

Until now the plugin delivered the text with a "normal injection": it pushed a part into
`event.system` in its `context` session hook, before every model call. That never
reaches `claude-agent` models. OpenCode 2.3.2's `claude-agent` provider has its own
built-in `context` hook, registered before any plugin. It copies `event.system`, minus
its first part, into Claude Code's launch options, so later plugin pushes are lost.

Pull request #364 (branch `fix/claude-agent-instructions`, head `57a1ce2`) fixes that.
It keeps the push on every step for all providers. It also puts a durable session
instruction entry, `ctx.session.instructions.entry.put({ sessionID, key, value })`,
once per session, and removes it when Smart Router is turned off or the plugin
unloads. Read:

- `index.js`: the `context` hook, `entry()`, `drop()` and the cleanup function.
- `tests/plugin.test.js`: the tests after the `routed()` helper.
- `docs/development.md`: the section "Context instructions on claude-agent: what is known".

The diff against main is `git diff b5d5faa..57a1ce2`.

## Settled rules (not up for a vote)
- The plugin only advises. Routing never launches work, reserves quota or grants spend.
- The instruction text itself is not up for a vote, only how it is delivered.
- Instructions placed inside tool results are not an acceptable channel: Claude models
  correctly treat them as prompt injection and refuse them (observed).
- This repository ships only the OpenCode plugin. Other client integrations are out of
  scope.

## Measured facts
All on OpenCode 2.3.2, 20–21 November 2026, in throwaway repositories. Each check asked
the model to quote any matching instruction and say where it appears.

| Provider (model) | Pushed into `event.system` | Session instruction entry |
|---|---|---|
| `claude-agent` (Sonnet 5.5) | Never arrives, even placed first and marked REQUIRED (4 runs, plus direct questions) | Arrives as a `<system-update>` in the user turn from the step after the put; Sonnet treats it as binding. After compaction it sits in the system prompt as a `<context key="…">` block. When removed, the model receives "The context under … no longer applies. Disregard it." |
| `openai` (GPT-6.1 Terra) | Arrives in the developer instructions on every step | Arrives as a developer-message `<context>` from the step after the put, and is still there after compaction |
| `opencode-go` (Claude Haiku 5.5 served through the Go plan) | Arrives in the system prompt | Arrives as a `<context key="…">` block **appended to the result of the next tool call** |

More measured facts:

- An entry put during a step applies only from the next step, for every provider. A
  first-step model call never sees an entry put in the same step.
- An entry put from a `session.created` event subscription was not observed: under a
  private server the plugin loaded after the session was created.
- Entry API details: keys must match `^[a-z0-9][a-z0-9._-]*$`. `put` and `remove` are
  idempotent; removing an absent key succeeds. `list` returns `[{ key, value }]`.
- In an earlier spike, a plugin appended an instruction ("call the advisor tool …") to
  tool results. Sonnet and Haiku through `claude-agent` both noticed it, said it came from
  a tool result rather than the user, treated it as a possible prompt injection and did
  not act on it (2 of 2 runs).
- Tests on head `57a1ce2`: `node --test tests/*.test.js` 207 pass, 0 fail; Python suite
  OK; `scripts/validate.py` passed. Three independent review rounds: no blocking findings
  remain.

## Items
- D1 — Use one mechanism for all three providers: the durable session instruction
  entry is the primary channel everywhere. Direction: neutral. If wrong: `opencode-go`
  receives the instruction appended to a tool result, which Claude-family models on that
  plan may refuse as injection; every provider misses it on a session's first step.
- D2 — Keep the normal injection (push into `event.system`) only as a fallback: push
  when no entry is in effect yet (the session's first step, a failed put, a host
  without the entry API), and stop pushing once the entry is in place. Direction:
  looser. If wrong: `openai` and `opencode-go` lose the every-step system-prompt
  instruction they have today and depend on the entry's placement (developer message for
  `openai`, tool result for `opencode-go`).
- D3 — Keep PR #364's design: push on every step for all providers, and also put the
  entry for all providers. Direction: stricter. If wrong: `openai` and `opencode-go`
  models see the instruction twice (system prompt plus entry), costing a few hundred
  tokens per session and some noise; one more host call per session.
- D4 — Choose by provider instead: put the entry only when the session's model is
  `claude-agent` (`event.model.providerID`), and push only for the others. Direction:
  neutral. If wrong: the code depends on a provider ID; a future provider that also
  copies the system prompt early would be missed until someone notices; a session that
  switches model mid-way needs both paths.
- D5 — Move the pull request fallback note ("open pull requests as [BLOCKED] drafts …",
  pushed only when the pull request hook failed to register) to the same mechanism.
  Direction: stricter. If wrong: more code for a note that appears only on hosts where
  plugin tool hooks are missing, which likely lack the entry API too.
- D6 — Report the ordering to OpenCode upstream (the built-in `claude-agent` context
  hook copies the system prompt before plugin hooks run) and ask for a fix or a
  supported plugin channel. Direction: neutral. If wrong: time spent on an issue that is
  declined; the workaround stays either way.
- D7 — Merge PR #364 now as it stands and treat any simplification as a follow-up
  pull request. Direction: optimistic. If wrong: churn, since a later change rewrites the
  same lines; or a design the council would reject ships first.

## Output format (strict)
Answer with at most 500 words, in this order:

1. One line per item, exactly:
   `<ID>: KEEP | CHANGE: <simplest change> | DROP | DEFER — <short reason>`
   Pick one verdict per line. Give a change only after CHANGE. KEEP means "do this as
   written"; DROP means "do not do this".
2. `Compounding`: 2 to 4 lines on how the items interact.
3. `Top 3`: the three items that matter most.

## Constraints
- Read-only. No edits, no Git writes, no subagents.
- Forbidden paths: `~/.local/share/opencode`, `~/.config/opencode`, `~/.claude`,
  `~/.codex`, `~/ai-memory-vault`, anything under `/private/tmp`, `.env` files, and the
  `docs/vibe-council/sessions/` folder of this repository. Do not read them.
- Use the network only if you need it.
- Use about 15 tool calls.
- Answer in your final message.
