# Development

This branch contains only the OpenCode advisory selector. Python 3.11+ standard
library and JavaScript ES modules are sufficient; no install or build step exists.

Work on task branches in separate worktrees. Preserve local changes, credentials,
native configuration and other plugins. Do not install, activate, publish or run
inference without authorization covering that action.

Write behavior tests before nontrivial changes. Validate, commit, then run:

```sh
python3 -B -m unittest discover -s tests -t .
node --test "tests/**/*.test.js"
python3 -B scripts/validate.py
python3 -B scripts/package.py --output dist
python3 -B scripts/validate.py --root dist
```

Run Python tests with `-t .`: without it the suite refuses to load. The quoted Node glob needs
Node 22 or newer (Node expands it; older versions cannot). Use a new output path for each
export. For a live check of plugin behavior, run
`python3 -B scripts/live_check.py --prompt "..." [--settings '<settings JSON>']`: it loads this
checkout's package into a throwaway clone, runs one API-created session through `opencode api`
(inference on the default model), prints the tool calls, `vibe-workflow:` notes, replies and
title as JSON, and removes the session and clone afterwards (`--keep` keeps them). It is never
exported. `--origin` sets the clone's remote for `gh` lookups; use a throwaway or local URL, because
the session's agent could fetch from or push to it.

`python3 -B scripts/live_check.py --injection` proves agents still receive the injected notes. In a
throwaway clone with Smart Router on, it asks each model in `tests/tooling/opencode-verified.json` to quote
the subagent tool's `model` parameter, one first-call prompt with no tools, through
`opencode run --standalone` with a private, empty config directory (so an installed copy of the
plugin cannot load) and `PWD` set to the clone (OpenCode takes its directory from `PWD`, not the
process's working directory), with every `OPENCODE*` variable removed (they can point at another
config or the caller's session). The record holds exactly one model per subscription path in
`config/routes.json` (`claude-agent`, `openai`, `opencode-go`); a record with another provider, a missing
path or two models on one path is refused before any prompt. Before running, the check reads OpenCode's
model list with `opencode api get /api/model --standalone` (no model call). A recorded model that is
still listed is used as is; one still listed but filtered out (disabled, deprecated, no tool use) stops
the check without a pick. An unreadable or empty list stops it as `models_unavailable`. The check
refuses to run unless `config/routes.json` holds exactly the three authorized paths, each billed as
`included_subscription`, so a new route never extends the authorization. One that is gone is replaced
by one level-1 Smart Router pick on its path, reported with its paid-overflow status
(the pick sees only that plan's models; scores and account facts come from the user's state folder); a pick that fails, lands on
another path or is not listed is reported as `could_not_select`, nothing runs and nothing is recorded.
The user pre-authorized that pick (council `51429ec7c824_router-engine-required`). Picks are sticky: the
record keeps the models used, so a weekly price change never swaps a working model. Each run's session is deleted afterwards
(`--archived` only hides it). A model passes only when its reply contains both notes verbatim and it
made no tool call. With git workflow also on, one more prompt on the `claude-agent` path's model
runs exactly one `git switch -c vibe-injection-probe` in the clone, a primary checkout. It passes only when
that is the session's single tool call, the `vibe-workflow:` primary-checkout note is in that call's result
(the hook delivered it), and the model's reply quotes it (the model saw it). That run has a config folder of its
own with two OpenCode V2 permission rules (`SHELL_PERMISSIONS`): deny every action, then allow that one exact
`shell` command (the last matching rule wins), so a command or tool call the rules see is refused before it
runs. The note probes keep the empty config and no rules, because denying tools might hide the subagent tool's
schema, which they read. The rules reduce what a misbehaving model can do; they do not guarantee it, and the
check does not rely on them. The shell rule matches what OpenCode's command scanner extracts, not the whole
input (a review of 2.3.2 found it skips shell declarations), and OpenCode still loads configuration outside the
private folder (ancestor directories of the clone and managed policies), which this check does not control. The
repository ships no OpenCode config or agent rules, so the clone adds none. What guarantees the single command is
the check afterwards: exactly one tool call whose command is exactly the allowed one. Observed live (OpenCode 2.3.2, 22 November
2026, `claude-agent/haiku`, asked to run the allowed command and then `ls`): the allowed command ran with the
note, and `ls` failed with a permission error. Session deletion stays best effort: each run deletes its own
session by ID, and one that cannot be deleted is reported in its result as `session_left`, never swept. The clone runs no Git hooks
(`core.hooksPath=/dev/null`) and every Git call drops inherited `GIT_*` variables. It proves the `execute.after` hook still appends notes to tool results (the PR and skill-load
notes use the same path); it does not cover the skill-load notes themselves, because the isolated config
has no superpowers skills. `opencode run --format json` (2.3.2) prints one event per line: the reply is
the top-level `text` events, a tool call is one `tool_use` event, and text nested in a tool result is not
the reply. Only when every model and the probe pass does it write the models used, the installed OpenCode version and
today's date into the record; commit that change. `tests/tooling/test_opencode_version.py` fails whenever
the installed OpenCode differs from the recorded version, so the suite turns red right after an
OpenCode update. The user pre-authorized agents to run `--injection` without asking when that test
fails or a plan changes the injected notes (`AGENTS.md`); other live checks still need explicit
authorization. If it fails, stop and tell the user. Keep each Python module within 1000 lines.
There is no linter, type checker, package manager, CI or release process here.
Test native readers only with synthetic transports and isolated private homes.
Report deterministic package checks separately from observed native execution.
Nontrivial changes require independent behavior/security review before acceptance.

`scripts/manifest.py` owns the complete runtime export list.
`docs/dependencies.md` records the extraction boundary and original source.
The extraction plan and design retain the task's explicit local-only delivery
and authorization. Its original workflow policy remains pinned for that task;
removing old product instructions does not waive its completion gates.

## The pull request hook: what is known

Read from the installed OpenCode's own code (2.2.6) and its database, not from a
live run of the hook:

- `execute.before` and `execute.after` run in one pipeline, for every tool call the
  session makes. Shell commands can arrive rewritten by rtk's own `execute.before`
  hook (for example `rtk gh pr ...`), so the classifier accepts an `rtk ` prefix.
- `execute.after` receives `{ tool, sessionID, agent, messageID, id, input, status,
  result }`. `input` is the input after earlier hooks, so commands arrive as
  `rtk gh pr ...` and `rtk git ...`.
- For the shell tool `result` is `{ content, output?, metadata }`, where `content`
  is an array of `{ type: "text", text }` parts and `metadata` is
  `{ status, truncated, exit }`. A failing command still has `status: "completed"`
  with a nonzero `exit`.
- The model is given `content` unchanged when it is a non-empty array or a string,
  and the stringified `output` only when `content` is empty. A note therefore has to
  go into `content`; text added to `output` is never shown. (Found when the first
  live `gh pr create` produced no note: the handler had appended to `output`.)

Observed live (OpenCode 2.2.6, 19 November 2026, plugin at `77274f2`): after the
fix, a note reaches the model after `gh pr edit <n> --title "[READY] ..."` (case 3),
after `git push` once a pull request was created or readied in the session (case 2)
and after `gh pr create` (case 1). Case 4 (`gh pr merge`) shares the same append path
but has not been observed. Case 5 (checkout in the primary) is observed on every
`--injection` run: the shell probe's `git switch -c` returned the note (OpenCode 2.3.2,
22 November 2026). Other OpenCode
versions are unverified. To repeat the check, run one matching command and look for
a `vibe-workflow:` line in its result.

