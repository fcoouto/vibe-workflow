# Session titles

## Status and intent

The user asked to bring the idea of the earlier plugin's "Title Sync" into this
repository, redesigned and as thin as possible. The idea to keep: the **status of a
session lives in its name**, so a list of chats shows where each one stands. This
document does not authorize implementing or installing anything.

Why a redesign, not a port: the earlier plugin competed with partners. It kept its
own task database, milestone weights, a five-cell meter, attention labels, a manual
edit "pause", a per-thread lock, baseline and readback bookkeeping, and a proof-token
shell script to rename an OpenCode session (about 840 lines of runtime, script and
protocol text). It needed all of that because it had to *infer* the status itself and
rely on the model following a long protocol.

This plugin improves what works. The partner workflow (superpowers) already moves
through well-defined stages and leaves deterministic traces. We watch the traces and
rename. Nothing else.

## What exists now (researched 2026-11-20)

- The plugin already registers an `execute.after` tool hook (`pr-advice.js`). The hook
  fires for every tool call of every session and was observed live on OpenCode 2.2.6
  (`docs/development.md`).
- A V2 server plugin can rename a session in-process: `ctx.session.update({ sessionID,
  title })`, and read one with `ctx.session.get({ sessionID })`. No shell, no proof
  token, no `opencode api`, no model turn.
- Superpowers leaves two fixed traces: the design is written to
  `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`, the implementation plan to
  `docs/superpowers/plans/YYYY-MM-DD-<topic>.md`. Execution starts when the session
  loads the `subagent-driven-development` skill (subagents) or the `executing-plans`
  skill (inline, the "native" path).
- Settings live in `.vibe-workflow/settings.json`, written only by the confirmed
  `vibe-onboard-project` flow. Visible side effects on the user's chats follow the
  same opt-in rule as the other features.

## Goals

- A session's name starts with its stage while superpowers work is under way:
  a three-step meter, and the words `WAITING` only while the user owes an
  approval: `▰▱▱▱▱ WAITING · session titles` (design written), `▰▰▱▱▱ WAITING · session titles` (plan written), `▰▰▰▱▱ session titles` (plan executing), each with the invisible `U+FFFB` in front.
- The name changes **only** at these events, as the user specified:
  1. a design is generated (a spec file is written) -> `▰▱▱▱▱ WAITING ·` (superpowers now asks the user to approve it)
  2. an implementation plan is generated (a plan file is written) -> `▰▰▱▱▱ WAITING ·` (the user picks how to execute)
  3. implementation of the plan starts (an executor skill is loaded) -> `▰▰▰▱▱`, no label
- The latest event wins, with one exception: a plan written while the session shows
  a 3-cell meter keeps it (usually a fix mid-build). A new design in a building
  session shows one cell again, which is correct: that session is designing again.
- No model calls, no polling, no database, no new runtime process, no new
  dependency.

## Non-goals and anti-goals

- No percentage, milestones or weights: the meter is three fixed steps, not measured
  progress, and cells 4 and 5 stay empty (room for review and merge later). No
  review/paused/done labels. Not even `DONE`: a merge is the pull request feature's event, and the user did not ask for it
  here. A later request can add it by observing the existing `gh pr merge` signal.
- No naming for work that never passes through these superpowers traces (for example
  "fix this bug" with no plan). The earlier plugin's "automatic implementation naming"
  is deliberately dropped.
- Anti-goal: overwriting what the user typed. The plugin only swaps its own prefix.
- Anti-goal: renaming a worker's or reviewer's session. Only a root session is renamed.
- Not a status source of truth. The title is a display of the last event.

## Behavior

Title format (as in the earlier plugin, including its leading `U+FFFB`, an invisible
character that keeps the meter at a stable width; corrected after the user pointed out
an earlier draft had dropped it): `<U+FFFB><5 meter cells> <topic>`, or
`<U+FFFB><5 meter cells> WAITING · <topic>`. Reading accepts titles without the mark. Text appears only for
`WAITING` (and `BLOCKED`, still undecided); everything else is the meter alone. The
stage is the count of filled cells. `WAITING` here means "the user owes the next
action", which is true by construction after a design (approval) and a plan (execution
choice) and clears when the next event arrives.

