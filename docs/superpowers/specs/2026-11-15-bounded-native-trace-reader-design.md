# Bounded native trace correlation

## Approved scope

The user approved a bounded trace-reader adapter after model-free discovery of
Codex's native rollout tracing. Implement and verify the offline source slice.
Do not launch a live turn, enable tracing, update installations, change native
configuration, or renew a historical allocation.

## Goal and approach

Allow an actual native trace bundle to supply the missing rejected-wrapper
identity relation. Preserve the existing native rollout call/output proofs and
Core's exact receipt lookup. An outer call selects a native code cell; that cell
selects its nested patch invocation; the native invocation ID selects Core.
Patch bytes and a denial remain necessary checks, never identity substitutes.

Reference semantics come from official immutable source
`openai/codex@ac4332c05b11e00ae775a24cb762edc05c5b5932`, specifically
`rollout-trace/src/raw_event.rs`, `tool_dispatch.rs`, and
`core/src/tools/tool_dispatch_trace.rs` and `registry.rs`.
Installed binary/schema/help observations support discovery, not live emission
or an exact executable/source match.

## Alternatives

- **Native raw trace:** selected. It records the cell/invocation relation before
  the pre-tool hook and records the denied result without inventing a new event.
- **Force direct calls:** not selected; current model advertises
  `code_mode_only`, and direct exposure has not been verified.
- **Receipt uniqueness or matching text:** forbidden; neither authenticates IDs.

## Trust and compatibility

Only the trusted coordinator/native observer may select a trace location.
Do not accept a mapping, trace path, or success assertion from task/proposal JSON.
Location selection is not proof: validate native manifest, thread/turn/cell and
call identities, sequence and lifecycle, exact invoked patch and denial, payload
hashes, private physical paths, and original task/attempt constraints.
Missing, partial, ambiguous, stale, changed, oversized, unsafe, or mismatched
sources fail closed. No directory-wide unbounded discovery or payload ingestion.

Preserve both native and hook IDs, source references and bytes through retained
consumption. Re-read all relied-on source files before issuing or consuming a
witness. Retained evidence must independently derive the same relation; changing
the ambient trace location cannot change an already issued observation.

Keep direct-call and supported completion behavior unchanged. Old proofs remain
readable. A rejected wrapper without a qualifying native trace remains
`native_hook_correlation_unavailable`. No new public CLI, preference, database
schema, dependency, proxy, model switch, or generic trace viewer.

## Verification and limits

Use sanitized, explicitly synthetic fixtures faithful to the official native
contract. Test a distinct-ID denial through live-owner simulation and retained
readback, plus malformed and hostile inputs. Offline fixture success is not
native qualification. Exact fresh live effects and budgets remain a later review.