The same hook also sees `skill` tool calls. Observed live (OpenCode 2.3.2, 20 November
2026, package exported from `3f52aff`, loaded project-locally in a throwaway clone with
git workflow on and agents merging): when an API-created session loaded
`finishing-a-development-branch`, the onboarding-decision note was in that tool result,
and the agent did not present the integration menu; it said it would push, open a
[BLOCKED] draft, mark it [READY], merge it and keep the worktree, with nothing to ask.
Not observed: the user-merges wording, the cleanup-on wording, a session started from
the desktop or terminal client, and a skill attached to a prompt by the user (no tool
call, so no note).

The note table also covers `using-git-worktrees`, and the finishing note was reworded
(declared choice first). Observed live (OpenCode 2.3.2, 20 November 2026, package
exported from `c0146d9`, throwaway clone with git workflow on, agents merging and cleanup
on): after loading `using-git-worktrees` the agent planned to create a worktree and move
the session into it without asking for consent (it would only ask for the task name);
after loading `finishing-a-development-branch` it planned to verify tests, push, open a
[BLOCKED] draft, mark it [READY], merge it and clean up, without the options menu (it
would ask for the base branch only if unclear). Not observed: the user-merges and
cleanup-off wordings, and sessions started from the desktop or terminal client.

The table also pre-approves the execution method when `writing-plans` loads, in any
onboarded project. Observed live (OpenCode 2.3.2, 21 November 2026, `scripts/live_check.py`,
only Smart Router saved, as off): the note was in the skill's tool result, and the agent's
handoff skipped the method question and still asked for the plan review ("Please review the
plan. Does it capture what you want?"). It did not name the method it would use, so the
note gained "name it"; observed again the same day, the handoff then named the method
("Execution method: Native (pre-approved, so no choice needed)") and still asked for the
review. Not observed: a real plan and execution after the review.

## Session titles: what is known

The opt-in `session_titles` setting is described in
`docs/superpowers/specs/2026-11-20-session-titles-design.md`. Its "Evidence
(2026-11-20)" section records the session timelines and the throwaway-plugin probe it rests on, and its
"Known gaps (documented, not closed)" section lists what is not verified. Read
those two sections before changing `src/session-facts.js` or `src/session-titles.js`; this
file does not copy them.

Observed live (OpenCode 2.3.2, 20 November 2026): the packaged plugin, exported
from branch commit `04a0104`, was loaded as a project-local copy in a throwaway
local clone, with one API-created session and no push. Each quoted title starts
with the invisible U+FFFB marker. The title was empty at creation; the first turn
end wrote `▰▱▱▱▱ WAITING · live probe`, and it stayed unchanged 20 seconds later.
Mid-turn the `WAITING` label was removed, and later turn ends showed `▰▰▰▱▱` and
`▰▰▰▰▱` with `WAITING`. A background child kept its own title, and the root title
was unchanged after the child's report woke it. After the checkout moved to a
branch whose pull request #346 was already merged on GitHub, the turn end showed
`▰▰▰▰▰` (cell 5) through a real `gh pr list` call from the plugin process.

Not observed:

- OpenCode's own title generation: the API-created session never got a generated
  title, so the first-turn race was not exercised. Desktop and terminal sessions
  are unverified for that race.
- Cell 2 on its own: turn 2 wrote the plan and the first declared file in one turn,
  so the meter went from 1 to 3 cells.
- The cleanup-on path: the clone's `git_workflow` had no `worktree_cleanup`, so
  cleanup counted as satisfied; worktree removal holding or releasing cell 5 was
  not exercised.
- A real merge: cell 5 came from a branch whose pull request #346 was already merged
  on GitHub, reached through a local-only upstream ref; no task was taken through
  to a merge.
- A sample taken while the background child was still running: the root's `WAITING`
  during the child's run was inferred from the turn-end title.

Other versions are unverified.

## Onboarding questions: what is known

Observed live (OpenCode 2.3.2, 20 November 2026, model `opencode/tidepool-alpha-free`,
the server default). The packaged plugin was loaded as a project-local copy in a
throwaway local clone, one per run. Each copy had its plugin id and its onboarding skill
id changed, because the installed plugin's skill wins a same-id clash. There was one
API-created session per run, answered through the form API. The remotes were local
paths, so no GitHub setting was read or changed, and question 4 was never asked.
Every run seeded Smart Router on and unconfigured, git workflow on with agents
merging, and session titles off.

- **Run A**: export of `1726cac`, `worktree_cleanup` unset.
  - Question 1 used the saved-on list with "(Current, Recommended)".
  - Question 2 marked "I merge them" as current, which is wrong.
  - Question 5 had "Leave titles alone (Current)" first, which is right. The status
    preview with `enabled: true` was not run.
  - Two headers were renamed. Context went into chat text for questions 2, 3 and 5,
    and was dropped for question 1.
  - Apply returned `invalid_request` from a mis-parsed tool result. The model then
    imported `project.js` with `node` and wrote the settings itself.
- **Run B**: export of `f4ee764`, `worktree_cleanup` unset.
  - The opening line, exact headers and the "It is on, but not set up yet." status
    all appeared.
  - Question 2 showed "Agents merge their own (Current)" first, then "I merge them
    (Recommended)" and "Turn off git guidance".
  - Question 3 used the plain list.
  - The question 5 status preview ran, but "(Current)" was missing.
  - Context was passed as a `context` field the control does not have, so the user
    never saw it.
  - Every apply went through the tool.
- **Run C**: export of `6af94bf`, `worktree_cleanup` false.
  - Questions 2 and 3 marked the right "(Current)" choice, but put it second.
    Question 2 lost its third choice.
  - Question 5 still lacked "(Current)" despite the explicit mapping. Its status
    preview was sent with an extra `expected_snapshot`, got `invalid_request` and
    was not retried.
  - Context was dropped. The run was stopped at question 5, so nothing was applied.

Not observed: any other model, a session started from the desktop or terminal
client, a worktree session, question 4, and the step 1 order and no-bypass wording
added after `6af94bf`.

The question 1 status sentence and saved-on description were reworded after the merge
with `main`, which no live run saw. Smart Router's own check can now report
`scores_unavailable` (model scores not loaded yet, retried automatically) or
`selector_unavailable` (for example Python or the package is missing, which does not
fix itself). The old "not set up yet" implied an action for the first reason, so the
wording is "not ready yet", with a separate hint for the second.

## Levels and reviews

Level bars come from the public score catalog, not a fixed model list. `scores.anchor(cache)`
takes the third-highest per-lab best score; `routing.bars(anchor)` turns it into the level 1,
2 and 3 floors (0.55, 0.80 and 1 times the anchor). When the anchor has
fewer than three labs it is the lowest lab's best score; with no scores it is `None` and selection
falls back to the strongest available. `below_level: true` marks that strongest-available fallback
(an ordinary near-clause pick can score slightly below the bar without setting it), and `index.js` shows that notice once per session
per level. Design and plan reviews are sized to the stakes. The specs are
`docs/superpowers/specs/2026-11-23-router-levels-design.md` and
`docs/superpowers/specs/2026-11-23-stakes-sized-reviews-design.md`.

Observed live (OpenCode 2.3.2, 23 November 2026, branch at 02fb0c8, `scripts/live_check.py --prompt`, Smart Router on):
in three prompts asking the agent to call `smart_router_select` once per delegation it would make, with no edits
or subagents, the ratings were as expected. Case 1 (order-sizing plan): plan review as planning and critical,
implementer as implementation, exact and critical, diff review as review, critical. Case 2 (one-word typo): no
delegation and no plan review. Case 3 (a config default read by order sizing): rated critical. Caveat: `--prompt`
mode also loads the user's installed plugin, whose copy answered the tool with the old rule, so these runs verify
the agents' ratings, not the new levels, which unit tests cover. Dispatch of the reviews was not observed.

## Feedback cards

Cards live in `router-feedback.json` in Smart Router's state folder (`$XDG_STATE_HOME/vibe-workflow/smart-router`,
by default `~/.local/state/vibe-workflow/smart-router`). A card rates a finished subagent's work from 0 to 5 and
moves that model's effective score only for the same kind of work and size group (below, or at or above, 4 agent
hours). From 0.13.0 every kind of work but writing starts at high effort at any level; writing follows the
level (medium, medium, high). Low is never recommended when the model offers more, and a task estimated at 4 or
more agent-hours gets one effort step more; the level never changes. The formula, caps and decay are in `docs/superpowers/specs/2026-11-24-router-feedback-design.md`; this file
does not copy them. A key gets no adjustment until its cards come from at least 3 different sessions. A missing,
damaged or oversized store means no adjustment, and picks fall back to catalog scores. A model cannot rate itself,
on any provider. A session gets one vote per model and kind of work, and can vote again only after 5,000 newer votes
evict its old one. Smart Router sends nothing anywhere; cards stay in that file. A hosted coordinator's own transcript
may still keep what it typed, such as a comment.

