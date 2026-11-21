# Vibe Council

Status: approved by the user (2026-11-21).

## Naming

One name everywhere: the `vibe-council` skill, the `/vibe-council` command, the
`vibe_council_roster` tool and the `docs/vibe-council/` record folder. In prose it is
"the council"; users may still call it the "LLM council".

## Intent

The council is a small panel of frontier models from different vendors. Each
member reviews the same set of decisions on its own and votes per item; the
coordinating agent tallies the votes into one recommendation, and the user decides.

It was run by hand three times with good results: two research-tool reviews (it found
stacked pessimism in one and two items that flattered results in the other), and the
controller-rulings review of PR #350. This feature makes it a repeatable part of
`vibe-workflow`, so any project can ask for it and get the same process, the same
checks on who sits on the panel, and a record kept with the project.

## Goals

- A `vibe-council` skill that carries the whole proven process: collect items,
  measure first, write one shared brief, dispatch members in parallel and blind, tally,
  present, optional round 2, record.
- A thin `/vibe-council` command that loads the skill with the user's arguments.
  Plugin commands are named `/vibe-<skill-name>`, after the skill they load.
- One small tool, `vibe_council_roster`, that checks every seat before dispatch: the
  exact model exists, its provider is one of the plan providers, the account is a
  proven subscription, quota is not exhausted, and paid overflow is reported. It never
  substitutes a model.
- Each run is recorded in the project, in a session folder that git tracks by default.

## Non-goals

