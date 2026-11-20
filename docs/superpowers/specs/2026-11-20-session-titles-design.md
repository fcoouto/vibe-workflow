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
  a three-step meter, and the words `WAITING` only while the session is idle with a hand-off outstanding:
  `▰▱▱▱▱ session titles` (design written), `▰▰▱▱▱ session titles` (plan written), `▰▰▰▱▱ session titles` (plan executing); when the session goes idle at one or two cells it reads
  `▰▱▱▱▱ WAITING · session titles`. Each title has the invisible `U+FFFB` in front.
- The meter changes **only** at these events, as the user specified:
  1. a design is generated (a spec file is written) -> `▰▱▱▱▱`
  2. an implementation plan is generated (a plan file is written) -> `▰▰▱▱▱`
  3. implementation of the plan starts (an executor skill is loaded) -> `▰▰▰▱▱`
- The `WAITING · ` label is **not** tied to those writes: the user may have said
  "once validated, proceed with implementation", in which case nobody owes an approval
  (correction from the user). It is derived from the session's own state: shown when the
  session goes idle (`session.idle`) with the meter at one or two cells, because then
  the hand-off is outstanding and only a user prompt can move it; removed when the next
  turn starts (`session.execution.started`) or the executor loads.
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
`WAITING`; everything else is the meter alone. `BLOCKED` is not a title state (see
"Decisions"). The stage is the count of filled cells. `WAITING` means "idle with a
design or plan hand-off outstanding"; it is never written by the artifact events.

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
- **Label events.** `session.idle` adds the label when the session already carries our
  meter at 1 or 2 cells; `session.execution.started` removes it. A session without our
  meter is never labeled. Both events are filtered by the same guards as the meter
  (opt-in, root session, user-removed prefix).
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

## Decisions (LLM Council, 2026-11-20: Astra, Fable 5.2, Opus 5; unanimous on 1 and 2)

1. **No `BLOCKED`.** The earlier plugin retired it on 9 November 2026 (it became
   `WAITING`) because it collided with the `[BLOCKED]` pull request prefix, where it is
   the normal in-progress state. "User must act" is already `WAITING`. No host signal
   observes an external block; only the model knows, which is the cooperation protocol
   this repository dropped. The title never shows it and the read regex does not accept it.
2. **No permission or question triggers.** Pending permissions and forms are transient
   and noisy; rejected by all three members.
3. **Superseded by the user's correction (council round 1, 2): `WAITING` is not set by
   the design and plan writes.** The council had it set by the writes and accepted a
   stale label; the user pointed out that a pre-authorized "validate, then proceed"
   flow owes no approval at all. The label is now derived from idle state and stage (see
   Goals). This reintroduces `session.idle` as a trigger, which the council had
   rejected only as a stand-alone signal; combined with "the meter shows an outstanding
   hand-off" it is accurate, and it adds two events, no storage and no polling.
   Awaiting the user's approval of that change.
4. **The read-then-write rename race is accepted** by the user (the council split:
   Fable accept, Opus accept plus a readback, Astra refuse without an atomic update).
   The host has no compare-and-set title update and the window is milliseconds. No
   readback is built.

## Keeping the title current (best effort, no guarantee)

A strict guarantee is not possible: the plugin only sees what the host emits, and
subscriptions are live only. These mechanisms give best effort, in order of how much
they carry:

1. **Trace events** (`execute.after`): the meter moves at the design write, the plan
   write and the executor load.
2. **The title is the state.** Each update is recomputed from the current title plus the
   event, so it is idempotent and nothing can drift out of sync with a stored copy; a
   server restart loses only the in-memory "we renamed this" record.
3. **Turn boundaries** (`session.idle`, `session.execution.started`): the `WAITING`
   label is reconciled once per turn, which also repairs a label left wrong by a
   missed event.
4. **Self-repair, no queue.** A failed or timed-out rename is dropped silently; the next
   event recomputes and applies it. Nothing retries in the background.
5. **Guards** keep it honest: root sessions only, opt-in, a removed prefix stops
   renaming of that session, an unchanged title writes nothing.

Known gaps, documented rather than closed: plans written by shell redirect or
`apply_patch`; skills the user invokes directly may leave no tool call; sessions that
never pass through superpowers; a finished build keeps `▰▰▰▱▱` because there is no
`DONE`; an idle session with a 3-cell meter shows no label even if it stopped
mid-build; the rename race; events dropped while the plugin reloads.

## Open points for the reviewer (author's calls, not user decisions)

- **`WAITING` from idle:** see Decisions 3, awaiting the user's approval.
- Meter steps 1/2/3 of 5 per stage (author's call).
- Opt-in through onboarding (default off) rather than on by default.
- Latest event wins, except that a plan does not replace `BUILD` (added after review).
- Gate: onboarding feature (recommended default here, one question) or a plugin option `ctx.options.session_titles` (about 60 fewer changed lines, but no per-project choice and no in-chat control).
- Unverified on the installed host: whether `session.idle` and `session.execution.started` carry a session id and fire as the API reference lists them,  the `skill` tool's name and input shape in
  `execute.after`, the `write` input field (`path` or `filePath`), the `parentID` and
  `title` fields of `ctx.session.get`, whether OpenCode's own title generation can
  overwrite a plugin-set title, and whether the sidebar refreshes at once. The plan
  verifies them before any claim of working.