## Steering

A coordinator may narrow a pick but not pin one. `excluded_models` takes a pick's `model_ref`, a catalog
key, a route name or a model name; an entry that matches no route is returned as `unmatched`.
`reviewing` takes the `model_ref` of the work under review; the router then prefers a reviewer from another
lab, among models that clear the task's level. The answer carries `reviewer_independent`, and the plugin
shows a notice once per session when it is false. Below-level picks get no feedback card. `allowed_models`,
`allowed_efforts` and `allowed_harnesses` are removed on this branch (release 0.11.0); they were advertised
in 0.10.0, so a caller still sending them gets `invalid_request`. Pinning stays outside the router. Design:
`docs/superpowers/specs/2026-11-24-router-steering-design.md`; council:
`docs/vibe-council/sessions/08d7b6126f45_router-constraints/result.md`.

## Smart Router automatic selection: what is known

The design is `docs/superpowers/specs/2026-11-20-smart-router-auto-selection-design.md`;
the ChatGPT findings are in `docs/chatgpt-quota-spike.md`.

Verified by tests only (synthetic hosts, fetchers and stub processes): the model
list reduction and its coercion of odd host fields, route building, task levels
and ranking, score mapping and family borrowing, the private score cache, its
refresh schedule and fetch bounds (size, same-host redirects, total deadline),
the caller-mistake versus `selector_unavailable`
split, the ChatGPT limit mark and its one-minute throttle, the Codex reset reader
against a stub `codex`, and the notices.