| Event (tool, completed) | Stage |
|---|---|
| `write` of a path ending `docs/superpowers/specs/*.md` | `DESIGN` |
| `write` of a path ending `docs/superpowers/plans/*.md` | `PLAN` |
| `skill` load of `subagent-driven-development` or `executing-plans` | `BUILD` |

- Only `write` counts for files. `edit` is ignored, because executors tick plan
  checkboxes with edits and that must not move `BUILD` back to `PLAN`.
- **Topic.** Keep whatever follows the current prefix, so a topic the user edited
  survives. If the title is empty or still OpenCode's default (`New session - <time>`),
  derive the topic from the file name (strip the date and a trailing `-design`, turn
  dashes into spaces). With no topic available (a skill event on a default title),
  write nothing.
- **State is the title.** The current stage is read from the prefix of the current
  title; nothing is stored, except a small in-memory record of sessions the plugin has
  renamed.
- **User override.** If the plugin renamed a session and its title no longer carries
  a prefix, the user removed or replaced it: the plugin leaves that session alone
  until the server restarts (the record is in memory; after a restart the prefix can
  return once, and onboarding says so).
- **Same title, no write.** An event that produces the current title does nothing.
- **Failure is silent.** Any error (missing field, failed call) is swallowed; the
  tool result and the chat are never touched or delayed by naming.
- **Root sessions only.** A session with a parent is skipped.
- **Opt-in.** A `session_titles` feature, `{"version":1,"features":[{"session_titles":{"enabled":true}}]}`
  entry, written by onboarding at the main project path. Not onboarded or off means no hook work and no calls.

## Approaches

1. **Observe superpowers' traces in the plugin hook (chosen, recommended).**
   About 70 lines in one new module, reuses the hook mechanism and settings system
   already in the repository. Deterministic, no model cooperation. Trade-off: it only
   recognizes the superpowers paths and skill names, and a plan written by a shell
   redirect (`cat > docs/...`) is missed.
2. **Instruct the agent to rename via a skill.** No code, but depends on the model
   remembering at the right moments and calling `session_rename` through the shell.
   It repeats the earlier plugin's weakness (a long protocol the model must follow)
   and spends tokens on every change.
3. **Port the earlier runtime thinly** (keep its states and manual-edit pause, drop
   the meter). Still needs its own task state to know the stage, which is exactly the
   competing machinery this repository was restarted to avoid.

The user set the shape (rename only at plan generated and plan started); approach 1
follows from it. Choice recorded as the author's call; open to correction.

## Constraints

- Advisory-only posture of the repository holds: the plugin never starts a model
  turn, launches work, or touches anything but a session title.
- Python 3.11+ standard library and Node ES modules only; no install or build step.
- A new exported file goes in `scripts/manifest.py`, `package.json` `files` and
  `tests/test_package.py`.
- Preserve credentials, native configuration and other plugins.

## Open points for the reviewer (author's calls, not user decisions)

- **`BLOCKED`:** no deterministic trace exists for it (it needs the agent to say it is
  blocked, which is model cooperation). v1 builds `WAITING` only; the user is asked
  what should mean `BLOCKED`.
- Meter steps 1/2/3 of 5 per stage (author's call).
- Opt-in through onboarding (default off) rather than on by default.
- Latest event wins, except that a plan does not replace `BUILD` (added after review).
- Gate: onboarding feature (recommended default here, one question) or a plugin option `ctx.options.session_titles` (about 60 fewer changed lines, but no per-project choice and no in-chat control).
- Unverified on the installed host: the `skill` tool's name and input shape in
  `execute.after`, the `write` input field (`path` or `filePath`), the `parentID` and
  `title` fields of `ctx.session.get`, whether OpenCode's own title generation can
  overwrite a plugin-set title, and whether the sidebar refreshes at once. The plan
  verifies them before any claim of working.
