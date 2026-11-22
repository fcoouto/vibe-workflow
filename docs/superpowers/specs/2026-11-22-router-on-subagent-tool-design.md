# Router instruction on the subagent tool, with an injection tripwire

## Intent

Smart Router tells agents to consult `smart_router_select` before delegating. Today
that instruction travels two ways (`index.js` context hook): pushed into the system
prompt on every step for every provider, and put as a durable session instruction entry
(`vibe-workflow-smart-router`) for `claude-agent` sessions, because `claude-agent`
drops pushed system text. No channel reaches a `claude-agent` session's first model call.

PR #366 found a channel that does: the `subagent` tool's `model` parameter description,
edited in the same `context` hook, reaches `claude-agent`, `openai` and `opencode-go`
on the first call, whole (the `claude-agent` bridge cuts only the tool description).
The subagent model note already rides there. The user chose to make it the router
instruction's only channel too, and asked for a test that tells them, after a local
OpenCode update, whether agents still receive the injected text, which agents can
check and re-verify on their own.

The council session `docs/vibe-council/sessions/a129ba8dd79c_router-instruction-delivery/`
chose push plus a `claude-agent` entry because "No option reaches a `claude-agent`
session's first model call; only an upstream change can, and it would let the entry
code be deleted." This design acts on new evidence the council did not have; it does
not dispute its reasoning.

## Goals

- One stateless channel for both injected texts: the `subagent` tool's `model`
  parameter description.
- Delete the system-prompt push of the router instruction, the session entry, and their
  tracking.
- A suite test that fails as soon as the local OpenCode version differs from the last
  version verified live, and a live check that agents may run without asking to clear
  it.

## Non-goals

- The pull request fallback push (`index.js`, "open pull requests as [BLOCKED] drafts…")
  and the skill-load and command notes keep their channels.
- No CI, release process or change to onboarding.
- No change to what Smart Router selects.

## Anti-goals

- The default suite must never run inference.
- A failing live check must never record a version as verified.
- No instruction may grant launch or spending authority.

## Approaches

- **Only channel (chosen by the user).** The parameter carries the router text when
  Smart Router is on; push and entry are deleted. One path, lightest code.
- Parameter plus the old channels as backup (rejected): text twice or three times per
  session, and no code goes away.

For the test: **a version tripwire plus a live check (chosen)**; a live check inside
the default suite (rejected: inference on every run, breaks the AGENTS.md rule); a live
check run by hand only (rejected: nothing reminds anyone after an update).

## Behavior

### Injection

- `ROUTER_NOTE`, appended after `MODEL_NOTE` (one space between parts), only when
  Smart Router's state is `on`, exactly:

  > Smart Router is on: when no exact model is named, consult smart_router_select first,
  > describing the task by its facts: files, spec, risk. If it has no recommendation,
  > show its notice in chat when present. Do not retry automatically or override a
  > native denial; routing grants no launch or spending authority.

- The `model` description is then: OpenCode's own text, `" " + MODEL_NOTE` when
  onboarded, `" " + ROUTER_NOTE` when Smart Router is on. Both notes are stripped
  before deciding, so a reused schema never doubles them and turning the router off
  removes its note on the next step. Every other rule of the existing `noteModel`
  holds: missing path or non-string description is a no-op, writes never throw,
  nothing changes after unload, including an unload during the settings read.
- `ROUTER_INSTRUCTION`, `ROUTER_ENTRY`, the `entries` map, `drop`, `entry`, the system
  push of the router instruction, and the unload loop over entries are deleted.
- The `context` hook keeps the PR fallback push and its existing order.

### Stale entries

Sessions that got the old entry keep it after the update (entries are durable). Builder
check may settle the exact form; the default: once per session per plugin load, for a
`claude-agent` session or one with no provider, call
`ctx.session.instructions.entry.remove({ sessionID, key: "vibe-workflow-smart-router" })`
without awaiting its result and never throwing, tracking removed sessions in a set capped
at 128 like the old map. Hosts without the entry API skip it.

### Version tripwire

