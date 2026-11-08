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
maintenance run may recompile the Smart Routing strategy table with
`runtime.routing_evidence.refresh_table`. Run it only during that maintenance
session, never from a spawn path, hook or startup check. Extract prose guidance
into structured rows first; code then drops rows naming unavailable models,
unsupported efforts or harnesses, and rows without cited evidence. Profiling or
ranking models also need zero-retention, no-training terms. A failed or invalid
refresh keeps the last verified table with its original age. A refresh re-ranks
later assignments without re-profiling them.