Observed live on 20 November 2026:

- OpenRouter's public `GET /api/v1/models` list shape, including the Artificial
  Analysis score field and `-1` prices on meta-models (routers, not models).
  A refresh downloads about 0.9 MB uncompressed: the fetch sends no
  `Accept-Encoding`, so the design's 88 KB gzip figure does not apply.
- OpenCode's ChatGPT provider uses WebSocket by default. Its requests carried
  `codex.response.metadata` and `responsesapi.websocket_timing` events with no
  usage fields.
- Codex `account/rateLimits/read` on a Codex login answered with the weekly
  window through the new reader in 0.71 s, with no model call.
- Codex refused rate-limit reads with OpenCode's token-sharing credential.
- `provider.chatgpt-limit` is defined in OpenCode 2.2.6's code.
- OpenCode 2.2.6's `ctx.model.list()` answers an envelope `{ location, data }`,
  not an array; `data` held 468 `Model.Info` objects. Each has `id`, `modelID`,
  `providerID`, `type`, `family`, `name`, `package`, `settings`, `headers`,
  `capabilities`, `variants`, `time`, `cost`, `status`, `enabled` and `limit`,
  sometimes `compatibility`. `variants` is a list of `{ id }` objects and can be
  empty. `limit.context` is an integer. `time.released` is an integer in
  milliseconds; the plugin converts it to whole seconds, the unit of
  OpenRouter's `created`, because the selector uses either as a tie-break.
  The plugin still accepts a bare array.
