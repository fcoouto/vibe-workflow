# Smart Routing worker choices

A worker choice is a Smart Routing setting, not API Usage. Reading or setting
one needs no API setup, Keychain connection, activation, enablement or canary,
and it authorizes no spending. Change it only on the user's explicit request.

1. From the package root, read the current settings and their revision. Each
   command reads one versioned JSON request on standard input:

   ```sh
   printf '%s' '{"v":1}' | python3 -I -B scripts/routing.py status --project /absolute/project/path
   ```

   Report `settings.worker_choices`, `settings_revision` and any
   `settings_warnings`.
2. Write the complete `worker_choices` map with the one-shot command, using
   that `settings_revision` as `expected_revision`:

   ```sh
   printf '%s' '{"v":1,"expected_revision":<revision>,"change":{"worker_choices":<map>}}' \
     | python3 -I -B scripts/routing.py set --project /absolute/project/path
   ```

   The answer is `updated` with the new revision and settings, or `blocked`
   with a reason and nothing written. On `stale_revision`, read `status` again
   and repeat the same requested change once.

## The map

- Each key is a routed specialist id from the roster in
  [the task profile template](../../../templates/task-profile.json), such as
  `software-engineer` or `code-reviewer`. The `orchestrator` is never routed.
  The orchestrator, an old role name or any other key is refused with
  `invalid_worker_choices`.
- Each value is a list, in order of preference, of at most 16 entries without
  duplicates. A longer list gives `invalid_worker_choices`; a repeated entry
  gives `duplicate_worker_choice`.
- Each entry has exactly the keys `provider`, `model`, `effort`, `harness` and
  `funding`. Each value is a nonempty printable string of at most 256
  characters, and `funding` is `subscription` or `api`. Any other entry gives
  `invalid_worker_choice`.
- Take each identity field from a source that shows it. Admission and launch
  replies name only the model and effort, never `provider` or `harness`.
  - `harness` and `provider`: the path for the client that launches the worker
    in the shipped path rules, `runtime/routing_routes.json` in the package.
    Codex native subagents use harness `codex-native` and provider `openai`.
  - All four fields together: copy one strategy from the compiled table,
    `strategy-table.json` beside the routing settings file. Its `model`,
    `effort`, `harness` and `provider` are the values routing compares. Keep
    a nonempty `effort` exactly. Where the strategy's `effort` is `null` or
    absent, the model has no effort control and the table stores no text:
    write the text `none` in the choice, as the settings schema requires a
    string. Routing matches it to that strategy's route, and the launch then
    sends no effort.
  - Anything those files do not show, such as a model missing from the table
    or a client with no path, is unavailable: ask the user for it, from the
    launching client's own model list. Never infer a field from a model name or
    write vendor model names from memory, and never sign in, connect or
    activate a client to discover one.

  A choice that matches no available route makes routing answer
  `worker_choice_unavailable`, never a substitute.
- `funding: api` only selects an API-funded route that the existing API Usage
  enablement, funding and caps already admit. It never enables API Usage, buys
  credit or authorizes a paid fallback.

The change replaces the whole map and leaves every other setting unchanged, so
include each specialist key to keep. A key written before the roster stays in
the file unused, and `status` and `check` list it in `settings_warnings` as
`worker_choice_key_unknown`. A new map cannot contain it, so writing the map
removes it: tell the user first, and re-key it only when they ask.
