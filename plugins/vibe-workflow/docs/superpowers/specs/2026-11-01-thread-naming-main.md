# Thread Naming: isolated main integration

The user authorized merging Thread Naming into `main`, then selected isolation of
Thread Naming and its essential dependencies. The reviewed development snapshot
is preserved at `6fca28f`; its 88 unmerged ancestor commits are outside this merge.

## Scope

Use main's existing schema-2 task, evidence, delegation and hook contracts. Add
the approved naming behavior and one uniform task-status vocabulary. Preserve
permissions, ownership, completion evidence and historical data. Do not backport
canonical schema 3, accounting, OpenRouter, observability, resource admission,
recovery monitors or the separate efficiency candidates.

## Required behavior

- Titles use `<meter> [W<number>] <P|C<number>> · <topic> · <STATUS>`.
- The meter has five cells, fills left to right and has one ASCII space before
  the bracketed family. `DONE` keeps the full meter. No numeric percentage.
- Families are unique across projects sharing this OS user's register. Parent
  and child identifiers remain stable; child ordinals are not reused.
- Task records and titles use exactly `TRIAGE`, `READY`, `WORKING`, `REVIEW`,
  `WAITING`, `BLOCKED`, `PAUSED`, `DONE` and `CANCELLED`.
- New tasks start at `TRIAGE`. `READY` means ready to begin. Only evidence
  verification establishes `DONE`. Cancellation remains terminal.
- Waiting, blocked and paused tasks require explicit resolution before work,
  verification or managed dispatch can resume. Existing guards remain binding.
- Optional task milestones are exactly `{id, weight, accepted, reference}`.
  Accept at most 64 unique printable IDs, positive integer weights at most
  1,000,000, boolean acceptance and nonblank printable references.
- Progress uses accepted weighted milestones, rounded down to fifths and capped
  at four cells before `DONE`. Reopened acceptance can reduce progress.
- The coordinator records semantic phase changes during normal work. Code
  repairs adopted titles after committed task/evidence/delegation commands and
  existing eligible hooks. No watcher or extra model turn.
- Topic-only manual edits are adopted. Managed-field or custom-format edits
  preserve the title and pause only title synchronization, with one durable
  notice. Resume only on explicit request. Manual `DONE` grants no completion.
- Retain pending writes before delivery, acknowledge native readback, serialize
  participating writers and defer unavailable clients to the next event.
- Legacy task states are read only through explicit historical inspection.
  Do not rewrite old records or pins, reinterpret old `ready` as new `READY`,
  or clear retained ownership. Preserve original-runtime reconciliation and
  retirement behavior through the existing approved update procedure.

## Qualification and integration

Test this main-based implementation, including title persistence, lost
acknowledgment, manual edits, replacement, completion freshness and hook outputs.
Run package validation and the main-based suite, then obtain Astra/high review.
Native hook observation remains a separate unverified gate. The user authorized
the local merge with that limitation disclosed; no push or global installation
is implied.