- Subscribing to `session.error` makes the host log `ignoring unknown event
  types` on every load; of the two types tried, only `session.step.failed` is
  accepted.
- A step event arrives as `{ id, created, type, durable, location, data }`;
  `session.step.ended` carried `data: { sessionID, assistantMessageID, finish,
  rawFinish, cost, tokens, snapshot, files }`. By OpenCode's schema,
  `session.step.failed` carries `data.error` as `{ type, message, status?,
  response?: { body }, retryAfterMs? }`.
- End to end in OpenCode 2.2.6 with real OpenRouter data, `smart_router_select`
  answered `selected` at level 1 and level 3. The first fetch ran and showed a
  one-time download notice, removed later because the README and the onboarding
  question already disclose the download. The score cache was 76 KB with mode
  0600. No internal keys reached the tool output. With `session.error`
  dropped, the host logged no unknown-event warning. Account facts were acquired
  for all three plans (ChatGPT, Claude, Go).
- `claude-agent` models report `time.released` as 0.
- OpenCode 2.2.6's token-sharing ChatGPT model set ends at `gpt-6.1-sol`.

The live check found five problems that the tests' synthetic hosts hid; each is
fixed with a regression test:

- The model-list envelope reduced to an empty list, so every call answered
  `no_models`.
- `time.released` arrived in milliseconds while OpenRouter's `created` is in
  seconds; the plugin now converts it.
- Subscribing to `session.error` logged an unknown-event warning on every load;
  the plugin now subscribes only to `session.step.failed`.
- The selector read the clock before acquiring account facts. The readers stamp
  facts after acquisition, so every route looked like it had future facts and
  was excluded as `billing_unverified`. The clock is now read after acquisition.
- Family borrowing took scores from newer versions: old Haiku 4.5 borrowed Haiku
  5.5's score, tied the real `haiku` and won on route order, and Opus 4.6 and 4.7
  appeared as alternatives with Opus 5.5's score. A model now borrows only from
  family members whose version is not greater than its own, and at equal price
  and score an own score ranks before a borrowed one.

The version rule and the own-before-borrowed order were re-run live with real
data:

- Level 1 (`implementation`; files 1-2, spec exact, risk reversible) chose
  `opencode-claude-subscription:haiku`: its own score 36.5 (`score_source:
  openrouter`), effort low, price 0.2, paid overflow blocked. Alternatives:
  `opencode-go-subscription:claude-haiku-5-5` (36.5) and
  `opencode-chatgpt-subscription:gpt-6.1-terra` (51.3, price 2). 25 models were
  unscored, including the old Claude versions that no longer borrow.
- Level 3 (`planning`; files cross-system, spec open, risk critical) chose
  `opencode-claude-subscription:opus`: its own score 59.1, effort high, price 8.
  Alternative: `claude-fable-5-2` (64.6, price 16).

