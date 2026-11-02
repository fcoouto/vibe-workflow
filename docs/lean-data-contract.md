# Lean data authority and compatibility contract

This is an authoring and implementation contract for the approved lean-data design. It does not change runtime behavior or establish that the target record formats, retention lifecycle, or physical limits are implemented or qualified. Routine tasks load their applicable local runtime contracts; they do not load this design document.

## Current behavior and target

The historical main-based package (M) has schema-2 runtime state and a smaller evidence implementation. The frozen Evidence Lifecycle candidate (E) is a separate source basis, not current behavior or qualification evidence. The approved composition targets fresh canonical Project authority schema 2 and SQLite runtime schema 3, while preserving old M schema-2 and E schema-3 packages, readers and pins at their original paths. Runtime-enabled legacy enrollment does not establish current task authority in the composed package; runtime-free legacy enrollment remains startup-only. Supported M historical inspection is explicit and read-only; E lowercase schema-3 history remains with the original E reader. These are distinct schema namespaces; proof, manifest, transaction and other protocol versions are independent and must not be globally replaced. Read retained records only with their matching readers.

Canonical authority may cover a descendant checkout only through explicit accepted repository/checkout/client membership; ancestry alone is not membership. D4 birth creates the fresh canonical database, original source baseline and ownership receipts, and `task start` rejects a missing canonical database. Canonical scope and schema-shape validation do not authenticate host intent or qualify a native client. The target keeps the existing SQLite state authority, immutable evidence files, and recoverable file journal. It adds no service, runtime graph, duplicate measurement ledger, or background cleanup worker. Toolkit remains the authoring, setup, and reviewed-update authority; complete local contracts remain sufficient for routine execution. Native defaults and user choices stay intact. Optional OpenRouter/OpenCode execution consent and Langfuse observability remain separately owned and opt-in.

## One authority per fact

| Fact | Authority and consumers | Keep / derive |
| --- | --- | --- |
| Current task, assignment, ownership, lifecycle, pins, and replay identity | Existing SQLite state; guarded task, delegation, completion, and recovery decisions | Keep the authoritative identity/state and only the fields needed by actual selectors. Safe retirement may preserve minimal replay/reference identity; it must never make expired evidence pass again. |
| Check/review/worker detail and publisher provenance | One immutable evidence body with its existing metadata binding; check, review, completion, and historical-finding readers | Keep the body once, its actual publisher/owner/pins/time, and bindings to final bytes and physical identity. New-format records may embed provenance in the existing envelope rather than duplicate mandate records, only after the replacement is qualified. |
| Historical completion and numerical totals | One compact closure for each eligible new task; completion and later reconciliation | Keep actual values, units, attribution/source semantics, coverage and corrections, findings/dispositions/recheck references, stop/verification bindings, dates, and detail references. Hashes alone cannot substantiate totals. Historical proof never grants current readiness. |
| Dependency meaning | Owner-written plans and versioned use contracts; admission, completion, retention, and recovery | Keep exact **direct** edges and each dependency's **per-root purpose**. Derive a temporary adjacency/reverse map for one guarded stage; do not persist a second authoritative graph. A stopped historical dependency satisfies only the exact historical requirement it proves; a separate live content use still requires its own permission and bytes. |
| Enrollment/update inputs and recovery | Existing private transaction directory; apply, resume, rollback, and recovery | Keep one immutable approved-input bundle, a small mutable stage/receipt journal, bundle digest, original backups, and ownership/conflict evidence while recovery needs them. Filesystem edits and multiple stores are not atomic; preserve durable foreign-publication intents until source evidence is safely registered at the destination. |
| Optional integration settings and analytics | Each existing separate settings owner; integration management and approved observability consumers | Keep native defaults. Do not merge OpenRouter/OpenCode consent with Langfuse consent or copy transcripts into a new local ledger. |

A field may be removed only after its last real consumer uses a replacement with the same authority, freshness, and recovery guarantees. Full inventories, orphan checks, pins, current permissions, native observations, and physical identity checks remain at their trust boundaries.

## Read reuse and freshness

Within one actual guarded transaction stage, acquire one operation-owned view of the relevant state: establish complete relevant ownership/coverage once, decode each needed immutable body once, and pass narrow derived views to existing consumers. This is temporary reuse, not a persistent cache or a copied full inventory.

Reacquire facts after publication, pending-delete commit, guard release, external execution, or another boundary that can change relevant state. Before unlinking, recheck current permission and the exact file and parent identity. A pin proves original identity; it does not prove present permission or content. A derived code graph is not live dependency evidence. Whole-owner and all-descendant stop observation remains unqualified; record that unknown rather than treating record shape as proof or adding a supervisor.

## Retention and safe deletion

The existing approved retention limits remain exact:

- Detailed closed-task briefs, review reports, and useful diagnostics expire **seven days after safe closure**. Safe closure means workers reconciled and no pending execution, merge, or recovery dependency.
- Closed-task detailed evidence has one **100 MiB per Project** allowance, shared across every repository in an umbrella. Evict the oldest eligible evidence first when age or size requires it. This is a requirement, not proof that current code enforces the cap.
- Compact closed-task proof is retained for **at most four months**, including task measurements, authoritative numerical source receipts, provenance, corrections, coverage, compact verification, and the short outcome summary reused from normal completion. Numerical correction age and the precise calendar-month boundary remain narrow qualification questions; do not invent dates or version keys or weaken the maximum.
- Active, uncertain, merge-required, or recovery-required proof is protected from both time limits and the size cap, including the four-month compact limit. Report protected excess and its dependency. Preserve it until safely releasable.

Before deleting detailed text, durably publish the compact proof needed for numerical reconciliation. Preserve only necessary non-content expiry/replay markers after eligible proof expires; they cannot resurrect readiness or validation. Unknown ownership, incomplete Project coverage, dangling references, aliases, temporary reviews, linked companions, or managed copies must not bypass the same allowance or become permission to delete. Temporary/aliased reviews reuse canonical owners and their original ages and allowances; unresolved identity limits retention rather than allocating a fresh allowance.

Deletion uses the existing recoverable sequence: durable replacement where needed, durable `delete_pending` intent, fresh permission and physical-identity checks, physical removal with directory durability, then `expired`. Resume must distinguish registered replacement, orphan, pending intent, missing bytes, and replaced inode. Do not infer safe deletion from an absent reference alone. Cleanup is bounded and occurs during normal use; no background worker is implied. Managed snapshots/exports cannot silently extend detailed retention or evade the cap.

## Compatibility and qualification boundary

Keep original readers for pinned, live, or uncertain records. New formats are forward-only: write them only for new records or an explicitly verified safe transition; never rewrite historical artifacts in place or downgrade a live record. When a new writer fails qualification, stop publishing that format, retain already-written records under their matching reader, and continue only compatible scopes. Do not erase new references, revive expired validation, or claim an old binary can read a new schema.

Current M behavior, the E candidate, the target design, and unknown qualification are separate claims. In particular, Project-wide age/size eviction, full temporary/linked/managed-copy coverage, compact expiry, metadata inspection-bound retirement, physical deletion recovery, and production cap behavior are not established by this contract. Package validation alone cannot establish native execution, complete retention, physical/cap behavior, or production benefit.
