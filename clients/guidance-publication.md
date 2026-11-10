# Guidance cache publication

Load only when refreshing the local platform guidance cache. Follow the refresh
triggers and authorization rules in [Agent Delegation](../policies/03-agent-delegation.md#select-capability-and-dispatch).
Ordinary compatible cache reuse needs no refresh or source lookup.

Every request contains `client_surface` and
`applicability:{client_version,capability_fingerprint}`. The CLI supports
`codex-cli` and `codex-desktop`. Client versions are nonempty strings up to 128
characters; the capability fingerprint is lowercase SHA-256. Compatibility uses
the observed capability fingerprint, not version-string equality.

Status optionally accepts `profile_ids`. Claim adds `trigger:{kind,id}` with
`kind` equal to `routine`, `manual` or `maintenance`, a nonempty ID up to 128
characters, and optional `lease_seconds` from 1 through 600. Reuse the same
trigger for a failed refresh; do not invent retries.

Record requires the actual claim `token`, positive `generation`,
`coordinator_ref`, and exactly one of `failure` or `guidance`. An expired or
superseded claim cannot publish. A failed refresh retains the last verified copy.
The guidance object has exactly:

- `schema_version:1`, matching `client_surface` and compatible `applicability`.
- `checked_at`: UTC ISO timestamp `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`, not in the future.
- `sources`: 1–16 objects `{id,url,sha256}`. IDs are unique, nonempty and at most
  64 characters. URLs are HTTPS, at most 2,048 characters, with a hostname,
  no credentials or fragment, and no port other than 443. Digests are SHA-256.
- `recommendations`: at most 32 objects with unique `id` (64 characters), `work`
  and `risk` (1,024 characters each), `models`, `conditions`, `limitations`,
  and nonempty `source_ids` referencing declared sources.
- Each model is `{model,efforts}`. Model names are unique per recommendation,
  nonempty and at most 128 characters. There are at most 16 models and 16
  unique efforts per model; effort strings are at most 64 characters.
- `conditions`, `limitations` and `source_ids` each contain at most 16 unique,
  nonempty strings of at most 512 characters.

The guidance/request limit is 64 KiB; unknown fields reject. Verify sources and
applicability independently. A cache record establishes neither model
availability nor execution authority. Keep requested, configured and observed
settings separate, and preserve the coordinator's model and authentication.

## Strategy table refresh

After a successful guidance record, or a relevant catalog or benchmark change, a
maintenance run recompiles the Smart Routing strategy table. Run it only during
that maintenance session, never from a spawn path, hook or startup check. Extract
prose guidance into structured rows first and save the four inputs as local JSON
files in the shapes `runtime/routing_evidence.py` documents, then run:

```sh
python3 -B scripts/routing_table.py refresh --guidance GUIDANCE.json \
  --catalog CATALOG.json --benchmarks BENCHMARKS.json --terms TERMS.json \
  [--settings SETTINGS.json] [--table TABLE.json]
```

It writes `strategy-table.json` beside the routing settings file (or `--table`),
where `scripts/routing.py` reads it, and makes no network, model or secret
access. Inputs must be regular files, not symlinks or devices. It prints one
JSON line: `refreshed` (exit 0) wrote a new table; `kept` (exit 1) kept the last
verified table; `blocked` (exit 1) wrote nothing, for example on a symlinked or
shared target or input; a usage error exits 2. A `dir_sync_failed` warning
means the table was written but may not survive a crash; rerun the refresh.

A refresh that writes a table of a new version keeps the table it replaces,
unchanged, as `strategy-table.previous.json` (`<name>.previous.json` beside
`--table`). Only one previous version is kept, and only a verified table. To
return to it, run `python3 -B scripts/routing_table.py rollback [--settings
SETTINGS.json] [--table TABLE.json]`. It swaps the two files, so running it again
returns to the newer table. It prints `rolled_back` (exit 0), or `blocked`
(exit 1) and changes nothing: `no_previous_table`, `unsafe_previous_table`,
`invalid_previous_table` or `unsafe_table_path`. A `newer_table_not_kept` warning
means the rollback happened but the replaced table was not kept; a refresh
recreates it. Rollback never contacts the network, a model or a secret store.
Report a `kept`, `blocked` or warning result as a maintenance finding; it never
blocks delegation. Code drops rows naming unavailable models,
unsupported efforts or harnesses, and rows without cited evidence. Profiling or
ranking models also need known endpoint terms that allow confidential data
(`forbids_confidential: false`). An active integration covers endpoints that may
retain or train on task text; a missing or malformed term, or a `user_restricted`
entry, excludes the endpoint. A failed or invalid refresh keeps the last verified
table with its original age. A refresh re-ranks later assignments without
re-profiling them. A refresh that compiles but leaves no qualified rows keeps only
the previous non-control rows: profiler and ranker rows come only from a compile
against the current terms.

An empty ranking (`no_recommendation`) is a missing suggestion, not a refusal. The
caller continues on the deterministic admission path or the user's explicit worker
choices; only admission can block. Ranking results also return `review_policy` and
`scope_ref` so callers can key a cache by profile digest, table version, review
policy and route scope.