A version is the key's ASCII all-digit segments, compared as integers; segments
of 8 or more digits are dates and are ignored, so `claude-sonnet-4-20250514`
borrows from Sonnet 4, not Sonnet 4.6. The date rule itself is verified by tests;
a live level-1 call after it (commit 3c17042) again chose
`opencode-claude-subscription:haiku` with its own score 36.5, the same
alternatives and 25 unscored models.

Not observed live:

- A `provider.chatgpt-limit` step failure itself, so the mark through the event
  stream is verified by tests only.
- Whether the `http.response` hook's `providerID` option scopes calls to OpenAI.

Ruling: the plugin subscribes only to `session.step.failed` and reads the error
at `data.error`, keeping the top-level and `properties` fallbacks. An event stream
is not re-subscribed if it ends, so later limits go unmarked until the plugin reloads.
Other OpenCode versions are unverified.

## Context instructions on claude-agent: what is known

Read from OpenCode 2.3.2's code: the `claude-agent` provider registers its own
`context`, `generate` and `compaction` session hooks at startup (`ClaudeInstructions`).
They copy `event.system`, except its first part, into the options Claude Code is
launched with. Plugin hooks run after it, so text a plugin pushes into `event.system`
never reaches a `claude-agent` model. Other providers receive it.

Observed live (OpenCode 2.3.2, 21 November 2026, installed plugin 0.7.1, throwaway
repository with only Smart Router on, one prompt per model and no tools):
`claude-agent/sonnet` reported no instruction about `smart_router_select`, only
its catalog entry; `openai/gpt-6.1-terra` quoted the instruction verbatim.

Durable session instruction entries (`ctx.session.instructions.entry.put` and
`remove`) do reach `claude-agent`, as a `<system-update>` in the user turn that
Sonnet treated as binding. Observed on the same version:

- An entry applies from the step after the one that puts it, for every provider.
  An entry put from a `session.created` subscription was not observed: under
  `--standalone` the plugin loaded after the session was created.
- Keys must match `^[a-z0-9][a-z0-9._-]*$`. `list` answers `[{ key, value }]`;
  removing an absent key succeeds.
- Where an entry lands differs by provider: `openai/gpt-6.1-terra` gets it as a
  developer-message `<context>`, still there after compaction; `opencode-go` (Claude
  Haiku 5.5 on the Go plan) gets it appended to the result of the next tool call. A
  pushed part reaches `opencode-go` in its system prompt.
- The `context` event carries `model` as `{ id, providerID, variant }`, with
  `providerID` `claude-agent`, `openai` or `opencode-go`. A hook registered with
  `{ providerID: "claude-agent" }` ran only for the `claude-agent` session.

The code reading is of the compiled 2.3.2 binary, so there is no source file or line;
the bundled function is named `ClaudeInstructions.register`.

Until the change below, the Smart Router instruction was pushed on every step for every
provider, and `claude-agent` sessions also got the entry `vibe-workflow-smart-router`,
read from the second step on, following a council run
(`docs/vibe-council/sessions/a129ba8dd79c_router-instruction-delivery/result.md`), which
found that no channel reached a `claude-agent` session's first model call.

PR #366's probes found one: the `subagent` tool's `model` parameter description, edited
in the `context` hook, reaches `claude-agent`, `openai` and `opencode-go` on the first
call. The design `docs/superpowers/specs/2026-11-22-router-on-subagent-tool-design.md`
makes it the router instruction's only channel: when Smart Router is on, `ROUTER_NOTE`
follows the subagent model note there. The system push and the entry are gone, and no
`put` is made. For a while each session's leftover entry was removed at its first step;
on 22 November 2026 OpenCode's `instruction_entry` table held 47 removed entries and one
active, in an archived test session that was then deleted, so that cleanup was removed
too and the plugin makes no entry calls.
The pull request fallback push, sent only when the `execute.after` tool hook could not
register, was removed: the hook is observed on 2.2.6 and 2.3.2, and the push never reached
`claude-agent`. Onboarding still reports `guard_unavailable` on such a host. The live check described above
(`--injection`) verifies delivery; the history below records the replaced design.