- No settings block in `.vibe-workflow/settings.json` and no onboarding question. The
  default roster lives in the skill; the user overrides it per run ("ask Astra and
  Opus").
- No code for the tally or the vote parsing. The coordinator applies the written rules.
- No consent store. Seats are limited to the plan providers in `config/routes.json`,
  which already receive the project's code during normal OpenCode work.
- No automatic trigger. The council runs only when the user asks.
- No dependence on Smart Router being enabled for the project. The council reuses its
  code and account cache, not its preference.

## Anti-goals

- A member that edits files, runs Git writes, starts subagents or acts outside the repo.
  Edits and subagents are blocked by the native `explore` agent; the rest is enforced
  only by the brief.
- A silent model swap, a provider outside the plan list, or a run that starts before
  the user has seen the roster.
- The coordinator voting on decisions it wrote.
- The council overriding the user: anything that reverses an earlier user decision is
  shown as a question.

## Approaches considered

- **A. Prose only** (skill, references, command). Smallest, but the roster rules that
  matter most (no substitution, billing proven) would rest on the coordinator reading
  carefully, with no tests.
- **B. Prose plus one roster tool** (chosen). The rules where a mistake costs money or
  leaks data run in tested code that reuses Smart Router's account checks; everything
  else stays in the skill.
- **C. B plus a `council` settings block through onboarding.** Rejected for now: it
  touches `project.js`, the most guarded code in the package, for a default the user
  can state in one line per run.

The user chose B. Tally parsing in code was considered and left out (the coordinator's
call, open to change if tallies prove unreliable).

## Parts

### 1. Skill `vibe-council`

Same shape as the other skills: a short `SKILL.md`, registered with
`registerSkill(ctx, "vibe-council")` in `index.js`, pointing at its references.

- `SKILL.md` frontmatter description: "Use when the user asks for the council or the
  LLM council, or to run the council on a set of decisions or review findings."
- `references/procedure.md`: the process, default roster, tally rules, presentation
  template, failure handling.
- `references/brief-template.md`: the member brief.
- `references/record-template.md`: `result.md` layout.

### 2. Command `/vibe-council`

Registered through `ctx.command.transform`, as the ponytail plugin does on OpenCode
2.2.6. Its template is one line, "Use the vibe-council skill. <arguments>", sent
back with `ctx.session.prompt`. If the host has no command API, the plugin skips the
command and the skill still works.

### 3. Tool `vibe_council_roster`

Input:

```json
{"v": 1, "seats": [
  {"seat": "Astra", "model": "openai/gpt-6.2-astra", "variant": null},
  {"seat": "Opus",  "model": "claude-agent/opus", "variant": null},
  {"seat": "Fable", "model": "claude-agent/claude-fable-5-2", "variant": null}]}
```

One to five seats, unique seat names, `model` in `provider/id` form, `variant` either
null (the provider's default) or a name.

Flow:

1. JavaScript: take the live model list from the existing `models()` helper
   (`ctx.model.list()`, already filtered to enabled models on the plan providers).
   Match each seat's `provider/id` exactly. No match: the seat's status depends on
   whether its provider is on the plan (`unknown_model`, with up to three enabled
   models whose id or name contains the last alphabetic word of the seat id as candidates; an unlisted model has no host family) or not (`provider_not_on_plan`). A variant
   not in the model's `variants` gives `unknown_variant`.
2. Python: matched seats go to `scripts/select.py --council-roster` through the existing
   `runRouter` spawn, reusing the Smart Router state directory and its account cache:
   `routing_paths.routes` → `LocalSource.acquire_billing` → `routing_observations.eligible`.
   That gives `billing_unverified`, `quota_exhausted` (this also covers the ChatGPT
   limit pause) or `ok` with `paid_overflow` (`blocked`, `enabled` or `unknown`).

Output: one entry per seat, in input order, with `seat`, `model` (unchanged),
`name` (the host's display name, which resolves aliases: `claude-agent/opus` shows as
"Opus 5.5"; `models()` keeps this field for the roster), `status`, `paid_overflow` and
`candidates`. Plus `ready`: true only when every seat is `ok`. Like
`smart_router_select`, it carries `admission`, `reservation` and `spend_permit` set to
false. The tool launches nothing and never changes a model ID.

The tool is always registered and does not read the Smart Router project preference.
Its billing step makes the same local, token-free account reads Smart Router makes
(the OpenCode credential listing and, for Claude, the native usage read), only when
the user asks for a council; the README says so. The ChatGPT limit pause is recorded
only while Smart Router is on, so with it off a limited Astra seat is found at dispatch
and handled as a member failure.

Checked on 2026-11-21 with the `models` search tool: `openai/gpt-6.2-astra`,
`claude-agent/opus` and `claude-agent/claude-fable-5-2` are listed with no per-token
cost, and the same models also appear under `opencode/` and `openrouter/` with
per-token prices. The live host check later showed that the search listing is not
proof of a route: OpenCode 2.2.6's own model list has no `openai/gpt-6.2-astra`, and a
direct run failed with `provider.no-route`. See `docs/development.md`.

### 4. Session records

Each run lives in
`<project folder>/docs/vibe-council/sessions/<hash12>_<context>/`:

- `brief.md`: the exact bytes every member read.
- `answers/<seat>.md`: each member's raw answer (round 2 answers as `<seat>-r2.md`).
- `result.md`: date, members (seat, model ID, display name, variant, paid overflow),
  the brief's full SHA-256, the vote table, tally, compounding insight, keep and defer
  lists, the questions asked and the user's decision.

`<hash12>` is the first 12 hex digits of the brief's SHA-256. `<context>` is a short
lowercase ASCII slug of the topic (at most 40 characters, `[a-z0-9-]`).

The record follows the work (chosen by the user, 2026-11-21). The project folder is
`git rev-parse --show-toplevel` of the checkout the work happens in:

- In a task worktree, the record goes there and travels with the branch and its pull
  request.
- In the main checkout with the project's git workflow on, the coordinator first moves
  to a task worktree, as the `vibe-pull-requests` rules already require for any file
  write, and the record ships through a pull request like other work.
- With the git workflow off or not onboarded, the record goes in the current checkout.
- Outside git, the session directory is used.

Files are committed with the work; users who prefer not to can ignore the folder.

When the run settles a spec or decision document, the coordinator adds a one-line link
to `result.md` there.

## The process (procedure.md)

1. **Collect items.** Stable IDs (R1, M1, F1…). Each item: one or two lines, a
   direction tag (neutral, stricter, looser, pessimistic, optimistic) and its
   consequence if wrong. Above about 30 items, split into batches.
2. **Measure before asking.** Where a fact would change votes, measure it first and put
   the number in the brief.
3. **Check the roster.** Call `vibe_council_roster`. Any seat not `ok` stops the run:
   ask, and offer listed candidates only as options. When every seat is `ok`, show the
   roster (seat, model ID, name, paid overflow) in the same message that starts the run,
   without waiting for a reply: paid overflow is reported, never a reason to stop, as in
   Smart Router since PR #350.
4. **Write the brief** from the template into the session folder; record its hash.
5. **Dispatch** every member in parallel with the subagent tool, `agent: "explore"`
   and `model: "<provider/id>"`, or `"<provider/id>#<variant>"` when a variant is set.
   All get the same one-line prompt: "Read <absolute brief path> and follow it exactly.
   Answer in your final message." No member sees another's answer. The native `explore`
   agent denies edits, writes and subagents and asks before reading `.env` files
   (OpenCode 2.2.6); it still allows shell, so "no Git writes" rests on the brief.
6. **Save answers** to `answers/`.
7. **Tally** each item: unanimous, majority (minority noted) or split. When a majority
   says CHANGE but differs on how, pick the simplest variant and flag it as the
   coordinator's own call. A missing line is "no vote".
8. **Present:** a compact table (item → decision → one vote column per seat), the keep
   and defer lists, the shared compounding insight, and one or two questions only where
   the user must decide. State that the coordinator did not vote.
9. **Round 2,** only when the user asks, and only for 1–1–1 splits or items the user
   names: anonymised positions and reasons, one final vote, at most one extra round.
10. **Record** `result.md` with the user's decision.

Default roster: Astra `openai/gpt-6.2-astra`, Opus `claude-agent/opus`, Fable
`claude-agent/claude-fable-5-2`. Three seats so majorities exist; vendor variety matters
more than count.

## Brief template (brief-template.md)

About 1,500 to 3,000 words, sections in this order:

1. Role line: "You are one of <N> council members (<seat names>). Answer
   independently." It never names the member's own seat, since one `brief.md` is shared
   and hashed. A round 2 prompt is saved as `brief-r2.md` and lists the session's
   `answers/` folder as forbidden.
2. The user's standing instructions, verbatim.
3. Bias prompt: weigh optimistic bias as well as pessimistic bias.
4. Context, half a page at most; point to files, do not paste them.
5. Settled rules, not up for a vote; members may comment on how they compound.
6. Measured facts, with numbers.
7. Items: ID, decision, direction tag, consequence.
8. Output format, strict: one line per item
   `<ID>: KEEP | CHANGE: <simplest change> | DROP | DEFER — <short reason>`, then
   "Compounding" (2 to 4 lines), then "Top 3"; at most 500 words.
9. Constraints: read-only; no edits, Git writes or subagents; the forbidden paths
   (secrets, credentials, private data; the coordinator lists project-specific ones);
   network only if needed; about 15 tool calls.

Never put secrets or personal data in a brief.

## Failure handling

- A member fails or times out: report it, tally the rest. With two votes left, a tie
  goes to the user.
- Malformed answer: take the lines that parse; show the rest as "no vote". Re-prompt
  that member at most once.
- Roster tool unavailable (Python missing, timeout): stop and tell the user; do not
  dispatch unchecked seats.
- Observed cost: about 3 to 8 minutes of wall time for three members at about 15 tool
  calls each.

## Tests

- Roster: exact match passes; unknown model returns candidates and keeps the input ID;
  provider not on plan; unknown variant; `billing_unverified`, `quota_exhausted` and
  each `paid_overflow` value from a stubbed source; seat count and duplicate names
  rejected; `ready` false when any seat is not `ok`; the false authority fields.
- Plugin: skill and tool registered; command registered when the host supports it and
  skipped without error when it does not.
- `select.py --council-roster`: bounded input and fixed fallback on error, as the other
  operations.
- Package: `scripts/manifest.py` lists the new skill files; `validate.py` and
  `package.py` pass.
- Dry run with stubbed members: a fixture of three answers (unanimous, majority, split
  and missing lines) and the expected `result.md` table, checked by reading, since
  tallying stays in prose.

## Documentation

- README: a short "Ask the council" section, written through the `impeccable`
  skill, as the repository requires.
- `PRODUCT.md`: move the council into "Available now".

## Left to the builder

- Exact JSON field names in the tool output beyond those named here.
- How the coordinator computes the hash (a shell `shasum -a 256` is fine) and the
  folder-naming steps (write the brief, hash it, then name the folder).
- Wording in the skill references, within the rules above.
