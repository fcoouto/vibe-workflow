# The council procedure

The council is a small panel of frontier models from different vendors. Each member
reviews the same items on its own and votes per item. You tally the votes into one
recommendation and the user decides. Run it only when the user asks. Never vote on
decisions you wrote, and never override an earlier user decision: show anything that
would reverse one as a question.

## Seats

There is no fixed roster. Unless the user names the seats, `vibe_council_roster`
picks them at council time from the plan models available now, using the same
scores and account checks as Smart Router:

1. frontier models (within 90% of the best score), one per lab, strongest first;
2. more frontier models, even from a lab already seated;
3. the strongest models below the frontier.

Different labs are preferred, never required. A model reachable through two plans
takes one seat. Three seats by default, so majorities exist. The user may ask for
one to five seats, or name every seat ("ask Astra and Opus"): unique names,
`provider/id` models and an optional variant. Named seats are never mixed with
picked ones.

## Steps

1. **Collect items.** Give each a stable ID (R1, M1, F1…). Write one or two lines, a
   direction tag (neutral, stricter, looser, pessimistic, optimistic) and the
   consequence if the item is wrong. Above about 30 items, split into batches.
2. **Measure before asking.** Where a fact would change votes, measure it first and
   put the number in the brief.
3. **Get the roster.** Call `vibe_council_roster`:
   - No seats named: `{"v":1}`, or `{"v":1,"count":N}` when the user asks for a
     size. The answer is already checked. Each seat also carries `score` and
     `creator` (its lab).
   - Seats named: `{"v":1,"seats":[{"seat":"<Name>","model":"<provider/id>","variant":null}, …]}`.
     Never substitute a named model.
   - With `fewer_seats`, show the seats found and ask whether to run with them or
     name the rest. To run with them, call `{"v":1,"count":<found>}` and use that
     answer.
   - If `ready` is false, or any seat is not `ok`, stop and ask the user. Statuses:
     `unknown_model`, `unknown_variant`, `provider_not_on_plan`, `billing_unverified`,
     `quota_exhausted`, `unchecked`. Offer a seat's `candidates` only as options.
     Never substitute a model yourself.
   - If the tool returns `ready` false with a reason (`invalid_request`,
     `models_unavailable`, `roster_unavailable`, or for a pick `no_models`,
     `no_eligible_route`, `scores_unavailable`, `unscored`, `no_recommendation`), or
     is unavailable (Python missing, timeout), stop and tell the user. Do not
     dispatch unchecked seats.
   - When every seat is `ok`, show the roster as this table in the same message
     that starts the run, before you dispatch anyone:

     | Seat | Model ID | Name | Variant | Paid overflow | Lab | Score |
     | --- | --- | --- | --- | --- | --- | --- |

     Lab and Score come from a picked roster (`creator`, `score`); write `—` for
     named seats. Under a picked roster, add the credit
     "Scores: Artificial Analysis via OpenRouter". Do not wait for a reply.
     A paid overflow value is reported, never a reason to stop.
   - After any seat change (the user picks a replacement or a candidate),
     call `vibe_council_roster` again. Dispatch only the exact `model` and `variant`
     values from an answer with `ready: true`.
4. **Write the brief.** Use [the brief template](brief-template.md). Pick the record
   location first (see Where the record goes). Then:
   1. Write the brief to `docs/vibe-council/sessions/draft_<context>/brief.md`.
      `<context>` is a short lowercase slug of the topic: at most 40 characters,
      `[a-z0-9-]`.
   2. Run `shasum -a 256` on it. Keep the full hash.
   3. Rename the folder to `<hash12>_<context>`, where `<hash12>` is the first 12 hex
      digits of the hash.

   The brief never contains its own folder path. Never put secrets or personal data
   in it.
5. **Dispatch** every member in parallel with the `subagent` tool:
   - `agent: "explore"`
   - `model: "<provider/id>"`, or `"<provider/id>#<variant>"` when a variant is set
     (the `#<variant>` suffix)
   - prompt: `Read <absolute brief path> and follow it exactly. Answer in your final message.`

   Every member gets the same prompt. No member sees another's answer. The native
   `explore` agent denies edits, writes and subagents and asks before reading `.env`
   files, but it still allows shell. "No Git writes" rests on the brief.
6. **Save answers** to `answers/<seat>.md`. Use the lowercased seat name with spaces
   turned into `-`; seat names that give the same file name count as the same seat.
   Round 2 answers go to `answers/<seat>-r2.md`.
7. **Tally** each item: unanimous, majority (note the minority) or split. When a
   majority says CHANGE but differs on how, pick the simplest variant and flag it as
   your own call. A missing line is "no vote".
8. **Present** a compact table (item → decision → one vote column per seat), the keep
   and defer lists, the shared compounding insight, and one or two questions only
   where the user must decide. State that you did not vote.
9. **Round 2,** only when the user asks, and only for 1–1–1 splits or items the user
   names. Write the prompt to `brief-r2.md` in the session folder, with anonymised
   positions and reasons, and a forbidden-path line for the session's `answers/`
   folder. Ask for one final vote and run at most one extra round.
10. **Record** `result.md` from [the record template](record-template.md) with the
    user's decision. When the run settles a spec or decision document, add a
    one-line link to `result.md` there.

## Where the record goes

The record follows the work. The project folder is `git rev-parse --show-toplevel`
of the checkout the work happens in.

- In a task worktree: the record goes there.
- In the main checkout with the project's git workflow on: move to a task worktree
  first, as the `vibe-pull-requests` rules require for any file write. The record
  ships through a pull request like other work.
- With the git workflow off or not onboarded: the record goes in the current checkout.
- Outside git: use the session directory.

Files are committed with the work. Users who prefer not to can ignore the folder.

## Failure handling

- A member fails or times out: report it and tally the rest. With two votes left, a
  tie goes to the user.
- Malformed answer: take the lines that parse and show the rest as "no vote".
  Re-prompt that member at most once.
- A member did not vote on an item: say so in the table; it is not a KEEP.
- Expect about 3 to 8 minutes of wall time for three members at about 15 tool calls
  each.