Observed live for the replaced design (OpenCode 2.3.2, 21 November 2026, export of
`f790b44` loaded as a project-local copy with its plugin id changed, in the same
throwaway repository, `claude-agent/sonnet`): after one shell step, Sonnet quoted the
instruction from a `<system-update>`. With Smart Router then turned off, the same
session's next step received "The context under "vibe-workflow-smart-router" no
longer applies. Disregard it.", and Sonnet treated the instruction as removed. In a
second session the entry was put, the session was compacted, and on the next step
Sonnet found the instruction in its system prompt as a
`<context key="vibe-workflow-smart-router">` block. That export pushed only on the
first step. With the export of `1332e32`, which pushes on every step, one new session
per model, after one shell step: `claude-agent/sonnet` found the instruction only in
the entry's `<system-update>`, not in its system prompt; `openai/gpt-6.1-terra` found
it in its developer instructions and in the entry. That repository also loaded the
installed 0.7.1 plugin, so Terra saw the pushed text twice. With the export of
`3a3745b`, which puts the entry only for `claude-agent`, after one shell step:
`claude-agent/sonnet` found the instruction only in the entry's `<system-update>`;
`opencode-go/claude-haiku-5-5` found it only in its system prompt (twice, again because
of the installed plugin) and in no tool result. Not observed: a session
started from the desktop or terminal client, other models, and unloading with live
sessions.

## Subagent model note: what is known

The design is `docs/superpowers/specs/2026-11-22-subagent-model-note-design.md`; its
"Evidence (2026-11-22)" and "Known gaps" sections record the probes and what is not
verified, and this file does not copy them. In an onboarded project the `context` hook
appends the note to the description of the `subagent` tool's `model` parameter, not to
the tool description: OpenCode 2.3.2's `claude-agent` bridge cuts the subagent tool
description to 2048 characters (bundled function `OZ`, constant `Y0`), moving the
"Available subagents:" list first, but passes the input schema whole. Only OpenCode
2.3.2 is verified; if `event.tools` changes shape, the hook changes nothing.

Observed live (OpenCode 2.3.2, 22 November 2026, `scripts/live_check.py` on the server's
default model, branch `feat/subagent-model-note` at `aeb4503` (rebased as `ccd4d57`), only Smart Router saved,
as off), one session per prompt:

- "Use a subagent to count the lines in README.md": the agent dispatched `general` with
  no `model`. No skill, plan or named model applied, so this is the note's "Otherwise
  leave it unset".
- The same task after loading `subagent-driven-development` and following its model
  guidance: the agent read the settings, said Smart Router was off so the skill's tier
  applied ("single-file mechanical task → cheap model"), looked models up with the
  `models` tool, and dispatched with `model: "openrouter/tencent/hy4-flash"`.

Not observed: the session's own model and provider (the script does not record them),
so the same-provider rule was not checked; Smart Router on; a user-named or
skill-named exact model; `claude-agent` sessions; and sessions started from the desktop
or terminal client.

With Smart Router on, `ROUTER_NOTE` follows this note on the same parameter (see "Context
instructions on claude-agent"). Observed live (OpenCode 2.3.2, 22 November 2026,
`scripts/live_check.py --injection`, branch `feat/router-on-subagent-tool`): on the first
call, `claude-agent/haiku`, `openai/gpt-6.1-terra` and `opencode-go/claude-haiku-5-5` each
quoted both notes verbatim with no tool call, and the check recorded 2.3.2 in
`tests/tooling/opencode-verified.json`. The first attempt failed for every model because the
server booted in the caller's directory (OpenCode reads `PWD`, not the process's working
directory), so the clone's plugin never loaded; the check failed and recorded nothing,
as it should.

## Vibe council: what is known

Observed live (OpenCode 2.2.6, 21 November 2026, export of `7e47e58`). The exported
package was the only plugin, loaded by a private server (`--standalone`) with its own
config and state directories, in a throwaway one-commit git repository that was not
onboarded. The native logins were the user's own. The coordinator model was
`claude-agent/sonnet`.

- `vibe_council_roster` with the default roster answered `ready: false`: Opus and
  Fable `ok` with paid overflow `blocked`, display names "Opus 5.5" and "Fable 5.2";
  Astra `unknown_model` with candidates `openai/gpt-6-astra` and
  `openai/gpt-6-astra-fast`. Nothing was substituted.
- The host's own model list (`ctx.model.list()`) has `gpt-6.2-astra` only under the
  paid `opencode` provider; on `openai` the newest Astra is `gpt-6-astra`. A direct
  `openai/gpt-6.2-astra` run failed with `provider.no-route: Model unavailable`. The
  `models` search tool lists `openai/gpt-6.2-astra`, so that listing is not proof of a
  route.
- `/vibe-council` with the default roster: the coordinator loaded the skill, called the
  roster tool, stopped, offered the candidates as options, and dispatched and wrote
  nothing.
