# Smart Routing Prospective Sample Registration Plan

> **For agentic workers:** Use `executing-plans` for the bounded registration work below. No implementation worker, independent review call or collection run is authorized in this phase.

**Goal:** Register the user's prospective replacement choice, preserve the incomplete historical evidence, and define the gates before any new evidence is collected.

**Architecture:** Add a dated amendment to the existing validation record, without changing the runtime. The later sample uses positively observed new assignment origins, not a date-only filter over old stores. Reuse existing lifecycle receipts and private exports only after their actual coverage is established.

**Tech Stack:** Markdown; existing Python standard-library package validation and read-only Git checks.

**Spec:** `docs/smart-routing-validation.md`, Edit 13. This is a registration plan, not an approved runtime implementation or collection plan.

## Global Constraints

- No live/model/provider calls or spending; ranked-canary cap remains 0.
- Do not inspect actual client history, session content or credentials.
- Preserve historical artifacts byte-for-byte and label their exclusion coverage incomplete.
- Retain both OpenCode/Codex mappings and this project's approved current/legacy roots; exclude `opus`.
- Preserve the 21 saved pilot admissions for strict replay, independently of the new advisory sample.
- Preserve all billing, entitlement, routing-path/table, native-host, strict-replay and canary gates.
- No campaign/ledger reset, allowance namespace, settings change, polling service, registry or bypass flag.
- No collection boundary is active and no sample records exist yet.
- The earlier Astra review covers Edit 12 only, not this changed design.

## Review Focus

- A recent timestamp or copied store must not turn an old session into a new observation.
- A new child of an old root, or a qualification descendant, must not enter the ordinary sample.
- A missing capture or source must not disappear from coverage or become a zero-result success.
- A hand-written identifier list or schema hash must not masquerade as independently observed creation provenance.
- The advisory replacement must not erase legacy failures or reset strict replay, billing or canary obligations.

These are mandatory future synthetic/independent review cases, not checks claimed to pass in this documentation phase.

## Approaches and decision

The user chose a fresh prospective sample rather than another attempt to reconstruct missing historical evidence. Repeating qualification would not recover the old identifiers. A date-only cut would still accept imported/restamped sessions and therefore is not the chosen design. Positive origin evidence is required for the newly declared sample, while old evidence stays available and incomplete.

## Task 1: Register the approved scope and its limits

**Files:** Modify `docs/smart-routing-validation.md`; create this plan.

**Interfaces:** Consumes the explicit user scope decision and existing Edits 6–12. Produces a dated, discoverable scope amendment; no runtime API or accepted exclusion basis.

- [ ] Append Edit 13 immediately before Results; preserve all previous bytes after removing only the new edit.
- [ ] State that prospective collection has not started; keep start/end unset.
- [ ] Define complete origin/ancestry/purpose coverage, two-source scope, private metadata retention and independent evidence acceptance.
- [ ] Preserve the strict pilot sample and all live/release prerequisites; state that the current runner has no prospective gate.
- [ ] Check each Review Focus item against the amendment, without reading real rows or generating data.

## Task 2: Verify and publish only the registration

**Files:** The two documentation files above; existing private coordinator task records.

**Interfaces:** Consumes Task 1's exact diff. Produces a committed, reviewable draft update with truthful evidence limits.

- [ ] Verify removal-of-only-Edit-13 byte equality and unchanged runtime/test files; run `git diff --check`.
- [ ] Run `python3 -B scripts/validate.py` with private HOME/TMPDIR. Expected: package validation passes; client execution/model names remain not checked.
- [ ] Commit only these documentation files. Do not add artificial unit tests that restate prose or rerun the unchanged product suite.
- [ ] Recheck the remote head/body/draft state, push to the existing task branch and append a sanitized update to #283, preserving concurrent edits.
- [ ] Keep #283 `[BLOCKED]` and draft. Do not merge or claim independent review, source qualification, collection or replay.
- [ ] Record exact commit/checks, preserved old-evidence hashes and next gate in the existing coordinator record.

## Later work: gated, not executed by this registration

1. Independently review the prospective design and fix any substantive gap before implementation is accepted. No earlier review allowance is restarted.
2. Establish which existing per-client lifecycle receipt/export can prove original creation, purpose and complete ancestry. If either producer cannot do so, define the smallest producer change and its synthetic tests before proposing an implementation. Do not guess an API, endpoint or data source.
3. Implement and synthetically test the minimal prospective intake in `tests/qualification/replay_history.py` and `tests/test_qualification_replay_history.py`, with corresponding caller/producer edits only where the established evidence path needs them. Test every Review Focus case, refusal before sensitive row projection, existing ID/schema/privacy gates, unchanged source bytes and aggregate-only output. This document does not authorize that unresolved implementation yet.
4. Before any real capture, register immutable UTC start/end, exact approved roots/source bindings and the qualified capture mechanism; obtain separate bounded metadata-access/collection authority. Do not run tasks solely to pad the sample. Missing capture remains incomplete, not silently replaced by another interval.
5. After authorized ordinary work has produced a complete nonempty sample, independently trace its retained original IDs, ancestry, purpose and source-to-input bindings. Record actual coverage and omissions, not invented identifiers or proof flags.
6. Only after that acceptance and all unchanged prerequisites, seek the separately scoped replay/live decisions. Keep strict pilot replay separate. A prospective advisory result is not a billing, canary or release qualification.

**Stop condition for this phase:** documentation published as a blocked draft, old evidence intact, no collection started. Next owner is the coordinator for an explicitly bounded independent design review, without assuming permission for model/live calls from this registration.
