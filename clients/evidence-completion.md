# Completion and merge evidence

Apply the [common evidence contract](evidence.md) before this procedure.
When relying on native review, retain the [current review bindings](evidence-review-bindings.md).

`evidence verify` requires purpose `completion` or `merge-evidence`; optional `reuse` entries need an attempt ID, reason, and unchanged relative dependencies. Optional current `security_impact` (`status`, `reason`, `reference`) is required after candidate changes unless a new plan recaptures that candidate; unresolved impact blocks. `status` takes `--client codex --session SESSION` and recaptures readiness.

## Completion and limits

Use `verify` with purpose `completion` before claiming the task complete; use `merge-evidence` to check local evidence before a merge. Merge evidence always requires general review and applicable security review, including for trivial changes. Only successful completion verification can set `DONE`; ordinary task updates cannot set it. Verification first reserves its own read-only resource barrier, then rechecks current content and blockers across relevant sessions/scopes/history. Only its own completed synchronous barrier settles automatically with the result; other relevant claims remain blockers. Successful completion releases that barrier and records `DONE` atomically. A later conflicting reservation invalidates affected readiness in the same admission transaction. A changed candidate, plan, relevant environment, policy, or check invalidates affected proof. Reuse unaffected evidence only with a recorded reason and unchanged dependencies.

Missing, stale, failed, interrupted, ambiguous, or unverified proof blocks dependent completion. Confirmed high/critical security findings block; medium/low findings need resolution or authorized disposition. A readiness result is point-in-time local evidence, not merge authorization or proof that a repository gate passed. For repository merge assurance only, read [repository merge assurance](repository-gate.md). Its bounded fixture inspector always exits 1; production sources, publishing and enforcement remain unqualified. Hooks and generation are invalidation hints, not a security boundary or proof of unchanged content.

For an authorized runtime update or recovery, follow [the fresh-state transition](runtime.md#change-a-pinned-runtime). Keep the retirement and exact assignment proof boundaries above; changing state authority cannot clear unresolved ownership.