- `/vibe-council` with Astra on `openai/gpt-6-astra`: the roster was `ready`
  (`paid_overflow` `unknown` for Astra). The coordinator wrote the brief, hashed it,
  named the folder `<hash12>_price-rounding-cli`, and dispatched three background
  `subagent` calls with `agent: "explore"`, each with its explicit model and the same
  one-line prompt. Every answer used the strict line format, under 500 words. The
  coordinator saved `answers/`, wrote `result.md` (majority, unanimous and simplest-
  variant rulings, "the coordinator did not vote") and asked two questions. The brief
  did not contain its own path. Nothing outside `docs/vibe-council/` changed. A run took
  about one minute.
- Two runs started at once by the test harness wrote separate folders with different
  hashes and did not interfere.

Not observed live: round 2, and a member that fails or times out. Other OpenCode
versions are unverified.

### Picked seats

Observed live (OpenCode 2.3.2, 21 November 2026, export of `4e8620c`), set up as
above with `opencode run --standalone`, a fresh private state folder (mode 0700),
the user's own logins and `claude-agent/sonnet` as the coordinator.

- `/vibe-council` with no named seats called `vibe_council_roster` with `{"v":1}`.
  The first call downloaded the scores (`openrouter-models.json`, 76 KB, mode 0600)
  and answered `ready: true` with Fable `claude-agent/claude-fable-5-2` (Anthropic,
  64.6), Astra `openai/gpt-6.2-astra` (OpenAI, 62.1) and Opus `claude-agent/opus`
  (Anthropic, 59.1), all with variant `high`. That is one seat per lab first, then
  the next frontier model. Paid overflow was `blocked` for Claude and `unknown` for
  ChatGPT.
- The three members were dispatched as `provider/id#high` and all answered; on
  2.3.2, `openai/gpt-6.2-astra` is on the plan provider and ran. A run took about
  90 seconds. Only `docs/vibe-council/` changed.
- The record's member table carried lab, score and the credit. Before dispatch, the
  coordinator wrote only "Roster is ready" and did not show the roster table the
  procedure asks for, in both runs.
- A state folder created with mode 0755 makes storage refuse it: every route is
  `billing_unverified` and the pick answers `no_eligible_route`. The first run of
  this check hit that through its own setup.

## Claude subscription limits: what is known

The design is `docs/superpowers/specs/2026-11-26-claude-quota-evidence-design.md`; this section
records what the code accepts and bounds. Its meter evidence comes from the native usage read, not
from the failure text.

Failure trigger (`src/claude-limit-events.js`, OpenCode 2.3.2): a `session.step.failed` whose
`data.error.type` is `provider.unknown` and whose message is exactly the observed weekly form,
`You've hit your weekly limit · resets <clause> Resets at <ISO UTC timestamp>.` The clause is
never parsed, has no newline, and the whole message is at most 512 characters. The timestamp may
carry 0 to 3 fractional digits; it must be a real UTC instant and is floored to Unix seconds.
Five-hour notices, generic 429s, auth errors, other providers and forged or embedded text do not
trigger. The model comes from the failed assistant row (`data.assistantMessageID`) in the failure's
own session and location, through `session.context`. It must be `claude-agent` with a model ID. The
session's current model is never used. The event location must match the plugin's directory string
and workspace ID (both absent counts as equal), with no symlink resolution. Smart Router must be on.

Bounds: one lookup or probe at a time per plugin instance; a failure that arrives meanwhile is
dropped, never queued. The lookup has a 2-second deadline and a late result is discarded. Duplicate
event IDs are remembered for the last 256 events. The probe runs `--claude-limit` with only
`{"reset_at": <seconds>}` on stdin, with a 15-second timeout that sends `SIGTERM`, and is never
retried. The reset must be after now and within 7 days plus 60 seconds, checked again before launch.
The Python side allows one probe in flight across instances and one start per account per 60 seconds.
The probe reads only meters from the native exchange. A pause applies to a route only when a
confirmed meter is exhausted, valid and resets in the future. Picks never wait for the probe.

Unknowns: an unrecognized notice, shape or context row starts nothing and is not guessed. A
missing, null, malformed, conflicting or unrecognized meter is unknown, and it neither extends nor
clears a pause.

Limit of coverage: the tests use invented fixtures, fake runners and synthetic child processes, all
in isolated homes. No live quota check, model call or end-to-end failure was run. Other OpenCode and
Claude Code versions are unverified.