- A tracked record, `tests/opencode-verified.json`, shaped
  `{"version": "<x.y.z>", "verified": "<YYYY-MM-DD>", "models": ["claude-agent/haiku", "openai/gpt-6.1-terra", "opencode-go/claude-haiku-5-5"]}`.
  Not exported (`scripts/manifest.py` stays unchanged). Only a passing live check
  writes `version` and `verified` (builder-check call: no hand-written first record;
  PR #366's probes used a marker and `claude-agent/sonnet`, not these notes and models).
  The implementation ships the record produced by its own live check run; before that
  run the record holds only `models`.
- `tests/test_opencode_version.py`: skips when `opencode` is not on `PATH`; otherwise
  runs `opencode --version` (prints `opencode v2.3.2`), and fails when the record has no
  `version` or its version differs from the installed one. The failure message names the installed and recorded versions and the
  command to run: `python3 -B scripts/live_check.py --injection`.
- The record must also be refreshed when the injected texts or the hook change: plans
  that touch them include a live check step.

### Live check

- `python3 -B scripts/live_check.py --injection` reuses `prepare()` (throwaway clone with
  the packaged plugin, renamed id) with settings that turn Smart Router on, then for each
  model in the record runs one first-call prompt with no tools:
  "Do not call any tools. Quote verbatim the description of the model parameter in your
  subagent tool's input schema." (via `opencode run --standalone --archived -m <model>`
  in the clone, the form the probes used).
- Isolation (builder-check call): the run must load only the checkout's packaged
  plugin, never the user's installed copy, which could carry the same notes and make a
  broken checkout pass. Run it with a private, empty OpenCode config directory (as the
  council live check did), keeping the user's own logins. If a model cannot run that
  way, it fails; the check never falls back to the user's config.
- A model passes when its reply contains `MODEL_NOTE` and `ROUTER_NOTE` verbatim,
  after normalizing escaped quotes and whitespace, and its run made no tool call
  (builder-check call: a tool call fails that model and is reported; the prompt plus
  this check is the guard, since blocking tools could also hide the schema).
- It prints a JSON summary per model (pass, and the first 200 characters of a failing
  reply). Only when every model passes does it rewrite the record's `version` (from
  `opencode --version`) and `verified` (today); the agent commits that change. Any
  failure leaves the record untouched and exits nonzero.
- The clone and sessions are removed afterwards, as `check()` does today.

### Authorization

- The user pre-authorizes `scripts/live_check.py --injection` for agents, with no
  further question, when `tests/test_opencode_version.py` fails or a plan touching the
  injection calls for it. Scope: one short first-call prompt per recorded model, no
  tools, on the user's subscription logins. Other live checks still need explicit
  authorization. Paid overflow follows each account's own setting, as in any session;
  the check adds no paid provider.
- Recorded as one line in `AGENTS.md` next to the inference rule, and in
  `docs/development.md`.
- When the live check fails, the agent stops and tells the user: agents no longer
  receive the notes on that OpenCode version.

## Constraints

- OpenCode only; text lives in `index.js`; `skills/` and `runtime/` unchanged.
- Python 3.11 standard library; Node ES modules; no new dependencies.
- Tests first for behavior changes; validate, commit, then run affected tests.

## Docs

- `docs/development.md`: "Context instructions on claude-agent" records that the push
  and entry were replaced, why (PR #366's probes), and the stale-entry removal; the
  "Subagent model note" section covers the router note, the tripwire and the live
  check; the live check line in the intro mentions `--injection` and its
  pre-authorization.
- `README.md` unchanged unless user-visible behavior changes (it does not).
- The memory vault fact `opencode-claude-agent-instructions` is updated through its
  `INSTRUCTIONS.md` policy: the tool schema reaches every provider from the first call.

## Left to the builder

- Test names and fixture layout, the normalizing function, how `live_check.py` runs
  `opencode run` and parses its output, and the summary's exact JSON keys.

## Amended after the final review (2026-11-22)

- Leftover entries are removed once per session on any provider (the old plugin kept the
  entry after a session left `claude-agent`), still only where Smart Router has a saved choice.
- The live check drops every `OPENCODE*` variable, sets `PWD` to the clone (OpenCode reads
  it instead of the process's working directory), gives `opencode run` empty stdin, merges
  streamed text parts, deletes each run's session, and refuses a record whose models are not
  exactly the approved three (`APPROVED_MODELS`).

## Shell-note probe (2026-11-22)

Approved by the user after #371: the event notes (PR and skill-load notes, added to tool results by the
`execute.after` hook) had no live check, so an OpenCode update that broke that hook would pass
`--injection`. The check now also runs one prompt on `claude-agent/haiku` that may make exactly one
shell call, `git switch -c vibe-injection-probe` in the throwaway clone, and requires the model to quote
the primary-checkout note. A failing probe blocks the record like a failing model. This is detection
only; no fallback delivery was added.
