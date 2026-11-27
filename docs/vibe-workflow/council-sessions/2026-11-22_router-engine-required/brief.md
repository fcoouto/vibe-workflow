# Council brief: Smart Router as a required engine, and model picking in the injection check

## Role
You are one of 3 council members (Fable, Astra, Opus). Answer independently. You cannot
see the other members' answers.

## The user's standing instructions
- "hmmm... I like the idea... And I'm starting to consider Smart Router as a required engine of our plugin, instead of opt-in..."
- "Is \"haiku\" model a hard-coded one? Are we able to select a small model without hard-coding its name?"
- Earlier the same day, on another change: "Use Fable's suggestion and don't change Question 1 nor add another question to Onboarding"
- Standing memory (business rule, verified 2026-11-10): "the user wants usage readings and the whole Smart Router as lightweight as possible: on-demand reads, reuse existing data, no extra model calls, no polling"
- On the live check: "make sure we have a test that validates that agents are still receiving that update so we can keep testing it as we update our local OpenCode version" and "preferably if agents are able to check the test results and run the live check by themselves."

## Bias
Weigh optimistic bias as well as pessimistic bias. Flag an item that flatters the
result as readily as one that is too cautious.

## Context
The repository is `vibe-workflow`, an OpenCode V2 plugin (JavaScript ES modules plus a
Python 3.11 standard-library runtime). Read `README.md`, `PRODUCT.md` and
`docs/development.md` first.

Smart Router has two layers today:
- **Engine:** the Python selector (`runtime/selector.py`, `runtime/routing.py`,
  `config/routes.json`), public model scores, and local token-free account reads. It
  ranks models only on three subscription paths (Claude via `claude-agent`, ChatGPT via
  `openai`, OpenCode Go via `opencode-go`) and never launches, reserves quota or grants
  spend.
- **Feature:** the agent-facing note on the `subagent` tool's `model` parameter
  ("Smart Router is on: ... consult smart_router_select first ...", `index.js`
  `ROUTER_NOTE`) and the `smart_router_select` tool answering. Onboarding question 1
  ("Use Smart Router for this project?", `skills/vibe-onboard-project/references/procedure.md`)
  turns this on or off per project.

`scripts/live_check.py --injection` (dev-only, not exported) proves after an OpenCode
update that agents still receive the injected notes: one first-call prompt on each of
three hard-coded models (`APPROVED_MODELS = ("claude-agent/haiku", "openai/gpt-6.1-terra",
"opencode-go/claude-haiku-5-5")`) plus one shell-note probe on `claude-agent/haiku`. It
records the OpenCode version in `tests/opencode-verified.json` only when all pass, and
refuses any model list other than `APPROVED_MODELS`. `tests/test_opencode_version.py`
fails when the installed OpenCode differs from that record; `AGENTS.md` pre-authorizes
agents to then run `--injection` without asking. The coordinator proposed replacing the
hard-coded names with Smart Router level-1 picks, one per subscription path, using the
selector's existing `allowed_harnesses` constraint (`runtime/routing.py`
`CONSTRAINT_FIELDS`).

## Settled rules (not up for a vote)
- Selection never launches, admits, reserves quota or grants a spend permit.
- Only the three subscription paths are routed; paid overflow is reported, never enabled.
- The default test suite never runs inference; `--injection` is the only pre-authorized
  live run, and a failing check never records a version.
- The injection check is detection only; no fallback delivery channel is added.
- `PRODUCT.md` currently says: "Offer Smart Router with its choice defaulting to ON and a
  clear opt-out." Changing that is the user's decision; you may vote on whether to
  recommend it (D1), and the coordinator will present a reversal as a question.

## Measured facts
- Engine requirements (README "Requirements", `docs/development.md`): Python 3.11+, Git,
  a score cache `openrouter-models.json` of 74.5 KB (mode 0600) under the user's state
  folder, refreshed about weekly from OpenRouter's public models list with no
  credentials (about 0.9 MB uncompressed per refresh), and local account reads that make
  no model call.
- Engine callers today (`index.js`): `smart_router_select` (`operation: "select"`, only
  when the project's preference is on), the onboarding readiness check (`check_setup`),
  the ChatGPT usage-limit mark (`mark`, only when on), and the council roster
  (`council`, `council_pick`), which already runs "whether or not Smart Router is on"
  (`PRODUCT.md` Council bullet, `README.md`).
- Reliability observed on 2026-11-22: 4 `smart_router_select` calls and 1
  `vibe_council_roster` pick, all answered (`selected` / `ready: true`), none failed.
  Earlier live notes in `docs/development.md` record five selector bugs found only
  live (all fixed), e.g. every call answering `no_models` before an envelope fix.
- Level-1 picks observed live (`docs/development.md`, "Smart Router automatic
  selection"): `opencode-claude-subscription:haiku` with alternatives
  `opencode-go-subscription:claude-haiku-5-5` and
  `opencode-chatgpt-subscription:gpt-6.1-terra`; 25 models unscored.
- Model naming: `claude-agent/haiku` is an alias that tracks the newest Haiku;
  `openai/gpt-6.1-terra` and `opencode-go/claude-haiku-5-5` are pinned versions.
- The selector is advisory and ranks by public score and price; a newly cheapest model
  may not have been tried on the check's quote prompt.

## Items
- D1 — Make Smart Router fully required: remove the opt-out (onboarding question 1),
  so the router note and `smart_router_select` are always on in onboarded projects.
  Direction: stricter. If wrong: users lose a choice the product promised, and every
  onboarded project pays the engine's requirements and a note on every step.
- D2 — Make the engine required for plugin internals (council roster, the injection
  check, onboarding readiness), but keep the agent-facing router note and
  `smart_router_select` advice opt-in per project. Direction: neutral. If wrong: two
  concepts named "Smart Router" confuse users, or the opt-out no longer means "no
  Smart Router at all".
- D3 — `--injection` picks its models per subscription path from a level-1 Smart Router
  request (`allowed_harnesses` per path) instead of hard-coded names, and records the
  models it used in `tests/opencode-verified.json`; the shell-note probe uses the
  `claude-agent` path's pick. Direction: neutral. If wrong: a selector outage or a
  newly picked model that handles the quote prompt badly makes the update check fail.
- D4 — When the selector cannot pick for a path, the check reports "could not select"
  separately from a delivery failure, records nothing, and the agent stops and tells
  the user. Direction: stricter. If wrong: a third outcome adds complexity, or blocks
  updates on selector problems unrelated to OpenCode.
- D5 — The user's pre-authorization changes from "these three named models" to "Smart
  Router's level-1 pick on each of the three subscription paths", replacing the check's
  current refusal of any unnamed model. Direction: looser. If wrong: the check could
  spend on a model the user never named, on their subscription.

## Output format (strict)
Answer with at most 500 words, in this order:

1. One line per item, exactly:
   `<ID>: KEEP | CHANGE: <simplest change> | DROP | DEFER — <short reason>`
   Pick one verdict per line. Give a change only after CHANGE.
2. `Compounding`: 2 to 4 lines on how the items interact.
3. `Top 3`: the three items that matter most.

## Constraints
- Read-only. No edits, no Git writes, no subagents.
- Forbidden paths: secrets, credentials and private data; `~/.local/state/vibe-workflow/`;
  `~/.local/share/opencode/`; `~/.claude/`; `~/.codex/`; `~/.config/opencode/`; any
  `.env` file; `docs/vibe-council/sessions/` other than reading earlier `result.md` files.
  Do not run `scripts/live_check.py` or any `opencode` command.
- Use the network only if you need it.
- Use about 15 tool calls.
- Answer in your final message.
